from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from personal.models import PerfilPersonal, PersonalTienda

from .models import (
    Categoria,
    EstadoProducto,
    Pasillo,
    PrecioReferencia,
    Producto,
    Supermercado,
    UbicacionProducto,
)


class CatalogoPruebas(TestCase):
    fixtures = ["datos_minimos_k02"]

    @classmethod
    def setUpTestData(cls):
        cls.categoria = Categoria.objects.get(pk=1)
        cls.supermercado = Supermercado.objects.get(pk=1)
        cls.producto = Producto.objects.create(
            codigo_barras="0000000000001",
            nombre_producto="  Té negro 100 g  ",
            marca=" Marca de prueba ",
            categoria=cls.categoria,
        )

    def test_producto_pendiente_sin_referencia_conserva_ceros(self):
        producto = Producto.objects.get(codigo_barras="0000000000001")
        self.assertEqual(producto.codigo_barras, "0000000000001")
        self.assertIsInstance(producto.id_producto, int)
        self.assertEqual(producto.categoria, self.categoria)
        self.assertEqual(producto.estado, EstadoProducto.PENDIENTE)
        self.assertEqual(producto.stock, 0)
        self.assertIsNone(producto.precio_normal)
        self.assertFalse(
            PrecioReferencia.objects.filter(
                codigo_barras=producto.codigo_barras,
            ).exists()
        )

    def test_codigo_duplicado_se_rechaza_aunque_producto_este_inactivo(self):
        Producto.objects.filter(pk=self.producto.pk).update(
            estado=EstadoProducto.INACTIVO, precio_normal=1200,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Producto.objects.create(
                codigo_barras=self.producto.codigo_barras,
                nombre_producto="Otra presentación",
                marca="Otra marca",
                categoria=self.categoria,
            )

    def test_validacion_exige_nombre_marca_categoria_y_codigo(self):
        with self.assertRaises(ValidationError) as error:
            Producto().full_clean()
        self.assertTrue(
            {"nombre_producto", "marca", "categoria", "codigo_barras"}
            <= error.exception.message_dict.keys()
        )

    def test_mysql_rechaza_stock_precio_y_estado_invalidos(self):
        casos_invalidos = (
            {"stock": -1},
            {"estado": "eliminado"},
            {"estado": "ACTIVO", "precio_normal": 1200},
            {"precio_normal": 0},
            {"precio_normal": -1},
            {"estado": EstadoProducto.ACTIVO},
            {"codigo_barras": ""},
        )
        for cambios in casos_invalidos:
            with self.subTest(cambios=cambios):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Producto.objects.filter(pk=self.producto.pk).update(
                        **cambios,
                    )

    def test_mysql_acepta_activo_con_precio_entero_positivo(self):
        Producto.objects.filter(pk=self.producto.pk).update(
            estado=EstadoProducto.ACTIVO, precio_normal=1200,
        )
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.precio_normal, 1200)
        self.assertEqual(self.producto.estado, EstadoProducto.ACTIVO)

    def test_duplicidad_de_nombre_y_marca_usa_comparacion_exacta(self):
        existe = Producto.existe_producto_con_nombre_y_marca
        self.assertTrue(existe(" TÉ NEGRO 100 G ", "MARCA DE PRUEBA"))
        self.assertFalse(existe("Te negro 100 g", "Marca de prueba"))
        self.assertFalse(existe("Té  negro 100 g", "Marca de prueba"))
        self.assertFalse(existe("Té negro 200 g", "Marca de prueba"))
        self.assertFalse(existe("Té negro 100 g", "Otra marca"))

    def test_nombre_y_marca_solo_bloquean_pendientes_y_activos(self):
        for estado, esperado in (
            (EstadoProducto.ACTIVO, True),
            (EstadoProducto.RECHAZADO, False),
            (EstadoProducto.INACTIVO, False),
        ):
            with self.subTest(estado=estado):
                precio = None if estado == EstadoProducto.RECHAZADO else 1200
                Producto.objects.filter(pk=self.producto.pk).update(
                    estado=estado, precio_normal=precio,
                )
                self.assertEqual(
                    Producto.existe_producto_con_nombre_y_marca(
                        "Té negro 100 g", "Marca de prueba",
                    ),
                    esperado,
                )

    def test_numero_pasillo_unico_por_supermercado(self):
        self.assertEqual(self.supermercado.pasillo_set.count(), 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Pasillo.objects.create(
                numero_pasillo=1, supermercado=self.supermercado,
            )
        otro_supermercado = Supermercado.objects.create()
        Pasillo.objects.create(
            numero_pasillo=1, supermercado=otro_supermercado,
        )

    def test_una_ubicacion_por_producto_con_datos_opcionales(self):
        ubicacion = UbicacionProducto.objects.create(producto=self.producto)
        self.assertIsNone(ubicacion.pasillo_id)
        self.assertFalse(ubicacion.fotografia_referencial)
        self.assertIsNone(ubicacion.fecha_ultima_actualizacion)
        self.assertIsNone(ubicacion.ultimo_responsable_id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            UbicacionProducto.objects.create(producto=self.producto)

    def test_fecha_y_responsable_de_ubicacion_se_guardan_juntos(self):
        usuario = get_user_model().objects.create_user(
            username="12345678-9", first_name="Camila",
        )
        personal = PersonalTienda.objects.create(
            usuario=usuario, perfil=PerfilPersonal.REPONEDOR,
        )
        fecha = timezone.now()
        for datos in (
            {"fecha_ultima_actualizacion": fecha},
            {"ultimo_responsable": personal},
        ):
            with self.subTest(datos=datos):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    UbicacionProducto.objects.create(
                        producto=self.producto, **datos,
                    )

        pasillo = Pasillo.objects.get(pk=1)
        ubicacion = UbicacionProducto.objects.create(
            producto=self.producto,
            pasillo=pasillo,
            fecha_ultima_actualizacion=fecha,
            ultimo_responsable=personal,
        )
        ubicacion.refresh_from_db()
        self.assertEqual(ubicacion.pasillo, pasillo)
        self.assertEqual(ubicacion.ultimo_responsable, personal)
        self.assertEqual(ubicacion.fecha_ultima_actualizacion, fecha)

    def test_precio_de_referencia_positivo_y_codigo_exacto(self):
        referencia = PrecioReferencia.objects.create(
            codigo_barras="000AB", precio_referencia=1250,
        )
        self.assertEqual(PrecioReferencia.objects.get(pk="000AB"), referencia)
        self.assertFalse(PrecioReferencia.objects.filter(pk="000ab").exists())
        for codigo, precio in (("000AB", 1500), ("00002", 0), ("00003", -1)):
            with self.subTest(codigo=codigo, precio=precio):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    PrecioReferencia.objects.create(
                        codigo_barras=codigo, precio_referencia=precio,
                    )
