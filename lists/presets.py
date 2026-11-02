"""Plantillas de tier list que se rellenan solas con la biblioteca del usuario: «Jugados en 2026», sus más jugados, su
pendiente…

Cada plantilla (PRESETS) elige juegos de la biblioteca en un orden y da el título y los niveles de la tier list;
lists.views.from_library la crea con esos juegos en «Sin clasificar», o ya colocados por la nota que les ha puesto
(RATING_TIERS), para que solo haya que arrastrarlos. Las de un año son del año en curso: Steam solo da la última
partida y el historial de partidas (library.models.PlaySession) es reciente, así que en un año pasado faltarían los
juegos que se han vuelto a jugar después.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from django.db.models import F, Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounts.achievements import progress_by_game
from library.models import HOURS_SOURCES, LibraryEntry, PlaySession, Review

from .models import DEFAULT_TIERS, LIST_MAX_GAMES, MOSAIC_COVERS, GameList

# Juegos que necesita una plantilla para crear su tier list, y los que entran en la de los más jugados.
MIN_GAMES = 2
MOST_PLAYED = 30
# Niveles del pendiente, para decidir qué jugar después: de lo primero a lo que no se jugará.
BACKLOG_TIERS = (("Ya mismo", "#ff4d5e"), ("Pronto", "#ffc84a"), ("Algún día", "#4f8bff"), ("Nunca", "#9d5cff"))
# Nivel de serie (por su id: 0 es S y 5 es F) en el que empieza un juego según su nota, de 1 a 10 medias estrellas:
# cinco estrellas en S, cuatro o cuatro y media en A… y media estrella en F.
RATING_TIERS = {10: 0, 9: 1, 8: 1, 7: 2, 6: 2, 5: 3, 4: 3, 3: 4, 2: 4, 1: 5}


def library(user):
    """La biblioteca de user (LibraryEntry, con su juego) con los minutos jugados en todas las plataformas (minutes):
    de más a menos horas y, a igualdad, de lo añadido hace menos a lo que más."""
    minutes = sum((Coalesce(f"{key}_playtime_minutes", Value(0)) for key in HOURS_SOURCES), Value(0))
    return (
        LibraryEntry.objects.filter(user=user).select_related("game")
        .annotate(minutes=minutes).order_by("-minutes", "-created_at", "-id")
    )


def year_bounds(year):
    """Principio de year y del año siguiente, en la zona horaria del sitio."""
    return timezone.make_aware(datetime(year, 1, 1)), timezone.make_aware(datetime(year + 1, 1, 1))


def played_in(user, year):
    """Lo jugado en year: lo que tiene ese año su última partida de Steam, PlayStation o Xbox, partidas apuntadas
    (PlaySession) o una reseña empezada o terminada; lo completado ese año; y, si es el año en curso, lo que está
    jugando."""
    start, end = year_bounds(year)
    played = Q(completed_at__gte=start, completed_at__lt=end)
    for key in HOURS_SOURCES:
        played |= Q(**{f"{key}_last_played__gte": start, f"{key}_last_played__lt": end})
    played |= Q(game__in=PlaySession.objects.filter(user=user, day__year=year).values("game"))
    reviews = Review.objects.filter(Q(started_on__year=year) | Q(finished_on__year=year), user=user)
    played |= Q(game__in=reviews.values("game"))
    if year == timezone.localdate().year:
        played |= Q(status=LibraryEntry.Status.PLAYING)
    return library(user).filter(played)


def completed_in(user, year):
    """Lo terminado en year: lo pasado a «Completado» ese año o con una reseña terminada ese año."""
    start, end = year_bounds(year)
    finished = Review.objects.filter(user=user, finished_on__year=year).values("game")
    return library(user).filter(Q(completed_at__gte=start, completed_at__lt=end) | Q(game__in=finished))


def most_played(user, year):
    """Los MOST_PLAYED juegos con más horas, sumando las de todas las plataformas."""
    return library(user).filter(minutes__gt=0)[:MOST_PLAYED]


def full_achievements(user, year):
    """Lo que tiene con el platino de PlayStation o con todos los logros en alguna plataforma (accounts.achievements)."""
    entries = list(library(user))
    progress = progress_by_game(user, [entry.game_id for entry in entries])
    return [
        entry for entry in entries
        if any(item["platinum"] or item["complete"] for item in progress.get(entry.game_id, []))
    ]


def played_on(platform):
    """Cómo elige sus juegos la plantilla de una plataforma: lo que tiene horas en platform (clave de HOURS_SOURCES), de
    más a menos horas en ella."""
    field = f"{platform}_playtime_minutes"

    def entries(user, year):
        """Lo jugado en la plataforma de la plantilla, de más a menos horas en ella."""
        return library(user).filter(**{f"{field}__gt": 0}).order_by(F(field).desc(), "-id")

    return entries


def backlog(user, year):
    """Lo pendiente, de lo añadido hace menos a lo que más."""
    return library(user).filter(status=LibraryEntry.Status.BACKLOG).order_by("-created_at", "-id")


@dataclass(frozen=True)
class Preset:
    """Una plantilla: su clave (la que manda el formulario), su nombre y su explicación en «Nueva lista» (label y help),
    el título de la tier list que crea (label y title pueden llevar {year}), cómo elige los juegos (entries: del usuario
    y el año a sus LibraryEntry, en orden), sus niveles ([(nombre, color)], de mejor a peor) y si coloca de entrada los
    juegos que tienen nota (by_rating, solo con los niveles de serie)."""

    key: str
    label: str
    help: str
    title: str
    entries: Callable
    tiers: tuple = tuple(DEFAULT_TIERS)
    by_rating: bool = True

    def label_for(self, year):
        """Su nombre en «Nueva lista» en year."""
        return self.label.format(year=year)

    def title_for(self, year):
        """Título de la tier list que crea en year."""
        return self.title.format(year=year)

    def choose(self, user, year):
        """Los juegos de la biblioteca de user que entran en year, en su orden (LibraryEntry), como mucho LIST_MAX_GAMES."""
        return list(self.entries(user, year)[:LIST_MAX_GAMES])

    def tier_list(self):
        """Sus niveles como los guarda GameList.tiers: [{"id", "label", "color"}]."""
        return [{"id": n, "label": label, "color": color} for n, (label, color) in enumerate(self.tiers)]

    def tier_of(self, entry):
        """Id del nivel en el que empieza el juego de entry: con by_rating, el de su nota (RATING_TIERS); si no, o sin
        nota, ninguno (sin clasificar)."""
        return RATING_TIERS.get(entry.rating) if self.by_rating else None


PRESETS = {
    preset.key: preset for preset in [
        Preset(
            "jugados", "Jugados en {year}", "Lo que has jugado este año, según tus importaciones y tus fechas.",
            "Jugados en {year}", played_in,
        ),
        Preset("terminados", "Terminados en {year}", "Lo que has completado este año.", "Terminados en {year}", completed_in),
        Preset(
            "mas-jugados", f"Tus {MOST_PLAYED} más jugados", "Por horas, sumando todas tus plataformas.",
            f"Mis {MOST_PLAYED} juegos más jugados", most_played,
        ),
        Preset(
            "platinos", "Tus platinos y 100 %", "Lo que tienes con el platino o con todos los logros.",
            "Mis platinos y 100 %", full_achievements,
        ),
        *[
            Preset(
                f"jugados-{key}", f"Lo jugado en {name}", f"Tus juegos con horas en {name}, de más a menos.",
                f"Lo que he jugado en {name}", played_on(key),
            )
            for key, name in HOURS_SOURCES.items()
        ],
        Preset(
            "pendiente", "Tu pendiente", "Para decidir qué jugar después: ya mismo, pronto, algún día o nunca.",
            "Mi pendiente: ¿por cuál empiezo?", backlog, tiers=BACKLOG_TIERS, by_rating=False,
        ),
    ]
}


def choices(user):
    """Las plantillas del bloque «Desde tu biblioteca» de «Nueva tier list», en el año en curso, con lo que enseña
    cada una: su clave, su nombre y su explicación (key, label y help), cuántos juegos metería (count), las portadas de
    los primeros (preview_items y mosaic_gaps, como el mosaico de una lista), si se puede crear (ready: al menos
    MIN_GAMES) y, si user ya tiene una lista con el título que le pondría, esa lista (existing). Las que no tienen
    ningún juego no salen."""
    year = timezone.localdate().year
    titles = [preset.title_for(year) for preset in PRESETS.values()]
    existing = {game_list.title: game_list for game_list in GameList.objects.filter(user=user, title__in=titles)}
    found = []
    for preset in PRESETS.values():
        entries = preset.choose(user, year)
        if not entries:
            continue
        covers = entries[:MOSAIC_COVERS]
        found.append({
            "key": preset.key,
            "label": preset.label_for(year),
            "help": preset.help,
            "count": len(entries),
            "preview_items": covers,
            "mosaic_gaps": range(MOSAIC_COVERS - len(covers)),
            "ready": len(entries) >= MIN_GAMES,
            "existing": existing.get(preset.title_for(year)),
        })
    return found
