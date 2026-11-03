"""Variables que reciben todas las plantillas: el número de avisos sin leer de la campana de la barra de arriba, que se
ve en todas las pantallas.
"""

from .models import Notification


def notifications(request):
    """Número de avisos sin leer del usuario con sesión, para la campana de la cabecera.

    Pantallas: todas (la campana de avisos de la barra de arriba).
    """
    if not request.user.is_authenticated:
        return {}
    return {"unread_notifications": Notification.objects.filter(user=request.user, read_at__isnull=True).count()}
