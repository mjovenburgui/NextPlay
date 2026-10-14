from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from library.models import LibraryEntry

from .forms import ProfileForm, SignupForm

User = get_user_model()


def signup(request):
    if request.user.is_authenticated:
        return redirect("profile", username=request.user.username)
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Cuenta creada. Añade tus primeros juegos a la biblioteca.")
        return redirect("games:catalog")
    return render(request, "registration/signup.html", {"form": form})


def profile(request, username):
    owner = get_object_or_404(User.objects.select_related("profile"), username=username)
    # 404 rather than 403 so private profiles don't confirm that the account exists.
    if not owner.profile.visible_to(request.user):
        raise Http404

    entries = list(
        LibraryEntry.objects.filter(user=owner).select_related("game").order_by("-updated_at")
    )
    counts = {value: 0 for value, _ in LibraryEntry.Status.choices}
    for entry in entries:
        counts[entry.status] += 1

    status = request.GET.get("estado")
    if status in counts:
        shown = [e for e in entries if e.status == status]
    else:
        status = ""
        shown = entries

    tabs = [(value, label, counts[value]) for value, label in LibraryEntry.Status.choices]
    return render(
        request,
        "accounts/profile.html",
        {
            "owner": owner,
            "entries": shown,
            "total": len(entries),
            "tabs": tabs,
            "active_status": status,
            "is_owner": request.user == owner,
        },
    )


@login_required
def edit_profile(request):
    form = ProfileForm(request.POST or None, instance=request.user.profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Perfil actualizado.")
        return redirect("profile", username=request.user.username)
    return render(request, "accounts/edit_profile.html", {"form": form})
