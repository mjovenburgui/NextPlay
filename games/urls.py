"""Direcciones de los juegos. Pantallas: Catálogo (juegos/) y Ficha del juego (juegos/<juego>/), con los trozos de la
ficha que se cargan aparte (precios, historial de precios e imágenes y vídeos); «importar» es el botón del Catálogo
para traer de IGDB lo que buscas.
"""

from django.urls import path

from . import views

app_name = "games"

urlpatterns = [
    path("", views.catalog, name="catalog"),
    path("importar/", views.import_search, name="import_search"),
    path("<slug:slug>/", views.detail, name="detail"),
    path("<slug:slug>/precios/", views.game_prices, name="prices"),
    path("<slug:slug>/historial-de-precios/", views.price_history, name="price_history"),
    path("<slug:slug>/imagenes-y-videos/", views.game_media, name="media"),
]
