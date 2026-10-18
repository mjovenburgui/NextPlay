from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Tareas de cada noche, en orden: sincroniza las bibliotecas de Steam (sync_libraries) y "
        "actualiza los precios (update_prices). Es lo que hay que programar en cron o en el Programador de tareas."
    )

    # Comandos que se lanzan, en este orden.
    TASKS = ("sync_libraries", "update_prices")

    def handle(self, *args, **options):
        """Lanza cada tarea aunque falle la anterior y da error al final si alguna ha fallado."""
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
