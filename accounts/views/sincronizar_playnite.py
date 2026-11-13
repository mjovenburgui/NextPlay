"""Vistas de la extensión NextPlay Sync de Playnite: generar su código y desvincularla desde Editar perfil › Cuentas
vinculadas, descargarla y la dirección a la que manda la biblioteca (api/playnite/), que no usa la sesión del navegador
sino el código personal (accounts.models.PlayniteAccount).
"""

import json
import logging
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from games import igdb

from .. import playnite
from ..models import PlayniteAccount
from .cuentas_vinculadas import _fetch_library_prices

logger = logging.getLogger(__name__)
# El código recién generado, que Editar perfil enseña una sola vez.
SESSION_KEY = "playnite_token"
# La extensión ya compilada (playnite-extension/build.ps1).
EXTENSION_FILE = Path(settings.BASE_DIR) / "playnite-extension" / "dist" / "NextPlaySync.pext"


def _back_to_card():
    """Editar perfil, en la tarjeta de Playnite.

    Pantallas: Editar perfil (Cuentas vinculadas › Playnite).
    """
    return redirect(reverse("edit_profile") + "#playnite")


@require_POST
@login_required
def playnite_token(request):
    """Genera el código de la extensión de Playnite (o uno nuevo, y el anterior deja de valer) y lleva a Editar perfil,
    que lo enseña una sola vez.

    Pantallas: Editar perfil (Cuentas vinculadas › Playnite: «Generar el código»), Perfil (modal «Vincular cuentas»).
    """
    renewed = PlayniteAccount.objects.filter(user=request.user).exists()
    request.session[SESSION_KEY] = PlayniteAccount.issue(request.user)
    if renewed:
        messages.success(request, "Código nuevo generado: el anterior ya no vale. Pégalo en la extensión.")
    else:
        messages.success(request, "Código generado: pégalo en la extensión NextPlay Sync de Playnite.")
    return _back_to_card()


@require_POST
@login_required
def playnite_unlink(request):
    """Desvincula la extensión de Playnite: su código deja de valer y los juegos que trajo se quedan en la biblioteca.

    Pantallas: Editar perfil (Cuentas vinculadas › Playnite), Perfil (modal «Vincular cuentas»).
    """
    PlayniteAccount.objects.filter(user=request.user).delete()
    request.session.pop(SESSION_KEY, None)
    messages.success(
        request, "Playnite desvinculado: su código ya no vale. Los juegos que trajo siguen en tu biblioteca.",
    )
    return _back_to_card()


@login_required
def playnite_extension(request):
    """Descarga la extensión NextPlay Sync ya compilada (un .pext, que Playnite instala al abrirlo), o 404 si aún no se
    ha compilado.

    Pantallas: Editar perfil (Cuentas vinculadas › Playnite: «Descargar la extensión»).
    """
    if not EXTENSION_FILE.exists():
        raise Http404("La extensión aún no está compilada (playnite-extension/build.ps1).")
    return FileResponse(EXTENSION_FILE.open("rb"), as_attachment=True, filename="NextPlaySync.pext")


def _error(text, status):
    """Respuesta de error para la extensión, que enseña text en Playnite.

    Pantallas: ninguna (la dirección que usa la extensión de Playnite).
    """
    return JsonResponse({"ok": False, "error": text}, status=status)


@csrf_exempt
@require_POST
def playnite_api(request):
    """Recibe lo que manda la extensión (accounts.playnite) con el código en «Authorization: Bearer …» y lo importa.
    Responde en JSON lo que ha pasado («message», para enseñarlo en Playnite, y las cifras), 401 si el código no vale,
    400 si lo que llega no tiene la forma esperada y 503 si no se ha podido buscar en el catálogo. Pide los precios de
    los juegos nuevos, como las demás importaciones.

    Pantallas: Perfil (biblioteca y horas), Juegos importados (los que no se encuentran), Inicio (feed: la
    importación), Editar perfil (Cuentas vinculadas › Playnite: la última sincronización), Retos (progreso), Página del
    reto (progreso).
    """
    header = request.headers.get("Authorization", "")
    account = PlayniteAccount.for_token(header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else "")
    if account is None:
        return _error("El código no vale: genera otro en NextPlay, en Editar perfil › Cuentas vinculadas.", 401)
    try:
        result = playnite.sync(account, json.loads(request.body or b"null"))
    except (ValueError, playnite.PayloadError) as exc:
        return _error(f"No se entiende lo que ha llegado: {exc}", 400)
    except igdb.IGDBError:
        logger.exception("Falló la sincronización de Playnite de %s", account.user.username)
        return _error("No se han podido buscar los juegos en el catálogo. Se volverá a intentar.", 503)
    added, updated, unmatched = result["added"], result["updated"], result["unmatched"]
    if added:
        _fetch_library_prices(account.user)
    text = f"{added} juego{pluralize(added)} añadido{pluralize(added)}, {updated} con horas actualizadas"
    if unmatched:
        text += f" y {unmatched} sin encontrar en el catálogo"
    counts = {key: result[key] for key in ("added", "updated", "unmatched", "skipped")}
    return JsonResponse({"ok": True, "message": text + ".", **counts})
