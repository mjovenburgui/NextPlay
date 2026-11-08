"""Comando «manage.py merge_editions» (parte de la tarea de cada noche): junta con su juego lo que la gente tiene de
cada edición (games.editions), para que sus horas, notas, reseñas y listas salgan en la Ficha del juego, el Perfil y
las listas como las del juego.
"""

from django.core.management.base import BaseCommand

from games import editions


class Command(BaseCommand):
    help = (
        "Junta con su juego lo que la gente tiene de cada edición (biblioteca, horas, reseñas, listas, lista de deseos, "
        "favoritos, retos...). Con --simular solo dice lo que haría."
    )

    def add_arguments(self, parser):
        """Opción --simular: enseña lo que se juntaría sin tocar nada.

        Pantallas: ninguna (opciones del comando merge_editions).
        """
        parser.add_argument("--simular", action="store_true", help="Solo dice lo que juntaría, sin cambiar nada.")

    def handle(self, *args, **options):
        """Junta (o con --simular, solo enseña) cada edición pendiente con su juego, una por línea.

        Pantallas: Perfil (biblioteca, horas, reseñas, listas, favoritos y retos), Ficha del juego (las horas, notas y
        reseñas de las ediciones cuentan en la del juego), Lista de deseos, Página de la lista, Página del reto, Inicio
        (feed), Avisos, Juegos importados.
        """
        waiting = editions.pending()
        if not waiting:
            self.stdout.write("No hay ediciones con cosas de jugadores por juntar.")
            return
        for edition, counts in waiting:
            if not options["simular"]:
                counts = editions.merge(edition) or counts
            self.stdout.write(f"  {edition.name} → {edition.parent.name}: {editions.describe(counts)}")
        verb = "Se juntarían" if options["simular"] else "Juntadas"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(waiting)} ediciones con su juego."))
