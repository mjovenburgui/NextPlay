"""Comando «manage.py nightly»: las tareas de cada noche en orden (sync_libraries, close_challenges, update_prices y
localize_games). No pinta ninguna pantalla; lo que guarda se ve sobre todo en el Perfil, la Ficha del juego, los Retos
y la Lista de deseos.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Tareas de cada noche, en orden: sincroniza las bibliotecas de Steam (sync_libraries), repasa los retos "
        "(close_challenges), actualiza los precios (update_prices) y completa los datos en castellano de los juegos "
        "(localize_games). Es lo que hay que programar en cron o en el Programador de tareas."
    )

    # Comandos que se lanzan, en este orden. Los retos van después de sincronizar, para que cuente lo que llega.
    TASKS = ("sync_libraries", "close_challenges", "update_prices", "localize_games")

    def handle(self, *args, **options):
        """Lanza cada tarea aunque falle la anterior y da error al final si alguna ha fallado.

        Pantallas: Perfil (biblioteca y horas; insignias de las portadas, cifras y sección «Logros»; filtro de idioma
        de la biblioteca), Ficha del juego (tus horas; tus logros; botón «Lo quiero»; Dónde comprarlo; nombre en
        España, sinopsis en español e idiomas), Juegos importados (los que no se encuentran), Inicio (feed: la
        importación y «consiguió el reto»), Retos (progreso), Página del reto (progreso y quién lo ha conseguido),
        Perfil (insignias de la sección «Retos»), Lista de deseos (mejor precio de hoy y mínimo histórico; filtro de
        idioma), Página de la lista (lo que cuesta lo que te falta), Avisos (bajadas de precio; retos conseguidos, que
        se acaban y cómo has quedado), Catálogo (búsqueda por nombre en España y filtro de idioma).
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
