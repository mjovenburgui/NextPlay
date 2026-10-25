import io
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from library.models import LibraryEntry, Review, WishlistItem

from . import history, igdb, media, prices, spanish, xbox
from .models import STEAM_DECK_SLUG, Game, GamePrice, Genre, Platform

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
        self.assertEqual(game.genres.get().name, "Rol (RPG)")
        self.assertEqual(game.platforms.get().abbreviation, "PC")

    def test_is_idempotent_and_updates_fields(self):
        igdb.upsert_games([IGDB_HADES])
        igdb.upsert_games([{**IGDB_HADES, "name": "Hades (updated)", "genres": []}])
        game = Game.objects.get()
        self.assertEqual(game.name, "Hades (updated)")
        self.assertEqual(game.genres.count(), 0)
        self.assertEqual(Genre.objects.count(), 1)
        # La Steam Deck la crea una migración y no viene de IGDB.
        self.assertEqual(Platform.objects.exclude(slug=STEAM_DECK_SLUG).count(), 1)

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

    def test_stores_critic_rating_and_steam_app_id(self):
        igdb.upsert_games([{
            **IGDB_HADES,
            "aggregated_rating": 92.6,
            "aggregated_rating_count": 41,
            "external_games": [
                {"id": 1, "uid": "1145360", "external_game_source": 1},
                {"id": 2, "uid": "hades", "external_game_source": 5},
            ],
        }])
        game = Game.objects.get()
        self.assertEqual((game.critic_score, game.critic_rating_count, game.critic_tier), (93, 41, "high"))
        self.assertEqual(game.steam_app_id, 1145360)

    def test_keeps_store_entries_named_like_the_game(self):
        igdb.upsert_games([{
            **IGDB_HADES,
            "external_games": [
                {"id": 1, "uid": "52003", "name": "Mac Hades", "external_game_source": 1},
                {"id": 2, "uid": "1145360", "name": "Hades", "external_game_source": 1},
                {"id": 3, "uid": "3717340", "name": "Hades™", "external_game_source": 1},
                {"id": 4, "uid": "1478349159", "name": "Hades Soundtrack", "external_game_source": 5},
            ],
        }])
        game = Game.objects.get()
        self.assertEqual(game.steam_app_id, 1145360)
        self.assertEqual(game.store_ids, {"steam": [1145360, 3717340], "gog": ["1478349159"]})

    def test_changed_store_ids_drop_old_prices(self):
        game = Game.objects.create(igdb_id=113112, name="Hades", slug="hades", steam_app_id=1, store_ids={"steam": [1]})
        GamePrice.objects.create(
            game=game, shop_id=61, shop_name="Steam", price=1, regular_price=1, currency="EUR", url="https://s.test"
        )
        igdb.upsert_games([IGDB_HADES])
        game.refresh_from_db()
        self.assertEqual((game.steam_app_id, game.prices.count()), (None, 0))

    def test_override_keeps_prices_and_survives_import(self):
        game = Game.objects.create(igdb_id=113112, name="Hades", slug="hades", steam_app_id_override=7)
        GamePrice.objects.create(
            game=game, shop_id=61, shop_name="Steam", price=1, regular_price=1, currency="EUR", url="https://s.test"
        )
        igdb.upsert_games([IGDB_HADES])
        game.refresh_from_db()
        self.assertEqual((game.steam_app_id_override, game.prices.count()), (7, 1))
        self.assertEqual(game.price_lookups, [([("steam", 7)], "")])

    def test_stores_editions_and_remasters_as_store_alternatives(self):
        def steam(uid, name):
            return {"external_games": [{"id": uid, "uid": str(uid), "name": name, "external_game_source": 1}]}

        igdb.upsert_games([{
            **IGDB_HADES,
            **steam(72850, "Hades"),
            "editions": [{"id": 2, "name": "Hades GOTY", **steam(200, "Hades GOTY")}],
            "remasters": [
                {"id": 3, "name": "Hades Remastered", **steam(300, "Hades Remastered")},
                {"id": 4, "name": "Hades (same app)", **steam(72850, "Hades")},
                {"id": 5, "name": "Hades Console"},
            ],
        }])
        self.assertEqual(Game.objects.get().price_lookups, [
            ([("steam", 72850)], ""),
            ([("steam", 200)], "Hades GOTY"),
            ([("steam", 300)], "Hades Remastered"),
        ])

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

    def test_attaches_editions_to_their_parent_games(self):
        client = igdb.IGDBClient()
        goty = {"id": 9, "name": "Hades GOTY", "version_parent": 113112}
        with mock.patch.object(client, "query", side_effect=[[{**IGDB_HADES}, {"id": 5}], [goty]]) as query,                 mock.patch("games.igdb.time.sleep"):
            games = client.top_games(limit=2)
        self.assertIn("where version_parent = (113112,5)", query.call_args.args[1])
        self.assertEqual([game["editions"] for game in games], [[goty], []])

    def test_blank_search_makes_no_request(self):
        client = igdb.IGDBClient()
        with mock.patch.object(client, "query") as query:
            self.assertEqual(client.search('  " '), [])
        query.assert_not_called()

    def test_artworks_skip_logos_icons_and_covers_and_put_key_art_first(self):
        client = igdb.IGDBClient("id", "secret")
        arts = [
            {"image_id": "icono", "artwork_type": 12}, {"image_id": "arte", "artwork_type": 1},
            {"image_id": "logo", "artwork_type": 7}, {"image_id": "principal", "artwork_type": 2},
            {"image_id": "portada", "artwork_type": 9}, {"image_id": "sin-tipo"},
        ]
        with mock.patch.object(igdb.IGDBClient, "query", side_effect=[arts, [{"image_id": "captura"}]]) as query:
            self.assertEqual(client.artworks(1942, limit=4), ["principal", "arte", "sin-tipo", "captura"])
        self.assertIn("artwork_type", query.call_args_list[0].args[1])
        self.assertIn("where game = 1942; limit 1;", query.call_args_list[1].args[1])

    def test_game_media_asks_everything_in_one_query(self):
        client = igdb.IGDBClient("id", "secret")
        with mock.patch.object(igdb.IGDBClient, "query", return_value=[{"id": 1942, "videos": []}]) as query:
            self.assertEqual(client.game_media(1942), {"id": 1942, "videos": []})
        endpoint, body = query.call_args.args
        self.assertEqual(endpoint, "games")
        self.assertIn("artworks.artwork_type", body)
        self.assertIn("videos.video_id", body)
        self.assertIn("where id = 1942;", body)
        with mock.patch.object(igdb.IGDBClient, "query", return_value=[]):
            self.assertEqual(client.game_media(1942), {})


# Lo que contesta IGDB a game_media: dos logos (uno con fondo) y un icono entre los artes, un arte vertical y dos vídeos.
IGDB_MEDIA = {
    "artworks": [
        {"image_id": "logo", "artwork_type": 5, "width": 800, "height": 200, "alpha_channel": True},
        {"image_id": "logo-con-fondo", "artwork_type": 7, "width": 800, "height": 200, "alpha_channel": False},
        {"image_id": "icono", "artwork_type": 12, "width": 256, "height": 256},
        {"image_id": "arte", "artwork_type": 1, "width": 1920, "height": 1080},
        {"image_id": "principal", "artwork_type": 2, "width": 1600, "height": 900},
        {"image_id": "vertical", "artwork_type": 1, "width": 700, "height": 1000},
    ],
    "screenshots": [{"image_id": "sc1", "width": 1920, "height": 1080}, {"image_id": "sc2", "width": 1280, "height": 720}],
    "videos": [
        {"id": 9, "name": "Gameplay", "video_id": "yt-gameplay"},
        {"id": 5, "name": "Launch Trailer", "video_id": "yt-launch"},
    ],
}
# Recorte de un logo sin su margen transparente, como lo calcula media._logo_crop.
LOGO_CROP = {"l": 0.034, "t": 0.32, "w": 0.93, "h": 0.364, "ratio": 4.542}


class GameMediaTests(TestCase):
    def setUp(self):
        self.game = Game.objects.create(
            name="Hades", slug="hades", igdb_id=113112, steam_app_id=1145360, cover_image_id="co39vc"
        )

    def load(self, steam_hero=True, steam_logo=LOGO_CROP, igdb_logo=None):
        """Guarda como imágenes del juego las de IGDB_MEDIA, simulando lo que contestan IGDB y Steam."""
        client = mock.Mock(spec=igdb.IGDBClient)
        client.game_media.return_value = IGDB_MEDIA
        with mock.patch.object(media, "_exists", return_value=steam_hero), \
                mock.patch.object(media, "_logo_crop", side_effect=lambda url: steam_logo if "steam" in url else igdb_logo) as crop:
            media.update_media(self.game, client)
        self.game.refresh_from_db()
        return crop

    def test_update_media_keeps_only_art_and_checks_steam(self):
        crop = self.load()
        stored = self.game.media
        self.assertEqual([art["id"] for art in stored["arts"]], ["principal", "arte", "vertical"])
        self.assertEqual(stored["logos"], [{"id": "logo", "type": 5}])  # sin recorte: va el logo de Steam
        self.assertEqual([video["id"] for video in stored["videos"]], ["yt-launch", "yt-gameplay"])
        self.assertEqual(stored["steam"], {"app_id": 1145360, "hero": True, "logo": LOGO_CROP})
        crop.assert_called_once()  # solo el logo de Steam
        self.assertFalse(media.needs_update(self.game))
        self.game.steam_app_id_override = 3240220
        self.assertTrue(media.needs_update(self.game))  # con otro ID de Steam hay que volver a mirar

    def test_logo_crop_skips_logos_without_transparency(self):
        def answer(mode, color):
            """Respuesta de un CDN con un PNG de 100 × 50; en RGBA, transparente salvo una franja de 80 × 10."""
            image = Image.new(mode, (100, 50), color)
            if mode == "RGBA":
                image.paste((255, 255, 255, 255), (10, 20, 90, 30))
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            return mock.Mock(status_code=200, content=buffer.getvalue())

        with mock.patch("games.media.requests.get", return_value=answer("RGBA", (0, 0, 0, 0))):
            self.assertEqual(media._logo_crop("https://cdn/logo.png"), {"l": 0.1, "t": 0.4, "w": 0.8, "h": 0.2, "ratio": 8.0})
        with mock.patch("games.media.requests.get", return_value=answer("RGB", (255, 255, 255))):
            self.assertIsNone(media._logo_crop("https://cdn/logo.png"))  # con fondo: haría un recuadro
        with mock.patch("games.media.requests.get", return_value=mock.Mock(status_code=404)):
            self.assertIsNone(media._logo_crop("https://cdn/logo.png"))

    def test_hero_prefers_steam_and_falls_back_to_igdb(self):
        # Aún sin imágenes de IGDB: la de Steam, a ver si carga, y el nombre en texto.
        hero = media.hero(self.game)
        self.assertIn("/apps/1145360/library_hero.jpg", hero["image"])
        self.assertEqual((hero["fallback"], hero["logo"], hero["credit"]), ("", "", "Imagen: biblioteca de Steam"))
        self.load()
        hero = media.hero(self.game)
        self.assertIn("/apps/1145360/logo.png", hero["logo"])
        self.assertIn("t_1080p/principal.jpg", hero["fallback"])
        self.assertEqual(hero["credit"], "Imagen y logo: biblioteca de Steam")
        self.assertEqual(hero["fallback_credit"], "Imagen: IGDB · Logo: biblioteca de Steam")
        self.assertEqual(
            hero["logo_style"], "--crop-l: 0.0340; --crop-t: 0.3200; --crop-w: 0.9300; --crop-h: 0.3640; --crop-ratio: 4.5420;"
        )
        # Sin imagen ni logo en Steam: el arte principal y el logo blanco de IGDB.
        self.load(steam_hero=False, steam_logo=None, igdb_logo=LOGO_CROP)
        hero = media.hero(self.game)
        self.assertIn("t_1080p/principal.jpg", hero["image"])
        self.assertIn("t_logo_med_2x/logo.png", hero["logo"])
        self.assertEqual((hero["fallback"], hero["credit"]), ("", "Imagen y logo: IGDB"))
        # Un juego sin nada: la carátula desenfocada y el nombre.
        bare = Game.objects.create(name="Celeste", slug="celeste")
        self.assertEqual(media.hero(bare), {
            "image": "", "fallback": "", "credit": "", "fallback_credit": "", "logo": "", "logo_style": "",
        })

    def test_carousel_order_labels_and_filters(self):
        self.load()
        carousel = media.carousel(self.game)
        items = carousel["items"]
        self.assertEqual([item["kind"] for item in items], ["video", "arte", "captura", "captura", "arte", "arte", "video"])
        self.assertEqual(items[0]["tag"], "Launch Trailer")
        self.assertIn("yt-launch/hqdefault.jpg", items[0]["thumb"])
        self.assertEqual(
            (items[1]["tag"], items[1]["heading"], items[1]["art"]), ("Arte principal", "Arte principal 1 de 3", "principal")
        )
        self.assertEqual((items[2]["heading"], items[2]["art"]), ("Captura 1 de 2", "sc1"))
        vertical = items[5]
        self.assertTrue(vertical["tall"])
        self.assertEqual(vertical["art"], "")  # una imagen vertical no vale de cabecera
        self.assertIn("t_720p/vertical.jpg", vertical["thumb"])
        self.assertEqual(carousel["filters"], [("video", "Vídeos", 2), ("captura", "Capturas", 2), ("arte", "Arte", 3)])
        self.assertEqual(media.banner_arts(self.game), {"principal", "arte", "sc1", "sc2"})

    def test_detail_loads_media_lazily_then_inline(self):
        with mock.patch.object(igdb, "is_configured", return_value=True):
            response = self.client.get(reverse("games:detail", args=["hades"]))
        self.assertContains(response, 'hx-get="/juegos/hades/imagenes-y-videos/"')
        self.assertContains(response, "/apps/1145360/library_hero.jpg")
        self.assertContains(response, '<span class="game-name">Hades</span>')
        with mock.patch.object(igdb, "is_configured", return_value=True), \
                mock.patch.object(igdb.IGDBClient, "__init__", return_value=None), \
                mock.patch.object(igdb.IGDBClient, "game_media", return_value=IGDB_MEDIA), \
                mock.patch.object(media, "_exists", return_value=True), \
                mock.patch.object(media, "_logo_crop", return_value=LOGO_CROP):
            response = self.client.get(reverse("games:media", args=["hades"]))
        self.assertContains(response, 'class="media-tile"', count=7)
        self.assertContains(response, 'hx-swap-oob="true"')  # llega la cabecera nueva, ya con el logo
        self.assertNotContains(response, "Poner de cabecera en mi perfil")  # sin sesión
        response = self.client.get(reverse("games:detail", args=["hades"]))
        self.assertNotContains(response, "imagenes-y-videos")
        self.assertContains(response, 'class="media-tile"', count=7)
        self.assertContains(response, 'class="game-logo"')

    def test_media_view_survives_igdb_errors(self):
        with mock.patch.object(igdb, "is_configured", return_value=True), \
                mock.patch.object(igdb.IGDBClient, "__init__", return_value=None), \
                mock.patch.object(igdb.IGDBClient, "game_media", side_effect=igdb.IGDBError("caído")), \
                self.assertLogs("games.views", level="ERROR"):
            response = self.client.get(reverse("games:media", args=["hades"]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "media-tile")
        self.assertTrue(media.needs_update(self.game))

    def test_viewer_offers_the_banner_button_only_with_the_game_in_the_library(self):
        self.load()
        user = User.objects.create_user("ana", "ana@example.com", "x")
        self.client.force_login(user)
        response = self.client.get(reverse("games:detail", args=["hades"]))
        self.assertNotContains(response, "Poner de cabecera en mi perfil")
        LibraryEntry.objects.create(user=user, game=self.game)
        response = self.client.get(reverse("games:detail", args=["hades"]))
        self.assertContains(response, "Poner de cabecera en mi perfil")
        self.assertContains(response, reverse("banner_from_game", args=[self.game.pk]))


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
        response = self.client.get(reverse("games:catalog"))
        self.assertEqual(list(response.context["page"].object_list), [self.hades, self.celeste])

    def test_home_shows_popular_and_trending(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["popular"], [self.hades, self.celeste])
        self.assertContains(response, "Crear cuenta")
        # Celeste está en más bibliotecas este mes, así que va primero en tendencia aunque sea menos popular.
        for name in ("ana", "luis"):
            user = User.objects.create_user(name, f"{name}@example.com", "x")
            LibraryEntry.objects.create(user=user, game=self.celeste)
        self.assertEqual([g.name for g in self.client.get(reverse("home")).context["trending"]], ["Celeste", "Hades"])

    def test_home_shows_your_summary_and_private_reviews_stay_hidden(self):
        ana = User.objects.create_user("ana", "ana@example.com", "x")
        luis = User.objects.create_user("luis", "luis@example.com", "x")
        luis.profile.is_public = False
        luis.profile.save()
        LibraryEntry.objects.create(user=ana, game=self.hades, status=LibraryEntry.Status.PLAYING)
        Review.objects.create(user=ana, game=self.hades, text="Genial", published_at=timezone.now())
        Review.objects.create(user=ana, game=self.celeste, text="Borrador sin publicar", is_draft=True)
        Review.objects.create(user=luis, game=self.celeste, text="Secreta", published_at=timezone.now())
        self.client.force_login(ana)
        response = self.client.get(reverse("home"))
        self.assertEqual(
            response.context["summary"], {"games": 1, "completed": 0, "reviews": 1, "followers": 0, "following": 0}
        )
        self.assertEqual([e.game for e in response.context["playing"]], [self.hades])
        self.assertContains(response, "Genial")
        self.assertNotContains(response, "Secreta")
        self.assertNotContains(response, "Borrador sin publicar")

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


ITAD_HADES = {
    "id": "uuid-hades",
    "historyLow": {"all": {"amount": 6.24, "amountInt": 624, "currency": "EUR"}},
    "deals": [
        {
            "shop": {"id": 61, "name": "Steam"},
            "price": {"amount": 12.49, "amountInt": 1249, "currency": "EUR"},
            "regular": {"amount": 24.99, "amountInt": 2499, "currency": "EUR"},
            "cut": 50,
            "url": "https://itad.link/steam-hades",
        },
        {
            "shop": {"id": 35, "name": "GOG"},
            "price": {"amount": 24.99, "amountInt": 2499, "currency": "EUR"},
            "regular": {"amount": 24.99, "amountInt": 2499, "currency": "EUR"},
            "cut": 0,
            "url": "https://itad.link/gog-hades",
        },
    ],
}


@override_settings(ITAD_API_KEY="key", ITAD_COUNTRY="ES")
class PricesTests(TestCase):
    def setUp(self):
        self.hades = Game.objects.create(name="Hades", slug="hades", store_ids={"steam": [1145360]})
        self.unknown = Game.objects.create(name="Indie", slug="indie", store_ids={"steam": [99]})
        self.client_mock = mock.Mock(spec=prices.ITADClient)
        self.itad_ids = {("steam", 1145360): "uuid-hades", ("steam", 99): None}
        self.client_mock.lookup.side_effect = lambda store, ids: {i: self.itad_ids.get((store, i)) for i in ids}
        self.client_mock.prices.return_value = [ITAD_HADES]

    def test_resolves_ids_and_stores_prices(self):
        GamePrice.objects.create(
            game=self.unknown, shop_id=1, shop_name="Old", price=1, regular_price=1, currency="EUR", url="https://o.test"
        )
        updated = prices.update_prices([self.hades, self.unknown], self.client_mock)


        self.assertEqual(updated, 1)
        self.client_mock.lookup.assert_called_once_with("steam", [99, 1145360])
        self.client_mock.prices.assert_called_once_with(["uuid-hades"])
        self.hades.refresh_from_db()
        self.assertEqual(self.hades.prices_edition, "")
        self.assertEqual(str(self.hades.historical_low), "6.24")
        self.assertEqual([(p.shop_name, str(p.price), p.cut) for p in self.hades.prices.all()],
                         [("Steam", "12.49", 50), ("GOG", "24.99", 0)])
        self.assertEqual(self.unknown.prices.count(), 0)

    def test_keeps_cheapest_deal_per_shop(self):
        steam_key = {**ITAD_HADES["deals"][1], "price": {"amount": 19.99, "currency": "EUR"}}
        self.client_mock.prices.return_value = [{**ITAD_HADES, "deals": [*ITAD_HADES["deals"], steam_key]}]
        prices.update_prices([self.hades], self.client_mock)
        self.assertEqual([(p.shop_name, str(p.price)) for p in self.hades.prices.all()],
                         [("Steam", "12.49"), ("GOG", "19.99")])

    def test_rerun_replaces_prices(self):
        prices.update_prices([self.hades], self.client_mock)
        prices.update_prices([self.hades], self.client_mock)
        self.assertEqual(self.hades.prices.count(), 2)

    def test_merges_deals_of_all_store_ids_of_the_game(self):
        # e.g. an original release on Steam and a re-release, known to ITAD as two games
        self.hades.store_ids = {"steam": [1145360, 3717340], "gog": ["1478349159"]}
        self.itad_ids.update({("steam", 3717340): "uuid-new", ("gog", "1478349159"): "uuid-hades"})
        cheaper_steam = {**ITAD_HADES["deals"][0], "price": {"amount": 9.99, "currency": "EUR"}}
        self.client_mock.prices.return_value = [
            ITAD_HADES,
            {"id": "uuid-new", "historyLow": {"all": {"amount": 4.99, "currency": "EUR"}}, "deals": [cheaper_steam]},
        ]
        prices.update_prices([self.hades], self.client_mock)
        self.client_mock.prices.assert_called_once_with(["uuid-hades", "uuid-new"])
        self.assertEqual([(p.shop_name, str(p.price)) for p in self.hades.prices.all()],
                         [("Steam", "9.99"), ("GOG", "24.99")])
        self.assertEqual(str(self.hades.historical_low), "4.99")

    def test_free_games_show_gratis(self):
        free = {**ITAD_HADES["deals"][0], "price": {"amount": 0, "currency": "EUR"}, "regular": {"amount": 0}, "cut": 0}
        self.client_mock.prices.return_value = [{**ITAD_HADES, "historyLow": None, "deals": [free]}]
        prices.update_prices([self.hades], self.client_mock)
        self.assertTrue(self.hades.is_free)
        response = self.client.get(self.hades.get_absolute_url())
        self.assertContains(response, "Free to play")
        self.assertContains(response, "<strong>Gratis</strong>")

    def test_falls_back_to_edition_when_game_has_no_deals(self):
        self.hades.store_alternatives = [{"name": "Hades GOTY", "steam": [200]}]
        self.hades.save()
        self.itad_ids.update({("steam", 1145360): "uuid-old", ("steam", 200): "uuid-hades"})
        self.client_mock.prices.side_effect = lambda ids: [
            {"id": "uuid-old", "historyLow": None, "deals": []}, ITAD_HADES
        ]
        self.assertEqual(prices.update_prices([self.hades, self.unknown], self.client_mock), 1)
        self.assertEqual(self.client_mock.lookup.call_args_list,
                         [mock.call("steam", [99, 1145360]), mock.call("steam", [200])])
        self.hades.refresh_from_db()
        self.assertEqual((self.hades.prices_edition, self.hades.prices.count()), ("Hades GOTY", 2))
        self.assertContains(self.client.get(self.hades.get_absolute_url()), "Edición a la venta: Hades GOTY")

    def test_retries_when_rate_limited(self):
        limited = mock.Mock(status_code=429, headers={"Retry-After": "1"})
        ok = mock.Mock(status_code=200, **{"json.return_value": {}})
        with mock.patch("games.prices.requests.post", side_effect=[limited, ok]) as post,                 mock.patch("games.prices.time.sleep") as sleep:
            prices.ITADClient().lookup("steam", [1])
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_client_sends_key_country_and_store_ids(self):
        response = mock.Mock(**{"json.return_value": {"app/1145360": "uuid-hades", "1478349159": "uuid-gog"}})
        with mock.patch("games.prices.requests.post", return_value=response) as post:
            self.assertEqual(prices.ITADClient().lookup("steam", [1145360]), {1145360: "uuid-hades"})
            self.assertEqual(prices.ITADClient().lookup("gog", ["1478349159"]), {"1478349159": "uuid-gog"})
            prices.ITADClient().prices(["uuid-hades"])
        self.assertEqual(post.call_args_list[0].args[0], "https://api.isthereanydeal.com/lookup/id/shop/61/v1")
        self.assertEqual(post.call_args_list[0].kwargs["json"], ["app/1145360"])
        self.assertEqual(post.call_args_list[1].args[0], "https://api.isthereanydeal.com/lookup/id/shop/35/v1")
        self.assertEqual(post.call_args.kwargs["params"], {"country": "ES"})
        self.assertEqual(post.call_args.kwargs["headers"], {"ITAD-API-Key": "key"})

    @override_settings(ITAD_API_KEY="")
    def test_command_without_api_key_skips_pc_prices(self):
        stderr = io.StringIO()
        with mock.patch("games.xbox.update_xbox_prices", return_value=0) as update_xbox:
            call_command("update_prices", stdout=io.StringIO(), stderr=stderr)
        self.assertIn("ITAD_API_KEY", stderr.getvalue())
        update_xbox.assert_called_once()

    def test_command_reports_api_errors(self):
        with mock.patch("games.prices.update_prices", side_effect=prices.ITADError("caído")),                 self.assertRaisesMessage(CommandError, "caído"):
            call_command("update_prices", stdout=io.StringIO())

    def test_detail_shows_critic_score_and_prices(self):
        self.hades.critic_rating = 92.6
        self.hades.critic_rating_count = 41
        self.hades.save()
        prices.update_prices([self.hades], self.client_mock)
        response = self.client.get(self.hades.get_absolute_url())
        self.assertContains(response, "Nota de la crítica")
        self.assertContains(response, ">93<")
        self.assertContains(response, "12,49 €")
        self.assertContains(response, "-50%")
        self.assertContains(response, "https://itad.link/steam-hades")
        self.assertContains(response, "IsThereAnyDeal")

    def test_featured_shops_first_and_cheapest_pointed_out(self):
        deal = ITAD_HADES["deals"][0]
        shops = [(6, "Fanatical", 5), (35, "GOG", 20), (36, "GreenManGaming", 15), (61, "Steam", 25)]
        self.client_mock.prices.return_value = [{**ITAD_HADES, "deals": [
            {**deal, "shop": {"id": shop_id, "name": name}, "price": {"amount": amount, "currency": "EUR"}}
            for shop_id, name, amount in shops
        ]}]
        prices.update_prices([self.hades], self.client_mock)
        featured, listed, folded, best = self.hades.price_groups()
        self.assertEqual([p.shop_name for p in featured], ["Steam", "GOG"])
        self.assertEqual((listed, [p.shop_name for p in folded]), ([], ["Fanatical", "GreenManGaming"]))
        self.assertEqual(best.shop_name, "Fanatical")
        response = self.client.get(self.hades.get_absolute_url())
        self.assertContains(response, 'class="shop-tile"', count=2)
        self.assertContains(response, "Ver 2 tiendas más")

    def test_lists_cheapest_stores_when_none_is_featured(self):
        deal = ITAD_HADES["deals"][0]
        self.client_mock.prices.return_value = [{**ITAD_HADES, "deals": [
            {**deal, "shop": {"id": shop_id, "name": f"Tienda {shop_id}"}} for shop_id in range(1, 8)
        ]}]
        prices.update_prices([self.hades], self.client_mock)
        featured, listed, folded, best = self.hades.price_groups()
        self.assertEqual((len(featured), len(listed), len(folded), best), (0, 5, 2, None))

    def test_detail_says_when_no_prices_were_found(self):
        response = self.client.get(self.unknown.get_absolute_url())
        self.assertNotContains(response, "Nota de la crítica")
        self.assertNotContains(response, 'class="shop-tile"')
        self.assertContains(response, "No se han encontrado precios en las tiendas.")

        GamePrice.objects.create(
            game=self.unknown, source=GamePrice.Source.XBOX, shop_id=GamePrice.XBOX_SHOP_ID, shop_name="Xbox",
            price=20, regular_price=20, currency="EUR", url="https://x.test",
        )
        response = self.client.get(self.unknown.get_absolute_url())
        self.assertContains(response, "No se han encontrado precios en tiendas de PC.")
        self.assertNotContains(response, "No se han encontrado precios en las tiendas.")


def xbox_product(product_id, *offers):
    """Catalog product with (list price, msrp, platform) purchase offers."""
    return {
        "ProductId": product_id,
        "DisplaySkuAvailabilities": [{"Availabilities": [
            {
                "Actions": ["Details", "Purchase"],
                "Conditions": {"ClientConditions": {"AllowedPlatforms": [{"PlatformName": platform}]}},
                "OrderManagementData": {"Price": {"ListPrice": price, "MSRP": msrp, "CurrencyCode": "EUR"}},
            }
            for price, msrp, platform in offers
        ]}],
    }


@override_settings(ITAD_COUNTRY="ES")
class XboxTests(TestCase):
    def setUp(self):
        self.game = Game.objects.create(
            name="Hades", slug="hades", xbox_product_ids=["9NOTFORXBOX0", "9P8DL6W0JBXP"]
        )

    def catalog(self, *products):
        response = mock.Mock(**{"json.return_value": {"Products": list(products)}})
        return mock.patch("games.xbox.requests.get", return_value=response)

    def test_stores_cheapest_paid_console_offer(self):
        products = [
            xbox_product("9NOTFORXBOX0", (5, 5, "Windows.Desktop")),
            xbox_product("9P8DL6W0JBXP", (0, 0, "Windows.Xbox"), (12.49, 24.99, "Windows.Xbox"), (20, 24.99, "Windows.Xbox")),
        ]
        with self.catalog(*products) as get:
            self.assertEqual(xbox.update_xbox_prices([self.game]), 1)
        self.assertEqual(get.call_args.kwargs["params"]["market"], "ES")
        price = self.game.prices.get()
        self.assertEqual((price.shop_name, str(price.price), str(price.regular_price), price.cut),
                         ("Xbox", "12.49", "24.99", 50))
        self.assertEqual(price.url, "https://www.xbox.com/es-ES/games/store/_/9P8DL6W0JBXP")

    def test_pc_and_xbox_updates_keep_each_others_prices(self):
        with self.catalog(xbox_product("9P8DL6W0JBXP", (20, 20, "Windows.Xbox"))):
            xbox.update_xbox_prices([self.game])
        self.game.store_ids = {"steam": [1145360]}
        client = mock.Mock(spec=prices.ITADClient)
        client.lookup.return_value = {1145360: "uuid-hades"}
        client.prices.return_value = [ITAD_HADES]
        prices.update_prices([self.game], client)
        self.assertEqual(sorted(p.shop_name for p in self.game.prices.all()), ["GOG", "Steam", "Xbox"])
        with self.catalog():
            xbox.update_xbox_prices([self.game])
        self.assertEqual(sorted(p.shop_name for p in self.game.prices.all()), ["GOG", "Steam"])

    def test_detail_shows_xbox_tile_and_console_store_searches(self):
        self.game.platforms.add(
            Platform.objects.create(name="PlayStation 5", slug="ps5"),
            Platform.objects.create(name="Xbox Series X|S", slug="series-x-s"),
        )
        url = self.game.get_absolute_url()
        response = self.client.get(url)
        self.assertContains(response, "https://store.playstation.com/es-es/search/Hades")
        self.assertContains(response, 'class="shop-tile shop-tile-search"', count=2)
        self.assertContains(response, "Xbox Store")
        self.assertNotContains(response, "Nintendo eShop")

        with self.catalog(xbox_product("9P8DL6W0JBXP", (20, 20, "Windows.Xbox"))):
            xbox.update_xbox_prices([self.game])
        response = self.client.get(url)
        self.assertContains(response, 'class="shop-tile"', count=1)
        self.assertContains(response, 'class="shop-tile shop-tile-search"', count=1)
        self.assertNotContains(response, "Xbox Store")

    def test_searches_current_xbox_games_by_exact_title_when_igdb_ids_are_not_sold(self):
        self.game.platforms.add(Platform.objects.create(name="Xbox One", slug="xbox-one"))
        search = {"Results": [{"Products": [
            {"ProductId": "9DLC00000000", "Title": "Hades Soundtrack", "Type": "Durable"},
            {"ProductId": "9OTHER000000", "Title": "Hades II", "Type": "Game"},
            {"ProductId": "9NEWHADES000", "Title": "Hades™", "Type": "Game"},
        ]}]}

        def get(url, params, timeout):
            if url == xbox.SEARCH_URL:
                return mock.Mock(**{"json.return_value": search})
            ids = params["bigIds"].split(",")
            products = [xbox_product(pid, (14.99, 24.99, "Windows.Xbox")) for pid in ids if pid not in self.game.xbox_product_ids]
            return mock.Mock(**{"json.return_value": {"Products": products}})

        with mock.patch("games.xbox.requests.get", side_effect=get) as requests_get:
            self.assertEqual(xbox.update_xbox_prices([self.game], sleep=mock.Mock()), 1)
        self.assertEqual(self.game.prices.get().url, "https://www.xbox.com/es-ES/games/store/_/9NEWHADES000")
        searched = [c for c in requests_get.call_args_list if c.args[0] == xbox.SEARCH_URL]
        self.assertEqual(searched[0].kwargs["params"]["query"], "Hades")

    def test_search_skips_titles_shared_by_several_games_on_sale(self):
        self.game.platforms.add(Platform.objects.create(name="Xbox One", slug="xbox-one"))
        search = {"Results": [{"Products": [
            {"ProductId": "9ORIGINAL000", "Title": "Hades", "Type": "Game"},
            {"ProductId": "9REMAKE00000", "Title": "Hades", "Type": "Game"},
        ]}]}

        def get(url, params, timeout):
            if url == xbox.SEARCH_URL:
                return mock.Mock(**{"json.return_value": search})
            ids = [pid for pid in params["bigIds"].split(",") if pid not in self.game.xbox_product_ids]
            return mock.Mock(**{"json.return_value": {"Products": [
                xbox_product(pid, (19.99, 19.99, "Windows.Xbox")) for pid in ids
            ]}})

        with mock.patch("games.xbox.requests.get", side_effect=get):
            self.assertEqual(xbox.update_xbox_prices([self.game], sleep=mock.Mock()), 0)

    def test_no_search_for_titles_shared_in_the_catalog(self):
        self.game.platforms.add(Platform.objects.create(name="Xbox One", slug="xbox-one"))
        Game.objects.create(name="HADES", slug="hades-remake")
        with self.catalog() as get:
            xbox.update_xbox_prices([self.game], sleep=mock.Mock())
        self.assertFalse(any(c.args[0] == xbox.SEARCH_URL for c in get.call_args_list))

    def test_manual_id_replaces_igdb_ids_and_search(self):
        self.game.platforms.add(Platform.objects.create(name="Xbox One", slug="xbox-one"))
        self.game.xbox_product_id_override = "9MANUAL00000"
        with self.catalog() as get:
            self.assertEqual(xbox.update_xbox_prices([self.game]), 0)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.kwargs["params"]["bigIds"], "9MANUAL00000")

    def test_igdb_keeps_store_product_ids_of_game_and_editions(self):
        def microsoft(*uids):
            return {"external_games": [{"id": i, "uid": uid, "external_game_source": 11} for i, uid in enumerate(uids)]}

        igdb.upsert_games([{
            **IGDB_HADES,
            **microsoft("xbox36066acd000-77fe-1000-9115-d802545407d8", "BTBMZQ3FM2V8"),
            "editions": [{"id": 2, "name": "Hades GOTY", **microsoft("9NVB51LMXLJF", "BTBMZQ3FM2V8")}],
        }])
        self.assertEqual(Game.objects.get(igdb_id=113112).xbox_product_ids, ["BTBMZQ3FM2V8", "9NVB51LMXLJF"])


def itad_change(days_ago, shop_id, shop, price, regular, now):
    """Cambio de precio tal como lo devuelve el historial de IsThereAnyDeal."""
    when = now - timedelta(days=days_ago)
    cut = round(100 - price * 100 / regular)
    return {
        "timestamp": when.isoformat(),
        "shop": {"id": shop_id, "name": shop},
        "deal": {"price": {"amount": price, "currency": "EUR"}, "regular": {"amount": regular, "currency": "EUR"}, "cut": cut},
    }


@override_settings(ITAD_API_KEY="key", ITAD_COUNTRY="ES")
class PriceHistoryTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.game = Game.objects.create(
            name="Hades", slug="hades", store_ids={"steam": [1145360]}, itad_ids=["uuid-hades"],
            historical_low=Decimal("6.24"), historical_low_currency="EUR",
        )
        self.steam = GamePrice.objects.create(
            game=self.game, shop_id=61, shop_name="Steam", price=Decimal("24.99"), regular_price=Decimal("24.99"),
            currency="EUR", url="https://itad.link/steam",
        )
        self.client_mock = mock.Mock(spec=prices.ITADClient)

    def load(self, *changes):
        """Guarda como historial del juego los cambios dados (simulando ITAD)."""
        self.client_mock.history.return_value = list(changes)
        history.update_history(self.game, self.client_mock, now=self.now)
        self.game.refresh_from_db()

    def test_series_keeps_the_cheapest_shop_and_starts_at_the_regular_price(self):
        self.load(
            itad_change(300, 61, "Steam", 12.49, 24.99, self.now),
            itad_change(290, 61, "Steam", 24.99, 24.99, self.now),
            itad_change(200, 35, "GOG", 9.99, 24.99, self.now),
            itad_change(190, 35, "GOG", 24.99, 24.99, self.now),
        )
        shops = self.game.price_history["shops"]
        self.assertEqual(sorted(shops), ["35", "61"])
        self.assertEqual([point[1:] for point in shops["35"]["points"]], [[24.99, 0], [9.99, 60], [24.99, 0]])
        series = history.lowest_series(self.game.price_history)
        self.assertEqual([point[1:] for point in series], [
            # Al acabar la rebaja de GOG, Steam y GOG valen lo mismo: se queda la primera tienda.
            (Decimal("24.99"), 0, "Steam"), (Decimal("12.49"), 50, "Steam"), (Decimal("24.99"), 0, "Steam"),
            (Decimal("9.99"), 60, "GOG"), (Decimal("24.99"), 0, "Steam"),
        ])
        gog = history.lowest_series(self.game.price_history, {35})
        self.assertEqual([point[1] for point in gog], [Decimal("24.99"), Decimal("9.99"), Decimal("24.99")])
        since = self.client_mock.history.call_args.args[1]
        self.assertEqual((self.now - since).days, 730)

    def test_wait_when_it_dropped_below_today_several_times(self):
        self.load(
            itad_change(300, 61, "Steam", 12.49, 24.99, self.now),
            itad_change(290, 61, "Steam", 24.99, 24.99, self.now),
            itad_change(120, 61, "Steam", 9.99, 24.99, self.now),
            itad_change(110, 61, "Steam", 24.99, 24.99, self.now),
        )
        verdict = history.summary(self.game, self.now)["verdict"]
        self.assertEqual(verdict["code"], "espera")
        self.assertIn("2 veces, hasta 9,99 €", verdict["text"])

    def test_overlapping_sales_count_once(self):
        self.load(
            itad_change(100, 61, "Steam", 9.99, 24.99, self.now),
            itad_change(99, 61, "Steam", 24.99, 24.99, self.now),
            itad_change(98, 61, "Steam", 9.99, 24.99, self.now),
            itad_change(90, 61, "Steam", 24.99, 24.99, self.now),
        )
        self.assertIn("una vez", history.summary(self.game, self.now)["verdict"]["text"])

    def test_good_price_and_historical_low(self):
        self.load()
        self.assertEqual(history.summary(self.game, self.now)["verdict"]["code"], "bueno")
        GamePrice.objects.filter(pk=self.steam.pk).update(price=Decimal("6.24"), cut=75)
        self.assertEqual(history.summary(self.game, self.now)["verdict"]["code"], "minimo")

    def test_last_big_sale_and_chart(self):
        self.load(
            itad_change(200, 61, "Steam", 6.24, 24.99, self.now),
            itad_change(190, 61, "Steam", 24.99, 24.99, self.now),
        )
        summary = history.summary(self.game, self.now)
        self.assertEqual((summary["last_big_sale"]["cut"], summary["last_big_sale"]["price"]), (75, Decimal("6.24")))
        chart = summary["chart"]
        self.assertEqual((chart["low"], chart["high"]), (Decimal("6.24"), Decimal("24.99")))
        self.assertEqual(chart["low_y"], "84.0")  # con punto aunque la web esté en español
        hover = json.loads(chart["hover"])
        self.assertEqual(
            [(point["price"], point["cut"], point["shop"]) for point in hover["points"]],
            [("24,99 €", 0, "Steam"), ("6,24 €", 75, "Steam"), ("24,99 €", 0, "Steam"), ("24,99 €", 0, "Steam")],
        )
        self.assertEqual(hover["points"][0]["t"], hover["start"])  # el primer tramo empieza con la gráfica
        self.assertTrue(chart["line"].startswith("6.0,6.0"))

    def test_detail_loads_history_lazily_then_inline(self):
        response = self.client.get(self.game.get_absolute_url())
        self.assertContains(response, 'hx-get="/juegos/hades/historial-de-precios/"')
        self.client_mock.history.return_value = []  # sin cambios en dos años: siempre al precio de hoy
        with mock.patch("games.prices.ITADClient", return_value=self.client_mock):
            response = self.client.get(reverse("games:price_history", args=["hades"]))
            self.client.get(reverse("games:price_history", args=["hades"]))
        self.assertContains(response, "Buen precio")
        self.client_mock.history.assert_called_once()  # el segundo usa el guardado
        page = self.client.get(self.game.get_absolute_url())
        self.assertContains(page, "price-chart")
        self.assertContains(page, "data-chart=")

    def test_chart_coordinates_use_a_decimal_point(self):
        self.load(itad_change(200, 61, "Steam", 6.24, 24.99, self.now), itad_change(190, 61, "Steam", 24.99, 24.99, self.now))
        response = self.client.get(self.game.get_absolute_url())
        self.assertContains(response, 'y1="84.0"')
        self.assertNotRegex(response.content.decode(), r'points="[^"]*\d,\d+,')

    def test_itad_failure_still_answers(self):
        self.client_mock.history.side_effect = prices.ITADError("caído")
        with mock.patch("games.prices.ITADClient", return_value=self.client_mock), \
                self.assertLogs("games.views", "ERROR"):
            response = self.client.get(reverse("games:price_history", args=["hades"]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "verdict")

    def test_old_games_look_up_their_itad_ids(self):
        self.game.itad_ids = []
        self.game.save()
        self.client_mock.lookup.return_value = {1145360: "uuid-hades"}
        self.load()
        self.assertEqual(self.game.itad_ids, ["uuid-hades"])
        self.client_mock.history.assert_called_once_with("uuid-hades", mock.ANY)

    def test_update_prices_remembers_itad_ids_and_drops_stale_history(self):
        self.game.itad_ids, self.game.price_history = ["uuid-old"], [["2026-01-01T00:00:00+00:00", 1.0, 0, "X"]]
        self.game.save()
        self.client_mock.lookup.side_effect = lambda store, ids: {i: "uuid-hades" for i in ids}
        self.client_mock.prices.return_value = [ITAD_HADES]
        prices.update_prices([self.game], self.client_mock)
        self.game.refresh_from_db()
        self.assertEqual((self.game.itad_ids, self.game.price_history), (["uuid-hades"], {}))

    def test_old_format_is_asked_again(self):
        self.game.price_history, self.game.price_history_updated_at = [["2026-01-01T00:00:00+00:00", 1.0, 0, "X"]], self.now
        self.assertTrue(history.needs_update(self.game, self.now))
        self.assertIsNone(history.summary(self.game, self.now))

    def test_chart_of_one_shop_or_the_users_shops(self):
        GamePrice.objects.create(
            game=self.game, shop_id=35, shop_name="GOG", price=Decimal("19.99"), regular_price=Decimal("24.99"),
            cut=20, currency="EUR", url="https://itad.link/gog",
        )
        self.load(
            itad_change(200, 61, "Steam", 6.24, 24.99, self.now),
            itad_change(190, 61, "Steam", 24.99, 24.99, self.now),
            itad_change(10, 35, "GOG", 19.99, 24.99, self.now),
        )
        url = reverse("games:price_history", args=["hades"])
        response = self.client.get(url, {"tienda": "35"})
        self.assertEqual(response.context["price_history"]["current"].shop_name, "GOG")
        self.assertContains(response, "Lo más barato en dos años")
        self.assertContains(response, "mínimo en dos años")
        options = [value for value, _ in response.context["history_shops"]]
        self.assertEqual(options, ["todas", "61", "35"])  # sin sesión no hay «Tus tiendas»; Steam antes que GOG
        # Con sesión y sus tiendas elegidas (solo GOG), es lo que sale por defecto.
        user = User.objects.create_user("ana", "ana@example.com", "x")
        user.profile.shops = [35]
        user.profile.save()
        self.client.force_login(user)
        response = self.client.get(url)
        self.assertEqual(response.context["history_shop"], "")
        self.assertEqual(response.context["price_history"]["current"].shop_name, "GOG")
        self.assertEqual(response.context["history_shops"][0], ("", "Tus tiendas"))
        response = self.client.get(url, {"tienda": "todas"})
        self.assertEqual(response.context["price_history"]["current"].shop_name, "GOG")  # el más barato de todas
        self.assertTrue(response.context["price_history"]["all_shops"])

    def test_a_shop_that_does_not_sell_it_now_says_so(self):
        self.load(itad_change(100, 35, "GOG", 9.99, 24.99, self.now))
        response = self.client.get(reverse("games:price_history", args=["hades"]), {"tienda": "35"})
        self.assertIsNone(response.context["price_history"])
        self.assertContains(response, "Esa tienda no lo vende ahora")


class PreferredShopsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("ana", "ana@example.com", "x")
        cls.game = Game.objects.create(name="Hades", slug="hades")
        for shop_id, name, price in ((61, "Steam", "24.99"), (35, "GOG", "19.99"), (0, "Xbox", "29.99")):
            GamePrice.objects.create(
                game=cls.game, shop_id=shop_id, shop_name=name, price=Decimal(price), regular_price=Decimal(price),
                currency="EUR", url=f"https://shop.test/{shop_id}",
                source=GamePrice.Source.XBOX if shop_id == 0 else GamePrice.Source.ITAD,
            )

    def setUp(self):
        self.client.force_login(self.user)

    def test_choose_shops_in_the_profile(self):
        response = self.client.get(reverse("edit_profile"))
        self.assertContains(response, "Tiendas en las que compras")
        self.assertEqual([name for _, name, _ in response.context["shop_choices"]], ["Steam", "GOG", "Xbox"])
        self.client.post(reverse("shops_update"), {"shops": ["61", "0", "999"]})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.shops, [0, 61])  # la desconocida se ignora
        # Marcarlas todas es lo mismo que ninguna: así se ven también las tiendas nuevas.
        self.client.post(reverse("shops_update"), {"shops": ["61", "35", "0"]})
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.shops, [])

    def test_game_page_shows_only_the_users_shops_unless_asked(self):
        self.user.profile.shops = [61]
        self.user.profile.save()
        response = self.client.get(self.game.get_absolute_url())
        self.assertEqual([price.shop_name for price in response.context["featured_prices"]], ["Steam"])
        self.assertContains(response, "2 tiendas en las que no compras ocultas")
        response = self.client.get(self.game.get_absolute_url(), {"tiendas": "todas"})
        self.assertEqual([price.shop_name for price in response.context["featured_prices"]], ["Steam", "GOG", "Xbox"])
        self.assertContains(response, "Solo las mías")

    def test_none_of_the_users_shops_sells_it(self):
        self.user.profile.shops = [16]  # Epic
        self.user.profile.save()
        self.assertContains(self.client.get(self.game.get_absolute_url()), "Ninguna de tus tiendas lo vende")


def igdb_language(code, support):
    """Un language_supports de IGDB: idioma por su código y tipo (1 voces, 2 subtítulos, 3 interfaz)."""
    return {"language": {"locale": code}, "language_support_type": support}


# Hades tal como llega de IGDB con los campos de castellano.
IGDB_HADES_ES = {
    **IGDB_HADES,
    "language_supports": [igdb_language("es-ES", 2), igdb_language("es-ES", 3), igdb_language("en-US", 1)],
    "alternative_names": [
        {"name": "Hades: La Batalla del Inframundo", "comment": "Spanish title"},
        {"name": "HDS", "comment": "Acronym"},
        {"name": "hades.exe", "comment": "Windows Executable"},
    ],
    "game_localizations": [{"region": 4, "name": "Hades", "cover": {"image_id": "coeu1"}}],
}
STEAM_LANGUAGES_HTML = (
    "Inglés<strong>*</strong>, Español de España, Español de Hispanoamérica<strong>*</strong>, Klingon"
    "<br><strong>*</strong>idiomas con localización de audio"
)


def steam_answer(app_id, languages=STEAM_LANGUAGES_HTML, summary="Un &quot;roguelike&quot; <b>infernal</b>."):
    """Respuesta de appdetails de Steam (pedida en español) para una app."""
    response = mock.Mock(status_code=200)
    response.raise_for_status.return_value = None
    response.json.return_value = {
        str(app_id): {"success": True, "data": {"supported_languages": languages, "short_description": summary}}
    }
    return response


class SpanishTests(TestCase):
    """Fichas en castellano: idiomas, nombre en España, portada europea, sinopsis en español y filtros."""

    def test_igdb_languages(self):
        self.assertEqual(spanish.igdb_languages(IGDB_HADES_ES), {"es-ES": ["text"], "en-US": ["audio"]})

    def test_steam_languages(self):
        self.assertEqual(spanish.steam_languages(STEAM_LANGUAGES_HTML), {
            "en-US": ["audio", "text"], "es-ES": ["text"], "es-MX": ["audio", "text"],
        })

    def test_spanish_levels(self):
        cases = [
            ({"igdb": {"es-ES": ["audio"]}}, ("es", "es")),
            ({"igdb": {"es-MX": ["audio"], "es-ES": ["text"]}}, ("latam", "es")),
            ({"igdb": {"en-US": ["audio", "text"]}}, ("no", "no")),
            ({"igdb": {}}, ("", "")),
            # Si una fuente dice que está doblado, lo está aunque la otra no lo diga.
            ({"igdb": {"es-ES": ["text"]}, "steam": {"es-ES": ["audio", "text"]}}, ("es", "es")),
        ]
        for languages, expected in cases:
            game = Game(name="X", slug="x", languages=languages)
            game.refresh_spanish()
            self.assertEqual((game.spanish_audio, game.spanish_text), expected, languages)

    def test_names_and_european_cover(self):
        name_es, search = spanish.spanish_names(IGDB_HADES_ES)
        self.assertEqual(name_es, "Hades: La Batalla del Inframundo")
        self.assertEqual(search, "hades: la batalla del inframundo | hds")
        self.assertEqual(spanish.europe_cover(IGDB_HADES_ES), "coeu1")

    def test_clean_text(self):
        self.assertEqual(spanish.clean_text("&quot;Hola&quot;<br>adiós  <b>ya</b>"), '"Hola"\nadiós ya')

    def test_import_from_igdb_saves_the_spanish_data(self):
        igdb.upsert_games([IGDB_HADES_ES])
        game = Game.objects.get(igdb_id=113112)
        self.assertEqual((game.spanish_audio, game.spanish_text), ("no", "es"))
        self.assertEqual(game.spain_name, "Hades: La Batalla del Inframundo")
        self.assertIn("coeu1", game.cover_url)
        # Volver a importar de IGDB no borra los idiomas que vinieron de Steam.
        game.languages = {**game.languages, "steam": {"es-ES": ["audio", "text"]}}
        game.save()
        igdb.upsert_games([IGDB_HADES_ES])
        game.refresh_from_db()
        self.assertEqual(game.spanish_audio, "es")

    def test_igdb_query_asks_for_the_spanish_fields(self):
        self.assertIn("language_supports.language.locale", igdb.GAME_FIELDS)
        self.assertIn("game_localizations.cover.image_id", igdb.GAME_FIELDS)

    def test_steam_details_retries_when_steam_fails(self):
        sleep = mock.Mock()
        with mock.patch("games.spanish.requests.get", side_effect=[spanish.requests.ConnectionError("corte"), steam_answer(1)]):
            languages, summary = spanish.steam_details(1, sleep=sleep)
        self.assertEqual(summary, 'Un "roguelike" infernal.')
        self.assertIn("es-MX", languages)
        sleep.assert_called_once_with(spanish.RETRY_WAITS[0])
        with mock.patch("games.spanish.requests.get", side_effect=spanish.requests.ConnectionError("corte")), \
                self.assertRaises(spanish.StoreError):
            spanish.steam_details(1, sleep=sleep)

    def test_steam_details_of_an_unknown_app(self):
        response = steam_answer(2)
        response.json.return_value = {"2": {"success": False}}
        with mock.patch("games.spanish.requests.get", return_value=response):
            self.assertEqual(spanish.steam_details(2), ({}, ""))

    def test_localize_games_from_steam_and_microsoft(self):
        hades = Game.objects.create(name="Hades", slug="hades", steam_app_id=1145360, languages={"igdb": {}})
        celeste = Game.objects.create(name="Celeste", slug="celeste", steam_app_id=504230)
        halo = Game.objects.create(name="Halo", slug="halo", xbox_product_ids=["9NP1P1WFS0LB"])
        sleep = mock.Mock()
        answers = [steam_answer(1145360), steam_answer(504230, summary="")]
        with mock.patch("games.spanish.requests.get", side_effect=answers), \
                mock.patch("games.xbox.fetch_descriptions", return_value={"9NP1P1WFS0LB": "El Jefe Maestro vuelve."}) as fetch:
            found = spanish.localize_games([hades, celeste, halo], pause=1.5, sleep=sleep)
        self.assertEqual(found, {"steam": 1, "xbox": 1, "none": 1})
        sleep.assert_called_once_with(1.5)  # una pausa entre las dos peticiones a Steam
        fetch.assert_called_once_with(["9NP1P1WFS0LB"])
        hades.refresh_from_db()
        halo.refresh_from_db()
        self.assertEqual((hades.summary_es, hades.summary_es_source), ('Un "roguelike" infernal.', "steam"))
        self.assertEqual((hades.spanish_audio, hades.spanish_text), ("latam", "es"))
        self.assertIsNotNone(hades.localized_at)
        self.assertEqual((halo.summary_es, halo.summary_es_source), ("El Jefe Maestro vuelve.", "xbox"))

    def test_localize_games_stops_when_steam_stops_answering(self):
        hades = Game.objects.create(name="Hades", slug="hades", steam_app_id=1145360)
        celeste = Game.objects.create(name="Celeste", slug="celeste", steam_app_id=504230)
        with mock.patch("games.spanish.steam_details", side_effect=[({}, "Bien."), spanish.StoreError("429")]), \
                self.assertRaises(spanish.StoreError):
            spanish.localize_games([hades, celeste], sleep=mock.Mock())
        hades.refresh_from_db()
        celeste.refresh_from_db()
        self.assertEqual(hades.summary_es, "Bien.")
        self.assertIsNone(celeste.localized_at)  # se volverá a pedir

    @override_settings(IGDB_CLIENT_ID="id", IGDB_CLIENT_SECRET="secret")
    def test_command_fills_igdb_data_and_asks_the_stores(self):
        done = Game.objects.create(name="Celeste", slug="celeste", igdb_id=1, languages={"igdb": {"es-ES": ["text"]}})
        hades = Game.objects.create(name="Hades", slug="hades", igdb_id=113112, steam_app_id=1145360)
        out = io.StringIO()
        with mock.patch.object(igdb.IGDBClient, "spanish_data", return_value=[IGDB_HADES_ES]) as igdb_data, \
                mock.patch("games.spanish.localize_games", return_value={"steam": 1, "xbox": 0, "none": 0}) as stores:
            call_command("localize_games", stdout=out)
        igdb_data.assert_called_once_with([113112])  # Celeste ya tenía sus datos de IGDB
        self.assertEqual(stores.call_args.args[0], [hades])  # Celeste no está en ninguna tienda
        hades.refresh_from_db()
        self.assertEqual(hades.spanish_text, "es")
        self.assertIn("1 con sinopsis de Steam", out.getvalue())
        self.assertEqual(done.spanish_text, "")

    def test_game_page_in_spanish(self):
        game = Game.objects.create(
            name="Hades", slug="hades", summary="A rogue-like dungeon crawler.", name_es="Hades: La Batalla",
            summary_es="Desafía al dios de los muertos.", summary_es_source="steam",
            languages={"igdb": {"es-ES": ["audio", "text"], "en-US": ["audio", "text"], "ja-JP": ["text"]}},
        )
        game.refresh_spanish()
        game.save()
        response = self.client.get(game.get_absolute_url())
        for text in ("En España: <strong>Hades: La Batalla</strong>", "Voces en castellano", "Textos en castellano",
                     "Desafía al dios de los muertos.", "Sinopsis de Steam", "Descripción de IGDB (en inglés)",
                     "Idiomas <span class=\"muted\">(3)</span>", "Japonés"):
            self.assertContains(response, text, html=False)
        self.assertEqual([row[0] for row in game.language_rows], ["Español (España)", "Inglés", "Japonés"])

    def test_game_page_without_spanish_data(self):
        game = Game.objects.create(name="Indie", slug="indie", summary="Only in English.")
        response = self.client.get(game.get_absolute_url())
        self.assertContains(response, "En inglés")
        self.assertNotContains(response, "spanish-badges")
        game.languages = {"igdb": {"en-US": ["text"]}}
        game.refresh_spanish()
        game.save()
        self.assertContains(self.client.get(game.get_absolute_url()), "Sin español")

    def test_catalog_finds_spanish_and_other_names_and_filters_by_language(self):
        sims = Game.objects.create(name="The Sims 4", slug="the-sims-4", name_es="Los Sims 4", spanish_audio="es", spanish_text="es")
        gta = Game.objects.create(name="Grand Theft Auto V", slug="gta-v", search_names="gta v | gta 5", spanish_text="es", spanish_audio="no")
        indie = Game.objects.create(name="Indie", slug="indie", spanish_text="latam", spanish_audio="no")
        Game.objects.create(name="Sin datos", slug="sin-datos")
        url = reverse("games:catalog")

        def names(**params):
            """Nombres de los juegos del catálogo con esos parámetros."""
            return sorted(game.name for game in self.client.get(url, params).context["page"].object_list)

        self.assertEqual(names(q="los sims"), [sims.name])
        self.assertEqual(names(q="gta 5"), [gta.name])
        self.assertEqual(names(idioma="voces"), [sims.name])
        self.assertEqual(names(idioma="textos"), [gta.name, sims.name])
        self.assertEqual(names(idioma="espanol"), [gta.name, indie.name, sims.name])
        self.assertEqual(len(names(idioma="otra-cosa")), 4)

    def test_wishlist_and_library_filter_by_language(self):
        user = User.objects.create_user("ana", "ana@example.com", "x")
        dubbed = Game.objects.create(name="Doblado", slug="doblado", spanish_audio="es", spanish_text="es")
        english = Game.objects.create(name="En inglés", slug="en-ingles", spanish_audio="no", spanish_text="no")
        for game in (dubbed, english):
            WishlistItem.objects.create(user=user, game=game)
            LibraryEntry.objects.create(user=user, game=game)
        self.client.force_login(user)
        rows = self.client.get(reverse("library:wishlist"), {"idioma": "voces"}).context["rows"]
        self.assertEqual([row["wish"].game for row in rows], [dubbed])
        response = self.client.get(reverse("profile", args=["ana"]), {"idioma": "voces"})
        self.assertEqual([entry.game for entry in response.context["entries"]], [dubbed])
        self.assertIn("idioma=voces", response.context["keep_qs"])

