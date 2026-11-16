from datetime import datetime, timezone as zona_utc
from itertools import product
from pathlib import Path
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from . import test_ficha_publica, test_ubicacion
from .models import (
    EstadoProducto, PrecioReferencia, Producto, UbicacionProducto,
)


class UbicacionPublicaPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_ficha_publica.FichaPublicaPruebas.setUpTestData.__func__(cls)

    def setUp(self):
        test_ficha_publica.FichaPublicaPruebas.setUp(self)
        self.instante = datetime(2026, 11, 13, 21, 45, tzinfo=zona_utc.utc)
        UbicacionProducto.objects.filter(pk=self.ubicacion.pk).update(
            fecha_ultima_actualizacion=self.instante,
        )
        self.foto_ubicacion = reverse(
            "cliente:fotografia_ubicacion", args=[self.producto.pk],
        )

    def test_ubicacion_completa_y_fotografias_independientes(self):
        with self.assertNumQueries(1):
            respuesta = self.client.get(self.ficha)
        for texto in ("Pasillo 3", "13/11/2026 18:45", "Galletas de arroz"):
            self.assertContains(respuesta, texto)
        self.assertContains(respuesta, f'src="{self.foto_ubicacion}"')
        self.assertContains(respuesta, f'src="{self.foto}"')
        self.assertContains(
            respuesta,
            'alt="Fotografía referencial de ubicación de Galletas de arroz"',
        )
        respuesta = self.client.get(self.foto_ubicacion)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "image/png")
        contenido = b"".join(respuesta.streaming_content)
        self.assertEqual(
            contenido,
            Path(self.ubicacion.fotografia_referencial.path).read_bytes(),
        )
        self.assertNotEqual(
            contenido,
            Path(self.producto.fotografia_comercial.path).read_bytes(),
        )

    def test_hora_chilena_en_verano_invierno_y_cambio_de_dia(self):
        for mes, esperado in (
            (1, "02/01/2026 00:05"), (7, "01/07/2026 23:05"),
        ):
            UbicacionProducto.objects.filter(pk=self.ubicacion.pk).update(
                fecha_ultima_actualizacion=datetime(
                    2026, mes, 2, 3, 5, 59, tzinfo=zona_utc.utc,
                ),
            )
            respuesta = self.client.get(self.ficha)
            self.assertContains(respuesta, esperado)
            self.assertContains(respuesta, "America/Santiago")
            self.assertNotContains(respuesta, "03:05:59")

    def test_cp003_todas_las_combinaciones_de_datos_incompletos(self):
        foto = self.ubicacion.fotografia_referencial.name
        for pasillo, fotografia, fecha in product((False, True), repeat=3):
            with self.subTest(
                pasillo=pasillo, fotografia=fotografia, fecha=fecha,
            ):
                UbicacionProducto.objects.filter(pk=self.ubicacion.pk).update(
                    pasillo=self.pasillo if pasillo else None,
                    fotografia_referencial=foto if fotografia else None,
                    fecha_ultima_actualizacion=(
                        self.instante if fecha else None
                    ),
                    ultimo_responsable=self.usuario.personaltienda
                    if fecha else None,
                )
                respuesta = self.client.get(self.ficha)
                for existe, dato, aviso in (
                    (pasillo, "Pasillo 3", "Ubicación por confirmar"),
                    (fotografia, f'src="{self.foto_ubicacion}"', "Sin imagen"),
                    (fecha, "13/11/2026 18:45",
                     "Fecha de actualización no disponible"),
                ):
                    self.assertContains(respuesta, dato if existe else aviso)
                    self.assertNotContains(
                        respuesta, aviso if existe else dato,
                    )
                self.assertContains(respuesta, "$1.250")
                self.assertContains(respuesta, self.producto.descripcion)
                self.assertNotContains(respuesta, "None")
                self.assertNotContains(respuesta, "NULL")

    def test_sin_fila_no_crea_ubicacion_y_muestra_tres_avisos(self):
        self.ubicacion.delete()
        antes = list(UbicacionProducto.objects.values())
        with self.assertNumQueries(1):
            respuesta = self.client.get(self.ficha)
        for aviso in (
            "Ubicación por confirmar", "Sin imagen",
            "Fecha de actualización no disponible",
        ):
            self.assertContains(respuesta, aviso)
        self.assertEqual(list(UbicacionProducto.objects.values()), antes)
        self.assertEqual(self.client.get(self.foto_ubicacion).status_code, 404)

    def test_archivo_ausente_conserva_pasillo_fecha_y_datos_comerciales(self):
        Path(self.ubicacion.fotografia_referencial.path).unlink()
        respuesta = self.client.get(self.ficha)
        for dato in (
            "Sin imagen", "Pasillo 3", "13/11/2026 18:45", "$1.250",
            self.producto.descripcion, f'src="{self.foto}"',
        ):
            self.assertContains(respuesta, dato)
        self.assertEqual(self.client.get(self.foto_ubicacion).status_code, 404)

    def test_cambios_k08_se_reflejan_y_sin_cambios_se_conserva_fecha(self):
        instante = datetime(2026, 11, 14, 22, 5, tzinfo=zona_utc.utc)
        self.client.force_login(self.usuario)
        ruta = reverse(
            "catalogo:actualizar_ubicacion", args=[self.producto.pk],
        )
        with patch("catalogo.ubicacion.timezone.now", return_value=instante):
            respuesta = self.client.post(ruta, {
                "pasillo": self.nuevo_pasillo.pk,
                "fotografia_referencial": test_ubicacion.imagen_ubicacion(
                    "nueva.png", color="red",
                ),
            })
        self.assertEqual(respuesta.status_code, 302)
        self.client.logout()
        respuesta = self.client.get(self.ficha)
        self.assertContains(respuesta, "Pasillo 4")
        self.assertContains(respuesta, "14/11/2026 19:05")
        self.ubicacion.refresh_from_db()
        anterior = UbicacionProducto.objects.values().get(pk=self.ubicacion.pk)
        respuesta = self.client.get(self.foto_ubicacion)
        self.assertEqual(
            b"".join(respuesta.streaming_content),
            Path(self.ubicacion.fotografia_referencial.path).read_bytes(),
        )
        self.client.force_login(self.usuario)
        self.client.post(ruta, {"pasillo": self.nuevo_pasillo.pk})
        self.client.logout()
        self.assertContains(self.client.get(self.ficha), "14/11/2026 19:05")
        self.assertEqual(
            UbicacionProducto.objects.values().get(pk=self.ubicacion.pk),
            anterior,
        )

    def test_stock_y_precio_no_alteran_fecha_publica(self):
        antes = list(UbicacionProducto.objects.values())
        for usuario, accion, datos in (
            (self.usuario, "actualizar_stock", {"stock": "0"}),
            (self.jefatura, "actualizar_precio", {"precio_normal": "1450"}),
        ):
            self.client.force_login(usuario)
            self.client.post(reverse(
                f"catalogo:{accion}", args=[self.producto.pk],
            ), datos)
            self.client.logout()
            self.assertContains(
                self.client.get(self.ficha), "13/11/2026 18:45",
            )
            self.assertEqual(list(UbicacionProducto.objects.values()), antes)
        respuesta = self.client.get(self.ficha)
        self.assertContains(respuesta, "$1.450")
        self.assertContains(respuesta, "Sin stock")

    def test_solo_lectura_sin_consultas_de_personal(self):
        ubicaciones = list(UbicacionProducto.objects.values())
        productos = list(Producto.objects.values())
        referencias = list(PrecioReferencia.objects.values())
        with CaptureQueriesContext(connection) as consultas:
            self.client.get(self.ficha)
            respuesta = self.client.get(self.foto_ubicacion)
            b"".join(respuesta.streaming_content)
        self.assertEqual(len(consultas), 2)
        for consulta in consultas:
            self.assertTrue(
                consulta["sql"].lstrip().upper().startswith("SELECT"),
            )
            self.assertNotIn("auth_user", consulta["sql"])
            self.assertNotIn("personal_personaltienda", consulta["sql"])
        self.assertEqual(list(UbicacionProducto.objects.values()), ubicaciones)
        self.assertEqual(list(Producto.objects.values()), productos)
        self.assertEqual(list(PrecioReferencia.objects.values()), referencias)

    def test_estados_no_activos_e_id_inexistente_devuelven_404(self):
        for estado in (
            EstadoProducto.PENDIENTE, EstadoProducto.RECHAZADO,
            EstadoProducto.INACTIVO,
        ):
            Producto.objects.filter(pk=self.producto.pk).update(estado=estado)
            for ruta in (self.ficha, self.foto_ubicacion):
                self.assertEqual(self.client.get(ruta).status_code, 404)
        self.assertEqual(self.client.get(reverse(
            "cliente:fotografia_ubicacion", args=[999999],
        )).status_code, 404)

    def test_metodos_de_escritura_rechazados_sin_cambios(self):
        antes = list(UbicacionProducto.objects.values())
        for ruta in (self.ficha, self.foto_ubicacion):
            for metodo in (
                self.client.post, self.client.put, self.client.delete,
            ):
                self.assertEqual(metodo(ruta).status_code, 405)
        self.assertEqual(list(UbicacionProducto.objects.values()), antes)

    def test_no_expone_personal_referencias_ni_rutas_de_almacenamiento(self):
        respuesta = self.client.get(self.ficha)
        for dato in (
            self.usuario.username, self.anterior.username,
            self.jefatura.username,
            "Personal de prueba", "Nombre interno privado", "$1.390",
            "ultimo_responsable", "media/ubicaciones/",
        ):
            self.assertNotContains(respuesta, dato)
        self.client.force_login(self.jefatura)
        self.client.post(reverse(
            "catalogo:desactivar_producto", args=[self.producto.pk],
        ), {"confirmar": "si"})
        self.client.logout()
        self.assertEqual(self.client.get(self.ficha).status_code, 404)
        self.assertEqual(self.client.get(self.foto_ubicacion).status_code, 404)

    def test_primera_ubicacion_k08_aparece_sin_nueva_aprobacion(self):
        self.ubicacion.delete()
        self.client.force_login(self.usuario)
        with patch(
            "catalogo.ubicacion.timezone.now", return_value=self.instante,
        ):
            respuesta = self.client.post(reverse(
                "catalogo:actualizar_ubicacion", args=[self.producto.pk],
            ), {"pasillo": self.pasillo.pk})
        self.assertEqual(respuesta.status_code, 302)
        self.client.logout()
        respuesta = self.client.get(self.ficha)
        self.assertContains(respuesta, "Pasillo 3")
        self.assertContains(respuesta, "13/11/2026 18:45")
        self.assertContains(respuesta, "Sin imagen")
