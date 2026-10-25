from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import Notification

from .models import Follow

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
