import io
import json
import shutil
import tempfile
import time
from datetime import date, datetime, timedelta, timezone as dt_timezone
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

from games import igdb, prices
from games.models import Game, GamePrice, Platform
from library.models import LibraryEntry, PlaySession, Review, WishlistItem
from social.models import Activity

from . import achievements, images, matching, notices, psn, steam, xbox
from .views import perfil
from .models import (
    Achievement, ExternalTitle, FavoriteGame, ImportedTitle, Notification, PlatformAccount, Profile, PSNAccount,
    SteamAccount, UnlockedAchievement, XboxAccount,
)

User = get_user_model()

# Las importaciones de Steam y de PlayStation también leen los logros y los trofeos, que serían llamadas de verdad: en
# todo el módulo no leen nada, salvo en AchievementsTests, que dice qué contestan (y prueba las funciones de verdad).
REAL_PLAYER_ACHIEVEMENTS, REAL_TROPHY_TITLES = steam.player_achievements, psn.trophy_titles
REAL_TROPHIES_BY_TITLE_ID, REAL_TROPHY_DETAILS = psn.trophies_by_title_id, psn.trophy_details
REAL_ACHIEVEMENT_SCHEMA, REAL_GLOBAL_PERCENTAGES = steam.achievement_schema, steam.global_percentages
NETWORK_PATCHES = [
    mock.patch.object(steam, "player_achievements", return_value=None),
    mock.patch.object(steam, "achievement_schema", return_value=None),
    mock.patch.object(steam, "global_percentages", return_value=None),
    mock.patch.object(psn, "trophy_titles", return_value=[]),
    mock.patch.object(psn, "trophies_by_title_id", return_value={}),
    mock.patch.object(psn, "trophy_details", return_value=None),
    # Después de importar se piden los precios de los juegos que nunca los han tenido: tampoco, salvo en las pruebas
    # que lo miran (con REAL_FETCH_MISSING y las tiendas simuladas).
    mock.patch.object(prices, "fetch_missing", return_value=0),
]
REAL_FETCH_MISSING = prices.fetch_missing


def setUpModule():
    """Corta las lecturas de logros y trofeos de todo el módulo (NETWORK_PATCHES)."""
    for patcher in NETWORK_PATCHES:
        patcher.start()


def tearDownModule():
    """Deshace los parches de setUpModule."""
    for patcher in NETWORK_PATCHES:
        patcher.stop()


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
        self.assertEqual(result, {"added": 2, "updated": 0, "unmatched": 1, "private": [], "achievements": 0})
        entries = {e.game: e for e in LibraryEntry.objects.filter(user=self.user)}
        self.assertEqual(entries[self.portal].status, LibraryEntry.Status.PLAYED)
        self.assertEqual([(item["key"], item["hours"]) for item in entries[self.portal].hours_by_platform], [("steam", 15.0)])
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

    def test_linking_another_account_is_not_a_session(self):
        now = int(time.time())
        with self.owned_by_account({self.account.steam_id: [
            {"appid": 620, "playtime_forever": 600, "rtime_last_played": now - 3 * 86400},
            {"appid": 1145360, "playtime_forever": 0},
        ]}):
            steam.import_library(self.user, include_unplayed=True)
        second = SteamAccount.objects.create(user=self.user, steam_id="76561190000000002")
        with self.owned_by_account({
            self.account.steam_id: [
                {"appid": 620, "playtime_forever": 630, "rtime_last_played": now - 3600},
                {"appid": 1145360, "playtime_forever": 0},
            ],
            second.steam_id: [{"appid": 620, "playtime_forever": 326}, {"appid": 1145360, "playtime_forever": 2150}],
        }):
            steam.import_library(self.user)
        # Lo que trae la cuenta nueva es de antes: de Portal 2 solo cuentan los 30 min jugados en la primera, y de
        # Hades, que en la primera estaba sin abrir, nada.
        self.assertEqual(list(PlaySession.objects.values_list("game", "minutes")), [(self.portal.pk, 30)])
        hours = dict(LibraryEntry.objects.filter(user=self.user).values_list("game", "steam_playtime_minutes"))
        self.assertEqual(hours, {self.portal.pk: 956, self.hades.pk: 2150})
        # En la siguiente importación la segunda cuenta ya no es nueva: lo que suba en ella sí es una sesión.
        with self.owned_by_account({
            self.account.steam_id: [{"appid": 620, "playtime_forever": 630, "rtime_last_played": now - 3600}],
            second.steam_id: [{"appid": 620, "playtime_forever": 326}, {"appid": 1145360, "playtime_forever": 2210}],
        }):
            steam.import_library(self.user)
        self.assertEqual(PlaySession.objects.get(game=self.hades).minutes, 60)

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
        self.assertEqual(result, {"added": 0, "updated": 1, "unmatched": 0, "private": [], "achievements": 0})
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
        # La eShop de Nintendo no es una tienda de PC: no cuenta como «más barato fuera de Steam».
        GamePrice.objects.create(
            game=self.hades, source=GamePrice.Source.NINTENDO, shop_id=GamePrice.NINTENDO_SHOP_ID,
            shop_name="Nintendo eShop", price=Decimal("11.99"), regular_price=Decimal("24.99"), currency="EUR", cut=52,
            url="https://ec.nintendo.com/ES/es/titles/70010000033132",
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

    def test_sync_button_asks_prices_of_library_games_never_searched(self):
        Game.objects.filter(pk=self.hades.pk).update(prices_updated_at=timezone.now())
        self.client.force_login(self.user)
        with self.owned({"appid": 620, "playtime_forever": 60}, {"appid": 1145360, "playtime_forever": 60}), \
                self.wishes(), mock.patch.object(steam, "refresh_account"), \
                mock.patch.object(prices, "fetch_missing", return_value=1) as fetch:
            self.client.post(reverse("steam_import"))
        # Hades ya tenía sus precios buscados: solo se piden los de Portal 2.
        fetch.assert_called_once()
        self.assertEqual(list(fetch.call_args.args[0]), [self.portal])

    @override_settings(ITAD_API_KEY="key")
    def test_import_works_even_if_isthereanydeal_does_not_answer(self):
        Game.objects.filter(pk=self.portal.pk).update(store_ids={"steam": [620]})
        self.client.force_login(self.user)
        with self.owned({"appid": 620, "playtime_forever": 60}), self.wishes(), \
                mock.patch.object(steam, "refresh_account"), mock.patch.object(prices, "fetch_missing", REAL_FETCH_MISSING), \
                mock.patch("games.prices.requests.post", side_effect=prices.requests.ConnectionError("caído")), \
                self.assertLogs("games.prices", "ERROR"):
            response = self.client.post(reverse("steam_import"), follow=True)
        self.assertContains(response, "Steam: 1 juego añadido")
        self.assertTrue(LibraryEntry.objects.filter(user=self.user, game=self.portal).exists())
        # Sigue sin buscar: se pedirán en la próxima importación, al abrir su ficha o por la noche.
        self.assertIsNone(Game.objects.get(pk=self.portal.pk).prices_updated_at)


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


def psn_title(name, duration, days_ago=400, category="ps4_game", title_id=None):
    """Juego tal como lo devuelve la lista de juegos jugados de PSN, con su ID si se da; la primera partida fue 30 días
    antes de la última."""
    played = timezone.now() - timedelta(days=days_ago)
    return {
        "name": name, "localizedName": name, "category": category,
        "playDuration": duration, "lastPlayedDateTime": played.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "firstPlayedDateTime": (played - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        **({"titleId": title_id} if title_id else {}),
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

    def test_sync_view_asks_prices_of_the_new_games(self):
        self.client.force_login(self.user)
        with self.fake_psn([psn_title("Hades", "PT3H")]), mock.patch.object(prices, "fetch_missing") as fetch:
            self.client.post(reverse("psn_sync"), {"npsso": NPSSO})
        self.assertEqual(list(fetch.call_args.args[0]), [self.hades])

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

    def test_callback_asks_prices_of_the_new_games(self):
        self.client.post(reverse("xbox_link"))
        state = self.client.session["xbox_oauth_state"]
        titles = [{"titleId": "1", "name": "Halo Infinite", "type": "Game", "titleHistory": {}}]
        with self.fake_xbox(titles, {"1": 90}), mock.patch.object(prices, "fetch_missing") as fetch:
            self.client.get(reverse("xbox_callback"), {"code": "c", "state": state})
        self.assertEqual(list(fetch.call_args.args[0]), [self.halo])

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


def match_one(name, platforms=()):
    """El juego que le toca a un solo título (None si no se encuentra)."""
    return matching.match_by_name({"x": (name, platforms)}).get("x")


@override_settings(IGDB_CLIENT_ID="", IGDB_CLIENT_SECRET="")
class NameMatchingTests(TestCase):
    def test_name_key_ignores_how_a_name_is_written(self):
        for one, other in (
            ("WATCH_DOGS 2", "Watch Dogs 2"),
            ("Tom Clancy’s Ghost Recon Breakpoint", "Tom Clancy's Ghost Recon: Breakpoint"),
            ("Ghostrunner2", "Ghostrunner 2"),
            ("God of War Ragnarök", "God of War: Ragnarok"),
            ("DOOM™", "Doom"),
        ):
            self.assertEqual(matching.name_key(one), matching.name_key(other))
        self.assertNotEqual(matching.name_key("Hades"), matching.name_key("Hades II"))
        self.assertEqual(matching.name_key("!!!"), "")

    def test_signs_do_not_stop_a_match(self):
        game = Game.objects.create(name="Infamous: Second Son", slug="infamous-second-son")
        self.assertEqual(match_one("inFAMOUS Second Son"), game)
        self.assertIsNone(match_one("!!!"))

    def test_other_names_find_the_game(self):
        siege = Game.objects.create(
            name="Rainbow Six Siege", slug="rainbow-six-siege", search_names="tom clancy's rainbow six siege | r6 siege",
        )
        sims = Game.objects.create(name="The Sims 4", slug="the-sims-4", name_es="Los Sims 4")
        self.assertEqual(match_one("Tom Clancy’s Rainbow Six Siege"), siege)
        self.assertEqual(match_one("Los Sims 4"), sims)
        self.assertIsNone(match_one("Rainbow Six"))

    def test_own_name_beats_another_games_other_name(self):
        real = Game.objects.create(name="Control", slug="control", popularity=1)
        Game.objects.create(name="Control Ultimate", slug="control-ultimate", popularity=900, search_names="control")
        self.assertEqual(match_one("Control"), real)

    def test_other_name_on_the_right_platform_beats_own_name_on_another(self):
        ps4 = Platform.objects.create(name="PlayStation 4", slug="ps4")
        atari = Platform.objects.create(name="Atari 2600", slug="atari")
        old = Game.objects.create(name="Ghostbusters", slug="ghostbusters-1984", popularity=900)
        old.platforms.set([atari])
        remaster = Game.objects.create(name="Ghostbusters Remastered", slug="ghostbusters-remastered", search_names="ghostbusters")
        remaster.platforms.set([ps4])
        self.assertEqual(match_one("Ghostbusters", ["PlayStation 4"]), remaster)
        self.assertEqual(match_one("Ghostbusters", ["Atari 2600"]), old)

    def test_other_name_on_another_platform_is_not_enough(self):
        atari = Platform.objects.create(name="Atari 2600", slug="atari")
        game = Game.objects.create(name="Pitfall Clásico", slug="pitfall-clasico", search_names="pitfall")
        game.platforms.set([atari])
        self.assertIsNone(match_one("Pitfall", ["PlayStation 4"]))
        self.assertEqual(match_one("Pitfall", ["Atari 2600"]), game)
        # Sin plataformas conocidas, cualquiera vale.
        self.assertEqual(match_one("Pitfall"), game)

    def test_same_name_already_matched_on_another_platform_gives_the_game(self):
        ps4 = Platform.objects.create(name="PlayStation 4", slug="ps4")
        witcher = Game.objects.create(name="The Witcher 3: Wild Hunt", slug="the-witcher-3")
        witcher.platforms.set([ps4])
        ExternalTitle.objects.create(platform="steam", external_id="292030", name="The Witcher 3: Wild Hunt — Remastered™", game=witcher)
        self.assertEqual(match_one("The Witcher 3: Wild Hunt — Remastered", ["PlayStation 4"]), witcher)
        # Si ese juego no sale en la plataforma, no basta.
        self.assertIsNone(match_one("The Witcher 3: Wild Hunt — Remastered", ["Nintendo Switch"]))

    def test_own_name_beats_the_same_name_on_another_platform(self):
        original = Game.objects.create(name="Resident Evil 4 HD", slug="re4-hd")
        remake = Game.objects.create(name="Resident Evil 4", slug="re4-remake")
        ExternalTitle.objects.create(platform="steam", external_id="254700", name="Resident Evil 4", game=original)
        self.assertEqual(match_one("Resident Evil 4"), remake)

    def test_year_in_the_title_picks_the_game_of_that_year(self):
        original = Game.objects.create(name="Resident Evil 4", slug="re4", first_release_date=date(2005, 1, 11), popularity=10)
        remake = Game.objects.create(name="Resident Evil 4", slug="re4-remake", first_release_date=date(2023, 3, 24), popularity=900)
        self.assertEqual(match_one("resident evil 4 (2005)"), original)
        self.assertEqual(match_one("Resident Evil 4 (2022)"), remake)
        self.assertEqual(match_one("Resident Evil 4"), remake)

    def test_region_at_the_end_is_ignored(self):
        game = Game.objects.create(name="LittleBigPlanet 3", slug="littlebigplanet-3")
        self.assertEqual(match_one("LittleBigPlanet3 (LATAM)"), game)

    def test_launchers_stay_unmatched(self):
        Game.objects.create(name="Call of Duty", slug="call-of-duty")
        self.assertIsNone(match_one("Call of Duty"))


def igdb_data(igdb_id, name, **extra):
    """Juego tal como lo devuelve IGDB, con lo mínimo para guardarlo."""
    return {"id": igdb_id, "name": name, "slug": name.casefold().replace(" ", "-").replace(":", ""), **extra}


class NameMatchingWithIGDBTests(TestCase):
    def setUp(self):
        """Simula IGDB: de partida no encuentra nada por nombre exacto ni buscando."""
        self.exact = self.patch("games_by_names", [])
        self.search = self.patch("search", [])
        self.patch("__init__", None)
        patcher = mock.patch.object(igdb, "is_configured", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch(self, method, answer):
        """Cambia un método de IGDBClient por uno que devuelve answer y lo deshace al acabar el test."""
        patcher = mock.patch.object(igdb.IGDBClient, method, return_value=answer)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_search_keeps_only_the_results_with_that_name(self):
        self.search.return_value = [igdb_data(10, "Sniper Elite 3"), igdb_data(11, "Sniper: Ghost Warrior 3")]
        game = match_one("Sniper Ghost Warrior 3")
        self.assertEqual(game.igdb_id, 11)
        self.assertFalse(Game.objects.filter(igdb_id=10).exists())

    def test_search_ignores_results_on_other_platforms(self):
        # El caso real: en IGDB, «The Witcher 3: Wild Hunt Remastered» es una actualización de 2026 sin versión de PS4.
        self.search.return_value = [igdb_data(
            15, "The Witcher 3: Wild Hunt Remastered", platforms=[{"id": 1, "name": "PlayStation 5", "slug": "ps5"}],
        )]
        self.assertIsNone(match_one("The Witcher 3: Wild Hunt — Remastered", ["PlayStation 4"]))
        self.assertFalse(Game.objects.filter(igdb_id=15).exists())
        self.assertEqual(match_one("The Witcher 3: Wild Hunt — Remastered", ["PlayStation 5"]).igdb_id, 15)

    def test_search_also_looks_at_the_other_names_igdb_gives(self):
        self.search.return_value = [igdb_data(12, "Outlast II", alternative_names=[{"name": "Outlast 2"}])]
        self.assertEqual(match_one("Outlast 2").igdb_id, 12)

    def test_exact_name_in_igdb_beats_another_games_other_name(self):
        Game.objects.create(name="Alien: Isolation", slug="alien-isolation", search_names="alien")
        self.exact.return_value = [igdb_data(13, "Alien")]
        self.assertEqual(match_one("Alien").igdb_id, 13)
        self.search.assert_not_called()

    def test_year_or_region_is_left_out_when_asking_igdb(self):
        self.exact.return_value = [igdb_data(14, "Lords of the Fallen", first_release_date=1414972800)]
        self.assertEqual(match_one("Lords of the Fallen (2014)").igdb_id, 14)
        self.assertIn("Lords of the Fallen", self.exact.call_args.args[0])

    def test_searches_have_a_limit(self):
        matching.match_by_name({i: (f"Juego Raro {i}", ()) for i in range(matching.MAX_SEARCHES + 5)})
        self.assertEqual(self.search.call_count, matching.MAX_SEARCHES)


@override_settings(IGDB_CLIENT_ID="", IGDB_CLIENT_SECRET="")
class RematchTitlesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")

    def rematch(self, *args):
        """Lanza el comando y devuelve lo que escribe."""
        out = io.StringIO()
        call_command("rematch_titles", *args, stdout=out)
        return out.getvalue()

    def import_title(self, user, duration="PT10H", account_id="111"):
        """Importa «inFAMOUS Second Son» de PlayStation para user, cuando aún no se encuentra en el catálogo."""
        return psn_import(user, [psn_title("inFAMOUS Second Son", duration, title_id="CUSA00001_00")], account_id=account_id)

    def test_hours_reach_everyones_library_without_syncing(self):
        self.import_title(self.ana)
        self.import_title(self.luis, "PT2H", account_id="222")
        self.assertFalse(LibraryEntry.objects.exists())
        game = Game.objects.create(name="Infamous: Second Son", slug="infamous-second-son")

        output = self.rematch()

        self.assertIn("«inFAMOUS Second Son» es Infamous: Second Son (12.0 h)", output)
        self.assertIn("1 título emparejado de 1 pendiente", output)
        title = ExternalTitle.objects.get()
        self.assertEqual((title.game, title.source), (game, ExternalTitle.Source.AUTO))
        entry = LibraryEntry.objects.get(user=self.ana, game=game)
        self.assertEqual((entry.psn_playtime_minutes, entry.status), (600, LibraryEntry.Status.PLAYED))
        self.assertEqual(LibraryEntry.objects.get(user=self.luis, game=game).psn_playtime_minutes, 120)
        # No es algo que hayan hecho ahora: ni actividad ni sesiones.
        self.assertFalse(Activity.objects.exists())
        self.assertFalse(PlaySession.objects.exists())

    def test_hours_add_up_with_a_game_already_in_the_library(self):
        game = Game.objects.create(name="Infamous: Second Son", slug="infamous-second-son")
        ExternalTitle.objects.create(platform="psn", external_id="CUSA00001_00", name="inFAMOUS Second Son")
        ImportedTitle.objects.create(user=self.ana, title=ExternalTitle.objects.get(), minutes=600)
        LibraryEntry.objects.create(
            user=self.ana, game=game, status=LibraryEntry.Status.COMPLETED, steam_playtime_minutes=60,
        )
        self.rematch()
        entry = LibraryEntry.objects.get(user=self.ana, game=game)
        self.assertEqual((entry.status, entry.playtime_minutes), (LibraryEntry.Status.COMPLETED, 660))

    def test_what_someone_decided_is_left_alone(self):
        Game.objects.create(name="Herramienta", slug="herramienta")
        Game.objects.create(name="Bloqueado", slug="bloqueado")
        Game.objects.create(name="Oculto", slug="oculto")
        by_hand = ExternalTitle.objects.create(
            platform="psn", external_id="A", name="Herramienta", source=ExternalTitle.Source.USER,
        )
        locked = ExternalTitle.objects.create(platform="psn", external_id="B", name="Bloqueado", locked=True)
        hidden = ExternalTitle.objects.create(platform="psn", external_id="C", name="Oculto")
        for title in (by_hand, locked):
            ImportedTitle.objects.create(user=self.ana, title=title, minutes=60)
        ImportedTitle.objects.create(user=self.ana, title=hidden, minutes=60, hidden=True)

        self.assertIn("0 títulos emparejados de 0 pendientes", self.rematch())
        self.assertFalse(ExternalTitle.objects.filter(game__isnull=False).exists())
        self.assertFalse(LibraryEntry.objects.exists())

    def test_steam_titles_are_not_searched_by_name(self):
        Game.objects.create(name="Herramienta", slug="herramienta")
        title = ExternalTitle.objects.create(platform="steam", external_id="1", name="Herramienta")
        ImportedTitle.objects.create(user=self.ana, title=title, minutes=60)
        self.rematch()
        title.refresh_from_db()
        self.assertIsNone(title.game)

    def test_dry_run_saves_nothing(self):
        self.import_title(self.ana)
        Game.objects.create(name="Infamous: Second Son", slug="infamous-second-son")
        output = self.rematch("--dry-run")
        self.assertIn("es Infamous: Second Son", output)
        self.assertIn("no se ha guardado nada", output)
        self.assertIsNone(ExternalTitle.objects.get().game)
        self.assertFalse(LibraryEntry.objects.exists())

    def test_what_is_still_missing_is_listed(self):
        psn_import(self.ana, [psn_title("Trilogía Mafia", "PT3H", title_id="CUSA00002_00")])
        self.assertIn("Sigue sin emparejar: PlayStation: «Trilogía Mafia»", self.rematch())


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
            secciones=[
                "estadisticas", "favoritos", "jugando", "resena", "generos", "listas", "tier-lists", "logros", "retos",
            ],
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
        self.assertEqual(
            [key for key, _, _ in layout[1:]],
            ["favoritos", "resena", "estadisticas", "generos", "listas", "tier-lists", "logros", "retos"],
        )

    def test_showcase_is_only_on_the_main_view(self):
        url = reverse("profile", args=["ana"])
        self.assertContains(self.client.get(url), "Jugando ahora")
        self.assertNotContains(self.client.get(url, {"estado": "playing"}), "Jugando ahora")
        self.assertNotContains(self.client.get(url, {"vista": "resenas"}), "Jugando ahora")
        self.assertNotContains(self.client.get(url, {"vista": "listas"}), "Jugando ahora")

    def test_library_cards_show_the_stars_of_the_rating(self):
        LibraryEntry.objects.filter(user=self.user, game=self.games[0]).update(rating=9)
        response = self.client.get(reverse("profile", args=["ana"]), {"estado": "playing"})
        self.assertContains(response, 'class="stars stars-sm" role="img" aria-label="4,5 estrellas"')

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
        # Las casillas de «Entera» no se cuelan entre los interruptores de privacidad ni entre los de los avisos.
        page = response.content.decode()
        privacy = page.split('id="privacidad"')[1].split("</section>")[0]
        # Solo los dos de privacidad: el perfil público y el diario de partidas visible.
        self.assertEqual(privacy.count('class="switch-row"'), 2)
        notice_section = page.split('id="avisos"')[1].split("</section>")[0]
        self.assertEqual(notice_section.count('class="switch-row'), len(notices.GROUPS) + 1)  # y el del correo

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
        with mock.patch.object(perfil, "PREVIEW_LIBRARY", 2):
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


def trophy_title(name, defined, earned):
    """Juego con trofeos tal como lo devuelve PSN: los que tiene y los conseguidos, de bronce a platino."""
    return {
        "trophyTitleName": name,
        "definedTrophies": dict(zip(psn.TROPHY_GRADES, defined)),
        "earnedTrophies": dict(zip(psn.TROPHY_GRADES, earned)),
    }


def steam_achievements(unlocked, total, when=None):
    """Logros de un juego como los da steam.player_achievements: total, con los unlocked primeros conseguidos, el
    primero en when (ahora, si no se da) y cada uno de los demás un día antes."""
    when = when or timezone.now()
    return [(f"ACH_{i}", i < unlocked, when - timedelta(days=i) if i < unlocked else None) for i in range(total)]


def steam_schema(total, hidden=()):
    """Logros de un juego como los da steam.achievement_schema: total, ocultos (y sin descripción) los de hidden."""
    return [
        {
            "api_name": f"ACH_{i}", "name": f"Logro {i}", "description": "" if i in hidden else f"Consigue {i} cosas",
            "icon": f"https://cdn.example.com/{i}.jpg", "icon_locked": f"https://cdn.example.com/{i}-gris.jpg",
            "hidden": i in hidden,
        }
        for i in range(total)
    ]


def steam_percentages(total):
    """Rareza de los logros de steam_schema(total): el primero lo tiene el 90 % y cada uno, 1,7 puntos menos."""
    return {f"ACH_{i}": round(90 - i * 1.7, 1) for i in range(total)}


# Cuántos logros tiene cada juego de AchievementsTests en Steam, por su ID de app.
SCHEMA_TOTALS = {620: 51, 1145360: 2, 504230: 31}


@override_settings(STEAM_API_KEY="key", IGDB_CLIENT_ID="", IGDB_CLIENT_SECRET="")
class AchievementsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        SteamAccount.objects.create(user=cls.user, steam_id="76561197960287930", persona="Ana")
        cls.portal = Game.objects.create(name="Portal 2", slug="portal-2", steam_app_id=620)
        cls.hades = Game.objects.create(name="Hades", slug="hades", steam_app_id=1145360)
        cls.celeste = Game.objects.create(name="Celeste", slug="celeste", steam_app_id=504230)

    def setUp(self):
        """Steam da los logros de cada juego (SCHEMA_TOTALS) y su rareza; cada prueba puede cambiar lo que contesta con
        self.schema y self.percentages."""
        self.schema = self.patch("achievement_schema", lambda app_id: steam_schema(SCHEMA_TOTALS.get(int(app_id), 0)))
        self.percentages = self.patch(
            "global_percentages", lambda app_id: steam_percentages(SCHEMA_TOTALS.get(int(app_id), 0)),
        )

    def patch(self, name, fake):
        """Sustituye steam.<name> por fake mientras dura la prueba y devuelve el simulacro."""
        patcher = mock.patch.object(steam, name, side_effect=fake)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def import_steam(self, games, achievements):
        """Importa de Steam games (como los da GetOwnedGames) con los logros de achievements ({appid: lo que da
        player_achievements})."""
        with mock.patch.object(steam, "owned_games", return_value=games), mock.patch.object(
            steam, "player_achievements", side_effect=lambda steam_id, app_id: achievements.get(int(app_id)),
        ) as read:
            result = steam.import_library(self.user)
        return result, read

    def achievements_of(self, game):
        """(conseguidos, totales, platino) del título importado de game."""
        row = ImportedTitle.objects.get(user=self.user, title__game=game)
        return row.achievements_unlocked, row.achievements_total, row.platinum

    def steam_games(self):
        """Los tres juegos jugados en Steam: Hades hace un día, Celeste hace dos y Portal 2 hace tres."""
        now = int(time.time())
        return [
            {"appid": 620, "playtime_forever": 900, "rtime_last_played": now - 3 * 86400},
            {"appid": 1145360, "playtime_forever": 60, "rtime_last_played": now - 86400},
            {"appid": 504230, "playtime_forever": 30, "rtime_last_played": now - 2 * 86400},
        ]

    def test_steam_reads_what_was_played_since_last_time(self):
        games = self.steam_games()
        # Hades no tiene logros y los de Celeste no se pueden leer.
        result, read = self.import_steam(games, {620: steam_achievements(10, 51), 1145360: []})
        self.assertEqual((result["achievements"], read.call_count), (2, 3))
        self.assertEqual(self.achievements_of(self.portal), (10, 51, False))
        self.assertEqual(self.achievements_of(self.hades), (0, 0, False))
        self.assertIsNone(ImportedTitle.objects.get(title__game=self.celeste).achievements_at)
        # La vez siguiente solo se pide lo que no se pudo leer y lo que se ha vuelto a jugar.
        games[0]["rtime_last_played"] = int(time.time()) + 60
        result, read = self.import_steam(games, {620: steam_achievements(12, 51), 504230: steam_achievements(5, 31)})
        self.assertEqual(sorted(call.args[1] for call in read.call_args_list), ["504230", "620"])
        self.assertEqual(self.achievements_of(self.portal), (12, 51, False))
        self.assertEqual(self.achievements_of(self.celeste), (5, 31, False))

    def test_steam_reads_the_latest_first_and_the_best_account(self):
        self.import_steam(self.steam_games(), {})
        with mock.patch.object(steam, "player_achievements", return_value=steam_achievements(1, 2)) as read:
            self.assertEqual(steam.import_achievements(self.user, limit=1), 1)
        self.assertEqual(read.call_args.args[1], "1145360")
        # Con dos cuentas de Steam, cuenta la que más lleva de cada juego.
        SteamAccount.objects.create(user=self.user, steam_id="76561197960287931", persona="Ana 2")
        with mock.patch.object(
            steam, "player_achievements",
            side_effect=lambda steam_id, app_id: steam_achievements(3 if steam_id.endswith("31") else 1, 31),
        ):
            self.assertEqual(steam.import_achievements(self.user, limit=None), 2)
        self.assertEqual(self.achievements_of(self.celeste), (3, 31, False))
        self.assertEqual(self.achievements_of(self.hades), (1, 2, False))

    def test_steam_answers(self):
        answers = [
            fake_response(data={"playerstats": {"success": True, "achievements": [
                {"apiname": "ACH_A", "achieved": 1, "unlocktime": 1700000000},
                {"apiname": "ACH_B", "achieved": 0, "unlocktime": 0},
                # De algunos logros muy antiguos Steam no guarda la fecha.
                {"apiname": "ACH_C", "achieved": 1, "unlocktime": 0},
            ]}}),
            fake_response(status=400, data={"playerstats": {"success": False, "error": "Requested app has no stats"}}),
            fake_response(status=403, data={"playerstats": {"success": False, "error": "Profile is not public"}}),
            steam.requests.ConnectionError("caído"),
        ]
        with mock.patch("accounts.steam.requests.get", side_effect=answers):
            results = [REAL_PLAYER_ACHIEVEMENTS("76561197960287930", 620) for _ in answers]
        unlocked_at = datetime(2023, 11, 14, 22, 13, 20, tzinfo=dt_timezone.utc)
        self.assertEqual(results, [[("ACH_A", True, unlocked_at), ("ACH_B", False, None), ("ACH_C", True, None)], [], None, None])

    def test_steam_schema_and_rarity_answers(self):
        rejected = fake_response(status=400)
        rejected.raise_for_status.side_effect = steam.requests.HTTPError("400")
        answers = [
            fake_response(data={"game": {"availableGameStats": {"achievements": [
                # Los iconos llegan con la dirección antigua de Steam, que ya no tiene los nuevos.
                {"name": "ACH_A", "displayName": "Primera sangre", "description": "Gana una partida.", "hidden": 0,
                 "icon": "https://steamcdn-a.akamaihd.net/steamcommunity/public/images/apps/620/a.jpg",
                 "icongray": "https://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps/620/a-gris.jpg"},
                # De los ocultos Steam no da la descripción.
                {"name": "ACH_B", "displayName": "El final", "hidden": 1, "icon": "https://cdn.example.com/b.jpg"},
            ]}}}),
            fake_response(data={"game": {}}),
            rejected,
            steam.requests.ConnectionError("caído"),
        ]
        with mock.patch("accounts.steam.requests.get", side_effect=answers) as get:
            results = [REAL_ACHIEVEMENT_SCHEMA(620) for _ in answers]
        self.assertEqual(get.call_args_list[0].kwargs["params"]["l"], "spanish")
        self.assertEqual(
            (results[0][0]["icon"], results[0][0]["icon_locked"]),
            (f"{steam.ICON_URL}620/a.jpg", f"{steam.ICON_URL}620/a-gris.jpg"),
        )
        self.assertEqual(results[0][1], {
            "api_name": "ACH_B", "name": "El final", "description": "", "icon": "https://cdn.example.com/b.jpg",
            "icon_locked": "", "hidden": True,
        })
        self.assertEqual(results[1:], [[], None, None])
        # La rareza llega como texto y no necesita la clave.
        answers = [
            fake_response(data={"achievementpercentages": {"achievements": [
                {"name": "ACH_A", "percent": "63.14"}, {"name": "ACH_B", "percent": 0.4},
            ]}}),
            rejected,
        ]
        with mock.patch("accounts.steam.requests.get", side_effect=answers) as get:
            results = [REAL_GLOBAL_PERCENTAGES(620) for _ in answers]
        self.assertNotIn("key", get.call_args.kwargs["params"])
        self.assertEqual(results, [{"ACH_A": 63.1, "ACH_B": 0.4}, None])

    def test_steam_saves_each_achievement_with_its_date_and_rarity(self):
        when = timezone.now() - timedelta(days=3)
        self.import_steam(self.steam_games(), {620: steam_achievements(2, 51, when)})
        title = ExternalTitle.objects.get(platform=ExternalTitle.Platform.STEAM, external_id="620")
        self.assertIsNotNone(title.achievements_read_at)
        self.assertEqual(title.achievements.count(), 51)
        first, last = title.achievements.get(api_name="ACH_0"), title.achievements.get(api_name="ACH_50")
        self.assertEqual((first.name, first.description, first.percent, first.rarity), ("Logro 0", "Consigue 0 cosas", 90, ""))
        self.assertEqual((last.position, last.percent, last.rarity), (50, 5, "Muy raro"))
        unlocks = UnlockedAchievement.objects.filter(imported__user=self.user)
        self.assertEqual(
            sorted(unlocks.values_list("achievement__api_name", "unlocked_at")),
            [("ACH_0", when), ("ACH_1", when - timedelta(days=1))],
        )

    def test_steam_reads_the_achievements_of_each_game_once(self):
        games = self.steam_games()
        self.import_steam(games, {620: steam_achievements(2, 51)})
        self.assertEqual((self.schema.call_count, self.percentages.call_count), (1, 1))
        # Otra persona con el mismo juego no los vuelve a pedir...
        bea = User.objects.create_user("bea", "bea@example.com", "x")
        SteamAccount.objects.create(user=bea, steam_id="76561197960287999", persona="Bea")
        with mock.patch.object(steam, "owned_games", return_value=games[:1]), mock.patch.object(
            steam, "player_achievements", return_value=steam_achievements(5, 51),
        ):
            steam.import_library(bea)
        self.assertEqual(self.schema.call_count, 1)
        self.assertEqual(UnlockedAchievement.objects.filter(imported__user=bea).count(), 5)
        # ... hasta que tienen más de ACHIEVEMENTS_INFO_DAYS días, cuando se vuelve a jugar.
        ExternalTitle.objects.filter(external_id="620").update(
            achievements_read_at=timezone.now() - timedelta(days=steam.ACHIEVEMENTS_INFO_DAYS + 1),
        )
        games[0]["rtime_last_played"] = int(time.time()) + 60
        self.import_steam(games, {620: steam_achievements(2, 51)})
        self.assertEqual((self.schema.call_count, self.percentages.call_count), (2, 2))

    def test_steam_keeps_achievements_that_its_game_does_not_list(self):
        self.import_steam(self.steam_games(), {620: steam_achievements(52, 52)})
        self.assertEqual(self.schema.call_count, 1)
        self.assertEqual(Achievement.objects.get(title__external_id="620", api_name="ACH_51").name, "ACH_51")
        self.assertEqual(self.achievements_of(self.portal), (52, 52, False))
        self.assertEqual(UnlockedAchievement.objects.filter(imported__user=self.user).count(), 52)

    def test_steam_waits_until_it_knows_the_achievements_of_the_game(self):
        self.schema.side_effect = lambda app_id: None
        games = self.steam_games()
        result, _ = self.import_steam(games, {620: steam_achievements(10, 51)})
        self.assertEqual(result["achievements"], 0)
        self.assertIsNone(ImportedTitle.objects.get(user=self.user, title__game=self.portal).achievements_at)
        self.assertFalse(UnlockedAchievement.objects.exists())
        # Con Steam respondiendo, la siguiente vez se guardan.
        self.schema.side_effect = lambda app_id: steam_schema(51)
        result, _ = self.import_steam(games, {620: steam_achievements(10, 51)})
        self.assertEqual(result["achievements"], 1)
        self.assertEqual(UnlockedAchievement.objects.count(), 10)

    def test_steam_keeps_the_rarity_if_it_cannot_read_it_again(self):
        games = self.steam_games()
        self.import_steam(games, {620: steam_achievements(2, 51)})
        ExternalTitle.objects.filter(external_id="620").update(achievements_read_at=timezone.now() - timedelta(days=60))
        self.percentages.side_effect = lambda app_id: None
        games[0]["rtime_last_played"] = int(time.time()) + 60
        self.import_steam(games, {620: steam_achievements(2, 51)})
        self.assertEqual(self.schema.call_count, 2)
        self.assertEqual(Achievement.objects.get(title__external_id="620", api_name="ACH_0").percent, 90)

    def test_steam_unlocks_follow_what_steam_says(self):
        games = self.steam_games()
        self.import_steam(games, {620: steam_achievements(3, 51)})
        # Si deja de tener uno (Steam deja reiniciarlos) se quita, y si cambia la fecha, se corrige.
        games[0]["rtime_last_played"] = int(time.time()) + 60
        when = timezone.now() - timedelta(days=100)
        self.import_steam(games, {620: steam_achievements(2, 51, when)})
        self.assertEqual(
            sorted(UnlockedAchievement.objects.values_list("achievement__api_name", "unlocked_at")),
            [("ACH_0", when), ("ACH_1", when - timedelta(days=1))],
        )
        self.assertEqual(self.achievements_of(self.portal), (2, 51, False))

    def fake_psn(self, trophies, by_title=None):
        """Simula Sony con Hades (CUSA00001_00) y God of War Ragnarök (PPSA00002_00) jugados, la lista de trofeos de la
        cuenta trophies (o el error que lanza) y las listas de trofeos de cada ID de juego by_title."""
        gow = Game.objects.create(name="God of War Ragnarök", slug="god-of-war-ragnarok")
        played = [
            psn_title("Hades", "PT10H", title_id="CUSA00001_00"),
            psn_title("God of War Ragnarök PS4 & PS5", "PT40H", title_id="PPSA00002_00"),
        ]
        answer = {"side_effect": trophies} if isinstance(trophies, Exception) else {"return_value": trophies}
        return gow, mock.patch.multiple(
            psn,
            access_token=mock.Mock(return_value="tok"),
            profile=mock.Mock(return_value={"account_id": "111", "online_id": "AnaPSN", "avatar": ""}),
            played_games=mock.Mock(return_value=played),
            trophy_titles=mock.Mock(**answer),
            trophies_by_title_id=mock.Mock(return_value=by_title or {}),
        )

    def test_psn_trophies_are_matched_by_name(self):
        gow, fake = self.fake_psn([
            trophy_title("Hades", (30, 10, 5, 1), (30, 10, 5, 1)),
            # La versión de PS4 y la de PS5 tienen trofeos aparte: cuenta la que más lleva.
            trophy_title("God of War Ragnarök", (25, 8, 2, 1), (10, 2, 0, 0)),
            trophy_title("God of War® Ragnarök", (25, 8, 2, 1), (20, 5, 1, 0)),
            trophy_title("Juego Sin Importar", (10, 0, 0, 0), (5, 0, 0, 0)),
        ])
        with fake:
            result = psn.import_library(self.user, NPSSO)
        self.assertEqual(result["trophies"], 2)
        self.assertEqual(self.achievements_of(self.hades), (46, 46, True))
        self.assertEqual(self.achievements_of(gow), (26, 36, False))

    def test_psn_trophies_one_by_one(self):
        trophy_list = {
            **trophy_title("Hades", (2, 1, 0, 0), (1, 0, 0, 0)), "npCommunicationId": "NPWR00001_00", "npServiceName": "trophy",
        }
        _, fake = self.fake_psn([], by_title={"CUSA00001_00": [trophy_list], "PPSA00002_00": []})
        now = timezone.now()
        defined = [
            {
                "trophyId": n, "trophyName": f"Trofeo {n}", "trophyDetail": f"Haz {n} cosas",
                "trophyIconUrl": f"https://psn.example.com/{n}.png", "trophyHidden": n == 2,
            }
            for n in range(3)
        ]
        earned = [
            {"trophyId": 0, "earned": True, "earnedDateTime": (now - timedelta(days=1)).isoformat(), "trophyEarnedRate": "3.2"},
            {"trophyId": 1, "earned": False, "trophyEarnedRate": "40.0"},
            {"trophyId": 2, "earned": False, "trophyEarnedRate": "1.5"},
        ]

        def rows(url, token, service):
            """Lo que da PlayStation: los trofeos de la lista o, en la dirección de la cuenta, los conseguidos."""
            self.assertEqual(service, "trophy")
            return earned if "/users/me/" in url else defined

        with fake, mock.patch.object(psn, "trophy_details", side_effect=REAL_TROPHY_DETAILS), \
                mock.patch.object(psn, "_trophy_rows", side_effect=rows):
            psn.import_library(self.user, NPSSO)
        detail = achievements.detail(self.user, self.hades, platform="psn")
        self.assertEqual(
            (detail["label"], detail["noun"], detail["unlocked"], detail["total"]), ("PlayStation", "trofeos", 1, 3),
        )
        self.assertEqual((detail["rarest"].name, detail["rarest"].percent), ("Trofeo 0", 3.2))
        self.assertEqual([a.api_name for a in detail["locked_shown"]], ["NPWR00001_00:1", "NPWR00001_00:2"])
        # El rarísimo recién conseguido sale en la actividad.
        trophy = Activity.objects.get(kind=Activity.Kind.TROPHY)
        self.assertEqual((trophy.trophy_verb, trophy.data["rare"][0]["name"]), ("consiguió un trofeo rarísimo en", "Trofeo 0"))
        # Sin cambios, la siguiente sincronización no los vuelve a pedir.
        with fake, mock.patch.object(psn, "trophy_details", side_effect=REAL_TROPHY_DETAILS) as read:
            psn.import_library(self.user, NPSSO)
        read.assert_not_called()
        # En la ficha, cada plataforma despliega los suyos.
        self.client.force_login(self.user)
        response = self.client.get(reverse("game_achievements", args=[self.hades.pk]) + "?plataforma=psn")
        self.assertContains(response, "Tu trofeo más raro")
        self.assertContains(response, 'id="achievement-panel-psn"')

    def test_psn_games_still_import_if_trophies_fail(self):
        _, fake = self.fake_psn(psn.PSNError("caído"))
        with fake:
            result = psn.import_library(self.user, NPSSO)
        self.assertEqual((result["added"], result["trophies"]), (2, 0))

    def test_psn_trophy_titles_go_page_by_page(self):
        pages = [{"trophyTitles": [{"trophyTitleName": "A"}], "nextOffset": 1}, {"trophyTitles": [{"trophyTitleName": "B"}]}]
        with mock.patch.object(psn, "_get", side_effect=pages) as get:
            titles = REAL_TROPHY_TITLES("tok")
        self.assertEqual([title["trophyTitleName"] for title in titles], ["A", "B"])
        self.assertEqual(get.call_args_list[1].kwargs["offset"], 1)

    def test_psn_trophies_are_read_by_game_id(self):
        gow, fake = self.fake_psn([trophy_title("Hades", (30, 10, 5, 1), (12, 3, 0, 0))], by_title={
            # Una colección trae una lista por cada juego: se suman y cuenta como platino si tiene alguno.
            "PPSA00002_00": [
                trophy_title("God of War Ragnarök", (25, 8, 2, 1), (25, 8, 2, 1)),
                trophy_title("God of War Ragnarök: Valhalla", (10, 0, 0, 0), (3, 0, 0, 0)),
            ],
            # PlayStation no da listas para el ID de Hades: se busca por nombre.
            "CUSA00001_00": [],
        })
        with fake:
            result = psn.import_library(self.user, NPSSO)
        self.assertEqual(result["trophies"], 2)
        self.assertEqual(self.achievements_of(gow), (39, 46, True))
        self.assertEqual(self.achievements_of(self.hades), (15, 46, False))

    def test_psn_trophies_by_name_ignore_signs_and_drop_the_year_only_if_needed(self):
        names = [
            "resident evil 4 (2005)", "DOOM (1993)", "LittleBigPlanet3 (LATAM)", "Tom Clancy’s Ghost Recon Breakpoint",
            "Ghostrunner2", "Hades II",
        ]
        for name in names:
            title = ExternalTitle.objects.create(platform=ExternalTitle.Platform.PSN, external_id=f"name:{name}", name=name)
            ImportedTitle.objects.create(user=self.user, title=title)
        saved = psn.save_trophies(self.user, [
            trophy_title("resident evil 4", (30, 10, 5, 1), (5, 0, 0, 0)),
            # DOOM (1993) tiene su propia lista: no se lleva la de DOOM.
            trophy_title("DOOM", (20, 5, 2, 1), (20, 5, 2, 1)),
            trophy_title("DOOM (1993)", (10, 0, 0, 0), (1, 0, 0, 0)),
            trophy_title("LittleBigPlanet™3", (50, 10, 3, 1), (2, 0, 0, 0)),
            trophy_title("Tom Clancy's Ghost Recon® Breakpoint", (40, 8, 2, 1), (4, 0, 0, 0)),
            trophy_title("Ghostrunner 2", (30, 10, 4, 1), (3, 0, 0, 0)),
            trophy_title("Hades", (10, 0, 0, 0), (1, 0, 0, 0)),
        ])
        self.assertEqual(saved, 5)
        found = {
            row.title.name: (row.achievements_unlocked, row.achievements_total)
            for row in ImportedTitle.objects.filter(user=self.user).select_related("title")
        }
        self.assertEqual(found, {
            "resident evil 4 (2005)": (5, 46), "DOOM (1993)": (1, 10), "LittleBigPlanet3 (LATAM)": (2, 64),
            "Tom Clancy’s Ghost Recon Breakpoint": (4, 51), "Ghostrunner2": (3, 45), "Hades II": (None, None),
        })

    def test_psn_trophies_by_game_id_go_five_by_five(self):
        def answer(url, token, npTitleIds):
            """Contesta como Sony: rechaza las peticiones con CUSA00003_00, que no conoce, y falla con CUSA00006_00."""
            batch = npTitleIds.split(",")
            if "CUSA00003_00" in batch:
                raise psn.PSNRejectedError("Resource not found")
            if "CUSA00006_00" in batch:
                raise psn.PSNError("caído")
            return {"titles": [{"npTitleId": title_id, "trophyTitles": [{"trophyTitleName": title_id}]} for title_id in batch]}

        ids = [f"CUSA0000{number}_00" for number in range(7)] + ["name:juego sin id"]
        with mock.patch.object(psn, "_get", side_effect=answer) as get:
            found = REAL_TROPHIES_BY_TITLE_ID("tok", ids)
        # Los cinco primeros van juntos y, como hay uno que no conoce, después de uno en uno; el que falla se lleva su
        # petición entera, y el título sin ID no se pide.
        self.assertEqual(sorted(found), ["CUSA00000_00", "CUSA00001_00", "CUSA00002_00", "CUSA00004_00"])
        self.assertEqual(found["CUSA00001_00"], [{"trophyTitleName": "CUSA00001_00"}])
        requested = sorted(call.kwargs["npTitleIds"] for call in get.call_args_list)
        self.assertEqual(requested, sorted([",".join(ids[:5]), *ids[:5], "CUSA00005_00,CUSA00006_00"]))

    def test_psn_tells_rejected_requests_apart(self):
        answers = []
        for status in (404, 500):
            response = fake_response(status=status)
            response.raise_for_status.side_effect = psn.requests.HTTPError(response=response)
            answers.append(response)
        with mock.patch("accounts.psn.requests.get", side_effect=answers):
            with self.assertRaises(psn.PSNRejectedError):
                psn._get(psn.GAME_TROPHIES_URL, "tok", npTitleIds="CUSA00001_00")
            with self.assertRaises(psn.PSNError) as caught:
                psn._get(psn.GAME_TROPHIES_URL, "tok", npTitleIds="CUSA00001_00")
        self.assertNotIsInstance(caught.exception, psn.PSNRejectedError)

    def test_xbox_achievements_come_with_the_history(self):
        halo = Game.objects.create(name="Halo Infinite", slug="halo-infinite")
        recent = (timezone.now() - timedelta(days=1)).isoformat()
        titles = [
            {"titleId": "1", "name": "Halo Infinite", "type": "Game", "titleHistory": {"lastTimePlayed": recent},
             "achievement": {"currentAchievements": 20, "totalAchievements": 119}},
            {"titleId": "2", "name": "Juego Raro", "type": "Game", "titleHistory": {}},
        ]
        with mock.patch.multiple(
            xbox,
            xbox_tokens=mock.Mock(return_value={"xuid": "2535", "gamertag": "AnaGT", "auth": "a"}),
            played_titles=mock.Mock(return_value=titles),
            minutes_played=mock.Mock(return_value={"1": 120}),
            avatar=mock.Mock(return_value=""),
        ):
            result = xbox.import_library(self.user, "code", "http://testserver/vuelta/")
        self.assertEqual(result["achievements"], 1)
        self.assertEqual(self.achievements_of(halo), (20, 119, False))

    def imported(self, platform, external_id, game, unlocked, total, platinum=False):
        """Título importado de game con sus logros ya leídos."""
        title = ExternalTitle.objects.create(platform=platform, external_id=external_id, name=game.name, game=game)
        row = ImportedTitle(user=self.user, title=title, game=game)
        row.set_achievements(unlocked, total, platinum)
        row.save()

    def test_the_game_page_and_the_profile_show_them(self):
        self.client.force_login(self.user)
        LibraryEntry.objects.create(user=self.user, game=self.hades, status=LibraryEntry.Status.COMPLETED)
        LibraryEntry.objects.create(user=self.user, game=self.portal, status=LibraryEntry.Status.PLAYING)
        self.imported("steam", "1145360", self.hades, 49, 49)
        # Versión de PS5 a medias y de PS4 con el platino: cuenta la de PS4.
        self.imported("psn", "PPSA00001_00", self.hades, 30, 46)
        self.imported("psn", "CUSA00001_00", self.hades, 46, 46, platinum=True)
        self.imported("steam", "620", self.portal, 10, 51)
        response = self.client.get(self.hades.get_absolute_url())
        self.assertContains(response, "Tus logros")
        progress = [(item["label"], item["unlocked"], item["platinum"]) for item in response.context["entry"].achievements]
        self.assertEqual(progress, [("Steam", 49, False), ("PlayStation", 46, True)])
        # Van en su propia tarjeta, aparte del widget de la biblioteca, que HTMX cambia sin tocarlos.
        response = self.client.post(
            reverse("library:set_status", args=[self.portal.pk]), {"status": "completed"}, HTTP_HX_REQUEST="true",
        )
        self.assertNotContains(response, "Tus logros")
        self.assertContains(self.client.get(self.portal.get_absolute_url()), "10 de 51 logros")
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertContains(response, '<span class="card-trophy is-platinum">', count=1)
        self.assertEqual((response.context["stats"]["platinums"], response.context["stats"]["complete"]), (1, 1))
        # Solo contados (sin leer aún uno a uno), no se pueden desplegar.
        self.assertNotContains(self.client.get(self.hades.get_absolute_url()), "Ver tus logros de")

    def test_your_achievements_one_by_one_on_the_game_page(self):
        self.schema.side_effect = lambda app_id: steam_schema(SCHEMA_TOTALS.get(int(app_id), 0), hidden={1, 49})
        self.import_steam(self.steam_games(), {620: steam_achievements(2, 51, timezone.now() - timedelta(days=10))})
        url = reverse("game_achievements", args=[self.portal.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(self.portal.get_absolute_url()), "Ver tus logros de Steam")
        response = self.client.get(url)
        self.assertContains(response, "<strong>2 de 51</strong>", html=True)
        # Los que tienes, del más reciente al más antiguo; los que te faltan, del más común al más raro y, pasados los
        # primeros, plegados.
        context = response.context
        self.assertEqual([achievement.api_name for achievement in context["unlocked_shown"]], ["ACH_0", "ACH_1"])
        self.assertEqual(context["locked_shown"][0].api_name, "ACH_2")
        self.assertEqual((len(context["locked_shown"]), len(context["locked_more"])), (12, 37))
        self.assertEqual(context["rarest"].api_name, "ACH_1")
        # El oculto que tienes se ve; el que te falta va tapado hasta pulsarlo.
        self.assertContains(response, "<strong>Logro 1</strong>", html=True)
        self.assertContains(response, 'title="Logro oculto: pulsa para ver su nombre">Logro 49</span>')
        self.assertEqual(self.client.get(url, {"orden": "raros"}).context["locked_shown"][0].api_name, "ACH_50")
        in_order = self.client.get(url, {"orden": "juego"}).context["locked_shown"]
        self.assertEqual([achievement.api_name for achievement in in_order[:2]], ["ACH_2", "ACH_3"])
        # De un juego sin logros leídos uno a uno no hay nada que enseñar.
        self.assertEqual(self.client.get(reverse("game_achievements", args=[self.hades.pk])).status_code, 404)

    def test_the_profile_shows_the_latest_and_the_rarest(self):
        self.schema.side_effect = lambda app_id: steam_schema(SCHEMA_TOTALS.get(int(app_id), 0), hidden={1})
        when = timezone.now() - timedelta(days=10)
        self.import_steam(self.steam_games(), {
            620: steam_achievements(2, 51, when), 504230: steam_achievements(1, 31, when - timedelta(days=30)),
        })
        url = reverse("profile", args=["ana"])
        response = self.client.get(url)
        showcase = response.context["achievement_showcase"]
        self.assertEqual(showcase["count"], 3)
        self.assertEqual(
            [(unlock.achievement.title.external_id, unlock.achievement.api_name) for unlock in showcase["latest"]],
            [("620", "ACH_0"), ("620", "ACH_1"), ("504230", "ACH_0")],
        )
        self.assertEqual(showcase["rarest"].achievement.name, "Logro 1")
        # El oculto va tapado para los demás (en «El más raro» y en «Los últimos»), pero no para su dueño.
        covered = 'title="Logro oculto: pulsa para ver su nombre">Logro 1</span>'
        self.assertContains(response, covered, count=2)
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(url), covered)
        # Sin la sección en el escaparate no se pide nada.
        Profile.objects.filter(user=self.user).update(sections=[["logros", False]])
        self.assertIsNone(self.client.get(url).context["achievement_showcase"])


class NoticesTests(TestCase):
    """accounts.notices: que nadie reciba avisos de lo que hace él mismo, las reglas para no repetirlos y que solo
    llegue lo que cada uno deja encendido."""

    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.eva = User.objects.create_user("eva", "eva@example.com", "x")

    def notify(self, kind, url="/algo/", actor=None, **kwargs):
        """Avisa a ana de algo de tipo kind; devuelve el aviso o None si no se ha creado."""
        return notices.notify(self.ana, kind, "Ha pasado algo.", url, actor=actor, **kwargs)

    def test_every_kind_has_its_rule_and_all_but_reports_their_group(self):
        self.assertEqual(set(notices.REPEAT), set(Notification.Kind))
        grouped = [kind for group in notices.GROUPS.values() for kind in group.kinds]
        self.assertEqual(len(grouped), len(set(grouped)))  # cada tipo, en un solo grupo
        self.assertEqual(set(grouped), set(Notification.Kind) - {Notification.Kind.REPORT})

    def test_nobody_is_notified_of_what_they_do(self):
        self.assertIsNone(self.notify(Notification.Kind.FOLLOW, actor=self.ana))
        self.assertIsNotNone(self.notify(Notification.Kind.FOLLOW, actor=self.luis))

    def test_likes_once_per_person_and_thing_even_after_reading_them(self):
        kind = Notification.Kind.LIST_LIKE
        self.assertIsNotNone(self.notify(kind, actor=self.luis))
        Notification.objects.update(read_at=timezone.now())
        self.assertIsNone(self.notify(kind, actor=self.luis))
        self.assertIsNotNone(self.notify(kind, actor=self.eva))
        self.assertIsNotNone(self.notify(kind, url="/otra/", actor=self.luis))

    def test_comments_again_once_the_previous_one_is_read(self):
        kind = Notification.Kind.POST_COMMENT
        self.assertIsNotNone(self.notify(kind, actor=self.luis))
        self.assertIsNone(self.notify(kind, actor=self.luis))
        self.assertIsNotNone(self.notify(kind, actor=self.eva))
        Notification.objects.update(read_at=timezone.now())
        self.assertIsNotNone(self.notify(kind, actor=self.luis))

    def test_follows_once_per_person_whatever_the_link(self):
        self.assertIsNotNone(self.notify(Notification.Kind.FOLLOW, url="/u/luis/", actor=self.luis))
        self.assertIsNone(self.notify(Notification.Kind.FOLLOW, url="/u/ana/seguidores/", actor=self.luis))

    def test_outdated_reviews_once_until_the_review_is_saved_again(self):
        kind, saved = Notification.Kind.REVIEW_UPDATE, timezone.now()
        self.assertIsNotNone(self.notify(kind, since=saved))
        self.assertIsNone(self.notify(kind, since=saved))
        self.assertIsNotNone(self.notify(kind, since=timezone.now()))  # la ha vuelto a guardar

    def test_prices_and_invitations_always_arrive(self):
        for kind in (Notification.Kind.PRICE, Notification.Kind.LIST_INVITE):
            self.assertIsNotNone(self.notify(kind, actor=self.luis))
            self.assertIsNotNone(self.notify(kind, actor=self.luis))

    def test_what_is_turned_off_does_not_arrive(self):
        self.ana.profile.notification_prefs = {"me_gusta": {"campana": False}}
        self.ana.profile.save()
        for kind in (Notification.Kind.LIKE, Notification.Kind.POST_LIKE, Notification.Kind.LIST_LIKE):
            self.assertIsNone(self.notify(kind, actor=self.luis))
        self.assertIsNotNone(self.notify(Notification.Kind.COMMENT, actor=self.luis))
        # Las denuncias no se pueden apagar, y por correo solo pueden llegar las bajadas de precio.
        self.ana.profile.notification_prefs = {key: {"campana": False} for key in notices.GROUPS}
        self.ana.profile.save()
        self.assertIsNotNone(self.notify(Notification.Kind.REPORT))
        self.assertTrue(notices.wants(self.ana, Notification.Kind.PRICE, notices.EMAIL))
        self.assertFalse(notices.wants(self.ana, Notification.Kind.FOLLOW, notices.EMAIL))
        self.assertFalse(notices.wants(self.ana, Notification.Kind.REPORT, notices.EMAIL))

    def test_the_text_is_cut_to_what_fits(self):
        notice = notices.notify(self.ana, Notification.Kind.PRICE, "x" * 400, "/juegos/hades/")
        self.assertEqual(len(notice.text), 300)

    def test_following_someone_who_turned_it_off_does_not_notify(self):
        self.luis.profile.notification_prefs = {"seguidores": {"campana": False}}
        self.luis.profile.save()
        self.client.force_login(self.ana)
        self.client.post(reverse("social:follow", args=["luis"]))
        self.assertTrue(self.ana.following.filter(followed=self.luis).exists())
        self.assertFalse(Notification.objects.filter(user=self.luis).exists())


class NoticePrefsTests(TestCase):
    """La sección «Avisos» de Editar perfil."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")

    def setUp(self):
        self.client.force_login(self.user)

    def prefs(self):
        """Lo que tiene guardado ana en notification_prefs."""
        return Profile.objects.get(user=self.user).notification_prefs

    def test_everything_starts_on(self):
        response = self.client.get(reverse("edit_profile"))
        self.assertContains(response, '<a href="#avisos">Avisos</a>')
        self.assertContains(response, "Nuevos seguidores")
        self.assertContains(response, "A ana@example.com.")
        rows = response.context["form"].notice_rows
        self.assertEqual(len(rows), len(notices.GROUPS))
        self.assertTrue(all(field.value() for row in rows for field in row if field is not None))

    def test_saves_what_is_turned_off_and_shows_it(self):
        data = {"is_public": "on", "avisos": "1", **{f"aviso_{key}": "on" for key in notices.GROUPS}}
        del data["aviso_me_gusta"]
        self.client.post(reverse("edit_profile"), data)  # sin correo_precios: el correo, apagado
        prefs = self.prefs()
        self.assertEqual(prefs["me_gusta"], {"campana": False})
        self.assertEqual(prefs["precios"], {"campana": True, "correo": False})
        self.assertEqual(prefs["comentarios"], {"campana": True})
        form = self.client.get(reverse("edit_profile")).context["form"]
        self.assertFalse(form["aviso_me_gusta"].value())
        self.assertFalse(form["correo_precios"].value())
        self.assertTrue(form["aviso_seguidores"].value())

    def test_a_form_without_the_section_turns_nothing_off(self):
        self.client.post(reverse("edit_profile"), {"display_name": "Ana", "is_public": "on"})
        self.assertEqual(self.prefs(), {})
        self.assertEqual(Profile.objects.get(user=self.user).display_name, "Ana")

    def test_unsaved_changes_warning_starts_hidden(self):
        response = self.client.get(reverse("edit_profile"))
        self.assertContains(response, '<span class="unsaved" data-unsaved role="status" hidden>Cambios sin guardar</span>')
        self.assertContains(response, '<button class="btn" type="button" data-discard hidden>Descartar</button>')
        self.assertContains(response, 'window.addEventListener("beforeunload"')
