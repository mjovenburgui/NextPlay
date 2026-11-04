"""Avisos de la lista de deseos: comparan los precios de hoy con lo que quiere cada usuario.

Se lanzan al terminar update_prices. Un juego avisa cuando su mejor precio (PC, Xbox o Nintendo, solo en las
tiendas del usuario si las ha elegido) baja del precio que puso el usuario o cuando está rebajado a su mínimo histórico. No se repite el aviso hasta
que baje más, o hasta que vuelva a subir y baje otra vez. Los juegos que ya están en la biblioteca
del usuario no avisan (ya lo tiene).

También está aquí el resumen que se enseña después de traer la lista de deseos de Steam (con los precios de los juegos
nuevos ya pedidos, ver games.prices.fetch_missing): cuántos están hoy en su mínimo histórico y cuántos son más baratos
fuera de Steam.
"""

import logging
from collections import defaultdict

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Exists, OuterRef
from django.urls import reverse

from accounts import notices
from accounts.models import Notification
from games.models import STEAM_SHOP_ID, GamePrice, format_price

from .models import LibraryEntry, WishlistItem

logger = logging.getLogger(__name__)


def at_historical_low(game, best):
    """Indica si best (la oferta más barata del juego) está rebajada a su mínimo histórico o por debajo.

    Solo cuenta si está rebajada: un juego que nunca baja siempre está en su mínimo.

    Pantallas: Lista de deseos (en su mínimo histórico), Avisos (bajadas de precio), Editar perfil (mensaje al traer
    la lista de deseos de Steam).
    """
    low = game.historical_low
    return bool(best and best.cut and low is not None and best.price <= low)


def steam_deals(user):
    """De los juegos de «Lo quiero» de user que están en su lista de deseos de Steam: (cuántos son, cuántos están hoy
    en su mínimo histórico, cuántos son más baratos en otra tienda de PC que en Steam).

    Cuentan solo las tiendas del usuario si las ha elegido; el precio de Steam con el que se compara es siempre el
    de Steam.

    Pantallas: Editar perfil, Perfil (mensaje al traer la lista de deseos de Steam desde Cuentas vinculadas).
    """
    shops = set(user.profile.shops) or None
    items = list(
        WishlistItem.objects.filter(user=user, steam_added_at__isnull=False)
        .select_related("game").prefetch_related("game__prices")
    )
    at_low = cheaper = 0
    for item in items:
        game = item.game
        at_low += at_historical_low(game, game.cheapest(shops))
        steam = next((price for price in game.prices.all() if price.shop_id == STEAM_SHOP_ID), None)
        pc = [
            price for price in game.prices.all()
            if price.source == GamePrice.Source.ITAD and price.shop_id != STEAM_SHOP_ID
            and (shops is None or price.shop_id in shops)
        ]
        cheaper += bool(steam and pc and min(price.price for price in pc) < steam.price)
    return len(items), at_low, cheaper


def alert_reason(item, best):
    """Por qué avisar del precio best (un GamePrice) de un juego de la lista: «objetivo», «minimo» o None.

    «minimo» solo cuenta si está rebajado: un juego que nunca baja siempre está en su mínimo.

    Pantallas: Avisos (bajadas de precio de tu lista de deseos).
    """
    if best is None or best.is_free:
        return None
    if item.target_price is not None and best.price <= item.target_price:
        return "objetivo"
    if at_historical_low(item.game, best):
        return "minimo"
    return None


def alert_text(item, best, reason):
    """Texto del aviso: el juego, el precio, la tienda y el motivo.

    Pantallas: Avisos (texto de cada aviso de bajada de precio, también en el correo).
    """
    price = format_price(best.price, best.symbol)
    if reason == "objetivo":
        return f"{item.game.name} ha bajado a {price} en {best.shop_name} (tu límite: {format_price(item.target_price)})."
    return f"{item.game.name} está a {price} en {best.shop_name} (-{best.cut} %): su mínimo histórico."


def check_alerts(games=None):
    """Avisa de las bajadas de precio de las listas de deseos (de esos juegos, o de todos): en la campana y, a quien
    también las quiere por correo, en un solo correo con todas las suyas. Devuelve de cuántas bajadas ha avisado.

    Lo que recibe cada uno lo eligió en Editar perfil (ver accounts.notices). Si alguien las tiene apagadas en la
    campana y en el correo, la bajada no cuenta como avisada: si las vuelve a encender, se le avisa del precio de
    entonces.

    Pantallas: Avisos (bajadas de precio de tu lista de deseos, también por correo), todas (el número de la campana).
    """
    owned = LibraryEntry.objects.filter(user=OuterRef("user"), game=OuterRef("game"))
    items = (
        WishlistItem.objects.filter(~Exists(owned))
        .select_related("user__profile", "game")
        .prefetch_related("game__prices")
    )
    if games is not None:
        items = items.filter(game__in=games)

    mails = defaultdict(list)  # usuario -> [(texto, enlace)] de su correo
    alerted = 0
    for item in items:
        best = item.game.cheapest(set(item.user.profile.shops) or None)
        reason = alert_reason(item, best)
        if reason is None:
            if item.alerted_price is not None:
                item.alerted_price = None  # ha vuelto a subir: la próxima bajada se avisa
                item.save(update_fields=["alerted_price"])
            continue
        if item.alerted_price is not None and best.price >= item.alerted_price:
            continue
        by_mail = bool(item.user.email) and notices.wants(item.user, Notification.Kind.PRICE, notices.EMAIL)
        if not by_mail and not notices.wants(item.user, Notification.Kind.PRICE):
            continue
        text, url = alert_text(item, best, reason), item.game.get_absolute_url()
        notices.notify(item.user, Notification.Kind.PRICE, text, url, game=item.game)
        if by_mail:
            mails[item.user].append((text, url))
        alerted += 1
        item.alerted_price = best.price
        item.save(update_fields=["alerted_price"])

    for user, alerts in mails.items():
        _send_email(user, alerts)
    return alerted


def _send_email(user, alerts):
    """Manda al usuario un correo con sus avisos de precio (pares de texto y enlace); si falla, lo apunta en el log
    y sigue.

    Pantallas: ninguna (manda por correo los avisos de precio a quien los tiene encendidos en Editar perfil › Avisos).
    """
    lines = [f"- {text}\n  {settings.SITE_URL}{url}" for text, url in alerts]
    body = (
        f"Hola, {user.profile.name}:\n\nHan bajado de precio juegos de tu lista de deseos en NextPlay:\n\n"
        + "\n".join(lines)
        + f"\n\nPuedes dejar de recibir estos correos en {settings.SITE_URL}{reverse('edit_profile')}#avisos\n"
    )
    subject = alerts[0][0] if len(alerts) == 1 else f"{len(alerts)} juegos de tu lista de deseos han bajado de precio"
    try:
        send_mail(subject[:150], body, None, [user.email])
    except Exception:  # un servidor de correo caído no debe parar la actualización de precios
        logger.exception("No se pudo mandar el correo de avisos de precio a %s", user.username)
