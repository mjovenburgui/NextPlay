from django.urls import path

from . import views

app_name = "library"

urlpatterns = [
    path("<int:game_id>/estado/", views.set_status, name="set_status"),
    path("<int:game_id>/quitar/", views.remove, name="remove"),
    path("<int:game_id>/valorar/", views.rate, name="rate"),
    path("<int:game_id>/resena/", views.review, name="review"),
    path("<int:game_id>/deseo/", views.wish_toggle, name="wish_toggle"),
    path("<int:game_id>/deseo/precio/", views.wish_target, name="wish_target"),
    path("lista-de-deseos/", views.wishlist, name="wishlist"),
]
