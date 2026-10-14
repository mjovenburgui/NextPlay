from django.urls import include, path

from . import views

urlpatterns = [
    path("cuenta/registro/", views.signup, name="signup"),
    path("cuenta/perfil/", views.edit_profile, name="edit_profile"),
    # login, logout and the password reset flow
    path("cuenta/", include("django.contrib.auth.urls")),
    path("u/<str:username>/", views.profile, name="profile"),
]
