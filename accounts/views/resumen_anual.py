"""Resumen del año (u/<usuario>/resumen/<año>/): lo que ha jugado un jugador ese año y la imagen para compartirlo."""

from django.contrib.auth import get_user_model
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from library import diary, stats

User = get_user_model()
# Años con resumen: desde este hasta el actual.
FIRST_YEAR = 2000


def _owner(request, username, year):
    """El jugador del resumen, si quien mira puede ver su perfil y el año tiene sentido; si no, 404 (un perfil privado
    da 404 a cualquiera que no sea su dueño, como en el Perfil).

    Pantallas: Resumen del año.
    """
    owner = get_object_or_404(User.objects.select_related("profile"), username=username)
    if not owner.profile.visible_to(request.user) or not FIRST_YEAR <= year <= timezone.localdate().year:
        raise Http404
    return owner


def year_summary(request, username, year):
    """El Resumen del año de un jugador: sus cifras, sus juegos más jugados, lo que terminó, sus logros y la imagen para
    compartirlo (library.stats.year_summary), con los demás años que tienen algo (library.diary.years).

    Pantallas: Resumen del año.
    """
    owner = _owner(request, username, year)
    return render(request, "accounts/year_summary.html", {
        "owner": owner,
        "profile": owner.profile,
        "summary": stats.year_summary(owner, year),
        "years": diary.years(owner),
        "is_owner": request.user == owner,
    })


def year_summary_image(request, username, year):
    """La imagen del Resumen del año de un jugador, en PNG (library.stats.year_image), para compartirla o descargarla.

    Pantallas: Resumen del año (la imagen para compartir).
    """
    owner = _owner(request, username, year)
    response = HttpResponse(stats.year_image(owner, stats.year_summary(owner, year)), content_type="image/png")
    response["Content-Disposition"] = f'inline; filename="nextplay-{owner.username}-{year}.png"'
    return response
