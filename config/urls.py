from django.contrib import admin
from django.urls import include, path

from games import views as games_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", games_views.home, name="home"),
    path("juegos/", include("games.urls")),
    path("biblioteca/", include("library.urls")),
    path("", include("accounts.urls")),
]
