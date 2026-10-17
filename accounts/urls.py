from django.urls import include, path

from . import views

urlpatterns = [
    path("cuenta/registro/", views.signup, name="signup"),
    path("cuenta/perfil/", views.edit_profile, name="edit_profile"),
    path("cuenta/steam/vincular/", views.steam_link, name="steam_link"),
    path("cuenta/steam/vuelta/", views.steam_callback, name="steam_callback"),
    path("cuenta/steam/importar/", views.steam_import, name="steam_import"),
    path("cuenta/steam/<int:account_id>/desvincular/", views.steam_unlink, name="steam_unlink"),
    path("cuenta/playstation/sincronizar/", views.psn_sync, name="psn_sync"),
    path("cuenta/playstation/desvincular/", views.psn_unlink, name="psn_unlink"),
    path("cuenta/xbox/vincular/", views.xbox_link, name="xbox_link"),
    path("cuenta/xbox/vuelta/", views.xbox_callback, name="xbox_callback"),
    path("cuenta/xbox/desvincular/", views.xbox_unlink, name="xbox_unlink"),
    path("cuenta/plataformas/anadir/", views.platform_add, name="platform_add"),
    path("cuenta/plataformas/<str:platform>/quitar/", views.platform_remove, name="platform_remove"),
    # login, logout and the password reset flow
    path("cuenta/", include("django.contrib.auth.urls")),
    path("u/<str:username>/", views.profile, name="profile"),
]
