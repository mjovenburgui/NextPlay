"""Vistas de las cuentas vinculadas: vincular, importar y desvincular Steam, PlayStation, Xbox, GOG y Epic Games y
añadir o quitar las cuentas de otras plataformas. Se pulsan en Editar perfil › Cuentas vinculadas y en el modal
«Vincular cuentas» del Perfil, y lo que importan sale en el Perfil, la Ficha del juego, Juegos importados, Inicio y los
retos.
"""

import logging
import secrets
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.utils.html import format_html
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from games import igdb, prices
from games.models import Game
from library import wishlist

from .. import epic, gog, psn, steam, xbox
from ..forms import PlatformAccountForm
from ..models import EpicAccount, GOGAccount, PlatformAccount, PSNAccount, SteamAccount, XboxAccount
from .comun import _back

STEAM_STATE_KEY = "steam_openid_state"
STEAM_KEY_MISSING = (
    "La importación de juegos de Steam no está configurada: falta STEAM_API_KEY en el archivo .env."
)
STEAM_PRIVATE_HINT = (
    "En Steam ve a Perfil › Editar perfil › Privacidad, pon «Detalles del juego» en Público y después "
    "pulsa «Sincronizar Steam»."
)
XBOX_NOT_CONFIGURED = (
    "La importación de Xbox no está configurada: faltan XBOX_CLIENT_ID y XBOX_CLIENT_SECRET en el archivo .env."
)
logger = logging.getLogger(__name__)


def linking_context():
    """Variables que necesitan los paneles de vinculación de cuentas (en Editar perfil y en el modal del perfil).

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño).
    """
    return {
        "steam_configured": steam.is_configured(),
        "xbox_configured": xbox.is_configured(),
        "max_steam_accounts": SteamAccount.MAX_PER_USER,
        "psn_npsso_url": psn.NPSSO_URL,
        "epic_login_url": epic.LOGIN_URL,
        "epic_code_url": epic.CODE_URL,
        # (valor, plataforma, etiqueta del campo, ayuda) para el formulario de otras plataformas.
        "platform_rules": [
            (value, label, *PlatformAccount.RULES[value][:2])
            for value, label in PlatformAccount.Platform.choices
        ],
    }


def _steam_return_to(request, state):
    """URL absoluta a la que Steam devuelve al usuario tras iniciar sesión, con el código anti-CSRF state.

    Pantallas: Editar perfil (Cuentas vinculadas: Steam), Perfil (modal «Vincular cuentas» de su dueño).
    """
    return request.build_absolute_uri(f"{reverse('steam_callback')}?{urlencode({'state': state})}")


def _report_import(request, result):
    """Avisa al usuario de cuántos juegos se han importado de Steam, cuántos no estaban en el catálogo
    y qué cuentas se han saltado por ser privadas.

    Pantallas: Editar perfil (Cuentas vinculadas: mensaje con el resultado de la importación de Steam), Perfil (modal
    «Vincular cuentas» de su dueño).
    """
    text = f"Steam: {result['added']} juego{pluralize(result['added'])} añadido{pluralize(result['added'])}"
    if result["updated"]:
        text += f", {result['updated']} con horas actualizadas"
    text += _achievements_note(result.get("achievements"))
    messages.success(request, _with_unmatched(text, result["unmatched"]))
    if result["private"]:
        messages.error(
            request,
            f"No se pudieron leer los juegos de {', '.join(result['private'])}. {STEAM_PRIVATE_HINT}",
        )


def _achievements_note(count, noun="logros"):
    """Coletilla del mensaje de una importación con los juegos de los que se han leído los logros (o los trofeos).

    Pantallas: Editar perfil (Cuentas vinculadas: mensaje con el resultado de la importación), Perfil (modal «Vincular
    cuentas» de su dueño).
    """
    return f", {noun} de {count} juego{pluralize(count)}" if count else ""


def _with_unmatched(text, unmatched):
    """Mensaje de resultado de una importación, con el aviso de los juegos no encontrados y un enlace para
    emparejarlos a mano (HTML seguro: el texto se escapa).

    Pantallas: Editar perfil (Cuentas vinculadas: mensaje de la importación, con el enlace a Juegos importados),
    Perfil (modal «Vincular cuentas» de su dueño).
    """
    if not unmatched:
        return f"{text}."
    if unmatched == 1:
        return format_html(
            '{}. 1 no se encontró en el catálogo: <a href="{}">emparéjalo a mano</a> si es un juego.',
            text, reverse("imported_titles"),
        )
    return format_html(
        '{}. {} no se encontraron en el catálogo: <a href="{}">empareja a mano los que sean juegos</a>.',
        text, unmatched, reverse("imported_titles"),
    )


def _report_wishlist(request, result):
    """Avisa de lo que se ha traído de la lista de deseos de Steam y, con los precios de hoy, de cuántos de sus
    deseados están en su mínimo histórico o son más baratos fuera de Steam (HTML seguro: el texto se escapa).

    Los precios de los juegos nuevos se piden en ese momento (games.prices.fetch_missing).

    Pantallas: Editar perfil (Cuentas vinculadas: mensaje con lo que se ha traído de la lista de deseos de Steam),
    Perfil (modal «Vincular cuentas» de su dueño).
    """
    if result["unreadable"]:
        messages.info(
            request,
            f"La lista de deseos de Steam de {', '.join(result['unreadable'])} está vacía o es privada: "
            "de ahí no se ha traído nada a «Lo quiero».",
        )
    total = result["total"]
    if not total:
        return
    prices.fetch_missing([item.game for item in result["added"]])
    count, at_low, cheaper = wishlist.steam_deals(request.user)
    added, removed, owned, unmatched = len(result["added"]), result["removed"], result["owned"], result["unmatched"]
    details = [f"{added} nuevo{pluralize(added)} en «Lo quiero»"]
    if removed:
        details.append(f"{removed} quitado{pluralize(removed)} porque ya no está{pluralize(removed, 'n')} en Steam")
    if owned:
        details.append(f"{owned} que ya tienes")
    if unmatched:
        details.append(f"{unmatched} que no está{pluralize(unmatched, 'n')} en el catálogo")
    text = f"Lista de deseos de Steam: {total} juego{pluralize(total)} ({', '.join(details)})"
    if count:
        text += (
            f". Hoy, {at_low} está{pluralize(at_low, 'n')} en su mínimo histórico y {cheaper} "
            f"{'es' if cheaper == 1 else 'son'} más barato{pluralize(cheaper)} fuera de Steam"
        )
    messages.success(request, format_html('{}. <a href="{}">Ver tu lista de deseos</a>', text, reverse("library:wishlist")))


def _fetch_library_prices(user):
    """Pide ya los precios de los juegos de la biblioteca del usuario que nunca se han buscado
    (games.prices.fetch_missing), para que lo recién importado tenga precio sin esperar a la tarea de cada noche.

    Si una tienda no responde, la importación vale igual: lo que falte llega con update_prices. La sincronización de
    cada noche (sync_libraries) no lo hace, porque justo después va update_prices con todo el catálogo.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño), Ficha del juego
    (Dónde comprarlo de lo recién importado), Lista de deseos (precios), Página de la lista (lo que cuesta lo que te
    falta).
    """
    prices.fetch_missing(
        Game.objects.filter(library_entries__user=user, prices_updated_at__isnull=True).prefetch_related("platforms")
    )


def _import_steam(request, include_unplayed=False):
    """Lanza la importación de todas las cuentas de Steam del usuario y le avisa del resultado o del fallo.

    Si lo tiene activado (Profile.steam_wishlist), trae también su lista de deseos de Steam a «Lo quiero»;
    que falle eso no estropea lo de la biblioteca. Si falla por Steam o IGDB deja el error completo en el
    log del servidor para poder revisarlo. Si sale bien, pide los precios de los juegos que nunca los han tenido
    (_fetch_library_prices).

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas;
    insignias de las portadas, cifras y sección «Logros»), Ficha del juego (tus horas; tus logros; botón «Lo quiero»),
    Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos (progreso), Página del reto
    (progreso), Lista de deseos.
    """
    if not steam.is_configured():
        messages.error(request, STEAM_KEY_MISSING)
        return
    try:
        _report_import(request, steam.import_library(request.user, include_unplayed=include_unplayed))
    except steam.SteamPrivateError:
        messages.error(request, f"Steam no deja ver tus juegos. {STEAM_PRIVATE_HINT}")
    except (steam.SteamError, igdb.IGDBError):
        logger.exception("Falló la importación de Steam de %s", request.user.username)
        messages.error(request, "No se pudieron importar tus juegos de Steam. Inténtalo de nuevo más tarde.")
    else:
        _fetch_library_prices(request.user)
    if not request.user.profile.steam_wishlist:
        return
    try:
        _report_wishlist(request, steam.import_wishlist(request.user))
    except (steam.SteamError, igdb.IGDBError):
        logger.exception("Falló la importación de la lista de deseos de Steam de %s", request.user.username)
        messages.error(request, "No se pudo traer tu lista de deseos de Steam. Inténtalo de nuevo más tarde.")


@require_POST
@login_required
def steam_link(request):
    """Manda al usuario a iniciar sesión en Steam para vincular una cuenta más (Steam nunca nos da su contraseña).

    No deja pasar de SteamAccount.MAX_PER_USER cuentas.

    Pantallas: Editar perfil (Cuentas vinculadas: botón para vincular Steam), Perfil (modal «Vincular cuentas» de su
    dueño).
    """
    if request.user.steam_accounts.count() >= SteamAccount.MAX_PER_USER:
        messages.error(request, f"Puedes vincular como mucho {SteamAccount.MAX_PER_USER} cuentas de Steam.")
        return redirect("edit_profile")
    state = request.session[STEAM_STATE_KEY] = secrets.token_urlsafe(16)
    return redirect(steam.login_url(_steam_return_to(request, state), request.build_absolute_uri("/")))


@login_required
def steam_callback(request):
    """Vuelta desde Steam: verifica el inicio de sesión, añade la cuenta a las del usuario e importa sus juegos.

    Rechaza la vuelta si el código anti-CSRF no coincide, si Steam no la confirma o si esa cuenta de
    Steam ya está vinculada a otro usuario. Si ya era suya, solo avisa.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas;
    insignias de las portadas, cifras y sección «Logros»), Ficha del juego (tus horas; tus logros), Juegos importados
    (los que no se encuentran), Inicio (feed: la importación), Retos (progreso), Página del reto (progreso).
    """
    expected = request.session.pop(STEAM_STATE_KEY, None)
    if not expected or request.GET.get("state") != expected:
        messages.error(request, "El enlace de Steam ha caducado. Vuelve a intentarlo.")
        return redirect("edit_profile")
    try:
        steam_id = steam.verify_login(request.GET, _steam_return_to(request, expected))
    except steam.SteamError:
        steam_id = None
    if not steam_id:
        messages.error(request, "No se pudo verificar tu cuenta de Steam.")
        return redirect("edit_profile")

    account = SteamAccount.objects.filter(steam_id=steam_id).first()
    if account is not None:
        if account.user_id != request.user.id:
            messages.error(request, "Esa cuenta de Steam ya está vinculada a otro usuario de NextPlay.")
        else:
            messages.info(
                request,
                f"La cuenta {account.name} ya estaba vinculada. Para añadir otra, cierra sesión en Steam "
                "(o pulsa «¿No eres tú?») antes de iniciar sesión.",
            )
        return redirect("edit_profile")
    if request.user.steam_accounts.count() >= SteamAccount.MAX_PER_USER:
        messages.error(request, f"Puedes vincular como mucho {SteamAccount.MAX_PER_USER} cuentas de Steam.")
        return redirect("edit_profile")

    account = SteamAccount.objects.create(user=request.user, steam_id=steam_id)
    steam.refresh_account(account)
    messages.success(request, f"Cuenta de Steam vinculada: {account.name}.")
    _import_steam(request)
    return redirect("profile", username=request.user.username)


@require_POST
@login_required
def steam_import(request):
    """Vuelve a importar los juegos de todas las cuentas de Steam vinculadas (con «unplayed» también los no jugados).

    Pantallas: Editar perfil (Cuentas vinculadas: botón «Importar» de Steam), Perfil (modal «Vincular cuentas» de su
    dueño; biblioteca y horas; insignias de las portadas, cifras y sección «Logros»), Ficha del juego (tus horas; tus
    logros), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos (progreso),
    Página del reto (progreso).
    """
    accounts = list(request.user.steam_accounts.all())
    if not accounts:
        messages.error(request, "Primero vincula una cuenta de Steam.")
        return redirect("edit_profile")
    for account in accounts:
        steam.refresh_account(account)
    _import_steam(request, include_unplayed=bool(request.POST.get("unplayed")))
    return redirect("profile", username=request.user.username)


@require_POST
@login_required
def steam_auto_sync(request):
    """Activa (auto_sync=1) o desactiva la sincronización nocturna de la biblioteca de Steam del usuario.

    Pantallas: Editar perfil (Cuentas vinculadas: casilla de la sincronización de cada noche), Perfil (modal «Vincular
    cuentas» de su dueño).
    """
    profile = request.user.profile
    profile.auto_sync = request.POST.get("auto_sync") == "1"
    profile.save(update_fields=["auto_sync"])
    if profile.auto_sync:
        messages.success(request, "Tu biblioteca de Steam se sincronizará sola cada noche.")
    else:
        messages.success(request, "Sincronización nocturna desactivada: Steam solo se importará cuando lo pidas.")
    return _back(request, f"{reverse('edit_profile')}#steam")


@require_POST
@login_required
def steam_wishlist(request):
    """Activa (steam_wishlist=1) o desactiva que la importación de Steam traiga la lista de deseos a «Lo quiero».

    Con restore=1, olvida los juegos que quitó a mano para que la próxima importación los vuelva a traer.

    Pantallas: Editar perfil (Cuentas vinculadas: casilla para traer la lista de deseos de Steam), Perfil (modal
    «Vincular cuentas» de su dueño), Lista de deseos, Ficha del juego (botón «Lo quiero»).
    """
    profile = request.user.profile
    if request.POST.get("restore") == "1":
        profile.steam_wishlist_skip = []
        profile.save(update_fields=["steam_wishlist_skip"])
        messages.success(request, "Hecho: la próxima vez que sincronices Steam se traerán también los que habías quitado.")
    else:
        profile.steam_wishlist = request.POST.get("steam_wishlist") == "1"
        profile.save(update_fields=["steam_wishlist"])
        if profile.steam_wishlist:
            messages.success(request, "Tu lista de deseos de Steam se traerá a «Lo quiero» cada vez que se sincronice Steam.")
        else:
            messages.success(request, "Ya no se traerá tu lista de deseos de Steam. Lo que ya está en «Lo quiero» se queda.")
    return _back(request, f"{reverse('edit_profile')}#steam")


@require_POST
@login_required
def steam_unlink(request, account_id):
    """Desvincula una cuenta de Steam del usuario; los juegos importados se quedan en su biblioteca.

    Solo puede desvincular cuentas suyas: con el ID de una cuenta ajena responde 404.

    Pantallas: Editar perfil (Cuentas vinculadas: botón para desvincular una cuenta de Steam), Perfil (modal «Vincular
    cuentas» de su dueño; cuentas de la cabecera).
    """
    account = get_object_or_404(SteamAccount, pk=account_id, user=request.user)
    account.delete()
    messages.success(
        request, f"Cuenta de Steam {account.name} desvinculada. Tus juegos importados siguen en tu biblioteca."
    )
    return _back(request, "edit_profile")


def _psn_error(request, text):
    """Avisa del error de PlayStation y vuelve a donde estaba el formulario (por defecto, Editar perfil).

    Pantallas: Editar perfil (Cuentas vinculadas: mensaje de error de PlayStation), Perfil (modal «Vincular cuentas»
    de su dueño).
    """
    messages.error(request, text)
    return _back(request, f"{reverse('edit_profile')}#psn")


@require_POST
@login_required
def psn_sync(request):
    """Vincula la cuenta de PlayStation del código NPSSO pegado (o la actualiza) e importa sus juegos de PS4/PS5.

    El código solo se usa durante esta petición: no se guarda. Avisa si el código no tiene el formato
    esperado, si Sony no lo acepta o si esa cuenta de PlayStation ya es de otro usuario. Después pide los
    precios de los juegos que nunca los han tenido (_fetch_library_prices).

    Pantallas: Editar perfil (Cuentas vinculadas: formulario del código NPSSO de PlayStation), Perfil (modal «Vincular
    cuentas» de su dueño; biblioteca y horas; insignias de las portadas, cifras y sección «Logros»), Ficha del juego
    (tus horas; tus logros), Juegos importados (los que no se encuentran), Inicio (feed: la importación), Retos
    (progreso), Página del reto (progreso).
    """
    npsso = psn.clean_npsso(request.POST.get("npsso"))
    if not npsso:
        return _psn_error(request, "Ese no parece un código NPSSO: son 64 letras y números. Cópialo otra vez.")
    try:
        result = psn.import_library(request.user, npsso)
    except psn.PSNAuthError:
        return _psn_error(
            request,
            "PlayStation no ha aceptado el código: puede haber caducado. Inicia sesión otra vez en "
            "playstation.com, copia un código nuevo y vuelve a intentarlo.",
        )
    except ValueError as exc:
        return _psn_error(request, str(exc))
    except (psn.PSNError, igdb.IGDBError):
        logger.exception("Falló la importación de PlayStation de %s", request.user.username)
        return _psn_error(request, "No se pudieron importar tus juegos de PlayStation. Inténtalo de nuevo más tarde.")

    _fetch_library_prices(request.user)
    added, updated = result["added"], result["updated"]
    text = f"PlayStation ({result['account'].name}): {added} juego{pluralize(added)} añadido{pluralize(added)}"
    if updated:
        text += f", {updated} con horas actualizadas"
    text += _achievements_note(result.get("trophies"), "trofeos")
    messages.success(request, _with_unmatched(text, result["unmatched"]))
    return redirect("profile", username=request.user.username)


@require_POST
@login_required
def psn_unlink(request):
    """Desvincula la cuenta de PlayStation del usuario; los juegos importados se quedan en su biblioteca.

    Pantallas: Editar perfil (Cuentas vinculadas: botón para desvincular PlayStation), Perfil (modal «Vincular
    cuentas» de su dueño; cuentas de la cabecera).
    """
    PSNAccount.objects.filter(user=request.user).delete()
    messages.success(request, "Cuenta de PlayStation desvinculada. Tus juegos importados siguen en tu biblioteca.")
    return _back(request, f"{reverse('edit_profile')}#psn")


def _report_store_import(request, label, result):
    """Avisa de lo que se ha importado de GOG o de Epic Games (label, con el nombre de la cuenta): juegos añadidos, con
    horas actualizadas y no encontrados en el catálogo, con el enlace para emparejarlos.

    Pantallas: Editar perfil (Cuentas vinculadas: mensaje con el resultado de la importación de GOG o de Epic), Perfil
    (modal «Vincular cuentas» de su dueño).
    """
    added, updated = result["added"], result["updated"]
    text = f"{label}: {added} juego{pluralize(added)} añadido{pluralize(added)}"
    if updated:
        text += f", {updated} con horas actualizadas"
    messages.success(request, _with_unmatched(text, result["unmatched"]))


def _gog_error(request, text):
    """Avisa del error de GOG y vuelve a donde estaba el formulario (por defecto, Editar perfil, en GOG).

    Pantallas: Editar perfil (Cuentas vinculadas: mensaje de error de GOG), Perfil (modal «Vincular cuentas» de su
    dueño).
    """
    messages.error(request, text)
    return _back(request, f"{reverse('edit_profile')}#gog")


@require_POST
@login_required
def gog_sync(request):
    """Vincula el perfil público de GOG escrito (o lo vuelve a importar) e importa sus juegos con sus horas; desde
    entonces se lee solo cada noche. Con «unplayed», también los que nunca ha abierto. Avisa si no parece un usuario de
    GOG, si el perfil no es público o si no existe, y, si solo se han traído los jugados, de cuántos se han quedado
    fuera. Después pide los precios de los juegos que nunca los han tenido (_fetch_library_prices).

    Pantallas: Editar perfil (Cuentas vinculadas: formulario de GOG), Perfil (modal «Vincular cuentas» de su dueño;
    biblioteca y horas; cuentas de la cabecera), Juegos importados (los que no se encuentran), Inicio (feed: la
    importación), Retos (progreso), Página del reto (progreso).
    """
    username = gog.clean_username(request.POST.get("username"))
    if not username:
        return _gog_error(
            request, "Ese no parece un usuario de GOG: son de 2 a 32 letras, números, puntos o guiones. Copia el que "
            "sale en la dirección de tu perfil (gog.com/u/…).",
        )
    include_unplayed = request.POST.get("unplayed") == "1"
    try:
        result = gog.import_library(request.user, username, include_unplayed)
    except gog.GOGPrivateError:
        return _gog_error(
            request, f"El perfil de GOG de {username} no es público. En gog.com, en los ajustes de privacidad de tu "
            "cuenta, deja que cualquiera pueda verlo y vuelve a intentarlo.",
        )
    except gog.GOGMissingError:
        return _gog_error(
            request, f"No hay ningún usuario de GOG que se llame {username}. Copia el que sale en la dirección de tu "
            "perfil (gog.com/u/…).",
        )
    except (gog.GOGError, igdb.IGDBError):
        logger.exception("Falló la importación de GOG de %s", request.user.username)
        return _gog_error(request, "No se pudieron importar tus juegos de GOG. Inténtalo de nuevo más tarde.")

    _fetch_library_prices(request.user)
    _report_store_import(request, f"GOG ({username})", result)
    total, left = result["total"], result["total"] - result["account"].games
    if left > 0 and not include_unplayed:
        others = "el otro" if left == 1 else f"los otros {left}"
        messages.info(
            request,
            f"Tu perfil de GOG tiene {total} juego{pluralize(total)} y solo se han traído los que tienen horas: "
            f"GOG solo cuenta las de lo que juegas con GOG Galaxy. Para traer también {others}, marca «Importar "
            "también los juegos que no he abierto nunca» y vuelve a sincronizar.",
        )
    return redirect("profile", username=request.user.username)


@require_POST
@login_required
def gog_unlink(request):
    """Deja de importar el perfil de GOG del usuario (ya no se lee cada noche). Los juegos importados se quedan en su
    biblioteca, y su usuario de GOG en «Otras plataformas», si lo tenía.

    Pantallas: Editar perfil (Cuentas vinculadas: botón para desvincular GOG), Perfil (modal «Vincular cuentas» de su
    dueño).
    """
    GOGAccount.objects.filter(user=request.user).delete()
    messages.success(
        request, "GOG desvinculado: ya no se leerá cada noche. Tus juegos importados siguen en tu biblioteca.",
    )
    return _back(request, f"{reverse('edit_profile')}#gog")


def _epic_error(request, text):
    """Avisa del error de Epic Games y vuelve a donde estaba el formulario (por defecto, Editar perfil, en Epic).

    Pantallas: Editar perfil (Cuentas vinculadas: mensaje de error de Epic Games), Perfil (modal «Vincular cuentas» de
    su dueño).
    """
    messages.error(request, text)
    return _back(request, f"{reverse('edit_profile')}#epic")


@sensitive_post_parameters("code")
@require_POST
@login_required
def epic_sync(request):
    """Vincula la cuenta de Epic Games del código pegado (o la actualiza) e importa sus juegos con sus horas. Con
    «unplayed», también los que nunca ha abierto.

    El código solo se usa durante esta petición: no se guarda, no sale en los informes de error
    (sensitive_post_parameters) y la sesión de Epic que abre se cierra al acabar (accounts.epic). Avisa si no parece
    un código, si Epic no lo acepta o si esa cuenta de Epic ya es de otro usuario. Después pide los precios de los
    juegos que nunca los han tenido (_fetch_library_prices).

    Pantallas: Editar perfil (Cuentas vinculadas: formulario del código de Epic Games), Perfil (modal «Vincular
    cuentas» de su dueño; biblioteca y horas; cuentas de la cabecera), Juegos importados (los que no se encuentran),
    Inicio (feed: la importación), Retos (progreso), Página del reto (progreso).
    """
    code = epic.clean_code(request.POST.get("code"))
    if not code:
        return _epic_error(
            request, "Ese no parece el código de Epic: copia lo que va detrás de «authorizationCode» (o todo el texto "
            "de la página).",
        )
    try:
        result = epic.import_library(request.user, code, include_unplayed=request.POST.get("unplayed") == "1")
    except epic.EpicAuthError:
        return _epic_error(
            request, "Epic no ha aceptado el código: caduca a los pocos minutos y solo vale una vez. Abre otra vez la "
            "página de Epic, copia el código nuevo y pégalo enseguida.",
        )
    except ValueError as exc:
        return _epic_error(request, str(exc))
    except (epic.EpicError, igdb.IGDBError):
        logger.exception("Falló la importación de Epic de %s", request.user.username)
        return _epic_error(request, "No se pudieron importar tus juegos de Epic. Inténtalo de nuevo más tarde.")

    _fetch_library_prices(request.user)
    _report_store_import(request, f"Epic Games ({result['account'].name})", result)
    return redirect("profile", username=request.user.username)


@require_POST
@login_required
def epic_unlink(request):
    """Desvincula la cuenta de Epic Games del usuario; los juegos importados se quedan en su biblioteca.

    Pantallas: Editar perfil (Cuentas vinculadas: botón para desvincular Epic Games), Perfil (modal «Vincular cuentas»
    de su dueño).
    """
    EpicAccount.objects.filter(user=request.user).delete()
    messages.success(request, "Cuenta de Epic Games desvinculada. Tus juegos importados siguen en tu biblioteca.")
    return _back(request, f"{reverse('edit_profile')}#epic")


@require_POST
@login_required
def platform_add(request):
    """Añade la cuenta de otra plataforma (Xbox, Switch, Epic, GOG, Battle.net) o cambia la que ya tenía.

    Si el formato no es el de esa plataforma avisa del error y no guarda nada.

    Pantallas: Editar perfil (Cuentas vinculadas: Otras plataformas), Perfil (modal «Vincular cuentas» de su dueño;
    cuentas de la cabecera).
    """
    form = PlatformAccountForm(request.POST)
    if form.is_valid():
        account, created = PlatformAccount.objects.update_or_create(
            user=request.user,
            platform=form.cleaned_data["platform"],
            defaults={"handle": form.cleaned_data["handle"]},
        )
        verb = "añadida" if created else "actualizada"
        messages.success(request, f"Cuenta de {account.get_platform_display()} {verb}: {account.handle}.")
    else:
        messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
    return _back(request, f"{reverse('edit_profile')}#plataformas")


@require_POST
@login_required
def platform_remove(request, platform):
    """Quita del perfil del usuario su cuenta de esa plataforma (no da error si no la tenía).

    Pantallas: Editar perfil (Cuentas vinculadas: Otras plataformas), Perfil (modal «Vincular cuentas» de su dueño;
    cuentas de la cabecera).
    """
    PlatformAccount.objects.filter(user=request.user, platform=platform).delete()
    messages.success(request, "Cuenta quitada de tu perfil.")
    return _back(request, f"{reverse('edit_profile')}#plataformas")


XBOX_STATE_KEY = "xbox_oauth_state"


def _xbox_redirect_uri(request):
    """URL absoluta de vuelta desde Microsoft; tiene que coincidir con la registrada en la aplicación de Azure.

    Pantallas: Editar perfil (Cuentas vinculadas: Xbox), Perfil (modal «Vincular cuentas» de su dueño).
    """
    return request.build_absolute_uri(reverse("xbox_callback"))


@require_POST
@login_required
def xbox_link(request):
    """Manda al usuario a iniciar sesión con su cuenta Microsoft para vincular Xbox o volver a sincronizarla.

    Sirve para las dos cosas porque no se guarda ningún token: cada sincronización pide iniciar sesión.

    Pantallas: Editar perfil (Cuentas vinculadas: botón para vincular Xbox), Perfil (modal «Vincular cuentas» de su
    dueño).
    """
    if not xbox.is_configured():
        messages.error(request, XBOX_NOT_CONFIGURED)
        return _back(request, f"{reverse('edit_profile')}#xbox")
    state = request.session[XBOX_STATE_KEY] = secrets.token_urlsafe(16)
    return redirect(xbox.login_url(_xbox_redirect_uri(request), state))


@login_required
def xbox_callback(request):
    """Vuelta desde Microsoft: comprueba el código anti-CSRF, vincula la cuenta de Xbox e importa sus juegos.

    Si el usuario canceló el inicio de sesión, si el código no coincide o si Xbox Live falla, avisa y
    vuelve a Editar perfil. Después pide los precios de los juegos que nunca los han tenido (_fetch_library_prices).

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; biblioteca y horas;
    insignias de las portadas, cifras y sección «Logros»), Ficha del juego (tus horas; tus logros), Juegos importados
    (los que no se encuentran), Inicio (feed: la importación), Retos (progreso), Página del reto (progreso).
    """
    expected = request.session.pop(XBOX_STATE_KEY, None)
    back = f"{reverse('edit_profile')}#xbox"
    if request.GET.get("error"):
        messages.error(request, "Has cancelado el inicio de sesión con Microsoft.")
        return redirect(back)
    if not expected or request.GET.get("state") != expected or not request.GET.get("code"):
        messages.error(request, "El enlace de Microsoft ha caducado. Vuelve a intentarlo.")
        return redirect(back)
    try:
        result = xbox.import_library(request.user, request.GET["code"], _xbox_redirect_uri(request))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    except (xbox.XboxError, igdb.IGDBError) as exc:
        logger.exception("Falló la importación de Xbox de %s", request.user.username)
        messages.error(request, f"No se pudieron importar tus juegos de Xbox. {exc}")
        return redirect(back)

    _fetch_library_prices(request.user)
    added, updated = result["added"], result["updated"]
    text = f"Xbox ({result['account'].name}): {added} juego{pluralize(added)} añadido{pluralize(added)}"
    if updated:
        text += f", {updated} con horas actualizadas"
    text += _achievements_note(result.get("achievements"))
    messages.success(request, _with_unmatched(text, result["unmatched"]))
    return redirect("profile", username=request.user.username)


@require_POST
@login_required
def xbox_unlink(request):
    """Desvincula la cuenta de Xbox del usuario; los juegos importados se quedan en su biblioteca.

    Pantallas: Editar perfil (Cuentas vinculadas: botón para desvincular Xbox), Perfil (modal «Vincular cuentas» de su
    dueño; cuentas de la cabecera).
    """
    XboxAccount.objects.filter(user=request.user).delete()
    messages.success(request, "Cuenta de Xbox desvinculada. Tus juegos importados siguen en tu biblioteca.")
    return _back(request, f"{reverse('edit_profile')}#xbox")
