from .models import Notification


def notifications(request):
    """Número de avisos sin leer del usuario con sesión, para la campana de la cabecera."""
    if not request.user.is_authenticated:
        return {}
    return {"unread_notifications": Notification.objects.filter(user=request.user, read_at__isnull=True).count()}
