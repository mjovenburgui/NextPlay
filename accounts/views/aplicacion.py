"""NextPlay como aplicación y sus avisos push: el manifiesto (nombre, iconos y accesos directos de NextPlay instalada),
el service worker, la página Sin conexión y las direcciones con las que cada dispositivo activa, prueba o quita sus
avisos (Editar perfil › Avisos en el móvil y el ordenador) y pregunta qué enseñar al recibir uno (accounts.push).
"""

import hashlib
import hmac
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles import finders
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import get_template
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .. import push
from ..models import PushSubscription

# El fondo de la web: el color de la barra del sistema y de la pantalla de arranque de NextPlay instalada.
THEME_COLOR = "#070912"
# Lo que el service worker guarda para cuando no hay red, además de la página Sin conexión: estilos e iconos.
PRECACHE = ("css/app.css", "favicon.svg", "icons/icon-192.png", "icons/badge-96.png")


def devices_context(user):
    """Lo que necesita la tarjeta «Avisos en el móvil y el ordenador» de Editar perfil: si NextPlay tiene sus claves
    VAPID, la clave pública que pide el navegador para activarlos y los dispositivos donde ya están activados.

    Pantallas: Editar perfil (Avisos en el móvil y el ordenador).
    """
    configured = push.is_configured()
    return {
        "push_configured": configured,
        "vapid_public_key": settings.VAPID_PUBLIC_KEY if configured else "",
        "push_devices": list(user.push_subscriptions.all()),
    }


def manifest(request):
    """El manifiesto de la aplicación (manifest.webmanifest): nombre, colores, iconos y accesos directos de NextPlay
    cuando se instala en el móvil o en el ordenador.

    Pantallas: todas (NextPlay instalada: su icono, su pantalla de arranque y sus accesos directos).
    """
    icons = [
        {"src": static("icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": static("icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": static("icons/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ]
    shortcuts = [
        ("Qué jugar después", "Qué jugar", reverse("library:next")),
        ("Lista de deseos", "Deseos", reverse("library:wishlist")),
        ("Publicar", "Publicar", reverse("social:post_new")),
        ("Avisos", "Avisos", reverse("notifications")),
    ]
    data = {
        "id": "/",
        "name": "NextPlay",
        "short_name": "NextPlay",
        "description": "Tu vida gamer, en un solo lugar: tus juegos con sus horas, reseñas, listas, retos y precios.",
        "lang": "es",
        "dir": "ltr",
        "start_url": reverse("home"),
        "scope": "/",
        "display": "standalone",
        "background_color": THEME_COLOR,
        "theme_color": THEME_COLOR,
        "categories": ["games", "entertainment", "social"],
        "icons": icons,
        "shortcuts": [
            {"name": name, "short_name": short, "url": url, "icons": [icons[0]]} for name, short, url in shortcuts
        ],
    }
    return JsonResponse(data, content_type="application/manifest+json", json_dumps_params={"ensure_ascii": False})


@never_cache
def service_worker(request):
    """El service worker (sw.js), desde la raíz para que cubra toda la web: guarda la página Sin conexión y lo de
    PRECACHE, enseña esa página cuando no hay red, recibe los avisos push y abre su enlace al pulsarlos. Su versión sale
    de lo que guarda, para que el navegador lo renueve cuando cambia.

    Pantallas: Sin conexión, todas (las notificaciones del móvil o del ordenador).
    """
    version = hashlib.sha256()
    for name in PRECACHE:
        path = finders.find(name)
        if path:
            with open(path, "rb") as file:
                version.update(file.read())
    with open(get_template("offline.html").origin.name, "rb") as file:
        version.update(file.read())
    config = {
        "version": version.hexdigest()[:12],
        "offline": reverse("offline"),
        "precache": [reverse("offline"), *(static(name) for name in PRECACHE)],
        "static": static(""),
        "pending": reverse("push_pending"),
        "notifications": reverse("notifications"),
        "icon": static("icons/icon-192.png"),
        "badge": static("icons/badge-96.png"),
    }
    response = render(request, "sw.js", {"config": json.dumps(config)}, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    return response


def offline(request):
    """Sin conexión: la página que enseña NextPlay instalada (o la web, si ya se había abierto) cuando no hay red. La
    guarda el service worker al instalarse, así que no lleva nada de nadie.

    Pantallas: Sin conexión.
    """
    return render(request, "offline.html")


def _json(request):
    """El cuerpo JSON que manda el navegador, o {} si no lo es.

    Pantallas: Editar perfil (Avisos en el móvil y el ordenador), todas (las notificaciones del móvil o del ordenador).
    """
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


@require_POST
@login_required
def push_subscribe(request):
    """Activa los avisos push en el dispositivo desde el que se pide: guarda lo que da su navegador al aceptarlos (su
    dirección en el servicio push y su secreto), solo si es de un servicio push conocido. Si ese navegador ya los tenía
    activados con otra cuenta, pasa a ser de esta. A un dispositivo nuevo no se le mandan los avisos que ya había, y
    con más de push.MAX_DEVICES se quita el más antiguo.

    Pantallas: Editar perfil (Avisos en el móvil y el ordenador: «Activar en este dispositivo»).
    """
    if not push.is_configured():
        return JsonResponse({"error": "Los avisos en el móvil aún no están disponibles."}, status=503)
    data = _json(request)
    endpoint = str(data.get("endpoint") or "")
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    auth = str(keys.get("auth") or "")
    if len(endpoint) > 600 or not push.allowed_endpoint(endpoint) or not 0 < len(auth) <= 100:
        return JsonResponse({"error": "El navegador no ha dado una suscripción válida."}, status=400)
    fields = {
        "auth": auth,
        "p256dh": str(keys.get("p256dh") or "")[:200],
        "device": push.device_label(request.headers.get("User-Agent", "")),
    }
    subscription = PushSubscription.objects.filter(endpoint=endpoint).first()
    if subscription is None or subscription.user_id != request.user.pk:
        # Desde ahora: los avisos que ya tenía no salen en este dispositivo.
        last = request.user.notifications.order_by("-pk").values_list("pk", flat=True).first() or 0
        fields.update(user=request.user, last_notification_id=last, last_push_at=None, test_requested_at=None)
    subscription, created = PushSubscription.objects.update_or_create(endpoint=endpoint, defaults=fields)
    extra = request.user.push_subscriptions.order_by("-created_at", "-pk").values_list("pk", flat=True)[push.MAX_DEVICES:]
    PushSubscription.objects.filter(pk__in=list(extra)).delete()
    return JsonResponse({"ok": True, "device": subscription.device}, status=201 if created else 200)


@require_POST
@login_required
def push_unsubscribe(request):
    """Desactiva los avisos push en el dispositivo desde el que se pide (su navegador ya ha quitado la suscripción).

    Pantallas: Editar perfil (Avisos en el móvil y el ordenador: «Desactivar aquí»).
    """
    request.user.push_subscriptions.filter(endpoint=str(_json(request).get("endpoint") or "")).delete()
    return JsonResponse({"ok": True})


@require_POST
@login_required
def push_remove(request, pk):
    """Quita un dispositivo de la lista de Editar perfil: ya no le llega ningún aviso, aunque su navegador siga suscrito.

    Pantallas: Editar perfil (Avisos en el móvil y el ordenador: «Quitar»).
    """
    request.user.push_subscriptions.filter(pk=pk).delete()
    messages.success(request, "Ese dispositivo ya no recibirá avisos.")
    return redirect(f"{reverse('edit_profile')}#dispositivos")


@require_POST
@login_required
def push_test(request):
    """Manda una prueba al dispositivo desde el que se pide, para ver cómo llegan los avisos: sale en unos segundos.

    Pantallas: Editar perfil (Avisos en el móvil y el ordenador: «Mandar una prueba»).
    """
    endpoint = str(_json(request).get("endpoint") or "")
    subscription = request.user.push_subscriptions.filter(endpoint=endpoint).first()
    if subscription is None:
        return JsonResponse({"error": "Este dispositivo no tiene los avisos activados."}, status=404)
    PushSubscription.objects.filter(pk=subscription.pk).update(test_requested_at=timezone.now())
    if not push.send(subscription):
        return JsonResponse(
            {"error": "El servicio de avisos del navegador no la ha aceptado. Desactívalos y vuelve a activarlos."},
            status=502,
        )
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def push_pending(request):
    """Lo que enseña un dispositivo al despertarlo un aviso push, que viaja sin contenido (push.pending). Lo pide su
    service worker, sin sesión: se identifica con su dirección en el servicio push y su secreto, que solo conocen su
    navegador y NextPlay. Por eso no lleva la comprobación CSRF, que sirve para las peticiones con sesión.

    Pantallas: todas (las notificaciones del móvil o del ordenador).
    """
    data = _json(request)
    subscription = (
        PushSubscription.objects.select_related("user__profile").filter(endpoint=str(data.get("endpoint") or "")).first()
    )
    if subscription is None or not hmac.compare_digest(subscription.auth, str(data.get("auth") or "")):
        return JsonResponse({"avisos": []}, status=404)
    return JsonResponse({"avisos": push.pending(subscription)})
