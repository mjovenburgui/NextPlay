"""Importar juegos y horas desde un archivo CSV: el que exporta Playnite, que junta Epic, GOG, EA, Ubisoft, Battle.net,
Amazon…, o el de otro tracker.

Cada columna se dice en un paso (FIELDS) y las conocidas se reconocen solas (GUESSES); el tiempo puede venir en horas,
minutos, segundos o como «12:30:00» (TIME_UNITS) y la nota, sobre 5, 10 o 100 (RATING_SCALES). Los juegos se emparejan
por su nombre, como los de PlayStation, y cada tienda va a su plataforma (STORES), con las horas en
LibraryEntry.extra_hours. Steam, PlayStation y Xbox no se traen de aquí: se importan desde sus cuentas. El estado y la
nota del archivo solo se ponen en los juegos que entran nuevos: lo que ya tenías se queda como estaba.

Pantallas: Importar desde un archivo, Editar perfil (Cuentas vinculadas: el enlace), Juegos importados (los que no se
encuentran), Perfil (biblioteca y horas).
"""

import csv
import io
import re
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from library.models import LibraryEntry

from .matching import add_title, clean_name, match_by_name, normalize, save_titles, update_library
from .models import ExternalTitle

Status = LibraryEntry.Status
# Lo que se dice de cada columna: clave → nombre en el formulario. Solo el nombre es obligatorio.
FIELDS = {
    "name": "Nombre del juego",
    "store": "Tienda",
    "time": "Tiempo jugado",
    "last": "Última partida",
    "status": "Estado",
    "rating": "Nota",
}
# Cabeceras que se reconocen solas para cada campo, sin fijarse en mayúsculas, espacios ni guiones: las de Playnite
# (Library Exporter Advanced) y las habituales de otros trackers.
GUESSES = {
    "name": ("name", "nombre", "title", "titulo", "game", "juego", "gamename"),
    "store": ("source", "sources", "library", "store", "tienda", "platform", "platforms", "plataforma", "launcher"),
    "time": ("playtime", "tiempojugado", "hoursplayed", "hours", "horas", "timeplayed", "minutesplayed", "minutos"),
    "last": ("lastactivity", "lastplayed", "ultimapartida", "lastplaydate", "lastsession"),
    "status": ("completionstatus", "status", "estado", "shelf", "shelves"),
    "rating": ("userscore", "rating", "nota", "score", "myrating", "puntuacion"),
}
# Unidades del tiempo jugado: clave → (nombre, minutos por unidad; None si viene como «horas:minutos:segundos»).
TIME_UNITS = {
    "hms": ("Como 12:30:00 (horas:minutos:segundos)", None),
    "horas": ("En horas", 60),
    "minutos": ("En minutos", 1),
    "segundos": ("En segundos (Playnite)", 1 / 60),
}
# Escalas de la nota: clave → (nombre, nota máxima). En NextPlay va de 1 a 10 medias estrellas.
RATING_SCALES = {"5": ("Sobre 5", 5), "10": ("Sobre 10", 10), "100": ("Sobre 100 (Playnite)", 100)}
# Palabras de la columna de la tienda → plataforma de ExternalTitle, en orden: gana la primera que aparece.
STORES = (
    ("epic", "epic"), ("gog", "gog"), ("battle.net", "battlenet"), ("battlenet", "battlenet"), ("blizzard", "battlenet"),
    ("ea app", "ea"), ("origin", "ea"), ("electronic arts", "ea"), ("ubisoft", "ubisoft"), ("uplay", "ubisoft"),
    ("amazon", "amazon"), ("prime gaming", "amazon"), ("steam", "steam"), ("playstation", "psn"), ("psn", "psn"),
    ("xbox", "xbox"), ("microsoft store", "xbox"),
)
# Lo que se importa desde la cuenta de la plataforma: del archivo se salta, para no contar dos veces sus horas.
ACCOUNT_PLATFORMS = {"steam", "psn", "xbox"}
# Tiendas de PC: sus juegos se buscan entre los que salen en PC.
PC_PLATFORM = "PC (Microsoft Windows)"
# Palabras de la columna del estado → estado de la biblioteca (sin las que no dicen nada, como «En la lista de deseos»).
STATUSES = (
    ("completed", Status.COMPLETED), ("beaten", Status.COMPLETED), ("completado", Status.COMPLETED),
    ("terminado", Status.COMPLETED), ("abandon", Status.DROPPED), ("dropped", Status.DROPPED),
    ("abandonado", Status.DROPPED), ("playing", Status.PLAYING), ("jugando", Status.PLAYING),
    ("played", Status.PLAYED), ("jugado", Status.PLAYED), ("plan to play", Status.BACKLOG),
    ("not played", Status.BACKLOG), ("backlog", Status.BACKLOG), ("pendiente", Status.BACKLOG),
    ("on hold", Status.BACKLOG),
)
# Límites del archivo y filas de la vista previa.
MAX_BYTES = 2_000_000
MAX_ROWS = 5000
PREVIEW_ROWS = 10
DATE_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%m/%d/%Y %H:%M:%S")


class CSVError(ValueError):
    """El archivo no se puede leer como CSV (vacío, demasiado grande o sin cabecera)."""


def read(content):
    """Lee un CSV (bytes) y devuelve (cabeceras, filas como dicts), como mucho MAX_ROWS. Entiende UTF-8 con o sin BOM y
    Latin-1, y adivina el separador (coma, punto y coma o tabulador). Lanza CSVError si no hay nada que leer.

    Pantallas: Importar desde un archivo.
    """
    if len(content) > MAX_BYTES:
        raise CSVError(f"El archivo pesa más de {MAX_BYTES // 1_000_000} MB.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    if not text.strip():
        raise CSVError("El archivo está vacío.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [header.strip() for header in reader.fieldnames or [] if header and header.strip()]
    if not headers:
        raise CSVError("El archivo no tiene una fila de cabecera con el nombre de cada columna.")
    rows = []
    for row in reader:
        rows.append({(key or "").strip(): (value or "").strip() for key, value in row.items() if isinstance(value, str)})
        if len(rows) >= MAX_ROWS:
            break
    return headers, rows


def _key(header):
    """Una cabecera para compararla con GUESSES: en minúsculas, sin tildes, espacios, guiones ni símbolos.

    Pantallas: Importar desde un archivo.
    """
    return re.sub(r"[^a-z0-9]", "", normalize(header))


def guess(headers):
    """Qué columna es cada campo de FIELDS según su cabecera (GUESSES): {campo: cabecera o ""}.

    Pantallas: Importar desde un archivo (las columnas ya elegidas).
    """
    keys = {_key(header): header for header in headers}
    return {field: next((keys[name] for name in GUESSES[field] if name in keys), "") for field in FIELDS}


def guess_unit(rows, column):
    """La unidad más probable del tiempo jugado (clave de TIME_UNITS) según los valores de column: «12:30:00» es
    horas:minutos:segundos; números de más de 10.000 son segundos (lo que exporta Playnite) y, si no, horas.

    Pantallas: Importar desde un archivo.
    """
    values = [row.get(column, "") for row in rows[:50] if row.get(column)]
    if any(":" in value for value in values):
        return "hms"
    numbers = [_number(value) for value in values]
    return "segundos" if any(number and number > 10_000 for number in numbers) else "horas"


def _number(value):
    """Un número de texto, con coma o punto decimal («12,5» o «12.5»), o None.

    Pantallas: Importar desde un archivo.
    """
    try:
        return float(value.replace(",", ".")) if value else None
    except ValueError:
        return None


def parse_time(value, unit):
    """Minutos jugados de value en la unidad unit (clave de TIME_UNITS), o None si no se entiende.

    Pantallas: Importar desde un archivo (el tiempo de cada fila).
    """
    if not value:
        return None
    if ":" in value:
        parts = [_number(part) for part in value.split(":")]
        if any(part is None for part in parts) or len(parts) > 3:
            return None
        hours, minutes, seconds = (parts + [0, 0])[:3]
        return round(hours * 60 + minutes + seconds / 60)
    number = _number(value)
    per_unit = TIME_UNITS.get(unit, TIME_UNITS["horas"])[1] or 60
    return None if number is None else round(number * per_unit)


def parse_when(value):
    """La fecha de value (ISO o como suele escribirse: 10/11/2026 18:54), con su hora en España, o None.

    Pantallas: Importar desde un archivo (la última partida de cada fila).
    """
    if not value:
        return None
    moment = parse_datetime(value.replace(" ", "T", 1)) if "-" in value[:5] else None
    if moment is None:
        day = parse_date(value[:10]) if "-" in value[:5] else None
        moment = datetime(day.year, day.month, day.day, 12) if day else None
    for fmt in DATE_FORMATS if moment is None else ():
        try:
            moment = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue
    if moment is None:
        return None
    return moment if timezone.is_aware(moment) else timezone.make_aware(moment)


def store_of(value, default=""):
    """La plataforma (clave de ExternalTitle.Platform) de una tienda tal como la escribe el archivo: la primera de STORES
    que aparece; si no aparece ninguna, «otras»; si la celda está vacía, default.

    Pantallas: Importar desde un archivo (la tienda de cada fila).
    """
    text = value.lower()
    if not text:
        return default
    return next((platform for word, platform in STORES if word in text), ExternalTitle.Platform.OTHER)


def status_of(value):
    """El estado de la biblioteca que dice value (STATUSES), o None si no se entiende o no dice nada.

    Pantallas: Importar desde un archivo (el estado de los juegos nuevos).
    """
    text = value.lower()
    return next((status for word, status in STATUSES if word in text), None) if text else None


def rating_of(value, scale):
    """La nota de value sobre scale (clave de RATING_SCALES) en medias estrellas de NextPlay (1 a 10), o None.

    Pantallas: Importar desde un archivo (la nota de los juegos nuevos).
    """
    number = _number(value)
    top = RATING_SCALES.get(scale, RATING_SCALES["10"])[1]
    if not number or number <= 0:
        return None
    return max(1, min(LibraryEntry.MAX_RATING, round(number / top * LibraryEntry.MAX_RATING)))


def interpret(rows, mapping, unit="horas", scale="10", default_store=""):
    """Las filas de un CSV como las entiende NextPlay con mapping ({campo: cabecera}): cada una con su nombre, su
    plataforma, sus minutos, su última partida, su estado y su nota, y «skip» con el motivo si no se importa (sin
    nombre, de una plataforma que se importa desde su cuenta o sin tienda).

    Pantallas: Importar desde un archivo (vista previa e importación).
    """
    def cell(row, field):
        """El valor de la columna de field en row, o vacío si no se ha elegido columna."""
        return row.get(mapping.get(field) or "", "") if mapping.get(field) else ""

    items = []
    for row in rows:
        name = clean_name(cell(row, "name"))
        platform = store_of(cell(row, "store"), default_store)
        skip = ""
        if not name:
            skip = "Sin nombre"
        elif not platform:
            skip = "Sin tienda: elige una para todas las filas"
        elif platform in ACCOUNT_PLATFORMS:
            skip = f"{ExternalTitle.Platform(platform).label}: se importa desde tu cuenta"
        items.append({
            "name": name,
            "platform": platform,
            "platform_label": ExternalTitle.Platform(platform).label if platform else "",
            "minutes": parse_time(cell(row, "time"), unit),
            "last_played": parse_when(cell(row, "last")),
            "status": status_of(cell(row, "status")),
            "rating": rating_of(cell(row, "rating"), scale),
            "skip": skip,
        })
    return items


def import_items(user, items):
    """Importa a la biblioteca de user las filas de interpret que no se saltan: por plataforma, como títulos importados
    emparejados por su nombre (como los de PlayStation) y después a la biblioteca (update_library, que apunta la
    importación en su actividad). A los juegos que entran nuevos les pone el estado y la nota del archivo si los trae;
    los que ya tenía se quedan como estaban. Devuelve {"added", "updated", "unmatched", "skipped", "platforms"}.

    Pantallas: Importar desde un archivo, Perfil (biblioteca y horas), Juegos importados (los que no se encuentran),
    Inicio (feed: la importación).
    """
    by_platform = {}
    for item in items:
        if not item["skip"]:
            by_platform.setdefault(item["platform"], []).append(item)
    before = set(LibraryEntry.objects.filter(user=user).values_list("game_id", flat=True))
    wanted, added, updated, unmatched = {}, 0, 0, 0
    for platform, rows in by_platform.items():
        titles = {}
        for item in rows:
            external_id = f"name:{normalize(item['name'])}"[:64]
            platforms = [] if platform == ExternalTitle.Platform.OTHER else [PC_PLATFORM]
            add_title(titles, external_id, item["name"], item["minutes"] or 0, item["last_played"], platforms)
            wanted[external_id] = item

        def auto_match(ids, titles=titles):
            """Busca por nombre (y en PC) los títulos que aún no tienen juego."""
            return match_by_name({i: (titles[i]["name"], titles[i]["platforms"]) for i in ids})

        games = save_titles(user, platform, titles, auto_match)
        unmatched += sum(1 for game in games.values() if game is None)
        new, changed = update_library(user, platform, opened_only=True, record_activity=True)
        added, updated = added + new, updated + changed
        # Estado y nota del archivo, solo en lo que acaba de entrar.
        for external_id, game in games.items():
            item = wanted[external_id]
            if game is None or game.pk in before or not (item["status"] or item["rating"]):
                continue
            entry = LibraryEntry.objects.filter(user=user, game=game).first()
            if entry is None:
                continue
            fields = []
            if item["status"] and item["status"] != entry.status:
                entry.status = item["status"]
                entry.completed_at = (item["last_played"] or timezone.now()) if item["status"] == Status.COMPLETED else None
                fields += ["status", "completed_at"]
            if item["rating"]:
                entry.rating = item["rating"]
                fields.append("rating")
            if fields:
                entry.save(update_fields=fields + ["updated_at"])
    return {
        "added": added,
        "updated": updated,
        "unmatched": unmatched,
        "skipped": sum(1 for item in items if item["skip"]),
        "platforms": [ExternalTitle.Platform(platform).label for platform in by_platform],
    }
