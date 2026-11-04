"""Vistas de NextPlay que no son de ninguna app: la página de formulario caducado (CSRF_FAILURE_VIEW). Las de página
no encontrada y fallo del servidor son las de Django, con las plantillas propias 404.html y 500.html.

Pantallas: Páginas de error.
"""

from django.conf import settings
from django.shortcuts import render


def csrf_failure(request, reason=""):
    """Formulario caducado: lo que sale cuando un envío no trae su comprobación de seguridad (CSRF) o ya no vale, casi
    siempre porque la página llevaba mucho rato abierta o se ha cerrado la sesión en otra pestaña. Va con la cabecera
    y la sesión de siempre (la plantilla por defecto de Django se pinta sin ellas) y, en desarrollo, con el motivo.

    Pantallas: Páginas de error (formulario caducado).
    """
    return render(request, "403_csrf.html", {"reason": reason if settings.DEBUG else ""}, status=403)
