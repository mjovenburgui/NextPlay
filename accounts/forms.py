from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import PlatformAccount, Profile

User = get_user_model()


class SignupForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_email(self):
        """Pasa el correo a minúsculas y rechaza el registro si ya hay una cuenta con ese correo."""
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Ya existe una cuenta con este correo.")
        return email


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ("display_name", "bio", "is_public")
        widgets = {"bio": forms.Textarea(attrs={"rows": 4})}


class PlatformAccountForm(forms.Form):
    platform = forms.ChoiceField(label="Plataforma", choices=PlatformAccount.Platform.choices)
    handle = forms.CharField(label="Nombre o código", max_length=40)

    def clean(self):
        """Normaliza el nombre o código y comprueba que tiene el formato de la plataforma elegida."""
        data = super().clean()
        platform, handle = data.get("platform"), data.get("handle")
        if platform and handle is not None:
            handle = PlatformAccount.normalize(platform, handle)
            if not PlatformAccount.is_valid_handle(platform, handle):
                label, help_text = PlatformAccount.RULES[platform][:2]
                raise forms.ValidationError(f"{label} no válido. {help_text}")
            data["handle"] = handle
        return data
