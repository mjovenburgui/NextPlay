"""El repaso de cada noche de los retos (comando close_challenges, que lanza nightly).

Apunta a quien ha llegado al objetivo sin que se hubiera mirado (lo normal es que lo apunten al momento la biblioteca y
las importaciones: challenges.progress.check), avisa a quien le quedan pocos días en uno que aún no ha conseguido y,
pasado el margen tras su fecha de fin (progress.GRACE_DAYS), avisa a cada uno de cómo ha quedado y lo cierra.

Pantallas: Avisos (retos que se acaban y cómo has quedado), Retos, Página del reto (quién lo ha conseguido), Perfil
(insignias de la sección «Retos»), Inicio (feed: quién consigue un reto).
"""

from datetime import timedelta

from django.db.models import Prefetch
from django.utils import timezone

from accounts import notices
from accounts.models import Notification

from . import progress
from .models import Challenge, Participation

# Días que le quedan a un reto (hoy incluido) cuando se avisa a quien aún no lo ha conseguido.
ENDING_DAYS = 3


def close_all(today=None):
    """Repasa los retos que no están cerrados y ya han empezado, y devuelve lo que ha hecho: {"won": a cuántos se les
    ha apuntado un reto conseguido, "ending": cuántos avisos de que se acaba ha mandado, "closed": cuántos ha cerrado}.

    En cada uno apunta a quien ha llegado al objetivo. Si le quedan ENDING_DAYS días o menos, avisa a quien aún no lo
    ha conseguido (una vez por reto). Si terminó hace más de progress.GRACE_DAYS días, avisa a cada uno de cómo ha
    quedado y lo marca como cerrado (Challenge.closed_at): desde entonces ya no cuenta nada nuevo.

    Pantallas: Avisos (retos conseguidos, que se acaban y cómo has quedado), Retos, Página del reto (quién lo ha
    conseguido), Perfil (insignias de la sección «Retos»), Inicio (feed: «consiguió el reto»).
    """
    today = today or timezone.localdate()
    result = {"won": 0, "ending": 0, "closed": 0}
    challenges = (
        Challenge.objects.filter(closed_at__isnull=True).exclude(starts_on__gt=today)
        .prefetch_related(Prefetch("participations", queryset=Participation.objects.select_related("user__profile")))
    )
    for challenge in challenges:
        participations = list(challenge.participations.all())
        done = progress.done_by(challenge, [participation.user_id for participation in participations]) if participations else {}
        newly = progress.mark_completed(challenge, done) if participations else []
        result["won"] += len(newly)
        if challenge.ends_on is None:
            continue
        won = {participation.user_id for participation in participations if participation.completed_at} | set(newly)
        days_left = (challenge.ends_on - today).days + 1
        if 0 < days_left <= ENDING_DAYS:
            result["ending"] += _remind(challenge, participations, won, done, days_left)
        elif challenge.ends_on + timedelta(days=progress.GRACE_DAYS) < today:
            _announce(challenge, participations, won, done)
            challenge.closed_at = timezone.now()
            challenge.save(update_fields=["closed_at"])
            result["closed"] += 1
    return result


def _amount(mine):
    """Lo que lleva alguien, en palabras, con lo de progress.summary: «2 de 5 juegos» o «12,5 de 50 horas».

    Pantallas: Avisos (retos que se acaban y cómo has quedado).
    """
    value = f"{mine['value']:g}".replace(".", ",")
    goal = mine["goal_value"]
    unit = ("hora" if goal == 1 else "horas") if mine["hours"] else ("juego" if goal == 1 else "juegos")
    return f"{value} de {goal} {unit}"


def _remind(challenge, participations, won, done, days_left):
    """Avisa a los de participations que aún no han conseguido challenge (won son los que sí) de que le quedan
    days_left días y de cómo van. Devuelve cuántos avisos ha creado: uno por persona y reto (accounts.notices).

    Pantallas: Avisos (retos que se acaban), todas (el número de la campana).
    """
    left = "Te queda 1 día" if days_left == 1 else f"Te quedan {days_left} días"
    created = 0
    for participation in participations:
        if participation.user_id in won:
            continue
        mine = progress.summary(challenge, done.get(participation.user_id, 0))
        text = f"{left} para el reto «{challenge.title}»: llevas {_amount(mine)}."
        created += bool(notices.notify(
            participation.user, Notification.Kind.CHALLENGE_ENDING, text, challenge.get_absolute_url(),
        ))
    return created


def _announce(challenge, participations, won, done):
    """Avisa a cada uno de participations de cómo ha quedado en challenge, que ya ha terminado: si lo consiguió (won)
    o hasta dónde llegó, y su puesto, de quien más lleva a quien menos (a igualdad, primero quien se unió antes, como en
    la clasificación de la página del reto). Con una sola persona no hay puesto que dar.

    Pantallas: Avisos (cómo has quedado en un reto), todas (el número de la campana).
    """
    ranking = sorted(participations, key=lambda participation: (-done.get(participation.user_id, 0), participation.joined_at))
    total = len(ranking)
    for position, participation in enumerate(ranking, start=1):
        place = f" y quedaste {position}.º de {total}" if total > 1 else ""
        if participation.user_id in won:
            outcome = "lo conseguiste"
        else:
            outcome = f"llegaste a {_amount(progress.summary(challenge, done.get(participation.user_id, 0)))}"
        notices.notify(
            participation.user, Notification.Kind.CHALLENGE_RESULT,
            f"El reto «{challenge.title}» ha terminado: {outcome}{place}.", challenge.get_absolute_url(),
        )
