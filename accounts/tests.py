import json
import time
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from games.models import Game
from library.models import LibraryEntry

from . import psn, steam, xbox
from .models import PlatformAccount, PSNAccount, SteamAccount, XboxAccount

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
        LibraryEntry.objects.filter(game=self.done).update(rating=4, steam_playtime_minutes=600)
        LibraryEntry.objects.filter(game=self.playing).update(rating=2, steam_playtime_minutes=30)
        response = self.client.get(reverse("profile", args=["ana"]))
        stats = response.context["stats"]
        self.assertEqual((stats["games"], stats["completed"], stats["hours"]), (2, 1, 10))
        self.assertEqual(stats["average_rating"], 3)
        response = self.client.get(reverse("profile", args=["ana"]), {"orden": "nombre"})
        self.assertEqual([e.game.name for e in response.context["entries"]], ["Celeste", "Hades"])
        response = self.client.get(reverse("profile", args=["ana"]), {"orden": "horas"})
        self.assertEqual([e.game.name for e in response.context["entries"]], ["Celeste", "Hades"])

    def test_reviews_tab_lists_only_reviewed_games(self):
        LibraryEntry.objects.filter(game=self.done).update(review="Preciosa.", reviewed_at="2026-01-01T10:00Z")
        response = self.client.get(reverse("profile", args=["ana"]), {"vista": "resenas"})
        self.assertContains(response, "Preciosa.")
        self.assertEqual([e.game.name for e in response.context["reviews"]], ["Celeste"])

    def test_edit_profile_requires_login(self):
        response = self.client.get(reverse("edit_profile"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('edit_profile')}")


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
    """Juego tal como lo devuelve la lista de juegos jugados de PSN."""
    played = timezone.now() - timedelta(days=days_ago)
    return {
        "name": name, "localizedName": name, "category": category,
        "playDuration": duration, "lastPlayedDateTime": played.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
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
