from django import forms

from .models import REVIEW_MAX_LENGTH, LibraryEntry


class ReviewForm(forms.ModelForm):
    class Meta:
        model = LibraryEntry
        fields = ("review", "review_has_spoilers")
        labels = {"review": "Tu reseña"}
        widgets = {
            "review": forms.Textarea(
                attrs={"rows": 5, "placeholder": "¿Qué te ha parecido? Cuéntaselo a otros jugadores…"}
            )
        }
        help_texts = {"review": f"Hasta {REVIEW_MAX_LENGTH} caracteres. Déjala vacía y guarda para borrarla."}

    def clean_review(self):
        """Quita los espacios del principio y del final para que una reseña en blanco cuente como vacía."""
        return self.cleaned_data["review"].strip()
