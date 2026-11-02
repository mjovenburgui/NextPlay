from django.core.management.base import BaseCommand
from django.db import transaction
from django.template.defaultfilters import pluralize

from ...matching import clean_name, match_by_name, update_library
from ...models import ExternalTitle, ImportedTitle

# Las que se emparejan por el nombre; Steam va por su ID de app.
PLATFORMS = (ExternalTitle.Platform.PSN, ExternalTitle.Platform.XBOX)


class Command(BaseCommand):
    help = (
        "Vuelve a buscar por el nombre los títulos de PlayStation y Xbox que se quedaron sin emparejar y pasa sus horas a "
        "la biblioteca de quien los tiene, sin volver a sincronizar (no hace falta el NPSSO ni iniciar sesión en Microsoft). "
        "No toca lo corregido a mano, lo bloqueado ni lo que han ocultado todos los que lo tienen."
    )

    def add_arguments(self, parser):
        """Opción: --dry-run, que enseña lo que haría sin guardar nada."""
        parser.add_argument("--dry-run", action="store_true", help="Enseña lo que emparejaría, sin guardar nada.")

    def handle(self, *args, **options):
        """Con --dry-run lo hace todo dentro de una transacción que se deshace, también los juegos que traiga de IGDB."""
        if not options["dry_run"]:
            self.rematch()
            return
        with transaction.atomic():
            self.rematch()
            transaction.set_rollback(True)
        self.stdout.write("Prueba en seco: no se ha guardado nada.")

    def rematch(self):
        """Busca los títulos pendientes, apunta su juego (para todos, como una importación) y actualiza las bibliotecas.

        Las horas pasan como con una corrección del emparejamiento: sin apuntar actividad ni sesiones, porque no son de ahora.
        """
        titles = list(
            ExternalTitle.objects.filter(
                platform__in=PLATFORMS, game__isnull=True, source=ExternalTitle.Source.AUTO, locked=False,
                imports__hidden=False,
            ).distinct().prefetch_related("imports")
        )
        matches = match_by_name({
            title.pk: (clean_name(title.name), {platform for row in title.imports.all() for platform in row.platforms})
            for title in titles
        })
        matched = [title for title in titles if title.pk in matches]
        with transaction.atomic():
            for title in matched:
                title.game = matches[title.pk]
                title.save(update_fields=["game", "updated_at"])
            rows = ImportedTitle.objects.filter(title__in=matched).select_related("user", "title")
            for user, platform in {(row.user, row.title.platform) for row in rows}:
                update_library(user, platform, opened_only=True)

        for title in sorted(matched, key=lambda title: -sum(row.minutes for row in title.imports.all())):
            hours = sum(row.minutes for row in title.imports.all()) / 60
            self.stdout.write(f"{title.get_platform_display()}: «{title.name}» es {title.game.name} ({hours:.1f} h)")
        self.stdout.write(self.style.SUCCESS(
            f"{len(matched)} título{pluralize(len(matched))} emparejado{pluralize(len(matched))} de {len(titles)} pendiente{pluralize(len(titles))}."
        ))
        for title in titles:
            if title.pk not in matches:
                self.stdout.write(f"Sigue sin emparejar: {title.get_platform_display()}: «{title.name}»")
