"""Comando «manage.py nightly»: las tareas de cada noche en orden (sync_libraries, sync_gog, close_challenges,
update_prices, localize_games, fill_game_fields, import_addons y merge_editions). No pinta ninguna pantalla; lo que
guarda se ve sobre todo en el Perfil, la Ficha del juego, el Catálogo, los Retos y la Lista de deseos.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Tareas de cada noche, en orden: sincroniza las bibliotecas de Steam (sync_libraries) y las de GOG "
        "(sync_gog), repasa los retos "
        "(close_challenges), actualiza los precios (update_prices), completa los datos en castellano de los juegos "
        "(localize_games), pone al día su ficha técnica de IGDB (fill_game_fields), trae las expansiones y los DLC que "
        "aún no tienen ficha (import_addons) y junta las ediciones con su juego (merge_editions). Es lo que hay que "
        "programar en cron o en el Programador de tareas."
    )

    # Comandos que se lanzan, en este orden. Los retos van después de sincronizar, para que cuente lo que llega; los DLC
    # se traen después de la pasada a IGDB, que puede descubrir alguno nuevo; y las ediciones se juntan al final, porque
    # las dos cosas pueden enlazar alguna.
    TASKS = (
        "sync_libraries", "sync_gog", "close_challenges", "update_prices", "localize_games", "fill_game_fields",
        "import_addons", "merge_editions",
    )

    def handle(self, *args, **options):
        """Lanza cada tarea aunque falle la anterior y da error al final si alguna ha fallado.

        Pantallas: Perfil (biblioteca y horas; insignias de las portadas, cifras y sección «Logros»; filtro de idioma
        de la biblioteca), Ficha del juego (tus horas; tus logros; botón «Lo quiero»; Dónde comprarlo; nombre en
        España, sinopsis en español e idiomas), Juegos importados (los que no se encuentran), Inicio (feed: la
        importación y «consiguió el reto»), Retos (progreso), Página del reto (progreso y quién lo ha conseguido),
        Perfil (insignias de la sección «Retos»), Lista de deseos (mejor precio de hoy y mínimo histórico; filtro de
        idioma), Página de la lista (lo que cuesta lo que te falta), Avisos (bajadas de precio; retos conseguidos, que
        se acaban y cómo has quedado), Catálogo (búsqueda por nombre en España, filtros y etiqueta de las tarjetas),
        Ficha del juego (ficha técnica, Cuánto dura, «Expansiones y DLC» con la ficha y el precio de cada uno y
        «Ediciones y versiones»).
        """
        failed = []
        for name in self.TASKS:
            self.stdout.write(self.style.MIGRATE_HEADING(f"== {name}"))
            try:
                call_command(name, stdout=self.stdout, stderr=self.stderr)
            except CommandError as exc:
                self.stderr.write(f"{name}: {exc}")
                failed.append(name)
        if failed:
            raise CommandError(f"Han fallado: {', '.join(failed)}.")
