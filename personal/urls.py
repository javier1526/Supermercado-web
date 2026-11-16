from django.urls import path

from . import views


app_name = "personal"

urlpatterns = [
    path("", views.AccesoPersonal.as_view(), name="acceso"),
    path("panel/", views.panel, name="panel"),
    path("panel/reponedor/", views.panel_reponedor, name="panel_reponedor"),
    path("panel/jefatura/", views.panel_jefatura, name="panel_jefatura"),
    path("cerrar-sesion/", views.cerrar_sesion, name="cerrar_sesion"),
]
