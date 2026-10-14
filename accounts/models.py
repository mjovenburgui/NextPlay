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
        return self.name

    @property
    def name(self):
        return self.display_name or self.user.username

    def get_absolute_url(self):
        return reverse("profile", args=[self.user.username])

    def visible_to(self, viewer):
        return self.is_public or viewer == self.user
