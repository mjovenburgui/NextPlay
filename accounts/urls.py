"""Direcciones de las cuentas. Pantallas: Registro (cuenta/registro/), Editar perfil (cuenta/perfil/, con su vista previa
y las imágenes de un juego para la cabecera), Juegos importados (cuenta/juegos-importados/), Avisos (avisos/), Iniciar
sesión y Recuperar contraseña (las de Django, en cuenta/), Importar desde un archivo (cuenta/importar-archivo/) y Perfil
(u/<usuario>/). La extensión de Playnite manda la biblioteca a api/playnite/. El resto son acciones de botones:
favoritos, tiendas, logros de la Ficha del juego y vincular, importar o desvincular Steam, PlayStation, Xbox y otras
plataformas.
"""

from django.urls import include, path

from . import views

urlpatterns = [
    path("cuenta/registro/", views.signup, name="signup"),
    path("cuenta/perfil/", views.edit_profile, name="edit_profile"),
    path("cuenta/perfil/vista-previa/", views.profile_preview, name="profile_preview"),
    path("cuenta/perfil/imagenes-de-juego/", views.banner_arts, name="banner_arts"),
    path("cuenta/perfil/cabecera-de-juego/<int:game_id>/", views.banner_from_game, name="banner_from_game"),
    path("cuenta/favoritos/<int:game_id>/", views.favorite_toggle, name="favorite_toggle"),
    path("cuenta/logros/<int:game_id>/", views.game_achievements, name="game_achievements"),
    path("cuenta/tiendas/", views.shops_update, name="shops_update"),
    path("cuenta/steam/vincular/", views.steam_link, name="steam_link"),
    path("cuenta/steam/vuelta/", views.steam_callback, name="steam_callback"),
    path("cuenta/steam/importar/", views.steam_import, name="steam_import"),
    path("cuenta/steam/automatica/", views.steam_auto_sync, name="steam_auto_sync"),
    path("cuenta/steam/deseados/", views.steam_wishlist, name="steam_wishlist"),
    path("cuenta/steam/<int:account_id>/desvincular/", views.steam_unlink, name="steam_unlink"),
    path("cuenta/playstation/sincronizar/", views.psn_sync, name="psn_sync"),
    path("cuenta/playstation/desvincular/", views.psn_unlink, name="psn_unlink"),
    path("cuenta/xbox/vincular/", views.xbox_link, name="xbox_link"),
    path("cuenta/xbox/vuelta/", views.xbox_callback, name="xbox_callback"),
    path("cuenta/xbox/desvincular/", views.xbox_unlink, name="xbox_unlink"),
    path("cuenta/juegos-importados/", views.imported_titles, name="imported_titles"),
    path("cuenta/importar-archivo/", views.import_file, name="import_file"),
    path("cuenta/playnite/codigo/", views.playnite_token, name="playnite_token"),
    path("cuenta/playnite/desvincular/", views.playnite_unlink, name="playnite_unlink"),
    path("cuenta/playnite/NextPlaySync.pext", views.playnite_extension, name="playnite_extension"),
    path("api/playnite/", views.playnite_api, name="playnite_api"),
    path("avisos/", views.notifications, name="notifications"),
    path("cuenta/juegos-importados/<int:pk>/asignar/", views.imported_title_assign, name="imported_title_assign"),
    path("cuenta/juegos-importados/<int:pk>/ocultar/", views.imported_title_hide, name="imported_title_hide"),
    path("cuenta/plataformas/anadir/", views.platform_add, name="platform_add"),
    path("cuenta/plataformas/<str:platform>/quitar/", views.platform_remove, name="platform_remove"),
    # login, logout and the password reset flow
    path("cuenta/", include("django.contrib.auth.urls")),
    path("u/<str:username>/", views.profile, name="profile"),
    path("u/<str:username>/resumen/<int:year>/", views.year_summary, name="year_summary"),
    path("u/<str:username>/resumen/<int:year>/imagen.png", views.year_summary_image, name="year_summary_image"),
]
