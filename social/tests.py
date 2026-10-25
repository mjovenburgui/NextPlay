import io
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import matching
from accounts.models import ExternalTitle, ImportedTitle, Notification
from games.models import Game
from library.models import LibraryEntry, Review

from . import activity
from .models import Activity, Follow

User = get_user_model()


class FollowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")

    def follow(self, username, seguir="1", htmx=False, **data):
        headers = {"HTTP_HX_REQUEST": "true"} if htmx else {}
        return self.client.post(reverse("social:follow", args=[username]), {"seguir": seguir, **data}, **headers)

    def test_follow_creates_follow_and_notifies(self):
        self.client.force_login(self.ana)
        response = self.follow("luis")
        self.assertRedirects(response, reverse("profile", args=["luis"]))
        self.assertTrue(Follow.objects.filter(follower=self.ana, followed=self.luis).exists())
        notification = Notification.objects.get(user=self.luis)
        self.assertEqual(notification.kind, Notification.Kind.FOLLOW)
        self.assertEqual(notification.actor, self.ana)
        self.assertEqual(notification.text, "ana ha empezado a seguirte.")
        self.assertEqual(notification.url, reverse("profile", args=["ana"]))

    def test_unfollow_and_follow_again_does_not_repeat_the_notification(self):
        self.client.force_login(self.ana)
        self.follow("luis")
        self.follow("luis", seguir="0")
        self.assertFalse(Follow.objects.exists())
        self.follow("luis")
        self.assertEqual(Follow.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(user=self.luis).count(), 1)

    def test_pressing_twice_does_not_undo(self):
        self.client.force_login(self.ana)
        self.follow("luis")
        self.follow("luis")
        self.assertEqual(Follow.objects.count(), 1)
        self.follow("luis", seguir="0")
        self.follow("luis", seguir="0")
        self.assertFalse(Follow.objects.exists())

    def test_cannot_follow_yourself(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.follow("ana").status_code, 400)
        self.assertFalse(Follow.objects.exists())

    def test_database_forbids_self_follows_and_duplicates(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Follow.objects.create(follower=self.ana, followed=self.ana)
        Follow.objects.create(follower=self.ana, followed=self.luis)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Follow.objects.create(follower=self.ana, followed=self.luis)

    def test_private_profile_cannot_be_followed_but_can_be_unfollowed(self):
        Follow.objects.create(follower=self.ana, followed=self.luis)
        self.luis.profile.is_public = False
        self.luis.profile.save()
        third = User.objects.create_user("eva", "eva@example.com", "x")
        self.client.force_login(third)
        self.assertEqual(self.follow("luis").status_code, 404)
        self.assertFalse(Follow.objects.filter(follower=third).exists())
        # Quien ya seguía a Luis antes de que se hiciera privado puede dejar de seguirlo.
        self.client.force_login(self.ana)
        self.follow("luis", seguir="0")
        self.assertFalse(Follow.objects.exists())

    def test_private_follower_notification_links_to_the_followers_list(self):
        self.ana.profile.is_public = False
        self.ana.profile.save()
        self.client.force_login(self.ana)
        self.follow("luis")
        notification = Notification.objects.get(user=self.luis)
        self.assertEqual(notification.url, reverse("social:followers", args=["luis"]))

    def test_anonymous_is_sent_to_log_in(self):
        response = self.follow("luis")
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('social:follow', args=['luis'])}")
        self.assertFalse(Follow.objects.exists())

    def test_get_is_not_allowed(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse("social:follow", args=["luis"])).status_code, 405)

    def test_goes_back_to_next_only_on_this_site(self):
        self.client.force_login(self.ana)
        back = reverse("social:followers", args=["luis"])
        self.assertRedirects(self.follow("luis", next=back), back)
        response = self.follow("luis", seguir="0", next="https://example.com/")
        self.assertRedirects(response, reverse("profile", args=["luis"]))

    def test_htmx_from_the_profile_returns_button_and_follower_count(self):
        self.client.force_login(self.ana)
        response = self.follow("luis", htmx=True, desde="perfil")
        self.assertContains(response, "Siguiendo")
        self.assertContains(response, 'name="seguir" value="0"')
        self.assertContains(response, 'id="follower-count"')
        self.assertContains(response, 'hx-swap-oob="true"')
        self.assertContains(response, "<strong>1</strong> seguidor<")
        response = self.follow("luis", seguir="0", htmx=True, desde="perfil")
        self.assertContains(response, 'name="seguir" value="1"')
        self.assertContains(response, "<strong>0</strong> seguidores")

    def test_htmx_from_a_list_returns_a_small_button_without_counter(self):
        Follow.objects.create(follower=self.luis, followed=self.ana)
        self.client.force_login(self.ana)
        response = self.follow("luis", seguir="0", htmx=True, desde="lista", next="/u/ana/seguidores/")
        self.assertContains(response, "btn-small")
        self.assertContains(response, "Seguir también")
        self.assertContains(response, 'value="/u/ana/seguidores/"')
        self.assertNotContains(response, "follower-count")


class ProfileFollowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.eva = User.objects.create_user("eva", "eva@example.com", "x")
        Follow.objects.create(follower=cls.luis, followed=cls.ana)
        Follow.objects.create(follower=cls.eva, followed=cls.ana)
        Follow.objects.create(follower=cls.ana, followed=cls.eva)

    def test_profile_shows_counts_and_follow_back(self):
        self.client.force_login(self.luis)
        response = self.client.get(reverse("profile", args=["eva"]))
        self.assertContains(response, "<strong>1</strong> seguidor<")
        self.assertContains(response, "<strong>1</strong> siguiendo")
        self.assertContains(response, 'name="seguir" value="1"')
        self.assertNotContains(response, "Te sigue")
        # Ana sigue a Eva, así que en el perfil de Ana a Eva le sale «Te sigue» y el botón «Siguiendo».
        self.client.force_login(self.eva)
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, "<strong>2</strong> seguidores")
        self.assertContains(response, "Te sigue")
        self.assertContains(response, "Siguiendo")

    def test_follows_you_without_following_back_offers_seguir_tambien(self):
        self.client.force_login(self.ana)
        response = self.client.get(reverse("profile", args=["luis"]))
        self.assertContains(response, "Te sigue")
        self.assertContains(response, "Seguir también")

    def test_owner_sees_no_follow_button(self):
        self.client.force_login(self.ana)
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, "<strong>2</strong> seguidores")
        self.assertNotContains(response, reverse("social:follow", args=["ana"]))

    def test_anonymous_gets_a_link_to_log_in(self):
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, f"{reverse('login')}?next=/u/ana/")
        self.assertNotContains(response, reverse("social:follow", args=["ana"]))

    def test_home_summary_shows_followers_and_following(self):
        self.client.force_login(self.ana)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "<strong>2</strong> seguidores")
        self.assertContains(response, "<strong>1</strong> siguiendo")

    def test_notifications_page_shows_the_follower(self):
        self.client.force_login(self.luis)
        self.client.post(reverse("social:follow", args=["eva"]), {"seguir": "1"})
        self.client.force_login(self.eva)
        response = self.client.get(reverse("notifications"))
        self.assertContains(response, "luis ha empezado a seguirte.")
        self.assertContains(response, 'href="/u/luis/"')


class PeopleListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.eva = User.objects.create_user("eva", "eva@example.com", "x")
        cls.eva.profile.bio = "Solo juego de noche."
        cls.eva.profile.save()
        Follow.objects.create(follower=cls.luis, followed=cls.ana)
        Follow.objects.create(follower=cls.eva, followed=cls.ana)
        Follow.objects.create(follower=cls.ana, followed=cls.luis)

    def test_followers_and_following_lists(self):
        response = self.client.get(reverse("social:followers", args=["ana"]))
        self.assertContains(response, 'href="/u/luis/"')
        self.assertContains(response, 'href="/u/eva/"')
        self.assertContains(response, "Solo juego de noche.")
        self.assertContains(response, "Seguidores <span>2</span>")
        self.assertContains(response, "Siguiendo <span>1</span>")
        response = self.client.get(reverse("social:following", args=["ana"]))
        self.assertContains(response, 'href="/u/luis/"')
        self.assertNotContains(response, 'href="/u/eva/"')

    def test_marks_who_follows_you_and_whom_you_follow(self):
        self.client.force_login(self.luis)
        response = self.client.get(reverse("social:followers", args=["ana"]))
        # Eva no sigue a Luis ni Luis a Eva: botón «Seguir». Luis no tiene botón para sí mismo.
        self.assertContains(response, reverse("social:follow", args=["eva"]))
        self.assertNotContains(response, reverse("social:follow", args=["luis"]))
        # Ana sigue a Luis (botón para dejar de seguir) y no a Eva («Seguir también»); los dos siguen a Ana.
        self.client.force_login(self.ana)
        response = self.client.get(reverse("social:followers", args=["ana"]))
        self.assertContains(response, "Te sigue", count=2)
        self.assertContains(response, 'name="seguir" value="0"', count=1)
        self.assertContains(response, "Seguir también", count=1)

    def test_private_people_are_listed_without_link_or_bio(self):
        self.eva.profile.is_public = False
        self.eva.profile.save()
        self.client.force_login(self.luis)
        response = self.client.get(reverse("social:followers", args=["ana"]))
        self.assertContains(response, "eva")
        self.assertContains(response, "Perfil privado")
        self.assertNotContains(response, 'href="/u/eva/"')
        self.assertNotContains(response, "Solo juego de noche.")
        self.assertNotContains(response, reverse("social:follow", args=["eva"]))

    def test_private_profile_lists_are_only_for_their_owner(self):
        self.ana.profile.is_public = False
        self.ana.profile.save()
        url = reverse("social:followers", args=["ana"])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.luis)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_empty_lists(self):
        User.objects.create_user("leo", "leo@example.com", "x")
        response = self.client.get(reverse("social:following", args=["leo"]))
        self.assertContains(response, "leo aún no sigue a nadie.")
        # Nadie sigue a Eva.
        self.client.force_login(self.eva)
        response = self.client.get(reverse("social:followers", args=["eva"]))
        self.assertContains(response, "Aún no te sigue nadie.")


class ActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.hades = Game.objects.create(name="Hades", slug="hades")
        cls.celeste = Game.objects.create(name="Celeste", slug="celeste")

    def setUp(self):
        self.client.force_login(self.ana)

    def set_status(self, game, status):
        return self.client.post(reverse("library:set_status", args=[game.pk]), {"status": status})

    def rate(self, game, rating):
        return self.client.post(reverse("library:rate", args=[game.pk]), {"rating": str(rating)})

    def publish(self, game, **fields):
        return self.client.post(reverse("library:review_new", args=[game.pk]), {"text": "Genial.", "publicar": "", **fields})

    def age(self):
        """Hace que toda la actividad sea de hace dos horas, para que lo siguiente ya no se junte con ella."""
        Activity.objects.update(updated_at=timezone.now() - timedelta(hours=2))

    def test_status_changes_are_recorded_and_joined(self):
        self.set_status(self.hades, "playing")
        self.set_status(self.hades, "playing")  # el mismo estado otra vez no cuenta
        self.assertEqual(Activity.objects.count(), 1)
        self.set_status(self.hades, "completed")
        item = Activity.objects.get()
        self.assertEqual((item.kind, item.game, item.status), ("game", self.hades, "completed"))
        self.assertEqual(item.summary, "terminó Hades")
        self.age()
        self.set_status(self.hades, "dropped")
        self.assertEqual(Activity.objects.count(), 2)

    def test_another_game_in_between_starts_a_new_one(self):
        self.set_status(self.hades, "playing")
        self.set_status(self.celeste, "backlog")
        self.rate(self.hades, 8)
        self.assertEqual(
            [item.summary for item in Activity.objects.all()],
            ["valoró Hades", "añadió Celeste a Pendiente", "empezó a jugar a Hades"],
        )

    def test_rating_joins_and_removing_it_right_away_undoes_it(self):
        self.set_status(self.hades, "completed")
        self.rate(self.hades, 9)
        item = Activity.objects.get()
        self.assertEqual((item.status, item.rating), ("completed", 9))
        self.rate(self.hades, 0)
        self.assertIsNone(Activity.objects.get().rating)
        # Una nota sola que se quita enseguida no deja nada.
        LibraryEntry.objects.create(user=self.ana, game=self.celeste)
        self.rate(self.celeste, 7)
        self.assertEqual(Activity.objects.count(), 2)
        self.rate(self.celeste, 0)
        self.assertEqual(Activity.objects.count(), 1)
        # Quitarla más tarde no cambia lo que ya pasó.
        self.rate(self.celeste, 6)
        self.age()
        self.rate(self.celeste, 0)
        self.assertEqual(Activity.objects.get(game=self.celeste).rating, 6)

    def test_publishing_a_review_joins_the_activity_and_editing_it_does_not_repeat_it(self):
        self.set_status(self.hades, "completed")
        self.publish(self.hades, rating="9")
        review = Review.objects.get()
        item = Activity.objects.get()
        self.assertEqual((item.status, item.rating, item.review), ("completed", 9, review))
        self.client.post(reverse("library:review_edit", args=[review.pk]), {"text": "Genial de verdad.", "publicar": ""})
        self.assertEqual(Activity.objects.count(), 1)

    def test_drafts_are_not_activity_until_published(self):
        LibraryEntry.objects.create(user=self.ana, game=self.hades)
        self.client.post(reverse("library:review_new", args=[self.hades.pk]), {"text": "A medias.", "borrador": ""})
        self.assertFalse(Activity.objects.exists())
        review = Review.objects.get()
        self.client.post(reverse("library:review_edit", args=[review.pk]), {"text": "Ya está.", "publicar": ""})
        self.assertEqual(Activity.objects.get().review, review)
        # Vuelta a borrador y publicada otra vez: sigue siendo la misma actividad.
        self.client.post(reverse("library:review_edit", args=[review.pk]), {"text": "Ya está.", "borrador": ""})
        self.client.post(reverse("library:review_edit", args=[review.pk]), {"text": "Ya está.", "publicar": ""})
        self.assertEqual(Activity.objects.count(), 1)

    def test_deleting_a_review_keeps_the_rest_of_its_activity(self):
        self.set_status(self.hades, "completed")
        self.publish(self.hades)
        self.age()
        self.publish(self.hades, text="Otra partida.")  # más tarde: su actividad es solo la reseña
        first, second = Review.objects.order_by("pk")
        self.assertEqual(Activity.objects.count(), 2)
        self.client.post(reverse("library:review_delete", args=[second.pk]))
        self.assertEqual(Activity.objects.count(), 1)
        self.client.post(reverse("library:review_delete", args=[first.pk]))
        item = Activity.objects.get()
        self.assertEqual((item.status, item.review), ("completed", None))

    def test_removing_a_game_forgets_its_activity_but_not_its_reviews(self):
        self.set_status(self.hades, "completed")
        self.publish(self.hades, rating="8")
        self.set_status(self.celeste, "playing")
        self.client.post(reverse("library:remove", args=[self.hades.pk]))
        self.client.post(reverse("library:remove", args=[self.celeste.pk]))
        item = Activity.objects.get()
        self.assertEqual((item.game, item.status, item.rating), (self.hades, "", None))
        self.assertEqual((item.summary, item.shown_rating), ("reseñó Hades", 8))

    def test_an_import_is_a_single_activity_with_the_most_played_games(self):
        for name, game, minutes in (("Celeste", self.celeste, 30), ("Hades", self.hades, 600)):
            title = ExternalTitle.objects.create(platform="steam", external_id=name, name=name, game=game)
            ImportedTitle.objects.create(user=self.ana, title=title, minutes=minutes)
        matching.update_library(self.ana, "steam", record_activity=True)
        item = Activity.objects.get()
        self.assertEqual(item.data, {"platform": "steam", "count": 2, "games": [self.hades.pk, self.celeste.pk]})
        self.assertEqual(item.summary, "importó 2 juegos de Steam")
        # Las correcciones del emparejamiento no la piden.
        hollow = Game.objects.create(name="Hollow Knight", slug="hollow-knight")
        title = ExternalTitle.objects.create(platform="steam", external_id="hk", name="Hollow Knight", game=hollow)
        ImportedTitle.objects.create(user=self.ana, title=title, minutes=60)
        self.assertEqual(matching.update_library(self.ana, "steam"), (1, 0))
        self.assertEqual(Activity.objects.get().data["count"], 2)

    def test_imports_right_after_add_up_per_platform(self):
        activity.games_imported(self.ana, "steam", [LibraryEntry(game=self.hades, steam_playtime_minutes=60)])
        activity.games_imported(self.ana, "steam", [LibraryEntry(game=self.celeste, steam_playtime_minutes=5)])
        self.assertEqual(Activity.objects.get().data["count"], 2)
        activity.games_imported(self.ana, "psn", [LibraryEntry(game=self.celeste, psn_playtime_minutes=5)])
        activity.games_imported(self.ana, "psn", [])
        self.assertEqual(Activity.objects.count(), 2)

    def test_status_changes_made_by_an_import_are_not_activity(self):
        entry = LibraryEntry.objects.create(user=self.ana, game=self.hades)
        entry.update_import("steam", 90, timezone.now())
        self.assertEqual(entry.status, "playing")
        self.assertFalse(Activity.objects.exists())


class ActivityTabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.hades = Game.objects.create(name="Hades", slug="hades")

    def url(self, **params):
        return f"{reverse('profile', args=['ana'])}?{urlencode({'vista': 'actividad', **params})}"

    def test_shows_what_was_done_with_the_review(self):
        review = Review.objects.create(user=self.ana, game=self.hades, rating=9, text="Una **joya**.", published_at=timezone.now())
        Activity.objects.create(user=self.ana, kind="game", game=self.hades, status="completed", rating=9, review=review)
        response = self.client.get(self.url())
        self.assertContains(response, "Terminó")
        self.assertContains(response, f'class="activity-game" href="{self.hades.get_absolute_url()}"')
        self.assertContains(response, "Una joya.")
        self.assertContains(response, review.get_absolute_url())

    def test_import_shows_how_many_and_their_covers(self):
        Activity.objects.create(user=self.ana, kind="import", data={"platform": "steam", "count": 129, "games": [self.hades.pk, 999]})
        response = self.client.get(self.url())
        self.assertContains(response, "<strong>129 juegos</strong> de Steam")
        self.assertContains(response, 'title="Hades"')

    def test_drafts_and_private_profiles_stay_hidden(self):
        review = Review.objects.create(user=self.ana, game=self.hades, text="Secreta.", published_at=timezone.now())
        Activity.objects.create(user=self.ana, kind="game", game=self.hades, review=review)
        Review.objects.filter(pk=review.pk).update(is_draft=True)
        response = self.client.get(self.url())
        self.assertNotContains(response, "Secreta.")
        self.assertContains(response, "ana aún no tiene actividad.")
        self.ana.profile.is_public = False
        self.ana.profile.save()
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        more = self.client.get(reverse("social:activity", args=["ana"]), HTTP_HX_REQUEST="true")
        self.assertEqual(more.status_code, 404)

    def test_load_more_brings_the_rest_without_repeating(self):
        now = timezone.now()
        # Diez a la misma hora, para comprobar que el cursor también desempata por id.
        for minutes in [0] * 10 + list(range(1, 16)):
            Activity.objects.create(
                user=self.ana, kind="game", game=self.hades, status="playing", updated_at=now - timedelta(minutes=minutes)
            )
        response = self.client.get(self.url())
        first = [item.pk for item in response.context["activities"]]
        self.assertEqual(len(first), 20)
        cursor = response.context["next_cursor"]
        self.assertContains(response, "Cargar más")
        more = self.client.get(reverse("social:activity", args=["ana"]), {"antes": cursor}, HTTP_HX_REQUEST="true")
        rest = [item.pk for item in more.context["activities"]]
        self.assertEqual((len(rest), more.context["next_cursor"]), (5, ""))
        self.assertNotContains(more, "Cargar más")
        self.assertEqual(sorted(first + rest), sorted(Activity.objects.values_list("pk", flat=True)))
        # Sin HTMX lleva a la pestaña desde ese punto; un cursor roto empieza por el principio.
        self.assertRedirects(self.client.get(reverse("social:activity", args=["ana"]), {"antes": cursor}), self.url(antes=cursor))
        self.assertEqual(len(self.client.get(self.url(antes=cursor)).context["activities"]), 5)
        self.assertEqual(len(self.client.get(self.url(antes="nada")).context["activities"]), 20)


@override_settings(DEBUG=True)
class SeedTests(TestCase):
    def test_seed_builds_activity_and_follows_and_can_run_twice(self):
        for n in range(8):
            Game.objects.create(name=f"Juego {n}", slug=f"juego-{n}", cover_image_id=f"co{n}")
        for name in ("demo1", "demo2", "demo3"):
            User.objects.create_user(name, f"{name}@example.com", "x")
        for _ in range(2):
            call_command("seed_test_profile", stdout=io.StringIO())
        tester = User.objects.get(username="tester")
        self.assertEqual(tester.activities.filter(kind="import").count(), 1)
        self.assertEqual(tester.activities.filter(kind="game").count(), 8)
        self.assertEqual(tester.activities.exclude(review=None).count(), 4)
        self.assertEqual((tester.followers.count(), tester.following.count()), (3, 2))
        self.assertEqual(Notification.objects.filter(user=tester, kind="follow").count(), 3)
        self.assertEqual(Activity.objects.filter(user__username="demo2").count(), 1)
