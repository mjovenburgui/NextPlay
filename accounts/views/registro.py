"""Vista de Registro (cuenta/registro/): el formulario para crear una cuenta."""

from django.contrib import messages
from django.contrib.auth import login
from django.shortcuts import redirect, render

from ..forms import SignupForm


def signup(request):
    """Registro de cuenta nueva.

    Si el usuario ya ha iniciado sesión lo manda a su perfil. Al registrarse bien inicia su sesión y lo lleva a
    Inicio, que con la biblioteca vacía empieza por la tarjeta «Trae tus juegos»: dónde juega y cómo traer sus juegos,
    con sus horas, de cada sitio.

    Pantallas: Registro, Inicio (tarjeta «Trae tus juegos»).
    """
    if request.user.is_authenticated:
        return redirect("profile", username=request.user.username)
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Cuenta creada. Lo primero: dinos dónde juegas y trae tus juegos con sus horas.")
        return redirect("home")
    return render(request, "registration/signup.html", {"form": form})
