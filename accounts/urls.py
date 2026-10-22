from django.urls import include, path

from . import views

urlpatterns = [
    path("cuenta/registro/", views.signup, name="signup"),
    path("cuenta/perfil/", views.edit_profile, name="edit_profile"),
    path("cuenta/perfil/vista-previa/", views.profile_preview, name="profile_preview"),
    path("cuenta/perfil/imagenes-de-juego/", views.banner_arts, name="banner_arts"),
    path("cuenta/favoritos/<int:game_id>/", views.favorite_toggle, name="favorite_toggle"),
    path("cuenta/tiendas/", views.shops_update, name="shops_update"),
    path("cuenta/steam/vincular/", views.steam_link, name="steam_link"),
    path("cuenta/steam/vuelta/", views.steam_callback, name="steam_callback"),
    path("cuenta/steam/importar/", views.steam_import, name="steam_import"),
    path("cuenta/steam/automatica/", views.steam_auto_sync, name="steam_auto_sync"),
    path("cuenta/steam/<int:account_id>/desvincular/", views.steam_unlink, name="steam_unlink"),
    path("cuenta/playstation/sincronizar/", views.psn_sync, name="psn_sync"),
    path("cuenta/playstation/desvincular/", views.psn_unlink, name="psn_unlink"),
    path("cuenta/xbox/vincular/", views.xbox_link, name="xbox_link"),
    path("cuenta/xbox/vuelta/", views.xbox_callback, name="xbox_callback"),
    path("cuenta/xbox/desvincular/", views.xbox_unlink, name="xbox_unlink"),
    path("cuenta/juegos-importados/", views.imported_titles, name="imported_titles"),
    path("avisos/", views.notifications, name="notifications"),
    path("cuenta/juegos-importados/<int:pk>/asignar/", views.imported_title_assign, name="imported_title_assign"),
    path("cuenta/juegos-importados/<int:pk>/ocultar/", views.imported_title_hide, name="imported_title_hide"),
    path("cuenta/plataformas/anadir/", views.platform_add, name="platform_add"),
    path("cuenta/plataformas/<str:platform>/quitar/", views.platform_remove, name="platform_remove"),
    # login, logout and the password reset flow
    path("cuenta/", include("django.contrib.auth.urls")),
    path("u/<str:username>/", views.profile, name="profile"),
]
