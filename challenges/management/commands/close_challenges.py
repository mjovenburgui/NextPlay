"""Comando «manage.py close_challenges»: el repaso de cada noche de los retos (challenges.closing.close_all). Lo lanza
nightly; lo que hace se ve en Avisos, Retos, la Página del reto, el Perfil y el feed de Inicio.
"""

from django.core.management.base import BaseCommand

from challenges import closing


class Command(BaseCommand):
    help = (
        "Repaso de los retos de cada noche: apunta quién ha llegado al objetivo, avisa a quien le quedan pocos días en "
        "uno y cierra los que terminaron hace más de una semana, avisando a cada uno de cómo ha quedado."
    )

    def handle(self, *args, **options):
        """Lanza el repaso y dice cuántos retos se han conseguido, cuántos avisos de que se acaban ha mandado y cuántos
        retos ha cerrado.

        Pantallas: Avisos (retos conseguidos, que se acaban y cómo has quedado), Retos, Página del reto (quién lo ha
        conseguido), Perfil (insignias de la sección «Retos»), Inicio (feed: «consiguió el reto»).
        """
        result = closing.close_all()
        self.stdout.write(
            f"Retos conseguidos: {result['won']} · avisos de que se acaban: {result['ending']} · "
            f"retos cerrados: {result['closed']}"
        )
