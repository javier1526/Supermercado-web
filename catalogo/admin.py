from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, transaction
from django.db.models import Subquery
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_GET, require_http_methods

from .formularios_admin import (
    FormularioImportacionPrecios, FormularioPasilloAdministrativo,
    TAMANO_MAXIMO_CSV,
)
from .models import Categoria, Pasillo, PrecioReferencia, Supermercado
from .templatetags.formato_comercial import precio_clp


class AdministracionSoloSuperusuario(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser


@admin.register(Categoria)
class AdministracionCategoria(AdministracionSoloSuperusuario):
    fields = ("nombre",)
    list_display = ("nombre",)
    search_fields = ("nombre",)
    ordering = ("nombre", "pk")


@admin.register(Pasillo)
class AdministracionPasillo(AdministracionSoloSuperusuario):
    form = FormularioPasilloAdministrativo
    list_display = ("numero_pasillo", "supermercado")
    list_select_related = ("supermercado",)
    ordering = ("numero_pasillo", "pk")

    def get_queryset(self, request):
        supermercado = Supermercado.objects.order_by("pk").values("pk")[:1]
        return super().get_queryset(request).filter(
            supermercado_id=Subquery(supermercado),
        )


@admin.register(PrecioReferencia)
class AdministracionPrecioReferencia(AdministracionSoloSuperusuario):
    list_display = ("codigo_barras", "precio_en_clp")
    list_display_links = None
    search_fields = ("codigo_barras",)
    ordering = ("codigo_barras",)
    actions = None
    change_list_template = "admin/catalogo/precioreferencia/listado.html"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(
        description="Precio de referencia (CLP)", ordering="precio_referencia",
    )
    def precio_en_clp(self, referencia):
        return precio_clp(referencia.precio_referencia)

    @method_decorator(require_GET)
    def changelist_view(self, request, extra_context=None):
        contexto = {**(extra_context or {}), "title": "Precios de referencia"}
        return super().changelist_view(request, extra_context=contexto)

    def get_urls(self):
        return [
            path(
                "importar-csv/",
                self.admin_site.admin_view(self.importar_csv),
                name="catalogo_precioreferencia_importar",
            ),
            *super().get_urls(),
        ]

    @method_decorator(require_http_methods(["GET", "POST"]))
    def importar_csv(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        formulario = FormularioImportacionPrecios(
            request.POST if request.method == "POST" else None,
            request.FILES if request.method == "POST" else None,
        )
        if request.method == "POST" and formulario.is_valid():
            salida = StringIO()
            ruta = None
            try:
                # La limpieza temporal termina antes de confirmar la carga.
                with transaction.atomic(), TemporaryDirectory() as directorio:
                    ruta = Path(directorio) / "referencias.csv"
                    with formulario.cleaned_data["archivo_csv"] as archivo:
                        with ruta.open("wb") as destino:
                            tamano = 0
                            for bloque in archivo.chunks():
                                tamano += len(bloque)
                                if tamano > TAMANO_MAXIMO_CSV:
                                    raise CommandError(
                                        "El archivo CSV supera el máximo "
                                        "de 1 MiB."
                                    )
                                destino.write(bloque)
                    call_command(
                        "importar_precios", str(ruta), stdout=salida,
                        no_color=True,
                    )
            except CommandError as error:
                mensaje = str(error).replace(
                    str(ruta), "el archivo seleccionado",
                )
                formulario.add_error("archivo_csv", mensaje)
            except OSError:
                formulario.add_error(
                    "archivo_csv",
                    "No se pudo procesar el archivo temporal. "
                    "Intenta nuevamente.",
                )
            except DatabaseError:
                formulario.add_error(
                    "archivo_csv",
                    "No se pudo completar la importación en MySQL. "
                    "Intenta nuevamente.",
                )
            else:
                self.message_user(
                    request, salida.getvalue().strip(), level=messages.SUCCESS,
                )
                return redirect("admin:catalogo_precioreferencia_changelist")
        return render(
            request, "admin/catalogo/precioreferencia/importar.html", {
                **self.admin_site.each_context(request),
                "opts": self.model._meta,
                "title": "Importar precios de referencia desde CSV",
                "formulario": formulario,
            },
        )
