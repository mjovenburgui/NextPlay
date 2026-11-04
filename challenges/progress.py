"""El progreso de cada jugador en los retos, contado con su biblioteca.

En los de juegos (concretos o un número) cuentan los que ha pasado a «Completado» entre las fechas del reto
(LibraryEntry.completed_at); sin fecha de inicio cuentan también los de antes, aunque no se sepa cuándo los terminó.
En los de horas se suma lo jugado entre las fechas (library.models.PlaySession), que se apunta desde que existen las
sesiones. done_by lo cuenta de golpe para muchos jugadores, para la clasificación de la página del reto.

Quien llega al objetivo queda apuntado (mark_completed) y se le cuenta: un aviso en la campana y «consiguió el reto»
en su actividad. Aquí está también lo que enseña de los retos de cada uno la sección «Retos» de su perfil (showcase).
"""

from datetime import date, datetime, time, timedelta

from django.db.models import Count, Sum
from django.utils import timezone

from accounts import notices
from accounts.models import Notification
from library.models import LibraryEntry, PlaySession
from social import activity

from .models import Challenge, Participation

# Días después de su fecha de fin en los que un reto aún cuenta lo que llega: las sincronizaciones de PlayStation y
# Xbox son a mano y pueden llegar tarde. Pasados, el repaso de cada noche lo cierra (challenges.closing).
GRACE_DAYS = 7
# Insignias (retos conseguidos) y retos en marcha que enseña la sección «Retos» del escaparate del perfil.
SHOWCASE_WON = 8
SHOWCASE_ACTIVE = 3


def _start(day):
    """El principio del día day en la hora de España, para compararlo con fechas y horas.

    Pantallas: Retos (cómo vas en cada uno), Página del reto (tu progreso y la clasificación), Ficha del juego (al
    pasar un juego a «Completado» se mira si consigues un reto).
    """
    return timezone.make_aware(datetime.combine(day, time.min))


def _game_filters(challenge, prefix="game__"):
    """Condiciones sobre el juego de un reto de número de juegos o de horas: su género, su plataforma y sus años.

    Pantallas: Retos (cómo vas en cada uno), Página del reto (tu progreso y la clasificación), Ficha del juego (al
    pasar un juego a «Completado» se mira si consigues un reto).
    """
    filters = {}
    if challenge.genre_id:
        filters[f"{prefix}genres"] = challenge.genre_id
    if challenge.platform_id:
        filters[f"{prefix}platforms"] = challenge.platform_id
    if challenge.released_from:
        filters[f"{prefix}first_release_date__year__gte"] = challenge.released_from
    if challenge.released_to:
        filters[f"{prefix}first_release_date__year__lte"] = challenge.released_to
    return filters


def _completed(challenge, users):
    """Juegos de la biblioteca de users que cuentan en un reto de juegos: los completados entre sus fechas que son de
    los suyos (juegos concretos) o que cumplen sus filtros (número de juegos).

    Pantallas: Retos (cómo vas en cada uno), Página del reto (tu progreso y la clasificación), Ficha del juego (al
    pasar un juego a «Completado» se mira si consigues un reto).
    """
    entries = LibraryEntry.objects.filter(user__in=users, status=LibraryEntry.Status.COMPLETED)
    if challenge.kind == Challenge.Kind.GAMES:
        entries = entries.filter(game__in=challenge.games.all())
    else:
        entries = entries.filter(**_game_filters(challenge))
    if challenge.starts_on:
        entries = entries.filter(completed_at__gte=_start(challenge.starts_on))
    if challenge.ends_on:
        entries = entries.filter(completed_at__lt=_start(challenge.ends_on + timedelta(days=1)))
    return entries


def done_by(challenge, users):
    """Cuánto lleva cada uno de users en challenge: {id del usuario: juegos terminados o minutos jugados}. Los que no
    llevan nada no salen.

    Pantallas: Retos (cómo vas en cada uno), Página del reto (tu progreso y la clasificación), Nuevo reto (al crearlo
    ya cuenta lo que llevas), Ficha del juego (al pasar un juego a «Completado» se mira si consigues un reto).
    """
    if challenge.kind == Challenge.Kind.HOURS:
        sessions = PlaySession.objects.filter(user__in=users, **_game_filters(challenge))
        if challenge.starts_on:
            sessions = sessions.filter(day__gte=challenge.starts_on)
        if challenge.ends_on:
            sessions = sessions.filter(day__lte=challenge.ends_on)
        return dict(sessions.values("user").annotate(n=Sum("minutes")).values_list("user", "n"))
    return dict(_completed(challenge, users).values("user").annotate(n=Count("id")).values_list("user", "n"))


def summary(challenge, done):
    """Cómo va alguien que lleva done (lo de done_by) en challenge, para enseñarlo: lo hecho y el objetivo en juegos o
    en horas (value y goal_value), el porcentaje (percent, como mucho 100) y si ha llegado (reached).

    Pantallas: Retos (cómo vas en cada uno), Página del reto (tu progreso y la clasificación).
    """
    goal = challenge.goal
    hours = challenge.kind == Challenge.Kind.HOURS
    return {
        "value": round(done / 60, 1) if hours else done,
        "goal_value": challenge.target if hours else goal,
        "percent": min(100, round(100 * done / goal)) if goal else 0,
        "reached": bool(goal) and done >= goal,
        "hours": hours,
    }


def finished_games(challenge, user):
    """Ids de los juegos de un reto de juegos concretos que user ha terminado y cuentan.

    Pantallas: Página del reto (los juegos que llevas de uno de juegos concretos).
    """
    return set(_completed(challenge, [user]).values_list("game_id", flat=True))


def mark_completed(challenge, done):
    """Apunta que lo han conseguido (Participation.completed_at, una sola vez) los participantes de challenge que, según
    done (lo que da done_by), han llegado al objetivo, y se lo cuenta a cada uno (_award). Devuelve los ids de los
    usuarios a los que se les ha apuntado ahora.

    Cada uno se apunta solo si aún no lo estaba en ese momento, para que dos repasos a la vez no lo cuenten dos veces.

    Pantallas: Retos (cómo vas en cada uno: quién lo ha conseguido), Página del reto (tu progreso y la clasificación),
    Perfil (insignias de la sección «Retos»), Avisos (reto conseguido), Inicio (feed: «consiguió el reto»).
    """
    goal = challenge.goal
    if not goal:
        return []
    reached = [user_id for user_id, value in done.items() if value >= goal]
    pending = Participation.objects.filter(challenge=challenge, user_id__in=reached, completed_at__isnull=True)
    won = []
    for participation in pending.select_related("user__profile"):
        marked = Participation.objects.filter(pk=participation.pk, completed_at__isnull=True).update(
            completed_at=timezone.now(),
        )
        if marked:
            _award(challenge, participation.user)
            won.append(participation.user_id)
    return won


def _award(challenge, user):
    """Le cuenta a user que ha conseguido challenge: un aviso en la campana y «consiguió el reto» en su actividad.

    Pantallas: Avisos (reto conseguido), todas (el número de la campana), Inicio (feed), Perfil (pestaña Actividad).
    """
    notices.notify(
        user, Notification.Kind.CHALLENGE_WON, f"¡Has conseguido el reto «{challenge.title}»!",
        challenge.get_absolute_url(),
    )
    activity.challenge_won(user, challenge)


def check(user):
    """Mira si user ha conseguido alguno de los retos en los que está y que aún no tenía: los que siguen en marcha y los
    que terminaron hace menos de GRACE_DAYS días, porque una sincronización a mano puede llegar tarde. Los cerrados ya
    no se miran.

    Lo llaman la biblioteca al pasar un juego a «Completado» y la importación al apuntar sesiones. Devuelve los retos
    conseguidos ahora.

    Pantallas: Ficha del juego (al pasar un juego a «Completado»), Editar perfil (Cuentas vinculadas: al importar
    horas), Perfil (modal «Vincular cuentas» de su dueño; insignias de la sección «Retos»), Retos (cómo vas en cada
    uno), Página del reto (tu progreso y la clasificación), Avisos (reto conseguido).
    """
    since = timezone.localdate() - timedelta(days=GRACE_DAYS)
    pending = Challenge.objects.filter(
        participations__user=user, participations__completed_at__isnull=True, closed_at__isnull=True,
    ).exclude(ends_on__lt=since)
    return [challenge for challenge in pending if mark_completed(challenge, done_by(challenge, [user]))]


def showcase(owner, viewer):
    """Lo que enseña a viewer la sección «Retos» del escaparate de owner, solo con los retos que viewer puede ver (los
    privados, solo su creador): sus insignias (won, los que ha conseguido, del último al primero, cada uno con won_at)
    y los que lleva en marcha (active, los que no han terminado, primero los que antes acaban, cada uno con cómo va en
    mine), y cuántos hay de cada (won_count y active_count). None si no hay nada que enseñar.

    Pantallas: Perfil (sección «Retos» del escaparate), Editar perfil (vista previa).
    """
    joined = Participation.objects.filter(user=owner)
    won_at = dict(joined.filter(completed_at__isnull=False).values_list("challenge_id", "completed_at"))
    # Con una subconsulta: filtrar por la participación antes de contar haría que solo se contara la suya.
    challenges = list(Challenge.objects.visible_to(viewer).filter(pk__in=joined.values("challenge_id")).with_counts())
    won = sorted((challenge for challenge in challenges if challenge.pk in won_at), key=lambda c: won_at[c.pk], reverse=True)
    for challenge in won:
        challenge.won_at = won_at[challenge.pk]
    active = [challenge for challenge in challenges if challenge.pk not in won_at and not challenge.is_over]
    if not won and not active:
        return None
    active.sort(key=lambda challenge: (challenge.ends_on is None, challenge.ends_on or date.max, -challenge.pk))
    shown = active[:SHOWCASE_ACTIVE]
    for challenge in shown:
        challenge.mine = summary(challenge, done_by(challenge, [owner]).get(owner.pk, 0))
        challenge.won = False
    return {"won": won[:SHOWCASE_WON], "won_count": len(won), "active": shown, "active_count": len(active)}
