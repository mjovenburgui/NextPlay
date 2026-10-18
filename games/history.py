"""Historial de precios de PC de un juego (de IsThereAnyDeal), veredicto «¿compro ahora o espero?» y gráfica.

El historial se guarda en Game.price_history por tienda, para poder enseñarlo de todas o solo de
algunas (las del usuario, o una concreta):

    {"since": fecha ISO, "shops": {"61": {"name": "Steam", "points": [[fecha ISO, precio o None, % de rebaja], ...]}}}

Cada tienda tiene un punto por cada cambio de precio, de más viejo a más nuevo; precio None es que
dejó de venderlo. Se pide a ITAD al abrir la ficha y se reutiliza durante MAX_AGE.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from . import prices
from .models import FEATURED_SHOPS, GamePrice, format_price

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
# Valor de ?tienda= para ver todas las tiendas aunque el usuario tenga elegidas las suyas.
ALL_SHOPS = "todas"


def needs_update(game, now=None):
    """Indica si hay que volver a pedir el historial: nunca se pidió, tiene más de MAX_AGE o es del
    formato antiguo (solo el más barato, sin separar por tienda)."""
    now = now or timezone.now()
    return (
        not isinstance(game.price_history, dict)
        or game.price_history_updated_at is None
        or game.price_history_updated_at < now - MAX_AGE
    )


def _amount(value):
    """Importe de un precio de ITAD como Decimal con dos decimales."""
    return Decimal(str(value["amount"])).quantize(Decimal("0.01"))


def _decimal(value):
    """Precio guardado en el JSON (float) como Decimal con dos decimales."""
    return Decimal(str(value)).quantize(Decimal("0.01"))


def shop_series(events, seed, start):
    """Historial de cada tienda a partir de los cambios de ITAD: {"since", "shops"} (ver el docstring del módulo).

    seed es {id de tienda: (precio, rebaja, nombre)} con lo que cuesta hoy en cada tienda; se usa
    como precio de partida de las tiendas sin cambios en el periodo. Las que sí tienen cambios
    empiezan con el precio normal de su primer cambio (antes de una rebaja se vendía a precio
    normal); si su primer cambio es que dejó de venderlo, no se sabe a cuánto estaba y empiezan ahí.
    """
    events = sorted(events, key=lambda event: datetime.fromisoformat(event["timestamp"]))
    since = start.isoformat(timespec="seconds")
    shops = {}
    for event in events:
        shop_id, deal = str(event["shop"]["id"]), event.get("deal")
        if shop_id not in shops:
            shops[shop_id] = {"name": event["shop"]["name"], "points": []}
            if deal:
                shops[shop_id]["points"].append([since, float(_amount(deal["regular"])), 0])
        point = [float(_amount(deal["price"])), deal.get("cut") or 0] if deal else [None, 0]
        shops[shop_id]["points"].append([event["timestamp"], *point])
    for shop_id, (price, cut, name) in seed.items():
        if str(shop_id) not in shops:
            shops[str(shop_id)] = {"name": name, "points": [[since, float(price), cut]]}
    return {"since": since, "shops": shops}


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
    game.price_history = shop_series(events, seed, since)
    game.price_history_updated_at = now
    game.save(update_fields=["itad_ids", "price_history", "price_history_updated_at"])


def lowest_series(history, shops=None):
    """Precio más barato entre esas tiendas (ids; None = todas) a lo largo del tiempo.

    Devuelve [(fecha, precio, rebaja, tienda)] de más viejo a más nuevo, con un punto solo cuando
    cambia el más barato; si varias tiendas cambian a la vez, queda el resultado final.
    """
    changes = []
    for shop_id, data in (history.get("shops") or {}).items():
        if shops is not None and int(shop_id) not in shops:
            continue
        for moment, price, cut in data["points"]:
            changes.append((datetime.fromisoformat(moment), int(shop_id), price, cut, data["name"]))
    changes.sort(key=lambda change: change[0])

    state, points = {}, []
    for when, shop_id, price, cut, name in changes:
        if price is None:
            state.pop(shop_id, None)
        else:
            state[shop_id] = (_decimal(price), cut, name)
        if not state:
            continue
        low = min(state.values(), key=lambda value: value[0])
        if points and points[-1][0] == when:
            points.pop()  # varios cambios en el mismo momento: cuenta el último
        if not points or points[-1][1] != low[0]:
            points.append((when, *low))
    return points


def _year(points, now):
    """Puntos (fecha, precio, rebaja, tienda) del último año, empezando con el que estaba vigente hace un
    año (con esa fecha) si se sabe."""
    start = now - timedelta(days=CHART_DAYS)
    before = [point for point in points if point[0] <= start]
    series = [(start, *before[-1][1:])] if before else []
    return series + [point for point in points if start < point[0] <= now]


def verdict(points, current, low, now, all_shops=True):
    """Veredicto para el precio de hoy: {"code", "title", "text", "year_low"}.

    «minimo» si está en su mínimo (low: el histórico con todas las tiendas, o el de los dos años de
    historial con unas tiendas concretas); «bueno» si en el último año no ha estado más barato o
    está cerca de lo más barato del año; «espera» si ha bajado de ese precio varias veces.
    """
    year = _year(points, now)
    year_low = min([price for _, price, _, _ in year] + [current])
    threshold = current * DROP_MARGIN
    drops, below, rose_at = 0, False, None
    for when, price, _, _ in year:
        if price < threshold:
            if not below and (rose_at is None or when - rose_at >= DROP_GAP):
                drops += 1
            below = True
        elif below:
            below, rose_at = False, when

    if low is not None and current <= low:
        if all_shops:
            code, title, text = "minimo", "Está en su mínimo histórico", "Nunca ha estado más barato en PC."
        else:
            code, title = "minimo", "Lo más barato en dos años"
            text = "En estas tiendas no ha estado más barato en los dos últimos años."
    elif drops == 0:
        code, title, text = "bueno", "Buen precio", "En el último año no ha estado más barato."
    elif current <= year_low * GOOD_MARGIN:
        code, title = "bueno", "Buen precio"
        text = f"Está cerca de lo más barato del último año ({format_price(year_low)})."
    else:
        times = "una vez" if drops == 1 else f"{drops} veces"
        code, title = "espera", "Suele bajar más: espera"
        text = f"En el último año ha bajado de este precio {times}, hasta {format_price(year_low)}."
    return {"code": code, "title": title, "text": text, "year_low": year_low}


def last_big_sale(points):
    """Última vez que estuvo rebajado al menos BIG_SALE_CUT %: {"date", "cut", "price", "shop"}, o None."""
    sales = [point for point in points if point[2] >= BIG_SALE_CUT]
    if not sales:
        return None
    when, price, cut, shop = sales[-1]
    return {"date": when, "cut": cut, "price": price, "shop": shop}


def chart(points, now, current=None, low_line=None, width=320, height=90, pad=6):
    """Datos de la gráfica escalonada del último año para dibujarla en SVG, o None si no hay historial.

    Termina en current (el GamePrice más barato de hoy, el que enseña la ficha) si se da: el
    historial de ITAD puede contar alguna tienda que no sale entre los precios de hoy. low_line es
    el precio de la línea del mínimo.

    Devuelve el tamaño, los puntos de la línea («line») y del área bajo ella («area»), el precio
    más alto y más bajo de la gráfica, la altura de la línea del mínimo («low_y», None si queda
    fuera) y, en JSON («hover»), cada tramo con su fecha, posición, precio, rebaja y tienda para la
    etiqueta que sale al pasar por encima. Las coordenadas van ya en texto con punto decimal: en una
    plantilla en español, floatformat pondría coma y el SVG no sería válido.
    """
    start = now - timedelta(days=CHART_DAYS)
    series = _year(points, now)
    if not series:
        return None
    if current is not None:
        series.append((now, current.price, current.cut, current.shop_name))
    else:
        series.append((now, *series[-1][1:]))
    low, high = min(point[1] for point in series), max(point[1] for point in series)
    span = (high - low) or Decimal(1)

    def x(moment):
        """Posición horizontal de una fecha (el primer punto puede ser anterior a start)."""
        return pad + max((moment - start) / (now - start), 0) * (width - 2 * pad)

    def y(price):
        """Posición vertical de un precio (arriba el más caro)."""
        return pad + float((high - price) / span) * (height - 2 * pad)

    coords = []
    for (when, price, _, _), (next_when, *_) in zip(series, series[1:]):
        coords += [(x(when), y(price)), (x(next_when), y(price))]
    coords.append((x(now), y(series[-1][1])))  # el último salto, hasta el precio de hoy
    line = " ".join(f"{cx:.1f},{cy:.1f}" for cx, cy in coords)
    bottom = height - pad
    area = f"{coords[0][0]:.1f},{bottom} {line} {coords[-1][0]:.1f},{bottom}"
    low_y = f"{y(low_line):.1f}" if low_line is not None and low <= low_line <= high else None
    hover = {
        "start": int(start.timestamp() * 1000), "end": int(now.timestamp() * 1000),
        "pad": pad, "width": width, "height": height,
        "points": [
            {
                "t": int(max(when, start).timestamp() * 1000), "x": round(x(when), 1), "y": round(y(price), 1),
                "price": format_price(price), "cut": cut, "shop": shop,
            }
            for when, price, cut, shop in series
        ],
    }
    return {
        "width": width, "height": height, "line": line, "area": area,
        "high": high, "low": low, "low_y": low_y, "start": start, "hover": json.dumps(hover),
    }


def shop_options(game, user_shops):
    """Opciones del selector de tienda de la gráfica: [(valor de ?tienda=, etiqueta)].

    «Tus tiendas» (si el usuario las tiene elegidas), «Todas las tiendas» y cada tienda con historial,
    las más conocidas primero.
    """
    shops = game.price_history.get("shops") if isinstance(game.price_history, dict) else None
    featured = list(FEATURED_SHOPS)

    def order(item):
        """Primero las tiendas destacadas, en su orden; después las demás por nombre."""
        shop_id = int(item[0])
        return (featured.index(shop_id) if shop_id in featured else len(featured), item[1]["name"].casefold())

    options = [("", "Tus tiendas")] if user_shops else []
    options.append((ALL_SHOPS, "Todas las tiendas"))
    return options + [(shop_id, data["name"]) for shop_id, data in sorted((shops or {}).items(), key=order)]


def selected_shops(value, user_shops):
    """Tiendas que enseñar según ?tienda= y las del usuario: (ids o None para todas, valor elegido).

    Sin valor: las del usuario si las tiene, si no todas. «todas»: todas. Un id: solo esa tienda.
    """
    value = value or ""
    if value.isdigit():
        return {int(value)}, value
    if value == ALL_SHOPS or not user_shops:
        return None, ALL_SHOPS if user_shops else ""
    return set(user_shops), ""


def summary(game, now=None, shops=None):
    """Todo lo que enseña la ficha sobre el historial de esas tiendas (ids; None = todas).

    {"current": precio más barato de hoy en esas tiendas de PC, "verdict", "chart", "last_big_sale",
    "all_shops"}, o None si no hay historial, el juego es gratis o ninguna de esas tiendas lo vende.
    """
    now = now or timezone.now()
    if not isinstance(game.price_history, dict) or not game.price_history.get("shops") or game.is_free:
        return None
    pc = [
        price for price in game.prices.all()
        if price.source == GamePrice.Source.ITAD and (shops is None or price.shop_id in shops)
    ]
    points = lowest_series(game.price_history, shops)
    if not pc or not points:
        return None
    current = min(pc, key=lambda price: price.price)
    all_shops = shops is None
    # Con unas tiendas concretas el mínimo histórico de ITAD (de todas) no sirve: se usa el de su historial.
    low = game.historical_low if all_shops else min([price for _, price, _, _ in points] + [current.price])
    return {
        "current": current,
        "verdict": verdict(points, current.price, low, now, all_shops),
        "chart": chart(points, now, current, low),
        "last_big_sale": last_big_sale(points),
        "all_shops": all_shops,
    }
