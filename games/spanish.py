"""Datos en castellano de los juegos: idiomas (voces y textos), nombre en España, portada europea y sinopsis en español.

Los idiomas, los nombres y la portada europea vienen de IGDB en la misma consulta de la importación
(SPANISH_FIELDS, que se añade a GAME_FIELDS). La sinopsis en español y los idiomas de la tienda vienen
de Steam (appdetails en español) y, si el juego no está en Steam o no tiene sinopsis, de la descripción
de la tienda de Microsoft. Steam admite pocas peticiones (unas 200 cada 5 minutos), así que esa parte
la hace el comando localize_games con pausas, nunca al abrir una ficha.
"""

import html
import re
import time

import requests
from django.utils import timezone

from . import xbox

# Campos de IGDB con los idiomas, los nombres alternativos y las versiones regionales (nombre y portada).
SPANISH_FIELDS = (
    "language_supports.language.locale, language_supports.language_support_type, "
    "alternative_names.name, alternative_names.comment, "
    "game_localizations.region, game_localizations.name, game_localizations.cover.image_id"
)
AUDIO_SUPPORT = 1  # language_support_type de IGDB: 1 voces, 2 subtítulos, 3 interfaz
EUROPE_REGION = 4  # región «Europe» de game_localizations
# Comentario de alternative_names con el que IGDB marca el título en España.
SPANISH_TITLE = "spanish title"
# Nombres alternativos que no sirven para buscar (los nombres de los ejecutables de Windows).
SKIPPED_NAME_COMMENTS = {"windows executable"}

STEAM_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
TIMEOUT = 15
# Nos identificamos ante Steam en vez de con el «python-requests» por defecto.
USER_AGENT = "NextPlay/1.0 (catálogo de videojuegos)"
# Pausa entre peticiones a Steam: unas 200 cada 5 minutos, que es lo que admite.
STEAM_PAUSE = 1.5
# Segundos que se espera antes de cada reintento cuando Steam corta la conexión o da error.
RETRY_WAITS = (5, 30)
# Nombres de idioma de Steam en español → código, el mismo que usa IGDB.
STEAM_LANGUAGES = {
    "Español de España": "es-ES", "Español de Hispanoamérica": "es-MX", "Inglés": "en-US", "Francés": "fr-FR",
    "Alemán": "de-DE", "Italiano": "it-IT", "Portugués de Portugal": "pt-PT", "Portugués de Brasil": "pt-BR",
    "Japonés": "ja-JP", "Coreano": "ko-KR", "Chino simplificado": "zh-CN", "Chino tradicional": "zh-TW",
    "Ruso": "ru-RU", "Polaco": "pl-PL", "Ucraniano": "uk-UA", "Checo": "cs-CZ", "Húngaro": "hu-HU",
    "Turco": "tr-TR", "Árabe": "ar", "Holandés": "nl-NL", "Sueco": "sv-SE", "Danés": "da-DK", "Finés": "fi-FI",
    "Noruego": "nb-NO", "Tailandés": "th-TH", "Vietnamita": "vi-VN", "Griego": "el-GR", "Rumano": "ro-RO",
    "Búlgaro": "bg-BG", "Indonesio": "id-ID",
}


class StoreError(Exception):
    """Fallo al pedir a Steam los datos de un juego (red, límite de peticiones o respuesta no válida)."""


def igdb_languages(data):
    """Idiomas de un juego de IGDB: {código: ["audio", "text"]}, con «text» si tiene interfaz o subtítulos."""
    languages = {}
    for item in data.get("language_supports") or []:
        code = (item.get("language") or {}).get("locale") or ""
        if not code:
            continue
        kind = "audio" if item.get("language_support_type") == AUDIO_SUPPORT else "text"
        kinds = languages.setdefault(code, [])
        if kind not in kinds:
            kinds.append(kind)
    return languages


def spanish_names(data):
    """(nombre en España, otros nombres para el buscador) de un juego de IGDB.

    El nombre en España es el primer nombre alternativo que IGDB marca como «Spanish title». Para el
    buscador se juntan, en minúsculas y sin repetir, los demás nombres alternativos (siglas, títulos de
    otros países...) y los de las versiones regionales, salvo los de los ejecutables.
    """
    alternatives = data.get("alternative_names") or []
    name_es = next(
        (alt["name"] for alt in alternatives if (alt.get("comment") or "").strip().casefold() == SPANISH_TITLE and alt.get("name")),
        "",
    )
    names = [
        alt.get("name") for alt in alternatives
        if (alt.get("comment") or "").strip().casefold() not in SKIPPED_NAME_COMMENTS
    ] + [local.get("name") for local in data.get("game_localizations") or []]
    own = (data.get("name") or "").strip().casefold()
    search = dict.fromkeys(name.strip().casefold() for name in names if name and name.strip().casefold() != own)
    return name_es[:255], " | ".join(search)


def europe_cover(data):
    """ID de imagen de la portada europea de un juego de IGDB (versión regional de Europa), o vacío si no tiene."""
    for local in data.get("game_localizations") or []:
        if local.get("region") == EUROPE_REGION and (local.get("cover") or {}).get("image_id"):
            return local["cover"]["image_id"]
    return ""


def apply_igdb(game, data):
    """Copia en game (sin guardarlo) los idiomas, el nombre en España, los otros nombres y la portada europea de IGDB."""
    game.languages = {**(game.languages or {}), "igdb": igdb_languages(data)}
    game.name_es, game.search_names = spanish_names(data)
    game.cover_eu_image_id = europe_cover(data)
    game.refresh_spanish()


def steam_languages(text):
    """Idiomas de la cadena supported_languages de Steam (pedida en español): {código: ["text"] o ["audio", "text"]}.

    Steam marca con un asterisco los idiomas con voces; lo que va después del primer <br> es la nota
    que explica el asterisco. Los idiomas que no están en STEAM_LANGUAGES se ignoran.
    """
    languages = {}
    for token in re.split(r"<br\s*/?>", text or "", maxsplit=1)[0].split(","):
        name = html.unescape(re.sub(r"<[^>]+>|\*", "", token)).strip()
        code = STEAM_LANGUAGES.get(name)
        if code:
            languages[code] = ["audio", "text"] if "*" in token else ["text"]
    return languages


def clean_text(value):
    """Texto plano a partir del HTML de una tienda: sin etiquetas, con las entidades traducidas y sin espacios de más."""
    value = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    value = html.unescape(re.sub(r"<[^>]+>", "", value))
    return "\n".join(" ".join(line.split()) for line in value.splitlines() if line.strip())


def steam_details(app_id, sleep=time.sleep):
    """Idiomas y sinopsis en español de una app de Steam: (idiomas, sinopsis); ({}, "") si Steam no la conoce.

    Los cortes de conexión y los errores de Steam (también el 429 por pedir demasiado) se reintentan
    tras esperar RETRY_WAITS; si sigue fallando, lanza StoreError.
    """
    for attempt, wait in enumerate((0, *RETRY_WAITS)):
        if wait:
            sleep(wait)
        try:
            response = requests.get(
                STEAM_DETAILS_URL,
                params={"appids": app_id, "l": "spanish", "cc": "es", "filters": "basic"},
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            item = (response.json() or {}).get(str(app_id)) or {}
            break
        except (requests.RequestException, ValueError, AttributeError) as exc:
            if attempt == len(RETRY_WAITS):
                raise StoreError(f"Steam no ha devuelto los datos de la app {app_id}: {exc}") from exc
    data = item.get("data") or {}
    if not item.get("success") or not data:
        return {}, ""
    return steam_languages(data.get("supported_languages")), clean_text(data.get("short_description"))


def localize_games(games, pause=STEAM_PAUSE, sleep=time.sleep):
    """Pide a las tiendas la sinopsis en español y los idiomas de Steam de esos juegos y los guarda.

    Steam va de uno en uno, con pause segundos entre petición y petición. Los que se quedan sin
    sinopsis y están en la tienda de Microsoft se piden después, en lotes. Cada juego queda marcado
    con localized_at para no volver a pedirlo hasta dentro de un tiempo. Si Steam deja de responder
    (StoreError) se para ahí: lo ya pedido queda guardado, se completa con Microsoft y después se
    vuelve a lanzar el error.

    Devuelve {"steam": con sinopsis de Steam, "xbox": con sinopsis de Microsoft, "none": sin sinopsis}.
    """
    found = {"steam": 0, "xbox": 0, "none": 0}
    pending, asked_steam, error = [], False, None
    now = timezone.now()
    for game in games:
        app_id = game.steam_app_id_override or game.steam_app_id
        summary = ""
        if app_id:
            if asked_steam:
                sleep(pause)
            asked_steam = True
            try:
                languages, summary = steam_details(app_id, sleep=sleep)
            except StoreError as exc:
                # Este juego no se marca: se volverá a pedir la próxima vez.
                error = exc
                break
            game.languages = {**(game.languages or {}), "steam": languages}
            game.refresh_spanish()
        game.summary_es, game.summary_es_source = (summary, "steam") if summary else ("", "")
        game.localized_at = now
        game.save(update_fields=[
            "languages", "spanish_audio", "spanish_text", "summary_es", "summary_es_source", "localized_at",
        ])
        if summary:
            found["steam"] += 1
        elif game.xbox_lookup_ids:
            pending.append(game)
        else:
            found["none"] += 1

    texts = xbox.fetch_descriptions(sorted({pid for game in pending for pid in game.xbox_lookup_ids})) if pending else {}
    for game in pending:
        text = next((texts[pid] for pid in game.xbox_lookup_ids if texts.get(pid)), "")
        if text:
            game.summary_es, game.summary_es_source = text, "xbox"
            game.save(update_fields=["summary_es", "summary_es_source"])
            found["xbox"] += 1
        else:
            found["none"] += 1
    if error is not None:
        raise error
    return found
