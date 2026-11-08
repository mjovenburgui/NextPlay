from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import matching
from accounts.models import ExternalTitle, ImportedTitle, Notification, Profile
from games.models import Game, Genre, Platform
from library.models import LibraryEntry, PlaySession
from lists.models import GameList
from social.models import Activity, Follow

from . import closing, progress
from .models import Challenge, Participation

User = get_user_model()


def make_game(name, genre=None, year=None):
    """Un juego del catálogo con su slug y, si se dan, su género y su año de lanzamiento."""
    game = Game.objects.create(
        name=name, slug=name.lower().replace(" ", "-"), first_release_date=date(year, 6, 1) if year else None,
    )
    if genre:
        game.genres.add(genre)
    return game


def complete(user, game, when):
    """Pone game en la biblioteca de user como completado en el momento when."""
    return LibraryEntry.objects.update_or_create(
        user=user, game=game, defaults={"status": LibraryEntry.Status.COMPLETED, "completed_at": when},
    )[0]


def moment(year, month, day):
    """Un momento de ese día, a mediodía en España."""
    return timezone.make_aware(datetime(year, month, day, 12))


class ChallengeRulesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.horror = Genre.objects.create(name="Terror", slug="terror")
        cls.pc = Platform.objects.create(name="PC (Microsoft Windows)", abbreviation="PC", slug="pc")

    def test_texts_of_each_kind(self):
        count = Challenge(
            creator=self.ana, title="Octubre de miedo", kind=Challenge.Kind.COUNT, target=5, genre=self.horror,
            starts_on=date(2026, 10, 1), ends_on=date(2026, 10, 31),
        )
        self.assertEqual(count.rules_text, "Termina 5 juegos de Terror")
        self.assertEqual(count.dates_text, "del 1 de octubre de 2026 al 31 de octubre de 2026")
        hours = Challenge(
            creator=self.ana, title="Maratón", kind=Challenge.Kind.HOURS, target=1, platform=self.pc,
            released_from=2000, released_to=2010,
        )
        self.assertEqual(hours.rules_text, "Juega 1 hora a juegos de PC lanzados entre 2000 y 2010")
        self.assertEqual(hours.dates_text, "sin fechas")
        self.assertEqual(hours.goal, 60)
        games = Challenge.objects.create(creator=self.ana, title="Souls", kind=Challenge.Kind.GAMES, starts_on=date(2026, 1, 1))
        games.games.set([make_game("Dark Souls"), make_game("Bloodborne")])
        self.assertEqual(games.rules_text, "Termina los 2 juegos")
        self.assertEqual(games.goal, 2)
        games.target = 1
        self.assertEqual(games.rules_text, "Termina 1 de estos 2 juegos")
        self.assertEqual(games.dates_text, "desde el 1 de enero de 2026")

    def test_dates_and_days_left(self):
        today = timezone.localdate()
        running = Challenge(creator=self.ana, kind=Challenge.Kind.COUNT, target=1, ends_on=today + timedelta(days=2))
        self.assertEqual((running.is_over, running.is_upcoming, running.days_left), (False, False, 3))
        self.assertTrue(Challenge(kind=Challenge.Kind.COUNT, ends_on=today - timedelta(days=1)).is_over)
        self.assertTrue(Challenge(kind=Challenge.Kind.COUNT, starts_on=today + timedelta(days=1)).is_upcoming)


class ProgressTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.horror = Genre.objects.create(name="Terror", slug="terror")
        cls.silent, cls.alan, cls.mario = (
            make_game("Silent Hill", cls.horror, 1999), make_game("Alan Wake", cls.horror, 2010), make_game("Mario", year=1985),
        )

    def test_count_counts_completions_between_the_dates_that_match(self):
        challenge = Challenge.objects.create(
            creator=self.ana, title="Octubre", kind=Challenge.Kind.COUNT, target=2, genre=self.horror,
            starts_on=date(2026, 10, 1), ends_on=date(2026, 10, 31),
        )
        complete(self.ana, self.silent, moment(2026, 10, 3))
        complete(self.ana, self.alan, moment(2026, 9, 30))
        complete(self.ana, self.mario, moment(2026, 10, 5))
        complete(self.luis, self.silent, timezone.make_aware(datetime(2026, 10, 31, 23, 30)))
        complete(self.luis, self.alan, moment(2026, 10, 10))
        self.assertEqual(progress.done_by(challenge, [self.ana, self.luis]), {self.ana.pk: 1, self.luis.pk: 2})
        self.assertEqual(progress.summary(challenge, 1), {"value": 1, "goal_value": 2, "percent": 50, "reached": False, "hours": False})

    def test_without_start_date_old_completions_count_even_without_date(self):
        challenge = Challenge.objects.create(creator=self.ana, title="Terror", kind=Challenge.Kind.COUNT, target=2, genre=self.horror)
        complete(self.ana, self.silent, None)
        complete(self.ana, self.alan, moment(2020, 1, 1))
        self.assertEqual(progress.done_by(challenge, [self.ana]), {self.ana.pk: 2})

    def test_games_only_counts_its_games(self):
        challenge = Challenge.objects.create(creator=self.ana, title="Clásicos", kind=Challenge.Kind.GAMES)
        challenge.games.set([self.silent, self.mario])
        complete(self.ana, self.silent, None)
        complete(self.ana, self.alan, None)
        self.assertEqual(progress.done_by(challenge, [self.ana]), {self.ana.pk: 1})
        self.assertEqual(progress.finished_games(challenge, self.ana), {self.silent.pk})

    def test_hours_add_up_sessions_between_the_dates(self):
        challenge = Challenge.objects.create(
            creator=self.ana, title="Noviembre", kind=Challenge.Kind.HOURS, target=2, genre=self.horror,
            starts_on=date(2026, 11, 1), ends_on=date(2026, 11, 30),
        )
        PlaySession.add(self.ana, self.silent.pk, "steam", date(2026, 11, 2), 50)
        PlaySession.add(self.ana, self.silent.pk, "steam", date(2026, 11, 2), 30)
        PlaySession.add(self.ana, self.alan.pk, "psn", date(2026, 11, 30), 45)
        PlaySession.add(self.ana, self.alan.pk, "psn", date(2026, 12, 1), 600)
        PlaySession.add(self.ana, self.mario.pk, "steam", date(2026, 11, 5), 600)
        self.assertEqual(PlaySession.objects.get(game=self.silent).minutes, 80)
        done = progress.done_by(challenge, [self.ana])
        self.assertEqual(done, {self.ana.pk: 125})
        summary = progress.summary(challenge, done[self.ana.pk])
        self.assertEqual((summary["value"], summary["goal_value"], summary["reached"], summary["hours"]), (2.1, 2, True, True))

    def test_completing_a_game_marks_the_challenge_won(self):
        challenge = Challenge.objects.create(creator=self.ana, title="Uno de terror", kind=Challenge.Kind.COUNT, target=1, genre=self.horror)
        Participation.objects.create(challenge=challenge, user=self.ana)
        self.client.force_login(self.ana)
        self.client.post(reverse("library:set_status", args=[self.mario.pk]), {"status": "completed"})
        self.assertIsNone(Participation.objects.get().completed_at)
        self.client.post(reverse("library:set_status", args=[self.silent.pk]), {"status": "completed"})
        entry = LibraryEntry.objects.get(user=self.ana, game=self.silent)
        self.assertIsNotNone(entry.completed_at)
        self.assertIsNotNone(Participation.objects.get().completed_at)
        # Cambiarlo de estado borra la fecha; volver a completarlo pone la nueva.
        self.client.post(reverse("library:set_status", args=[self.silent.pk]), {"status": "playing"})
        self.assertIsNone(LibraryEntry.objects.get(pk=entry.pk).completed_at)

    def test_imports_record_sessions_only_for_known_hours(self):
        entry = LibraryEntry.objects.create(user=self.ana, game=self.alan)
        titles = {}
        for name, game, minutes in (("Silent", self.silent, 100), ("Alan", self.alan, 300)):
            titles[name] = ImportedTitle.objects.create(
                user=self.ana, title=ExternalTitle.objects.create(platform="steam", external_id=name, name=name, game=game),
                minutes=minutes, last_played=moment(2026, 11, 3),
            )
        # La primera importación trae lo de antes: no son sesiones (ni del que es nuevo ni del que ya estaba sin horas).
        matching.update_library(self.ana, "steam", record_activity=True)
        self.assertFalse(PlaySession.objects.exists())
        entry.refresh_from_db()
        self.assertEqual(entry.steam_playtime_minutes, 300)
        # En la siguiente, lo que ha subido sí, el día de la última partida.
        ImportedTitle.objects.filter(pk=titles["Alan"].pk).update(minutes=420, last_played=moment(2026, 11, 4))
        matching.update_library(self.ana, "steam", record_activity=True)
        session = PlaySession.objects.get()
        self.assertEqual((session.game, session.platform, session.day, session.minutes), (self.alan, "steam", date(2026, 11, 4), 120))
        # Las correcciones del emparejamiento no son sesiones.
        ImportedTitle.objects.filter(pk=titles["Silent"].pk).update(minutes=500)
        matching.update_library(self.ana, "steam")
        self.assertEqual(PlaySession.objects.count(), 1)


class ChallengePagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.eva = User.objects.create_user("eva", "eva@example.com", "x")
        cls.horror = Genre.objects.create(name="Terror", slug="terror")
        cls.silent = make_game("Silent Hill", cls.horror, 1999)
        cls.alan = make_game("Alan Wake", cls.horror, 2010)

    def setUp(self):
        self.client.force_login(self.ana)

    def create(self, **data):
        """Crea como ana un reto desde el formulario y lo devuelve."""
        self.client.post(reverse("challenges:new"), {"title": "Terror", "description": "", "is_public": "on", "kind": "count", "target": 2, **data})
        return Challenge.objects.latest("pk")

    def test_create_a_count_challenge_and_join_it(self):
        response = self.client.post(reverse("challenges:new"), {
            "title": "  Octubre   de miedo ", "description": "", "is_public": "on", "kind": "count", "target": 2,
            "genre": self.horror.pk, "starts_on": "2026-10-01", "ends_on": "2026-10-31",
        })
        challenge = Challenge.objects.get()
        self.assertRedirects(response, challenge.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual((challenge.title, challenge.creator, challenge.genre, challenge.target), ("Octubre de miedo", self.ana, self.horror, 2))
        self.assertTrue(Participation.objects.filter(challenge=challenge, user=self.ana).exists())

    def test_form_errors(self):
        url = reverse("challenges:new")
        base = {"title": "Reto", "description": "", "kind": "count"}
        self.assertContains(self.client.post(url, base), "Di cuántos juegos hay que terminar.")
        self.assertContains(self.client.post(url, {**base, "kind": "hours"}), "Di cuántas horas hay que jugar.")
        self.assertContains(self.client.post(url, {**base, "target": 3, "starts_on": "2026-10-10", "ends_on": "2026-10-01"}), "No puede terminar antes de empezar.")
        self.assertContains(self.client.post(url, {**base, "target": 3, "released_from": 2010, "released_to": 2000}), "No puede ser antes del año")
        self.assertContains(self.client.post(url, {**base, "kind": "games"}), "Elige la lista con los juegos del reto.")
        self.assertFalse(Challenge.objects.exists())

    def test_convert_a_list_into_a_challenge(self):
        game_list = GameList.objects.create(user=self.luis, title="Terror clásico")
        for game in (self.silent, self.alan):
            game_list.add(game, self.luis)
        response = self.client.get(game_list.get_absolute_url())
        self.assertContains(response, f"{reverse('challenges:new')}?lista={game_list.pk}")
        response = self.client.get(reverse("challenges:new"), {"lista": game_list.pk})
        self.assertContains(response, "Pásate «Terror clásico»")
        challenge = self.create(kind="games", target="", source_list=game_list.pk, lista=game_list.pk, genre=self.horror.pk)
        self.assertEqual(set(challenge.games.all()), {self.silent, self.alan})
        self.assertEqual((challenge.source_list, challenge.genre, challenge.goal), (game_list, None, 2))
        # La lista cambia después; el reto no.
        game_list.remove(self.alan)
        self.assertEqual(challenge.games.count(), 2)
        # Una lista privada de otro no vale.
        private = GameList.objects.create(user=self.luis, title="Secreta", is_public=False)
        private.add(self.silent, self.luis)
        response = self.client.post(reverse("challenges:new"), {
            "title": "X", "description": "", "kind": "games", "source_list": private.pk, "lista": private.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Challenge.objects.count(), 1)

    def test_page_shows_progress_and_ranking(self):
        challenge = self.create()
        for user in (self.luis, self.eva):
            Participation.objects.create(challenge=challenge, user=user)
        complete(self.luis, self.silent, None)
        complete(self.luis, self.alan, None)
        complete(self.ana, self.alan, None)
        self.eva.profile.is_public = False
        self.eva.profile.save()
        response = self.client.get(challenge.get_absolute_url())
        self.assertEqual([participation.user.username for participation in response.context["ranking"]], ["luis", "ana"])
        self.assertEqual(response.context["mine"].progress["value"], 1)
        self.assertContains(response, "Tu progreso")
        # Luis ha llegado al objetivo: sale como conseguido, pero la página no apunta nada; lo apunta check.
        self.assertContains(response, "Conseguido")
        self.assertIsNone(Participation.objects.get(user=self.luis).completed_at)
        progress.check(self.luis)
        self.assertIsNotNone(Participation.objects.get(user=self.luis).completed_at)

    def test_private_challenges_are_only_for_their_creator(self):
        challenge = self.create(is_public="")
        self.client.force_login(self.luis)
        self.assertEqual(self.client.get(challenge.get_absolute_url()).status_code, 404)
        self.assertEqual(self.client.post(reverse("challenges:join", args=[challenge.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("challenges:explore")), "Terror")

    def test_join_leave_and_closed_challenges(self):
        challenge = self.create()
        self.client.force_login(self.luis)
        self.client.post(reverse("challenges:join", args=[challenge.pk]))
        self.assertTrue(Participation.objects.filter(challenge=challenge, user=self.luis).exists())
        response = self.client.get(reverse("challenges:explore"))
        self.assertEqual([item.title for item in response.context["mine"]], ["Terror"])
        self.assertEqual(response.context["mine"][0].participant_count, 2)
        self.client.post(reverse("challenges:leave", args=[challenge.pk]))
        self.assertFalse(Participation.objects.filter(challenge=challenge, user=self.luis).exists())
        Challenge.objects.filter(pk=challenge.pk).update(ends_on=timezone.localdate() - timedelta(days=1))
        self.assertEqual(self.client.post(reverse("challenges:join", args=[challenge.pk])).status_code, 400)

    def test_only_the_creator_edits_or_deletes(self):
        challenge = self.create()
        self.client.force_login(self.luis)
        self.assertEqual(self.client.get(reverse("challenges:edit", args=[challenge.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("challenges:delete", args=[challenge.pk])).status_code, 404)
        self.client.force_login(self.ana)
        response = self.client.post(reverse("challenges:edit", args=[challenge.pk]), {"title": "Terror total", "description": "", "kind": "hours", "target": 99})
        self.assertRedirects(response, challenge.get_absolute_url(), fetch_redirect_response=False)
        challenge.refresh_from_db()
        # Cambia el título, pero no las reglas.
        self.assertEqual((challenge.title, challenge.kind, challenge.target, challenge.is_public), ("Terror total", "count", 2, False))
        self.client.post(reverse("challenges:delete", args=[challenge.pk]))
        self.assertFalse(Challenge.objects.exists())

    def test_explore_sections(self):
        self.create(title="De Ana")
        self.client.force_login(self.luis)
        Follow.objects.create(follower=self.luis, followed=self.ana)
        response = self.client.get(reverse("challenges:explore"))
        self.assertEqual([item.title for item in response.context["followed"]], ["De Ana"])
        self.assertEqual([item.title for item in response.context["popular"]], ["De Ana"])
        # Una vez dentro sale en «Tus retos» y ya no abajo.
        self.client.post(reverse("challenges:join", args=[Challenge.objects.get().pk]))
        response = self.client.get(reverse("challenges:explore"))
        self.assertEqual([item.title for item in response.context["mine"]], ["De Ana"])
        self.assertEqual((response.context["popular"], response.context["recent"], response.context["followed"]), ([], [], []))
        self.client.logout()
        response = self.client.get(reverse("challenges:explore"))
        self.assertContains(response, "De Ana")
        self.assertNotIn("mine", response.context)


class ChallengeNewsTests(TestCase):
    """Fase 2: lo que cuentan los retos fuera de su página: los avisos, la actividad, la sección del perfil y el repaso
    de cada noche."""

    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana", "ana@example.com", "x")
        cls.luis = User.objects.create_user("luis", "luis@example.com", "x")
        cls.hades = make_game("Hades")
        cls.celeste = make_game("Celeste")

    def challenge(self, title="Dos juegos", target=1, creator=None, **fields):
        """Un reto de número de juegos de creator (ana), con creator ya dentro."""
        challenge = Challenge.objects.create(
            creator=creator or self.ana, title=title, kind=Challenge.Kind.COUNT, target=target, **fields,
        )
        Participation.objects.create(challenge=challenge, user=challenge.creator)
        return challenge

    def texts(self, user, kind):
        """Los textos de los avisos de tipo kind de user, del más nuevo al más viejo."""
        return list(Notification.objects.filter(user=user, kind=kind).values_list("text", flat=True))

    def test_winning_notifies_once_and_shows_in_the_feed(self):
        self.challenge(title="Uno cualquiera")
        self.client.force_login(self.ana)
        self.client.post(reverse("library:set_status", args=[self.hades.pk]), {"status": "completed"})
        self.assertIsNotNone(Participation.objects.get().completed_at)
        self.assertEqual(
            self.texts(self.ana, Notification.Kind.CHALLENGE_WON), ["¡Has conseguido el reto «Uno cualquiera»!"],
        )
        self.assertEqual(Activity.objects.get(kind=Activity.Kind.CHALLENGE).challenge_verb, "consiguió el reto")
        # Terminar otro juego no lo repite.
        self.client.post(reverse("library:set_status", args=[self.celeste.pk]), {"status": "completed"})
        self.assertEqual(len(self.texts(self.ana, Notification.Kind.CHALLENGE_WON)), 1)
        self.client.force_login(self.luis)
        response = self.client.get(reverse("home"), {"ver": "descubrir"})
        self.assertContains(response, "consiguió el reto")
        self.assertContains(response, "Uno cualquiera")

    def test_joining_tells_the_creator_once_and_leaving_forgets_the_activity(self):
        challenge = self.challenge(target=2)
        join = reverse("challenges:join", args=[challenge.pk])
        self.client.force_login(self.luis)
        self.client.post(join)
        self.assertEqual(
            self.texts(self.ana, Notification.Kind.CHALLENGE_JOINED), ["luis se ha unido a tu reto «Dos juegos»."],
        )
        self.assertEqual(Activity.objects.get(user=self.luis).challenge_verb, "se unió al reto")
        self.client.post(reverse("challenges:leave", args=[challenge.pk]))
        self.assertFalse(Activity.objects.filter(user=self.luis).exists())
        self.client.post(join)  # vuelve: a ana no se le avisa otra vez
        self.assertEqual(len(self.texts(self.ana, Notification.Kind.CHALLENGE_JOINED)), 1)

    def test_private_challenges_stay_out_of_other_peoples_feed(self):
        self.client.force_login(self.ana)
        self.client.post(reverse("challenges:new"), {
            "title": "Mi secreto", "description": "", "is_public": "", "kind": "count", "target": 3,
        })
        self.assertEqual(Activity.objects.get(user=self.ana).challenge_verb, "creó el reto")
        self.assertContains(self.client.get(reverse("profile", args=["ana"]), {"vista": "actividad"}), "Mi secreto")
        self.client.force_login(self.luis)
        self.assertNotContains(self.client.get(reverse("home"), {"ver": "descubrir"}), "Mi secreto")
        self.assertNotContains(self.client.get(reverse("profile", args=["ana"]), {"vista": "actividad"}), "Mi secreto")

    def test_profile_section_with_badges_and_the_ones_in_progress(self):
        won = self.challenge(title="Conseguido")
        Participation.objects.filter(challenge=won).update(completed_at=timezone.now())
        self.challenge(title="En marcha", target=3)
        self.challenge(title="Secreto", is_public=False)
        url = reverse("profile", args=["ana"])
        response = self.client.get(url)
        showcase = response.context["challenge_showcase"]
        self.assertEqual([challenge.title for challenge in showcase["won"]], ["Conseguido"])
        self.assertEqual([challenge.title for challenge in showcase["active"]], ["En marcha"])
        self.assertContains(response, "Lleva 0 de 3")
        self.client.force_login(self.ana)
        response = self.client.get(url)
        self.assertEqual(
            {challenge.title for challenge in response.context["challenge_showcase"]["active"]}, {"En marcha", "Secreto"},
        )
        self.assertContains(response, "Llevas 0 de 3")
        # Sin la sección en el escaparate no se pide nada.
        Profile.objects.filter(user=self.ana).update(sections=[["retos", False]])
        self.assertIsNone(self.client.get(url).context["challenge_showcase"])

    def test_late_syncs_still_count_for_a_week(self):
        today = timezone.localdate()
        self.challenge(title="Reciente", ends_on=today - timedelta(days=2))
        self.challenge(title="Antiguo", ends_on=today - timedelta(days=progress.GRACE_DAYS + 2))
        complete(self.ana, self.hades, timezone.now() - timedelta(days=10))
        self.assertEqual([challenge.title for challenge in progress.check(self.ana)], ["Reciente"])

    def test_nightly_review_reminds_grants_and_closes(self):
        today = timezone.localdate()
        ending = self.challenge(title="Se acaba", target=2, ends_on=today + timedelta(days=2))
        late = self.challenge(title="Tardío", starts_on=today - timedelta(days=10), ends_on=today - timedelta(days=3))
        over = self.challenge(title="Terminado", starts_on=today - timedelta(days=20), ends_on=today - timedelta(days=8))
        Participation.objects.create(challenge=over, user=self.luis)
        # Sin pasar por la biblioteca: nadie ha mirado aún si cuentan.
        complete(self.ana, self.hades, timezone.now() - timedelta(days=5))
        complete(self.luis, self.celeste, timezone.now() - timedelta(days=12))

        self.assertEqual(closing.close_all(), {"won": 2, "ending": 1, "closed": 1})
        self.assertEqual(
            self.texts(self.ana, Notification.Kind.CHALLENGE_ENDING),
            ["Te quedan 3 días para el reto «Se acaba»: llevas 1 de 2 juegos."],
        )
        self.assertIsNotNone(Participation.objects.get(challenge=late, user=self.ana).completed_at)
        self.assertEqual(
            self.texts(self.luis, Notification.Kind.CHALLENGE_RESULT),
            ["El reto «Terminado» ha terminado: lo conseguiste y quedaste 1.º de 2."],
        )
        self.assertEqual(
            self.texts(self.ana, Notification.Kind.CHALLENGE_RESULT),
            ["El reto «Terminado» ha terminado: llegaste a 0 de 1 juego y quedaste 2.º de 2."],
        )
        over.refresh_from_db()
        self.assertIsNotNone(over.closed_at)
        self.assertIsNone(Challenge.objects.get(pk=ending.pk).closed_at)
        # La noche siguiente no repite nada.
        self.assertEqual(closing.close_all(), {"won": 0, "ending": 0, "closed": 0})
        self.assertEqual(Notification.objects.filter(kind=Notification.Kind.CHALLENGE_ENDING).count(), 1)

    def test_nightly_reviews_the_challenges_after_syncing(self):
        from accounts.management.commands.nightly import Command

        self.assertLess(Command.TASKS.index("sync_libraries"), Command.TASKS.index("close_challenges"))
