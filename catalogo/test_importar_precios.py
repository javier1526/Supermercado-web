from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import Categoria, EstadoProducto, PrecioReferencia, Producto


ENCABEZADO = "codigo_barras,precio_referencia\n"
CSV_FICTICIO = (
    settings.BASE_DIR / "ejemplos"
    / "precios_referencia_ficticios.csv"
)


class ImportarPreciosPruebas(TestCase):
    def setUp(self):
        directorio = self.enterContext(TemporaryDirectory())
        self.ruta = Path(directorio) / "precios.csv"

    def cargar(self, contenido=None):
        if contenido is not None:
            self.ruta.write_text(contenido, encoding="utf-8", newline="")
        salida = StringIO()
        call_command("importar_precios", str(self.ruta), stdout=salida)
        return salida.getvalue()

    def referencias(self):
        return dict(PrecioReferencia.objects.values_list(
            "codigo_barras", "precio_referencia",
        ))

    def test_cp023_importa_12_referencias_sin_productos_y_preserva_ceros(self):
        precios = (
            1250, 1690, 2190, 1350, 2490, 990,
            1780, 520, 3490, 1290, 750, 2090,
        )
        esperadas = {
            f"{indice:013d}": precio
            for indice, precio in enumerate(precios, start=1)
        }
        salida = StringIO()
        call_command("importar_precios", str(CSV_FICTICIO), stdout=salida)
        self.assertIn("nuevas (12) y existentes (0)", salida.getvalue())
        self.assertEqual(self.referencias(), esperadas)
        self.assertFalse(Producto.objects.exists())

        salida = StringIO()
        call_command("importar_precios", str(CSV_FICTICIO), stdout=salida)
        self.assertIn("nuevas (0) y existentes (12)", salida.getvalue())
        self.assertEqual(self.referencias(), esperadas)

    def test_cp024_actualiza_referencia_sin_cambiar_precio_activo(self):
        categoria = Categoria.objects.create(nombre="Snacks")
        producto = Producto.objects.create(
            codigo_barras="0000000000001",
            nombre_producto="Producto de prueba",
            marca="Marca de prueba",
            categoria=categoria,
            precio_normal=1390,
            estado=EstadoProducto.ACTIVO,
        )
        self.cargar(ENCABEZADO + "0000000000001,1250\n")
        salida = self.cargar(
            ENCABEZADO + "0000000000001,1490\n0000000000002,1690\n",
        )
        self.assertIn("nuevas (1) y existentes (1)", salida)
        self.assertEqual(
            self.referencias(), {"0000000000001": 1490, "0000000000002": 1690},
        )
        producto.refresh_from_db()
        self.assertEqual(producto.precio_normal, 1390)
        self.assertEqual(producto.estado, EstadoProducto.ACTIVO)
        self.assertEqual(Producto.objects.count(), 1)

    def test_acepta_bom_comillas_y_limites_del_modelo(self):
        codigo = "0" * 31 + "1"
        self.cargar(f'\ufeff{ENCABEZADO}" {codigo} ", 2147483647\r\n')
        self.assertEqual(self.referencias(), {codigo: 2147483647})

    def test_rechaza_encabezados_incorrectos_o_desordenados(self):
        for encabezado in (
            "codigo,precio",
            "precio_referencia,codigo_barras",
            "codigo_barras,precio_referencia,otra",
            "codigo_barras;precio_referencia",
        ):
            with self.subTest(encabezado=encabezado):
                with self.assertRaisesMessage(CommandError, "El encabezado"):
                    self.cargar(encabezado + "\n001,1000\n")
        self.assertEqual(self.referencias(), {})

    def test_rechaza_estructura_invalida_y_archivos_sin_filas(self):
        for contenido, mensaje in (
            (ENCABEZADO, "al menos una referencia"),
            ("", "El encabezado"),
            (ENCABEZADO + "001\n", "dos columnas"),
            (ENCABEZADO + "001,1000,otra\n", "dos columnas"),
            (ENCABEZADO + "001,1000\n\n", "dos columnas"),
            (ENCABEZADO + '"001,1000\n', "formato inválido"),
        ):
            with self.subTest(contenido=contenido):
                with self.assertRaisesMessage(CommandError, mensaje):
                    self.cargar(contenido)
        self.assertEqual(self.referencias(), {})

    def test_rechaza_archivo_ausente_o_codificacion_invalida(self):
        with self.assertRaisesMessage(CommandError, "No se pudo leer"):
            self.cargar()
        self.ruta.write_bytes(b"\xff\xfe")
        with self.assertRaisesMessage(CommandError, "CSV en UTF-8"):
            self.cargar()
        self.assertEqual(self.referencias(), {})

    def test_cp023_archivos_invalidos_no_intentan_guardar_cambios(self):
        self.cargar(ENCABEZADO + "001,1250\n")
        for fila, mensaje in (
            (" 001 ,3000", "se repite"),
            (",1300", "está vacío"),
            ("   ,1300", "está vacío"),
            (f"{'0' * 33},1300", "excede 32 caracteres"),
            ("003,0", "entero positivo"),
            ("003,-2", "entero positivo"),
            ("003,1.5", "entero positivo"),
            ("003,abc", "entero positivo"),
            ("003,", "entero positivo"),
            ("003,2147483648", "excede el máximo"),
            (f"003,{'9' * 4500}", "excede el máximo"),
        ):
            with self.subTest(caso=mensaje):
                with patch.object(
                    PrecioReferencia.objects, "update_or_create",
                ) as guardar:
                    with self.assertRaisesMessage(CommandError, mensaje):
                        self.cargar(
                            ENCABEZADO + f"001,2000\n002,1400\n{fila}\n",
                        )
                    guardar.assert_not_called()
                self.assertEqual(self.referencias(), {"001": 1250})

    def test_error_de_mysql_en_segunda_fila_revierte_la_primera(self):
        self.cargar(ENCABEZADO + "001,1250\n")
        guardar = PrecioReferencia.objects.update_or_create

        def guardar_con_error(codigo_barras, defaults):
            if codigo_barras == "002":
                defaults = {"precio_referencia": -1}
            return guardar(codigo_barras=codigo_barras, defaults=defaults)

        with patch.object(
            PrecioReferencia.objects, "update_or_create",
            side_effect=guardar_con_error,
        ):
            with self.assertRaisesMessage(
                CommandError, "MySQL no pudo guardar",
            ):
                self.cargar(ENCABEZADO + "001,1500\n002,1700\n")
        self.assertEqual(self.referencias(), {"001": 1250})
