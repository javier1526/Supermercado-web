import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from catalogo.models import PrecioReferencia


ENCABEZADOS = ["codigo_barras", "precio_referencia"]


def leer_precio(texto, linea, precio_maximo):
    texto = texto.strip()
    mensaje = f"Línea {linea}: el precio debe ser un entero positivo en CLP."
    mensaje_rango = (
        f"Línea {linea}: el precio excede el máximo de {precio_maximo}."
    )
    if not texto.isascii() or not texto.isdecimal():
        raise CommandError(mensaje)
    try:
        precio = int(texto)
    except ValueError as error:
        raise CommandError(mensaje_rango) from error
    if precio == 0:
        raise CommandError(mensaje)
    if precio > precio_maximo:
        raise CommandError(mensaje_rango)
    return precio


def leer_referencias(ruta):
    campo_codigo = PrecioReferencia._meta.get_field("codigo_barras")
    campo_precio = PrecioReferencia._meta.get_field("precio_referencia")
    _, precio_maximo = connection.ops.integer_field_range(
        campo_precio.get_internal_type(),
    )
    referencias = {}
    try:
        with ruta.open(encoding="utf-8-sig", newline="") as archivo:
            lector = csv.reader(archivo, strict=True)
            if next(lector, None) != ENCABEZADOS:
                raise CommandError(
                    "El encabezado debe ser codigo_barras,precio_referencia."
                )
            for fila in lector:
                linea = lector.line_num
                if len(fila) != len(ENCABEZADOS):
                    raise CommandError(
                        f"Línea {linea}: se esperan exactamente dos columnas."
                    )
                codigo_barras = fila[0].strip()
                if not codigo_barras:
                    raise CommandError(
                        f"Línea {linea}: el código de barras está vacío."
                    )
                if len(codigo_barras) > campo_codigo.max_length:
                    raise CommandError(
                        f"Línea {linea}: el código excede "
                        f"{campo_codigo.max_length} caracteres."
                    )
                if codigo_barras in referencias:
                    raise CommandError(
                        f"Línea {linea}: el código de barras se repite "
                        "en el archivo."
                    )
                referencias[codigo_barras] = leer_precio(
                    fila[1], linea, precio_maximo,
                )
    except (OSError, UnicodeError) as error:
        raise CommandError(
            f"No se pudo leer «{ruta}» como un archivo CSV en UTF-8."
        ) from error
    except csv.Error as error:
        raise CommandError(
            f"El CSV tiene un formato inválido cerca de la línea "
            f"{lector.line_num}."
        ) from error
    if not referencias:
        raise CommandError("El CSV debe contener al menos una referencia.")
    return referencias


class Command(BaseCommand):
    help = "Importa precios de referencia desde un archivo CSV."

    def add_arguments(self, parser):
        parser.add_argument("archivo", help="Ruta del archivo CSV.")

    def handle(self, *args, **options):
        referencias = leer_referencias(Path(options["archivo"]))
        creadas = 0
        try:
            with transaction.atomic():
                for codigo_barras in sorted(referencias):
                    _, creada = PrecioReferencia.objects.update_or_create(
                        codigo_barras=codigo_barras,
                        defaults={
                            "precio_referencia": referencias[codigo_barras],
                        },
                    )
                    creadas += int(creada)
        except DatabaseError as error:
            raise CommandError(
                "MySQL no pudo guardar la carga. Revisa la conexión y los "
                "permisos de la base de datos."
            ) from error

        existentes = len(referencias) - creadas
        self.stdout.write(self.style.SUCCESS(
            "Importación completada. "
            f"Referencias nuevas ({creadas}) y existentes ({existentes})."
        ))
