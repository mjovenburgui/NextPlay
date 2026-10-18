"""Historial de precios de PC de un juego (de IsThereAnyDeal), veredicto «¿compro ahora o espero?» y gráfica.

El historial se guarda en Game.price_history como el precio más barato entre todas las tiendas a lo
largo del tiempo: un punto [fecha ISO, precio, % de rebaja, tienda] por cada vez que cambia. Se pide
a ITAD al abrir la ficha y se reutiliza durante MAX_AGE.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from . import prices
from .models import GamePrice, format_price

# Se piden dos años para saber cuánto costaba al empezar la gráfica, que enseña el último.
HISTORY_DAYS = 730
CHART_DAYS = 365
MAX_AGE = timedelta(hours=24)
# Rebaja a partir de la cual se considera «rebaja fuerte».
BIG_SALE_CUT = 50
# Un precio es «bueno» si está como mucho un 10 % por encima de lo más barato del último año.
GOOD_MARGIN = Decimal("1.10")
# Para contar las veces que bajó «de este precio» hace falta bajar al menos un 5 %; dos bajadas con
# menos de DROP_GAP entre medias (rebajas de varias tiendas que se solapan) cuentan como una.
DROP_MARGIN = Decimal("0.95")
DROP_GAP = timedelta(days=3)
VERDICTS = {
    "minimo": "Está en su mínimo histórico",
    "bueno": "Buen precio",
    "espera": "Suele bajar más: espera",
}


def needs_update(game, now=None):
    """Indica si hay que volver a pedir el historial: nunca se pidió o tiene más de MAX_AGE."""
    now = now or timezone.now()
    return game.price_history_updated_at is None or game.price_history_updated_at < now - MAX_AGE


def _amount(value):
    """Importe de un precio de ITAD como Decimal con dos decimales."""
    return Decimal(str(value["amount"])).quantize(Decimal("0.01"))


def lowest_series(events, seed, start):
    """Precio más barato entre todas las tiendas a lo largo del tiempo, a partir de los cambios de ITAD.

    seed es {id de tienda: (precio, rebaja, nombre)} con lo que cuesta hoy en cada tienda; se usa
    como precio de partida de las tiendas sin cambios en el periodo. Las que sí tienen cambios
    empiezan con el precio normal de su primer cambio (antes de una rebaja se vendía a precio
    normal). Devuelve [[fecha ISO, precio, rebaja, tienda], ...] de más viejo a más nuevo, con un
    punto solo cuando cambia el precio más barato.
    """
    events = sorted(events, key=lambda event: datetime.fromisoformat(event["timestamp"]))
    state = dict(seed)
    first = {}
    for event in events:
        first.setdefault(event["shop"]["id"], event)
    for shop_id, event in first.items():
        if event.get("deal"):
            state[shop_id] = (_amount(event["deal"]["regular"]), 0, event["shop"]["name"])
        else:
            state.pop(shop_id, None)  # empezó a venderlo después: no se sabe a cuánto antes

    points = []

    def push(moment):
        """Añade el precio más barato de ese momento si ha cambiado."""
        if not state:
            return
        price, cut, shop = min(state.values(), key=lambda value: value[0])
        if not points or Decimal(str(points[-1][1])) != price:
            points.append([moment.isoformat(timespec="seconds"), float(price), cut, shop])

    push(start)
    for event in events:
        deal, shop = event.get("deal"), event["shop"]
        if deal:
            state[shop["id"]] = (_amount(deal["price"]), deal.get("cut") or 0, shop["name"])
        else:
            state.pop(shop["id"], None)
        push(datetime.fromisoformat(event["timestamp"]))
    return points


def update_history(game, client=None, now=None):
    """Pide a ITAD el historial de los dos últimos años del juego y lo guarda. Lanza ITADError si falla."""
    client = client or prices.ITADClient()
    now = now or timezone.now()
    itad_ids = game.itad_ids or prices.lookup_ids(game, client)
    since = now - timedelta(days=HISTORY_DAYS)
    events = [event for itad_id in itad_ids for event in client.history(itad_id, since)]
    seed = {
        price.shop_id: (price.price, price.cut, price.shop_name)
        for price in game.prices.all()
        if price.source == GamePrice.Source.ITAD
    }
    game.itad_ids = itad_ids
    game.price_history = lowest_series(events, seed, since)
    game.price_history_updated_at = now
    game.save(update_fields=["itad_ids", "price_history", "price_history_updated_at"])


def _points(game):
    """Puntos del historial guardado como (fecha, precio Decimal, rebaja, tienda)."""
    return [
        (datetime.fromisoformat(moment), Decimal(str(price)).quantize(Decimal("0.01")), cut, shop)
        for moment, price, cut, shop in game.price_history
    ]


def _price_at(points, moment):
    """Precio más barato que tenía en ese momento (el del último cambio anterior), o None si no se sabe."""
    before = [price for when, price, _, _ in points if when <= moment]
    return before[-1] if before else None


def _year(points, now):
    """[(fecha, precio)] del último año, empezando con el precio que tenía hace un año si se sabe."""
    start = now - timedelta(days=CHART_DAYS)
    series = [(start, price)] if (price := _price_at(points, start)) is not None else []
    return series + [(when, price) for when, price, _, _ in points if start < when <= now]


def verdict(game, current, now):
    """Veredicto para el precio de hoy: {"code", "title", "text", "year_low"}.

    «minimo» si está en su mínimo histórico; «bueno» si en el último año no ha estado más barato o
    está cerca de lo más barato del año; «espera» si ha bajado de ese precio varias veces.
    """
    year = _year(_points(game), now)
    year_low = min([price for _, price in year] + [current])
    threshold = current * DROP_MARGIN
    drops, below, rose_at = 0, False, None
    for when, price in year:
        if price < threshold:
            if not below and (rose_at is None or when - rose_at >= DROP_GAP):
                drops += 1
            below = True
        elif below:
            below, rose_at = False, when

    if game.historical_low is not None and current <= game.historical_low:
        code, text = "minimo", "Nunca ha estado más barato en PC."
    elif drops == 0:
        code, text = "bueno", "En el último año no ha estado más barato."
    elif current <= year_low * GOOD_MARGIN:
        code, text = "bueno", f"Está cerca de lo más barato del último año ({format_price(year_low)})."
    else:
        times = "una vez" if drops == 1 else f"{drops} veces"
        code, text = "espera", f"En el último año ha bajado de este precio {times}, hasta {format_price(year_low)}."
    return {"code": code, "title": VERDICTS[code], "text": text, "year_low": year_low}


def last_big_sale(game):
    """Última vez que estuvo rebajado al menos BIG_SALE_CUT %: {"date", "cut", "price", "shop"}, o None."""
    sales = [point for point in _points(game) if point[2] >= BIG_SALE_CUT]
    if not sales:
        return None
    when, price, cut, shop = sales[-1]
    return {"date": when, "cut": cut, "price": price, "shop": shop}


def chart(game, now, current=None, width=320, height=90, pad=6):
    """Datos de la gráfica escalonada del último año para dibujarla en SVG, o None si no hay historial.

    Termina en current (el precio de hoy que enseña la ficha) si se da: el historial de ITAD puede
    contar alguna tienda que no sale entre los precios de hoy.

    Devuelve el tamaño, los puntos de la línea («line») y del área bajo ella («area»), el precio
    más alto y más bajo de la gráfica y la altura de la línea del mínimo histórico («low_y», None si
    queda fuera). Las coordenadas van ya en texto con punto decimal: en una plantilla en español,
    floatformat pondría coma y el SVG no sería válido.
    """
    start = now - timedelta(days=CHART_DAYS)
    series = _year(_points(game), now)
    if not series:
        return None
    series.append((now, current if current is not None else series[-1][1]))
    low, high = min(price for _, price in series), max(price for _, price in series)
    span = (high - low) or Decimal(1)

    def x(moment):
        """Posición horizontal de una fecha (el primer punto puede ser anterior a start)."""
        return pad + max((moment - start) / (now - start), 0) * (width - 2 * pad)

    def y(price):
        """Posición vertical de un precio (arriba el más caro)."""
        return pad + float((high - price) / span) * (height - 2 * pad)

    coords = []
    for (when, price), (next_when, _) in zip(series, series[1:]):
        coords += [(x(when), y(price)), (x(next_when), y(price))]
    coords.append((x(now), y(series[-1][1])))  # el último salto, hasta el precio de hoy
    line = " ".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coords)
    bottom = height - pad
    area = f"{coords[0][0]:.1f},{bottom} {line} {coords[-1][0]:.1f},{bottom}"
    historical_low = game.historical_low
    low_y = f"{y(historical_low):.1f}" if historical_low is not None and low <= historical_low <= high else None
    return {
        "width": width, "height": height, "line": line, "area": area,
        "high": high, "low": low, "low_y": low_y, "start": start,
    }


def summary(game, now=None):
    """Todo lo que enseña la ficha sobre el historial, o None si no hay historial o precios de PC.

    {"current": precio más barato de hoy en PC, "verdict", "chart", "last_big_sale"}.
    """
    now = now or timezone.now()
    pc = [price for price in game.prices.all() if price.source == GamePrice.Source.ITAD]
    if not game.price_history or not pc or game.is_free:
        return None
    current = min(pc, key=lambda price: price.price)
    return {
        "current": current,
        "verdict": verdict(game, current.price, now),
        "chart": chart(game, now, current.price),
        "last_big_sale": last_big_sale(game),
    }
