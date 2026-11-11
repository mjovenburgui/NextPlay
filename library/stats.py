"""Las estadísticas de un jugador, sacadas de su biblioteca, sus horas importadas, sus sesiones, sus logros y sus
reseñas: las de siempre (overview, la pestaña Estadísticas del perfil) y las de cada año (year_summary, el Resumen del
año), con una imagen para compartir el año (year_image).

Pantallas: Perfil (pestaña Estadísticas), Resumen del año.
"""

import functools
import io
import os
import unicodedata
from collections import Counter, defaultdict

from django.db.models import Sum
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from accounts import achievements
from accounts.models import UnlockedAchievement
from games.models import Game
from lists import presets

from .diary import duration
from .models import HOURS_SOURCES, LibraryEntry, PlaySession, Review

# Géneros, juegos, años y estrellas que salen en las listas de las estadísticas.
TOP_GENRES = 8
TOP_GAMES = 5
MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre",
    "diciembre",
)
# La imagen para compartir el resumen del año: vertical, como las de las redes, con el color del perfil si lo tiene.
IMAGE_SIZE = (1080, 1350)
IMAGE_BACKGROUND = (7, 9, 18)
DEFAULT_ACCENT = "#9d5cff"
# Letras de la imagen, (normal, negrita), en orden: la DejaVu de Linux (la de los servidores), Segoe o Arial de Windows y
# Arial de macOS, como en docs/generar_pdf.py. Sin ninguna, la de Pillow, que no tiene tildes ni eñes (_text las quita).
FONT_CANDIDATES = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
)


def _bars(items):
    """Añade a cada elemento de items (dicts con «value») su ancho en las barras («percent», respecto al mayor) y los
    devuelve.

    Pantallas: Perfil (pestaña Estadísticas: las barras), Resumen del año.
    """
    top = max((item["value"] for item in items), default=0)
    for item in items:
        item["percent"] = round(100 * item["value"] / top) if top else 0
    return items


def _hours(minutes):
    """Minutos en horas enteras, para las cifras grandes: «1.240» sale de 74.400 minutos.

    Pantallas: Perfil (pestaña Estadísticas), Resumen del año.
    """
    return round((minutes or 0) / 60)


def _played_by_year(owner, entries):
    """Juegos que owner jugó cada año, según lo que deja cada fuente: la última partida de cada juego, sus sesiones y
    sus logros con fecha. {año: número de juegos}, de los años con alguno.

    Pantallas: Perfil (pestaña Estadísticas: juegos jugados cada año).
    """
    found = defaultdict(set)
    for entry in entries:
        if entry.last_played:
            found[timezone.localdate(entry.last_played).year].add(entry.game_id)
    for day, game_id in PlaySession.objects.filter(user=owner).values_list("day", "game_id"):
        found[day.year].add(game_id)
    unlocks = UnlockedAchievement.objects.filter(imported__user=owner, unlocked_at__isnull=False)
    for moment, game_id in unlocks.values_list("unlocked_at", "imported__title__game_id"):
        if game_id:
            found[timezone.localdate(moment).year].add(game_id)
    return {year: len(games) for year, games in found.items()}


def overview(owner):
    """Las estadísticas de siempre de owner, para la pestaña Estadísticas de su perfil: las cifras («totals»: juegos,
    horas, completados, valorados, nota media, reseñas, logros conseguidos uno a uno, platinos y juegos al 100 %),
    y en barras las horas de cada plataforma, los estados de la biblioteca, los géneros (por horas, o por juegos si no
    hay horas), las notas que pone, las décadas de lanzamiento y los juegos jugados y completados cada año; además, los
    TOP_GAMES más jugados y los años de los que hay resumen («years»).

    Pantallas: Perfil (pestaña Estadísticas).
    """
    entries = list(
        LibraryEntry.objects.filter(user=owner).select_related("game").prefetch_related("game__genres")
    )
    minutes = sum(entry.total_minutes or 0 for entry in entries)
    platforms = sorted(
        ({"platform": key, "label": name, "value": sum(entry.minutes_on(key) or 0 for entry in entries)}
         for key, name in HOURS_SOURCES.items()),
        key=lambda item: -item["value"],
    )
    platforms = _bars([item for item in platforms if item["value"]])
    for item in platforms:
        item["text"] = f"{_hours(item['value'])} h"
    counts = Counter(entry.status for entry in entries)
    statuses = _bars([
        {"key": value, "label": label, "value": counts[value], "text": str(counts[value])}
        for value, label in LibraryEntry.Status.choices if counts[value]
    ])
    genre_minutes, genre_games = Counter(), Counter()
    for entry in entries:
        for genre in entry.game.genres.all():
            genre_games[genre.name] += 1
            genre_minutes[genre.name] += entry.total_minutes or 0
    by_hours = any(genre_minutes.values())
    ranking = (genre_minutes if by_hours else genre_games).most_common(TOP_GENRES)
    genres = _bars([
        {"label": name, "value": value, "text": f"{_hours(value)} h" if by_hours else str(value)}
        for name, value in ranking if value
    ])
    ratings = Counter(entry.rating for entry in entries if entry.rating)
    stars = _bars([
        {"label": f"{value / 2:g}".replace(".", ",") + " ★", "value": ratings[value], "text": str(ratings[value])}
        for value in range(LibraryEntry.MAX_RATING, 0, -1) if ratings[value]
    ])
    decades = Counter(
        entry.game.first_release_date.year // 10 * 10 for entry in entries if entry.game.first_release_date
    )
    decade_bars = _bars([
        {"label": f"Años {decade % 100:02d}" if decade < 2000 else f"Años {decade}", "value": decades[decade],
         "text": str(decades[decade])}
        for decade in sorted(decades)
    ])
    played = _played_by_year(owner, entries)
    completed = Counter(timezone.localdate(entry.completed_at).year for entry in entries if entry.completed_at)
    years = sorted(set(played) | set(completed))
    by_year = _bars([
        {"label": str(year), "value": played.get(year, 0), "completed": completed.get(year, 0),
         "text": f"{played.get(year, 0)} jugados · {completed.get(year, 0)} completados"}
        for year in years
    ])
    rated = [entry.stars for entry in entries if entry.rating]
    progress = achievements.progress_by_game(owner, [entry.game_id for entry in entries])
    most_played = sorted((entry for entry in entries if entry.total_minutes), key=lambda entry: -entry.total_minutes)
    return {
        "totals": {
            "games": len(entries),
            "hours": _hours(minutes),
            "completed": counts[LibraryEntry.Status.COMPLETED],
            "rated": len(rated),
            "average": sum(rated) / len(rated) if rated else None,
            "reviews": Review.objects.filter(user=owner, is_draft=False).count(),
            "unlocked": UnlockedAchievement.objects.filter(imported__user=owner).count(),
            **achievements.totals(progress),
        },
        "platforms": platforms,
        "statuses": statuses,
        "genres": genres,
        "genres_by_hours": by_hours,
        "stars": stars,
        "decades": decade_bars,
        "by_year": by_year,
        "most_played": [{"game": entry.game, "text": f"{_hours(entry.total_minutes)} h"} for entry in most_played[:TOP_GAMES]],
        "years": sorted(years, reverse=True),
    }


def year_summary(owner, year):
    """Lo que hizo owner en year, para su Resumen del año: los juegos que jugó («played», lo que da
    lists.presets.played_in) y los que terminó («completed»), lo jugado en sesiones («minutes» y su texto), los más
    jugados («top»: por sesiones si las hay y, si no, por logros conseguidos ese año), los logros de ese año («unlocked»
    y el más raro, «rarest»), las reseñas que publicó, los géneros a los que más jugó y el mes con más actividad
    («busiest», por sesiones o, si no hay, por logros).

    Pantallas: Resumen del año.
    """
    played = list(presets.played_in(owner, year).prefetch_related("game__genres"))
    completed = list(presets.completed_in(owner, year))
    sessions = PlaySession.objects.filter(user=owner, day__year=year)
    minutes = sessions.aggregate(total=Sum("minutes"))["total"] or 0
    unlocks = list(
        UnlockedAchievement.objects.filter(imported__user=owner, unlocked_at__year=year)
        .select_related("achievement", "imported__title")
    )
    if minutes:
        rows = sessions.values("game").annotate(total=Sum("minutes")).order_by("-total", "game")[:TOP_GAMES]
        games = Game.objects.in_bulk([row["game"] for row in rows])
        top = [{"game": games[row["game"]], "text": duration(row["total"])} for row in rows]
    else:
        per_game = Counter(unlock.imported.title.game_id for unlock in unlocks if unlock.imported.title.game_id)
        games = Game.objects.in_bulk([game_id for game_id, _ in per_game.most_common(TOP_GAMES)])
        top = [
            {"game": games[game_id], "text": f"{count} logro{'s' if count != 1 else ''}"}
            for game_id, count in per_game.most_common(TOP_GAMES)
        ]
    month_minutes = Counter()
    for day, value in sessions.values_list("day", "minutes"):
        month_minutes[day.month] += value
    month_unlocks = Counter(timezone.localdate(unlock.unlocked_at).month for unlock in unlocks if unlock.unlocked_at)
    busiest = (month_minutes or month_unlocks).most_common(1)
    genres = Counter(genre.name for entry in played for genre in entry.game.genres.all())
    rarest = min(
        (unlock for unlock in unlocks if unlock.achievement.percent is not None),
        key=lambda unlock: unlock.achievement.percent, default=None,
    )
    reviews = Review.objects.filter(user=owner, is_draft=False, published_at__year=year)
    return {
        "year": year,
        "played": played,
        "completed": completed,
        "minutes": minutes,
        "played_text": duration(minutes),
        "top": top,
        "top_by_sessions": bool(minutes),
        "unlocked": len(unlocks),
        "rarest": rarest,
        "reviews": reviews.count(),
        "genres": [name for name, _ in genres.most_common(3)],
        "busiest": MONTHS[busiest[0][0] - 1] if busiest else "",
    }


def _accent(owner):
    """El color del perfil de owner como (r, g, b), o el violeta de NextPlay si no ha elegido ninguno.

    Pantallas: Resumen del año (la imagen para compartir).
    """
    value = (owner.profile.accent_color or DEFAULT_ACCENT).lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


@functools.lru_cache(maxsize=None)
def _font_files():
    """(normal, negrita) de la primera letra de FONT_CANDIDATES que hay en este ordenador, o None.

    Pantallas: Resumen del año (la imagen para compartir).
    """
    return next((pair for pair in FONT_CANDIDATES if all(os.path.exists(path) for path in pair)), None)


def _font(size, bold=False):
    """La letra de la imagen en size píxeles, en negrita si bold: la de _font_files o, si no hay, la de Pillow.

    Pantallas: Resumen del año (la imagen para compartir).
    """
    files = _font_files()
    if files:
        return ImageFont.truetype(files[1 if bold else 0], size)
    return ImageFont.load_default(size=size)


def _text(value):
    """value tal cual si hay letra con tildes (_font_files); si se usa la de Pillow, sin tildes ni eñes, para que no
    salgan cuadros.

    Pantallas: Resumen del año (la imagen para compartir).
    """
    if _font_files():
        return value
    return "".join(char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char))


def _fit(draw, text, font, width):
    """text cortado con «…» para que quepa en width píxeles con font.

    Pantallas: Resumen del año (la imagen para compartir).
    """
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def year_image(owner, summary):
    """La imagen para compartir el Resumen del año de owner (summary, lo que da year_summary): un PNG vertical de
    IMAGE_SIZE con un degradado del color de su perfil, sus cifras del año y los juegos a los que más jugó, con la letra
    de _font. Devuelve los bytes del PNG.

    Pantallas: Resumen del año (la imagen para compartir y descargar).
    """
    width, height = IMAGE_SIZE
    accent = _accent(owner)
    image = Image.new("RGB", IMAGE_SIZE, IMAGE_BACKGROUND)
    draw = ImageDraw.Draw(image)
    # Degradado de arriba abajo: del color del perfil, apagado, al fondo de NextPlay.
    for y in range(height):
        mix = max(0.0, 1 - y / (height * 0.75)) * 0.55
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(round(back + (front - back) * mix) for front, back in zip(accent, IMAGE_BACKGROUND)),
        )
    margin = 90
    draw.text((margin, 90), "NEXTPLAY", font=_font(40, bold=True), fill=(255, 255, 255))
    draw.text((margin, 170), f"Mi {summary['year']}", font=_font(150, bold=True), fill=(255, 255, 255))
    name = _fit(draw, _text(owner.profile.name), _font(52), width - 2 * margin)
    draw.text((margin, 350), name, font=_font(52), fill=(220, 224, 245))
    figures = [
        (str(len(summary["played"])), "juegos jugados"),
        (str(len(summary["completed"])), "terminados"),
        (str(summary["unlocked"]), "logros"),
        (f"{_hours(summary['minutes'])} h" if summary["minutes"] else str(summary["reviews"]),
         "en sesiones" if summary["minutes"] else "reseñas"),
    ]
    for index, (value, label) in enumerate(figures):
        x = margin + (index % 2) * (width - 2 * margin) // 2
        y = 490 + (index // 2) * 210
        draw.text((x, y), value, font=_font(110, bold=True), fill=(255, 255, 255))
        draw.text((x, y + 128), _text(label), font=_font(40), fill=(180, 186, 214))
    if summary["top"]:
        draw.text((margin, 940), _text("A lo que más jugué"), font=_font(44, bold=True), fill=accent)
        for index, item in enumerate(summary["top"][:3]):
            line = _fit(draw, _text(f"{index + 1}. {item['game'].name}"), _font(54), width - 2 * margin)
            draw.text((margin, 1010 + index * 72), line, font=_font(54), fill=(255, 255, 255))
    draw.text((margin, height - 110), _text(f"nextplay · @{owner.username}"), font=_font(36), fill=(180, 186, 214))
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
