from django.db.models import Q

from .models import EstadoProducto, Producto


ORDENES_PRODUCTOS = {
    "nombre": ("nombre_producto", "pk"),
    "precio": ("precio_normal", "nombre_producto", "pk"),
}
CAMPOS_TARJETA = (
    "nombre_producto", "marca", "precio_normal", "stock",
    "fotografia_comercial", "categoria",
)


def obtener_orden(valor):
    return valor if valor in ORDENES_PRODUCTOS else "nombre"


def ordenar_productos(productos, orden):
    return productos.order_by(*ORDENES_PRODUCTOS[obtener_orden(orden)])


def buscar_productos_activos(consulta):
    productos = Producto.objects.filter(estado=EstadoProducto.ACTIVO)
    if consulta:
        productos = productos.filter(
            Q(nombre_producto__icontains=consulta)
            | Q(marca__icontains=consulta),
        )
    return productos
