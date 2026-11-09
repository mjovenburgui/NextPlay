"""Comando «manage.py import_addons» (parte de la tarea de cada noche): trae de IGDB las expansiones y los DLC de los
juegos del catálogo que aún no tienen ficha propia, con su ficha técnica, y busca sus precios de PC. Se ve en la Ficha
del juego («Expansiones y DLC», cada uno con su precio, y la ficha de cada uno) y en el Catálogo con ?tipo=.
"""

from django.core.management.base import BaseCommand, CommandError

from games import details, igdb, prices
from games.models import Game


class Command(BaseCommand):
    help = (
        "Trae de IGDB las expansiones y los DLC de los juegos del catálogo que aún no tienen ficha propia, con su "
        "ficha técnica, y busca en IsThereAnyDeal sus precios de PC (también los de los que ya tenían ficha y nunca se "
        "han buscado). Si no falta nada no pide nada a IGDB; con --simular solo cuenta."
    )

    def add_arguments(self, parser):
        """Opciones: --simular (solo dice cuántos faltan) y --sin-precios (no busca precios: ya los pondrá al día
        update_prices por la noche).

        Pantallas: ninguna (opciones del comando import_addons).
        """
        parser.add_argument("--simular", action="store_true", help="Solo dice cuántos faltan, sin pedir nada.")
        parser.add_argument(
            "--sin-precios", action="store_true",
            help="No busca sus precios (los pone al día update_prices cada noche).",
        )

    def handle(self, *args, **options):
        """Junta las expansiones y los DLC de las fichas de todos los juegos (details.addon_ids) y trae de IGDB los que
        no están en el catálogo (igdb.upsert_games, unas dos peticiones por cada 500) con su ficha técnica
        (details.update_details). Después busca en IsThereAnyDeal los precios de PC de los que nunca se han buscado
        (prices.update_prices). Los de Xbox y Nintendo se los pone update_prices por la noche, solo por su ID: por el
        título no los encontraría.

        Pantallas: Ficha del juego («Expansiones y DLC», cada uno con su precio, y la ficha de cada expansión o DLC),
        Catálogo (con ?tipo=, los DLC y las expansiones traídos).
        """
        ids = set()
        for game in Game.objects.filter(parent__isnull=True).exclude(details_updated_at=None).only("details"):
            ids.update(details.addon_ids(game))
        known = set(Game.objects.filter(igdb_id__in=ids).values_list("igdb_id", flat=True))
        missing = sorted(ids - known)
        self.stdout.write(
            f"Expansiones y DLC en las fichas: {len(ids)}; con ficha propia: {len(known)}; sin ella: {len(missing)}."
        )
        if options["simular"]:
            return
        if missing and not igdb.is_configured():
            self.stderr.write("IGDB: faltan IGDB_CLIENT_ID e IGDB_CLIENT_SECRET, no se trae ninguno.")
        elif missing:
            client = igdb.IGDBClient()
            try:
                brought = igdb.upsert_games(client.games_by_ids(missing))
                details.update_details(brought, client)
            except igdb.IGDBError as exc:
                raise CommandError(f"{exc} Lo guardado hasta ahí se queda: vuelve a lanzarlo más tarde.") from exc
            self.stdout.write(self.style.SUCCESS(f"Traídos de IGDB: {len(brought)}, con su ficha técnica."))
        if options["sin_precios"]:
            return
        if not prices.is_configured():
            self.stderr.write("PC: sin ITAD_API_KEY, no se buscan sus precios.")
            return
        unpriced = list(Game.objects.filter(igdb_id__in=ids, parent__isnull=True, prices_updated_at=None))
        if not unpriced:
            return
        try:
            found = prices.update_prices(unpriced)
        except prices.ITADError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Precios de PC buscados de {len(unpriced)}: {found} con precio."))
