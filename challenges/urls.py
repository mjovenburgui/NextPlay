"""Direcciones de los retos. Pantallas: Retos (retos/), Nuevo reto (retos/nuevo/), Página del reto (retos/<id>/) y Editar
reto (retos/<id>/editar/); borrar, unirse y dejarlo son acciones de sus botones.
"""

from django.urls import path

from . import views

app_name = "challenges"

urlpatterns = [
    path("retos/", views.explore, name="explore"),
    path("retos/nuevo/", views.new, name="new"),
    path("retos/<int:challenge_id>/", views.detail, name="detail"),
    path("retos/<int:challenge_id>/editar/", views.edit, name="edit"),
    path("retos/<int:challenge_id>/borrar/", views.delete, name="delete"),
    path("retos/<int:challenge_id>/unirse/", views.join, name="join"),
    path("retos/<int:challenge_id>/dejar/", views.leave, name="leave"),
]
