"""Quién puede editar una lista: invitar y quitar a quien colabora (en Editar lista, sección «Quién puede editarla») y
aceptar o dejar la invitación (en la Página de la lista).
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts import notices
from accounts.models import Notification

from ..models import MAX_COLLABORATORS, ListCollaborator
from .comun import _own_list

User = get_user_model()
# Gente a la que sigue el dueño de una lista que se le sugiere al invitar a editarla.
SUGGESTED_COLLABORATORS = 20


def collaborators_context(game_list, owner):
    """Variables de la sección «Quién puede editarla» del editor (lists/_collaborators.html): la gente invitada, con si
    ha aceptado; cuántos caben; y, para escribir su nombre, la gente que sigue a owner y aún no está invitada, que es a
    quien puede invitar (suggested, como mucho SUGGESTED_COLLABORATORS).

    Pantallas: Editar lista («Quién puede editarla»).
    """
    collaborators = list(game_list.collaborators.select_related("user__profile", "user__psn_account", "user__xbox_account"))
    invited = [collaborator.user_id for collaborator in collaborators]
    suggested = list(
        owner.followers.exclude(follower_id__in=invited)
        .values_list("follower__username", flat=True)[:SUGGESTED_COLLABORATORS]
    )
    return {
        "game_list": game_list, "collaborators": collaborators, "max_collaborators": MAX_COLLABORATORS,
        "suggested": suggested,
    }


def _collaborators_response(request, game_list, error=""):
    """Respuesta de invitar o quitar a alguien de una lista: con HTMX, la sección «Quién puede editarla» del editor
    (con el error, si lo hay); sin HTMX, vuelve a ella avisando del error.

    Pantallas: Editar lista («Quién puede editarla»).
    """
    if request.headers.get("HX-Request"):
        return render(request, "lists/_collaborators.html", {
            **collaborators_context(game_list, request.user), "collaborator_error": error,
        })
    if error:
        messages.error(request, error)
    return redirect(f"{reverse('lists:edit', args=[game_list.pk])}#colaboradores")


@require_POST
@login_required
def invite(request, list_id):
    """Invita a alguien (usuario=, su nombre de usuario, con o sin @) a editar los juegos de una lista del usuario y se
    lo avisa en la campana. Solo a quien le sigue: seguir ya es una forma de decir que quiere saber de él. Caben
    MAX_COLLABORATORS; no puede invitarse a sí mismo ni a quien ya está invitado.

    Pantallas: Editar lista (invitar a alguien), Avisos (aviso al invitado), Página de la lista (le pregunta si
    acepta).
    """
    game_list = _own_list(request, list_id)
    username = request.POST.get("usuario", "").strip().lstrip("@")
    invitee = User.objects.select_related("profile").filter(username__iexact=username).first() if username else None
    error = ""
    if invitee is None:
        error = f"No hay nadie con el nombre de usuario «{username}»." if username else "Escribe su nombre de usuario."
    elif invitee == request.user:
        error = "Ya puedes editarla: es tuya."
    elif game_list.collaborators.filter(user=invitee).exists():
        error = f"{invitee.profile.name} ya está invitado."
    elif not invitee.following.filter(followed=request.user).exists():
        error = f"{invitee.profile.name} no te sigue: solo puedes invitar a gente que te sigue."
    elif game_list.collaborators.count() >= MAX_COLLABORATORS:
        error = f"Caben {MAX_COLLABORATORS} personas por lista: quita a alguien para invitar a otra."
    else:
        ListCollaborator.objects.create(game_list=game_list, user=invitee)
        notices.notify(
            invitee, Notification.Kind.LIST_INVITE,
            f"{request.user.profile.name} te ha invitado a editar su {game_list.noun} «{game_list.title}».",
            game_list.get_absolute_url(), actor=request.user,
        )
    return _collaborators_response(request, game_list, error)


@require_POST
@login_required
def collaborator_remove(request, list_id, user_id):
    """Quita a alguien de los que pueden editar una lista del usuario, o retira su invitación; lo que añadió se queda.

    Pantallas: Editar lista (quitar a alguien o retirar su invitación).
    """
    game_list = _own_list(request, list_id)
    game_list.collaborators.filter(user_id=user_id).delete()
    return _collaborators_response(request, game_list)


@require_POST
@login_required
def accept(request, list_id):
    """Acepta la invitación del usuario a editar una lista, se lo cuenta a su dueño (una vez por persona:
    accounts.notices) y lleva a su editor; 404 si no le han invitado.

    Pantallas: Página de la lista (aceptar la invitación), Editar lista, Avisos (al dueño de la lista).
    """
    invitation = get_object_or_404(
        ListCollaborator.objects.select_related("game_list__user"), game_list_id=list_id, user=request.user,
    )
    if invitation.accepted_at is None:
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["accepted_at"])
        game_list = invitation.game_list
        notices.notify(
            game_list.user, Notification.Kind.LIST_ACCEPT,
            f"{request.user.profile.name} ya puede editar tu {game_list.noun} «{game_list.title}».",
            game_list.get_absolute_url(), actor=request.user,
        )
        messages.success(request, "Ya puedes añadir, ordenar y comentar los juegos de esta lista.")
    return redirect(f"{reverse('lists:edit', args=[list_id])}#juegos")


@require_POST
@login_required
def leave(request, list_id):
    """Rechaza la invitación del usuario a editar una lista o deja de colaborar en ella (lo que añadió se queda).

    Vuelve a la lista si aún puede verla y, si no (era privada), a sus listas (o a sus tier lists, si lo era); 404 si no
    le habían invitado.

    Pantallas: Página de la lista (rechazar la invitación), Editar lista (dejar de colaborar).
    """
    invitation = get_object_or_404(
        ListCollaborator.objects.select_related("game_list__user__profile"), game_list_id=list_id, user=request.user,
    )
    game_list, joined = invitation.game_list, invitation.accepted_at is not None
    invitation.delete()
    messages.success(request, "Ya no colaboras en esta lista." if joined else "Invitación rechazada.")
    if game_list.visible_to(request.user):
        return redirect(game_list)
    return redirect(f"{reverse('profile', args=[request.user.username])}?vista={game_list.profile_tab}")
