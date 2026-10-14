from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from games.models import Game
from library.models import LibraryEntry

User = get_user_model()


class SignupTests(TestCase):
    def test_signup_creates_user_with_profile_and_logs_in(self):
        response = self.client.post(
            reverse("signup"),
            {"username": "ana", "email": "Ana@example.com", "password1": "s3gura-clave-larga", "password2": "s3gura-clave-larga"},
        )
        self.assertRedirects(response, reverse("games:catalog"))
        user = User.objects.get(username="ana")
        self.assertEqual(user.email, "ana@example.com")
        self.assertTrue(user.profile.is_public)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_signup_rejects_duplicate_email_ignoring_case(self):
        User.objects.create_user("ana", "ana@example.com", "x")
        response = self.client.post(
            reverse("signup"),
            {"username": "otra", "email": "ANA@example.com", "password1": "s3gura-clave-larga", "password2": "s3gura-clave-larga"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="otra").exists())


class ProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("ana", "ana@example.com", "x")
        cls.other = User.objects.create_user("luis", "luis@example.com", "x")
        cls.playing = Game.objects.create(name="Hades", slug="hades")
        cls.done = Game.objects.create(name="Celeste", slug="celeste")
        LibraryEntry.objects.create(user=cls.owner, game=cls.playing, status=LibraryEntry.Status.PLAYING)
        LibraryEntry.objects.create(user=cls.owner, game=cls.done, status=LibraryEntry.Status.COMPLETED)

    def test_public_profile_shows_library_to_anonymous(self):
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, "Hades")
        self.assertContains(response, "Celeste")
        self.assertNotContains(response, "Editar perfil")

    def test_status_filter(self):
        response = self.client.get(reverse("profile", args=["ana"]), {"estado": "completed"})
        self.assertContains(response, "Celeste")
        self.assertNotContains(response, "Hades")

    def test_private_profile_is_hidden_from_others_but_not_owner(self):
        self.owner.profile.is_public = False
        self.owner.profile.save()
        url = reverse("profile", args=["ana"])

        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_edit_profile_only_changes_own_profile(self):
        self.client.force_login(self.other)
        self.client.post(reverse("edit_profile"), {"display_name": "Luis G.", "bio": "Hola", "is_public": "on"})
        self.other.profile.refresh_from_db()
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.other.profile.display_name, "Luis G.")
        self.assertEqual(self.owner.profile.display_name, "")

    def test_edit_profile_requires_login(self):
        response = self.client.get(reverse("edit_profile"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('edit_profile')}")
