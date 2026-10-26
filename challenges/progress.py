"""El progreso de cada jugador en los retos, contado con su biblioteca.

En los de juegos (concretos o un número) cuentan los que ha pasado a «Completado» entre las fechas del reto
(LibraryEntry.completed_at); sin fecha de inicio cuentan también los de antes, aunque no se sepa cuándo los terminó.
En los de horas se suma lo jugado entre las fechas (library.models.PlaySession), que se apunta desde que existen las
sesiones. done_by lo cuenta de golpe para muchos jugadores, para la clasificación de la página del reto.
"""

from datetime import datetime, time, timedelta

from django.db.models import Count, Sum
from django.utils import timezone

from library.models import LibraryEntry, PlaySession

from .models import Challenge, Participation


def _start(day):
    """El principio del día day en la hora de España, para compararlo con fechas y horas."""
    return timezone.make_aware(datetime.combine(day, time.min))


def _game_filters(challenge, prefix="game__"):
    """Condiciones sobre el juego de un reto de número de juegos o de horas: su género, su plataforma y sus años."""
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
    los suyos (juegos concretos) o que cumplen sus filtros (número de juegos)."""
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
    llevan nada no salen."""
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
    en horas (value y goal_value), el porcentaje (percent, como mucho 100) y si ha llegado (reached)."""
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
    """Ids de los juegos de un reto de juegos concretos que user ha terminado y cuentan."""
    return set(_completed(challenge, [user]).values_list("game_id", flat=True))


def mark_completed(challenge, done):
    """Apunta que lo han conseguido (Participation.completed_at, una sola vez) los participantes de challenge que, según
    done (lo que da done_by), han llegado al objetivo. Devuelve a cuántos se les ha apuntado ahora."""
    goal = challenge.goal
    if not goal:
        return 0
    reached = [user_id for user_id, value in done.items() if value >= goal]
    return Participation.objects.filter(
        challenge=challenge, user_id__in=reached, completed_at__isnull=True,
    ).update(completed_at=timezone.now())


def check(user):
    """Mira si user ha conseguido alguno de los retos en los que está y que aún no tenía (de los que no han terminado).

    Lo llaman la biblioteca al pasar un juego a «Completado» y la importación al apuntar sesiones. Devuelve los retos
    conseguidos ahora.
    """
    pending = Challenge.objects.filter(
        participations__user=user, participations__completed_at__isnull=True,
    ).exclude(ends_on__lt=timezone.localdate())
    return [challenge for challenge in pending if mark_completed(challenge, done_by(challenge, [user]))]
