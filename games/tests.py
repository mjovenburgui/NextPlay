import io
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from library.models import LibraryEntry

from . import igdb, prices, xbox
from .models import Game, GamePrice, Genre, Platform

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
