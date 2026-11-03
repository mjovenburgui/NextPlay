import time

from django.core.management.base import BaseCommand, CommandError

from games.igdb import MAX_PAGE_SIZE, IGDBClient, IGDBError, upsert_games
from games.models import Game


class Command(BaseCommand):
    help = (
        "Importa juegos de IGDB: los más populares, o los que coincidan con --search. Con --sin-ids-de-tienda vuelve "
        "a pedir los del catálogo que no tienen con qué buscar sus precios de PC."
    )

    def add_arguments(self, parser):
        """Opciones del comando: --limit (cuántos populares), --search (importar una búsqueda) y --sin-ids-de-tienda
        (repasar los del catálogo sin IDs de Steam ni de GOG)."""
        parser.add_argument("--limit", type=int, default=500, help="Número de juegos populares a importar.")
        parser.add_argument("--search", help="Importa solo los resultados de esta búsqueda.")
        parser.add_argument(
            "--sin-ids-de-tienda", action="store_true",
            help="Vuelve a pedir a IGDB los juegos del catálogo sin ID de Steam ni de GOG con que buscar sus precios.",
        )

    def handle(self, *args, **options):
        """Punto de entrada: importa una búsqueda, los más populares o repasa los que no tienen IDs de tienda, y dice
        cuántos juegos guardó."""
        try:
            client = IGDBClient()
            if options["sin_ids_de_tienda"]:
                found, total = self._refresh_without_store_ids(client)
                self.stdout.write(self.style.SUCCESS(
                    f"{total} juegos sin IDs de tienda vueltos a pedir: {found} tienen ya con qué buscar sus precios."
                ))
                return
            if options["search"]:
                total = len(upsert_games(client.search(options["search"])))
            else:
                total = self._import_top(client, options["limit"])
        except IGDBError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"{total} juegos importados o actualizados."))

    def _import_top(self, client, limit):
        """Importa los limit juegos más populares en páginas de hasta 500. Devuelve cuántos importó."""
        total = 0
        while total < limit:
            page = client.top_games(limit=min(limit - total, MAX_PAGE_SIZE), offset=total)
            if not page:
                break
            upsert_games(page)
            total += len(page)
            self.stdout.write(f"  {total} juegos…")
            time.sleep(0.3)  # IGDB allows 4 requests per second
        return total

    def _refresh_without_store_ids(self, client):
        """Vuelve a pedir a IGDB los juegos del catálogo que no tienen con qué buscar sus precios de PC (sin IDs de
        Steam ni de GOG, ni suyos ni de sus ediciones), por si los tienen en un port o una edición ampliada
        (igdb.RELATED_STORES). Devuelve (cuántos tienen ahora con qué buscarlos, cuántos se han pedido); sus precios
        llegan con update_prices."""
        ids = [game.igdb_id for game in Game.objects.exclude(igdb_id=None) if not game.price_lookups]
        games = upsert_games(client.games_by_ids(ids)) if ids else []
        return sum(1 for game in games if game.price_lookups), len(ids)
