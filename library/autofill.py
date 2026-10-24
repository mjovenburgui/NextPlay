"""Plataforma y fechas de una partida sacadas de las importaciones, para que una reseña nueva se rellene sola.

Cada título importado (accounts.ImportedTitle) guarda sus minutos, la última partida, la primera (solo la
da PlayStation) y las plataformas en las que se jugó (PS4 o PS5 en PlayStation, los dispositivos de Xbox
y el PC en Steam, que además dice cuántos minutos fueron en una Steam Deck). Con eso el editor elige la
plataforma en la que más ha jugado el usuario y propone las fechas, diciendo de dónde sale cada dato para
que se pueda cambiar.
"""

from django.utils import timezone

from accounts.models import ExternalTitle, ImportedTitle
from games.models import STEAM_DECK_NAME

from .models import LibraryEntry

# Para los títulos importados antes de que se guardara dónde se jugaron: lo de Steam es del PC y en
# PlayStation lo dice el principio del ID del juego (CUSA en PS4 y PPSA en PS5).
STEAM_PLATFORM = "PC (Microsoft Windows)"
PSN_PREFIXES = {"CUSA": "PlayStation 4", "PPSA": "PlayStation 5"}


def _platforms(row):
    """Plataformas (nombres de IGDB) en las que se jugó un título importado: las guardadas o, si no hay, las que se deducen."""
    if row.platforms:
        return row.platforms
    if row.title.platform == ExternalTitle.Platform.STEAM:
        return [STEAM_PLATFORM]
    if row.title.platform == ExternalTitle.Platform.PSN and row.title.external_id[:4] in PSN_PREFIXES:
        return [PSN_PREFIXES[row.title.external_id[:4]]]
    return []


def _played_on(row):
    """Dónde se jugó un título importado y cuántos minutos en cada sitio: [(nombre de plataforma, minutos)].

    Lo jugado en una Steam Deck se separa de lo del PC. En lo demás no se sabe cómo se reparten las horas
    entre sus plataformas (una consola y su sucesora, por ejemplo) y cada una cuenta con todos los minutos.
    """
    names = _platforms(row)
    deck = min(row.deck_minutes, row.minutes)
    if not deck:
        return [(name, row.minutes) for name in names]
    rest = row.minutes - deck
    return [(name, rest) for name in names if rest] + [(STEAM_DECK_NAME, deck)]


def _duration(minutes):
    """Tiempo jugado corto para las notas: «12 h» o «40 min»."""
    return f"{round(minutes / 60)} h" if minutes >= 60 else f"{minutes} min"


def play_details(user, game, entry):
    """Plataforma y fechas de la partida de user en game según sus importaciones, para el editor de reseñas.

    Devuelve {"initial": {campo: valor}, "notes": {campo: de dónde sale}}: la plataforma del juego en la que
    más minutos lleva (la Steam Deck cuenta como una más en los juegos de PC); «Lo empecé el» con su primera
    partida (si alguna importación la da) y, si tiene el juego como Completado, «Lo terminé el» con su última
    partida. Vacíos si el juego no viene de ninguna importación o sus plataformas no son las del juego.
    """
    initial, notes = {}, {}
    rows = list(ImportedTitle.objects.filter(user=user, title__game=game, hidden=False).select_related("title"))
    by_name = {platform.name: platform for platform in game.review_platforms()}
    played = {}  # nombre de plataforma → [minutos, cuentas de las que sale]
    for row in rows:
        for name, minutes in _played_on(row):
            if name in by_name:
                total, sources = played.setdefault(name, [0, set()])
                played[name] = [total + minutes, sources | {row.title.get_platform_display()}]
    if played:
        name, (minutes, sources) = max(played.items(), key=lambda item: item[1][0])
        initial["platform"] = by_name[name]
        if len(played) > 1:
            split = " y ".join(f"{by_name[n].short_name} ({_duration(m)})" for n, (m, _) in played.items())
            notes["platform"] = f"Has jugado en {split}: va la de más horas."
        else:
            notes["platform"] = f"De tu cuenta de {' y '.join(sorted(sources))}."

    first = min((row for row in rows if row.first_played), key=lambda row: row.first_played, default=None)
    if first is not None:
        initial["started_on"] = timezone.localtime(first.first_played).date()
        notes["started_on"] = f"Tu primera partida en {first.title.get_platform_display()}."
    if entry is not None and entry.status == LibraryEntry.Status.COMPLETED:
        last = max((row for row in rows if row.last_played), key=lambda row: row.last_played, default=None)
        finished = timezone.localtime(last.last_played).date() if last is not None else None
        if finished and finished >= initial.get("started_on", finished):
            initial["finished_on"] = finished
            notes["finished_on"] = (
                f"Tu última partida en {last.title.get_platform_display()}: cámbiala si lo terminaste otro día."
            )
    return {"initial": initial, "notes": notes}
