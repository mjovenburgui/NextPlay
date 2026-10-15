import time

from django.core.management.base import BaseCommand, CommandError

from games.igdb import MAX_PAGE_SIZE, IGDBClient, IGDBError, upsert_games


class Command(BaseCommand):
    help = "Importa juegos de IGDB: los más populares, o los que coincidan con --search."

    def add_arguments(self, parser):
        """Opciones del comando: --limit (cuántos populares) y --search (importar una búsqueda)."""
        parser.add_argument("--limit", type=int, default=500, help="Número de juegos populares a importar.")
        parser.add_argument("--search", help="Importa solo los resultados de esta búsqueda.")

    def handle(self, *args, **options):
        """Punto de entrada: importa una búsqueda o los más populares y dice cuántos juegos guardó."""
        try:
            client = IGDBClient()
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
