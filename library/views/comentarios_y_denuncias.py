"""Comentarios y denuncias, que comparten las reseñas, las publicaciones (social) y las listas (lists): las variables de
library/_comments.html y guardar una denuncia avisando a los administradores.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.models import Notification

from ..forms import ReportForm
from ..models import Report

# Lo que se puede denunciar: clave → (cómo se nombra en el botón, cómo se nombra en el aviso a los administradores).
REPORT_TARGETS = {
    "resena": ("esta reseña", "una reseña"),
    "comentario": ("este comentario", "un comentario"),
    "publicacion": ("esta publicación", "una publicación"),
    "lista": ("esta lista", "una lista"),
}


def comment_thread(parent, comments, viewer, *, comment_url, delete_url_name, report_url_name, count_id):
    """Variables de library/_comments.html para los comentarios de parent: una reseña o una publicación (social.Post).

    comments son sus comentarios (con su parent ya cargado, para saber quién puede borrarlos); salen con sus
    autores, en orden, y en cada uno si viewer ya lo ha denunciado (reported). comment_url es la dirección del
    formulario para comentar; delete_url_name y report_url_name, los nombres de las URL para borrar y denunciar
    un comentario; y count_id, el id del contador de comentarios que HTMX cambia al comentar o borrar.

    Pantallas: Página de la reseña, Página de la publicación, Página de la lista (comentarios).
    """
    comments = list(comments.select_related("user__profile", "user__psn_account").prefetch_related("user__steam_accounts"))
    reported = set()
    if viewer.is_authenticated and comments:
        reported = set(
            Report.objects.filter(
                reporter=viewer, content_type=ContentType.objects.get_for_model(comments[0]),
                object_id__in=[comment.pk for comment in comments],
            ).values_list("object_id", flat=True)
        )
    for comment in comments:
        comment.reported = comment.pk in reported
    parent.comment_count = len(comments)
    return {
        "comments": comments,
        "parent": parent,
        "comment_url": comment_url,
        "delete_url_name": delete_url_name,
        "report_url_name": report_url_name,
        "count_id": count_id,
    }


def _report(request, target, kind, game, back_url):
    """Guarda la denuncia del usuario sobre target (una reseña, un comentario, una publicación o una lista, según kind) y avisa a los administradores.

    Si ya lo había denunciado, cambia el motivo y la vuelve a dejar pendiente. Con HTMX devuelve el botón de
    denunciar (library/_report.html) ya como «Gracias» o, si falta el motivo, abierto con el error; si no,
    vuelve a back_url con un mensaje.

    Pantallas: Ficha del juego, Perfil, Página de la reseña, Página de la publicación, Página de la lista (formularios
    de denuncia), Panel de administración (denuncias), Avisos (aviso a los administradores).
    """
    form = ReportForm(request.POST)
    context = {"kind": kind, "target": target, "noun": REPORT_TARGETS[kind][0], "report_url": request.path}
    if form.is_valid():
        report, _ = Report.objects.update_or_create(
            content_type=ContentType.objects.get_for_model(target), object_id=target.pk, reporter=request.user,
            defaults={"reason": form.cleaned_data["reason"], "detail": form.cleaned_data["detail"], "resolved_at": None},
        )
        _notify_staff(report, f"{REPORT_TARGETS[kind][1]} de {target.user.username}", game)
        if request.headers.get("HX-Request"):
            return render(request, "library/_report.html", {**context, "reported": True, "thanks": True})
        messages.success(request, "Gracias por avisar: un administrador lo revisará.")
        return redirect(back_url)
    if request.headers.get("HX-Request"):
        return render(request, "library/_report.html", {**context, "open": True, "report_error": True})
    messages.error(request, "Elige el motivo de la denuncia.")
    return redirect(back_url)


def _notify_staff(report, what, game):
    """Avisa en la campana a los administradores de la denuncia de what («una reseña de ana») en game, sin
    repetirlo mientras tengan uno igual sin leer.

    game puede ser None (una publicación que no habla de ningún juego). El aviso lleva a la denuncia en el
    admin, desde donde se puede ir a lo denunciado y borrarlo.

    Pantallas: Avisos (de los administradores), Panel de administración (denuncias).
    """
    url = reverse("admin:library_report_change", args=[report.pk])
    where = f" en {game.name}" if game else ""
    text = f"Han denunciado {what}{where}: {report.get_reason_display()}."
    staff = get_user_model().objects.filter(is_staff=True, is_active=True).exclude(pk=report.reporter_id)
    for admin_user in staff:
        notices = Notification.objects.filter(user=admin_user, kind=Notification.Kind.REPORT, url=url, read_at=None)
        if not notices.exists():
            Notification.objects.create(user=admin_user, kind=Notification.Kind.REPORT, text=text[:300], url=url, game=game)
