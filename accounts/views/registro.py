"""Vista de Registro (cuenta/registro/): el formulario para crear una cuenta."""

from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect, render

from ..forms import SignupForm


def signup(request):
    """Registro de cuenta nueva.

    Si el usuario ya ha iniciado sesión lo manda a su perfil. Al registrarse bien inicia su sesión y lo lleva a
    Configurar NextPlay: dónde juega, cómo traer sus juegos con sus horas y de qué tiendas quiere precios.

    Pantallas: Registro, Configurar NextPlay.
    """
    if request.user.is_authenticated:
        return redirect("profile", username=request.user.username)
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Cuenta creada. Antes de empezar, deja NextPlay a tu gusto: son tres pasos.")
        return redirect("setup")
    return render(request, "registration/signup.html", {"form": form})
