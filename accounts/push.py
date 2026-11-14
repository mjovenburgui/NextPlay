"""Avisos push: los avisos de la campana, también como notificación del móvil o del ordenador (Web Push, RFC 8030),
en cada dispositivo donde se han activado (accounts.models.PushSubscription).

Viajan sin contenido: el servicio push del navegador (el de Google, Mozilla, Apple o Microsoft) solo despierta al
service worker de NextPlay (templates/sw.js), que pregunta a NextPlay qué enseñar (pending). Así no hay nada que
cifrar y el texto de los avisos no pasa por nadie más. Cada envío va firmado con VAPID (accounts.vapid), con las
claves de .env.

notices.notify llama a schedule con cada aviso nuevo: se manda en segundo plano y después de guardarlo, para que quien
da un «me gusta» no espere al servicio push de otro.

Pantallas: Avisos, Editar perfil (Avisos en el móvil y el ordenador), todas (las notificaciones del móvil o del
ordenador).
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone

from . import notices, vapid
from .models import Notification, PushSubscription

logger = logging.getLogger(__name__)

# Los servicios push a los que se manda: los de Chrome y Android, Firefox, Safari y Edge. Así nadie puede apuntar un
# «dispositivo» a otra dirección y hacer que NextPlay la llame.
PUSH_HOSTS = (
    "fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com", "push.apple.com",
    "notify.windows.com",
)
# Cuánto guarda el servicio push un aviso si el dispositivo está apagado: un día, que más tarde ya no sirve.
TTL = 24 * 3600
# El mismo tema para todos: con el dispositivo apagado, los pendientes se quedan en uno, que al llegar lo enseña todo.
TOPIC = "avisos"
TIMEOUT = 10
# Lo que enseña un dispositivo de una vez: los avisos de los últimos PENDING_DAYS días, como mucho PENDING_MAX.
PENDING_DAYS = 2
PENDING_MAX = 5
# Cuánto vale una prueba pedida desde Editar perfil.
TEST_MINUTES = 10
# Como mucho, tantos dispositivos por persona: al activar uno más, se quita el más antiguo.
MAX_DEVICES = 10

# Dos hilos para mandarlos en segundo plano. Python espera a que acaben antes de salir, así que la tarea de cada noche
# no corta los que queden. Los tests cambian _submit por una llamada directa.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="push")
_submit = _executor.submit
# Usuarios con un envío en cola: los avisos que les lleguen mientras tanto se enseñan con ese mismo.
_queued = set()
_lock = threading.Lock()


def is_configured():
    """Si NextPlay tiene sus claves VAPID (VAPID_PUBLIC_KEY y VAPID_PRIVATE_KEY en .env) y son un par. Sin ellas, los
    avisos se quedan en la campana y el correo.

    Pantallas: Editar perfil (Avisos en el móvil y el ordenador), Avisos.
    """
    return vapid.valid_keys(settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY)


def allowed_endpoint(endpoint):
    """Si endpoint es una dirección https de uno de los servicios push de PUSH_HOSTS.

    Pantallas: Editar perfil (Avisos en el móvil y el ordenador: activarlos en este dispositivo).
    """
    try:
        parts = urlsplit(endpoint or "")
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    return parts.scheme == "https" and any(host == known or host.endswith(f".{known}") for known in PUSH_HOSTS)


def device_label(user_agent):
    """El nombre del dispositivo a partir del User-Agent de su navegador: «Chrome en Android», «Safari en iPhone»…

    Pantallas: Editar perfil (Avisos en el móvil y el ordenador: la lista de tus dispositivos).
    """
    agent = user_agent or ""
    browsers = (
        ("EdgA/", "Edge"), ("Edg/", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"),
        ("Firefox/", "Firefox"), ("FxiOS", "Firefox"), ("CriOS", "Chrome"), ("Chrome/", "Chrome"), ("Safari/", "Safari"),
    )
    systems = (
        ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"), ("Mac OS X", "Mac"),
        ("CrOS", "ChromeOS"), ("Linux", "Linux"),
    )
    browser = next((name for token, name in browsers if token in agent), "Navegador")
    system = next((name for token, name in systems if token in agent), "")
    return f"{browser} en {system}" if system else browser


def schedule(notification):
    """Manda notification, recién creado, a los dispositivos de su usuario, en segundo plano y cuando se haya guardado.
    Nada si NextPlay no tiene sus claves VAPID, si el usuario no quiere ese tipo de aviso como notificación o si no lo
    ha activado en ningún dispositivo.

    Pantallas: todas (las notificaciones del móvil o del ordenador).
    """
    user = notification.user
    if not is_configured() or not notices.wants(user, notification.kind, notices.PUSH):
        return
    if not PushSubscription.objects.filter(user=user).exists():
        return
    transaction.on_commit(lambda: _queue(user.pk))


def _queue(user_id):
    """Pone en cola el envío a los dispositivos de user_id, si no lo estaba ya.

    Pantallas: todas (las notificaciones del móvil o del ordenador).
    """
    with _lock:
        if user_id in _queued:
            return
        _queued.add(user_id)
    _submit(_in_background, user_id)


def _in_background(user_id):
    """Lo que hace el hilo: manda a los dispositivos de user_id y cierra su conexión a la base de datos. Si algo falla,
    lo apunta en el log: un aviso push que no sale no debe romper nada.

    Pantallas: todas (las notificaciones del móvil o del ordenador).
    """
    with _lock:
        _queued.discard(user_id)
    try:
        deliver(user_id)
    except Exception:
        logger.exception("Falló el envío de avisos push al usuario %s", user_id)
    finally:
        connection.close()


def deliver(user_id):
    """Despierta todos los dispositivos de user_id y devuelve a cuántos ha llegado.

    Pantallas: todas (las notificaciones del móvil o del ordenador).
    """
    return sum(send(subscription) for subscription in PushSubscription.objects.filter(user_id=user_id))


def send(subscription):
    """Manda un aviso push sin contenido a subscription, firmado con VAPID. Devuelve True si el servicio push lo acepta.
    Si contesta que esa suscripción ya no existe (404 o 410: el navegador la ha quitado o ha caducado), la borra.

    Pantallas: todas (las notificaciones del móvil o del ordenador), Editar perfil (Avisos en el móvil y el ordenador:
    «Mandar una prueba»).
    """
    if not allowed_endpoint(subscription.endpoint):
        subscription.delete()
        return False
    headers = {
        "Authorization": vapid.authorization(
            subscription.endpoint, settings.VAPID_SUBJECT, settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY,
        ),
        "TTL": str(TTL),
        "Urgency": "normal",
        "Topic": TOPIC,
    }
    try:
        response = requests.post(subscription.endpoint, data=b"", headers=headers, timeout=TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("No se pudo mandar el aviso push al dispositivo %s: %s", subscription.pk, exc)
        return False
    if response.status_code in (404, 410):
        subscription.delete()
        return False
    if response.status_code >= 300:
        logger.warning(
            "El servicio push contestó %s al dispositivo %s: %s",
            response.status_code, subscription.pk, response.text[:200],
        )
        return False
    PushSubscription.objects.filter(pk=subscription.pk).update(last_push_at=timezone.now())
    return True


def pending(subscription):
    """Lo que tiene que enseñar el dispositivo de subscription al despertarlo un aviso push: la prueba, si se ha pedido
    hace poco, y los avisos de su usuario que aún no ha enseñado (de los últimos PENDING_DAYS días, como mucho
    PENDING_MAX) y que quiere también como notificación. Apunta hasta dónde ha llegado, para no repetirlos.

    Pantallas: todas (las notificaciones del móvil o del ordenador).
    """
    now = timezone.now()
    user = subscription.user
    fresh = list(
        Notification.objects.filter(
            user=user, pk__gt=subscription.last_notification_id, created_at__gte=now - timedelta(days=PENDING_DAYS),
        ).select_related("game", "actor__profile").order_by("pk")[:50]
    )
    items = [_entry(notification) for notification in fresh if notices.wants(user, notification.kind, notices.PUSH)]
    items = items[-PENDING_MAX:]
    updates = {"last_notification_id": fresh[-1].pk} if fresh else {}
    test = subscription.test_requested_at
    if test is not None:
        if test >= now - timedelta(minutes=TEST_MINUTES):
            items.insert(0, {
                "id": "prueba", "title": "NextPlay", "body": "Así te llegarán los avisos a este dispositivo.",
                "url": reverse("notifications"), "icon": "",
            })
        updates["test_requested_at"] = None
    if updates:
        PushSubscription.objects.filter(pk=subscription.pk).update(**updates)
    return items


def _entry(notification):
    """Lo que enseña la notificación de un aviso: su tipo como título, su texto, su enlace y, como imagen, la portada del
    juego o la foto de quien lo ha provocado.

    Pantallas: todas (las notificaciones del móvil o del ordenador).
    """
    icon = ""
    if notification.game_id:
        icon = notification.game.cover_url or ""
    elif notification.actor_id:
        icon = notification.actor.profile.avatar_url or ""
    return {
        "id": notification.pk,
        "title": notification.get_kind_display(),
        "body": notification.text,
        "url": notification.url or reverse("notifications"),
        "icon": icon,
    }
