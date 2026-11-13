"""Importar juegos y horas desde un archivo CSV: el que exporta Playnite con su complemento Library Exporter Advanced,
que junta Epic, GOG, EA, Ubisoft, Battle.net, Amazon…, o el de otro tracker.

Qué columna es cada cosa se dice en un paso (FIELDS), y las conocidas se reconocen solas (GUESSES, con los nombres que
pone Playnite en castellano y en inglés). El tiempo puede venir en horas, minutos, segundos (lo que escribe Playnite) o
como «12:30:00» (TIME_UNITS); la nota, sobre 5, 10 o 100 (RATING_SCALES), y las fechas, con el día o con el mes delante
(month_first). Los juegos se emparejan por su nombre, como los de PlayStation, y cada tienda va a su plataforma (STORES),
con las horas en LibraryEntry.extra_hours. Steam, PlayStation y Xbox no se traen de aquí: se importan desde sus cuentas.
El estado y la nota del archivo solo se ponen en los juegos que entran nuevos: lo que ya tenías se queda como estaba.
Las horas de un archivo cuentan en tu biblioteca, pero no como verificadas (library.models.VERIFIED_SOURCES), porque un
archivo se puede tocar a mano.

Pantallas: Importar desde un archivo, Editar perfil (Cuentas vinculadas: el enlace), Juegos importados (los que no se
encuentran), Perfil (biblioteca y horas).
"""

import csv
import io
import re
from datetime import datetime

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from challenges import progress as challenge_progress
from library.models import LibraryEntry

from .banner import refresh_user
from .matching import add_title, clean_name, match_by_name, name_key, normalize, save_titles, update_library
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
# Cabeceras que se reconocen solas para cada campo, sin fijarse en mayúsculas, tildes, espacios ni guiones, de más a
# menos segura: las de Playnite en inglés y en castellano («Sources», «Fuentes», «Time Played», «Tiempo jugado», «Last
# Played», «Jugado por última vez», «Completion Status», «Estado de finalización», «User Score», «Puntuación del
# usuario») y las habituales de otros trackers.
GUESSES = {
    "name": ("name", "nombre", "title", "titulo", "game", "juego", "gamename", "gametitle"),
    "store": (
        "sources", "source", "fuentes", "fuente", "library", "biblioteca", "store", "tienda", "launcher", "platforms",
        "platform", "plataformas", "plataforma",
    ),
    "time": ("timeplayed", "tiempojugado", "playtime", "hoursplayed", "hours", "horas", "minutesplayed", "minutos"),
    "last": (
        "lastplayed", "jugadoporultimavez", "lastactivity", "ultimapartida", "ultimaactividad", "lastplaydate",
        "lastsession",
    ),
    "status": ("completionstatus", "estadodefinalizacion", "status", "estado", "shelf", "shelves"),
    "rating": ("userscore", "puntuaciondelusuario", "rating", "nota", "score", "myrating", "puntuacion"),
}
# Unidades del tiempo jugado: clave → (nombre, minutos por unidad; None si viene como «horas:minutos:segundos»).
TIME_UNITS = {
    "hms": ("Como 12:30:00 (horas:minutos:segundos)", None),
    "horas": ("En horas", 60),
    "minutos": ("En minutos", 1),
    "segundos": ("En segundos (Playnite)", 1 / 60),
}
# Cabeceras del tiempo jugado de Playnite («Time Played» y «Tiempo jugado»): lo escribe en segundos, sin decimales.
PLAYNITE_TIME = ("timeplayed", "tiempojugado")
# Y las de su nota («User Score» y «Puntuación del usuario»), que va de 0 a 100.
PLAYNITE_SCORE = ("userscore", "puntuaciondelusuario")
# Escalas de la nota: clave → (nombre, nota máxima). En NextPlay va de 1 a 10 medias estrellas.
RATING_SCALES = {"5": ("Sobre 5", 5), "10": ("Sobre 10", 10), "100": ("Sobre 100 (Playnite)", 100)}
# Palabras de la columna de la tienda → plataforma de ExternalTitle, en orden: gana la primera que aparece. Son los
# nombres de las bibliotecas de Playnite («Epic», «GOG», «EA app», «Ubisoft Connect», «Battle.net», «Amazon Games»…).
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
# Palabras de la columna del estado → estado de la biblioteca, en orden: gana la primera que aparece, así que van
# primero las que niegan («Not Played» no es «Played»). Las que no dicen nada (como «Wishlist») no se pasan.
STATUSES = (
    ("not played", Status.BACKLOG), ("sin jugar", Status.BACKLOG), ("no jugado", Status.BACKLOG),
    ("plan to play", Status.BACKLOG), ("want to play", Status.BACKLOG), ("por jugar", Status.BACKLOG),
    ("planeado", Status.BACKLOG), ("on hold", Status.BACKLOG), ("en pausa", Status.BACKLOG),
    ("en espera", Status.BACKLOG), ("backlog", Status.BACKLOG), ("pendiente", Status.BACKLOG),
    ("completed", Status.COMPLETED), ("completado", Status.COMPLETED), ("beaten", Status.COMPLETED),
    ("superado", Status.COMPLETED), ("terminado", Status.COMPLETED), ("finished", Status.COMPLETED),
    ("abandon", Status.DROPPED), ("dropped", Status.DROPPED),
    ("playing", Status.PLAYING), ("jugando", Status.PLAYING),
    ("played", Status.PLAYED), ("jugado", Status.PLAYED),
)
# Límites del archivo y filas de la vista previa.
MAX_BYTES = 10_000_000
MAX_ROWS = 5000
PREVIEW_ROWS = 10
# Una columna con alguna celda más larga que esto (descripciones, notas, enlaces) no puede ser ninguno de los campos: no
# se ofrece ni se guarda entre un paso y otro.
LONG_CELL = 300
# Fechas con el día delante (España, y lo que escribe Playnite con Windows en castellano: «12/11/2026 18:54:03») o con
# el mes delante (Estados Unidos: «11/12/2026 6:54:03 PM»). Las ISO («2026-11-12 18:54») las lee parse_datetime.
DAY_FIRST = (
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y",
)
MONTH_FIRST = (
    "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y",
)
DATE_START = re.compile(r"\s*(\d{1,2})[/.-](\d{1,2})[/.-]\d{4}")
ISO_DATE = re.compile(r"\s*\d{4}-\d{1,2}-\d{1,2}")

# Una descripción de Playnite puede pasar del límite de serie del módulo csv (128 KB por celda).
csv.field_size_limit(MAX_BYTES)


class CSVError(ValueError):
    """El archivo no se puede leer como CSV (vacío, demasiado grande o sin cabecera)."""


def read(content):
    """Lee un CSV (bytes) y devuelve (cabeceras, filas como dicts), como mucho MAX_ROWS filas. Entiende UTF-8 con o sin
    BOM y Latin-1, adivina el separador (coma, punto y coma o tabulador) y deja fuera las columnas sin nombre, las
    repetidas y las que tienen alguna celda de más de LONG_CELL caracteres, que no pueden ser ninguno de los campos.
    Lanza CSVError si no hay nada que leer.

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
    records = csv.reader(io.StringIO(text), dialect=dialect)
    try:
        first = next(records, [])
        columns = {}  # cabecera → su posición, la primera vez que sale
        for index, header in enumerate(cell.strip() for cell in first):
            if header and header not in columns:
                columns[header] = index
        if not columns:
            raise CSVError("El archivo no tiene una fila de cabecera con el nombre de cada columna.")
        longest = dict.fromkeys(columns, 0)
        rows = []
        for record in records:
            row = {header: record[index].strip() if index < len(record) else "" for header, index in columns.items()}
            if not any(row.values()):
                continue
            for header, value in row.items():
                longest[header] = max(longest[header], len(value))
            rows.append(row)
            if len(rows) >= MAX_ROWS:
                break
    except csv.Error as exc:
        raise CSVError(f"No se ha podido leer el archivo como CSV: {exc}.") from exc
    headers = [header for header in columns if longest[header] <= LONG_CELL]
    if not headers:
        raise CSVError("Ninguna columna del archivo parece tener nombres de juegos.")
    return headers, [{header: row[header] for header in headers} for row in rows]


def _key(header):
    """Una cabecera para compararla con GUESSES: en minúsculas, sin tildes, espacios, guiones ni símbolos
    («Jugado por última vez» → «jugadoporultimavez»), como compara los nombres de los juegos matching.name_key.

    Pantallas: Importar desde un archivo.
    """
    return name_key(header)


def guess(headers):
    """Qué columna es cada campo de FIELDS según su cabecera (GUESSES): {campo: cabecera o ""}.

    Pantallas: Importar desde un archivo (las columnas ya elegidas).
    """
    keys = {}
    for header in headers:
        keys.setdefault(_key(header), header)
    return {field: next((keys[name] for name in GUESSES[field] if name in keys), "") for field in FIELDS}


def guess_unit(rows, column):
    """La unidad más probable del tiempo jugado de column (clave de TIME_UNITS): «12:30:00» es horas:minutos:segundos; si
    la cabecera dice la unidad, esa; las cabeceras de Playnite con números enteros, segundos, y si no, por lo grandes que
    son los números enteros (más de 60.000, segundos; más de 1000, minutos). Con decimales o números pequeños, horas.

    Pantallas: Importar desde un archivo (la unidad ya elegida).
    """
    key = _key(column)
    values = [row.get(column, "") for row in rows if row.get(column)]
    if any(":" in value for value in values):
        return "hms"
    for words, unit in ((("min",), "minutos"), (("seg", "sec"), "segundos"), (("hora", "hour"), "horas")):
        if any(word in key for word in words):
            return unit
    numbers = [number for number in map(_number, values) if number is not None]
    if not numbers or any(number != int(number) for number in numbers):
        return "horas"
    if key in PLAYNITE_TIME or max(numbers) > 60_000:
        return "segundos"
    return "minutos" if max(numbers) > 1000 else "horas"


def guess_scale(rows, column):
    """La escala más probable de la nota de column (clave de RATING_SCALES): la de Playnite es sobre 100 y, si no, la
    más pequeña en la que caben todas las notas.

    Pantallas: Importar desde un archivo (la escala ya elegida).
    """
    if _key(column) in PLAYNITE_SCORE:
        return "100"
    top = max((number for number in (_number(row.get(column, "")) for row in rows) if number), default=0)
    return "5" if top <= 5 else "10" if top <= 10 else "100"


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
    return None if number is None or number < 0 else round(number * per_unit)


def month_first(values):
    """Si las fechas de values llevan el mes delante (como en Estados Unidos: «11/12/2026 6:54:03 PM»). Lo dice un
    número por encima de 12 en el primer o en el segundo puesto; si no hay ninguno, el AM/PM; y si tampoco, se lee con
    el día delante, salvo que así salga alguna fecha futura y con el mes delante no.

    Pantallas: Importar desde un archivo (la última partida de cada fila).
    """
    starts = [match for match in map(DATE_START.match, values) if match]
    if any(int(match[1]) > 12 for match in starts):
        return False
    if any(int(match[2]) > 12 for match in starts):
        return True
    if any(value.strip().upper().endswith(("AM", "PM")) for value in values):
        return True
    now = timezone.now()

    def future(first):
        """Si alguna fecha sale en el futuro leyéndolas con el mes delante (first) o con el día."""
        return any(moment and moment > now for moment in (parse_when(value, first) for value in values))

    return future(False) and not future(True)


def parse_when(value, month_first=False):
    """La fecha de value (ISO o como suele escribirse: «10/11/2026 18:54», con el mes delante si month_first), con su
    hora en España, o None.

    Pantallas: Importar desde un archivo (la última partida de cada fila).
    """
    value = (value or "").strip()
    if not value:
        return None
    moment = None
    if ISO_DATE.match(value):
        if len(value) <= 10:
            day = parse_date(value)
            moment = datetime(day.year, day.month, day.day, 12) if day else None
        else:
            moment = parse_datetime(value.replace(" ", "T", 1))
    else:
        for fmt in MONTH_FIRST if month_first else DAY_FIRST:
            try:
                moment = datetime.strptime(value, fmt)
            except ValueError:
                continue
            # Un día sin hora, a mediodía: así no cambia de día al pasarlo a otra zona horaria.
            if "%H" not in fmt and "%I" not in fmt:
                moment = moment.replace(hour=12)
            break
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
    # Redondeando hacia arriba las mitades: un 85 sobre 100 son cuatro estrellas y media, no cuatro.
    return max(1, min(LibraryEntry.MAX_RATING, int(number / top * LibraryEntry.MAX_RATING + 0.5)))


def interpret(rows, mapping, unit="horas", scale="10", default_store=""):
    """Las filas de un CSV como las entiende NextPlay con mapping ({campo: cabecera}): cada una con su nombre, su
    plataforma, sus minutos (y sus horas), su última partida, su estado y su nota (con sus nombres para enseñarlos), y
    «skip» con el motivo si no se importa (sin nombre, de una plataforma que se importa desde su cuenta o sin tienda).

    Pantallas: Importar desde un archivo (vista previa e importación).
    """
    def cell(row, field):
        """El valor de la columna de field en row, o vacío si no se ha elegido columna."""
        return row.get(mapping.get(field) or "", "") if mapping.get(field) else ""

    order = month_first([cell(row, "last") for row in rows if cell(row, "last")])
    return [
        make_item(
            clean_name(cell(row, "name")), store_of(cell(row, "store"), default_store),
            parse_time(cell(row, "time"), unit), parse_when(cell(row, "last"), order),
            status_of(cell(row, "status")), rating_of(cell(row, "rating"), scale),
        )
        for row in rows
    ]


def make_item(name, platform, minutes, last_played, status, rating):
    """Una fila tal como la entiende NextPlay: sus datos, sus nombres para enseñarlos (tienda, horas, estado y
    estrellas) y «skip» con el motivo si no se importa (sin nombre, sin tienda o de una plataforma que se importa desde
    su cuenta). La usan los archivos (interpret) y la extensión de Playnite (accounts.playnite).

    Pantallas: Importar desde un archivo (vista previa e importación), Editar perfil (Cuentas vinculadas › Playnite).
    """
    skip = ""
    if not name:
        skip = "Sin nombre"
    elif not platform:
        skip = "Sin tienda: elige una para estas filas"
    elif platform in ACCOUNT_PLATFORMS:
        skip = f"{ExternalTitle.Platform(platform).label}: se importa desde tu cuenta"
    return {
        "name": name,
        "platform": platform,
        "platform_label": ExternalTitle.Platform(platform).label if platform else "",
        "minutes": minutes,
        "hours": None if minutes is None else round(minutes / 60, 1),
        "last_played": last_played,
        "status": status,
        "status_label": Status(status).label if status else "",
        "rating": rating,
        "stars": rating / 2 if rating else None,
        "skip": skip,
    }


def external_id(name):
    """El ID con el que se guarda un juego de un archivo en ExternalTitle: su nombre normalizado, como los de PlayStation
    sin ID («name:…»), para que las correcciones de uno sirvan a todos los que lo importen de esa tienda.

    Pantallas: Importar desde un archivo, Juegos importados (los que no se encuentran).
    """
    return f"name:{normalize(name)}"[:64]


def import_items(user, items):
    """Importa a la biblioteca de user las filas de interpret que no se saltan: por plataforma, como títulos importados
    emparejados por su nombre (como los de PlayStation) y después a la biblioteca (update_library, que apunta la
    importación en su actividad). Los juegos sin horas ni partida entran como «Pendiente», porque Playnite lista también
    los que nunca se han abierto. A los que entran nuevos les pone el estado y la nota del archivo si los trae
    (_keep_file_choices); los que ya tenía se quedan como estaban. Al acabar, vuelve a mirar la cabecera «Lo que estoy
    jugando» (accounts.banner), ya con esos estados. Devuelve {"added", "updated", "unmatched", "skipped",
    "platforms"}.

    Pantallas: Importar desde un archivo, Perfil (biblioteca y horas), Juegos importados (los que no se encuentran),
    Inicio (feed: la importación), Retos (progreso), Página del reto (progreso).
    """
    by_platform = {}
    for item in items:
        if not item["skip"]:
            by_platform.setdefault(item["platform"], []).append(item)
    before = set(LibraryEntry.objects.filter(user=user).values_list("game_id", flat=True))
    added = updated = unmatched = 0
    completed = False
    for platform, rows in by_platform.items():
        platforms = [] if platform == ExternalTitle.Platform.OTHER else [PC_PLATFORM]
        titles, first = {}, {}
        for item in rows:
            key = external_id(item["name"])
            add_title(titles, key, item["name"], item["minutes"] or 0, item["last_played"], platforms)
            # Si un juego sale dos veces en la misma tienda, vale el estado y la nota de su primera fila.
            first.setdefault(key, item)

        def auto_match(ids, titles=titles):
            """Busca por nombre (en PC, salvo los de «Otras tiendas») los títulos que aún no tienen juego."""
            return match_by_name({i: (titles[i]["name"], titles[i]["platforms"]) for i in ids})

        games = save_titles(user, platform, titles, auto_match)
        unmatched += sum(1 for game in games.values() if game is None)
        new, changed = update_library(user, platform, record_activity=True)
        added, updated = added + new, updated + changed
        completed = _keep_file_choices(user, games, first, before) or completed
    if completed:
        challenge_progress.check(user)
    # update_library ya puso al día la cabecera «Lo que estoy jugando», pero antes de los estados del archivo: un juego
    # que Playnite da por terminado no debe quedarse en ella.
    refresh_user(user)
    return {
        "added": added,
        "updated": updated,
        "unmatched": unmatched,
        "skipped": sum(1 for item in items if item["skip"]),
        "platforms": [ExternalTitle.Platform(platform).label for platform in by_platform],
    }


def _keep_file_choices(user, games, items, before):
    """Pone el estado y la nota del archivo (items: {ID externo: fila de interpret}) en los juegos de games ({ID externo:
    Game o None}) que acaban de entrar en la biblioteca de user, los que no estaban en before. Un juego completado lleva
    como fecha su última partida si se sabe, nunca la de hoy, para que no cuente en un reto que no le toca. Devuelve si
    ha completado alguno con fecha.

    Pantallas: Importar desde un archivo, Perfil (biblioteca: estado y nota), Retos (progreso).
    """
    completed = False
    for key, game in games.items():
        item = items[key]
        if game is None or game.pk in before or not (item["status"] or item["rating"]):
            continue
        entry = LibraryEntry.objects.filter(user=user, game=game).first()
        if entry is None:
            continue
        fields = []
        if item["status"] and item["status"] != entry.status:
            entry.status = item["status"]
            entry.completed_at = item["last_played"] if item["status"] == Status.COMPLETED else None
            fields += ["status", "completed_at"]
            completed = completed or entry.completed_at is not None
        if item["rating"] and item["rating"] != entry.rating:
            entry.rating = item["rating"]
            fields.append("rating")
        if fields:
            entry.save(update_fields=fields + ["updated_at"])
    return completed
