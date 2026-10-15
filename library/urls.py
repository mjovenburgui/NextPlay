from django.urls import path

from . import views

app_name = "library"

urlpatterns = [
    path("<int:game_id>/estado/", views.set_status, name="set_status"),
    path("<int:game_id>/quitar/", views.remove, name="remove"),
    path("<int:game_id>/valorar/", views.rate, name="rate"),
]
