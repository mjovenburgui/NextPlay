"""Tests del proyecto que no son de ninguna app: las páginas de error propias (404.html, 500.html y la de formulario
caducado, config.views.csrf_failure), que cada función diga a qué pantallas afecta y que los formularios que recargan la
página avisen mientras trabajan (el script de base.html y los data-busy de sus botones)."""

import importlib.util
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.views.defaults import server_error


def load_generator():
    """El generador del PDF de funciones (docs/generar_pdf.py), para leer las docstrings con sus mismas reglas."""
    path = Path(settings.BASE_DIR) / "docs" / "generar_pdf.py"
    spec = importlib.util.spec_from_file_location("generar_pdf", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScreensInDocstringsTests(SimpleTestCase):
    """Cada función del proyecto termina su docstring con «Pantallas: …», con nombres de docs/pantallas.json (o «todas»
    y «ninguna»), como pide el PDF de funciones: un cambio que se lo salte no pasa los tests."""

    def test_every_function_says_its_screens(self):
        try:
            generator = load_generator()
        except ImportError:
            self.skipTest("El PDF de funciones necesita reportlab (requirements-dev.txt).")
        root = Path(settings.BASE_DIR)
        names = {screen["nombre"] for screen in generator.load_screens()}
        # Las apps del proyecto (las carpetas de primer nivel) y config.
        folders = {"config"} | {Path(app.path).name for app in apps.get_app_configs() if Path(app.path).parent == root}
        problems = []
        for folder in sorted(folders):
            for path in sorted((root / folder).rglob("*.py")):
                relative = path.relative_to(root).as_posix()
                if path.name == "__init__.py" or any(part in relative for part in generator.SKIP):
                    continue
                _, sections = generator.parse(relative)
                for _, _, functions in sections:
                    for name, _, doc, line in functions:
                        _, value, _, unknown = generator.split_screens(doc, names)
                        if value is None:
                            problems.append(f"{relative}:{line} {name}: falta el párrafo «Pantallas:»")
                        elif unknown:
                            problems.append(f"{relative}:{line} {name}: no están en docs/pantallas.json: {unknown}")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))


class ErrorPagesTests(TestCase):
    def test_page_not_found_keeps_the_header(self):
        response = self.client.get("/esto-no-existe/")
        self.assertContains(response, "No encontramos esta página", status_code=404)
        self.assertContains(response, 'class="topbar"', status_code=404)

    def test_server_error_page_needs_nothing_from_the_request(self):
        response = server_error(RequestFactory().get("/"))
        self.assertEqual(response.status_code, 500)
        self.assertIn("Algo ha fallado", response.content.decode())

    def test_expired_form_with_the_session_of_always(self):
        user = get_user_model().objects.create_user("ana", "ana@example.com", "x")
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.post(reverse("edit_profile"), {"display_name": "Ana"})
        self.assertContains(response, "El formulario ha caducado", status_code=403)
        self.assertContains(response, "Salir", status_code=403)  # con la sesión iniciada, como siempre


class BusyFormsTests(TestCase):
    """Los formularios que recargan la página avisan mientras trabajan: el script de base.html va en todas las páginas
    (salvo en la vista previa del perfil) y los botones lentos dicen qué hacen con data-busy."""

    @classmethod
    def setUpTestData(cls):
        from games.models import Game
        from library.models import LibraryEntry

        cls.user = get_user_model().objects.create_user("ana", "ana@example.com", "x")
        cls.game = Game.objects.create(name="Hades", slug="hades")
        # Escribir reseña solo se abre con el juego en la biblioteca.
        LibraryEntry.objects.create(user=cls.user, game=cls.game)

    def setUp(self):
        self.client.force_login(self.user)

    def test_every_page_has_the_script_but_the_profile_preview(self):
        script = 'document.addEventListener("submit"'
        self.assertContains(self.client.get(reverse("home")), script)
        self.assertContains(self.client.get(reverse("edit_profile")), script)
        self.assertNotContains(self.client.post(reverse("profile_preview"), {"display_name": "Ana"}), script)
        self.client.logout()
        self.assertContains(self.client.get(reverse("signup")), 'data-busy="Creando tu cuenta…"')

    def test_slow_buttons_say_what_they_do(self):
        pages = {
            reverse("edit_profile"): ['data-busy="Guardando…">Guardar cambios', 'data-busy="Importando tus juegos…"'],
            reverse("home"): ['data-busy="Publicando…">Publicar'],
            reverse("library:review_new", args=[self.game.pk]): [
                'name="publicar" data-busy="Publicando…"', 'name="borrador" data-busy="Guardando…"',
            ],
            reverse("import_file"): ['data-busy="Leyendo el archivo…"'],
            reverse("lists:new"): ['data-busy="Creando…">Crear lista'],
            reverse("challenges:new"): ['data-busy="Creando…"'],
        }
        for url, fragments in pages.items():
            response = self.client.get(url)
            for fragment in fragments:
                self.assertContains(response, fragment, msg_prefix=url)
