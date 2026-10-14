from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from library.models import LibraryEntry

from . import igdb
from .models import Game, Genre, Platform

User = get_user_model()

IGDB_HADES = {
    "id": 113112,
    "name": "Hades",
    "slug": "hades",
    "summary": "A rogue-like dungeon crawler.",
    "first_release_date": 1600300800,  # 2020-09-17 UTC
    "total_rating_count": 2500,
    "cover": {"id": 1, "image_id": "co39vc"},
    "genres": [{"id": 12, "name": "Role-playing (RPG)", "slug": "role-playing-rpg"}],
    "platforms": [{"id": 6, "name": "PC (Microsoft Windows)", "abbreviation": "PC", "slug": "win"}],
}


class UpsertGamesTests(TestCase):
    def test_creates_game_with_relations(self):
        igdb.upsert_games([IGDB_HADES])
        game = Game.objects.get(igdb_id=113112)
        self.assertEqual(game.first_release_date, date(2020, 9, 17))
        self.assertEqual(game.popularity, 2500)
        self.assertIn("co39vc", game.cover_url)
        self.assertEqual(game.genres.get().slug, "role-playing-rpg")
        self.assertEqual(game.platforms.get().abbreviation, "PC")

    def test_is_idempotent_and_updates_fields(self):
        igdb.upsert_games([IGDB_HADES])
        igdb.upsert_games([{**IGDB_HADES, "name": "Hades (updated)", "genres": []}])
        game = Game.objects.get()
        self.assertEqual(game.name, "Hades (updated)")
        self.assertEqual(game.genres.count(), 0)
        self.assertEqual(Genre.objects.count(), 1)
        self.assertEqual(Platform.objects.count(), 1)

    def test_adopts_demo_rows_keeping_library_entries(self):
        call_command("seed_demo", verbosity=0, stdout=mock.Mock())
        demo = Game.objects.get(slug="hades")
        user = User.objects.create_user("ana", "ana@example.com", "x")
        LibraryEntry.objects.create(user=user, game=demo)
        total = Game.objects.count()

        igdb.upsert_games([IGDB_HADES])

        self.assertEqual(Game.objects.count(), total)
        demo.refresh_from_db()
        self.assertEqual(demo.igdb_id, 113112)
        self.assertEqual(LibraryEntry.objects.get(user=user).game, demo)
        self.assertEqual(Genre.objects.filter(slug="role-playing-rpg").count(), 1)

    def test_skips_payloads_without_name_or_slug(self):
        self.assertEqual(igdb.upsert_games([{"id": 1}, {"id": 2, "name": "Sin slug"}]), [])


@override_settings(IGDB_CLIENT_ID="id", IGDB_CLIENT_SECRET="secret")
class IGDBClientTests(TestCase):
    def test_search_strips_quotes_from_the_query(self):
        client = igdb.IGDBClient()
        with mock.patch.object(client, "query", return_value=[]) as query:
            client.search('zelda"; fields *; \\')
        body = query.call_args.args[1]
        self.assertTrue(body.startswith('search "zelda ; fields *;";'))
        self.assertEqual(body.count('"'), 2)

    def test_blank_search_makes_no_request(self):
        client = igdb.IGDBClient()
        with mock.patch.object(client, "query") as query:
            self.assertEqual(client.search('  " '), [])
        query.assert_not_called()


class CatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        rpg = Genre.objects.create(name="RPG", slug="rpg")
        switch = Platform.objects.create(name="Nintendo Switch", slug="switch")
        cls.hades = Game.objects.create(name="Hades", slug="hades", popularity=10)
        cls.hades.genres.add(rpg)
        cls.hades.platforms.add(switch)
        cls.celeste = Game.objects.create(name="Celeste", slug="celeste", popularity=5)
        cls.celeste.platforms.add(switch)

    def test_lists_games_by_popularity(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(list(response.context["page"].object_list), [self.hades, self.celeste])

    def test_search_and_filters(self):
        url = reverse("games:catalog")
        self.assertEqual(list(self.client.get(url, {"q": "cele"}).context["page"].object_list), [self.celeste])
        self.assertEqual(list(self.client.get(url, {"genero": "rpg"}).context["page"].object_list), [self.hades])
        self.assertEqual(self.client.get(url, {"plataforma": "switch"}).context["page"].paginator.count, 2)

    def test_detail_shows_library_widget_only_when_logged_in(self):
        url = self.hades.get_absolute_url()
        self.assertNotContains(self.client.get(url), 'id="library-widget"')
        self.client.force_login(User.objects.create_user("ana", "ana@example.com", "x"))
        self.assertContains(self.client.get(url), 'id="library-widget"')

    def test_import_requires_login_and_post(self):
        url = reverse("games:import_search")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"q": "zelda"}).status_code, 302)
        self.assertIn(reverse("login"), self.client.post(url, {"q": "zelda"})["Location"])

    @override_settings(IGDB_CLIENT_ID="id", IGDB_CLIENT_SECRET="secret")
    def test_import_search_adds_igdb_results(self):
        self.client.force_login(User.objects.create_user("ana", "ana@example.com", "x"))
        zelda = {"id": 7346, "name": "Zelda", "slug": "zelda"}
        with mock.patch.object(igdb.IGDBClient, "search", return_value=[zelda]):
            response = self.client.post(reverse("games:import_search"), {"q": "zelda"})
        self.assertRedirects(response, f"{reverse('games:catalog')}?q=zelda")
        self.assertTrue(Game.objects.filter(igdb_id=7346).exists())
