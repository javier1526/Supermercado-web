from pathlib import Path
from uuid import uuid4

from MySQLdb.constants.ER import DUP_ENTRY

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from .forms import MENSAJE_CODIGO_DUPLICADO
from .models import EstadoProducto, Producto


def guardar_producto(formulario):
    producto = formulario.save(commit=False)
    producto.estado = EstadoProducto.PENDIENTE
    producto.precio_normal = None
    producto.stock = 0
    fotografia = formulario.cleaned_data["fotografia_comercial"]
    producto.fotografia_comercial = None

    # La consulta por nombre y marca debe preceder al guardado sin carreras.
    bloqueo = f"{connection.settings_dict['NAME']}.registro_producto"
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(%s, %s)", (bloqueo, 5))
        if cursor.fetchone()[0] != 1:
            raise ValidationError("Intenta registrar el producto nuevamente.")
        try:
            if Producto.existe_producto_con_nombre_y_marca(
                producto.nombre_producto, producto.marca,
            ):
                raise ValidationError({
                    "nombre_producto": (
                        "Ya existe un producto pendiente o activo con este "
                        "nombre completo y marca."
                    ),
                })
            _persistir_producto(producto, fotografia)
        finally:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (bloqueo,))


def _persistir_producto(producto, fotografia):
    archivo_nuevo = None
    almacenamiento = producto.fotografia_comercial.storage
    try:
        with transaction.atomic():
            producto.save(force_insert=True)
            if fotografia:
                nombre = f"{uuid4().hex}{Path(fotografia.name).suffix}"
                archivo_nuevo = (
                    producto.fotografia_comercial.field
                    .generate_filename(producto, nombre)
                )
                producto.fotografia_comercial.save(
                    nombre, fotografia, save=False,
                )
                archivo_nuevo = producto.fotografia_comercial.name
                producto.save(update_fields=["fotografia_comercial"])
    except Exception as error:
        # MySQL revierte la fila, pero el archivo requiere una limpieza aparte.
        if archivo_nuevo:
            almacenamiento.delete(archivo_nuevo)
        if (
            isinstance(error, IntegrityError)
            and error.args[0] == DUP_ENTRY
            and Producto.objects.filter(codigo_barras=producto.codigo_barras)
            .exists()
        ):
            raise ValidationError({
                "codigo_barras": MENSAJE_CODIGO_DUPLICADO,
            }) from error
        if isinstance(error, OSError):
            raise ValidationError(
                "No se pudo guardar la fotografía.",
            ) from error
        raise
