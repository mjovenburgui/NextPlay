"""Lo que comparten varias vistas de las cuentas: volver a la página desde la que se envió un formulario (Editar perfil, el
Perfil o Juegos importados).
"""

from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme


def _back(request, default):
    """Redirige a la página de la que vino el formulario (campo «next») si es de esta web; si no, a default.

    Así las acciones lanzadas desde el modal del perfil vuelven al perfil y no a Editar perfil.

    Pantallas: Editar perfil, Perfil, Juegos importados (vuelve a la página desde la que se envió el formulario).
    """
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(default)
