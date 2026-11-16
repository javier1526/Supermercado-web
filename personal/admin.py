from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User

from .formularios_admin import FormularioAltaUsuario, FormularioCambioUsuario
from .models import PersonalTienda


admin.site.unregister(User)


@admin.register(User)
class AdministracionUsuario(UserAdmin):
    add_form = FormularioAltaUsuario
    form = FormularioCambioUsuario
    add_fieldsets = (
        (None, {"fields": (
            "username", "first_name", "perfil", "usable_password",
            "password1", "password2",
        )}),
    )
    fieldsets = UserAdmin.fieldsets + (
        ("Personal de tienda", {"fields": ("perfil",)}),
    )
    list_display = UserAdmin.list_display + ("perfil_tienda",)
    list_select_related = ("personaltienda",)

    @admin.display(description="Perfil de tienda")
    def perfil_tienda(self, usuario):
        personal = getattr(usuario, "personaltienda", None)
        if personal is not None:
            return personal.get_perfil_display()
        return "Sin perfil de tienda"

    def save_model(self, request, obj, form, change):
        # Django Admin guarda usuario y perfil dentro de la misma transacción.
        super().save_model(request, obj, form, change)
        perfil = form.cleaned_data.get("perfil")
        if perfil:
            personal, _ = PersonalTienda.objects.get_or_create(
                usuario=obj, defaults={"perfil": perfil},
            )
            personal.perfil = perfil
            personal.full_clean()
            personal.save(update_fields=["perfil"])
