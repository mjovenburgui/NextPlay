"""Direcciones de la biblioteca. Pantallas: Escribir reseña (<juego>/resenas/nueva/ y resenas/<id>/editar/, con su vista
previa), Lista de deseos (lista-de-deseos/), Repasar la biblioteca (repasar/) y Qué jugar después (que-jugar/, con su
botón «No me interesa»). El resto son acciones de botones de la Ficha del juego (estado, quitar,
nota, «Lo quiero» y su precio) y de las reseñas y sus comentarios (borrar, «me gusta», comentar y denunciar). La
Página de la reseña está en config/urls.py.
"""

from django.urls import path

from . import views

app_name = "library"

urlpatterns = [
    path("<int:game_id>/estado/", views.set_status, name="set_status"),
    path("<int:game_id>/quitar/", views.remove, name="remove"),
    path("<int:game_id>/valorar/", views.rate, name="rate"),
    path("<int:game_id>/resenas/nueva/", views.review_new, name="review_new"),
    path("resenas/<int:review_id>/editar/", views.review_edit, name="review_edit"),
    path("resenas/<int:review_id>/borrar/", views.review_delete, name="review_delete"),
    path("resenas/<int:review_id>/me-gusta/", views.review_like, name="review_like"),
    path("resenas/<int:review_id>/comentar/", views.review_comment, name="review_comment"),
    path("resenas/<int:review_id>/denunciar/", views.review_report, name="review_report"),
    path("resenas/vista-previa/", views.review_preview, name="review_preview"),
    path("comentarios/<int:comment_id>/borrar/", views.comment_delete, name="comment_delete"),
    path("comentarios/<int:comment_id>/denunciar/", views.comment_report, name="comment_report"),
    path("<int:game_id>/deseo/", views.wish_toggle, name="wish_toggle"),
    path("<int:game_id>/deseo/precio/", views.wish_target, name="wish_target"),
    path("lista-de-deseos/", views.wishlist, name="wishlist"),
    path("repasar/", views.tidy, name="tidy"),
    path("repasar/<int:entry_id>/", views.tidy_answer, name="tidy_answer"),
    path("que-jugar/", views.next_games, name="next"),
    path("que-jugar/<int:game_id>/no-me-interesa/", views.not_interested, name="not_interested"),
]
