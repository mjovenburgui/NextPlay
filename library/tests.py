import io
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Notification
from games.models import Game, GamePrice

from . import wishlist
from .models import LibraryEntry, WishlistItem

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

    def test_rate_sets_and_clears_the_rating(self):
        LibraryEntry.objects.create(user=self.user, game=self.game)
        self.client.force_login(self.user)
        rate_url = reverse("library:rate", args=[self.game.id])
        self.assertRedirects(self.client.post(rate_url, {"rating": "4"}), self.game.get_absolute_url())
        self.assertEqual(LibraryEntry.objects.get().rating, 4)
        self.client.post(rate_url, {"rating": "0"})
        self.assertIsNone(LibraryEntry.objects.get().rating)

    def test_rate_rejects_out_of_range_values(self):
        LibraryEntry.objects.create(user=self.user, game=self.game, rating=3)
        self.client.force_login(self.user)
        rate_url = reverse("library:rate", args=[self.game.id])
        for value in ("6", "-1", "abc", ""):
            self.assertEqual(self.client.post(rate_url, {"rating": value}).status_code, 400)
        self.assertEqual(LibraryEntry.objects.get().rating, 3)

    def test_rate_needs_the_game_in_the_library(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("library:rate", args=[self.game.id]), {"rating": "5"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(LibraryEntry.objects.exists())

    def test_htmx_rating_also_refreshes_the_community_box(self):
        LibraryEntry.objects.create(user=self.other, game=self.game, rating=2)
        LibraryEntry.objects.create(user=self.user, game=self.game)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("library:rate", args=[self.game.id]), {"rating": "5"}, headers={"HX-Request": "true"}
        )
        self.assertContains(response, 'id="community-rating" hx-swap-oob="true"')
        self.assertContains(response, "3,5")
        self.assertContains(response, "2 valoraciones")

    def test_community_rating_averages_only_rated_entries(self):
        LibraryEntry.objects.create(user=self.user, game=self.game, rating=5)
        LibraryEntry.objects.create(user=self.other, game=self.game, rating=None)
        self.assertEqual(self.game.community_rating(), {"average": 5.0, "count": 1})
        response = self.client.get(self.game.get_absolute_url())
        self.assertContains(response, "1 valoración de jugadores")
        self.assertContains(response, "width: 100%")

    def test_game_page_without_ratings(self):
        response = self.client.get(self.game.get_absolute_url())
        self.assertContains(response, "Aún nadie lo ha valorado")
        self.assertContains(response, "width: 0%")

    def test_database_rejects_duplicate_entries(self):
        LibraryEntry.objects.create(user=self.user, game=self.game)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LibraryEntry.objects.create(user=self.user, game=self.game)


class ReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.other = User.objects.create_user("luis", "luis@example.com", "x")
        cls.game = Game.objects.create(name="Hades", slug="hades")
        cls.url = reverse("library:review", args=[cls.game.id])

    def test_write_edit_and_delete_review(self):
        LibraryEntry.objects.create(user=self.user, game=self.game)
        self.client.force_login(self.user)
        response = self.client.post(self.url, {"review": "  Adictivo.  ", "review_has_spoilers": "on"})
        self.assertRedirects(response, f"{self.game.get_absolute_url()}#resenas", fetch_redirect_response=False)
        entry = LibraryEntry.objects.get()
        self.assertEqual((entry.review, entry.review_has_spoilers), ("Adictivo.", True))
        self.assertIsNotNone(entry.reviewed_at)
        self.client.post(self.url, {"review": "   "})
        entry.refresh_from_db()
        self.assertEqual((entry.review, entry.review_has_spoilers, entry.reviewed_at), ("", False, None))

    def test_review_needs_the_game_in_the_library(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.url, {"review": "Hola"}).status_code, 404)

    def test_too_long_review_is_rejected(self):
        LibraryEntry.objects.create(user=self.user, game=self.game)
        self.client.force_login(self.user)
        self.client.post(self.url, {"review": "x" * 3001})
        self.assertEqual(LibraryEntry.objects.get().review, "")

    def test_game_page_hides_reviews_from_private_profiles(self):
        LibraryEntry.objects.create(user=self.user, game=self.game, review="Pública", reviewed_at="2026-01-01T10:00Z")
        LibraryEntry.objects.create(user=self.other, game=self.game, review="Secreta", reviewed_at="2026-01-02T10:00Z")
        self.other.profile.is_public = False
        self.other.profile.save()
        response = self.client.get(self.game.get_absolute_url())
        self.assertContains(response, "Pública")
        self.assertNotContains(response, "Secreta")
        self.client.force_login(self.other)
        response = self.client.get(self.game.get_absolute_url())
        self.assertContains(response, "Secreta")
        self.assertContains(response, "Tu reseña")

    def test_htmx_add_to_library_refreshes_the_review_section(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("library:set_status", args=[self.game.id]), {"status": "playing"}, headers={"HX-Request": "true"}
        )
        self.assertContains(response, 'id="resenas" hx-swap-oob="true"')
        self.assertContains(response, "Publicar reseña")


class WishlistTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.game = Game.objects.create(name="Hades", slug="hades", historical_low=Decimal("6.24"))
        cls.other_game = Game.objects.create(name="Celeste", slug="celeste")

    def setUp(self):
        self.client.force_login(self.user)
        self.price = GamePrice.objects.create(
            game=self.game, shop_id=61, shop_name="Steam", price=Decimal("24.99"), regular_price=Decimal("24.99"),
            currency="EUR", url="https://itad.link/steam",
        )

    def set_price(self, price, cut):
        """Cambia el precio de hoy de Hades en Steam."""
        GamePrice.objects.filter(pk=self.price.pk).update(price=Decimal(price), cut=cut)

    def test_toggle_and_target_from_the_game_page(self):
        response = self.client.get(self.game.get_absolute_url())
        self.assertContains(response, "Lo quiero")
        response = self.client.post(reverse("library:wish_toggle", args=[self.game.id]), HTTP_HX_REQUEST="true")
        self.assertContains(response, "En tu lista de deseos")
        response = self.client.post(
            reverse("library:wish_target", args=[self.game.id]), {"target_price": "14,5"}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(response, "cuando baje de 14,50 €")
        self.assertEqual(WishlistItem.objects.get().target_price, Decimal("14.50"))
        response = self.client.post(
            reverse("library:wish_target", args=[self.game.id]), {"target_price": "barato"}, HTTP_HX_REQUEST="true"
        )
        self.assertContains(response, "Escribe un precio")
        self.client.post(reverse("library:wish_toggle", args=[self.game.id]))
        self.assertFalse(WishlistItem.objects.exists())

    def test_alert_when_below_target_only_once_until_it_drops_again(self):
        WishlistItem.objects.create(user=self.user, game=self.game, target_price=Decimal("15"))
        self.assertEqual(wishlist.check_alerts(), 0)
        self.set_price("12.49", 50)
        self.assertEqual(wishlist.check_alerts(), 1)
        self.assertEqual(wishlist.check_alerts(), 0)  # mismo precio: no se repite
        self.set_price("9.99", 60)
        self.assertEqual(wishlist.check_alerts(), 1)  # ha bajado más
        self.set_price("24.99", 0)
        wishlist.check_alerts()
        self.set_price("12.49", 50)
        self.assertEqual(wishlist.check_alerts(), 1)  # subió y volvió a bajar
        notification = Notification.objects.filter(user=self.user).first()
        self.assertEqual(notification.text, "Hades ha bajado a 12,49 € en Steam (tu límite: 15,00 €).")
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn("http://localhost:8000/juegos/hades/", mail.outbox[0].body)

    def test_alert_at_historical_low_needs_a_sale(self):
        WishlistItem.objects.create(user=self.user, game=self.game)
        self.set_price("6.24", 0)  # nunca rebajado: no avisa
        self.assertEqual(wishlist.check_alerts(), 0)
        self.set_price("6.24", 75)
        self.assertEqual(wishlist.check_alerts(), 1)
        self.assertIn("su mínimo histórico", Notification.objects.get().text)

    def test_no_alert_for_owned_games_and_no_email_when_disabled(self):
        WishlistItem.objects.create(user=self.user, game=self.game, target_price=Decimal("30"))
        LibraryEntry.objects.create(user=self.user, game=self.game)
        self.assertEqual(wishlist.check_alerts(), 0)
        LibraryEntry.objects.all().delete()
        self.user.profile.price_alert_emails = False
        self.user.profile.save()
        self.assertEqual(wishlist.check_alerts(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_update_prices_command_checks_alerts(self):
        WishlistItem.objects.create(user=self.user, game=self.game, target_price=Decimal("30"))
        with override_settings(ITAD_API_KEY=""), mock.patch("games.xbox.update_xbox_prices", return_value=0):
            out = io.StringIO()
            call_command("update_prices", stdout=out, stderr=io.StringIO())
        self.assertIn("Avisos de la lista de deseos: 1", out.getvalue())

    def test_wishlist_page_sorts_by_discount_and_marks_targets(self):
        WishlistItem.objects.create(user=self.user, game=self.other_game)
        WishlistItem.objects.create(user=self.user, game=self.game, target_price=Decimal("15"))
        self.set_price("12.49", 50)
        response = self.client.get(reverse("library:wishlist"), {"orden": "descuento"})
        names = [row["wish"].game.name for row in response.context["rows"]]
        self.assertEqual(names, ["Hades", "Celeste"])
        self.assertContains(response, "Por debajo de tu límite")
        self.assertContains(response, "Sin precio")

    def test_bell_counts_unread_and_the_page_marks_them_read(self):
        Notification.objects.create(user=self.user, kind="price", text="Hades ha bajado", url="/juegos/hades/")
        self.assertContains(self.client.get(reverse("library:wishlist")), '<span class="bell-count">1</span>')
        response = self.client.get(reverse("notifications"))
        self.assertContains(response, "Nuevo")
        self.assertNotContains(self.client.get(reverse("notifications")), "Nuevo")
        self.assertNotContains(self.client.get(reverse("library:wishlist")), "bell-count")

    def test_pages_need_login(self):
        self.client.logout()
        for url in (reverse("library:wishlist"), reverse("notifications")):
            self.assertEqual(self.client.get(url).status_code, 302)
