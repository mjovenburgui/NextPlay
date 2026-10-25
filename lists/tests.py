from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Notification
from games.models import Game, GamePrice
from library.models import LibraryEntry, Report, WishlistItem
from social.models import Activity, Follow

from .models import LIST_MAX_GAMES, NOTE_MAX_LENGTH, GameList, ListComment, ListItem, ListLike

User = get_user_model()
HTMX = {"HTTP_HX_REQUEST": "true"}


def make_games(*names):
    """Juegos del catálogo con esos nombres, cada uno con su slug."""
    return [Game.objects.create(name=name, slug=name.lower().replace(" ", "-")) for name in names]


def add_price(game, price, low=None):
    """Pone a game un precio en Steam (rebajado a la mitad) y, si se da, su mínimo histórico."""
    GamePrice.objects.create(
        game=game, shop_id=61, shop_name="Steam", price=Decimal(price), regular_price=Decimal(price) * 2,
        currency="EUR", cut=50, url="https://store.steampowered.com/",
    )
    if low is not None:
        game.historical_low = Decimal(low)
        game.save(update_fields=["historical_low"])


class GameListModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.hades, cls.celeste, cls.tunic = make_games("Hades", "Celeste", "Tunic")

    def setUp(self):
        self.game_list = GameList.objects.create(user=self.ana, title="Indies")

    def order(self):
        return list(self.game_list.items.values_list("game__name", flat=True))

    def test_add_goes_to_the_end_and_does_not_repeat(self):
        self.assertTrue(self.game_list.add(self.hades))
        self.assertTrue(self.game_list.add(self.celeste))
        self.assertFalse(self.game_list.add(self.hades))
        self.assertEqual(self.order(), ["Hades", "Celeste"])

    def test_database_forbids_the_same_game_twice(self):
        self.game_list.add(self.hades)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ListItem.objects.create(game_list=self.game_list, game=self.hades)

    def test_remove_closes_the_gap(self):
        for game in (self.hades, self.celeste, self.tunic):
            self.game_list.add(game)
        self.assertTrue(self.game_list.remove(self.hades))
        self.assertFalse(self.game_list.remove(self.hades))
        self.assertEqual(list(self.game_list.items.values_list("position", flat=True)), [0, 1])

    def test_move_stops_at_both_ends(self):
        for game in (self.hades, self.celeste, self.tunic):
            self.game_list.add(game)
        self.assertTrue(self.game_list.move(self.tunic, 0))
        self.assertEqual(self.order(), ["Tunic", "Hades", "Celeste"])
        self.game_list.move(self.tunic, 99)
        self.assertEqual(self.order(), ["Hades", "Celeste", "Tunic"])
        self.game_list.move(self.hades, -5)
        self.assertEqual(self.order(), ["Hades", "Celeste", "Tunic"])
        other = GameList.objects.create(user=self.ana, title="Otra")
        self.assertFalse(other.move(self.hades, 0))

    def test_cards_go_from_the_last_changed(self):
        older = GameList.objects.create(user=self.ana, title="Antigua")
        GameList.objects.filter(pk=older.pk).update(updated_at=timezone.now() - timedelta(days=2))
        self.assertEqual([game_list.title for game_list in GameList.objects.with_preview()], ["Indies", "Antigua"])
        older.add(self.hades)
        self.assertEqual([game_list.title for game_list in GameList.objects.with_preview()], ["Antigua", "Indies"])

    def test_changing_its_games_updates_the_date(self):
        yesterday = timezone.now() - timedelta(days=1)
        GameList.objects.filter(pk=self.game_list.pk).update(updated_at=yesterday)
        self.game_list.add(self.hades)
        self.game_list.refresh_from_db()
        self.assertGreater(self.game_list.updated_at, yesterday)

    def test_visibility_matches_in_python_and_in_queries(self):
        private = GameList.objects.create(user=self.ana, title="Secreta", is_public=False)
        for viewer, expected in ((self.ana, {"Indies", "Secreta"}), (self.luis, {"Indies"}), (AnonymousUser(), {"Indies"})):
            self.assertEqual(set(GameList.objects.visible_to(viewer).values_list("title", flat=True)), expected)
            self.assertEqual({game_list.title for game_list in (self.game_list, private) if game_list.visible_to(viewer)}, expected)
        self.ana.profile.is_public = False
        self.ana.profile.save()
        self.assertFalse(GameList.objects.visible_to(self.luis).exists())
        self.assertFalse(self.game_list.visible_to(self.luis))
        self.assertEqual(GameList.objects.visible_to(self.ana).count(), 2)


class ListPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.hades, cls.celeste, cls.tunic = make_games("Hades", "Celeste", "Tunic")
        cls.game_list = GameList.objects.create(
            user=cls.ana, title="Indies favoritos", description="Los **mejores**.", is_ranked=True,
        )
        for game in (cls.hades, cls.celeste, cls.tunic):
            cls.game_list.add(game)

    def test_anyone_sees_a_public_list_with_its_description_and_ranking(self):
        response = self.client.get(self.game_list.get_absolute_url())
        self.assertContains(response, "Indies favoritos")
        self.assertContains(response, "Los <strong>mejores</strong>.")
        self.assertContains(response, "list-games is-ranked")
        self.assertContains(response, 'data-copy-link="http')
        self.assertIsNone(response.context["progress"])
        self.assertNotContains(response, "Has jugado")
        # Es una página de la comunidad, no del catálogo: «Juegos» no se marca en la cabecera.
        self.assertContains(response, f'class="" href="{reverse("games:catalog")}"')
        response = self.client.get(self.hades.get_absolute_url())
        self.assertContains(response, f'class="is-active" href="{reverse("games:catalog")}"')

    def test_private_list_or_profile_is_only_for_its_owner(self):
        self.game_list.is_public = False
        self.game_list.save()
        self.client.force_login(self.luis)
        self.assertEqual(self.client.get(self.game_list.get_absolute_url()).status_code, 404)
        self.client.force_login(self.ana)
        response = self.client.get(self.game_list.get_absolute_url())
        self.assertContains(response, "Privada: solo la ves tú")
        self.assertNotContains(response, 'data-copy-link="http')
        self.game_list.is_public = True
        self.game_list.save()
        self.ana.profile.is_public = False
        self.ana.profile.save()
        self.client.force_login(self.luis)
        self.assertEqual(self.client.get(self.game_list.get_absolute_url()).status_code, 404)

    def test_wrong_username_gives_404(self):
        url = reverse("lists:detail", args=["luis", self.game_list.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_progress_of_whoever_looks(self):
        LibraryEntry.objects.create(
            user=self.luis, game=self.hades, status=LibraryEntry.Status.COMPLETED, steam_playtime_minutes=600,
        )
        LibraryEntry.objects.create(user=self.luis, game=self.celeste, status=LibraryEntry.Status.BACKLOG)
        self.client.force_login(self.luis)
        response = self.client.get(self.game_list.get_absolute_url())
        progress = response.context["progress"]
        self.assertEqual(
            (progress["played"], progress["percent"], progress["completed"], progress["backlog"], progress["hours"]),
            (1, 33, 1, 1, 10),
        )
        self.assertEqual(progress["missing"], 1)
        self.assertContains(response, "Has jugado 1 de 3")
        self.assertContains(response, "Te falta 1")
        # El estado de cada juego en la biblioteca de quien mira.
        self.assertContains(response, '<span class="badge">Completado</span>', html=True)

    def test_what_is_missing_and_what_it_costs(self):
        add_price(self.celeste, "4.99")
        add_price(self.tunic, "14.99", low="9.99")
        LibraryEntry.objects.create(user=self.luis, game=self.hades, status=LibraryEntry.Status.PLAYED)
        WishlistItem.objects.create(user=self.luis, game=self.celeste)
        self.client.force_login(self.luis)
        response = self.client.get(self.game_list.get_absolute_url())
        progress = response.context["progress"]
        self.assertEqual((progress["missing"], progress["priced"], progress["wished"], progress["to_wish"]), (2, 2, 1, 1))
        self.assertContains(response, "Te faltan 2")
        self.assertContains(response, "19,98 € hoy en tus tiendas o 14,98 € si esperas a sus mínimos históricos")
        self.assertContains(response, "Añadir el que te falta a «Lo quiero»")

    def test_everything_played(self):
        for game in (self.hades, self.celeste, self.tunic):
            LibraryEntry.objects.create(user=self.luis, game=game, status=LibraryEntry.Status.PLAYED)
        self.client.force_login(self.luis)
        response = self.client.get(self.game_list.get_absolute_url())
        self.assertContains(response, "¡Los has jugado todos!")
        self.assertNotContains(response, "Te falta")

    def test_opens_with_the_comments_when_there_are_any(self):
        url = self.game_list.get_absolute_url()
        self.assertEqual(self.client.get(url).context["view"], "portadas")
        ListItem.objects.filter(game=self.hades).update(note="El mejor *roguelike*.")
        response = self.client.get(url)
        self.assertEqual(response.context["view"], "detalle")
        self.assertContains(response, "El mejor <em>roguelike</em>.")
        self.assertContains(response, "list-rows is-ranked")
        self.assertEqual(self.client.get(f"{url}?vista=portadas").context["view"], "portadas")


class ListEditingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.hades, cls.celeste, cls.tunic = make_games("Hades", "Celeste", "Tunic")

    def setUp(self):
        self.game_list = GameList.objects.create(user=self.ana, title="Indies")
        self.client.force_login(self.ana)

    def order(self):
        return list(self.game_list.items.values_list("game__name", flat=True))

    def post(self, name, game=None, htmx=False, **data):
        args = [self.game_list.pk] + ([game.pk] if game else [])
        return self.client.post(reverse(f"lists:{name}", args=args), data, **(HTMX if htmx else {}))

    def test_create_a_list_that_starts_with_a_game(self):
        response = self.client.post(reverse("lists:new"), {
            "title": "  Para   jugar en pareja ", "description": "", "is_public": "on", "juego": self.hades.pk,
        })
        game_list = GameList.objects.get(title="Para jugar en pareja")
        self.assertRedirects(response, f"{reverse('lists:edit', args=[game_list.pk])}#juegos", fetch_redirect_response=False)
        self.assertEqual(game_list.user, self.ana)
        self.assertEqual(list(game_list.items.values_list("game", flat=True)), [self.hades.pk])
        self.assertTrue(game_list.is_public)
        self.assertFalse(game_list.is_ranked)

    def test_new_list_form_names_the_game(self):
        response = self.client.get(f"{reverse('lists:new')}?juego={self.hades.pk}")
        self.assertContains(response, "Empezará con <strong>Hades</strong>")

    def test_a_title_is_needed(self):
        response = self.client.post(reverse("lists:new"), {"title": "   ", "description": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ponle un título a la lista.")
        self.assertEqual(GameList.objects.count(), 1)

    def test_new_needs_a_session(self):
        self.client.logout()
        response = self.client.get(reverse("lists:new"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('lists:new')}", fetch_redirect_response=False)

    def test_edit_saves_the_details(self):
        response = self.client.post(reverse("lists:edit", args=[self.game_list.pk]), {
            "title": "Indies de culto", "description": "Pequeños y **enormes**.", "is_ranked": "on",
        })
        self.assertRedirects(response, self.game_list.get_absolute_url(), fetch_redirect_response=False)
        self.game_list.refresh_from_db()
        self.assertEqual(self.game_list.title, "Indies de culto")
        self.assertFalse(self.game_list.is_public)
        self.assertTrue(self.game_list.is_ranked)

    def test_an_invalid_edit_keeps_what_was_saved(self):
        response = self.client.post(reverse("lists:edit", args=[self.game_list.pk]), {"title": "", "description": ""})
        self.assertContains(response, "Editar «Indies»")
        self.game_list.refresh_from_db()
        self.assertEqual(self.game_list.title, "Indies")

    def test_others_cannot_touch_the_list(self):
        self.game_list.add(self.hades)
        self.client.force_login(self.luis)
        self.assertEqual(self.client.get(reverse("lists:edit", args=[self.game_list.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("lists:search", args=[self.game_list.pk]), {"q": "a"}).status_code, 404)
        for name, game, data in (
            ("edit", None, {"title": "Mía"}), ("delete", None, {}), ("add", self.celeste, {}), ("remove", self.hades, {}),
            ("move", self.hades, {"a": "abajo"}), ("note", self.hades, {"note": "Mía"}),
        ):
            self.assertEqual(self.post(name, game, **data).status_code, 404, name)
        self.game_list.refresh_from_db()
        self.assertEqual((self.game_list.title, self.order()), ("Indies", ["Hades"]))
        self.assertEqual(self.game_list.items.get().note, "")

    def test_add_and_remove_from_the_game_page(self):
        response = self.post("add", self.hades, htmx=True, desde="ficha")
        self.assertTemplateUsed(response, "lists/_picker.html")
        self.assertContains(response, "list-picker-item is-on")
        self.assertContains(response, 'id="list-picker" open')
        self.assertEqual(self.order(), ["Hades"])
        response = self.post("remove", self.hades, htmx=True, desde="ficha")
        self.assertNotContains(response, "is-on")
        self.assertEqual(self.order(), [])

    def test_add_from_the_search_marks_the_result(self):
        response = self.post("add", self.hades, htmx=True, desde="buscador")
        self.assertTemplateUsed(response, "lists/_editor_items.html")
        self.assertContains(response, f'id="resultado-{self.hades.pk}" hx-swap-oob="true"')
        self.assertContains(response, "En la lista")
        self.assertEqual(self.order(), ["Hades"])

    def test_without_htmx_goes_back_only_within_the_site(self):
        response = self.post("add", self.hades, next=self.hades.get_absolute_url())
        self.assertRedirects(response, self.hades.get_absolute_url(), fetch_redirect_response=False)
        response = self.post("add", self.celeste, next="https://example.com/")
        self.assertRedirects(response, f"{reverse('lists:edit', args=[self.game_list.pk])}#juegos", fetch_redirect_response=False)

    def test_a_full_list_takes_no_more(self):
        games = Game.objects.bulk_create([Game(name=f"Juego {n}", slug=f"juego-{n}") for n in range(LIST_MAX_GAMES)])
        ListItem.objects.bulk_create([ListItem(game_list=self.game_list, game=game, position=n) for n, game in enumerate(games)])
        response = self.post("add", self.hades, htmx=True)
        self.assertContains(response, f"Caben {LIST_MAX_GAMES} juegos por lista")
        self.assertEqual(self.game_list.items.count(), LIST_MAX_GAMES)
        # Uno que ya estaba no cuenta como nuevo.
        response = self.post("add", games[0], htmx=True)
        self.assertNotContains(response, "Caben")

    def test_move_up_down_and_to_a_position(self):
        for game in (self.hades, self.celeste, self.tunic):
            self.game_list.add(game)
        self.post("move", self.tunic, htmx=True, a="arriba")
        self.assertEqual(self.order(), ["Hades", "Tunic", "Celeste"])
        self.post("move", self.hades, htmx=True, a="abajo")
        self.assertEqual(self.order(), ["Tunic", "Hades", "Celeste"])
        response = self.post("move", self.celeste, htmx=True, puesto="1")
        self.assertEqual(self.order(), ["Celeste", "Tunic", "Hades"])
        self.assertTemplateUsed(response, "lists/_editor_items.html")
        self.assertEqual(self.post("move", self.hades, puesto="primero").status_code, 400)
        other = make_games("Ori")[0]
        self.assertEqual(self.post("move", other, a="arriba").status_code, 404)

    def test_comment_is_saved_trimmed_and_can_be_cleared(self):
        self.game_list.add(self.hades)
        response = self.post("note", self.hades, htmx=True, note="  Un   *clásico*  ")
        self.assertTemplateUsed(response, "lists/_note_form.html")
        self.assertContains(response, "Guardado")
        self.assertEqual(self.game_list.items.get().note, "Un *clásico*")
        response = self.post("note", self.hades, htmx=True, note="x" * (NOTE_MAX_LENGTH + 1))
        self.assertContains(response, f"Caben {NOTE_MAX_LENGTH} caracteres.")
        self.assertEqual(self.game_list.items.get().note, "Un *clásico*")
        self.post("note", self.hades, note="")
        self.assertEqual(self.game_list.items.get().note, "")

    def test_delete_keeps_the_games(self):
        self.game_list.add(self.hades)
        response = self.post("delete")
        self.assertRedirects(response, f"{reverse('profile', args=['ana'])}?vista=listas", fetch_redirect_response=False)
        self.assertFalse(GameList.objects.exists())
        self.assertTrue(Game.objects.filter(pk=self.hades.pk).exists())

    def test_search_marks_what_is_already_in_the_list(self):
        self.game_list.add(self.hades)
        Game.objects.filter(pk=self.celeste.pk).update(name_es="Celeste (edición española)")
        response = self.client.get(reverse("lists:search", args=[self.game_list.pk]), {"q": "e"}, **HTMX)
        results = {game.name: game.in_list for game in response.context["results"]}
        self.assertEqual(results, {"Hades": True, "Celeste": False})
        self.assertContains(response, "En la lista")
        response = self.client.get(reverse("lists:search", args=[self.game_list.pk]), {"q": "española"}, **HTMX)
        self.assertEqual([game.name for game in response.context["results"]], ["Celeste"])

    def test_search_without_htmx_goes_to_the_editor(self):
        response = self.client.get(reverse("lists:search", args=[self.game_list.pk]), {"q": "hades"})
        self.assertRedirects(
            response, f"{reverse('lists:edit', args=[self.game_list.pk])}?q=hades#juegos", fetch_redirect_response=False,
        )
        response = self.client.get(reverse("lists:edit", args=[self.game_list.pk]), {"q": "hades"})
        self.assertContains(response, f'id="resultado-{self.hades.pk}"')
        # La búsqueda del editor no rellena el buscador de juegos de la cabecera.
        self.assertContains(response, 'value="" placeholder="Buscar juegos…"')


class WishMissingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.hades, cls.celeste, cls.tunic = make_games("Hades", "Celeste", "Tunic")
        cls.game_list = GameList.objects.create(user=cls.ana, title="Indies")
        for game in (cls.hades, cls.celeste, cls.tunic):
            cls.game_list.add(game)

    def test_wishes_what_you_neither_have_nor_want_yet(self):
        LibraryEntry.objects.create(user=self.luis, game=self.hades)
        WishlistItem.objects.create(user=self.luis, game=self.celeste, target_price=Decimal("5"))
        profile = self.luis.profile
        profile.steam_wishlist_skip = [self.tunic.pk]
        profile.save()
        self.client.force_login(self.luis)
        response = self.client.post(reverse("lists:wish_missing", args=[self.game_list.pk]), follow=True)
        self.assertRedirects(response, self.game_list.get_absolute_url())
        self.assertContains(response, "1 juego añadido a tu lista de deseos: te avisaremos cuando baje de precio.")
        wishes = dict(WishlistItem.objects.filter(user=self.luis).values_list("game__name", "target_price"))
        self.assertEqual(wishes, {"Celeste": Decimal("5.00"), "Tunic": None})
        profile.refresh_from_db()
        self.assertEqual(profile.steam_wishlist_skip, [])

    def test_private_lists_give_404(self):
        GameList.objects.filter(pk=self.game_list.pk).update(is_public=False)
        self.client.force_login(self.luis)
        self.assertEqual(self.client.post(reverse("lists:wish_missing", args=[self.game_list.pk])).status_code, 404)
        self.assertFalse(WishlistItem.objects.exists())


class ListsAroundTheSiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.hades, cls.celeste = make_games("Hades", "Celeste")
        cls.public = GameList.objects.create(user=cls.ana, title="Roguelikes")
        cls.private = GameList.objects.create(user=cls.ana, title="Lista secreta", is_public=False)
        for game_list in (cls.public, cls.private):
            game_list.add(cls.hades)

    def test_profile_tab_shows_what_the_viewer_can_see(self):
        url = f"{reverse('profile', args=['ana'])}?vista=listas"
        self.client.force_login(self.luis)
        response = self.client.get(url)
        self.assertContains(response, "Roguelikes")
        self.assertNotContains(response, "Lista secreta")
        self.assertEqual(len(response.context["lists"]), 1)
        self.assertNotContains(response, "Nueva lista")
        self.client.force_login(self.ana)
        response = self.client.get(url)
        self.assertContains(response, "Lista secreta")
        self.assertContains(response, "Nueva lista")

    def test_cards_say_how_much_the_viewer_has_played(self):
        LibraryEntry.objects.create(user=self.luis, game=self.hades, status=LibraryEntry.Status.PLAYING)
        self.client.force_login(self.luis)
        response = self.client.get(f"{reverse('profile', args=['ana'])}?vista=listas")
        self.assertContains(response, "Has jugado 1 de 1")

    def test_showcase_shows_the_latest_lists(self):
        response = self.client.get(reverse("profile", args=["ana"]))
        self.assertEqual([game_list.title for game_list in response.context["showcase_lists"]], ["Roguelikes"])
        self.assertContains(response, "Roguelikes")

    def test_game_page_offers_your_lists_and_shows_the_ones_it_is_in(self):
        mine = GameList.objects.create(user=self.ana, title="Vacía")
        self.client.force_login(self.ana)
        response = self.client.get(self.hades.get_absolute_url())
        picker = {game_list.title: game_list.has_game for game_list in response.context["my_lists"]}
        self.assertEqual(picker, {mine.title: False, "Lista secreta": True, "Roguelikes": True})
        self.assertEqual(response.context["in_lists"], 2)
        self.assertEqual(response.context["game_lists_count"], 2)
        self.client.force_login(self.luis)
        response = self.client.get(self.hades.get_absolute_url())
        self.assertEqual([game_list.title for game_list in response.context["game_lists"]], ["Roguelikes"])
        self.assertContains(response, "En 1 lista")
        self.assertNotContains(response, "Lista secreta")
        self.client.logout()
        response = self.client.get(self.hades.get_absolute_url())
        self.assertNotIn("my_lists", response.context)
        self.assertContains(response, "Roguelikes")

    def test_page_with_all_the_lists_of_a_game(self):
        response = self.client.get(reverse("lists:game", args=[self.hades.slug]))
        self.assertContains(response, "Listas con Hades")
        self.assertContains(response, "De ana")
        self.assertNotContains(response, "Lista secreta")
        response = self.client.get(reverse("lists:game", args=[self.celeste.slug]))
        self.assertContains(response, "aún no está en ninguna lista")


class ListLikesCommentsAndReportsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.admin = User.objects.create_user("admin", "admin@example.com", "x", is_staff=True)
        cls.hades = make_games("Hades")[0]
        cls.game_list = GameList.objects.create(user=cls.ana, title="Roguelikes")
        cls.game_list.add(cls.hades)

    def setUp(self):
        self.client.force_login(self.luis)

    def url(self, name, pk=None):
        return reverse(f"lists:{name}", args=[pk or self.game_list.pk])

    def test_like_toggles_and_tells_the_owner_once(self):
        response = self.client.post(self.url("like"), **HTMX)
        self.assertTemplateUsed(response, "lists/_like.html")
        self.assertContains(response, "like-btn is-on")
        notice = Notification.objects.get(user=self.ana)
        self.assertEqual(
            (notice.kind, notice.actor, notice.text),
            (Notification.Kind.LIST_LIKE, self.luis, "A luis le ha gustado tu lista «Roguelikes»."),
        )
        self.client.post(self.url("like"))
        self.assertFalse(ListLike.objects.exists())
        self.client.post(self.url("like"))
        self.assertTrue(ListLike.objects.filter(user=self.luis, game_list=self.game_list).exists())
        self.assertEqual(Notification.objects.filter(user=self.ana).count(), 1)

    def test_own_and_hidden_lists_cannot_be_liked(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.post(self.url("like")).status_code, 400)
        GameList.objects.filter(pk=self.game_list.pk).update(is_public=False)
        self.client.force_login(self.luis)
        self.assertEqual(self.client.post(self.url("like")).status_code, 404)
        self.assertFalse(ListLike.objects.exists())

    def test_comment_tells_the_owner_and_updates_the_counter(self):
        response = self.client.post(self.url("comment"), {"text": "  Muy buena  "}, **HTMX)
        self.assertTemplateUsed(response, "library/_comments.html")
        self.assertContains(response, f'<span id="list-comment-count-{self.game_list.pk}" hx-swap-oob="true">1</span>')
        comment = ListComment.objects.get()
        self.assertEqual((comment.user, comment.text), (self.luis, "Muy buena"))
        notice = Notification.objects.get(user=self.ana)
        self.assertEqual((notice.kind, notice.url), (Notification.Kind.LIST_COMMENT, f"{self.game_list.get_absolute_url()}#comentarios"))
        # Otro comentario no repite el aviso mientras no se lea el primero, y el dueño no se avisa a sí mismo.
        self.client.post(self.url("comment"), {"text": "Y otra cosa"})
        self.client.force_login(self.ana)
        self.client.post(self.url("comment"), {"text": "¡Gracias!"})
        self.assertEqual(Notification.objects.filter(user=self.ana).count(), 1)
        self.assertEqual(ListComment.objects.count(), 3)

    def test_an_empty_comment_shows_the_error(self):
        response = self.client.post(self.url("comment"), {"text": "   "}, **HTMX)
        self.assertContains(response, "Escribe algo en el comentario.")
        self.assertFalse(ListComment.objects.exists())

    def test_who_can_delete_a_comment(self):
        mine = ListComment.objects.create(game_list=self.game_list, user=self.luis, text="Mío")
        eva = User.objects.create_user("eva", "eva@example.com", "x")
        hers = ListComment.objects.create(game_list=self.game_list, user=eva, text="De Eva")
        self.assertEqual(self.client.post(self.url("comment_delete", hers.pk)).status_code, 403)
        self.client.post(self.url("comment_delete", mine.pk))
        self.assertFalse(ListComment.objects.filter(pk=mine.pk).exists())
        # El dueño de la lista puede borrar los de los demás.
        self.client.force_login(self.ana)
        response = self.client.post(self.url("comment_delete", hers.pk), **HTMX)
        self.assertTemplateUsed(response, "library/_comments.html")
        self.assertFalse(ListComment.objects.exists())

    def test_report_a_list_and_a_comment(self):
        response = self.client.post(self.url("report"), {"reason": "spam"}, **HTMX)
        self.assertContains(response, "Gracias: lo revisaremos")
        self.assertEqual(Report.objects.get().target, self.game_list)
        notice = Notification.objects.get(user=self.admin)
        self.assertEqual(notice.text, "Han denunciado una lista de ana: Spam o publicidad.")
        comment = ListComment.objects.create(game_list=self.game_list, user=self.ana, text="Compra aquí")
        self.client.post(self.url("comment_report", comment.pk), {"reason": "spam"})
        self.assertEqual(Report.objects.count(), 2)
        # Lo propio no se puede denunciar.
        self.client.force_login(self.ana)
        self.assertEqual(self.client.post(self.url("report"), {"reason": "spam"}).status_code, 400)
        self.assertEqual(self.client.post(self.url("comment_report", comment.pk), {"reason": "spam"}).status_code, 400)

    def test_admin_shows_reported_lists_and_comments(self):
        comment = ListComment.objects.create(game_list=self.game_list, user=self.ana, text="Compra aquí")
        self.client.post(self.url("report"), {"reason": "spam"})
        self.client.post(self.url("comment_report", comment.pk), {"reason": "spam"})
        self.client.force_login(User.objects.create_superuser("jefa", "jefa@example.com", "x"))
        response = self.client.get(reverse("admin:library_report_changelist"))
        self.assertContains(response, reverse("admin:lists_gamelist_change", args=[self.game_list.pk]))
        self.assertContains(response, reverse("admin:lists_listcomment_change", args=[comment.pk]))
        self.assertContains(self.client.get(reverse("admin:lists_gamelist_changelist")), "Roguelikes")
        response = self.client.get(reverse("admin:lists_gamelist_change", args=[self.game_list.pk]))
        self.assertContains(response, "Compra aquí")

    def test_reports_and_comments_go_with_the_list(self):
        ListComment.objects.create(game_list=self.game_list, user=self.luis, text="Hola")
        self.client.post(self.url("report"), {"reason": "spam"})
        self.game_list.delete()
        self.assertFalse(Report.objects.exists())
        self.assertFalse(ListComment.objects.exists())

    def test_list_page_shows_likes_comments_and_report(self):
        ListLike.objects.create(user=self.luis, game_list=self.game_list)
        ListComment.objects.create(game_list=self.game_list, user=self.luis, text="Me encanta *Hades*")
        response = self.client.get(self.game_list.get_absolute_url())
        self.assertContains(response, "like-btn is-on")
        self.assertContains(response, "Me encanta <em>Hades</em>")
        self.assertContains(response, f'id="denunciar-lista-{self.game_list.pk}"')
        # Su dueño ve el contador, pero no puede darse «me gusta» ni denunciarla.
        self.client.force_login(self.ana)
        response = self.client.get(self.game_list.get_absolute_url())
        self.assertNotContains(response, "like-btn")
        self.assertNotContains(response, "denunciar-lista")
        response = self.client.get(f"{reverse('profile', args=['ana'])}?vista=listas")
        self.assertContains(response, 'class="list-card-likes"')


class ListActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        Follow.objects.create(follower=cls.luis, followed=cls.ana)
        cls.hades, cls.celeste = make_games("Hades", "Celeste")

    def setUp(self):
        self.client.force_login(self.ana)

    def create(self, game=None, public=True):
        """Crea como ana la lista «Roguelikes» desde el formulario (con game, desde la ficha de ese juego)."""
        data = {"title": "Roguelikes", "description": ""}
        if public:
            data["is_public"] = "on"
        if game:
            data["juego"] = game.pk
        self.client.post(reverse("lists:new"), data)
        return GameList.objects.get(title="Roguelikes")

    def test_creating_and_filling_a_list_right_away_is_one_activity(self):
        game_list = self.create(game=self.hades)
        self.client.post(reverse("lists:add", args=[game_list.pk, self.celeste.pk]))
        activity = Activity.objects.get()
        self.assertEqual(
            (activity.kind, activity.game_list, activity.list_created, activity.list_added),
            (Activity.Kind.LIST, game_list, True, 2),
        )
        self.client.post(reverse("lists:remove", args=[game_list.pk, self.celeste.pk]))
        activity.refresh_from_db()
        self.assertEqual(activity.list_added, 1)

    def test_adding_later_is_a_new_activity_that_can_be_undone(self):
        game_list = self.create()
        Activity.objects.update(updated_at=timezone.now() - timedelta(hours=2))
        self.client.post(reverse("lists:add", args=[game_list.pk, self.hades.pk]))
        latest = Activity.objects.first()
        self.assertEqual((Activity.objects.count(), latest.list_created, latest.list_added), (2, False, 1))
        self.client.post(reverse("lists:remove", args=[game_list.pk, self.hades.pk]))
        self.assertEqual(list(Activity.objects.values_list("data", flat=True)), [{"created": True}])

    def test_followers_see_it_once_it_has_games(self):
        game_list = self.create()
        self.client.force_login(self.luis)
        self.assertNotContains(self.client.get(reverse("home")), "Roguelikes")
        game_list.add(self.hades)
        LibraryEntry.objects.create(user=self.luis, game=self.hades, status=LibraryEntry.Status.PLAYING)
        ListLike.objects.create(user=self.luis, game_list=game_list)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "creó")
        self.assertContains(response, "Roguelikes")
        self.assertContains(response, "has jugado 1")
        self.assertContains(response, "like-btn is-on")
        self.assertContains(response, f'href="{game_list.get_absolute_url()}#comentarios"')

    def test_private_lists_are_only_in_their_owners_activity(self):
        self.create(game=self.hades, public=False)
        self.client.force_login(self.luis)
        self.assertNotContains(self.client.get(reverse("home")), "Roguelikes")
        self.assertNotContains(self.client.get(f"{reverse('profile', args=['ana'])}?vista=actividad"), "Roguelikes")
        self.client.force_login(self.ana)
        response = self.client.get(f"{reverse('profile', args=['ana'])}?vista=actividad")
        self.assertContains(response, "Creó")
        self.assertContains(response, "Roguelikes")

    def test_deleting_the_list_deletes_its_activity(self):
        game_list = self.create(game=self.hades)
        self.client.post(reverse("lists:delete", args=[game_list.pk]))
        self.assertFalse(Activity.objects.exists())
