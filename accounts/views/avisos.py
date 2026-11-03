"""Vista de Avisos (avisos/): la página de la campana de la barra de arriba."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

NOTIFICATIONS_SHOWN = 50


@login_required
def notifications(request):
    """Últimos avisos del usuario (bajadas de precio, nuevos seguidores...). Al abrir la página, los no leídos pasan a leídos.

    Los que tienen quién los ha provocado (actor) traen ya lo que hace falta para enseñar su foto.

    Pantallas: Avisos, todas (la campana de la barra de arriba se queda sin número al abrirla).
    """
    shown = list(
        request.user.notifications.select_related("game", "actor__profile", "actor__psn_account", "actor__xbox_account")
        .prefetch_related("actor__steam_accounts")[:NOTIFICATIONS_SHOWN]
    )
    unread = {n.pk for n in shown if n.read_at is None}
    request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    return render(request, "accounts/notifications.html", {"shown": shown, "unread": unread})
