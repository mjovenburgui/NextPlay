"""Tests de las páginas de error propias: 404.html, 500.html y la de formulario caducado (config.views.csrf_failure)."""

from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.views.defaults import server_error


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
