from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from games.models import Game

from .models import LibraryEntry

User = get_user_model()


class LibraryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.other = User.objects.create_user("luis", "luis@example.com", "x")
        cls.game = Game.objects.create(name="Hades", slug="hades")
        cls.set_url = reverse("library:set_status", args=[cls.game.id])
        cls.remove_url = reverse("library:remove", args=[cls.game.id])

    def test_set_status_creates_then_updates_a_single_entry(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.post(self.set_url, {"status": "playing"}), self.game.get_absolute_url())
        self.client.post(self.set_url, {"status": "completed"})
        entry = LibraryEntry.objects.get(user=self.user)
        self.assertEqual(entry.status, LibraryEntry.Status.COMPLETED)

    def test_htmx_request_returns_the_widget_fragment(self):
        self.client.force_login(self.user)
        response = self.client.post(self.set_url, {"status": "playing"}, headers={"HX-Request": "true"})
        self.assertTemplateUsed(response, "library/_widget.html")
        self.assertTemplateNotUsed(response, "base.html")
        self.assertContains(response, "Quitar de la biblioteca")

    def test_invalid_status_is_rejected(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.set_url, {"status": "nope"}).status_code, 400)
        self.assertFalse(LibraryEntry.objects.exists())

    def test_anonymous_and_get_requests_change_nothing(self):
        self.assertIn(reverse("login"), self.client.post(self.set_url, {"status": "playing"})["Location"])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.set_url).status_code, 405)
        self.assertFalse(LibraryEntry.objects.exists())

    def test_remove_only_deletes_own_entry(self):
        LibraryEntry.objects.create(user=self.user, game=self.game)
        LibraryEntry.objects.create(user=self.other, game=self.game)
        self.client.force_login(self.user)
        self.client.post(self.remove_url)
        self.assertEqual(list(LibraryEntry.objects.values_list("user", flat=True)), [self.other.id])

    def test_database_rejects_duplicate_entries(self):
        LibraryEntry.objects.create(user=self.user, game=self.game)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LibraryEntry.objects.create(user=self.user, game=self.game)
