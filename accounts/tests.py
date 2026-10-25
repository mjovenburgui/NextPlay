import io
import json
import shutil
import tempfile
import time
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from games import igdb
from games.models import Game, GamePrice, Platform
from library.models import LibraryEntry, Review, WishlistItem

from . import images, matching, psn, steam, views, xbox
from .models import (
    ExternalTitle, FavoriteGame, ImportedTitle, Notification, PlatformAccount, Profile, PSNAccount, SteamAccount,
    XboxAccount,
)

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

    def test_profile_stats_and_sort(self):
        # Las notas van en medias estrellas: 9 = 4,5 estrellas y 4 = 2 estrellas.
        LibraryEntry.objects.filter(game=self.done).update(rating=9, steam_playtime_minutes=600)
        LibraryEntry.objects.filter(game=self.playing).update(rating=4, steam_playtime_minutes=30)
        response = self.client.get(reverse("profile", args=["ana"]))
        stats = response.context["stats"]
        self.assertEqual((stats["games"], stats["completed"], stats["hours"]), (2, 1, 10))
        self.assertEqual(stats["average_rating"], 3.25)
        response = self.client.get(reverse("profile", args=["ana"]), {"orden": "nombre"})
        self.assertEqual([e.game.name for e in response.context["entries"]], ["Celeste", "Hades"])
        response = self.client.get(reverse("profile", args=["ana"]), {"orden": "horas"})
        self.assertEqual([e.game.name for e in response.context["entries"]], ["Celeste", "Hades"])

    def test_reviews_tab_lists_published_reviews_and_your_drafts(self):
        Review.objects.create(user=self.owner, game=self.done, text="Preciosa.", published_at=timezone.now())
        Review.objects.create(user=self.owner, game=self.playing, text="A medias.", is_draft=True)
        response = self.client.get(reverse("profile", args=["ana"]), {"vista": "resenas"})
        self.assertContains(response, "Preciosa.")
        self.assertEqual([review.game.name for review in response.context["reviews"]], ["Celeste"])
        self.assertEqual(response.context["stats"]["reviews"], 1)
        self.assertNotContains(response, "Tus borradores")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("profile", args=["ana"]), {"vista": "resenas"})
        self.assertContains(response, "Tus borradores")
        self.assertEqual([draft.game.name for draft in response.context["drafts"]], ["Hades"])

    def test_edit_profile_requires_login(self):
        response = self.client.get(reverse("edit_profile"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('edit_profile')}")


class VisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.public = User.objects.create_user("ana", "ana@example.com", "x")
        cls.private = User.objects.create_user("luis", "luis@example.com", "x")
        cls.private.profile.is_public = False
        cls.private.profile.save()

    def test_visible_filter_says_the_same_as_visible_to(self):
        game = Game.objects.create(name="Hades", slug="hades")
        for user in (self.public, self.private):
            Review.objects.create(user=user, game=game, text="Bien.", published_at=timezone.now())
        for viewer in (AnonymousUser(), self.public, self.private):
            expected = {user.pk for user in User.objects.all() if user.profile.visible_to(viewer)}
            users = User.objects.filter(Profile.visible_filter(viewer, path=""))
            self.assertEqual(set(users.values_list("pk", flat=True)), expected)
            reviews = Review.objects.filter(Profile.visible_filter(viewer))
            self.assertEqual(set(reviews.values_list("user_id", flat=True)), expected)
        # El dueño del perfil privado se ve a sí mismo; los demás, solo el público.
        self.assertEqual(expected, {self.public.pk, self.private.pk})


def openid_params(steam_id="76561197960287930", state="abc"):
    """Respuesta de OpenID de Steam tal como llega a la URL de vuelta."""
    return {
        "state": state,
        "openid.ns": steam.OPENID_NS,
        "openid.mode": "id_res",
        "openid.op_endpoint": steam.OPENID_URL,
        "openid.claimed_id": f"https://steamcommunity.com/openid/id/{steam_id}",
        "openid.identity": f"https://steamcommunity.com/openid/id/{steam_id}",
        "openid.return_to": f"http://testserver{reverse('steam_callback')}?state={state}",
        "openid.response_nonce": "2026-10-17T10:00:00Zxyz",
        "openid.assoc_handle": "1234567890",
        "openid.signed": "signed,op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle",
        "openid.sig": "abc=",
    }


@override_settings(STEAM_API_KEY="key")
class SteamLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")

    def setUp(self):
        self.client.force_login(self.user)
        # Al vincular se trae también la lista de deseos: aquí, vacía (sin llamar a Steam).
        patcher = mock.patch.object(steam, "wishlist", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def start_login(self):
        """Pulsa «Vincular Steam» y devuelve el código anti-CSRF que se guardó en la sesión."""
        response = self.client.post(reverse("steam_link"))
        self.assertTrue(response["Location"].startswith(steam.OPENID_URL))
        return self.client.session["steam_openid_state"]

    @mock.patch.object(
        steam, "import_library", return_value={"added": 3, "updated": 0, "unmatched": 1, "private": []}
    )
    @mock.patch.object(
        steam, "player_summary",
        return_value={"persona": "Gabe", "avatar": "https://a/x.jpg", "profile_url": "https://s/gabe"},
    )
    @mock.patch("accounts.steam.requests.post")
    def test_callback_links_account_and_imports(self, post, summary, import_library):
        post.return_value = mock.Mock(text="ns:http://specs.openid.net/auth/2.0\nis_valid:true\n")
        state = self.start_login()
        response = self.client.get(reverse("steam_callback"), openid_params(state=state))
        self.assertRedirects(response, reverse("profile", args=["ana"]))
        account = self.user.steam_accounts.get()
        self.assertEqual((account.steam_id, account.persona), ("76561197960287930", "Gabe"))
        self.assertEqual(post.call_args.kwargs["data"]["openid.mode"], "check_authentication")
        import_library.assert_called_once()

    @mock.patch.object(steam, "import_library", return_value={"added": 0, "updated": 0, "unmatched": 0, "private": []})
    @mock.patch.object(steam, "player_summary", return_value={})
    @mock.patch("accounts.steam.requests.post")
    def test_a_second_account_is_added_next_to_the_first(self, post, summary, import_library):
        SteamAccount.objects.create(user=self.user, steam_id="76561190000000001", persona="Principal")
        post.return_value = mock.Mock(text="is_valid:true\n")
        state = self.start_login()
        self.client.get(reverse("steam_callback"), openid_params(state=state))
        self.assertEqual(
            list(self.user.steam_accounts.values_list("steam_id", flat=True)),
            ["76561190000000001", "76561197960287930"],
        )
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, "Principal")
        self.assertContains(response, "76561197960287930")

    @mock.patch("accounts.steam.requests.post")
    def test_linking_an_account_already_linked_only_warns(self, post):
        SteamAccount.objects.create(user=self.user, steam_id="76561197960287930", persona="Gabe")
        post.return_value = mock.Mock(text="is_valid:true\n")
        state = self.start_login()
        response = self.client.get(reverse("steam_callback"), openid_params(state=state), follow=True)
        self.assertContains(response, "ya estaba vinculada")
        self.assertEqual(self.user.steam_accounts.count(), 1)

    def test_no_more_than_the_maximum_accounts(self):
        for n in range(SteamAccount.MAX_PER_USER):
            SteamAccount.objects.create(user=self.user, steam_id=f"7656119000000000{n}")
        response = self.client.post(reverse("steam_link"))
        self.assertRedirects(response, reverse("edit_profile"))
        self.assertNotIn("steam_openid_state", self.client.session)

    @mock.patch("accounts.steam.requests.post")
    def test_callback_rejects_what_steam_does_not_confirm(self, post):
        post.return_value = mock.Mock(text="ns:http://specs.openid.net/auth/2.0\nis_valid:false\n")
        state = self.start_login()
        self.client.get(reverse("steam_callback"), openid_params(state=state))
        self.assertFalse(SteamAccount.objects.exists())

    @mock.patch("accounts.steam.requests.post")
    def test_callback_rejects_wrong_state_and_foreign_return_to(self, post):
        self.start_login()
        self.client.get(reverse("steam_callback"), openid_params(state="otro"))
        state = self.start_login()
        self.client.get(
            reverse("steam_callback"), {**openid_params(state=state), "openid.return_to": "https://evil.example/"}
        )
        post.assert_not_called()
        self.assertFalse(SteamAccount.objects.exists())

    @mock.patch("accounts.steam.requests.post")
    def test_steam_account_cannot_be_linked_to_two_users(self, post):
        other = User.objects.create_user("luis", "luis@example.com", "x")
        SteamAccount.objects.create(user=other, steam_id="76561197960287930")
        post.return_value = mock.Mock(text="is_valid:true\n")
        state = self.start_login()
        self.client.get(reverse("steam_callback"), openid_params(state=state))
        self.assertFalse(self.user.steam_accounts.exists())
        self.assertEqual(SteamAccount.objects.get().user, other)

    def test_unlink_one_account_keeps_the_rest_and_the_games(self):
        game = Game.objects.create(name="Portal 2", slug="portal-2")
        LibraryEntry.objects.create(user=self.user, game=game, steam_playtime_minutes=120)
        first = SteamAccount.objects.create(user=self.user, steam_id="76561190000000001")
        second = SteamAccount.objects.create(user=self.user, steam_id="76561190000000002")
        self.client.post(reverse("steam_unlink", args=[first.id]))
        self.assertEqual(list(self.user.steam_accounts.all()), [second])
        self.assertTrue(LibraryEntry.objects.filter(user=self.user).exists())

    def test_cannot_unlink_someone_elses_account(self):
        other = User.objects.create_user("luis", "luis@example.com", "x")
        account = SteamAccount.objects.create(user=other, steam_id="76561190000000001")
        self.assertEqual(self.client.post(reverse("steam_unlink", args=[account.id])).status_code, 404)
        self.assertTrue(SteamAccount.objects.filter(pk=account.pk).exists())


@override_settings(STEAM_API_KEY="key", IGDB_CLIENT_ID="", IGDB_CLIENT_SECRET="")
class SteamImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.account = SteamAccount.objects.create(user=cls.user, steam_id="76561197960287930", persona="Ana")
        cls.portal = Game.objects.create(name="Portal 2", slug="portal-2", steam_app_id=620)
        cls.hades = Game.objects.create(name="Hades", slug="hades", steam_app_id=1145360)
        cls.celeste = Game.objects.create(name="Celeste", slug="celeste", steam_app_id_override=504230)

    def owned(self, *games):
        """Simula la lista de juegos que devuelve Steam (la misma para todas las cuentas)."""
        return mock.patch.object(steam, "owned_games", return_value=list(games))

    def owned_by_account(self, games_by_steam_id):
        """Simula Steam con una lista de juegos distinta por SteamID; una lista None es una cuenta privada."""
        def fake(steam_id):
            """Devuelve los juegos de esa cuenta o lanza SteamPrivateError si es privada."""
            games = games_by_steam_id[steam_id]
            if games is None:
                raise steam.SteamPrivateError("privada")
            return games
        return mock.patch.object(steam, "owned_games", side_effect=fake)

    def test_imports_played_games_with_status_by_recency(self):
        now = int(time.time())
        with self.owned(
            {"appid": 620, "playtime_forever": 900, "rtime_last_played": now - 400 * 86400},
            {"appid": 1145360, "playtime_forever": 60, "rtime_last_played": now - 86400},
            {"appid": 504230, "playtime_forever": 0},
            {"appid": 999999, "playtime_forever": 5},
        ):
            result = steam.import_library(self.user)
        self.assertEqual(result, {"added": 2, "updated": 0, "unmatched": 1, "private": []})
        entries = {e.game: e for e in LibraryEntry.objects.filter(user=self.user)}
        self.assertEqual(entries[self.portal].status, LibraryEntry.Status.PLAYED)
        self.assertEqual(entries[self.portal].steam_hours, 15)
        self.assertEqual(entries[self.hades].status, LibraryEntry.Status.PLAYING)
        self.assertNotIn(self.celeste, entries)
        self.account.refresh_from_db()
        self.assertIsNotNone(self.account.synced_at)
        # Lo de Steam se juega en el PC: rellena la plataforma de las reseñas.
        self.assertEqual(ImportedTitle.objects.get(title__external_id="620").platforms, ["PC (Microsoft Windows)"])

    def test_hours_add_up_across_accounts(self):
        second = SteamAccount.objects.create(user=self.user, steam_id="76561190000000002")
        now = int(time.time())
        with self.owned_by_account({
            self.account.steam_id: [{"appid": 620, "playtime_forever": 600, "rtime_last_played": now - 400 * 86400}],
            second.steam_id: [
                {"appid": 620, "playtime_forever": 300, "rtime_last_played": now - 86400},
                {"appid": 1145360, "playtime_forever": 60},
            ],
        }):
            result = steam.import_library(self.user)
        self.assertEqual((result["added"], result["unmatched"]), (2, 0))
        portal = LibraryEntry.objects.get(user=self.user, game=self.portal)
        self.assertEqual(portal.steam_playtime_minutes, 900)
        # La partida más reciente es la de la segunda cuenta, de ayer: entra como «Jugando».
        self.assertEqual(portal.status, LibraryEntry.Status.PLAYING)

    def test_private_accounts_are_skipped_and_reported(self):
        second = SteamAccount.objects.create(user=self.user, steam_id="76561190000000002", persona="Secreta")
        with self.owned_by_account({
            self.account.steam_id: [{"appid": 620, "playtime_forever": 60}],
            second.steam_id: None,
        }):
            result = steam.import_library(self.user)
        self.assertEqual((result["added"], result["private"]), (1, ["Secreta"]))
        second.refresh_from_db()
        self.assertIsNone(second.synced_at)

    def test_unplayed_games_go_to_backlog_when_asked(self):
        with self.owned({"appid": 504230, "playtime_forever": 0}):
            steam.import_library(self.user, include_unplayed=True)
        self.assertEqual(LibraryEntry.objects.get(user=self.user).status, LibraryEntry.Status.BACKLOG)

    def test_reimport_keeps_status_and_updates_hours(self):
        LibraryEntry.objects.create(user=self.user, game=self.portal, status=LibraryEntry.Status.COMPLETED, rating=5)
        with self.owned({"appid": 620, "playtime_forever": 120}):
            result = steam.import_library(self.user)
        self.assertEqual(result, {"added": 0, "updated": 1, "unmatched": 0, "private": []})
        entry = LibraryEntry.objects.get(user=self.user)
        self.assertEqual((entry.status, entry.rating, entry.steam_playtime_minutes), ("completed", 5, 120))

    def wishes(self, *app_ids):
        """Simula la lista de deseos de Steam de todas las cuentas con esos IDs, en ese orden (sin ninguno: vacía o privada)."""
        items = [{"appid": app_id, "priority": n, "date_added": 1700000000 + n} for n, app_id in enumerate(app_ids, 1)]
        return mock.patch.object(steam, "wishlist", return_value=items or None)

    def test_wishlist_comes_to_lo_quiero_without_the_games_you_own(self):
        LibraryEntry.objects.create(user=self.user, game=self.portal)
        knight = Game.objects.create(name="Hollow Knight", slug="hollow-knight", steam_app_id=367520)
        manual = WishlistItem.objects.create(user=self.user, game=knight, target_price=Decimal("5"))
        items = [
            {"appid": 620, "priority": 1, "date_added": 1700000000},
            {"appid": 1145360, "priority": 2, "date_added": 1710000000},
            {"appid": 504230, "priority": 0, "date_added": 1720000000},
            {"appid": 367520, "priority": 3, "date_added": 1690000000},
            {"appid": 999999, "priority": 4, "date_added": 1690000000},
        ]
        with mock.patch.object(steam, "wishlist", return_value=items):
            result = steam.import_wishlist(self.user)
        self.assertEqual(
            (result["total"], [item.game for item in result["added"]], result["owned"], result["unmatched"]),
            (5, [self.hades, self.celeste], 1, 1),
        )
        hades = WishlistItem.objects.get(game=self.hades)
        self.assertEqual((hades.from_steam, hades.steam_priority, hades.steam_added_at.year), (True, 2, 2024))
        self.assertIsNone(WishlistItem.objects.get(game=self.celeste).steam_priority)  # sin ordenar en Steam
        # El que ya tenía en «Lo quiero» solo apunta su puesto en Steam y sigue siendo suyo.
        manual.refresh_from_db()
        self.assertEqual((manual.from_steam, manual.steam_priority, manual.target_price), (False, 3, Decimal("5")))
        self.assertFalse(WishlistItem.objects.filter(game=self.portal).exists())

    def test_wishlist_follows_steam_and_remembers_what_you_removed(self):
        knight = Game.objects.create(name="Hollow Knight", slug="hollow-knight", steam_app_id=367520)
        WishlistItem.objects.create(user=self.user, game=knight)  # puesto a mano en NextPlay

        def run(*app_ids):
            """Importa la lista de deseos con esos juegos en Steam (con el usuario recién leído, como en cada petición)."""
            with self.wishes(*app_ids):
                return steam.import_wishlist(User.objects.get(pk=self.user.pk))

        run(1145360, 504230, 367520)
        self.assertEqual(WishlistItem.objects.filter(user=self.user).count(), 3)
        # Quitar aquí uno que está en Steam lo apunta para no volver a traerlo.
        self.client.force_login(self.user)
        self.client.post(reverse("library:wish_toggle", args=[self.hades.id]))
        self.assertEqual(Profile.objects.get(user=self.user).steam_wishlist_skip, [self.hades.pk])
        result = run(1145360, 504230, 367520)
        self.assertEqual((len(result["added"]), result["skipped"]), (0, 1))
        # Lo que deja de estar en Steam se va si vino de Steam; lo puesto a mano se queda.
        result = run(1145360)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(list(WishlistItem.objects.values_list("game__slug", flat=True)), ["hollow-knight"])
        self.assertFalse(WishlistItem.objects.get(game=knight).on_steam)
        # Una lista privada o vacía no quita nada.
        run(1145360, 504230)
        result = run()
        self.assertEqual((result["unreadable"], result["removed"]), (["Ana"], 0))
        self.assertTrue(WishlistItem.objects.filter(game=self.celeste).exists())
        # Si lo vuelve a añadir él, se olvida que lo quitó.
        self.client.post(reverse("library:wish_toggle", args=[self.hades.id]))
        self.assertEqual(Profile.objects.get(user=self.user).steam_wishlist_skip, [])

    @override_settings(ITAD_API_KEY="")
    def test_sync_button_reports_the_wishlist_with_todays_deals(self):
        Game.objects.filter(pk=self.hades.pk).update(historical_low=Decimal("12.49"))
        prices = [
            (self.hades, 61, "Steam", "12.49", 50), (self.celeste, 61, "Steam", "19.99", 0), (self.celeste, 35, "GOG", "14.99", 25),
        ]
        for game, shop_id, shop, price, cut in prices:
            GamePrice.objects.create(
                game=game, shop_id=shop_id, shop_name=shop, price=Decimal(price), regular_price=Decimal("24.99"),
                currency="EUR", cut=cut, url="https://itad.link/x",
            )
        self.client.force_login(self.user)
        with self.owned({"appid": 620, "playtime_forever": 60}), self.wishes(1145360, 504230, 999999):
            response = self.client.post(reverse("steam_import"), follow=True)
        self.assertContains(
            response,
            "Lista de deseos de Steam: 3 juegos (2 nuevos en «Lo quiero», 1 que no está en el catálogo). "
            "Hoy, 1 está en su mínimo histórico y 1 es más barato fuera de Steam.",
        )
        response = self.client.get(reverse("library:wishlist"), {"orden": "steam"})
        self.assertEqual([row["wish"].game for row in response.context["rows"]], [self.hades, self.celeste])

    def test_wishlist_can_be_turned_off_and_removed_games_brought_back(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("edit_profile")), "También trae tu lista de deseos de Steam")
        self.client.post(reverse("steam_wishlist"), {"steam_wishlist": "0"})
        profile = Profile.objects.get(user=self.user)
        self.assertFalse(profile.steam_wishlist)
        with self.owned(), mock.patch.object(steam, "wishlist") as wishlist:
            self.client.post(reverse("steam_import"))
        wishlist.assert_not_called()
        profile.steam_wishlist, profile.steam_wishlist_skip = True, [self.hades.pk]
        profile.save()
        self.assertContains(self.client.get(reverse("edit_profile")), "1 juego que quitaste a mano no se vuelve a traer")
        self.client.post(reverse("steam_wishlist"), {"restore": "1"})
        self.assertEqual(Profile.objects.get(user=self.user).steam_wishlist_skip, [])

    def test_minutes_on_steam_deck_are_kept_apart(self):
        with self.owned(
            {"appid": 620, "playtime_forever": 900, "playtime_deck_forever": 600},
            {"appid": 1145360, "playtime_forever": 60},
        ):
            steam.import_library(self.user)
        deck = dict(ImportedTitle.objects.values_list("title__external_id", "deck_minutes"))
        self.assertEqual(deck, {"620": 600, "1145360": 0})

    def test_reimport_reminds_to_update_an_outdated_review(self):
        LibraryEntry.objects.create(user=self.user, game=self.portal, steam_playtime_minutes=60)
        review = Review.objects.create(
            user=self.user, game=self.portal, text="Corto.", hours=Decimal("1"), hours_sources="steam",
            published_at=timezone.now(),
        )
        with self.owned({"appid": 620, "playtime_forever": 25 * 60}):
            steam.import_library(self.user)
        notice = Notification.objects.get(user=self.user, kind=Notification.Kind.REVIEW_UPDATE)
        self.assertEqual(notice.text, "Llevas 24 h más en Portal 2 que cuando escribiste tu reseña: ¿la actualizas?")
        self.assertEqual(notice.url, reverse("library:review_edit", args=[review.pk]))

    @override_settings(IGDB_CLIENT_ID="id", IGDB_CLIENT_SECRET="secret")
    def test_unknown_games_are_looked_up_in_igdb(self):
        payload = [{"id": 72, "name": "Portal", "slug": "portal", "external_games": [
            {"uid": "400", "name": "Portal", "external_game_source": 1}]}]
        with self.owned({"appid": 400, "playtime_forever": 30}), mock.patch(
            "games.igdb.IGDBClient.games_by_steam_ids", return_value=(payload, {400: 72})
        ):
            result = steam.import_library(self.user)
        self.assertEqual(result["added"], 1)
        self.assertEqual(LibraryEntry.objects.get(user=self.user).game.igdb_id, 72)

    def test_private_steam_profile_shows_a_hint(self):
        self.client.force_login(self.user)
        with mock.patch.object(steam, "_api", return_value={}):
            response = self.client.post(reverse("steam_import"), follow=True)
        self.assertContains(response, "Detalles del juego")


class SteamWithoutKeyTests(TestCase):
    @override_settings(STEAM_API_KEY="")
    def test_import_without_api_key_explains_what_is_missing(self):
        user = User.objects.create_user("ana", "ana@example.com", "x")
        SteamAccount.objects.create(user=user, steam_id="76561197960287930")
        self.client.force_login(user)
        response = self.client.post(reverse("steam_import"), follow=True)
        self.assertContains(response, "falta STEAM_API_KEY")
        self.assertNotContains(response, "Sincronizar Steam")


NPSSO = "a" * 64


def psn_title(name, duration, days_ago=400, category="ps4_game"):
    """Juego tal como lo devuelve la lista de juegos jugados de PSN; la primera partida fue 30 días antes de la última."""
    played = timezone.now() - timedelta(days=days_ago)
    return {
        "name": name, "localizedName": name, "category": category,
        "playDuration": duration, "lastPlayedDateTime": played.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "firstPlayedDateTime": (played - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }


class PSNHelpersTests(TestCase):
    def test_clean_npsso_accepts_code_or_sony_json(self):
        self.assertEqual(psn.clean_npsso(f"  {NPSSO} "), NPSSO)
        self.assertEqual(psn.clean_npsso(f'{{"npsso":"{NPSSO}"}}'), NPSSO)
        self.assertIsNone(psn.clean_npsso("corto"))
        self.assertIsNone(psn.clean_npsso('{"npsso": 5'))

    def test_duration_minutes(self):
        self.assertEqual(psn.duration_minutes("PT228H56M33S"), 228 * 60 + 57)
        self.assertEqual(psn.duration_minutes("P1DT2H"), 26 * 60)
        self.assertEqual(psn.duration_minutes("PT45M"), 45)
        self.assertEqual(psn.duration_minutes(""), 0)

    def test_clean_name_drops_marks_and_platform_suffixes(self):
        self.assertEqual(psn.clean_name("Marvel's Spider-Man™"), "Marvel's Spider-Man")
        self.assertEqual(psn.clean_name("God of War Ragnarök PS4 & PS5"), "God of War Ragnarök")
        self.assertEqual(psn.clean_name("Hades (PS5)"), "Hades")
        self.assertEqual(psn.clean_name("Death Stranding - PS5 Edition"), "Death Stranding")
        self.assertEqual(psn.clean_name("Persona 5"), "Persona 5")

    def test_access_token_needs_the_authorization_code(self):
        with mock.patch("accounts.psn.requests.get", return_value=mock.Mock(headers={"Location": "x://redirect?error=login_required"})):
            with self.assertRaises(psn.PSNAuthError):
                psn.access_token(NPSSO)
        with mock.patch("accounts.psn.requests.get", return_value=mock.Mock(headers={"Location": "x://redirect?code=v3.ABC"})), \
                mock.patch("accounts.psn.requests.post", return_value=mock.Mock(json=lambda: {"access_token": "tok"})) as post:
            self.assertEqual(psn.access_token(NPSSO), "tok")
        self.assertEqual(post.call_args.kwargs["data"]["code"], "v3.ABC")


@override_settings(IGDB_CLIENT_ID="", IGDB_CLIENT_SECRET="")
class PSNImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.hades = Game.objects.create(name="Hades", slug="hades")
        cls.gow = Game.objects.create(name="God of War Ragnarök", slug="god-of-war-ragnarok")

    def fake_psn(self, titles, account_id="111", online_id="AnaPSN"):
        """Simula Sony: token, perfil y lista de juegos jugados."""
        return mock.patch.multiple(
            psn,
            access_token=mock.Mock(return_value="tok"),
            profile=mock.Mock(return_value={"account_id": account_id, "online_id": online_id, "avatar": ""}),
            played_games=mock.Mock(return_value=titles),
        )

    def test_import_links_account_and_adds_games_with_hours(self):
        with self.fake_psn([
            psn_title("Hades (PS5)", "PT10H", days_ago=2, category="ps5_native_game"),
            psn_title("Hades", "PT5H30M"),
            psn_title("God of War Ragnarök PS4 & PS5", "PT40H"),
            psn_title("Juego Desconocido", "PT1H"),
        ]):
            result = psn.import_library(self.user, NPSSO)
        self.assertEqual((result["added"], result["updated"], result["unmatched"]), (2, 0, 1))
        self.assertEqual(self.user.psn_account.online_id, "AnaPSN")
        hades = LibraryEntry.objects.get(user=self.user, game=self.hades)
        # Las versiones de PS4 y PS5 se suman y la partida reciente lo deja como «Jugando».
        self.assertEqual((hades.psn_playtime_minutes, hades.status), (930, LibraryEntry.Status.PLAYING))
        self.assertEqual(LibraryEntry.objects.get(game=self.gow).status, LibraryEntry.Status.PLAYED)
        # Se guardan las dos plataformas y la primera partida más antigua, para rellenar las reseñas.
        imported = ImportedTitle.objects.get(user=self.user, title__game=self.hades)
        self.assertEqual(imported.platforms, ["PlayStation 4", "PlayStation 5"])
        self.assertEqual(imported.first_played.date(), (timezone.now() - timedelta(days=430)).date())

    def test_hours_add_up_with_steam_and_status_is_kept(self):
        LibraryEntry.objects.create(
            user=self.user, game=self.hades, status=LibraryEntry.Status.COMPLETED, steam_playtime_minutes=120
        )
        with self.fake_psn([psn_title("Hades", "PT3H")]):
            result = psn.import_library(self.user, NPSSO)
        self.assertEqual(result["updated"], 1)
        entry = LibraryEntry.objects.get(user=self.user, game=self.hades)
        self.assertEqual((entry.status, entry.playtime_minutes, entry.playtime_hours), ("completed", 300, 5.0))

    def test_psn_account_of_another_user_is_rejected(self):
        other = User.objects.create_user("luis", "luis@example.com", "x")
        PSNAccount.objects.create(user=other, account_id="111")
        with self.fake_psn([psn_title("Hades", "PT3H")]), self.assertRaises(ValueError):
            psn.import_library(self.user, NPSSO)
        self.assertFalse(LibraryEntry.objects.filter(user=self.user).exists())

    def test_sync_view_reports_result_and_never_stores_the_code(self):
        self.client.force_login(self.user)
        with self.fake_psn([psn_title("Hades", "PT3H")]):
            response = self.client.post(reverse("psn_sync"), {"npsso": NPSSO}, follow=True)
        self.assertContains(response, "PlayStation (AnaPSN): 1 juego añadido")
        self.assertContains(response, "AnaPSN")
        stored = [str(v) for v in PSNAccount.objects.values().get().values()]
        self.assertNotIn(NPSSO, stored)

    def test_sync_view_rejects_bad_or_expired_codes(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("psn_sync"), {"npsso": "nope"}, follow=True)
        self.assertContains(response, "no parece un código NPSSO")
        with mock.patch.object(psn, "access_token", side_effect=psn.PSNAuthError("x")):
            response = self.client.post(reverse("psn_sync"), {"npsso": NPSSO}, follow=True)
        self.assertContains(response, "puede haber caducado")
        self.assertFalse(PSNAccount.objects.exists())

    def test_unlink_keeps_games(self):
        PSNAccount.objects.create(user=self.user, account_id="111")
        LibraryEntry.objects.create(user=self.user, game=self.hades, psn_playtime_minutes=60)
        self.client.force_login(self.user)
        self.client.post(reverse("psn_unlink"))
        self.assertFalse(PSNAccount.objects.exists())
        self.assertTrue(LibraryEntry.objects.filter(user=self.user).exists())


@override_settings(IGDB_CLIENT_ID="", IGDB_CLIENT_SECRET="")
class ImportStatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.game = Game.objects.create(name="Sifu", slug="sifu")
        cls.cod = Game.objects.create(name="Call of Duty", slug="call-of-duty")

    def test_backlog_game_with_hours_from_psn_becomes_played(self):
        LibraryEntry.objects.create(user=self.user, game=self.game, status=LibraryEntry.Status.BACKLOG, steam_playtime_minutes=0)
        with mock.patch.multiple(
            psn, access_token=mock.Mock(return_value="tok"),
            profile=mock.Mock(return_value={"account_id": "1", "online_id": "a", "avatar": ""}),
            played_games=mock.Mock(return_value=[psn_title("Sifu", "PT7H")]),
        ):
            psn.import_library(self.user, NPSSO)
        self.assertEqual(LibraryEntry.objects.get(user=self.user).status, LibraryEntry.Status.PLAYED)

    def test_user_status_is_kept_when_it_is_not_backlog(self):
        entry = LibraryEntry.objects.create(user=self.user, game=self.game, status=LibraryEntry.Status.DROPPED)
        self.assertTrue(entry.update_import("steam", 90, None))
        entry.refresh_from_db()
        self.assertEqual((entry.status, entry.steam_playtime_minutes), (LibraryEntry.Status.DROPPED, 90))
        self.assertFalse(entry.update_import("steam", 90, None))

    def test_call_of_duty_launcher_is_not_matched(self):
        with mock.patch.multiple(
            psn, access_token=mock.Mock(return_value="tok"),
            profile=mock.Mock(return_value={"account_id": "1", "online_id": "a", "avatar": ""}),
            played_games=mock.Mock(return_value=[psn_title("Call of Duty®", "PT5H")]),
        ):
            result = psn.import_library(self.user, NPSSO)
        self.assertEqual((result["added"], result["unmatched"]), (0, 1))


class PlatformAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")

    def setUp(self):
        self.client.force_login(self.user)

    def add(self, platform, handle):
        """Envía el formulario de otras plataformas y sigue la redirección."""
        return self.client.post(reverse("platform_add"), {"platform": platform, "handle": handle}, follow=True)

    def test_add_and_show_on_profile(self):
        self.add("battlenet", "Ana#1234")
        self.add("gog", "ana_gamer")
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, "Ana#1234")
        self.assertContains(response, "https://www.gog.com/u/ana_gamer")

    def test_switch_code_is_normalized(self):
        self.add("nintendo", "1234 5678 9012")
        self.assertEqual(PlatformAccount.objects.get().handle, "SW-1234-5678-9012")

    def test_invalid_formats_are_rejected(self):
        for platform, handle in [
            ("nintendo", "SW-12"), ("battlenet", "Ana"), ("xbox", "este gamertag es larguísimo"), ("gog", "con espacios"),
        ]:
            response = self.add(platform, handle)
            self.assertContains(response, "no válido")
        self.assertFalse(PlatformAccount.objects.exists())

    def test_one_account_per_platform_is_replaced(self):
        self.add("xbox", "AnaPlays")
        self.add("xbox", "AnaPlays#42")
        self.assertEqual(list(PlatformAccount.objects.values_list("handle", flat=True)), ["AnaPlays#42"])

    def test_remove_only_own_account(self):
        other = User.objects.create_user("luis", "luis@example.com", "x")
        PlatformAccount.objects.create(user=other, platform="epic", handle="Luis")
        PlatformAccount.objects.create(user=self.user, platform="epic", handle="Ana")
        self.client.post(reverse("platform_remove", args=["epic"]))
        self.assertEqual(list(PlatformAccount.objects.values_list("handle", flat=True)), ["Luis"])


def fake_response(status=200, data=None, text=""):
    """Respuesta de requests simulada con su código, su JSON y su texto."""
    response = mock.Mock(status_code=status, text=text or json.dumps(data or {}))
    response.json.return_value = data or {}
    response.raise_for_status = mock.Mock()
    return response


@override_settings(XBOX_CLIENT_ID="cid", XBOX_CLIENT_SECRET="secret", IGDB_CLIENT_ID="", IGDB_CLIENT_SECRET="")
class XboxTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.halo = Game.objects.create(name="Halo Infinite", slug="halo-infinite")
        cls.forza = Game.objects.create(name="Forza Horizon 5", slug="forza-horizon-5")

    def setUp(self):
        self.client.force_login(self.user)

    def test_tokens_chain_microsoft_user_and_xsts(self):
        responses = [
            fake_response(data={"access_token": "ms"}),
            fake_response(data={"Token": "user"}),
            fake_response(data={"Token": "xsts", "DisplayClaims": {"xui": [{"uhs": "123", "xid": "2535", "gtg": "AnaGT"}]}}),
        ]
        with mock.patch("accounts.xbox.requests.post", side_effect=responses) as post:
            data = xbox.xbox_tokens("code", "http://testserver/vuelta/")
        self.assertEqual(data, {"xuid": "2535", "gamertag": "AnaGT", "auth": "XBL3.0 x=123;xsts"})
        self.assertEqual(post.call_args_list[1].kwargs["json"]["Properties"]["RpsTicket"], "d=ms")

    def test_xsts_errors_are_explained(self):
        responses = [
            fake_response(data={"access_token": "ms"}),
            fake_response(data={"Token": "user"}),
            fake_response(status=401, data={"XErr": 2148916233}),
        ]
        with mock.patch("accounts.xbox.requests.post", side_effect=responses), self.assertRaisesMessage(
            xbox.XboxError, "no tiene perfil de Xbox"
        ):
            xbox.xbox_tokens("code", "http://testserver/vuelta/")

    def test_minutes_played_reads_the_batch(self):
        data = {"statlistscollection": [{"stats": [
            {"titleid": "1", "name": "MinutesPlayed", "value": "600"},
            {"titleid": "2", "name": "MinutesPlayed", "value": None},
        ]}]}
        with mock.patch("accounts.xbox.requests.post", return_value=fake_response(data=data)):
            self.assertEqual(xbox.minutes_played("2535", "auth", ["1", "2"]), {"1": 600})

    def fake_xbox(self, titles, minutes, xuid="2535"):
        """Simula Xbox Live: tokens, historial, minutos y avatar."""
        return mock.patch.multiple(
            xbox,
            xbox_tokens=mock.Mock(return_value={"xuid": xuid, "gamertag": "AnaGT", "auth": "a"}),
            played_titles=mock.Mock(return_value=titles),
            minutes_played=mock.Mock(return_value=minutes),
            avatar=mock.Mock(return_value=""),
        )

    def test_import_links_account_and_adds_games(self):
        recent = (timezone.now() - timedelta(days=1)).isoformat()
        old = (timezone.now() - timedelta(days=300)).isoformat()
        PlatformAccount.objects.create(user=self.user, platform="xbox", handle="AnaManual")
        titles = [
            {"titleId": "1", "name": "Halo Infinite", "type": "Game", "titleHistory": {"lastTimePlayed": recent}},
            {"titleId": "2", "name": "Forza Horizon 5 Xbox Series X|S", "type": "Game", "titleHistory": {"lastTimePlayed": old}},
            {"titleId": "3", "name": "Juego Raro", "type": "Game", "titleHistory": {}},
        ]
        with self.fake_xbox(titles, {"1": 120, "2": 600}):
            result = xbox.import_library(self.user, "code", "http://testserver/vuelta/")
        self.assertEqual((result["added"], result["unmatched"]), (2, 1))
        halo = LibraryEntry.objects.get(game=self.halo)
        self.assertEqual((halo.status, halo.xbox_playtime_minutes), (LibraryEntry.Status.PLAYING, 120))
        self.assertEqual(LibraryEntry.objects.get(game=self.forza).status, LibraryEntry.Status.PLAYED)
        self.assertEqual(self.user.xbox_account.gamertag, "AnaGT")
        # El gamertag escrito a mano se sustituye por la cuenta verificada.
        self.assertFalse(PlatformAccount.objects.filter(user=self.user).exists())

    def test_hours_add_up_with_other_platforms(self):
        LibraryEntry.objects.create(user=self.user, game=self.halo, steam_playtime_minutes=60, psn_playtime_minutes=30)
        with self.fake_xbox([{"titleId": "1", "name": "Halo Infinite", "type": "Game", "titleHistory": {}}], {"1": 90}):
            xbox.import_library(self.user, "code", "http://testserver/vuelta/")
        self.assertEqual(LibraryEntry.objects.get(game=self.halo).playtime_minutes, 180)

    def test_xbox_account_of_another_user_is_rejected(self):
        other = User.objects.create_user("luis", "luis@example.com", "x")
        XboxAccount.objects.create(user=other, xuid="2535")
        with self.fake_xbox([], {}), self.assertRaises(ValueError):
            xbox.import_library(self.user, "code", "http://testserver/vuelta/")

    def test_link_and_callback_check_the_state(self):
        response = self.client.post(reverse("xbox_link"))
        self.assertTrue(response["Location"].startswith(xbox.AUTHORIZE_URL))
        state = self.client.session["xbox_oauth_state"]
        with self.fake_xbox([], {}):
            response = self.client.get(reverse("xbox_callback"), {"code": "c", "state": "otro"}, follow=True)
        self.assertContains(response, "ha caducado")
        self.client.post(reverse("xbox_link"))
        state = self.client.session["xbox_oauth_state"]
        with self.fake_xbox([], {}):
            response = self.client.get(reverse("xbox_callback"), {"code": "c", "state": state})
        self.assertRedirects(response, reverse("profile", args=["ana"]))
        self.assertTrue(XboxAccount.objects.filter(user=self.user).exists())

    @override_settings(XBOX_CLIENT_ID="", XBOX_CLIENT_SECRET="")
    def test_link_without_azure_app_explains_what_is_missing(self):
        response = self.client.post(reverse("xbox_link"), follow=True)
        self.assertContains(response, "faltan XBOX_CLIENT_ID")


class LinkDialogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.other = User.objects.create_user("luis", "luis@example.com", "x")

    def test_owner_sees_the_dialog_with_every_platform(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, "Vincular cuentas")
        self.assertContains(response, 'id="link-dialog"')
        for text in ("Steam", "Xbox", "PlayStation", "Otras plataformas"):
            self.assertContains(response, text)

    def test_visitors_do_not_see_it(self):
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse("profile", args=["ana"])), 'id="link-dialog"')

    def test_actions_from_the_dialog_return_to_the_profile(self):
        self.client.force_login(self.user)
        profile_url = reverse("profile", args=["ana"])
        response = self.client.post(
            reverse("platform_add"), {"platform": "gog", "handle": "ana", "next": profile_url}
        )
        self.assertRedirects(response, profile_url)
        response = self.client.post(reverse("platform_remove", args=["gog"]), {"next": "https://evil.example/"})
        self.assertRedirects(response, f"{reverse('edit_profile')}#plataformas", fetch_redirect_response=False)


def psn_import(user, titles, account_id="111"):
    """Importa de PlayStation con Sony simulado: token, perfil y la lista de juegos jugados."""
    with mock.patch.multiple(
        psn,
        access_token=mock.Mock(return_value="tok"),
        profile=mock.Mock(return_value={"account_id": account_id, "online_id": "AnaPSN", "avatar": ""}),
        played_games=mock.Mock(return_value=titles),
    ):
        return psn.import_library(user, NPSSO)


@override_settings(IGDB_CLIENT_ID="", IGDB_CLIENT_SECRET="")
class ImportedTitleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.other = User.objects.create_user("luis", "luis@example.com", "x")
        ps2 = Platform.objects.create(name="PlayStation 2", slug="ps2")
        ps4 = Platform.objects.create(name="PlayStation 4", slug="ps4")
        # Dos juegos con el mismo nombre: el de PS2 es más popular, pero en PS4 solo puede ser el de 2018.
        cls.gow_2005 = Game.objects.create(name="God of War", slug="god-of-war-2005", popularity=900)
        cls.gow_2005.platforms.set([ps2])
        cls.gow_2018 = Game.objects.create(name="God of War", slug="god-of-war", popularity=500)
        cls.gow_2018.platforms.set([ps4])
        cls.hades = Game.objects.create(name="Hades", slug="hades")

    def title(self, name, duration="PT2H", title_id=None, **kwargs):
        """Juego de PSN con su titleId (por defecto, sacado del nombre)."""
        return {**psn_title(name, duration, **kwargs), "titleId": title_id or name.upper().replace(" ", "_")}

    def test_remakes_are_told_apart_by_platform(self):
        psn_import(self.user, [self.title("God of War", "PT30H")])
        entry = LibraryEntry.objects.get(user=self.user)
        self.assertEqual((entry.game, entry.psn_playtime_minutes), (self.gow_2018, 1800))

    def test_titles_are_saved_and_reused_without_searching_again(self):
        result = psn_import(self.user, [self.title("Hades"), self.title("Juego Raro")])
        self.assertEqual(result["unmatched"], 1)
        hades = ExternalTitle.objects.get(external_id="HADES")
        self.assertEqual((hades.platform, hades.game, hades.source), ("psn", self.hades, "auto"))
        self.assertEqual(ImportedTitle.objects.get(user=self.user, title=hades).minutes, 120)
        # La segunda vez solo se busca el que seguía sin juego.
        with mock.patch.object(psn, "match_by_name", return_value={}) as search:
            psn_import(self.user, [self.title("Hades", "PT3H"), self.title("Juego Raro")])
        self.assertEqual(list(search.call_args.args[0]), ["JUEGO_RARO"])
        self.assertEqual(LibraryEntry.objects.get(user=self.user, game=self.hades).psn_playtime_minutes, 180)

    def test_unmatched_title_is_assigned_by_hand_for_everyone(self):
        psn_import(self.user, [self.title("Hades Edición Rara", "PT4H")])
        self.client.force_login(self.user)
        response = self.client.get(reverse("imported_titles"))
        self.assertContains(response, "Hades Edición Rara")
        imported = ImportedTitle.objects.get(user=self.user)
        response = self.client.get(reverse("imported_titles"), {"titulo": imported.pk, "q": "hades"})
        self.assertContains(response, "Es este")

        response = self.client.post(
            reverse("imported_title_assign", args=[imported.pk]), {"game": self.hades.pk}, follow=True
        )
        self.assertContains(response, "es ahora Hades")
        entry = LibraryEntry.objects.get(user=self.user, game=self.hades)
        self.assertEqual((entry.psn_playtime_minutes, entry.status), (240, LibraryEntry.Status.PLAYED))
        title = imported.title
        title.refresh_from_db()
        self.assertEqual((title.source, title.assigned_by), ("user", self.user))
        # Otro usuario con el mismo título lo recibe ya emparejado.
        with mock.patch.object(psn, "match_by_name", return_value={}) as search:
            psn_import(self.other, [self.title("Hades Edición Rara", "PT1H")], account_id="222")
        search.assert_not_called()
        self.assertTrue(LibraryEntry.objects.filter(user=self.other, game=self.hades).exists())

    def test_wrong_match_moves_hours_and_drops_the_untouched_game(self):
        psn_import(self.user, [self.title("Hades", "PT5H")])
        imported = ImportedTitle.objects.get(user=self.user)
        self.client.force_login(self.user)
        response = self.client.get(reverse("imported_titles"), {"juego": self.hades.pk})
        self.assertContains(response, "¿No es Hades?")
        self.client.post(reverse("imported_title_assign", args=[imported.pk]), {"game": self.gow_2018.pk})
        self.assertFalse(LibraryEntry.objects.filter(user=self.user, game=self.hades).exists())
        self.assertEqual(LibraryEntry.objects.get(user=self.user, game=self.gow_2018).psn_playtime_minutes, 300)

    def test_a_correction_by_someone_else_moves_hours_on_the_next_import(self):
        psn_import(self.other, [self.title("Hades", "PT2H")], account_id="222")
        psn_import(self.user, [self.title("Hades", "PT5H")])
        matching.reassign(ImportedTitle.objects.get(user=self.other), self.gow_2018, self.other)
        psn_import(self.user, [self.title("Hades", "PT6H")])
        self.assertIsNone(LibraryEntry.objects.get(user=self.user, game=self.hades).psn_playtime_minutes)
        self.assertEqual(LibraryEntry.objects.get(user=self.user, game=self.gow_2018).psn_playtime_minutes, 360)

    def test_hours_from_before_the_titles_existed_are_kept(self):
        # Importado antes de que se guardaran los títulos: no hay nada que diga que esté mal emparejado.
        LibraryEntry.objects.create(user=self.user, game=self.gow_2005, psn_playtime_minutes=0)
        psn_import(self.user, [self.title("Hades")])
        self.assertEqual(LibraryEntry.objects.get(user=self.user, game=self.gow_2005).psn_playtime_minutes, 0)

    def test_wrong_match_keeps_a_rated_game_without_its_hours(self):
        psn_import(self.user, [self.title("Hades", "PT5H")])
        LibraryEntry.objects.filter(user=self.user, game=self.hades).update(rating=4)
        imported = ImportedTitle.objects.get(user=self.user)
        result = matching.reassign(imported, self.gow_2018, self.user)
        self.assertFalse(result["removed"])
        self.assertIsNone(LibraryEntry.objects.get(user=self.user, game=self.hades).psn_playtime_minutes)

    def test_locked_titles_and_other_users_titles_cannot_be_changed(self):
        psn_import(self.user, [self.title("Hades")])
        imported = ImportedTitle.objects.get(user=self.user)
        ExternalTitle.objects.update(locked=True)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("imported_title_assign", args=[imported.pk]), {"game": self.gow_2018.pk}, follow=True
        )
        self.assertContains(response, "ya no se puede cambiar")
        self.assertEqual(ExternalTitle.objects.get().game, self.hades)
        self.client.force_login(self.other)
        response = self.client.post(reverse("imported_title_assign", args=[imported.pk]), {"game": ""})
        self.assertEqual(response.status_code, 404)

    def test_hidden_titles_leave_the_list(self):
        psn_import(self.user, [self.title("Herramienta Rara")])
        imported = ImportedTitle.objects.get(user=self.user)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("profile", args=["ana"])), "1 juego importado sin emparejar")
        response = self.client.post(reverse("imported_title_hide", args=[imported.pk]), {"hidden": "1"}, follow=True)
        self.assertContains(response, "Has ocultado")
        self.assertNotContains(self.client.get(reverse("imported_titles")), "Herramienta Rara")
        self.assertContains(self.client.get(reverse("imported_titles"), {"ocultos": "1"}), "Herramienta Rara")
        self.assertNotContains(self.client.get(reverse("profile", args=["ana"])), "sin emparejar")

    def test_widget_offers_to_review_only_imported_games(self):
        self.client.force_login(self.user)
        LibraryEntry.objects.create(user=self.user, game=self.gow_2005)
        self.assertNotContains(self.client.get(self.gow_2005.get_absolute_url()), "¿No es este el juego")
        psn_import(self.user, [self.title("Hades")])
        self.assertContains(self.client.get(self.hades.get_absolute_url()), "¿No es este el juego")


@override_settings(STEAM_API_KEY="key", IGDB_CLIENT_ID="", IGDB_CLIENT_SECRET="")
class SyncLibrariesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.portal = Game.objects.create(name="Portal 2", slug="portal-2", steam_app_id=620)
        cls.users = {}
        for name in ("ana", "luis", "eva", "sin_auto"):
            user = User.objects.create_user(name, f"{name}@example.com", "x")
            SteamAccount.objects.create(user=user, steam_id=str(76561197960287930 + len(cls.users)))
            cls.users[name] = user
        cls.users["sin_auto"].profile.auto_sync = False
        cls.users["sin_auto"].profile.save()
        # Eva sincronizó a mano hace una hora: no hace falta repetirlo esta noche.
        SteamAccount.objects.filter(user=cls.users["eva"]).update(synced_at=timezone.now() - timedelta(hours=1))

    def run_sync(self, owned, wishes=None, **options):
        """Lanza sync_libraries sin pausas con Steam simulado (owned es el side_effect de owned_games y wishes, la
        lista de deseos de todas las cuentas; None = vacía o privada)."""
        out = io.StringIO()
        with mock.patch.object(steam, "owned_games", side_effect=owned), \
                mock.patch.object(steam, "player_summary", return_value={}), \
                mock.patch.object(steam, "wishlist", return_value=wishes):
            call_command("sync_libraries", pause=0, stdout=out, stderr=io.StringIO(), **options)
        return out.getvalue()

    def test_nightly_sync_also_brings_the_wishlist_if_wanted(self):
        hades = Game.objects.create(name="Hades", slug="hades", steam_app_id=1145360)
        self.users["luis"].profile.steam_wishlist = False
        self.users["luis"].profile.save()
        out = self.run_sync(
            lambda steam_id: [{"appid": 620, "playtime_forever": 90}], wishes=[{"appid": 1145360, "date_added": 1700000000}]
        )
        self.assertIn("ana: lista de deseos de Steam con 1 juego, 1 nuevo en «Lo quiero» y 0 quitados.", out)
        self.assertEqual(list(WishlistItem.objects.values_list("user__username", "game")), [("ana", hades.pk)])

    def test_syncs_only_users_with_auto_sync_and_not_synced_recently(self):
        out = self.run_sync(lambda steam_id: [{"appid": 620, "playtime_forever": 90}])
        synced = set(LibraryEntry.objects.values_list("user__username", flat=True))
        self.assertEqual(synced, {"ana", "luis"})
        self.assertIn("2 bibliotecas sincronizadas", out)
        self.assertIsNotNone(User.objects.get(username="ana").profile.auto_synced_at)

    def test_one_failing_user_does_not_stop_the_rest(self):
        ana_id = self.users["ana"].steam_accounts.get().steam_id

        def owned(steam_id):
            """Steam falla con Ana y responde con los demás."""
            if steam_id == ana_id:
                raise steam.SteamError("caído")
            return [{"appid": 620, "playtime_forever": 90}]

        with self.assertLogs("accounts.management.commands.sync_libraries", "ERROR"):
            out = self.run_sync(owned)
        self.assertIn("1 bibliotecas sincronizadas, 1 con error", out)
        self.assertTrue(LibraryEntry.objects.filter(user=self.users["luis"]).exists())

    def test_user_option_forces_one_user(self):
        self.run_sync(lambda steam_id: [{"appid": 620, "playtime_forever": 90}], user="sin_auto")
        self.assertEqual(list(LibraryEntry.objects.values_list("user__username", flat=True)), ["sin_auto"])
        with self.assertRaises(CommandError):
            self.run_sync(lambda steam_id: [], user="nadie")

    @override_settings(STEAM_API_KEY="")
    def test_without_steam_key_it_fails_and_nightly_still_runs_prices(self):
        with self.assertRaisesMessage(CommandError, "sync_libraries"), \
                mock.patch("games.management.commands.update_prices.Command.handle", return_value=None) as prices, \
                mock.patch("games.management.commands.localize_games.Command.handle", return_value=None) as spanish:
            call_command("nightly", stdout=io.StringIO(), stderr=io.StringIO())
        prices.assert_called_once()
        spanish.assert_called_once()

    def test_toggle_from_the_steam_panel(self):
        user = self.users["ana"]
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse("edit_profile")), "Se sincroniza sola cada noche")
        self.client.post(reverse("steam_auto_sync"), {"auto_sync": "0"})
        user.profile.refresh_from_db()
        self.assertFalse(user.profile.auto_sync)
        self.assertContains(self.client.get(reverse("edit_profile")), "Sincronización nocturna desactivada")


def image_upload(name="foto.jpg", size=(800, 600), fmt="JPEG", orientation=None):
    """Imagen de prueba lista para subir. Con orientation lleva EXIF como las fotos del móvil (marca y giro)."""
    buffer = io.BytesIO()
    extra = {}
    if orientation:
        exif = Image.Exif()
        exif[0x010F] = "Movil de prueba"  # Make
        exif[0x0112] = orientation  # Orientation
        extra["exif"] = exif
    Image.new("RGB", size, (200, 30, 90)).save(buffer, fmt, **extra)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


# Carpeta temporal para las imágenes que suben los tests (se borra al terminar la clase).
TEST_MEDIA = tempfile.mkdtemp(prefix="nextplay-media-")


@override_settings(MEDIA_ROOT=TEST_MEDIA)
class ProfileCustomizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.other = User.objects.create_user("luis", "luis@example.com", "x")
        cls.games = [Game.objects.create(name=f"Juego {i}", slug=f"juego-{i}", cover_image_id=f"co{i}") for i in range(7)]
        for game in cls.games[:3]:
            LibraryEntry.objects.create(user=cls.user, game=game, status=LibraryEntry.Status.PLAYING)

    @classmethod
    def tearDownClass(cls):
        """Borra las imágenes que han subido los tests."""
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA, ignore_errors=True)

    def setUp(self):
        self.client.force_login(self.user)
        cache.clear()

    def post_profile(self, **data):
        """Envía Editar perfil con los datos dados (perfil público salvo que se diga otra cosa)."""
        return self.client.post(reverse("edit_profile"), {"is_public": "on", **data})

    def profile(self):
        """El perfil de ana tal como está guardado ahora."""
        return Profile.objects.get(user=self.user)

    def test_avatar_is_converted_and_loses_exif_but_is_not_cropped(self):
        response = self.post_profile(avatar=image_upload(size=(1600, 900), orientation=1))
        self.assertRedirects(response, reverse("profile", args=["ana"]))
        avatar = self.profile().avatar
        self.assertTrue(avatar.name.startswith(f"perfiles/{self.user.pk}/avatar-"))
        self.assertTrue(avatar.name.endswith(".webp"))
        with Image.open(avatar.path) as saved:
            # Entera y reducida hasta caber en 800 × 800: la parte que se ve la elige el encuadre.
            self.assertEqual((saved.format, saved.size), ("WEBP", (800, 450)))
            self.assertEqual(len(saved.getexif()), 0)

    def test_background_follows_exif_orientation_and_keeps_its_ratio(self):
        # Orientación 6: la foto está tumbada y hay que girarla 90°.
        self.post_profile(background_mode="image", background=image_upload(size=(800, 600), orientation=6))
        with Image.open(self.profile().background.path) as saved:
            self.assertEqual(saved.size, (600, 800))

    def test_rejects_files_that_are_not_images(self):
        response = self.post_profile(avatar=SimpleUploadedFile("foto.png", b"no soy una imagen", "image/png"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors["avatar"])
        self.assertFalse(self.profile().avatar)

    def test_rejects_too_heavy_images(self):
        upload = SimpleUploadedFile("enorme.jpg", b"x" * (5 * 1024 * 1024 + 1), "image/jpeg")
        with self.assertRaisesMessage(ValidationError, "el máximo son 5 MB"):
            images.process(upload, "avatar")

    def test_replaced_and_removed_images_are_deleted_from_disk(self):
        self.post_profile(avatar=image_upload())
        first = self.profile().avatar.name
        self.post_profile(avatar=image_upload())
        second = self.profile().avatar.name
        self.assertNotEqual(first, second)
        self.assertFalse(default_storage.exists(first))
        self.assertTrue(default_storage.exists(second))
        self.post_profile(**{"avatar-clear": "on"})
        self.assertFalse(self.profile().avatar)
        self.assertFalse(default_storage.exists(second))

    def test_deleting_the_user_deletes_their_images(self):
        user = User.objects.create_user("eva", "eva@example.com", "x")
        self.client.force_login(user)
        self.post_profile(avatar=image_upload())
        name = Profile.objects.get(user=user).avatar.name
        user.delete()
        self.assertFalse(default_storage.exists(name))

    def test_avatar_source(self):
        response = self.client.get(reverse("edit_profile"))
        sources = [value for value, _ in response.context["form"].fields["avatar_source"].choices]
        # Sin cuentas vinculadas no se ofrecen sus fotos.
        self.assertEqual(sources, ["auto", "upload", "initial"])
        self.post_profile(avatar=image_upload(), avatar_source="initial")
        self.assertEqual(self.profile().avatar_url, "")
        self.post_profile(avatar_source="auto")
        self.assertTrue(self.profile().avatar_url.endswith(".webp"))

    def test_steam_avatar_can_be_chosen_over_the_uploaded_one(self):
        SteamAccount.objects.create(user=self.user, steam_id="76561197960287930", avatar="https://steam/a.jpg")
        self.post_profile(avatar=image_upload(), avatar_source="steam")
        self.assertEqual(self.profile().avatar_url, "https://steam/a.jpg")

    def test_accent_color(self):
        self.post_profile(accent_color="#22E0F0")
        self.assertEqual(self.profile().accent_color, "#22e0f0")
        self.assertContains(self.client.get(reverse("profile", args=["ana"])), "--violet: #22e0f0;")
        response = self.post_profile(accent_color="rojo")
        self.assertTrue(response.context["form"].errors["accent_color"])
        # El violeta de siempre se guarda vacío: si cambia el de la web, cambia también en el perfil.
        self.post_profile(accent_color="#9d5cff")
        self.assertEqual(self.profile().accent_color, "")

    def test_text_on_the_accent_color_is_readable(self):
        self.assertEqual(Profile(accent_color="").accent_text, "#ffffff")
        self.assertEqual(Profile(accent_color="#ffc84a").accent_text, "#0b0d18")
        self.assertEqual(Profile(accent_color="#ffc84a").accent_rgb, "255, 200, 74")

    def test_status_pronouns_location_and_background_on_the_profile(self):
        self.post_profile(
            status_emoji="🎮", status_text="Platineando Bloodborne", pronouns="elle", location="Madrid",
            background_mode="image", background=image_upload(), background_dim="40", layout="centered",
            avatar_shape="hexagon",
        )
        response = self.client.get(reverse("profile", args=["ana"]))
        for text in ("🎮", "Platineando Bloodborne", "elle", "Madrid", "--dim: 0.40", "profile-layout-centered",
                     "avatar-shape-hexagon"):
            self.assertContains(response, text)

    def test_fields_missing_from_the_post_keep_their_value(self):
        self.post_profile(layout="centered", avatar_shape="rounded", background_dim="30")
        self.post_profile(display_name="Ana")
        profile = self.profile()
        self.assertEqual((profile.display_name, profile.layout, profile.avatar_shape, profile.background_dim),
                         ("Ana", "centered", "rounded", 30))

    def test_social_links_are_normalized(self):
        self.post_profile(
            link_twitch="@Ana_Juega", link_x="https://x.com/ana_x", link_bluesky="https://bsky.app/profile/ana.bsky.social",
            link_discord="Ana.Discord", link_web="ana.dev",
        )
        self.assertEqual(self.profile().links, {
            "twitch": "Ana_Juega", "x": "ana_x", "bluesky": "ana.bsky.social", "discord": "ana.discord",
            "web": "https://ana.dev",
        })
        response = self.client.get(reverse("profile", args=["ana"]))
        for url in ("https://www.twitch.tv/Ana_Juega", "https://x.com/ana_x", "https://bsky.app/profile/ana.bsky.social",
                    "https://ana.dev"):
            self.assertContains(response, f'href="{url}"')
        self.assertContains(response, "ana.discord")

    def test_invalid_social_links_are_rejected(self):
        response = self.post_profile(link_twitch="no vale!", link_web="ftp://ana.dev")
        errors = response.context["form"].errors
        self.assertIn("link_twitch", errors)
        self.assertIn("link_web", errors)
        self.assertEqual(self.profile().links, {})

    def test_sections_order_and_visibility(self):
        FavoriteGame.objects.create(user=self.user, game=self.games[0])
        self.post_profile(
            secciones=["estadisticas", "favoritos", "jugando", "resena", "generos"],
            secciones_visibles=["estadisticas", "jugando"],
        )
        profile = self.profile()
        self.assertEqual(profile.visible_sections, ["estadisticas", "jugando"])
        self.assertEqual(profile.sections[1], ["favoritos", False])
        content = self.client.get(reverse("profile", args=["ana"])).content.decode()
        self.assertNotIn("Juegos favoritos", content)
        self.assertLess(content.index("Completados"), content.index("Jugando ahora"))

    def test_section_layout_ignores_unknown_and_repeated_keys(self):
        profile = Profile(sections=[["no-existe", True], "basura", ["jugando", False], ["jugando", True]])
        layout = profile.section_layout
        self.assertEqual(layout[0], ("jugando", "Jugando ahora", False))
        self.assertEqual([key for key, _, _ in layout[1:]], ["favoritos", "resena", "estadisticas", "generos"])

    def test_showcase_is_only_on_the_main_view(self):
        url = reverse("profile", args=["ana"])
        self.assertContains(self.client.get(url), "Jugando ahora")
        self.assertNotContains(self.client.get(url, {"estado": "playing"}), "Jugando ahora")
        self.assertNotContains(self.client.get(url, {"vista": "resenas"}), "Jugando ahora")

    def test_favorites_toggle_with_limit(self):
        for game in self.games[:5]:
            self.client.post(reverse("favorite_toggle", args=[game.pk]))
        self.assertEqual(
            list(self.user.favorite_games.values_list("game_id", flat=True)), [g.pk for g in self.games[:5]]
        )
        response = self.client.post(reverse("favorite_toggle", args=[self.games[5].pk]), HTTP_HX_REQUEST="true")
        self.assertContains(response, "Ya tienes 5 favoritos")
        self.assertEqual(self.user.favorite_games.count(), 5)
        response = self.client.post(reverse("favorite_toggle", args=[self.games[0].pk]), HTTP_HX_REQUEST="true")
        self.assertContains(response, "Añadir a mis favoritos")
        self.assertEqual(self.user.favorite_games.count(), 4)

    def test_game_page_shows_the_favorite_button(self):
        response = self.client.get(self.games[0].get_absolute_url())
        self.assertContains(response, "Añadir a mis favoritos")
        self.assertContains(response, "0 de 5")

    def test_reorder_and_remove_favorites_from_edit_profile(self):
        for position, game in enumerate(self.games[:4]):
            FavoriteGame.objects.create(user=self.user, game=game, position=position)
        g0, g1, g2, g3 = (game.pk for game in self.games[:4])
        self.post_profile(favoritos=[g2, g0, g3], quitar_favoritos=[g3])
        self.assertEqual(list(self.user.favorite_games.values_list("game_id", flat=True)), [g2, g0, g1])
        content = self.client.get(reverse("profile", args=["ana"])).content.decode()
        positions = [content.index(f"Juego {i}</p>") for i in (2, 0, 1)]
        self.assertEqual(positions, sorted(positions))

    def test_pinned_review_must_be_one_of_your_published_reviews(self):
        own = Review.objects.create(user=self.user, game=self.games[0], text="Me ha encantado.", published_at=timezone.now())
        draft = Review.objects.create(user=self.user, game=self.games[1], text="A medias.", is_draft=True)
        theirs = Review.objects.create(user=self.other, game=self.games[1], text="Del otro.", published_at=timezone.now())
        for pk in (theirs.pk, draft.pk):
            response = self.post_profile(pinned_review=pk)
            self.assertIn("pinned_review", response.context["form"].errors)
        self.post_profile(pinned_review=own.pk)
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, "Reseña destacada")
        self.assertContains(response, "Me ha encantado.")

    def test_banner_from_a_game_of_the_library(self):
        game = self.games[0]
        self.post_profile(banner_mode="game", banner_game=game.pk)
        profile = self.profile()
        self.assertEqual(profile.banner_url, "https://images.igdb.com/igdb/image/upload/t_1080p/co0.jpg")
        self.assertTrue(profile.banner_blurred)
        self.post_profile(banner_mode="game", banner_game=game.pk, banner_art="ar5l8")
        profile = self.profile()
        self.assertEqual(profile.banner_url, "https://images.igdb.com/igdb/image/upload/t_1080p/ar5l8.jpg")
        self.assertFalse(profile.banner_blurred)
        # Al cambiar de juego sin elegir imagen, la del juego anterior no vale: se usa la portada del nuevo.
        self.post_profile(banner_mode="game", banner_game=self.games[1].pk, banner_art="ar5l8")
        self.assertEqual(self.profile().banner_art, "")

    def test_banner_errors(self):
        errors = self.post_profile(banner_mode="game", banner_game=self.games[6].pk).context["form"].errors
        self.assertIn("banner_game", errors)  # no está en su biblioteca
        errors = self.post_profile(banner_mode="game", banner_game=self.games[0].pk, banner_art="<script>").context["form"].errors
        self.assertIn("banner_art", errors)
        errors = self.post_profile(banner_mode="image").context["form"].errors
        self.assertIn("banner", errors)
        errors = self.post_profile(background_mode="image").context["form"].errors
        self.assertIn("background", errors)

    def test_uploaded_banner_and_background_from_banner(self):
        self.post_profile(banner_mode="image", banner=image_upload(size=(3000, 1000)), background_mode="banner")
        profile = self.profile()
        with Image.open(profile.banner.path) as saved:
            self.assertEqual(saved.size, (2000, 667))
        self.assertEqual(profile.background_url, profile.banner_url)
        self.assertContains(self.client.get(reverse("profile", args=["ana"])), "profile-backdrop")

    def test_framing_is_saved_and_drawn_on_the_profile(self):
        self.post_profile(
            avatar=image_upload(), avatar_x="30", avatar_y="20.5", avatar_zoom="150",
            banner_mode="image", banner=image_upload(size=(1600, 900)), banner_fit="on",
            background_mode="banner", background_x="0", background_y="100", background_zoom="200",
        )
        profile = self.profile()
        self.assertEqual((profile.avatar_x, profile.avatar_y, profile.avatar_zoom), (30, 20.5, 150))
        self.assertTrue(profile.banner_fit)
        content = self.client.get(reverse("profile", args=["ana"])).content.decode()
        self.assertIn('style="--focus-x: 30%; --focus-y: 20.5%; --zoom: 1.5;"', content)
        self.assertIn("--focus-x: 0%; --focus-y: 100%; --zoom: 2;", content)
        # La cabecera, entera: encima de una copia suya desenfocada que llena el hueco.
        self.assertIn("profile-banner is-fit", content)
        self.assertEqual(content.count('class="img-fill"'), 1)

    def test_framing_values_are_checked(self):
        errors = self.post_profile(avatar_x="150", banner_zoom="50", background_zoom="900").context["form"].errors
        for name in ("avatar_x", "banner_zoom", "background_zoom"):
            self.assertIn(name, errors)

    def test_avatar_framing_only_applies_to_the_uploaded_photo(self):
        self.assertEqual(self.profile().avatar_image, {})
        self.post_profile(avatar=image_upload(), avatar_x="10", avatar_fit="on")
        image = self.profile().avatar_image
        self.assertEqual((image["framing"], image["fit"]), ("--focus-x: 10%; --focus-y: 50%; --zoom: 1;", True))
        SteamAccount.objects.create(user=self.user, steam_id="76561197960287930", avatar="https://steam/a.jpg")
        self.post_profile(avatar_source="steam", avatar_fit="on")
        self.assertEqual(self.profile().avatar_image, {"url": "https://steam/a.jpg", "framing": "", "fit": False})

    def test_edit_profile_has_a_framer_per_image(self):
        response = self.client.get(reverse("edit_profile"))
        for name in ("avatar", "banner", "background"):
            self.assertContains(response, f'data-framer="{name}"')
        # Las casillas de «Entera» no se cuelan entre los interruptores de privacidad.
        privacy = response.content.decode().split('id="privacidad"')[1].split("</section>")[0]
        self.assertEqual(privacy.count('class="switch-row"'), 2)

    @override_settings(IGDB_CLIENT_ID="", IGDB_CLIENT_SECRET="")
    def test_banner_arts_without_igdb(self):
        response = self.client.get(reverse("banner_arts"), {"banner_game": self.games[0].pk})
        self.assertContains(response, "Portada")
        self.assertContains(response, "Sin IGDB configurado")
        response = self.client.get(reverse("banner_arts"), {"banner_game": self.games[6].pk})
        self.assertContains(response, "Elige un juego")
        response = self.client.get(reverse("banner_arts"), {"banner_game": "abc"})
        self.assertContains(response, "Elige un juego")

    def test_banner_arts_from_igdb_are_cached(self):
        game = self.games[0]
        game.igdb_id = 1942
        game.save()
        with mock.patch.object(igdb, "is_configured", return_value=True), \
                mock.patch.object(igdb.IGDBClient, "__init__", return_value=None), \
                mock.patch.object(igdb.IGDBClient, "artworks", return_value=["ar1", "sc2"]) as artworks:
            for _ in range(2):
                response = self.client.get(reverse("banner_arts"), {"banner_game": game.pk})
        self.assertContains(response, "t_screenshot_med/ar1.jpg")
        self.assertContains(response, 'value="sc2"')
        artworks.assert_called_once_with(1942)

    def test_igdb_artworks_fills_with_screenshots(self):
        client = igdb.IGDBClient("id", "secret")
        answers = [[{"image_id": "ar1"}], [{"image_id": "sc1"}, {"image_id": "sc2"}]]
        with mock.patch.object(igdb.IGDBClient, "query", side_effect=answers) as query:
            self.assertEqual(client.artworks(1942, limit=3), ["ar1", "sc1", "sc2"])
        self.assertIn("where game = 1942; limit 2;", query.call_args_list[1].args[1])

    def test_banner_from_the_game_page_viewer(self):
        game = self.games[0]  # está en la biblioteca de ana
        game.media = {
            "arts": [{"id": "ar1", "type": 2, "w": 1920, "h": 1080}, {"id": "ar2", "type": 1, "w": 500, "h": 900}],
            "shots": [{"id": "sc1", "w": 1920, "h": 1080}],
        }
        game.save()
        profile = self.profile()
        profile.banner_x, profile.banner_zoom = 10, 150
        profile.save()
        url = reverse("banner_from_game", args=[game.pk])
        response = self.client.post(url, {"art": "sc1"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Hecho: es la nueva cabecera de tu perfil")
        profile = self.profile()
        self.assertEqual((profile.banner_mode, profile.banner_game, profile.banner_art), (Profile.BannerMode.GAME, game, "sc1"))
        self.assertEqual((profile.banner_x, profile.banner_zoom), (50, 100))  # el encuadre vuelve al centro
        # No valen una imagen vertical, una que no es del juego ni un juego que no está en la biblioteca.
        for game_pk, art in ((game.pk, "ar2"), (game.pk, "zz9"), (self.games[5].pk, "sc1")):
            response = self.client.post(reverse("banner_from_game", args=[game_pk]), {"art": art}, HTTP_HX_REQUEST="true")
            self.assertContains(response, 'role="alert"')
        self.assertEqual(self.profile().banner_art, "sc1")
        # Sin HTMX vuelve a la ficha con el aviso.
        response = self.client.post(url, {"art": "ar1"})
        self.assertRedirects(response, game.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(self.profile().banner_art, "ar1")
        self.client.logout()
        response = self.client.post(url, {"art": "sc1"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.profile().banner_art, "ar1")

    def preview(self, **data):
        """Pide la vista previa de Editar perfil con los datos dados (perfil público salvo que se diga otra cosa)."""
        return self.client.post(reverse("profile_preview"), {"is_public": "on", **data})

    def test_edit_profile_has_the_desktop_and_phone_preview(self):
        response = self.client.get(reverse("edit_profile"))
        self.assertContains(response, reverse("profile_preview"))
        # En el panel de al lado y en la ventana ampliada.
        self.assertContains(response, 'title="Tu perfil en un ordenador"', count=2)
        self.assertContains(response, 'title="Tu perfil en un móvil"', count=2)

    def test_preview_shows_the_changes_without_saving_them(self):
        response = self.preview(
            display_name="Ana Previa", status_text="Probando", accent_color="#22e0f0", layout="centered",
            link_twitch="ana_juega",
        )
        for text in ("Ana Previa", "Probando", "--violet: #22e0f0;", "profile-layout-centered",
                     "https://www.twitch.tv/ana_juega"):
            self.assertContains(response, text)
        profile = self.profile()
        self.assertEqual((profile.display_name, profile.accent_color, profile.layout, profile.links), ("", "", "classic", {}))

    def test_preview_is_what_others_see_and_cannot_be_used(self):
        response = self.preview()
        self.assertTrue(response.context["preview"])
        self.assertNotContains(response, "Vincular cuentas")
        self.assertContains(response, 'class="has-tabbar is-preview" inert')
        # Sin JavaScript: el marco no lo ejecutaría y el navegador se quejaría de cada script.
        self.assertNotContains(response, "<script")
        self.assertNotContains(response, "onchange=")

    def test_preview_keeps_saved_values_for_invalid_fields(self):
        response = self.preview(display_name="Ana Previa", link_twitch="no vale!", accent_color="rojo")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ana Previa")
        self.assertNotContains(response, "twitch.tv")
        self.assertContains(response, "--violet: #9d5cff;")

    def test_preview_shows_images_chosen_in_the_browser(self):
        avatar, banner = "blob:http://testserver/7f9c-avatar", "blob:http://testserver/0a1b-banner"
        response = self.preview(local_avatar=avatar, local_banner=banner, banner_mode="image", background_mode="banner")
        self.assertContains(response, f'src="{avatar}"')
        # La cabecera y, como el fondo es «la imagen de la cabecera», también el fondo de la página.
        self.assertContains(response, f'src="{banner}"', count=2)
        # Solo valen direcciones blob: del navegador.
        response = self.preview(
            local_avatar="javascript:alert(1)", local_banner="https://ejemplo.com/a.png", banner_mode="image"
        )
        self.assertNotContains(response, "javascript:")
        self.assertNotContains(response, "ejemplo.com")

    def test_preview_shows_the_framing(self):
        response = self.preview(local_avatar="blob:http://testserver/a", avatar_x="25", avatar_fit="on")
        self.assertContains(response, "--focus-x: 25%")
        self.assertContains(response, 'class="img-fill" src="blob:http://testserver/a"')

    def test_preview_follows_favorites_and_sections_of_the_form(self):
        for position, game in enumerate(self.games[:3]):
            FavoriteGame.objects.create(user=self.user, game=game, position=position)
        g0, g1, g2 = (game.pk for game in self.games[:3])
        response = self.preview(
            favoritos=[g2, g0, g1], quitar_favoritos=[g1],
            secciones=["favoritos", "jugando", "resena", "estadisticas", "generos"], secciones_visibles=["favoritos"],
        )
        favorites = response.content.decode().split('<ol class="favorites">')[1].split("</ol>")[0]
        positions = [favorites.find(f"Juego {i}</p>") for i in (2, 0, 1)]
        self.assertGreaterEqual(positions[0], 0)
        self.assertLess(positions[0], positions[1])
        self.assertEqual(positions[2], -1)
        self.assertNotContains(response, "Jugando ahora")
        # Nada se ha guardado.
        self.assertEqual(list(self.user.favorite_games.values_list("game_id", flat=True)), [g0, g1, g2])
        self.assertEqual(self.profile().sections, [])

    def test_preview_cuts_the_library_but_not_the_stats(self):
        with mock.patch.object(views, "PREVIEW_LIBRARY", 2):
            response = self.preview()
        self.assertEqual(len(response.context["entries"]), 2)
        self.assertEqual(response.context["stats"]["games"], 3)

    def test_private_profile_has_a_preview_too(self):
        response = self.client.post(reverse("profile_preview"), {})
        self.assertContains(response, "Perfil privado")

    def test_preview_does_not_use_up_pending_messages(self):
        for position, game in enumerate(self.games[:5]):
            FavoriteGame.objects.create(user=self.user, game=game, position=position)
        # Sin HTMX deja el aviso para la página siguiente.
        self.client.post(reverse("favorite_toggle", args=[self.games[5].pk]))
        self.assertNotContains(self.preview(), "Ya tienes 5 favoritos")
        self.assertContains(self.client.get(reverse("edit_profile")), "Ya tienes 5 favoritos")

    def test_preview_needs_login_and_post(self):
        url = reverse("profile_preview")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertRedirects(self.client.post(url), f"{reverse('login')}?next={url}")
