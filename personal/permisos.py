from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache

from .models import PerfilPersonal


def obtener_personal_autorizado(usuario):
    if not usuario.is_authenticated or not usuario.is_active:
        return None
    personal = getattr(usuario, "personaltienda", None)
    if personal is not None and personal.perfil in PerfilPersonal.values:
        return personal
    return None


def requiere_perfil(*perfiles):
    def decorar(vista):
        @never_cache
        @wraps(vista)
        def vista_protegida(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("personal:acceso")
            personal = obtener_personal_autorizado(request.user)
            if personal is None or personal.perfil not in perfiles:
                raise PermissionDenied
            return vista(request, *args, **kwargs)

        return vista_protegida

    return decorar
