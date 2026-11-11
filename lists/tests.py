from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Achievement, ExternalTitle, ImportedTitle, Notification, UnlockedAchievement
from games.models import Game, GamePrice
from library.models import LibraryEntry, Report, Review, WishlistItem
from social.models import Activity, Follow

from . import presets
from .community import community_board, middle_tier
from .models import (
    DEFAULT_TIERS, LIST_MAX_GAMES, MAX_COLLABORATORS, NOTE_MAX_LENGTH, GameList, ListCollaborator, ListComment, ListItem,
    ListLike,
)
from .views.tarjetas import BOARD_COVERS, mark_boards

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
            user=cls.ana, title="Indies favoritos", description="Los **mejores**.", mode=GameList.Mode.RANKED,
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
        self.assertContains(response, '<span class="badge st-completed">Completado</span>', html=True)

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
            "title": "Indies de culto", "description": "Pequeños y **enormes**.", "mode": "ranked",
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


def tier_form(**changes):
    """Datos del formulario de una tier list «Roguelikes» con los niveles de serie (S, A, B, C, D y F) y changes encima."""
    data = {"title": "Roguelikes", "description": "", "is_public": "on", "mode": "tiers"}
    for n, (label, color) in enumerate([("S", "#ff4d5e"), ("A", "#ff8a3d"), ("B", "#ffc84a"), ("C", "#3fbf3f"), ("D", "#4f8bff"), ("F", "#9d5cff")]):
        data[f"nivel_{n}"], data[f"color_{n}"] = label, color
    return {**data, **changes}


class TierListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.hades, cls.celeste, cls.tunic = make_games("Hades", "Celeste", "Tunic")

    def setUp(self):
        self.client.force_login(self.ana)

    def create(self, **changes):
        """Crea como ana la tier list «Roguelikes» desde el formulario de «Nueva tier list», con sus tres juegos."""
        self.client.post(reverse("lists:new_tier_list"), tier_form(**changes))
        game_list = GameList.objects.get(title="Roguelikes")
        for game in (self.hades, self.celeste, self.tunic):
            game_list.add(game, self.ana)
        return game_list

    def tier(self, game_list, game, level, htmx=True):
        return self.client.post(
            reverse("lists:tier", args=[game_list.pk, game.pk]), {"nivel": level}, **(HTMX if htmx else {}),
        )

    def test_a_new_tier_list_has_the_usual_tiers(self):
        game_list = self.create()
        self.assertTrue(game_list.is_tiered)
        self.assertEqual([tier["label"] for tier in game_list.tiers], ["S", "A", "B", "C", "D", "F"])
        self.assertEqual(game_list.kind_note, "tier list")

    def test_tiers_can_be_renamed_recoloured_and_removed(self):
        game_list = self.create()
        self.tier(game_list, self.hades, 0)
        self.tier(game_list, self.celeste, 1)
        response = self.client.post(reverse("lists:edit", args=[game_list.pk]), tier_form(
            nivel_0="  Obras   maestras ", color_0="#E6E8F5", nivel_1="", nivel_6="Ni fu ni fa", color_6="#22e0f0",
        ))
        self.assertRedirects(response, game_list.get_absolute_url(), fetch_redirect_response=False)
        game_list.refresh_from_db()
        self.assertEqual(game_list.tiers[0], {"id": 0, "label": "Obras maestras", "color": "#e6e8f5"})
        self.assertEqual([tier["id"] for tier in game_list.tiers], [0, 2, 3, 4, 5, 6])
        # Celeste estaba en el nivel que se ha quitado: queda sin clasificar.
        rows = game_list.tier_rows(list(game_list.items.all()))
        self.assertEqual([item.game.name for item in rows[0][1]], ["Hades"])
        self.assertEqual((rows[-1][0], [item.game.name for item in rows[-1][1]]), (None, ["Celeste", "Tunic"]))
        # La etiqueta clara lleva texto oscuro; la violeta (F), blanco.
        tiers = {tier["id"]: tier for tier in game_list.tier_list}
        self.assertEqual((tiers[0]["text"], tiers[5]["text"]), ("#0b0d18", "#ffffff"))

    def test_a_tier_list_needs_a_tier_and_valid_colours(self):
        empty = {f"nivel_{n}": "" for n in range(6)}
        response = self.client.post(reverse("lists:new_tier_list"), tier_form(**empty))
        self.assertContains(response, "Ponle nombre al menos a un nivel.")
        response = self.client.post(reverse("lists:new_tier_list"), tier_form(color_0="rojo"))
        self.assertContains(response, "Elige un color.")
        self.assertFalse(GameList.objects.exists())

    def test_put_a_game_in_a_tier_and_take_it_out(self):
        game_list = self.create()
        response = self.tier(game_list, self.hades, 0)
        self.assertTemplateUsed(response, "lists/_editor_items.html")
        self.assertContains(response, f'id="nivel-{self.hades.pk}-0"')
        self.assertEqual(game_list.items.get(game=self.hades).tier, 0)
        self.assertContains(response, 'class="tier-chip is-on"')
        self.tier(game_list, self.hades, "")
        self.assertIsNone(game_list.items.get(game=self.hades).tier)
        self.assertEqual(self.tier(game_list, self.hades, 9).status_code, 400)
        self.assertEqual(self.tier(game_list, make_games("Ori")[0], 0).status_code, 404)
        self.client.force_login(self.luis)
        self.assertEqual(self.tier(game_list, self.hades, 0).status_code, 404)

    def test_the_page_shows_the_board(self):
        game_list = self.create()
        self.tier(game_list, self.hades, 0)
        self.tier(game_list, self.celeste, 2)
        response = self.client.get(game_list.get_absolute_url())
        self.assertEqual(response.context["view"], "niveles")
        board = [(level["label"] if level else None, [item.game.name for item in items]) for level, items in response.context["tier_rows"]]
        self.assertEqual(board, [("S", ["Hades"]), ("A", []), ("B", ["Celeste"]), ("C", []), ("D", []), ("F", []), (None, ["Tunic"])])
        self.assertContains(response, "Sin clasificar")
        self.assertContains(response, "Tier list")
        response = self.client.get(f"{game_list.get_absolute_url()}?vista=detalle")
        self.assertContains(response, 'class="tier-chip is-on" style="--tier: #ff4d5e; --tier-text: #ffffff;">S</span>', html=False)

    def test_numbered_lists_keep_their_mode(self):
        self.client.post(reverse("lists:new"), {"title": "Ranking", "description": "", "mode": "ranked"})
        game_list = GameList.objects.get(title="Ranking")
        for game in (self.hades, self.celeste, self.tunic):
            game_list.add(game, self.ana)
        self.assertTrue(game_list.is_ranked)
        response = self.client.get(game_list.get_absolute_url())
        self.assertEqual(response.context["view"], "portadas")
        self.assertContains(response, "list-games is-ranked")

    def place(self, game_list, game, level, row, where="tablero"):
        """Suelta game en el nivel level del tablero con su fila como row (juegos en orden), como lo manda lists/_drag.html."""
        return self.client.post(
            reverse("lists:tier", args=[game_list.pk, game.pk]),
            {"nivel": level, "orden": ",".join(str(other.pk) for other in row), "desde": where}, **HTMX,
        )

    def board(self, game_list):
        """El tablero guardado: [(nombre del nivel o None, [juegos])], con «Sin clasificar» al final."""
        rows = game_list.tier_rows(list(game_list.items.select_related("game")), keep_loose=True)
        return [(level["label"] if level else None, [item.game.name for item in items]) for level, items in rows if items]

    def test_drag_a_cover_to_its_place(self):
        game_list = self.create()
        self.tier(game_list, self.hades, 0)
        self.tier(game_list, self.celeste, 0)
        # Tunic, sin clasificar, entre Hades y Celeste: desde la página de la lista vuelve el tablero.
        response = self.place(game_list, self.tunic, 0, [self.hades, self.tunic, self.celeste])
        self.assertTemplateUsed(response, "lists/_tier_board.html")
        self.assertContains(response, 'data-desde="tablero"')
        self.assertEqual(self.board(game_list), [("S", ["Hades", "Tunic", "Celeste"])])
        # Hades al final de su fila y Celeste fuera de su nivel.
        self.place(game_list, self.hades, 0, [self.tunic, self.celeste, self.hades])
        self.place(game_list, self.celeste, "", [self.celeste])
        self.assertEqual(self.board(game_list), [("S", ["Tunic", "Hades"]), (None, ["Celeste"])])
        # Celeste a la cabeza de A, con ids que no son de esa fila (un juego de otra lista y uno de S): no cuentan.
        ori = make_games("Ori")[0]
        response = self.place(game_list, self.celeste, 1, [self.celeste, ori, self.hades], where="editor")
        self.assertTemplateUsed(response, "lists/_editor_items.html")
        self.assertEqual(self.board(game_list), [("S", ["Tunic", "Hades"]), ("A", ["Celeste"])])
        self.assertEqual(self.place(game_list, self.hades, 9, [self.hades]).status_code, 400)

    def test_placing_a_game_does_not_reorder_the_rest(self):
        game_list = self.create()
        dead_cells, ori = make_games("Dead Cells", "Ori")
        for game in (dead_cells, ori):
            game_list.add(game, self.ana)
        for game in (self.hades, dead_cells):
            self.tier(game_list, game, 0)
        # Orden de la lista: Hades (S), Celeste, Tunic, Dead Cells (S), Ori. Ori entre Hades y Dead Cells en S queda
        # justo detrás de Hades; los demás siguen en su orden.
        self.place(game_list, ori, 0, [self.hades, ori, dead_cells])
        order = ["Hades", "Ori", "Celeste", "Tunic", "Dead Cells"]
        self.assertEqual([item.game.name for item in game_list.items.all()], order)
        self.assertEqual(self.board(game_list)[0], ("S", ["Hades", "Ori", "Dead Cells"]))
        # Solo en una fila vacía, conserva su puesto.
        self.place(game_list, self.celeste, 2, [self.celeste])
        self.assertEqual([item.game.name for item in game_list.items.all()], order)
        self.assertEqual(game_list.items.get(game=self.celeste).tier, 2)

    def test_only_who_can_edit_gets_a_board_to_drag(self):
        game_list = self.create()
        self.tier(game_list, self.hades, 0)
        for game in (self.celeste, self.tunic):
            self.tier(game_list, game, 1)
        response = self.client.get(game_list.get_absolute_url())
        self.assertContains(response, 'data-tier-board data-desde="tablero"')
        self.assertContains(response, "sortablejs@")
        # Sin juegos sueltos, la fila «Sin clasificar» sigue ahí para poder sacarlos de su nivel.
        self.assertEqual(response.context["tier_rows"][-1], (None, []))
        self.client.force_login(self.luis)
        response = self.client.get(game_list.get_absolute_url())
        self.assertNotContains(response, "data-tier-board")
        self.assertNotContains(response, "sortablejs@")
        self.assertNotContains(response, "Sin clasificar")
        self.assertEqual(self.place(game_list, self.hades, 1, [self.hades]).status_code, 404)

    def test_the_editor_drags_covers_or_rows(self):
        game_list = self.create()
        # Los atributos tal como salen en el HTML (el script de lists/_drag.html también nombra sus selectores).
        response = self.client.get(reverse("lists:edit", args=[game_list.pk]))
        self.assertContains(response, 'data-tier-board data-desde="editor"')
        self.assertNotContains(response, "data-sortable-rows>")
        ranked = GameList.objects.create(user=self.ana, title="Ranking", mode=GameList.Mode.RANKED)
        for game in (self.hades, self.celeste, self.tunic):
            ranked.add(game, self.ana)
        response = self.client.get(reverse("lists:edit", args=[ranked.pk]))
        self.assertNotContains(response, "data-tier-board data-desde")
        self.assertContains(response, "data-sortable-rows>")
        self.assertContains(response, 'class="drag-handle"', count=3)

    def test_tier_lists_have_their_own_page_to_start_them(self):
        # «Nueva lista» ya no ofrece el tipo «Por niveles», sino un enlace a su página.
        response = self.client.get(reverse("lists:new"))
        self.assertNotContains(response, 'value="tiers"')
        self.assertContains(response, f'href="{reverse("lists:new_tier_list")}"')
        response = self.client.post(reverse("lists:new"), tier_form())
        self.assertIn("mode", response.context["form"].errors)
        self.assertFalse(GameList.objects.exists())
        # En la suya el tipo va fijo y los niveles se ven siempre. Sin juegos en la biblioteca no hay plantillas: dice
        # cómo tenerlas.
        url = reverse("lists:new_tier_list")
        response = self.client.get(url)
        self.assertContains(response, '<input type="hidden" name="mode" value="tiers"', html=False)
        self.assertContains(response, "Niveles, de mejor a peor")
        self.assertContains(response, "Cuando tengas juegos en tu biblioteca")
        # Aunque se mande otro tipo, sale una tier list, y lleva a su editor para añadir los juegos.
        response = self.client.post(url, tier_form(mode="list"))
        game_list = GameList.objects.get()
        self.assertRedirects(response, f"{reverse('lists:edit', args=[game_list.pk])}#juegos", fetch_redirect_response=False)
        self.assertTrue(game_list.is_tiered)
        self.assertContains(self.client.get(response.url), "Tier list creada.")
        self.client.logout()
        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}", fetch_redirect_response=False)


class TierListSectionTests(TestCase):
    """Las tier lists, aparte de las demás listas: su pestaña del perfil, su sección del escaparate, su tablero en
    miniatura (lists.views.mark_boards) y cómo se las llama."""

    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.games = make_games(*[f"Juego {n}" for n in range(10)])
        cls.plain = GameList.objects.create(user=cls.ana, title="Roguelikes")
        cls.plain.add(cls.games[0])
        cls.tiers = GameList.objects.create(user=cls.ana, title="Mis niveles", mode=GameList.Mode.TIERS)
        for game in cls.games:
            cls.tiers.add(game)
        # Ocho juegos en S, uno en B y uno sin clasificar.
        cls.tiers.items.update(tier=0)
        cls.tiers.items.filter(game=cls.games[8]).update(tier=2)
        cls.tiers.items.filter(game=cls.games[9]).update(tier=None)

    def profile(self, view=""):
        return self.client.get(reverse("profile", args=["ana"]), {"vista": view} if view else {})

    def test_each_kind_has_its_own_tab(self):
        response = self.profile("listas")
        self.assertEqual([game_list.title for game_list in response.context["lists"]], ["Roguelikes"])
        self.assertNotContains(response, "Mis niveles")
        self.assertContains(response, 'href="?vista=listas">Listas <span>1</span>')
        self.assertContains(response, 'href="?vista=tier-lists">Tier lists <span>1</span>')
        response = self.profile("tier-lists")
        self.assertEqual([game_list.title for game_list in response.context["tier_lists"]], ["Mis niveles"])
        self.assertNotContains(response, "Roguelikes")
        self.assertNotContains(response, "Nueva tier list")
        # Su dueño la crea desde ahí y la encuentra en su menú.
        self.client.force_login(self.ana)
        response = self.profile("tier-lists")
        self.assertContains(response, f'href="{reverse("lists:new_tier_list")}"')
        self.assertContains(response, f'href="{reverse("profile", args=["ana"])}?vista=tier-lists"')

    def test_the_card_shows_a_small_board(self):
        game_list = self.profile("tier-lists").context["tier_lists"][0]
        board = [(level["label"], len(items), more) for level, items, more in game_list.board]
        self.assertEqual(
            board, [("S", BOARD_COVERS, 8 - BOARD_COVERS), ("A", 0, 0), ("B", 1, 0), ("C", 0, 0), ("D", 0, 0), ("F", 0, 0)],
        )
        self.assertEqual(game_list.loose_count, 1)
        response = self.profile("tier-lists")
        self.assertContains(response, f'<span class="tier-mini-more">+{8 - BOARD_COVERS}</span>', html=False)
        self.assertContains(response, "1 sin clasificar")
        # Los juegos de un nivel que ya no existe también cuentan como sin clasificar.
        self.tiers.tiers = [tier for tier in self.tiers.tiers if tier["id"] != 2]
        self.tiers.save()
        game_list = mark_boards(list(GameList.objects.filter(pk=self.tiers.pk)))[0]
        self.assertEqual(
            ([level["label"] for level, _, _ in game_list.board], game_list.loose_count), (["S", "A", "C", "D", "F"], 2),
        )

    def test_the_showcase_has_a_section_for_them(self):
        response = self.profile()
        self.assertEqual([game_list.title for game_list in response.context["showcase_lists"]], ["Roguelikes"])
        self.assertEqual([game_list.title for game_list in response.context["showcase_tier_lists"]], ["Mis niveles"])
        self.assertContains(response, 'class="tier-mini"')
        # Sin ninguna, a los demás no les sale; a su dueño, sí, con cómo empezar una.
        self.tiers.delete()
        self.assertNotContains(self.profile(), " Tier lists</h2>")
        self.client.force_login(self.ana)
        response = self.profile()
        self.assertContains(response, " Tier lists</h2>")
        self.assertContains(response, "crea una tier list")

    def test_it_is_called_a_tier_list(self):
        luis = User.objects.create_user("luis", "luis@example.com", "x")
        self.client.force_login(luis)
        self.client.post(reverse("lists:like", args=[self.tiers.pk]))
        self.assertEqual(Notification.objects.get().text, "A luis le ha gustado tu tier list «Mis niveles».")
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(self.tiers.get_absolute_url()), "Editar tier list")
        # Al borrarla vuelve a sus tier lists.
        response = self.client.post(reverse("lists:delete", args=[self.tiers.pk]))
        self.assertRedirects(response, f"{reverse('profile', args=['ana'])}?vista=tier-lists", fetch_redirect_response=False)
        self.assertContains(self.client.get(response.url), "Tier list «Mis niveles» borrada.")


class CollaborationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.eva = User.objects.create_user("eva", "eva@example.com", "x")
        cls.hades, cls.celeste = make_games("Hades", "Celeste")
        # Solo se puede invitar a quien sigue al dueño de la lista.
        for follower in (cls.luis, cls.eva):
            Follow.objects.create(follower=follower, followed=cls.ana)

    def setUp(self):
        self.game_list = GameList.objects.create(user=self.ana, title="Para jugar juntos", is_public=False)
        self.game_list.add(self.hades, self.ana)
        self.client.force_login(self.ana)

    def invite(self, username, htmx=True):
        return self.client.post(
            reverse("lists:invite", args=[self.game_list.pk]), {"usuario": username}, **(HTMX if htmx else {}),
        )

    def join(self, user):
        """Invita como ana a user y acepta él; deja la sesión con user."""
        self.client.force_login(self.ana)
        self.invite(user.username)
        self.client.force_login(user)
        self.client.post(reverse("lists:accept", args=[self.game_list.pk]))

    def test_invite_tells_the_invitee(self):
        response = self.invite("@Luis")
        self.assertTemplateUsed(response, "lists/_collaborators.html")
        self.assertContains(response, "Invitación pendiente")
        invitation = ListCollaborator.objects.get()
        self.assertEqual((invitation.user, invitation.accepted_at), (self.luis, None))
        notice = Notification.objects.get(user=self.luis)
        self.assertEqual(
            (notice.kind, notice.actor, notice.text, notice.url),
            (Notification.Kind.LIST_INVITE, self.ana, "ana te ha invitado a editar su lista «Para jugar juntos».",
             self.game_list.get_absolute_url()),
        )

    def test_invite_errors(self):
        self.assertContains(self.invite("nadie"), "No hay nadie con el nombre de usuario «nadie».")
        self.assertContains(self.invite("ana"), "Ya puedes editarla: es tuya.")
        self.invite("luis")
        self.assertContains(self.invite("luis"), "luis ya está invitado.")
        User.objects.create_user("leo", "leo@example.com", "x")
        self.assertContains(self.invite("leo"), "leo no te sigue: solo puedes invitar a gente que te sigue.")
        others = [User.objects.create_user(f"amigo{n}", f"amigo{n}@example.com", "x") for n in range(MAX_COLLABORATORS)]
        Follow.objects.bulk_create(Follow(follower=other, followed=self.ana) for other in others)
        for other in others[: MAX_COLLABORATORS - 1]:
            self.invite(other.username)
        self.assertContains(self.invite(others[-1].username), f"Caben {MAX_COLLABORATORS} personas por lista")
        self.assertEqual(ListCollaborator.objects.count(), MAX_COLLABORATORS)
        # Solo invita su dueño.
        self.client.force_login(self.eva)
        self.assertEqual(self.invite("eva").status_code, 404)

    def test_the_owner_hears_when_someone_accepts_and_suggestions_are_followers(self):
        response = self.client.get(reverse("lists:edit", args=[self.game_list.pk]))
        self.assertEqual(sorted(response.context["suggested"]), ["eva", "luis"])
        self.join(self.luis)
        notice = Notification.objects.get(user=self.ana)
        self.assertEqual(
            (notice.kind, notice.actor, notice.text),
            (Notification.Kind.LIST_ACCEPT, self.luis, "luis ya puede editar tu lista «Para jugar juntos»."),
        )
        self.client.post(reverse("lists:accept", args=[self.game_list.pk]))  # ya había aceptado: no repite
        self.assertEqual(Notification.objects.filter(user=self.ana).count(), 1)

    def test_a_tier_with_a_strange_color_takes_the_default_one(self):
        tier_list = GameList(
            user=self.ana, title="Rara", mode=GameList.Mode.TIERS,
            tiers=[{"id": 0, "label": "S", "color": "rojo"}, {"id": 1, "label": "A", "color": "#00FF00"}],
        )
        first, second = tier_list.tier_list
        self.assertEqual((first["color"], second["color"]), (DEFAULT_TIERS[0][1], "#00FF00"))

    def test_the_invitee_sees_the_private_list_and_can_accept(self):
        self.invite("luis")
        self.client.force_login(self.eva)
        self.assertEqual(self.client.get(self.game_list.get_absolute_url()).status_code, 404)
        self.client.force_login(self.luis)
        response = self.client.get(self.game_list.get_absolute_url())
        self.assertContains(response, "ana te ha invitado a editar esta lista.")
        # Hasta que acepta no puede editarla.
        self.assertEqual(self.client.post(reverse("lists:add", args=[self.game_list.pk, self.celeste.pk])).status_code, 404)
        response = self.client.post(reverse("lists:accept", args=[self.game_list.pk]))
        self.assertRedirects(response, f"{reverse('lists:edit', args=[self.game_list.pk])}#juegos", fetch_redirect_response=False)
        self.assertIsNotNone(ListCollaborator.objects.get().accepted_at)
        self.assertNotContains(self.client.get(self.game_list.get_absolute_url()), "te ha invitado")

    def test_collaborators_edit_the_games_but_not_the_list(self):
        self.join(self.luis)
        self.client.post(reverse("lists:add", args=[self.game_list.pk, self.celeste.pk]))
        item = self.game_list.items.get(game=self.celeste)
        self.assertEqual(item.added_by, self.luis)
        self.client.post(reverse("lists:note", args=[self.game_list.pk, self.celeste.pk]), {"note": "Lo propuse yo"})
        self.client.post(reverse("lists:move", args=[self.game_list.pk, self.celeste.pk]), {"a": "arriba"})
        self.assertEqual(list(self.game_list.items.values_list("game__name", flat=True)), ["Celeste", "Hades"])
        response = self.client.get(reverse("lists:edit", args=[self.game_list.pk]))
        self.assertContains(response, "Colaboras en esta lista de")
        self.assertNotContains(response, "Datos de la lista")
        self.assertNotContains(response, "Borrar la lista")
        self.assertEqual(self.client.post(reverse("lists:edit", args=[self.game_list.pk]), {"title": "Mía"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("lists:delete", args=[self.game_list.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("lists:invite", args=[self.game_list.pk]), {"usuario": "eva"}).status_code, 404)
        # Su actividad dice de quién es la lista.
        activity = Activity.objects.get(user=self.luis)
        self.assertEqual((activity.game_list, activity.list_added), (self.game_list, 1))
        self.client.force_login(self.ana)
        response = self.client.get(self.game_list.get_absolute_url())
        self.assertContains(response, "Añadido por luis")
        self.assertContains(response, ">luis</a>")

    def test_collaborators_find_the_list_in_the_game_page_and_their_profile(self):
        self.join(self.luis)
        response = self.client.get(self.celeste.get_absolute_url())
        self.assertEqual([game_list.title for game_list in response.context["my_lists"]], ["Para jugar juntos"])
        self.assertContains(response, "de ana")
        response = self.client.get(f"{reverse('profile', args=['luis'])}?vista=listas")
        self.assertContains(response, "De ana")
        # Los demás no la ven en el perfil de luis: es privada.
        self.client.force_login(self.eva)
        self.assertNotContains(self.client.get(f"{reverse('profile', args=['luis'])}?vista=listas"), "Para jugar juntos")

    def test_leaving_and_being_removed(self):
        self.join(self.luis)
        self.client.post(reverse("lists:add", args=[self.game_list.pk, self.celeste.pk]))
        response = self.client.post(reverse("lists:leave", args=[self.game_list.pk]))
        # Era privada: ya no puede verla, así que vuelve a sus listas. Lo que añadió se queda.
        self.assertRedirects(response, f"{reverse('profile', args=['luis'])}?vista=listas", fetch_redirect_response=False)
        self.assertFalse(ListCollaborator.objects.exists())
        self.assertTrue(self.game_list.items.filter(game=self.celeste).exists())
        self.join(self.eva)
        self.client.force_login(self.ana)
        self.client.post(reverse("lists:collaborator_remove", args=[self.game_list.pk, self.eva.pk]), **HTMX)
        self.assertFalse(ListCollaborator.objects.exists())
        self.client.force_login(self.eva)
        self.assertEqual(self.client.get(reverse("lists:edit", args=[self.game_list.pk])).status_code, 404)

    def test_owner_sees_who_collaborates_in_the_editor(self):
        Follow.objects.create(follower=self.ana, followed=self.eva)
        self.invite("luis")
        response = self.client.get(reverse("lists:edit", args=[self.game_list.pk]))
        self.assertContains(response, "Quién puede editarla")
        self.assertContains(response, "Invitación pendiente")
        # Para escribir el nombre, la gente a la que sigue y aún no ha invitado.
        self.assertEqual(response.context["suggested"], ["eva"])


class LibraryPresetTests(TestCase):
    """Tier lists que se rellenan solas con la biblioteca (lists.presets y lists.views.from_library)."""

    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.year = timezone.localdate().year
        this_year = timezone.make_aware(datetime(cls.year, 1, 1, 12))
        last_year = timezone.make_aware(datetime(cls.year - 1, 6, 1))
        cls.hades, cls.celeste, cls.tunic, cls.inside, cls.outer, cls.doom = make_games(
            "Hades", "Celeste", "Tunic", "Inside", "Outer Wilds", "Doom",
        )
        played = LibraryEntry.Status.PLAYED
        # Hades, jugado este año en Steam y con cinco estrellas; Celeste, jugado el año pasado en PlayStation pero
        # completado este año; Tunic, jugado el año pasado en Xbox y nada más.
        LibraryEntry.objects.create(
            user=cls.ana, game=cls.hades, status=played, rating=10, steam_playtime_minutes=600, steam_last_played=this_year,
        )
        LibraryEntry.objects.create(
            user=cls.ana, game=cls.celeste, status=LibraryEntry.Status.COMPLETED, completed_at=this_year,
            psn_playtime_minutes=1200, psn_last_played=last_year,
        )
        LibraryEntry.objects.create(
            user=cls.ana, game=cls.tunic, status=played, xbox_playtime_minutes=60, xbox_last_played=last_year,
        )
        # Inside, sin horas importadas pero con una reseña terminada este año; Outer Wilds, jugándolo (sin horas
        # importadas); Doom, pendiente.
        LibraryEntry.objects.create(user=cls.ana, game=cls.inside, status=played)
        Review.objects.create(user=cls.ana, game=cls.inside, text="Corto y redondo.", finished_on=date(cls.year, 1, 1))
        LibraryEntry.objects.create(user=cls.ana, game=cls.outer, status=LibraryEntry.Status.PLAYING)
        LibraryEntry.objects.create(user=cls.ana, game=cls.doom, status=LibraryEntry.Status.BACKLOG)

    def setUp(self):
        self.client.force_login(self.ana)

    def chosen(self, key):
        """Nombres de los juegos que elige para ana la plantilla key, en su orden."""
        return [entry.game.name for entry in presets.PRESETS[key].choose(self.ana, self.year)]

    def achieve(self, game, platform, unlocked, total, platinum=False):
        """Apunta a ana unlocked de total logros de game en platform, como una importación."""
        title = ExternalTitle.objects.create(platform=platform, external_id=game.slug, name=game.name, game=game)
        row = ImportedTitle(user=self.ana, title=title, game=game)
        row.set_achievements(unlocked, total, platinum)
        row.save()

    def create(self, key):
        """Crea como ana la tier list de la plantilla key."""
        return self.client.post(reverse("lists:from_library"), {"plantilla": key})

    def test_last_year_also_counts_the_achievements(self):
        # Hades se jugó por última vez este año, pero tiene un logro del año pasado: también entra en el año pasado.
        title = ExternalTitle.objects.create(platform="steam", external_id="hades", name="Hades", game=self.hades)
        row = ImportedTitle.objects.create(user=self.ana, title=title, game=self.hades, minutes=600)
        achievement = Achievement.objects.create(title=title, api_name="A", name="Logro")
        UnlockedAchievement.objects.create(
            imported=row, achievement=achievement, unlocked_at=timezone.make_aware(datetime(self.year - 1, 3, 3, 12)),
        )
        self.assertEqual(self.chosen("jugados-anterior"), ["Celeste", "Hades", "Tunic"])
        self.assertEqual(presets.PRESETS["jugados-anterior"].title_for(self.year), f"Jugados en {self.year - 1}")

    def test_each_preset_chooses_from_the_library(self):
        # De más a menos horas y, sin horas, de lo añadido hace menos a lo que más. Tunic es del año pasado.
        self.assertEqual(self.chosen("jugados"), ["Celeste", "Hades", "Outer Wilds", "Inside"])
        self.assertEqual(self.chosen("terminados"), ["Celeste", "Inside"])
        self.assertEqual(self.chosen("mas-jugados"), ["Celeste", "Hades", "Tunic"])
        self.assertEqual(
            [self.chosen(f"jugados-{key}") for key in ("steam", "psn", "xbox")], [["Hades"], ["Celeste"], ["Tunic"]],
        )
        self.assertEqual(self.chosen("pendiente"), ["Doom"])
        self.assertEqual(self.chosen("platinos"), [])
        self.achieve(self.hades, "steam", 49, 49)
        self.achieve(self.celeste, "psn", 30, 31, platinum=True)
        self.achieve(self.tunic, "xbox", 10, 40)
        self.assertEqual(self.chosen("platinos"), ["Celeste", "Hades"])

    def test_a_preset_becomes_a_private_tier_list_ready_to_drag(self):
        response = self.create("jugados")
        game_list = GameList.objects.get()
        # Sin abrirla aún: al abrirla se lee el aviso de que se ha creado.
        self.assertRedirects(response, game_list.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(
            (game_list.title, game_list.mode, game_list.is_public, [tier["label"] for tier in game_list.tiers]),
            (f"Jugados en {self.year}", GameList.Mode.TIERS, False, ["S", "A", "B", "C", "D", "F"]),
        )
        # En el orden de la plantilla y sin clasificar, salvo Hades, que empieza en S por sus cinco estrellas.
        self.assertEqual(
            [(item.game.name, item.tier, item.added_by) for item in game_list.items.select_related("game")],
            [("Celeste", None, self.ana), ("Hades", 0, self.ana), ("Outer Wilds", None, self.ana), ("Inside", None, self.ana)],
        )
        activity = Activity.objects.get(kind=Activity.Kind.LIST)
        self.assertEqual((activity.list_created, activity.list_added), (True, 4))
        page = self.client.get(response.url)
        self.assertContains(page, "Tier list creada con 4 juegos (1 ya colocado por tu nota)")
        self.assertContains(page, "data-tier-board")
        # La misma plantilla otra vez lleva a la que ya tiene en vez de repetirla.
        self.assertRedirects(self.create("jugados"), game_list.get_absolute_url())
        self.assertEqual(GameList.objects.count(), 1)

    def test_the_backlog_has_its_own_tiers_and_every_preset_needs_two_games(self):
        response = self.create("pendiente")
        self.assertRedirects(response, reverse("lists:new_tier_list"), fetch_redirect_response=False)
        self.assertFalse(GameList.objects.exists())
        hollow = make_games("Hollow Knight")[0]
        LibraryEntry.objects.create(user=self.ana, game=hollow, status=LibraryEntry.Status.BACKLOG, rating=8)
        self.create("pendiente")
        game_list = GameList.objects.get()
        self.assertEqual([tier["label"] for tier in game_list.tiers], ["Ya mismo", "Pronto", "Algún día", "Nunca"])
        # El pendiente no coloca por nota: todo empieza sin clasificar.
        self.assertEqual(list(game_list.items.values_list("tier", flat=True)), [None, None])

    def test_the_new_tier_list_page_offers_them(self):
        response = self.client.get(reverse("lists:new_tier_list"))
        self.assertContains(response, "Empieza desde tu biblioteca")
        self.assertContains(response, f"Jugados en {self.year}")
        shown = {preset["key"]: preset for preset in response.context["presets"]}
        # Las que no tienen juegos no salen y las que tienen uno solo aún no se pueden crear.
        self.assertNotIn("platinos", shown)
        self.assertEqual((shown["jugados"]["count"], shown["jugados"]["ready"]), (4, True))
        self.assertFalse(shown["jugados-xbox"]["ready"])
        self.assertContains(response, "Hacen falta al menos 2 juegos")
        # Si ya tiene una lista con ese título, la enseña en vez de ofrecer crearla.
        game_list = GameList.objects.create(user=self.ana, title=f"Jugados en {self.year}")
        self.assertContains(self.client.get(reverse("lists:new_tier_list")), game_list.get_absolute_url())
        # «Nueva lista» ya no las ofrece: son tier lists, y tienen su página.
        self.assertNotContains(self.client.get(reverse("lists:new")), "Empieza desde tu biblioteca")

    def test_only_known_presets_by_post_and_with_a_session(self):
        self.assertEqual(self.create("no-existe").status_code, 404)
        self.assertEqual(self.client.get(reverse("lists:from_library")).status_code, 405)
        self.client.logout()
        self.assertRedirects(
            self.create("jugados"), f"{reverse('login')}?next={reverse('lists:from_library')}", fetch_redirect_response=False,
        )


class OpenTierListTests(TestCase):
    """Tier lists abiertas a la comunidad: «Haz tu versión», la tier list de la comunidad (lists.community), los niveles
    fijos en cuanto hay versiones y la pestaña Tier lists."""

    @classmethod
    def setUpTestData(cls):
        cls.ana, cls.luis, cls.eva, cls.leo = [
            User.objects.create_user(name, f"{name}@example.com", "x") for name in ("ana", "luis", "eva", "leo")
        ]
        cls.hades, cls.celeste, cls.tunic = make_games("Hades", "Celeste", "Tunic")

    def setUp(self):
        self.open = GameList.objects.create(user=self.ana, title="Indies", mode=GameList.Mode.TIERS, is_open=True)
        for game in (self.hades, self.celeste, self.tunic):
            self.open.add(game, self.ana)

    def version(self, user, **tiers):
        """La versión de user de la abierta, con los juegos de tiers (su nombre en minúsculas → id del nivel) colocados."""
        version, _ = self.open.make_version(user)
        for name, tier in tiers.items():
            version.items.filter(game__name__iexact=name).update(tier=tier)
        return version

    def board(self):
        """La de la comunidad como {nombre del nivel (None, los que nadie ha colocado): [juegos]} y lo que devuelve
        community_board."""
        result = community_board(self.open)
        rows = {(level["label"] if level else None): [item.game.name for item in games] for level, games in result["rows"]}
        return rows, result

    def test_a_version_starts_with_the_same_games_and_tiers_unplaced(self):
        self.open.items.filter(game=self.hades).update(tier=0)
        self.client.force_login(self.luis)
        response = self.client.post(reverse("lists:make_version", args=[self.open.pk]))
        version = GameList.objects.get(user=self.luis)
        self.assertRedirects(response, version.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(
            (version.parent, version.title, version.tiers, version.is_public, version.is_tiered, version.is_open),
            (self.open, "Indies", self.open.tiers, True, True, False),
        )
        self.assertEqual(
            list(version.items.values_list("game__name", "tier")), [("Hades", None), ("Celeste", None), ("Tunic", None)],
        )
        activity = Activity.objects.get(user=self.luis)
        self.assertEqual((activity.game_list, activity.list_created), (version, True))
        # A ana, que la abrió, le llega un aviso con la versión de luis; volver a pulsarlo no lo repite.
        notice = Notification.objects.get(user=self.ana)
        self.assertEqual(
            (notice.kind, notice.actor, notice.text, notice.url),
            (Notification.Kind.LIST_VERSION, self.luis, "luis ha hecho su versión de tu tier list «Indies».",
             version.get_absolute_url()),
        )
        self.client.post(reverse("lists:make_version", args=[self.open.pk]))
        self.assertEqual(Notification.objects.filter(user=self.ana).count(), 1)

    def test_one_version_each_and_none_of_your_own(self):
        url = reverse("lists:make_version", args=[self.open.pk])
        self.client.force_login(self.luis)
        first, second = self.client.post(url), self.client.post(url)
        self.assertEqual(GameList.objects.filter(user=self.luis).count(), 1)
        self.assertEqual(first["Location"], second["Location"])
        self.client.force_login(self.ana)
        self.assertEqual(self.client.post(url).status_code, 400)
        with self.assertRaises(IntegrityError), transaction.atomic():
            GameList.objects.create(user=self.luis, title="Otra", mode=GameList.Mode.TIERS, parent=self.open)

    def test_only_open_public_tier_lists_take_versions(self):
        url = reverse("lists:make_version", args=[self.open.pk])
        self.client.force_login(self.luis)
        for changes in ({"is_open": False}, {"is_public": False}, {"mode": GameList.Mode.LIST}):
            values = {"is_open": True, "is_public": True, "mode": GameList.Mode.TIERS, **changes}
            GameList.objects.filter(pk=self.open.pk).update(**values)
            self.assertEqual(self.client.post(url).status_code, 404)
        self.assertFalse(GameList.objects.filter(user=self.luis).exists())
        self.client.logout()
        self.assertRedirects(self.client.post(url), f"{reverse('login')}?next={url}", fetch_redirect_response=False)

    def test_the_community_board_waits_for_enough_people(self):
        self.version(self.luis, hades=0)
        _, result = self.board()
        self.assertEqual((result["voters"], result["missing"]), (1, 2))
        page = self.client.get(self.open.get_absolute_url())
        self.assertEqual(page.context["view"], "niveles")
        self.assertContains(page, "falta")
        # Su autora coloca uno, y con la de eva ya son tres: la de la comunidad sale la primera.
        self.open.items.filter(game=self.hades).update(tier=1)
        self.version(self.eva, hades=2)
        _, result = self.board()
        self.assertEqual((result["voters"], result["missing"]), (3, 0))
        page = self.client.get(self.open.get_absolute_url())
        self.assertEqual(page.context["view"], "comunidad")
        self.assertContains(page, "La han hecho 3 personas")
        self.assertContains(page, "is-community")

    def test_each_game_goes_to_the_middle_of_its_votes(self):
        self.open.items.filter(game=self.hades).update(tier=0)
        self.version(self.luis, hades=0, celeste=2)
        self.version(self.eva, hades=1, celeste=0, tunic=5)
        rows, _ = self.board()
        self.assertEqual(rows, {"S": ["Hades"], "A": ["Celeste"], "B": [], "C": [], "D": [], "F": ["Tunic"]})

    def test_middle_tier_with_an_even_number_of_votes(self):
        self.assertEqual(middle_tier([3]), 3)
        self.assertEqual(middle_tier([5, 0, 0]), 0)
        # Entre S y B queda A; entre S y A, igual de cerca de la media, la mejor; entre A y B, la más cercana a la media.
        self.assertEqual(middle_tier([0, 2]), 1)
        self.assertEqual(middle_tier([0, 1]), 0)
        self.assertEqual(middle_tier([0, 1, 2, 5]), 2)

    def test_votes_spread_and_order_inside_a_tier(self):
        self.version(self.luis, hades=0, celeste=0)
        self.version(self.eva, hades=0, celeste=1)
        self.version(self.leo, celeste=0)
        rows = {(level["label"] if level else None): games for level, games in community_board(self.open)["rows"]}
        self.assertEqual([item.game.name for item in rows["S"]], ["Hades", "Celeste"])
        celeste = rows["S"][1]
        self.assertEqual(celeste.votes, 3)
        self.assertEqual([(level["label"], count) for level, count in celeste.spread], [("S", 2), ("A", 1)])
        self.assertEqual([item.game.name for item in rows[None]], ["Tunic"])

    def test_private_versions_and_their_own_tiers_do_not_count(self):
        private = self.version(self.luis, hades=0)
        private.is_public = False
        private.save()
        self.version(self.eva, hades=0)
        self.eva.profile.is_public = False
        self.eva.profile.save()
        extra = self.version(self.leo)
        extra.tiers = [*extra.tiers, {"id": 6, "label": "Mío", "color": "#22e0f0"}]
        extra.save()
        extra.items.filter(game=self.hades).update(tier=6)
        self.assertEqual(self.board()[1]["voters"], 0)

    def test_games_added_later_reach_the_versions(self):
        version = self.version(self.luis, hades=0)
        portal, = make_games("Portal")
        self.open.add(portal, self.ana)
        self.assertIsNone(version.items.get(game=portal).tier)
        # Quitarlo de la abierta no lo quita de las versiones: solo deja de contar en la de la comunidad.
        self.open.remove(portal)
        self.assertTrue(version.items.filter(game=portal).exists())

    def test_tiers_are_fixed_once_there_are_versions(self):
        self.version(self.luis)
        self.client.force_login(self.ana)
        url = reverse("lists:edit", args=[self.open.pk])
        self.assertContains(self.client.get(url), "Ya hay versiones de esta tier list")
        response = self.client.post(url, tier_form(title="Indies", is_open="on", nivel_5=""))
        self.assertContains(response, "Este nivel no se puede quitar")
        # Renombrar sí; añadir un nivel o dejar de ser tier list, no (esos campos no cuentan).
        self.client.post(url, tier_form(title="Indies", is_open="on", mode="list", nivel_0="Obras maestras", nivel_6="Nuevo", color_6="#22e0f0"))
        self.open.refresh_from_db()
        self.assertEqual([tier["label"] for tier in self.open.tiers], ["Obras maestras", "A", "B", "C", "D", "F"])
        self.assertTrue(self.open.is_tiered and self.open.is_open)

    def test_a_version_keeps_its_tiers_and_cannot_be_opened(self):
        version = self.version(self.luis)
        self.client.force_login(self.luis)
        url = reverse("lists:edit", args=[version.pk])
        response = self.client.get(url)
        self.assertNotIn("is_open", response.context["form"].fields)
        self.assertContains(response, "Es una versión de otra tier list")
        self.client.post(url, tier_form(title="Mis indies", is_open="on"))
        version.refresh_from_db()
        self.assertEqual((version.title, version.is_open, len(version.tiers)), ("Mis indies", False, 6))

    def test_opening_needs_a_public_tier_list(self):
        self.client.force_login(self.luis)
        response = self.client.post(reverse("lists:new_tier_list"), tier_form(is_open="on", is_public=""))
        self.assertContains(response, "Para abrirla a la comunidad tiene que ser visible para los demás.")
        self.assertFalse(GameList.objects.filter(title="Roguelikes").exists())
        self.client.post(reverse("lists:new_tier_list"), tier_form(is_open="on"))
        self.assertTrue(GameList.objects.get(title="Roguelikes").is_open)
        # «Nueva lista» no hace tier lists, así que no la ofrece.
        self.assertNotIn("is_open", self.client.get(reverse("lists:new")).context["form"].fields)

    def test_the_list_page_offers_your_version(self):
        self.client.force_login(self.luis)
        page = self.client.get(self.open.get_absolute_url())
        self.assertContains(page, "Haz tu versión")
        self.assertContains(page, "Aún no la ha hecho nadie")
        version = self.version(self.luis, hades=0)
        page = self.client.get(self.open.get_absolute_url())
        self.assertContains(page, "Ver tu versión")
        self.assertNotContains(page, "Haz tu versión")
        self.assertContains(page, "La ha hecho 1 persona.")
        mine = self.client.get(version.get_absolute_url())
        self.assertContains(mine, "Es tu versión de")
        self.assertContains(mine, f"{self.open.get_absolute_url()}?vista=comunidad")
        # Si la abierta deja de ser pública, la versión ya no dice cuál era.
        GameList.objects.filter(pk=self.open.pk).update(is_public=False)
        self.assertNotContains(self.client.get(version.get_absolute_url()), "Es tu versión de")

    def test_the_tier_lists_tab(self):
        GameList.objects.create(user=self.luis, title="Mi privada", mode=GameList.Mode.TIERS, is_public=False, is_open=True)
        self.version(self.eva, hades=0)
        response = self.client.get(reverse("lists:tier_lists"))
        self.assertEqual([game_list.title for game_list in response.context["popular"]], ["Indies"])
        self.assertEqual(response.context["popular"][0].version_count, 1)
        self.assertContains(response, "Abierta · 1 versión")
        self.assertNotContains(response, "Mi privada")
        self.assertContains(response, f'href="{reverse("lists:tier_lists")}"')
        # Con sesión, las tuyas van arriba y no se repiten abajo; las de la gente que sigues, aparte.
        Follow.objects.create(follower=self.ana, followed=self.eva)
        self.client.force_login(self.ana)
        response = self.client.get(reverse("lists:tier_lists"))
        self.assertEqual([game_list.title for game_list in response.context["mine"]], ["Indies"])
        self.assertEqual(response.context["recent"], [])
        self.assertEqual([game_list.user for game_list in response.context["followed"]], [self.eva])
        self.assertContains(response, "Versión de la de ana")

    def test_the_feed_says_whose_version_it_is(self):
        Follow.objects.create(follower=self.ana, followed=self.luis)
        self.client.force_login(self.luis)
        self.client.post(reverse("lists:make_version", args=[self.open.pk]))
        self.client.force_login(self.ana)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "hizo su versión de")
        self.assertContains(response, f'href="{self.ana.profile.get_absolute_url()}">{self.ana.profile.name}</a>')
