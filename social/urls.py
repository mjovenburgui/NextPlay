from django.urls import path

from . import views

app_name = "social"

# Cuelgan del perfil de cada usuario (/u/<usuario>/), igual que la página de cada reseña.
urlpatterns = [
    path("u/<str:username>/seguir/", views.follow, name="follow"),
    path("u/<str:username>/seguidores/", views.followers, name="followers"),
    path("u/<str:username>/siguiendo/", views.following, name="following"),
    path("u/<str:username>/actividad/", views.activity, name="activity"),
    # «Cargar más» del feed de la portada.
    path("actividad/", views.feed, name="feed"),
]
