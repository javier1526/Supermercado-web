from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from .forms import FormularioAcceso
from .models import PerfilPersonal
from .permisos import requiere_perfil


class AccesoPersonal(LoginView):
    template_name = "personal/acceso.html"
    authentication_form = FormularioAcceso

    def get_success_url(self):
        return reverse("personal:panel")


@requiere_perfil(PerfilPersonal.REPONEDOR, PerfilPersonal.JEFATURA)
def panel(request):
    if request.user.personaltienda.perfil == PerfilPersonal.REPONEDOR:
        return redirect("personal:panel_reponedor")
    return redirect("personal:panel_jefatura")


@requiere_perfil(PerfilPersonal.REPONEDOR)
def panel_reponedor(request):
    return render(request, "personal/panel.html", {
        "titulo": "Panel del Reponedor",
        "secciones": (
            (
                "Registrar producto", "Crear un registro pendiente.",
                reverse("catalogo:registrar_producto"),
            ),
            (
                "Actualizar ubicación", "Cambiar pasillo o fotografía.",
                reverse("catalogo:seleccionar_ubicacion"),
            ),
            (
                "Actualizar stock", "Modificar la cantidad disponible.",
                reverse("catalogo:seleccionar_stock"),
            ),
            (
                "Última actualización", "Consultar cambios de ubicación.",
                reverse("catalogo:seleccionar_actualizacion"),
            ),
        ),
    })


@requiere_perfil(PerfilPersonal.JEFATURA)
def panel_jefatura(request):
    return render(request, "personal/panel.html", {
        "titulo": "Panel de Jefatura",
        "secciones": (
            (
                "Productos pendientes", "Revisar los registros nuevos.",
                reverse("catalogo:pendientes"),
            ),
            (
                "Gestión desde la ficha",
                "Gestionar precios y desactivaciones.",
                reverse("catalogo:seleccionar_gestion"),
            ),
            (
                "Actualizar stock", "Modificar la cantidad disponible.",
                reverse("catalogo:seleccionar_stock"),
            ),
            (
                "Última actualización", "Consultar cambios de ubicación.",
                reverse("catalogo:seleccionar_actualizacion"),
            ),
        ),
    })


@require_POST
@csrf_protect
def cerrar_sesion(request):
    logout(request)
    messages.success(request, "Sesión cerrada")
    return redirect("personal:acceso")
