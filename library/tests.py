import io
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import ExternalTitle, ImportedTitle, Notification
from games import prices
from games.models import STEAM_DECK_SLUG, Game, GamePrice, Platform

from . import markup, reminders, wishlist
from .models import LibraryEntry, Report, Review, ReviewComment, ReviewImage, ReviewLike, WishlistItem

User = get_user_model()


def image_upload(name="captura.png", size=(2400, 1200), fmt="PNG"):
    """Imagen de prueba lista para subir."""
    buffer = io.BytesIO()
    Image.new("RGB", size, (30, 90, 200)).save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


# Carpeta temporal para las imágenes que suben los tests (se borra al terminar la clase).
TEST_MEDIA = tempfile.mkdtemp(prefix="nextplay-resenas-")


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
        for value in ("11", "-1", "abc", ""):
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
        # 10 medias estrellas son cinco estrellas.
        LibraryEntry.objects.create(user=self.user, game=self.game, rating=10)
        LibraryEntry.objects.create(user=self.other, game=self.game, rating=None)
        self.assertEqual(
            self.game.community_rating(), {"average": 5.0, "count": 1, "verified_average": None, "verified_count": 0}
        )
        response = self.client.get(self.game.get_absolute_url())
        self.assertContains(response, "1 valoración de jugadores")
        self.assertContains(response, "width: 100%")
        self.assertNotContains(response, "Con horas jugadas")

    def test_community_rating_with_played_hours_only(self):
        # Solo cuentan quienes tienen horas importadas (más de 0 minutos en alguna plataforma).
        LibraryEntry.objects.create(user=self.user, game=self.game, rating=10, steam_playtime_minutes=600)
        LibraryEntry.objects.create(user=self.other, game=self.game, rating=2, psn_playtime_minutes=0)
        third = User.objects.create_user("eva", "eva@example.com", "x")
        LibraryEntry.objects.create(user=third, game=self.game, rating=6, xbox_playtime_minutes=90)
        rating = self.game.community_rating()
        self.assertEqual((rating["count"], rating["verified_count"], rating["verified_average"]), (3, 2, 4.0))
        response = self.client.get(self.game.get_absolute_url())
        self.assertContains(response, "Con horas jugadas: <strong>4,0</strong>")
        self.assertContains(response, "2 valoraciones")

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
        cls.third = User.objects.create_user("eva", "eva@example.com", "x")
        cls.platform = Platform.objects.create(name="PlayStation 5", abbreviation="PS5", slug="ps5")
        cls.game = Game.objects.create(name="Hades", slug="hades")
        cls.game.platforms.add(cls.platform)
        cls.new_url = reverse("library:review_new", args=[cls.game.id])

    def publish(self, user, text="Muy bueno.", **fields):
        """Reseña publicada de user sobre el juego (sin pasar por el editor)."""
        return Review.objects.create(user=user, game=self.game, text=text, published_at=timezone.now(), **fields)

    def test_publishing_saves_hours_status_and_the_library_rating(self):
        LibraryEntry.objects.create(
            user=self.user, game=self.game, status=LibraryEntry.Status.COMPLETED, rating=6,
            steam_playtime_minutes=750, psn_playtime_minutes=120,
        )
        self.client.force_login(self.user)
        response = self.client.get(self.new_url)
        self.assertContains(response, 'id="nota-6" value="6" checked')  # empieza con la nota que ya tenía
        response = self.client.post(self.new_url, {
            "rating": "9", "text": "  **Genial**.  ", "platform": self.platform.pk, "finished_on": "2026-01-02",
            "publicar": "",
        })
        review = Review.objects.get()
        self.assertRedirects(response, review.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(review.get_absolute_url(), f"/u/ana/resenas/{review.pk}/")
        self.assertEqual((review.text, review.rating, review.platform, review.is_draft), ("**Genial**.", 9, self.platform, False))
        self.assertEqual((review.status, review.hours, review.hours_sources), ("completed", Decimal("14.5"), "steam,psn"))
        self.assertEqual(review.hours_source_names, "Steam y PlayStation")
        self.assertIsNotNone(review.published_at)
        self.assertEqual(LibraryEntry.objects.get().rating, 9)  # la nota de la reseña pasa a la biblioteca
        # Una segunda reseña del mismo juego empieza marcada como rejugada.
        self.assertContains(self.client.get(self.new_url), 'name="is_replay" id="id_is_replay" checked')

    def test_drafts_stay_private_until_published(self):
        LibraryEntry.objects.create(user=self.user, game=self.game)
        self.client.force_login(self.user)
        response = self.client.post(self.new_url, {"text": "Aún lo estoy pensando.", "borrador": ""})
        review = Review.objects.get()
        self.assertRedirects(response, reverse("library:review_edit", args=[review.pk]), fetch_redirect_response=False)
        self.assertEqual((review.is_draft, review.published_at), (True, None))
        self.assertContains(self.client.get(self.game.get_absolute_url()), "Borrador")
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(self.game.get_absolute_url()), "Aún lo estoy pensando.")
        self.assertEqual(self.client.get(f"/u/ana/resenas/{review.pk}/").status_code, 404)
        self.client.force_login(self.user)
        self.client.post(reverse("library:review_edit", args=[review.pk]), {"text": "Ya está.", "publicar": ""})
        review.refresh_from_db()
        self.assertFalse(review.is_draft)
        self.assertIsNotNone(review.published_at)

    def test_review_form_validation(self):
        LibraryEntry.objects.create(user=self.user, game=self.game)
        self.client.force_login(self.user)
        other_platform = Platform.objects.create(name="Wii U", slug="wiiu")
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        cases = [
            ({"text": "   "}, "text"),
            ({"text": "x" * 8001}, "text"),
            ({"text": "Bien.", "rating": "11"}, "rating"),
            ({"text": "Bien.", "platform": other_platform.pk}, "platform"),
            ({"text": "Bien.", "started_on": "2026-02-01", "finished_on": "2026-01-01"}, "finished_on"),
            ({"text": "Bien.", "finished_on": tomorrow}, "finished_on"),
        ]
        for data, field in cases:
            response = self.client.post(self.new_url, {**data, "publicar": ""})
            self.assertIn(field, response.context["form"].errors, data)
        self.assertFalse(Review.objects.exists())

    def test_review_needs_the_game_in_the_library_and_only_its_author_edits_it(self):
        self.client.force_login(self.user)
        response = self.client.get(self.new_url)
        self.assertRedirects(response, self.game.get_absolute_url(), fetch_redirect_response=False)
        review = self.publish(self.other)
        self.assertEqual(self.client.get(reverse("library:review_edit", args=[review.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("library:review_delete", args=[review.pk])).status_code, 404)
        self.client.force_login(self.other)
        response = self.client.post(reverse("library:review_delete", args=[review.pk]))
        self.assertRedirects(response, f"{self.game.get_absolute_url()}#resenas", fetch_redirect_response=False)
        self.assertFalse(Review.objects.exists())

    def test_game_page_hides_reviews_from_private_profiles(self):
        self.publish(self.user, "Pública")
        self.publish(self.other, "Secreta")
        self.other.profile.is_public = False
        self.other.profile.save()
        response = self.client.get(self.game.get_absolute_url())
        self.assertContains(response, "Pública")
        self.assertNotContains(response, "Secreta")
        self.assertEqual(response.context["review_count"], 1)
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
        self.assertContains(response, "Escribir una reseña")

    def test_likes_toggle_and_notify_the_author_once(self):
        review = self.publish(self.user)
        url = reverse("library:review_like", args=[review.pk])
        self.client.force_login(self.other)
        response = self.client.post(url, headers={"HX-Request": "true"})
        self.assertContains(response, 'aria-pressed="true"')
        self.assertEqual(review.likes.count(), 1)
        self.client.post(url)  # lo quita
        self.client.post(url)  # y lo vuelve a dar
        self.assertEqual(review.likes.count(), 1)
        notices = Notification.objects.filter(user=self.user, kind=Notification.Kind.LIKE)
        self.assertEqual([notice.text for notice in notices], ["A luis le ha gustado tu reseña de Hades."])
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url).status_code, 400)  # la propia no

    def test_comments_notify_and_only_writer_or_author_delete_them(self):
        review = self.publish(self.user)
        self.client.force_login(self.other)
        response = self.client.post(
            reverse("library:review_comment", args=[review.pk]), {"text": "  ¡Muy *buena*!  "}, headers={"HX-Request": "true"}
        )
        self.assertContains(response, "<em>buena</em>")
        comment = review.comments.get()
        self.assertEqual(comment.text, "¡Muy *buena*!")
        self.assertTrue(Notification.objects.filter(user=self.user, kind=Notification.Kind.COMMENT).exists())
        delete_url = reverse("library:comment_delete", args=[comment.pk])
        self.client.force_login(self.third)
        self.assertEqual(self.client.post(delete_url).status_code, 403)
        self.client.force_login(self.user)  # el autor de la reseña sí puede
        self.client.post(delete_url, headers={"HX-Request": "true"})
        self.assertFalse(review.comments.exists())
        response = self.client.post(reverse("library:review_comment", args=[review.pk]), {"text": "   "})
        self.assertFalse(review.comments.exists())

    def test_review_page_renders_safe_formatting(self):
        text = (
            "<script>alert(1)</script> **negrita** y *cursiva*\n\n||el final|| [web](https://example.com/a?b=1&c=2) "
            "[mal](javascript:alert(1))"
        )
        review = self.publish(self.user, text)
        response = self.client.get(review.get_absolute_url())
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertContains(response, "<strong>negrita</strong> y <em>cursiva</em>")
        self.assertContains(response, 'class="spoiler-inline"')
        self.assertContains(response, '<a href="https://example.com/a?b=1&amp;c=2" rel="nofollow ugc noopener"')
        self.assertNotContains(response, 'href="javascript:')
        self.assertContains(response, "[spoiler]")  # en la descripción de la página, tapado

    def test_markup_plain_and_paragraphs(self):
        self.assertEqual(
            str(markup.render("Uno\ndos\n\n**tres**")), "<p>Uno<br>dos</p><p><strong>tres</strong></p>"
        )
        self.assertEqual(markup.plain("**Ojo**: ||muere|| al *final* [aquí](https://x.es)"), "Ojo: [spoiler] al final aquí")
        self.assertEqual(str(markup.render("2 * 3 * 4")), "<p>2 * 3 * 4</p>")  # sin palabras pegadas no es cursiva

    def test_sort_by_likes_and_filters(self):
        old = self.publish(self.user, "La terminé.", status=LibraryEntry.Status.COMPLETED, hours=Decimal("30"))
        old.published_at = timezone.now() - timedelta(days=2)
        old.save()
        liked = self.publish(self.other, "Gustada.", status=LibraryEntry.Status.PLAYING, hours=Decimal("3"))
        ReviewLike.objects.create(user=self.third, review=old)
        response = self.client.get(self.game.get_absolute_url())
        self.assertEqual(response.context["reviews"], [liked, old])
        response = self.client.get(self.game.get_absolute_url(), {"resenas": "gustadas"})
        self.assertEqual(response.context["reviews"], [old, liked])
        for chosen in ("terminado", "horas"):
            response = self.client.get(self.game.get_absolute_url(), {"filtro": chosen})
            self.assertEqual(response.context["reviews"], [old])
        self.assertContains(response, "?filtro=horas#resenas")

    def test_reviews_far_ahead_of_your_progress_are_folded(self):
        self.publish(self.other, "Lo que pasa al final.", hours=Decimal("40"))
        entry = LibraryEntry.objects.create(user=self.user, game=self.game, steam_playtime_minutes=120)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(self.game.get_absolute_url()), "puede contar lo que te queda")
        entry.status = LibraryEntry.Status.COMPLETED
        entry.save()
        self.assertNotContains(self.client.get(self.game.get_absolute_url()), "puede contar lo que te queda")

    def test_preview_renders_the_formatting(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("library:review_preview"), {"text": "**hola**"})
        self.assertContains(response, "<strong>hola</strong>")

    def test_best_and_worst_are_saved_clean_and_shown(self):
        LibraryEntry.objects.create(user=self.user, game=self.game)
        self.client.force_login(self.user)
        response = self.client.post(self.new_url, {
            "text": "Muy bueno.", "pros_0": "  La banda   sonora ", "pros_1": "la banda sonora", "pros_2": "",
            "cons_0": "Los **tiempos** de carga", "publicar": "",
        })
        review = Review.objects.get()
        self.assertEqual((review.pros, review.cons), (["La banda sonora"], ["Los **tiempos** de carga"]))
        response = self.client.get(review.get_absolute_url())
        self.assertContains(response, "Lo mejor")
        self.assertContains(response, "Los <strong>tiempos</strong> de carga")
        # Al editarla, cada punto vuelve a su casilla.
        response = self.client.get(reverse("library:review_edit", args=[review.pk]))
        self.assertContains(response, 'name="pros_0" id="id_pros_0" value="La banda sonora"')
        response = self.client.post(reverse("library:review_edit", args=[review.pk]), {"text": "Bien.", "pros_0": "x" * 91})
        self.assertIn("pros", response.context["form"].errors)

    def test_what_reviews_agree_on_and_suggestions(self):
        self.publish(self.other, pros=["Banda sonora", "Historia"], cons=["Corto"])
        self.publish(self.third, pros=["¡banda sonora!"], cons=["corto."])
        private = User.objects.create_user("max", "max@example.com", "x")
        private.profile.is_public = False
        private.profile.save()
        self.publish(private, pros=["Historia"])
        response = self.client.get(self.game.get_absolute_url())
        self.assertEqual(response.context["consensus"], {"pros": [("Banda sonora", 2)], "cons": [("Corto", 2)]})
        self.assertContains(response, "Lo que más destacan")
        # El editor sugiere lo que han escrito otros (no lo propio ni lo de perfiles privados).
        LibraryEntry.objects.create(user=self.third, game=self.game)
        self.client.force_login(self.third)
        response = self.client.get(self.new_url)
        self.assertEqual(response.context["suggestions"], {"pros": ["Banda sonora", "Historia"], "cons": ["Corto"]})
        self.assertContains(response, '<datalist id="lo-mejor-sugerido"><option value="Banda sonora">')

    def test_platform_and_dates_fill_themselves_from_imports(self):
        pc = Platform.objects.create(name="PC (Microsoft Windows)", abbreviation="PC", slug="win")
        self.game.platforms.add(pc)
        entry = LibraryEntry.objects.create(user=self.user, game=self.game, status=LibraryEntry.Status.COMPLETED)
        when = timezone.make_aware(datetime(2026, 1, 5, 20, 0))
        psn_title = ExternalTitle.objects.create(platform="psn", external_id="CUSA1", name="Hades", game=self.game)
        steam_title = ExternalTitle.objects.create(platform="steam", external_id="1145360", name="Hades", game=self.game)
        ImportedTitle.objects.create(
            user=self.user, title=psn_title, minutes=600, platforms=["PlayStation 5"],
            first_played=when, last_played=when + timedelta(days=30),
        )
        # Importado antes de que se guardaran las plataformas: lo de Steam cuenta como PC.
        ImportedTitle.objects.create(user=self.user, title=steam_title, minutes=120, last_played=when + timedelta(days=40))
        self.client.force_login(self.user)
        response = self.client.get(self.new_url)
        form = response.context["form"]
        self.assertEqual(form["platform"].value(), self.platform.pk)  # PS5: donde más ha jugado
        self.assertEqual(form["started_on"].value(), date(2026, 1, 5))
        self.assertEqual(form["finished_on"].value(), date(2026, 2, 14))  # su última partida, porque lo completó
        self.assertContains(response, "Has jugado en PS5 (10 h) y PC (2 h): va la de más horas.")
        self.assertContains(response, "Tu primera partida en PlayStation.")
        # Sin completarlo no se inventa la fecha de fin, y una reseña ya escrita no se toca.
        entry.status = LibraryEntry.Status.PLAYING
        entry.save()
        self.assertIsNone(self.client.get(self.new_url).context["form"]["finished_on"].value())
        review = self.publish(self.user)
        self.assertIsNone(self.client.get(reverse("library:review_edit", args=[review.pk])).context["form"]["platform"].value())

    def test_playstation_console_comes_from_the_title_id_of_older_imports(self):
        ps4 = Platform.objects.create(name="PlayStation 4", abbreviation="PS4", slug="ps4")
        self.game.platforms.add(ps4)
        LibraryEntry.objects.create(user=self.user, game=self.game)
        title = ExternalTitle.objects.create(platform="psn", external_id="CUSA03173_00", name="Hades", game=self.game)
        ImportedTitle.objects.create(user=self.user, title=title, minutes=300)  # sin plataformas guardadas
        self.client.force_login(self.user)
        response = self.client.get(self.new_url)
        self.assertEqual(response.context["form"]["platform"].value(), ps4.pk)
        self.assertContains(response, "De tu cuenta de PlayStation.")

    def test_steam_deck_is_offered_in_pc_games_and_chosen_when_most_hours_were_there(self):
        deck = Platform.objects.get(slug=STEAM_DECK_SLUG)
        self.assertNotIn(deck, self.game.review_platforms())  # solo sale en PS5
        pc = Platform.objects.create(name="PC (Microsoft Windows)", abbreviation="PC", slug="win")
        self.game.platforms.add(pc)
        self.assertIn(deck, self.game.review_platforms())
        LibraryEntry.objects.create(user=self.user, game=self.game)
        title = ExternalTitle.objects.create(platform="steam", external_id="1145360", name="Hades", game=self.game)
        ImportedTitle.objects.create(
            user=self.user, title=title, minutes=600, deck_minutes=480, platforms=["PC (Microsoft Windows)"]
        )
        self.client.force_login(self.user)
        response = self.client.get(self.new_url)
        self.assertEqual(response.context["form"]["platform"].value(), deck.pk)
        self.assertContains(response, "Has jugado en PC (2 h) y Steam Deck (8 h): va la de más horas.")
        self.client.post(self.new_url, {"text": "En la Deck va de lujo.", "platform": deck.pk, "publicar": ""})
        review = Review.objects.get()
        self.assertEqual(review.platform, deck)
        self.assertContains(self.client.get(review.get_absolute_url()), '<li class="fact">Steam Deck</li>')

    def test_hours_badge_shows_the_platform_logos(self):
        review = self.publish(self.user, hours=Decimal("14.5"), hours_sources="steam,psn")
        response = self.client.get(review.get_absolute_url())
        self.assertContains(response, "Escrita a las 14,5 h")
        self.assertContains(response, '<span class="fact-logo" role="img" aria-label="Steam">')
        self.assertContains(response, '<span class="fact-logo" role="img" aria-label="PlayStation">')

    def test_authors_who_played_much_more_are_asked_to_update_their_latest_review(self):
        # Se propone con el doble de horas que al escribirla y al menos 10 más.
        self.assertIsNone(Review(hours=Decimal("30")).hours_since(LibraryEntry(steam_playtime_minutes=55 * 60)))
        self.assertEqual(Review(hours=Decimal("30")).hours_since(LibraryEntry(steam_playtime_minutes=60 * 60)), 30)
        self.assertIsNone(Review(hours=Decimal("1")).hours_since(LibraryEntry(steam_playtime_minutes=10 * 60)))
        self.assertIsNone(Review(hours=None).hours_since(LibraryEntry(steam_playtime_minutes=60 * 60)))
        self.assertIsNone(Review(hours=Decimal("1"), is_draft=True).hours_since(LibraryEntry(steam_playtime_minutes=60 * 60)))

        LibraryEntry.objects.create(user=self.user, game=self.game, steam_playtime_minutes=50 * 60)
        old = self.publish(self.user, "Primera partida.", hours=Decimal("2"))
        old.published_at = timezone.now() - timedelta(days=30)
        old.save()
        latest = self.publish(self.user, "Rejugada.", hours=Decimal("5"), is_replay=True)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(self.game.get_absolute_url()), "Llevas 45 h más desde que la escribiste", count=1)
        self.assertContains(self.client.get(latest.get_absolute_url()), "Llevas 45 h más que cuando la escribiste")
        self.assertNotContains(self.client.get(old.get_absolute_url()), "Llevas")  # la de otra partida, no
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(latest.get_absolute_url()), "Llevas")
        # En la campana, un aviso por reseña hasta que la vuelva a guardar.
        self.assertEqual(reminders.remind_outdated_reviews(self.user), 1)
        self.assertEqual(reminders.remind_outdated_reviews(self.user), 0)
        notice = Notification.objects.get(kind=Notification.Kind.REVIEW_UPDATE)
        self.assertEqual(notice.text, "Llevas 45 h más en Hades que cuando escribiste tu reseña: ¿la actualizas?")
        self.assertEqual(notice.url, reverse("library:review_edit", args=[latest.pk]))
        # Al actualizarla se guardan las horas de ahora y ya no se propone.
        self.client.force_login(self.user)
        self.client.post(reverse("library:review_edit", args=[latest.pk]), {"text": "Rejugada, ya terminada.", "publicar": ""})
        latest.refresh_from_db()
        self.assertEqual(latest.hours, Decimal("50.0"))
        self.assertNotContains(self.client.get(latest.get_absolute_url()), "Llevas")


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class ReviewImageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.game = Game.objects.create(name="Hades", slug="hades", media={
            "shots": [{"id": "sc1", "w": 1920, "h": 1080}, {"id": "sc2", "w": 1920, "h": 1080}],
            "arts": [{"id": "ar1", "w": 1000, "h": 1500, "type": 1}],
        })
        LibraryEntry.objects.create(user=cls.user, game=cls.game)
        cls.new_url = reverse("library:review_new", args=[cls.game.id])

    @classmethod
    def tearDownClass(cls):
        """Borra las imágenes que han subido los tests."""
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA, ignore_errors=True)

    def setUp(self):
        self.client.force_login(self.user)

    def review_with_images(self, count):
        """Reseña publicada de ana con count imágenes oficiales («x0», «x1»...)."""
        review = Review.objects.create(user=self.user, game=self.game, text="Hola.", published_at=timezone.now())
        for position in range(count):
            ReviewImage.objects.create(review=review, igdb_image_id=f"x{position}", position=position)
        return review

    def test_editor_offers_the_official_images(self):
        response = self.client.get(self.new_url)
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, 'name="captura" value="sc1"')
        self.assertContains(response, 'name="captura" value="ar1"')
        self.assertContains(response, 'name="imagenes"')

    def test_uploads_are_converted_with_a_thumbnail_and_official_images_need_no_upload(self):
        response = self.client.post(self.new_url, {
            "text": "Mira esto.", "imagenes": [image_upload("a.png"), image_upload("b.jpg", fmt="JPEG")],
            "spoiler_nueva": ["1"], "captura": ["sc2"], "spoiler_captura": ["sc2"], "publicar": "",
        })
        review = Review.objects.get()
        self.assertRedirects(response, review.get_absolute_url(), fetch_redirect_response=False)
        first, second, official = review.images.all()
        self.assertTrue(first.image.name.startswith(f"resenas/{self.user.pk}/review-"))
        self.assertTrue(first.thumb.name.startswith(f"resenas/{self.user.pk}/review_thumb-"))
        with Image.open(first.image.path) as big, Image.open(first.thumb.path) as small:
            self.assertEqual((big.format, big.size, small.size), ("WEBP", (1920, 960), (720, 360)))
        self.assertEqual([image.is_spoiler for image in (first, second, official)], [False, True, True])
        self.assertEqual((official.igdb_image_id, official.image.name), ("sc2", ""))
        self.assertIn("t_1080p/sc2.jpg", official.url)
        response = self.client.get(review.get_absolute_url())
        self.assertContains(response, 'class="gallery-tile', count=3)
        self.assertContains(response, 'class="gallery-tile is-spoiler"', count=2)
        self.assertContains(response, "Imagen oficial del juego · IGDB")
        # También en la ficha y en la portada.
        self.assertContains(self.client.get(self.game.get_absolute_url()), first.thumb.url)
        self.assertContains(self.client.get(reverse("home")), first.thumb.url)
        # Al editarla ya no se ofrece la oficial que tiene.
        response = self.client.get(reverse("library:review_edit", args=[review.pk]))
        self.assertNotContains(response, 'name="captura" value="sc2"')
        self.assertContains(response, f'name="spoiler" value="{official.pk}" checked')

    def test_no_more_than_four_and_bad_files_are_explained(self):
        review = self.review_with_images(3)
        edit_url = reverse("library:review_edit", args=[review.pk])
        response = self.client.post(edit_url, {"text": "Hola.", "captura": ["sc1", "sc2"], "publicar": ""})
        self.assertIn("Caben 4 imágenes por reseña", str(response.context["images_form"].non_field_errors()))
        response = self.client.post(edit_url, {"text": "Hola.", "captura": ["inventada"], "publicar": ""})
        self.assertIn("captura", response.context["images_form"].errors)
        response = self.client.post(edit_url, {
            "text": "Hola.", "imagenes": [SimpleUploadedFile("roto.png", b"no soy una imagen", "image/png")], "publicar": "",
        })
        self.assertIn("«roto.png»", str(response.context["images_form"].errors["imagenes"]))
        self.assertContains(response, "Vuelve a elegir las imágenes nuevas")
        self.assertEqual(review.images.count(), 3)
        # Quitando una ya caben dos más.
        first = review.images.first()
        self.client.post(edit_url, {"text": "Hola.", "quitar": [str(first.pk)], "captura": ["sc1", "sc2"], "publicar": ""})
        self.assertEqual(list(review.images.values_list("igdb_image_id", flat=True)), ["x1", "x2", "sc1", "sc2"])
        self.assertContains(self.client.get(edit_url), "Ya tiene 4 imágenes")

    def test_removed_images_and_deleted_reviews_take_their_files_with_them(self):
        self.client.post(
            self.new_url, {"text": "Hola.", "imagenes": [image_upload("a.png"), image_upload("b.png")], "publicar": ""}
        )
        review = Review.objects.get()
        kept, removed = review.images.all()
        files = {image.pk: [image.image.path, image.thumb.path] for image in (kept, removed)}
        self.assertTrue(all(os.path.exists(path) for paths in files.values() for path in paths))
        self.client.post(reverse("library:review_edit", args=[review.pk]), {
            "text": "Hola.", "quitar": [str(removed.pk)], "spoiler": [str(kept.pk)], "publicar": "",
        })
        self.assertEqual([(image.pk, image.is_spoiler, image.position) for image in review.images.all()], [(kept.pk, True, 0)])
        self.assertFalse(any(os.path.exists(path) for path in files[removed.pk]))
        self.client.post(reverse("library:review_delete", args=[review.pk]))
        self.assertFalse(any(os.path.exists(path) for path in files[kept.pk]))

    def test_drafts_keep_their_images_private(self):
        self.client.post(self.new_url, {"text": "Aún no.", "captura": ["sc1"], "borrador": ""})
        review = Review.objects.get()
        self.assertEqual(review.images.count(), 1)
        self.client.logout()
        self.assertNotContains(self.client.get(self.game.get_absolute_url()), 'class="gallery-tile')


class ReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user("ana", "ana@example.com", "x")
        cls.reader = User.objects.create_user("luis", "luis@example.com", "x")
        cls.admin = User.objects.create_user("jefa", "jefa@example.com", "x", is_staff=True, is_superuser=True)
        cls.game = Game.objects.create(name="Hades", slug="hades")
        cls.review = Review.objects.create(user=cls.author, game=cls.game, text="Muy bueno.", published_at=timezone.now())
        cls.url = reverse("library:review_report", args=[cls.review.pk])

    def test_reporting_a_review_tells_the_admins_once_and_can_be_changed(self):
        self.client.force_login(self.reader)
        response = self.client.post(
            self.url, {"reason": "spam", "detail": "  Anuncia una web  "}, headers={"HX-Request": "true"}
        )
        self.assertContains(response, "Gracias: lo revisaremos")
        report = Report.objects.get()
        self.assertEqual(
            (report.target, report.reporter, report.reason, report.detail, report.target_kind),
            (self.review, self.reader, "spam", "Anuncia una web", "reseña"),
        )
        notice = Notification.objects.get(user=self.admin)
        self.assertEqual(notice.text, "Han denunciado una reseña de ana en Hades: Spam o publicidad.")
        self.assertEqual(notice.url, reverse("admin:library_report_change", args=[report.pk]))
        self.assertFalse(Notification.objects.filter(user__in=[self.author, self.reader]).exists())
        # Volver a denunciarla cambia el motivo y la deja pendiente, sin repetir el aviso que aún no se ha leído.
        Report.objects.update(resolved_at=timezone.now())
        self.assertRedirects(self.client.post(self.url, {"reason": "abuse"}), self.review.get_absolute_url())
        report.refresh_from_db()
        self.assertEqual((report.reason, report.resolved_at), ("abuse", None))
        self.assertEqual(Notification.objects.filter(user=self.admin).count(), 1)
        self.assertContains(self.client.get(self.review.get_absolute_url()), "Ya lo has denunciado")

    def test_needs_a_reason_and_your_own_cannot_be_reported(self):
        self.client.force_login(self.reader)
        response = self.client.post(self.url, {"reason": "nada"}, headers={"HX-Request": "true"})
        self.assertContains(response, "Elige un motivo.")
        self.assertContains(response, f'<details class="report" id="denunciar-resena-{self.review.pk}" open>')
        self.assertFalse(Report.objects.exists())
        self.client.force_login(self.author)
        self.assertEqual(self.client.post(self.url, {"reason": "spam"}).status_code, 400)
        self.assertNotContains(self.client.get(self.review.get_absolute_url()), "Denunciar")
        self.client.logout()
        self.assertIn(reverse("login"), self.client.post(self.url, {"reason": "spam"})["Location"])

    def test_lists_link_to_the_review_page_with_the_form_open(self):
        self.client.force_login(self.reader)
        link = f"{self.review.get_absolute_url()}?denunciar#denunciar-resena-{self.review.pk}"
        self.assertContains(self.client.get(self.game.get_absolute_url()), link)
        response = self.client.get(f"{self.review.get_absolute_url()}?denunciar")
        self.assertContains(response, f'<details class="report" id="denunciar-resena-{self.review.pk}" open>')
        self.client.force_login(self.author)
        self.assertNotContains(self.client.get(self.game.get_absolute_url()), link)

    def test_comments_can_be_reported_but_not_in_private_reviews(self):
        comment = ReviewComment.objects.create(review=self.review, user=self.author, text="¡Gracias!")
        url = reverse("library:comment_report", args=[comment.pk])
        self.client.force_login(self.reader)
        self.assertContains(self.client.get(self.review.get_absolute_url()), f'id="denunciar-comentario-{comment.pk}"')
        self.client.post(url, {"reason": "abuse"})
        report = Report.objects.get()
        self.assertEqual((report.target, report.target_kind), (comment, "comentario"))
        self.assertEqual(
            Notification.objects.get(user=self.admin).text, "Han denunciado un comentario de ana en Hades: Insultos, acoso u odio."
        )
        self.client.force_login(self.author)
        self.assertEqual(self.client.post(url, {"reason": "spam"}).status_code, 400)  # el propio no
        self.author.profile.is_public = False
        self.author.profile.save()
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post(url, {"reason": "spam"}).status_code, 404)
        self.assertEqual(self.client.post(self.url, {"reason": "spam"}).status_code, 404)

    def test_admin_lists_pending_reports_and_marks_them_reviewed(self):
        Report.objects.create(target=self.review, reporter=self.reader, reason=Report.Reason.IMAGE)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin:library_report_changelist"), {"estado": "pendientes"})
        self.assertContains(response, self.review.get_absolute_url())
        self.assertContains(response, "Imagen inapropiada")
        reviews = self.client.get(reverse("admin:library_review_changelist"))
        self.assertContains(reviews, '<td class="field-pending_reports">1</td>')
        self.client.post(reverse("admin:library_report_changelist"), {
            "action": "mark_resolved", "_selected_action": [Report.objects.get().pk],
        })
        self.assertIsNotNone(Report.objects.get().resolved_at)
        self.assertContains(self.client.get(reverse("admin:library_review_change", args=[self.review.pk])), "Imagen inapropiada")
        # Al borrar la reseña se van sus denuncias.
        self.review.delete()
        self.assertFalse(Report.objects.exists())


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

    def test_only_the_users_shops_count(self):
        WishlistItem.objects.create(user=self.user, game=self.game, target_price=Decimal("15"))
        self.set_price("12.49", 50)  # rebajado en Steam, que no es de sus tiendas
        self.user.profile.shops = [35]
        self.user.profile.save()
        self.assertEqual(wishlist.check_alerts(), 0)
        self.assertContains(self.client.get(reverse("library:wishlist")), "No está en tus tiendas")
        self.user.profile.shops = [61, 35]
        self.user.profile.save()
        self.assertEqual(wishlist.check_alerts(), 1)

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

    def test_games_from_the_steam_wishlist_are_marked_and_sorted_like_on_steam(self):
        third = Game.objects.create(name="Celeste 2", slug="celeste-2")
        now = timezone.now()
        WishlistItem.objects.create(user=self.user, game=self.game, from_steam=True, steam_priority=2, steam_added_at=now)
        WishlistItem.objects.create(user=self.user, game=self.other_game, from_steam=True, steam_priority=1, steam_added_at=now)
        WishlistItem.objects.create(user=self.user, game=third)
        response = self.client.get(reverse("library:wishlist"), {"orden": "steam"})
        self.assertEqual([row["wish"].game.name for row in response.context["rows"]], ["Celeste", "Hades", "Celeste 2"])
        self.assertContains(response, "Como en tu lista de Steam")
        self.assertContains(response, 'class="pill pill-steam"', count=2)
        # Sin nada de Steam no se ofrece ese orden.
        WishlistItem.objects.filter(steam_added_at__isnull=False).delete()
        self.assertNotContains(self.client.get(reverse("library:wishlist")), "Como en tu lista de Steam")

    def test_prices_of_games_from_steam_are_fetched_at_once(self):
        Game.objects.filter(pk=self.game.pk).update(prices_updated_at=timezone.now())
        self.game.refresh_from_db()
        with override_settings(ITAD_API_KEY="key"), mock.patch("games.prices.update_prices", return_value=1) as update:
            self.assertEqual(wishlist.fetch_missing_prices([self.game, self.other_game]), 1)
        update.assert_called_once_with([self.other_game])  # el que ya tiene precios no se vuelve a pedir
        with override_settings(ITAD_API_KEY="key"), mock.patch("games.prices.update_prices", side_effect=prices.ITADError("caído")), \
                self.assertLogs("library.wishlist", "ERROR"):
            self.assertEqual(wishlist.fetch_missing_prices([self.other_game]), 0)
        with override_settings(ITAD_API_KEY=""), mock.patch("games.prices.update_prices") as update:
            wishlist.fetch_missing_prices([self.other_game])
        update.assert_not_called()

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
