"""Avisos de la lista de deseos: comparan los precios de hoy con lo que quiere cada usuario.

Se lanzan al terminar update_prices. Un juego avisa cuando su mejor precio (PC, Xbox o Nintendo, solo en las
tiendas del usuario si las ha elegido) baja del precio que puso el usuario o cuando está rebajado a su mínimo histórico. No se repite el aviso hasta
que baje más, o hasta que vuelva a subir y baje otra vez. Los juegos que ya están en la biblioteca
del usuario no avisan (ya lo tiene).

También están aquí los precios que se piden al momento para los juegos que llegan de la lista de deseos
de Steam y el resumen que se enseña después: cuántos están hoy en su mínimo histórico y cuántos son más
baratos fuera de Steam.
"""

import logging
from collections import defaultdict

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Exists, OuterRef

from accounts.models import Notification
from games import prices
from games.models import STEAM_SHOP_ID, GamePrice, format_price

from .models import LibraryEntry, WishlistItem

logger = logging.getLogger(__name__)


def at_historical_low(game, best):
    """Indica si best (la oferta más barata del juego) está rebajada a su mínimo histórico o por debajo.

    Solo cuenta si está rebajada: un juego que nunca baja siempre está en su mínimo.
    """
    low = game.historical_low
    return bool(best and best.cut and low is not None and best.price <= low)


def fetch_missing_prices(games):
    """Pide ya a IsThereAnyDeal los precios de PC de los juegos que aún no los tienen, sin esperar a la noche.

    Sin ITAD_API_KEY, o si falla, no hace nada (los traerá update_prices). Devuelve cuántos tienen ahora precio.
    """
    games = [game for game in games if game.prices_updated_at is None]
    if not games or not prices.is_configured():
        return 0
    try:
        return prices.update_prices(games)
    except prices.ITADError:
        logger.exception("No se pudieron pedir los precios de los juegos traídos de la lista de deseos de Steam")
        return 0


def steam_deals(user):
    """De los juegos de «Lo quiero» de user que están en su lista de deseos de Steam: (cuántos son, cuántos están hoy
    en su mínimo histórico, cuántos son más baratos en otra tienda de PC que en Steam).

    Cuentan solo las tiendas del usuario si las ha elegido; el precio de Steam con el que se compara es siempre el
    de Steam.
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
    """
    if best is None or best.is_free:
        return None
    if item.target_price is not None and best.price <= item.target_price:
        return "objetivo"
    if at_historical_low(item.game, best):
        return "minimo"
    return None


def alert_text(item, best, reason):
    """Texto del aviso: el juego, el precio, la tienda y el motivo."""
    price = format_price(best.price, best.symbol)
    if reason == "objetivo":
        return f"{item.game.name} ha bajado a {price} en {best.shop_name} (tu límite: {format_price(item.target_price)})."
    return f"{item.game.name} está a {price} en {best.shop_name} (-{best.cut} %): su mínimo histórico."


def check_alerts(games=None):
    """Crea los avisos de bajada de precio de las listas de deseos (de esos juegos, o de todos) y manda
    un correo por usuario con los suyos si lo tiene activado. Devuelve cuántos avisos ha creado."""
    owned = LibraryEntry.objects.filter(user=OuterRef("user"), game=OuterRef("game"))
    items = (
        WishlistItem.objects.filter(~Exists(owned))
        .select_related("user__profile", "game")
        .prefetch_related("game__prices")
    )
    if games is not None:
        items = items.filter(game__in=games)

    created = defaultdict(list)  # usuario -> [aviso]
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
        created[item.user].append(Notification.objects.create(
            user=item.user, kind=Notification.Kind.PRICE, game=item.game,
            text=alert_text(item, best, reason), url=item.game.get_absolute_url(),
        ))
        item.alerted_price = best.price
        item.save(update_fields=["alerted_price"])

    for user, notifications in created.items():
        if user.email and user.profile.price_alert_emails:
            _send_email(user, notifications)
    return sum(len(notifications) for notifications in created.values())


def _send_email(user, notifications):
    """Manda al usuario un correo con sus avisos de precio; si falla, lo apunta en el log y sigue."""
    lines = [f"- {n.text}\n  {settings.SITE_URL}{n.url}" for n in notifications]
    body = (
        f"Hola, {user.profile.name}:\n\nHan bajado de precio juegos de tu lista de deseos en NextPlay:\n\n"
        + "\n".join(lines)
        + f"\n\nPuedes dejar de recibir estos correos en {settings.SITE_URL}/cuenta/perfil/\n"
    )
    subject = (
        notifications[0].text if len(notifications) == 1
        else f"{len(notifications)} juegos de tu lista de deseos han bajado de precio"
    )
    try:
        send_mail(subject[:150], body, None, [user.email])
    except Exception:  # un servidor de correo caído no debe parar la actualización de precios
        logger.exception("No se pudo mandar el correo de avisos de precio a %s", user.username)
