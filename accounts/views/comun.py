"""Lo que comparten varias vistas de las cuentas: volver a la página desde la que se envió un formulario (Editar perfil, el
Perfil, Juegos importados o Configurar NextPlay) y el selector de tiendas (Editar perfil y Configurar NextPlay).
"""

from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

from games.models import FEATURED_SHOPS, known_shops


def shop_picker(chosen):
    """Variables del selector de tiendas (accounts/_shop_picker.html) con las de chosen marcadas (ids de
    GamePrice.shop_id): las destacadas de FEATURED_SHOPS, cada una con su logo (featured_shops), y las demás, plegadas
    (other_shops), que llegan desplegadas si alguna va marcada (others_open) y dicen cuántas lo están (others_checked).
    Solo salen las tiendas con algún precio en el catálogo (games.models.known_shops).

    Pantallas: Configurar NextPlay (Dónde compras), Editar perfil (Tiendas en las que compras).
    """
    chosen = set(chosen)
    featured, others = [], []
    for shop_id, name in known_shops():
        if shop_id in FEATURED_SHOPS:
            featured.append((shop_id, name, FEATURED_SHOPS[shop_id], shop_id in chosen))
        else:
            others.append((shop_id, name, shop_id in chosen))
    checked = sum(1 for _, _, is_checked in others if is_checked)
    return {
        "featured_shops": featured, "other_shops": others, "others_open": bool(checked), "others_checked": checked,
    }


def _back(request, default):
    """Redirige a la página de la que vino el formulario (campo «next») si es de esta web; si no, a default.

    Así las acciones lanzadas desde el modal del perfil vuelven al perfil y no a Editar perfil, y las de Configurar
    NextPlay, a su paso.

    Pantallas: Editar perfil, Perfil, Juegos importados, Configurar NextPlay (vuelve a la página desde la que se envió el
    formulario).
    """
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(default)
