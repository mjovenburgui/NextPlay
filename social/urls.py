"""Direcciones de lo social. Pantallas: Seguidores y seguidos (u/<usuario>/seguidores/ y siguiendo/), Página de la
publicación (u/<usuario>/publicaciones/<id>/), Publicar (publicar/), Guardados (guardados/), Comunidad del juego
(juegos/<juego>/comunidad/), Hilo o pregunta (juegos/<juego>/comunidad/<id>/) y Nuevo hilo o pregunta
(juegos/<juego>/comunidad/nuevo/); actividad/, u/<usuario>/actividad/ y u/<usuario>/publicaciones/ son el «Cargar más»
del feed de Inicio y de las pestañas Actividad y Publicaciones del Perfil, y juegos/<juego>/publicaciones/, lo último de
la comunidad en la Ficha del juego. El resto son acciones de botones: seguir, borrar, «me gusta», guardar, comentar,
responder, elegir la respuesta que te ha servido, «¿Cómo se consigue?» y denunciar.
"""

from django.urls import path

from . import community, views

app_name = "social"

# Cuelgan del perfil de cada usuario (/u/<usuario>/), igual que la página de cada reseña.
urlpatterns = [
    path("u/<str:username>/seguir/", views.follow, name="follow"),
    path("u/<str:username>/seguidores/", views.followers, name="followers"),
    path("u/<str:username>/siguiendo/", views.following, name="following"),
    path("u/<str:username>/actividad/", views.activity, name="activity"),
    path("u/<str:username>/publicaciones/", views.user_posts, name="posts"),
    path("u/<str:username>/publicaciones/<int:post_id>/", views.post_detail, name="post"),
    path("juegos/<slug:slug>/publicaciones/", community.game_posts, name="game_posts"),
    # La comunidad de cada juego, que cuelga de su ficha (/juegos/<juego>/).
    path("juegos/<slug:slug>/comunidad/", community.community, name="community"),
    path("juegos/<slug:slug>/comunidad/nuevo/", community.thread_new, name="thread_new"),
    path("juegos/<slug:slug>/comunidad/<int:post_id>/", community.thread, name="thread"),
    path(
        "juegos/<slug:slug>/comunidad/logro/<int:achievement_id>/", community.achievement_question,
        name="achievement_question",
    ),
    path("publicaciones/<int:post_id>/responder/", community.thread_reply, name="thread_reply"),
    path("publicaciones/respuestas/<int:comment_id>/servido/", community.choose_answer, name="choose_answer"),
    path("publicaciones/respuestas/<int:comment_id>/borrar/", community.reply_delete, name="reply_delete"),
    path("guardados/", views.saved_posts, name="saved"),
    # «Cargar más» del feed de la portada.
    path("actividad/", views.feed, name="feed"),
    path("publicar/", views.post_new, name="post_new"),
    path("publicaciones/<int:post_id>/borrar/", views.post_delete, name="post_delete"),
    path("publicaciones/<int:post_id>/me-gusta/", views.post_like, name="post_like"),
    path("publicaciones/<int:post_id>/guardar/", views.save_post, name="save_post"),
    path("publicaciones/<int:post_id>/denunciar/", views.post_report, name="post_report"),
    path("publicaciones/<int:post_id>/comentar/", views.post_comment, name="post_comment"),
    path("publicaciones/comentarios/<int:comment_id>/borrar/", views.post_comment_delete, name="post_comment_delete"),
    path("publicaciones/comentarios/<int:comment_id>/denunciar/", views.post_comment_report, name="post_comment_report"),
]
