"""El repaso de la biblioteca (Repasar la biblioteca) y lo que cuesta el pendiente.

Las importaciones de Steam, PlayStation y Xbox nunca dan un juego por terminado: lo que se jugó hace tiempo entra como
«Jugado», sin saber si se terminó, y lo que se jugó hace poco, como «Jugando», aunque luego se deje. El repaso los va
sacando de uno en uno (queue) con lo que NextPlay ya sabe de cada uno (hint: horas, última partida, logros y lo que
suele durar según IGDB) para que se contesten con un botón (answer). Lo repasado no se vuelve a preguntar
(LibraryEntry.reviewed_at).

value es lo que cuesta hoy el pendiente de alguien, contando solo lo que tiene precio.

Pantallas: Repasar la biblioteca, Perfil (aviso de lo que queda por repasar; lo que cuesta su pendiente).
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Max
from django.utils import timezone
from django.utils.timesince import timesince

from accounts import achievements
from accounts.models import UnlockedAchievement
from challenges import progress as challenge_progress
from games.models import MULTIPLAYER_MODE_ID, format_price, half_hours
from social import activity

from .models import HOURS_SOURCES, LibraryEntry

Status = LibraryEntry.Status

# Un juego en «Jugando» que no se toca en estos días (ni se juega ni se cambia) se pregunta en el repaso.
STALE_DAYS = 180
# Si sus horas vienen de una importación, que dice cuándo se jugó por última vez, basta con estos días sin jugarlo (y
# sin tocarlo): es el «lleva semanas sin sesiones» del diario de partidas.
IDLE_DAYS = 30
# Con multijugador y más de estas veces lo que suele durar, es de los que se juegan sin parar (Rocket League,
# Counter-Strike…): IGDB les da modo de un jugador y una duración, pero las horas no dicen si se terminó.
SERVICE_FACTOR = 4

# Respuestas del repaso → estado al que pasa el juego (None: se queda como estaba).
ANSWERS = {
    "terminado": Status.COMPLETED,
    "dejado": Status.DROPPED,
    "jugando": Status.PLAYING,
    "pendiente": Status.BACKLOG,
    "saltar": None,
}


def last_played(entry):
    """La última partida de entry en cualquier plataforma (Steam, PlayStation o Xbox), o None si no se sabe.

    Pantallas: Repasar la biblioteca (cuándo lo jugaste por última vez y la fecha con la que se apunta terminado).
    """
    dates = [entry.last_played_on(key) for key in HOURS_SOURCES]
    dates = [day for day in dates if day]
    return max(dates) if dates else None


def is_stale(entry, now=None):
    """Indica si entry está en «Jugando» pero parece que ya no se juega. Si alguna importación dice cuándo fue su
    última partida, cuando es de hace más de IDLE_DAYS días y desde entonces tampoco se ha tocado; si no, cuando no se
    toca desde hace STALE_DAYS días: ni se juega (las importaciones lo cambiarían) ni se cambia a mano ni se repasa
    (todo eso mueve updated_at).

    Pantallas: Repasar la biblioteca (los de «Jugando» que se preguntan), Perfil (aviso de lo que queda por repasar).
    """
    if entry.status != Status.PLAYING:
        return False
    now = now or timezone.now()
    played_on = last_played(entry)
    if played_on is not None:
        cutoff = now - timedelta(days=IDLE_DAYS)
        return played_on < cutoff and entry.updated_at < cutoff
    return entry.updated_at < now - timedelta(days=STALE_DAYS)


def pending(entries):
    """Las de entries que pregunta el repaso: (las de «Jugando» que no se tocan desde hace STALE_DAYS días, las de
    «Jugado» que nunca se han repasado), sin ordenar. No consulta nada: sirve con la biblioteca ya cargada.

    Pantallas: Repasar la biblioteca, Perfil (aviso de lo que queda por repasar).
    """
    now = timezone.now()
    playing = [entry for entry in entries if is_stale(entry, now)]
    played = [entry for entry in entries if entry.status == Status.PLAYED and entry.reviewed_at is None]
    return playing, played


def queue(user):
    """Lo que le queda por repasar a user, en el orden en que se pregunta: primero los de «Jugando» que llevan más
    tiempo sin tocarse; después los de «Jugado», primero los que tienen todos sus logros o el platino (los que mejor se
    contestan) y luego los de más horas. Los juegos sin final (Game.is_endless: multijugador masivo, battle royale o
    cientos de horas según IGDB) van al fondo de cada grupo, porque sus horas no dicen si se terminaron.

    Pantallas: Repasar la biblioteca (el siguiente juego y cuántos quedan).
    """
    entries = (
        LibraryEntry.objects.filter(user=user, status__in=[Status.PLAYING, Status.PLAYED])
        .select_related("game").prefetch_related("game__modes")
    )
    playing, played = pending(list(entries))
    complete = _complete_games(user, [entry.game_id for entry in played])
    playing.sort(key=lambda entry: (entry.game.is_endless, entry.updated_at))
    played.sort(key=lambda entry: (
        entry.game.is_endless, entry.game_id not in complete, -(entry.playtime_minutes or 0), entry.game.name.casefold(),
    ))
    return playing + played


def _complete_games(user, game_ids):
    """Ids de los juegos de game_ids de los que user tiene todos los logros o el platino en alguna plataforma.

    Pantallas: Repasar la biblioteca (los que se preguntan primero).
    """
    return {
        game_id for game_id, rows in achievements.progress_by_game(user, game_ids).items()
        if any(row["complete"] or row["platinum"] for row in rows)
    }


def _number(value):
    """Un número como se escribe en España, sin decimales de sobra: 12,5 o 3.

    Pantallas: Repasar la biblioteca (las horas de cada juego).
    """
    return f"{value:g}".replace(".", ",")


def hint(entry):
    """Lo que NextPlay sabe de entry para contestarlo en el repaso: {"lines": frases con sus horas, su última partida y
    sus logros, "suggest": la respuesta que propone (clave de ANSWERS) o None, "finished_on": la fecha que se apuntará
    si se marca terminado, o None si sería hoy, "finished_from": de dónde sale esa fecha}.

    Con todos los logros o el platino propone «terminado», con la fecha de su último logro si se conoce (los de Steam
    se guardan uno a uno) o, si no, la de su última partida. También lo propone si lleva más horas de lo que suele
    durar según los jugadores de IGDB (Game.normal_hours), salvo en los juegos sin final, de los que solo dice que no
    se acaban: los de Game.is_endless y los multijugador en los que lleva más de SERVICE_FACTOR veces esa duración.

    Pantallas: Repasar la biblioteca (la tarjeta de cada juego).
    """
    lines = []
    hours = entry.playtime_hours
    played_on = last_played(entry)
    stale = entry.status == Status.PLAYING and is_stale(entry)
    if stale and played_on:
        lines.append(f"Está en «Jugando», pero no lo juegas desde hace {timesince(played_on, depth=1)}.")
    elif stale:
        lines.append(f"Está en «Jugando», pero no se mueve desde hace {timesince(entry.updated_at, depth=1)}.")
    if stale and played_on:
        # La última partida ya está dicha: aquí solo van las horas.
        if hours:
            lines.append(f"Llevas {_number(hours)} h.")
    elif hours and played_on:
        lines.append(f"Llevas {_number(hours)} h; la última partida fue hace {timesince(played_on, depth=1)}.")
    elif hours:
        lines.append(f"Llevas {_number(hours)} h.")
    elif played_on:
        lines.append(f"La última partida fue hace {timesince(played_on, depth=1)}.")
    normal, endless, past_normal = entry.game.normal_hours, entry.game.is_endless, False
    multiplayer = any(mode.igdb_id == MULTIPLAYER_MODE_ID for mode in entry.game.modes.all())
    if normal and hours and multiplayer and hours > SERVICE_FACTOR * normal:
        endless = True
    if endless:
        lines.append("No tiene un final claro: es de los que se juegan sin parar.")
    elif normal and hours:
        usual = _number(half_hours(round(normal * 60)))
        past_normal = hours >= normal
        if past_normal:
            lines.append(f"Suele durar unas {usual} h, y tú llevas más.")
        else:
            left = _number(half_hours(round((normal - hours) * 60)) or 0.5)
            lines.append(f"Suele durar unas {usual} h: te faltarían unas {left} h.")
    rows = achievements.progress_by_game(entry.user_id, [entry.game_id]).get(entry.game_id, [])
    best = max(rows, key=lambda row: (row["platinum"], row["unlocked"] / row["total"]), default=None)
    complete = bool(best and (best["complete"] or best["platinum"]))
    if best and best["platinum"]:
        lines.append(f"Tienes el platino en {best['label']}.")
    elif complete:
        lines.append(f"Tienes todos sus {best['noun']} en {best['label']} ({best['unlocked']} de {best['total']}).")
    elif best:
        lines.append(f"Tienes {best['unlocked']} de sus {best['total']} {best['noun']} en {best['label']}.")
    finished_on, finished_from = None, ""
    if complete:
        finished_on = UnlockedAchievement.objects.filter(
            imported_id__in=[row["imported_id"] for row in rows],
        ).aggregate(last=Max("unlocked_at"))["last"]
        finished_from = "la de tu último logro" if finished_on else ""
    if finished_on is None and played_on:
        finished_on, finished_from = played_on, "la de tu última partida"
    return {
        "lines": lines,
        "suggest": "terminado" if complete or past_normal else None,
        "finished_on": finished_on,
        "finished_from": finished_from,
    }


def answer(entry, choice):
    """Apunta la respuesta del repaso (clave de ANSWERS) para entry: le pone el estado que toca y lo da por repasado.

    Si pasa a «Completado», la fecha es la de hint (su último logro o su última partida; hoy si no se sabe ninguna),
    para que no cuente en un reto que no le toca, y luego se mira si con eso consigue alguno. Lo que termina o deja va a
    su actividad, todo junto (social.activity.library_tidied), y no como una entrada por juego. Devuelve el estado con
    el que queda.

    Pantallas: Repasar la biblioteca (botones de cada tarjeta), Perfil (biblioteca y pestaña Actividad), Inicio (feed:
    «ordenó su biblioteca»), Retos, Página del reto (progreso), Avisos (reto conseguido).
    """
    status = ANSWERS[choice]
    now = timezone.now()
    entry.reviewed_at = now
    fields = ["reviewed_at", "updated_at"]
    if status is not None and status != entry.status:
        entry.completed_at = (hint(entry)["finished_on"] or now) if status == Status.COMPLETED else None
        entry.status = status
        fields += ["status", "completed_at"]
    entry.save(update_fields=fields)
    if status == Status.COMPLETED:
        challenge_progress.check(entry.user)
    if status in (Status.COMPLETED, Status.DROPPED):
        activity.library_tidied(entry.user, entry.game, status)
    return entry.status


def today_summary(user):
    """Lo que user ha repasado hoy, según cómo ha quedado cada juego: {"total", "completed", "dropped", "playing",
    "backlog", "played"} (en «played», los que ha saltado y siguen en «Jugado»).

    Pantallas: Repasar la biblioteca (lo que llevas hoy y el resumen al acabar).
    """
    start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    counts = dict(
        LibraryEntry.objects.filter(user=user, reviewed_at__gte=start)
        .values("status").annotate(n=Count("id")).values_list("status", "n")
    )
    return {"total": sum(counts.values()), **{value: counts.get(value, 0) for value in Status.values}}


def value(user):
    """Lo que cuesta hoy el pendiente de user en sus tiendas (todas si no ha elegido): {"count": juegos en
    «Pendiente», "priced": cuántos tienen precio, "total": lo que suman, "total_text": eso escrito}. Los que no vende
    nadie no cuentan: no se inventa lo que falta.

    Pantallas: Perfil (pestaña Pendiente: lo que cuesta).
    """
    entries = (
        LibraryEntry.objects.filter(user=user, status=Status.BACKLOG)
        .select_related("game").prefetch_related("game__prices")
    )
    shops = set(user.profile.shops) or None
    count, priced, total = 0, 0, Decimal("0")
    for entry in entries:
        count += 1
        best = entry.game.cheapest(shops)
        if best is not None:
            priced += 1
            total += best.price
    return {"count": count, "priced": priced, "total": total, "total_text": format_price(total)}
