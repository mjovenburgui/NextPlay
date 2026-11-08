"""Comando «manage.py fill_game_fields» (parte de la tarea de cada noche): la pasada a IGDB. Trae para los juegos del
catálogo su tipo, estado, modos, temas, perspectivas, estudios, editoras, nota de los usuarios de IGDB, duración y ficha
técnica (games.details), y enlaza cada edición con su juego. Se ve en la Ficha del juego y en los filtros del Catálogo.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from games import details, editions, igdb
from games.models import Game


class Command(BaseCommand):
    help = (
        "Pasada a IGDB: trae tipo, estado, modos, temas, estudios, nota de los usuarios, duración y ficha técnica de "
        "los juegos que nunca la han pedido o la tienen de hace más de 30 días (o de todos con --todos) y enlaza las "
        "ediciones con su juego. Son unas tres peticiones por cada 500 juegos."
    )

    def add_arguments(self, parser):
        """Opciones: --todos (también los que están al día) y --limit (cuántos como mucho, los más populares primero).

        Pantallas: ninguna (opciones del comando fill_game_fields).
        """
        parser.add_argument("--todos", action="store_true", help="Vuelve a pedir todos los juegos, aunque estén al día.")
        parser.add_argument(
            "--limit", type=int, default=0, help="Como mucho estos juegos, los más populares primero (0: todos)."
        )

    def handle(self, *args, **options):
        """Pide a IGDB los juegos pendientes en lotes de 500 y dice cuántos tienen ya tipo y duración, cuántas
        ediciones se han enlazado y cuántas tienen cosas de jugadores por juntar con merge_editions.

        Pantallas: Ficha del juego (ficha técnica, Cuánto dura, «Ediciones y versiones», «Juegos parecidos» y nota de
        los jugadores de IGDB), Catálogo (filtros de tipo, modo, tema, perspectiva, estudio, nota y duración, y
        etiqueta de las tarjetas).
        """
        if not igdb.is_configured():
            self.stderr.write("IGDB: faltan IGDB_CLIENT_ID e IGDB_CLIENT_SECRET, no se pide nada.")
            return
        games = Game.objects.filter(igdb_id__isnull=False).order_by("-popularity", "name")
        if not options["todos"]:
            cutoff = timezone.now() - details.MAX_AGE
            games = games.filter(Q(details_updated_at__isnull=True) | Q(details_updated_at__lt=cutoff))
        if options["limit"]:
            games = games[:options["limit"]]
        games = list(games)
        if not games:
            self.stdout.write("No hay juegos pendientes.")
            return
        self.stdout.write(f"Pidiendo a IGDB {len(games)} juegos…")
        try:
            linked = details.update_details(games)
        except igdb.IGDBError as exc:
            raise CommandError(f"{exc} Lo pedido hasta ahí está guardado: vuelve a lanzarlo más tarde.") from exc
        done = Game.objects.filter(pk__in=[game.pk for game in games])
        self.stdout.write(self.style.SUCCESS(
            f"{len(games)} juegos al día: {done.exclude(game_type=None).count()} con tipo, "
            f"{done.exclude(ttb_normal=None).count()} con duración y {len(linked)} ediciones enlazadas con su juego."
        ))
        waiting = editions.pending()
        if waiting:
            self.stdout.write(
                f"{len(waiting)} ediciones tienen cosas de jugadores: «manage.py merge_editions» las junta con su juego."
            )
