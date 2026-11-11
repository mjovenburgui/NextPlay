"""El diario de partidas: lo que cada uno ha jugado y cuándo, sacado de sus importaciones sin apuntar nada a mano.

Las sesiones (library.models.PlaySession) son lo que ha subido el tiempo de cada juego entre dos sincronizaciones,
apuntado el día de su última partida; si la partida anterior fue otro día (since), se jugó en algún momento entre los
dos y la sesión es aproximada. Los logros con fecha (accounts.models.UnlockedAchievement) dicen además qué días se
jugó seguro, también de antes de que existieran las sesiones, así que el calendario no empieza vacío.

De aquí salen la pestaña Diario del perfil (page: el calendario de un año, la última partida y las últimas sesiones) y
la línea del diario en la ficha del juego (timeline).

Pantallas: Perfil (pestaña Diario), Ficha del juego (Tu biblioteca: tu primera partida y tus sesiones).
"""

from datetime import date, timedelta

from django.db.models import Count, Min, Sum
from django.db.models.functions import TruncDate
from django.utils import formats, timezone

from accounts.models import ImportedTitle, UnlockedAchievement
from games.models import Game

from .models import HOURS_SOURCES, LibraryEntry, PlaySession

# Minutos de un día desde los que su casilla del calendario sube de tono: el primero es «algo» y cada uno más, otro tono.
LEVELS = (1, 30, 90, 180)
# Lado de cada casilla del calendario y hueco entre ellas, en unidades del SVG.
CELL, GAP = 11, 3
# Sitio a la izquierda para los días de la semana y arriba para los meses.
LEFT, TOP = 22, 14
# Rótulos del calendario: los meses y, como en los calendarios de actividad, solo lunes, miércoles y viernes.
MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
WEEKDAYS = ((0, "L"), (2, "X"), (4, "V"))
# Sesiones de la lista «Últimas sesiones» de la pestaña Diario.
RECENT = 12
# Juegos de «Lo que más has jugado» de cada año.
TOP_GAMES = 3


def can_see(owner, viewer):
    """Si viewer puede ver el diario de owner: su dueño, siempre; los demás, si pueden ver su perfil y no lo ha ocultado
    en Editar perfil (Profile.diary_visible).

    Pantallas: Perfil (pestaña Diario), Editar perfil (vista previa: la pestaña como la ven los demás).
    """
    profile = owner.profile
    return viewer == owner or (profile.visible_to(viewer) and profile.diary_visible)


def duration(minutes):
    """Un tiempo de juego para leerlo: «45 min», «3 h» o «2 h 10 min».

    Pantallas: Perfil (pestaña Diario: el calendario y las sesiones), Ficha del juego (Tu biblioteca: tu última sesión).
    """
    hours, rest = divmod(int(minutes or 0), 60)
    if not hours:
        return f"{rest} min"
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def _local_day(moment):
    """El día de un momento en la hora de España, o None si no hay momento.

    Pantallas: Perfil (pestaña Diario: la última partida), Ficha del juego (Tu biblioteca: tu primera partida).
    """
    return timezone.localdate(moment) if moment else None


def _level(minutes):
    """Tono de la casilla de un día del calendario según lo jugado: 0 si nada y de 1 a 4 cuanto más (LEVELS).

    Pantallas: Perfil (pestaña Diario: el calendario).
    """
    return sum(1 for limit in LEVELS if minutes >= limit)


def _empty_day():
    """Lo que se sabe de un día antes de contar nada: sin minutos, sin juegos y sin logros.

    Pantallas: Perfil (pestaña Diario: el calendario).
    """
    return {"minutes": 0, "games": [], "exact": True, "achievements": 0, "achievement_games": []}


def _activity(user, year):
    """Lo que hizo user cada día de year: {día: {"minutes" (de sus sesiones), "games" (a qué jugó, sin repetir),
    "exact" (si todas sus sesiones son de ese día y no de desde la partida anterior), "achievements" (logros
    conseguidos ese día) y "achievement_games" (en qué juegos)}}. Solo salen los días con algo.

    Pantallas: Perfil (pestaña Diario: el calendario y las cifras del año).
    """
    days = {}
    for session in PlaySession.objects.filter(user=user, day__year=year).select_related("game").order_by("day", "pk"):
        info = days.setdefault(session.day, _empty_day())
        info["minutes"] += session.minutes
        if session.game.name not in info["games"]:
            info["games"].append(session.game.name)
        if session.since != session.day:
            info["exact"] = False
    unlocks = (
        UnlockedAchievement.objects.filter(imported__user=user, unlocked_at__year=year)
        .annotate(day=TruncDate("unlocked_at"))
        .values("day", "imported__title__name", "imported__title__game__name")
        .annotate(count=Count("pk"))
        .order_by("day")
    )
    for row in unlocks:
        info = days.setdefault(row["day"], _empty_day())
        info["achievements"] += row["count"]
        name = row["imported__title__game__name"] or row["imported__title__name"]
        if name not in info["achievement_games"]:
            info["achievement_games"].append(name)
    return days


def _title(day, info):
    """El texto que sale al pasar por encima de un día del calendario: la fecha, lo jugado y a qué, y los logros.

    Pantallas: Perfil (pestaña Diario: el calendario).
    """
    parts = []
    if info and info["minutes"]:
        played = f"{duration(info['minutes'])} de {', '.join(info['games'])}"
        if not info["exact"]:
            played += " (aproximado: incluye lo jugado desde tu partida anterior)"
        parts.append(played)
    if info and info["achievements"]:
        count = info["achievements"]
        parts.append(f"{count} logro{'s' if count != 1 else ''} en {', '.join(info['achievement_games'])}")
    return f"{formats.date_format(day, 'l, j M Y')}: {' · '.join(parts) or 'nada apuntado'}"


def _streak(days):
    """La racha más larga de días seguidos de una lista de días ordenada.

    Pantallas: Perfil (pestaña Diario: las cifras del año).
    """
    best = run = 0
    previous = None
    for day in days:
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return best


def calendar(user, year, today=None):
    """El calendario de actividad de user en year para dibujarlo en SVG y sus cifras.

    Una casilla por día, en columnas de semanas de lunes a domingo, con su tono según lo jugado (_level), si ese día
    consiguió algún logro, si aún no ha llegado y el texto que sale al pasar por encima (_title); los rótulos de los
    meses y de los días; el tamaño; y las cifras del año: lo jugado según las sesiones (minutes y su texto, played),
    los días con partida o logro, los días con logro, cuántas sesiones, la racha más larga de días seguidos y los
    juegos a los que más ha jugado (top_games, como {"game", "label"}). Las coordenadas van en texto: en una plantilla en
    español, los números largos podrían salir con punto de los miles y el SVG no sería válido.

    Pantallas: Perfil (pestaña Diario: el calendario y las cifras del año).
    """
    today = today or timezone.localdate()
    days = _activity(user, year)
    first, last = date(year, 1, 1), date(year, 12, 31)
    # El lunes de la semana del 1 de enero: ahí empieza la primera columna.
    origin = first - timedelta(days=first.weekday())
    step = CELL + GAP
    cells = []
    day = first
    while day <= last:
        info = days.get(day)
        cells.append({
            "x": str(LEFT + (day - origin).days // 7 * step), "y": str(TOP + day.weekday() * step),
            "level": _level(info["minutes"]) if info else 0,
            "achievement": bool(info and info["achievements"]),
            "future": day > today,
            "title": _title(day, info),
        })
        day += timedelta(days=1)
    columns = (last - origin).days // 7 + 1
    sessions = PlaySession.objects.filter(user=user, day__year=year)
    top = list(sessions.values("game").annotate(total=Sum("minutes")).order_by("-total", "game")[:TOP_GAMES])
    games = Game.objects.in_bulk([row["game"] for row in top])
    minutes = sum(info["minutes"] for info in days.values())
    return {
        "cells": cells,
        "months": [
            {"label": label, "x": str(LEFT + (date(year, month, 1) - origin).days // 7 * step)}
            for month, label in enumerate(MONTHS, 1)
        ],
        "weekdays": [{"label": label, "y": str(TOP + row * step + CELL - 2)} for row, label in WEEKDAYS],
        "width": str(LEFT + columns * step),
        "height": str(TOP + 7 * step),
        "cell": str(CELL),
        "minutes": minutes,
        "played": duration(minutes),
        "days": sum(1 for info in days.values() if info["minutes"] or info["achievements"]),
        "achievement_days": sum(1 for info in days.values() if info["achievements"]),
        "sessions": sessions.count(),
        "streak": _streak(sorted(day for day, info in days.items() if info["minutes"] or info["achievements"])),
        "top_games": [{"game": games[row["game"]], "label": duration(row["total"])} for row in top],
    }


def years(user, today=None):
    """Años con algo en el diario de user (sesiones o logros con fecha), del más reciente al más antiguo, con el año en
    curso siempre.

    Pantallas: Perfil (pestaña Diario: los años que se pueden elegir).
    """
    today = today or timezone.localdate()
    found = {today.year}
    found |= {day.year for day in PlaySession.objects.filter(user=user).dates("day", "year")}
    unlocks = UnlockedAchievement.objects.filter(imported__user=user, unlocked_at__isnull=False)
    found |= {moment.year for moment in unlocks.datetimes("unlocked_at", "year")}
    return sorted(found, reverse=True)


def last_session(user):
    """La última partida de user según sus importaciones: {"game", "when" (fecha y hora), "platform" (nombre de la
    plataforma), "minutes" (lo que jugó ese día según sus sesiones, o None si no hay sesión de ese día), "label" (esos
    minutos para leerlos) y "exact" (si son todos de ese día)}, o None si ninguna importación dice cuándo jugó.

    Pantallas: Perfil (pestaña Diario: tu última partida).
    """
    best = None
    for key, name in HOURS_SOURCES.items():
        field = f"{key}_last_played"
        entry = (
            LibraryEntry.objects.filter(user=user, **{f"{field}__isnull": False})
            .select_related("game").order_by(f"-{field}").first()
        )
        if entry is not None and (best is None or getattr(entry, field) > best[1]):
            best = (entry, getattr(entry, field), key, name)
    if best is None:
        return None
    entry, when, key, name = best
    session = PlaySession.objects.filter(
        user=user, game=entry.game, platform=key, day=timezone.localdate(when),
    ).first()
    return {
        "game": entry.game,
        "when": when,
        "platform": name,
        "minutes": session.minutes if session else None,
        "label": duration(session.minutes) if session else "",
        "exact": bool(session and session.since == session.day),
    }


def recent(user, limit=RECENT):
    """Las últimas limit sesiones de user, de la más reciente a la más antigua, cada una con su tiempo para leerlo
    (label), si es de un solo día (exact) y el nombre de su plataforma (platform_name).

    Pantallas: Perfil (pestaña Diario: últimas sesiones).
    """
    sessions = list(PlaySession.objects.filter(user=user).select_related("game")[:limit])
    for session in sessions:
        session.label = duration(session.minutes)
        session.exact = session.since == session.day
        session.platform_name = HOURS_SOURCES.get(session.platform, session.platform)
    return sessions


def synced_at(user):
    """La última vez que user importó alguna de sus cuentas (Steam, PlayStation o Xbox), o None si nunca: hasta ahí
    llega lo que sabe el diario.

    Pantallas: Perfil (pestaña Diario: hasta cuándo llegan tus datos).
    """
    moments = [moment for moment in user.steam_accounts.values_list("synced_at", flat=True) if moment]
    for name in ("psn_account", "xbox_account"):
        # Sin esa cuenta, Django lanza un error que también es AttributeError: getattr da None.
        account = getattr(user, name, None)
        if account is not None and account.synced_at:
            moments.append(account.synced_at)
    return max(moments, default=None)


def page(owner, year=None, today=None):
    """Lo que enseña la pestaña Diario del perfil de owner: el año que se ve (year si tiene algo o es el actual; si no,
    el más reciente), si es el año en curso (current: en el móvil, el calendario empieza por el final), los años que se
    pueden elegir, su calendario, la última partida, las últimas sesiones y hasta cuándo llegan sus datos (synced_at).

    Pantallas: Perfil (pestaña Diario).
    """
    today = today or timezone.localdate()
    available = years(owner, today)
    if year not in available:
        year = available[0]
    return {
        "year": year,
        "current": year == today.year,
        "years": available,
        "calendar": calendar(owner, year, today),
        "last": last_session(owner),
        "recent": recent(owner),
        "synced_at": synced_at(owner),
    }


def timeline(user_id, game_id):
    """Lo que dice el diario de un juego de la biblioteca de un usuario, para la ficha: {"started" (el día de su primera
    partida, que solo da PlayStation, o None), "first_achievement" (si no se sabe la primera partida, el día de su primer
    logro, o None), "sessions" (cuántas sesiones hay) y "last" (la última, con label y exact, o None)}, o None si no hay
    nada que contar.

    Pantallas: Ficha del juego (Tu biblioteca: tu primera partida y tus sesiones).
    """
    sessions = PlaySession.objects.filter(user_id=user_id, game_id=game_id)
    count = sessions.count()
    started = ImportedTitle.objects.filter(user_id=user_id, title__game_id=game_id).aggregate(
        first=Min("first_played"),
    )["first"]
    first_unlock = UnlockedAchievement.objects.filter(
        imported__user_id=user_id, imported__title__game_id=game_id,
    ).aggregate(first=Min("unlocked_at"))["first"]
    if not (count or started or first_unlock):
        return None
    last = sessions.first() if count else None
    if last is not None:
        last.label = duration(last.minutes)
        last.exact = last.since == last.day
    return {
        "started": _local_day(started),
        "first_achievement": None if started else _local_day(first_unlock),
        "sessions": count,
        "last": last,
    }
