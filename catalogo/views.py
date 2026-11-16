from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import (
    require_GET, require_http_methods, require_POST,
)

from personal.models import PerfilPersonal
from personal.permisos import requiere_perfil

from .consultas import buscar_productos_activos
from .forms import (
    FormularioAprobacion, FormularioRegistroProducto, FormularioStock,
    FormularioUbicacion, FormularioPrecioNormal,
)
from .models import (
    EstadoProducto, PrecioReferencia, Producto, UbicacionProducto,
)
from .registro import guardar_producto
from .ubicacion import guardar_ubicacion


@requiere_perfil(PerfilPersonal.REPONEDOR)
@require_http_methods(["GET", "POST"])
@csrf_protect
def registrar_producto(request):
    if request.method == "POST":
        formulario = FormularioRegistroProducto(request.POST, request.FILES)
        if formulario.is_valid():
            try:
                guardar_producto(formulario)
            except ValidationError as error:
                formulario.add_error(None, error)
            else:
                messages.success(
                    request,
                    "Producto registrado. Quedó pendiente de aprobación.",
                )
                return redirect("personal:panel_reponedor")
    else:
        formulario = FormularioRegistroProducto()

    return render(request, "catalogo/registrar_producto.html", {
        "formulario": formulario,
        "volver_a_adjuntar": bool(request.FILES),
    })


@requiere_perfil(PerfilPersonal.JEFATURA)
@require_GET
def listar_pendientes(request):
    productos = (
        Producto.objects.filter(estado=EstadoProducto.PENDIENTE)
        .select_related("categoria")
        .order_by("id_producto")
    )
    return render(request, "catalogo/pendientes.html", {
        "productos": productos,
    })


@requiere_perfil(PerfilPersonal.JEFATURA)
@require_GET
def revisar_producto(request, producto_id):
    producto = get_object_or_404(
        Producto.objects.select_related("categoria"), pk=producto_id,
    )
    if producto.estado != EstadoProducto.PENDIENTE:
        return _solicitud_procesada(request)

    precio_referencia = (
        PrecioReferencia.objects.filter(codigo_barras=producto.codigo_barras)
        .values_list("precio_referencia", flat=True)
        .first()
    )
    formulario = FormularioAprobacion(
        initial={"precio_inicial": precio_referencia},
    )
    return render(request, "catalogo/revisar_producto.html", {
        "producto": producto,
        "formulario": formulario,
    })


@requiere_perfil(PerfilPersonal.JEFATURA)
@require_GET
def foto_producto_pendiente(request, producto_id):
    producto = get_object_or_404(
        Producto, pk=producto_id, estado=EstadoProducto.PENDIENTE,
    )
    if not producto.fotografia_comercial:
        raise Http404
    try:
        archivo = producto.fotografia_comercial.open("rb")
    except FileNotFoundError as error:
        raise Http404 from error
    respuesta = FileResponse(archivo)
    respuesta["Content-Security-Policy"] = "sandbox"
    return respuesta


@requiere_perfil(PerfilPersonal.JEFATURA)
@require_POST
@csrf_protect
def aprobar_producto(request, producto_id):
    return _procesar_revision(
        request, producto_id, FormularioAprobacion(request.POST),
    )


@requiere_perfil(PerfilPersonal.JEFATURA)
@require_POST
@csrf_protect
def rechazar_producto(request, producto_id):
    return _procesar_revision(request, producto_id, None)


def _procesar_revision(request, producto_id, formulario):
    with transaction.atomic():
        producto = get_object_or_404(
            Producto.objects.select_for_update(), pk=producto_id,
        )
        if producto.estado != EstadoProducto.PENDIENTE:
            return _solicitud_procesada(request)

        if formulario is None:
            producto.estado = EstadoProducto.RECHAZADO
            producto.save(update_fields=["estado"])
            mensaje = "Producto rechazado."
        elif formulario.is_valid():
            producto.precio_normal = formulario.cleaned_data["precio_inicial"]
            producto.estado = EstadoProducto.ACTIVO
            producto.save(update_fields=["precio_normal", "estado"])
            mensaje = "Producto aprobado. Quedó activo."
        else:
            mensaje = None

    if mensaje:
        messages.success(request, mensaje)
        return redirect("catalogo:pendientes")
    return render(request, "catalogo/revisar_producto.html", {
        "producto": producto,
        "formulario": formulario,
    })


def _solicitud_procesada(request):
    messages.warning(request, "Esta solicitud ya fue procesada.")
    return redirect("catalogo:pendientes")


@requiere_perfil(PerfilPersonal.REPONEDOR, PerfilPersonal.JEFATURA)
@require_GET
def seleccionar_producto_stock(request):
    consulta = request.GET.get("consulta", "").strip()
    productos = buscar_productos_activos(consulta)
    return render(request, "catalogo/seleccionar_stock.html", {
        "productos": productos.order_by("nombre_producto", "id_producto"),
        "consulta": consulta,
    })


@requiere_perfil(PerfilPersonal.REPONEDOR, PerfilPersonal.JEFATURA)
@require_http_methods(["GET", "POST"])
@csrf_protect
def actualizar_stock(request, producto_id):
    if request.method == "POST":
        formulario = FormularioStock(request.POST)
        with transaction.atomic():
            producto = get_object_or_404(
                Producto.objects.select_for_update(), pk=producto_id,
            )
            if producto.estado != EstadoProducto.ACTIVO:
                return _stock_no_disponible(request)
            if formulario.is_valid():
                producto.stock = formulario.cleaned_data["stock"]
                producto.save(update_fields=["stock"])
                messages.success(request, "Stock actualizado correctamente.")
                return redirect("catalogo:actualizar_stock", producto_id)
    else:
        producto = get_object_or_404(Producto, pk=producto_id)
        if producto.estado != EstadoProducto.ACTIVO:
            return _stock_no_disponible(request)
        formulario = FormularioStock(initial={"stock": producto.stock})
    return render(request, "catalogo/actualizar_stock.html", {
        "producto": producto, "formulario": formulario,
    })


def _stock_no_disponible(request):
    messages.warning(
        request, "El producto ya no está activo. No se modificó su stock.",
    )
    return redirect("catalogo:seleccionar_stock")


@requiere_perfil(PerfilPersonal.REPONEDOR)
@require_GET
def seleccionar_producto_ubicacion(request):
    consulta = request.GET.get("consulta", "").strip()
    productos = buscar_productos_activos(consulta)
    return render(request, "catalogo/seleccionar_ubicacion.html", {
        "productos": productos.order_by("nombre_producto", "id_producto"),
        "consulta": consulta,
    })


@requiere_perfil(PerfilPersonal.REPONEDOR)
@require_http_methods(["GET", "POST"])
@csrf_protect
def actualizar_ubicacion(request, producto_id):
    if request.method == "POST":
        try:
            producto, ubicacion, formulario, cambio = guardar_ubicacion(
                producto_id, request.POST, request.FILES,
                request.user.personaltienda,
            )
        except ValidationError:
            return _ubicacion_no_disponible(request)
        except (DatabaseError, OSError):
            formulario = FormularioUbicacion(request.POST, request.FILES)
            formulario.is_valid()
            formulario.add_error(
                None, "No se pudo guardar la ubicación. Intenta nuevamente.",
            )
        else:
            if formulario.is_valid():
                mensaje = (
                    "Ubicación actualizada correctamente." if cambio
                    else "No hubo cambios en la ubicación."
                )
                messages.success(request, mensaje)
                return redirect("catalogo:actualizar_ubicacion", producto_id)
    producto = get_object_or_404(Producto, pk=producto_id)
    if producto.estado != EstadoProducto.ACTIVO:
        return _ubicacion_no_disponible(request)
    ubicacion = (
        UbicacionProducto.objects.select_related("pasillo")
        .filter(producto=producto).first()
    )
    if request.method == "GET":
        formulario = FormularioUbicacion(initial={
            "pasillo": ubicacion.pasillo_id if ubicacion else None,
        })
    return render(request, "catalogo/actualizar_ubicacion.html", {
        "producto": producto, "ubicacion": ubicacion,
        "formulario": formulario, "volver_a_adjuntar": bool(request.FILES),
    })


def _ubicacion_no_disponible(request):
    messages.warning(
        request, "El producto ya no está activo. No se modificó su ubicación.",
    )
    return redirect("catalogo:seleccionar_ubicacion")


@requiere_perfil(PerfilPersonal.REPONEDOR)
@require_GET
def foto_ubicacion(request, producto_id):
    ubicacion = get_object_or_404(
        UbicacionProducto, producto_id=producto_id,
        producto__estado=EstadoProducto.ACTIVO,
    )
    if not ubicacion.fotografia_referencial:
        raise Http404
    try:
        archivo = ubicacion.fotografia_referencial.open("rb")
    except FileNotFoundError as error:
        raise Http404 from error
    respuesta = FileResponse(archivo)
    respuesta["Content-Security-Policy"] = "sandbox"
    return respuesta


@requiere_perfil(PerfilPersonal.REPONEDOR, PerfilPersonal.JEFATURA)
@require_GET
def seleccionar_ultima_actualizacion(request):
    consulta = request.GET.get("consulta", "").strip()
    productos = buscar_productos_activos(consulta)
    return render(request, "catalogo/seleccionar_stock.html", {
        "productos": productos.order_by("nombre_producto", "id_producto"),
        "consulta": consulta,
        "titulo": "Última actualización",
        "descripcion": (
            "Selecciona un producto activo para consultar su ubicación."
        ),
        "ruta_detalle": "catalogo:ultima_actualizacion",
        "consulta_actualizacion": True,
    })


@requiere_perfil(PerfilPersonal.REPONEDOR, PerfilPersonal.JEFATURA)
@require_GET
def consultar_ultima_actualizacion(request, producto_id):
    producto = get_object_or_404(
        Producto, pk=producto_id, estado=EstadoProducto.ACTIVO,
    )
    ubicacion = (
        UbicacionProducto.objects.select_related(
            "pasillo", "ultimo_responsable__usuario",
        ).filter(producto=producto).first()
    )
    return render(request, "catalogo/ultima_actualizacion.html", {
        "producto": producto, "ubicacion": ubicacion,
    })


@requiere_perfil(PerfilPersonal.JEFATURA)
@require_GET
def seleccionar_producto_gestion(request):
    consulta = request.GET.get("consulta", "").strip()
    return render(request, "catalogo/seleccionar_stock.html", {
        "productos": buscar_productos_activos(consulta).order_by(
            "nombre_producto", "id_producto",
        ),
        "consulta": consulta, "titulo": "Gestión desde la ficha",
        "descripcion": (
            "Selecciona un producto activo para gestionar su precio o estado."
        ),
        "ruta_detalle": "catalogo:ficha_gestion",
        "consulta_actualizacion": True,
    })


@requiere_perfil(PerfilPersonal.JEFATURA)
@require_GET
def ficha_gestion(request, producto_id):
    producto = get_object_or_404(
        Producto.objects.select_related("categoria"), pk=producto_id,
        estado__in=[EstadoProducto.ACTIVO, EstadoProducto.INACTIVO],
    )
    return _mostrar_ficha_gestion(request, producto)


def _mostrar_ficha_gestion(request, producto, formulario=None):
    if formulario is None:
        formulario = FormularioPrecioNormal(initial={
            "precio_normal": producto.precio_normal,
        })
    return render(request, "catalogo/ficha_gestion.html", {
        "producto": producto, "formulario": formulario,
        "editable": producto.estado == EstadoProducto.ACTIVO,
    })


@requiere_perfil(PerfilPersonal.JEFATURA)
@require_POST
@csrf_protect
def actualizar_precio(request, producto_id):
    return _guardar_gestion(request, producto_id, desactivar=False)


@requiere_perfil(PerfilPersonal.JEFATURA)
@require_POST
@csrf_protect
def desactivar_producto(request, producto_id):
    return _guardar_gestion(request, producto_id, desactivar=True)


def _guardar_gestion(request, producto_id, desactivar):
    formulario = FormularioPrecioNormal(request.POST)
    try:
        with transaction.atomic():
            producto = get_object_or_404(
                Producto.objects.select_for_update(), pk=producto_id,
            )
            if producto.estado != EstadoProducto.ACTIVO:
                messages.warning(
                    request, "El producto ya no está activo. No se modificó.",
                )
                if producto.estado == EstadoProducto.INACTIVO:
                    return redirect("catalogo:ficha_gestion", producto_id)
                return redirect("catalogo:seleccionar_gestion")
            if desactivar:
                if request.POST.get("confirmar") != "si":
                    messages.warning(
                        request, "Debes confirmar la desactivación.",
                    )
                    return redirect("catalogo:ficha_gestion", producto_id)
                producto.estado = EstadoProducto.INACTIVO
                producto.save(update_fields=["estado"])
            elif formulario.is_valid():
                producto.precio_normal = (
                    formulario.cleaned_data["precio_normal"]
                )
                producto.save(update_fields=["precio_normal"])
            else:
                return _mostrar_ficha_gestion(request, producto, formulario)
    except DatabaseError:
        producto = get_object_or_404(Producto, pk=producto_id)
        formulario.is_valid()
        formulario.add_error(None, "No se pudo guardar. Intenta nuevamente.")
        return _mostrar_ficha_gestion(request, producto, formulario)
    messages.success(request, (
        "Producto desactivado. Su registro se conserva." if desactivar
        else "Precio normal actualizado correctamente."
    ))
    return redirect("catalogo:ficha_gestion", producto_id)
