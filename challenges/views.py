from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Exists, OuterRef, Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.models import Profile
from lists.models import GameList

from . import progress
from .forms import ChallengeEditForm, ChallengeForm
from .models import Challenge, Participation

# Retos de cada sección de la página de retos.
SECTION_SIZE = 12
# Participantes que salen en la clasificación de la página de un reto.
RANKING_SIZE = 50


def _open(challenges):
    """Los retos de challenges a los que aún se puede unir alguien: los que no han terminado."""
    return challenges.filter(Q(ends_on__isnull=True) | Q(ends_on__gte=timezone.localdate()))


def _with_progress(challenges, user):
    """Pone en cada reto de challenges cómo va user en él (mine, ver challenges.progress.summary) y si lo ha
    conseguido (won); devuelve la misma lista."""
    won = set(
        Participation.objects.filter(user=user, challenge__in=challenges, completed_at__isnull=False)
        .values_list("challenge_id", flat=True)
    )
    for challenge in challenges:
        challenge.mine = progress.summary(challenge, progress.done_by(challenge, [user]).get(user.pk, 0))
        challenge.won = challenge.pk in won
    return challenges


def explore(request):
    """Página de retos: con sesión, los tuyos (con cómo vas en cada uno) y los de la gente que sigues; para todos, los
    más populares que siguen abiertos (con más gente) y los últimos que se han creado. Solo salen los públicos, salvo
    los tuyos, y con sesión, abajo solo aquellos en los que aún no estás: los tuyos ya salen arriba."""
    viewer = request.user
    others = _open(Challenge.objects.filter(is_public=True).with_counts())
    context = {}
    if viewer.is_authenticated:
        # Con una subconsulta: filtrar por la participación antes de contar haría que solo se contara la suya.
        joined_ids = Participation.objects.filter(user=viewer).values("challenge_id")
        mine = list(Challenge.objects.filter(pk__in=joined_ids).with_counts().order_by("-created_at"))
        context["mine"] = _with_progress(mine, viewer)
        others = others.exclude(Exists(Participation.objects.filter(challenge=OuterRef("pk"), user=viewer)))
        context["followed"] = list(others.filter(creator__followers__follower=viewer).order_by("-created_at")[:SECTION_SIZE])
    context["popular"] = list(others.filter(participant_count__gt=0).order_by("-participant_count", "-created_at")[:SECTION_SIZE])
    context["recent"] = list(others.order_by("-created_at")[:SECTION_SIZE])
    return render(request, "challenges/explore.html", context)


def _visible(request, challenge_id):
    """Reto challenge_id si el usuario puede verlo; si no, 404."""
    challenge = get_object_or_404(Challenge.objects.with_counts().select_related("source_list__user", "genre", "platform"), pk=challenge_id)
    if not challenge.visible_to(request.user):
        raise Http404
    return challenge


def detail(request, challenge_id):
    """Página de un reto: qué hay que hacer y cuándo, quién lo ha creado, cómo vas tú (en los de juegos concretos, cuáles
    llevas) y la clasificación de quienes se han unido, de quien más lleva a quien menos.

    En la clasificación solo salen quienes tienen el perfil visible para quien mira; los que han llegado al objetivo
    quedan apuntados como que lo han conseguido (challenges.progress.mark_completed).
    """
    challenge = _visible(request, challenge_id)
    viewer = request.user
    participations = list(
        challenge.participations.filter(Profile.visible_filter(viewer))
        .select_related("user__profile", "user__psn_account", "user__xbox_account")
        .prefetch_related("user__steam_accounts")
    )
    done = progress.done_by(challenge, [participation.user_id for participation in participations])
    if progress.mark_completed(challenge, done):
        for participation in participations:
            participation.refresh_from_db(fields=["completed_at"])
    for participation in participations:
        participation.progress = progress.summary(challenge, done.get(participation.user_id, 0))
    participations.sort(key=lambda participation: (-done.get(participation.user_id, 0), participation.joined_at))
    mine = next((participation for participation in participations if participation.user_id == viewer.pk), None)
    if mine is None and viewer.is_authenticated:
        mine = challenge.participations.filter(user=viewer).first()
        if mine:
            mine.progress = progress.summary(challenge, progress.done_by(challenge, [viewer]).get(viewer.pk, 0))
    games = []
    if challenge.kind == Challenge.Kind.GAMES:
        finished = progress.finished_games(challenge, viewer) if viewer.is_authenticated else set()
        games = list(challenge.games.all())
        for game in games:
            game.finished = game.pk in finished
    source = challenge.source_list
    return render(request, "challenges/detail.html", {
        "challenge": challenge,
        "ranking": participations[:RANKING_SIZE],
        "ranking_count": len(participations),
        "mine": mine,
        "games": games,
        "is_creator": viewer == challenge.creator,
        # La lista de la que salen sus juegos, si quien mira puede verla.
        "source_list": source if source is not None and source.visible_to(viewer) else None,
    })


@login_required
def new(request):
    """Crea un reto (POST) o enseña el formulario (GET). Quien lo crea queda unido a él.

    Con lista= (desde «Convertir en reto» de una lista que puede ver) empieza como uno de juegos concretos con los de esa
    lista; si no, como uno de número de juegos.
    """
    raw = request.POST.get("lista") or request.GET.get("lista", "")
    game_list = None
    if raw.isdigit():
        game_list = GameList.objects.filter(pk=raw).select_related("user__profile").first()
        if game_list is not None and not game_list.visible_to(request.user):
            game_list = None
    initial = {"kind": Challenge.Kind.COUNT}
    if game_list is not None:
        initial = {"kind": Challenge.Kind.GAMES, "source_list": game_list.pk, "title": f"Pásate «{game_list.title}»"[:100]}
    form = ChallengeForm(request.POST or None, user=request.user, game_list=game_list, initial=initial)
    if request.method == "POST" and form.is_valid():
        challenge = form.save(request.user)
        Participation.objects.create(challenge=challenge, user=request.user)
        progress.mark_completed(challenge, progress.done_by(challenge, [request.user]))
        messages.success(request, "Reto creado: ya estás dentro. Comparte el enlace para que se una más gente.")
        return redirect(challenge)
    return render(request, "challenges/form.html", {"form": form, "game_list": game_list})


@login_required
def edit(request, challenge_id):
    """Cambia el título, la descripción o si es público un reto del usuario (sus reglas no se cambian); los de otros dan 404."""
    challenge = get_object_or_404(Challenge, pk=challenge_id, creator=request.user)
    form = ChallengeEditForm(request.POST or None, instance=Challenge.objects.get(pk=challenge.pk))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Reto guardado.")
        return redirect(challenge)
    return render(request, "challenges/edit.html", {"form": form, "challenge": challenge})


@require_POST
@login_required
def delete(request, challenge_id):
    """Borra un reto del usuario, con quienes se habían unido, y vuelve a la página de retos."""
    challenge = get_object_or_404(Challenge, pk=challenge_id, creator=request.user)
    challenge.delete()
    messages.success(request, f"Reto «{challenge.title}» borrado.")
    return redirect("challenges:explore")


@require_POST
@login_required
def join(request, challenge_id):
    """Une al usuario a un reto que puede ver (si ya estaba, no hace nada) y mira si ya lo ha conseguido con lo que
    lleva. A uno que ya ha terminado no se puede unir nadie (400)."""
    challenge = _visible(request, challenge_id)
    if challenge.is_over:
        return HttpResponseBadRequest("Este reto ya ha terminado.")
    _, created = Participation.objects.get_or_create(challenge=challenge, user=request.user)
    if created:
        progress.mark_completed(challenge, progress.done_by(challenge, [request.user]))
        messages.success(request, "¡Te has unido! Tu progreso se cuenta solo con tu biblioteca.")
    return redirect(challenge)


@require_POST
@login_required
def leave(request, challenge_id):
    """Saca al usuario de un reto en el que estaba (si no estaba, no hace nada) y vuelve a él si aún puede verlo."""
    challenge = get_object_or_404(Challenge, pk=challenge_id)
    challenge.participations.filter(user=request.user).delete()
    messages.success(request, "Has dejado el reto.")
    if challenge.visible_to(request.user):
        return redirect(challenge)
    return redirect("challenges:explore")
