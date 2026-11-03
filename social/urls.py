"""Direcciones de lo social. Pantallas: Seguidores y seguidos (u/<usuario>/seguidores/ y siguiendo/), Página de la
publicación (u/<usuario>/publicaciones/<id>/) y Publicar (publicar/); actividad/ y u/<usuario>/actividad/ son el
«Cargar más» del feed de Inicio y de la pestaña Actividad del Perfil. El resto son acciones de botones: seguir,
borrar, «me gusta», comentar y denunciar.
"""

from django.urls import path

from . import views

app_name = "social"

# Cuelgan del perfil de cada usuario (/u/<usuario>/), igual que la página de cada reseña.
urlpatterns = [
    path("u/<str:username>/seguir/", views.follow, name="follow"),
    path("u/<str:username>/seguidores/", views.followers, name="followers"),
    path("u/<str:username>/siguiendo/", views.following, name="following"),
    path("u/<str:username>/actividad/", views.activity, name="activity"),
    path("u/<str:username>/publicaciones/<int:post_id>/", views.post_detail, name="post"),
    # «Cargar más» del feed de la portada.
    path("actividad/", views.feed, name="feed"),
    path("publicar/", views.post_new, name="post_new"),
    path("publicaciones/<int:post_id>/borrar/", views.post_delete, name="post_delete"),
    path("publicaciones/<int:post_id>/me-gusta/", views.post_like, name="post_like"),
    path("publicaciones/<int:post_id>/denunciar/", views.post_report, name="post_report"),
    path("publicaciones/<int:post_id>/comentar/", views.post_comment, name="post_comment"),
    path("publicaciones/comentarios/<int:comment_id>/borrar/", views.post_comment_delete, name="post_comment_delete"),
    path("publicaciones/comentarios/<int:comment_id>/denunciar/", views.post_comment_report, name="post_comment_report"),
]
