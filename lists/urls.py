from django.urls import path

from . import views

app_name = "lists"

urlpatterns = [
    # La página de cada lista cuelga del perfil de su autor, como las de las reseñas y las publicaciones.
    path("u/<str:username>/listas/<int:list_id>/", views.detail, name="detail"),
    path("juegos/<slug:slug>/listas/", views.game_lists, name="game"),
    path("listas/nueva/", views.new, name="new"),
    path("listas/<int:list_id>/editar/", views.edit, name="edit"),
    path("listas/<int:list_id>/borrar/", views.delete, name="delete"),
    path("listas/<int:list_id>/buscar/", views.search, name="search"),
    path("listas/<int:list_id>/lo-quiero/", views.wish_missing, name="wish_missing"),
    path("listas/<int:list_id>/juegos/<int:game_id>/anadir/", views.add, name="add"),
    path("listas/<int:list_id>/juegos/<int:game_id>/quitar/", views.remove, name="remove"),
    path("listas/<int:list_id>/juegos/<int:game_id>/mover/", views.move, name="move"),
    path("listas/<int:list_id>/juegos/<int:game_id>/comentario/", views.note, name="note"),
]
