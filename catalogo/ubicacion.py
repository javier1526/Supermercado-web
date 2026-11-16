from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .forms import FormularioUbicacion
from .models import EstadoProducto, Producto, UbicacionProducto


def fotografias_iguales(anterior, nueva):
    if not anterior:
        return False
    try:
        with anterior.storage.open(anterior.name, "rb") as archivo:
            huella = sha256()
            for bloque in iter(lambda: archivo.read(65536), b""):
                huella.update(bloque)
    except FileNotFoundError:
        return False
    nueva.seek(0)
    huella_nueva = sha256()
    for bloque in nueva.chunks():
        huella_nueva.update(bloque)
    nueva.seek(0)
    return huella.digest() == huella_nueva.digest()


def guardar_ubicacion(producto_id, datos, archivos, responsable):
    archivo_nuevo = None
    almacenamiento = None
    try:
        with transaction.atomic():
            # Bloquear el producto serializa la creación de su ubicación.
            producto = get_object_or_404(
                Producto.objects.select_for_update(), pk=producto_id,
            )
            if producto.estado != EstadoProducto.ACTIVO:
                raise ValidationError(
                    "El producto ya no está activo.",
                    code="producto_no_activo",
                )
            ubicacion = (
                UbicacionProducto.objects.select_for_update()
                .filter(producto=producto).first()
            ) or UbicacionProducto(producto=producto)
            datos = datos.copy()
            if "pasillo" not in datos:
                datos["pasillo"] = ubicacion.pasillo_id or ""
            formulario = FormularioUbicacion(datos, archivos)
            cambio = False
            if formulario.is_valid():
                pasillo = formulario.cleaned_data["pasillo"]
                fotografia = formulario.cleaned_data["fotografia_referencial"]
                cambia_pasillo = ubicacion.pasillo_id != (
                    pasillo.pk if pasillo else None
                )
                cambia_foto = bool(fotografia) and not fotografias_iguales(
                    ubicacion.fotografia_referencial, fotografia,
                )
                cambio = cambia_pasillo or cambia_foto
                if cambio:
                    anterior = ubicacion.fotografia_referencial.name
                    almacenamiento = (
                        ubicacion.fotografia_referencial.storage
                    )
                    if cambia_foto:
                        # Permite limpiar el archivo si falla su escritura.
                        nombre = f"{uuid4().hex}{Path(fotografia.name).suffix}"
                        archivo_nuevo = (
                            ubicacion.fotografia_referencial.field
                            .generate_filename(ubicacion, nombre)
                        )
                        ubicacion.fotografia_referencial.save(
                            nombre, fotografia, save=False,
                        )
                        archivo_nuevo = ubicacion.fotografia_referencial.name
                    ubicacion.pasillo = pasillo
                    ubicacion.fecha_ultima_actualizacion = timezone.now()
                    ubicacion.ultimo_responsable = responsable
                    ubicacion.save()
                    if cambia_foto and anterior:
                        transaction.on_commit(
                            lambda: almacenamiento.delete(anterior),
                            robust=True,
                        )
        return producto, ubicacion, formulario, cambio
    except (DatabaseError, OSError):
        # MySQL no revierte archivos: se elimina solo el reemplazo fallido.
        if archivo_nuevo:
            almacenamiento.delete(archivo_nuevo)
        raise
