from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.urls import reverse


class User(AbstractUser):
    # Custom from day one: swapping the user model after the first migration is painful.
    email = models.EmailField("correo electrónico", unique=True)


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    display_name = models.CharField("nombre visible", max_length=50, blank=True)
    bio = models.TextField("biografía", max_length=300, blank=True)
    is_public = models.BooleanField(
        "perfil público",
        default=True,
        help_text="Si lo desactivas, solo tú podrás ver tu perfil y tu biblioteca.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        """Texto del perfil en el admin y en la consola: su nombre visible."""
        return self.name

    @property
    def name(self):
        """Nombre que se muestra: el nombre visible si lo tiene, si no el nombre de usuario."""
        return self.display_name or self.user.username

    def get_absolute_url(self):
        """URL del perfil público del usuario (/u/<usuario>/)."""
        return reverse("profile", args=[self.user.username])

    def visible_to(self, viewer):
        """Indica si viewer puede ver el perfil: siempre si es público, y si es privado solo su dueño."""
        return self.is_public or viewer == self.user
