"""Filtros de plantilla de las reseñas: el formato del texto, las notas en estrellas, los permisos de los comentarios y
los motivos de las denuncias."""

from django import template

from .. import markup
from ..forms import stars_label as _stars_label
from ..models import Report

register = template.Library()


@register.simple_tag
def report_reasons():
    """Motivos que se pueden elegir al denunciar una reseña o un comentario: [(valor, texto)]."""
    return Report.Reason.choices


@register.filter
def review_html(text):
    """HTML seguro de una reseña o un comentario con su formato (ver library.markup)."""
    return markup.render(text)


@register.filter
def review_inline(text):
    """HTML seguro de un punto de «Lo mejor» o «Lo peor» con su formato, sin párrafo."""
    return markup.render_inline(text)


@register.filter
def review_plain(text):
    """Una reseña sin formato y con los spoilers tapados, para resúmenes."""
    return markup.plain(text)


@register.filter
def stars_label(rating):
    """Nota de 1 a 10 escrita en estrellas: «3,5 estrellas»; vacía sin nota."""
    return _stars_label(int(rating)) if rating else ""


@register.filter
def can_delete(comment, user):
    """Indica si user puede borrar el comentario (es suyo o la reseña es suya)."""
    return comment.can_delete(user)
