"""Vistas de Nueva lista (listas/nueva/) y Nueva tier list (tier-lists/nueva/), en blanco o desde una de las plantillas que
la rellenan con tu biblioteca.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from games.models import Game
from social import activity

from .. import presets
from ..forms import NewListForm, TierListForm
from ..models import GameList, ListItem


def _create(request, form, game=None):
    """Guarda como del usuario la lista nueva de form (ya validado), que empieza con game si se da; la apunta en su
    actividad (social.activity.list_created) y lleva a su editor, para buscar y añadir sus juegos.

    Pantallas: Nueva lista, Nueva tier list (al crearla), Inicio (feed), Perfil (pestañas Listas y Tier lists).
    """
    game_list = form.save(commit=False)
    game_list.user = request.user
    game_list.save()
    activity.list_created(game_list)
    if game:
        game_list.add(game, request.user)
        activity.list_games_added(game_list, request.user)
    messages.success(
        request,
        f"{game_list.noun.capitalize()} creada{' con ' + game.name if game else ''}. Busca aquí los juegos que quieras añadir.",
    )
    return redirect(f"{reverse('lists:edit', args=[game_list.pk])}#juegos")


@login_required
def new(request):
    """Crea una lista del usuario (POST) o enseña el formulario para hacerlo (GET): una lista sin más o numerada; las
    tier lists tienen su propia página (new_tier_list).

    Con juego= (desde «Nueva lista con este juego» en la ficha) la lista empieza con ese juego. Se apunta en su
    actividad y lleva a su editor, para buscar y añadir más (_create).

    Pantallas: Nueva lista.
    """
    raw = request.POST.get("juego") or request.GET.get("juego", "")
    game = Game.objects.filter(pk=raw).first() if raw.isdigit() else None
    form = NewListForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        return _create(request, form, game)
    return render(request, "lists/form.html", {"form": form, "game": game})


@login_required
def new_tier_list(request):
    """Página «Nueva tier list»: las plantillas que la crean ya rellena con la biblioteca del usuario (lists.presets; se
    crean con from_library) y el formulario para empezar una en blanco con sus niveles (POST), que la crea como new.

    Pantallas: Nueva tier list.
    """
    form = TierListForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        return _create(request, form)
    return render(request, "lists/tier_form.html", {
        "form": form, "presets": presets.choices(request.user), "min_games": presets.MIN_GAMES,
    })


@require_POST
@login_required
def from_library(request):
    """Crea una tier list del usuario con una de las plantillas de su biblioteca (plantilla=, ver lists.presets) y lleva
    a su página, para arrastrar sus juegos en el tablero.

    Los juegos entran en el orden de la plantilla, sin clasificar o ya colocados por su nota, y la lista se crea privada:
    recién hecha está sin colocar, y su dueño la hace visible cuando quiera. Se apunta en su actividad como cualquier
    lista (social.activity). Si ya tiene una lista con ese título, lleva a ella en vez de repetirla; con menos de
    presets.MIN_GAMES juegos, vuelve a «Nueva tier list» con un aviso.

    Pantallas: Nueva tier list (botón de cada plantilla), Página de la lista.
    """
    preset = presets.PRESETS.get(request.POST.get("plantilla", ""))
    if preset is None:
        raise Http404
    year = timezone.localdate().year
    title = preset.title_for(year)
    existing = GameList.objects.filter(user=request.user, title=title).first()
    if existing:
        messages.info(request, f"Ya tienes la {existing.noun} «{title}».")
        return redirect(existing)
    entries = preset.choose(request.user, year)
    if len(entries) < presets.MIN_GAMES:
        messages.error(request, f"Para «{title}» hacen falta al menos {presets.MIN_GAMES} juegos de tu biblioteca.")
        return redirect("lists:new_tier_list")
    with transaction.atomic():
        game_list = GameList.objects.create(
            user=request.user, title=title, mode=GameList.Mode.TIERS, tiers=preset.tier_list(), is_public=False,
        )
        ListItem.objects.bulk_create([
            ListItem(game_list=game_list, game=entry.game, position=position, tier=preset.tier_of(entry), added_by=request.user)
            for position, entry in enumerate(entries)
        ])
    activity.list_created(game_list)
    activity.list_games_added(game_list, request.user, count=len(entries))
    placed = sum(1 for entry in entries if preset.tier_of(entry) is not None)
    messages.success(request, (
        f"Tier list creada con {len(entries)} juegos"
        + (f" ({placed} ya colocado{pluralize(placed)} por tu nota)" if placed else "")
        + ". Arrástralos a su nivel; es privada hasta que la hagas visible en «Editar tier list»."
    ))
    return redirect(game_list)
