from django.conf import settings
from django.conf.urls.static import static
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

# En local Django sirve las imágenes subidas; static() no hace nada si DEBUG está apagado.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
