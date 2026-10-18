from django.urls import path

from . import views

app_name = "games"

urlpatterns = [
    path("", views.catalog, name="catalog"),
    path("importar/", views.import_search, name="import_search"),
    path("<slug:slug>/", views.detail, name="detail"),
    path("<slug:slug>/historial-de-precios/", views.price_history, name="price_history"),
]
