from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from games import igdb, spanish, xbox
from games.models import Game

# Campos que cambia spanish.apply_igdb.
IGDB_FIELDS = ["languages", "spanish_audio", "spanish_text", "name_es", "search_names", "cover_eu_image_id"]


class Command(BaseCommand):
    help = (
        "Completa los datos en castellano de los juegos: idiomas, nombre en España y portada europea de IGDB "
        "(los que aún no los tienen, o todos con --igdb) y sinopsis en español e idiomas de Steam y de la tienda "
        "de Microsoft (los --limit más populares que no se hayan pedido en los últimos --dias días)."
    )

    def add_arguments(self, parser):
        """Opciones: --limit, --dias, --igdb, --sin-tiendas y --pausa."""
        parser.add_argument(
            "--limit", type=int, default=200, help="Juegos que se piden a Steam y Microsoft, los más populares primero."
        )
        parser.add_argument(
            "--dias", type=int, default=30, help="Vuelve a pedir a las tiendas los juegos pedidos hace más de estos días."
        )
        parser.add_argument("--igdb", action="store_true", help="Vuelve a pedir a IGDB los datos de todos los juegos.")
        parser.add_argument("--sin-tiendas", action="store_true", help="Solo IGDB: no pide nada a Steam ni a Microsoft.")
        parser.add_argument(
            "--pausa", type=float, default=spanish.STEAM_PAUSE, help="Segundos de espera entre peticiones a Steam."
        )

    def handle(self, *args, **options):
        """Primero IGDB (rápido, en lotes de 500) y después las tiendas (Steam de uno en uno, con pausas)."""
        self._from_igdb(everything=options["igdb"])
        if not options["sin_tiendas"]:
            self._from_stores(options["limit"], options["dias"], options["pausa"])

    def _from_igdb(self, everything):
        """Pide a IGDB los idiomas, los nombres y la portada europea de los juegos que no los tienen (o de todos).

        Los que IGDB no devuelve se quedan con los datos de IGDB vacíos, para no volver a pedirlos cada noche.
        """
        if not igdb.is_configured():
            self.stderr.write("IGDB: faltan IGDB_CLIENT_ID e IGDB_CLIENT_SECRET, no se piden idiomas ni nombres.")
            return
        games = Game.objects.filter(igdb_id__isnull=False)
        if not everything:
            games = games.exclude(languages__has_key="igdb")
        games = {game.igdb_id: game for game in games}
        if not games:
            return
        try:
            payload = {data["id"]: data for data in igdb.IGDBClient().spanish_data(sorted(games))}
        except igdb.IGDBError as exc:
            raise CommandError(str(exc)) from exc
        for igdb_id, game in games.items():
            spanish.apply_igdb(game, payload.get(igdb_id, {}))
            game.save(update_fields=IGDB_FIELDS)
        dubbed = sum(1 for game in games.values() if game.spanish_audio == "es")
        self.stdout.write(self.style.SUCCESS(f"IGDB: {len(games)} juegos revisados, {dubbed} con voces en castellano."))

    def _from_stores(self, limit, days, pause):
        """Pide la sinopsis en español y los idiomas de Steam (y la descripción de Microsoft) de los más populares."""
        cutoff = timezone.now() - timedelta(days=days)
        in_stores = (
            Q(steam_app_id__isnull=False) | Q(steam_app_id_override__isnull=False)
            | ~Q(xbox_product_ids=[]) | ~Q(xbox_product_id_override="")
        )
        games = list(
            Game.objects.filter(in_stores)
            .filter(Q(localized_at__isnull=True) | Q(localized_at__lt=cutoff))
            .order_by("-popularity", "name")[:limit]
        )
        if not games:
            self.stdout.write("Tiendas: no hay juegos pendientes.")
            return
        self.stdout.write(f"Tiendas: pidiendo {len(games)} juegos (unos {round(len(games) * pause / 60)} min)…")
        try:
            found = spanish.localize_games(games, pause=pause)
        except (spanish.StoreError, xbox.XboxError) as exc:
            raise CommandError(f"{exc} Lo pedido hasta ahí está guardado: vuelve a lanzarlo más tarde.") from exc
        self.stdout.write(self.style.SUCCESS(
            f"Tiendas: {found['steam']} con sinopsis de Steam, {found['xbox']} de Microsoft y {found['none']} sin sinopsis."
        ))
