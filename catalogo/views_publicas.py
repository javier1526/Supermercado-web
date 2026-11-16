from urllib.parse import urlencode

from django.db.models import Prefetch
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .consultas import (
    CAMPOS_TARJETA, buscar_productos_activos, obtener_orden, ordenar_productos,
)
from .models import (
    Categoria, EstadoProducto, Pasillo, Producto, Supermercado,
    UbicacionProducto,
)
from .planos import obtener_plano_disponible


@never_cache
@require_GET
def buscar_productos(request):
    texto = request.GET.get("consulta", "")
    consulta = texto.strip()
    enviada = "consulta" in request.GET
    orden = obtener_orden(request.GET.get("orden"))
    productos = []
    categorias = []
    mensaje = ""
    if enviada:
        if consulta:
            productos = _preparar_tarjetas(
                ordenar_productos(buscar_productos_activos(consulta), orden)
                .only(*CAMPOS_TARJETA),
            )
            if not productos:
                mensaje = "No se encontraron productos"
        else:
            mensaje = "Ingresa un nombre o marca para buscar."
    else:
        categorias = list(Categoria.objects.order_by(
            "nombre", "pk",
        ).prefetch_related(
            Prefetch(
                "producto_set",
                queryset=ordenar_productos(
                    buscar_productos_activos(""), "nombre",
                ).only(*CAMPOS_TARJETA)[:4],
                to_attr="productos_inicio",
            ),
        ))
        for categoria in categorias:
            _preparar_tarjetas(categoria.productos_inicio)
    return render(request, "catalogo/busqueda_publica.html", {
        "consulta": texto, "enviada": enviada,
        "productos": productos, "mensaje": mensaje,
        "categorias": categorias, "orden": orden,
        "parametros_tarjetas": _parametros_listado(orden, consulta=texto),
    })


@never_cache
@require_GET
def listar_categorias(request):
    return render(request, "catalogo/categorias_publicas.html", {
        "categorias": Categoria.objects.order_by("nombre", "pk"),
    })


@never_cache
@require_GET
def productos_categoria(request, categoria_id):
    categoria = get_object_or_404(Categoria, pk=categoria_id)
    orden = obtener_orden(request.GET.get("orden"))
    productos = _preparar_tarjetas(ordenar_productos(
        buscar_productos_activos("").filter(categoria=categoria), orden,
    ).only(*CAMPOS_TARJETA))
    return render(request, "catalogo/productos_categoria.html", {
        "categoria": categoria, "productos": productos, "orden": orden,
        "parametros_tarjetas": _parametros_listado(
            orden, categoria_id=categoria.pk,
        ),
    })


@never_cache
@require_GET
def ubicacion_general(request):
    return render(request, "catalogo/ubicacion_general.html")


@never_cache
@require_GET
def plano_general(request):
    supermercado = Supermercado.objects.order_by("pk").first()
    return render(request, "catalogo/plano_general.html", {
        "plano": obtener_plano_disponible(supermercado),
    })


@never_cache
@require_GET
def listar_pasillos(request):
    supermercado = Supermercado.objects.order_by("pk").first()
    pasillos = (
        Pasillo.objects.filter(supermercado=supermercado)
        .order_by("numero_pasillo", "pk")
        if supermercado else Pasillo.objects.none()
    )
    return render(request, "catalogo/pasillos_publicos.html", {
        "pasillos": pasillos,
    })


@never_cache
@require_GET
def productos_pasillo(request, pasillo_id):
    supermercado = Supermercado.objects.order_by("pk").first()
    if not supermercado:
        raise Http404
    pasillo = get_object_or_404(
        Pasillo, pk=pasillo_id, supermercado=supermercado,
    )
    productos = _preparar_tarjetas(ordenar_productos(
        buscar_productos_activos("").filter(
            ubicacionproducto__pasillo=pasillo,
        ), "nombre",
    ).only(*CAMPOS_TARJETA))
    return render(request, "catalogo/productos_pasillo.html", {
        "pasillo": pasillo, "productos": productos,
        "parametros_tarjetas": urlencode({"pasillo": pasillo.pk}),
    })


def _parametros_listado(orden, consulta=None, categoria_id=None):
    parametros = {}
    if categoria_id is not None:
        parametros["categoria"] = categoria_id
    elif consulta is not None:
        parametros["consulta"] = consulta
    if orden == "precio":
        parametros["orden"] = orden
    return urlencode(parametros)


def _preparar_tarjetas(productos):
    productos = list(productos)
    for producto in productos:
        producto.fotografia_disponible = _fotografia_disponible(
            producto.fotografia_comercial,
        )
    return productos


@never_cache
@require_GET
def sugerir_productos(request):
    consulta = request.GET.get("consulta", "").strip()
    productos = []
    if consulta:
        productos = list(
            buscar_productos_activos(consulta).order_by("pk")
            .values("nombre_producto", "marca")[:3]
        )
    return JsonResponse({"productos": productos})


@never_cache
@require_GET
def ficha_producto(request, producto_id):
    producto = get_object_or_404(
        Producto.objects.select_related("ubicacionproducto__pasillo").only(
            "nombre_producto", "marca", "precio_normal", "stock",
            "descripcion", "fotografia_comercial",
            "ubicacionproducto__producto",
            "ubicacionproducto__pasillo__numero_pasillo",
            "ubicacionproducto__fotografia_referencial",
            "ubicacionproducto__fecha_ultima_actualizacion",
        ),
        pk=producto_id, estado=EstadoProducto.ACTIVO,
    )
    try:
        ubicacion = producto.ubicacionproducto
    except UbicacionProducto.DoesNotExist:
        ubicacion = None
    orden = obtener_orden(request.GET.get("orden"))
    if "pasillo" in request.GET:
        pasillo_id = request.GET["pasillo"].lstrip("0") or "0"
        if (
            not pasillo_id.isascii() or not pasillo_id.isdecimal()
            or len(pasillo_id) > 19
        ):
            raise Http404
        supermercado = Supermercado.objects.order_by("pk").first()
        if not supermercado:
            raise Http404
        pasillo = get_object_or_404(
            Pasillo, pk=pasillo_id, supermercado=supermercado,
        )
        volver_a = reverse("cliente:productos_pasillo", args=[pasillo.pk])
        parametros = ""
        texto_regreso = "Volver al pasillo"
    elif "categoria" in request.GET:
        categoria_id = request.GET["categoria"].lstrip("0") or "0"
        if (
            not categoria_id.isascii() or not categoria_id.isdecimal()
            or len(categoria_id) > 19
        ):
            raise Http404
        categoria = get_object_or_404(Categoria, pk=categoria_id)
        volver_a = reverse("cliente:productos_categoria", args=[categoria.pk])
        parametros = (
            urlencode({"orden": "precio"}) if orden == "precio" else ""
        )
        texto_regreso = "Volver a categoría"
    else:
        volver_a = reverse("cliente:inicio")
        parametros = _parametros_listado(
            orden, consulta=request.GET.get("consulta")
            if "consulta" in request.GET else None,
        )
        texto_regreso = (
            "Volver a resultados" if "consulta" in request.GET
            else "Volver al buscador"
        )
    if parametros:
        volver_a += "?" + parametros
    return render(request, "catalogo/ficha_publica.html", {
        "producto": producto,
        "fotografia_disponible": _fotografia_disponible(
            producto.fotografia_comercial,
        ),
        "ubicacion": ubicacion,
        "fotografia_ubicacion_disponible": _fotografia_disponible(
            ubicacion.fotografia_referencial if ubicacion else None,
        ),
        "consulta": request.GET.get("consulta", ""),
        "conservar_consulta": "consulta" in request.GET,
        "volver_a": volver_a, "texto_regreso": texto_regreso,
    })


@never_cache
@require_GET
def fotografia_producto(request, producto_id):
    producto = get_object_or_404(
        Producto.objects.only("fotografia_comercial"),
        pk=producto_id, estado=EstadoProducto.ACTIVO,
    )
    return _responder_fotografia(producto.fotografia_comercial)


@never_cache
@require_GET
def fotografia_ubicacion(request, producto_id):
    ubicacion = get_object_or_404(
        UbicacionProducto.objects.only("fotografia_referencial"),
        producto_id=producto_id, producto__estado=EstadoProducto.ACTIVO,
    )
    return _responder_fotografia(ubicacion.fotografia_referencial)


def _responder_fotografia(fotografia):
    if not fotografia:
        raise Http404
    try:
        archivo = fotografia.open("rb")
    except OSError as error:
        raise Http404 from error
    respuesta = FileResponse(archivo)
    respuesta["Content-Security-Policy"] = "sandbox"
    return respuesta


def _fotografia_disponible(fotografia):
    if not fotografia:
        return False
    try:
        return fotografia.storage.exists(fotografia.name)
    except OSError:
        return False
