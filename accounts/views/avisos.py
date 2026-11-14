"""Vista de Avisos (avisos/): la página de la campana de la barra de arriba."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from .. import push

NOTIFICATIONS_SHOWN = 50


@login_required
def notifications(request):
    """Últimos avisos del usuario (bajadas de precio, nuevos seguidores...). Al abrir la página, los no leídos pasan a leídos.

    Los que tienen quién los ha provocado (actor) traen ya lo que hace falta para enseñar su foto. Si NextPlay puede
    mandar avisos push y no los tiene activados en ningún dispositivo, la página le dice dónde activarlos.

    Pantallas: Avisos, todas (la campana de la barra de arriba se queda sin número al abrirla).
    """
    shown = list(
        request.user.notifications.select_related("game", "actor__profile", "actor__psn_account", "actor__xbox_account")
        .prefetch_related("actor__steam_accounts")[:NOTIFICATIONS_SHOWN]
    )
    unread = {n.pk for n in shown if n.read_at is None}
    request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    push_hint = push.is_configured() and not request.user.push_subscriptions.exists()
    return render(request, "accounts/notifications.html", {"shown": shown, "unread": unread, "push_hint": push_hint})
