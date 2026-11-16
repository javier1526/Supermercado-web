from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from personal.models import PerfilPersonal, PersonalTienda

from . import test_ubicacion
from .models import (
    EstadoProducto, PrecioReferencia, Producto, UbicacionProducto,
)


class FichaPublicaPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_ubicacion.UbicacionPruebas.setUpTestData.__func__(cls)
        cls.jefatura = get_user_model().objects.create_user(
            username="87654321-K", first_name="Nombre interno privado",
        )
        PersonalTienda.objects.create(
            usuario=cls.jefatura, perfil=PerfilPersonal.JEFATURA,
        )

    def setUp(self):
        test_ubicacion.UbicacionPruebas.setUp(self)
        self.client.logout()
        self.producto.descripcion = "Galletas de arroz, presentación de 200 g."
        self.producto.fotografia_comercial = test_ubicacion.imagen_ubicacion(
            "comercial.png", color="blue",
        )
        self.producto.save()
        self.ficha = reverse("cliente:ficha_producto", args=[self.producto.pk])
        self.foto = reverse(
            "cliente:fotografia_producto", args=[self.producto.pk],
        )

    def test_ficha_publica_muestra_datos_comerciales_y_stock(self):
        with self.assertNumQueries(1):
            respuesta = self.client.get(self.ficha)
        self.assertEqual(respuesta.status_code, 200)
        for dato in (
            "Galletas de arroz", "DelSol", "$1.250",
            self.producto.descripcion, "15 unidades disponibles",
        ):
            self.assertContains(respuesta, dato)
        self.assertContains(respuesta, f'src="{self.foto}"')
        self.assertContains(
            respuesta, 'alt="Fotografía comercial de Galletas de arroz"',
        )

    def test_precio_chileno_entero_con_puntos_y_sin_decimales(self):
        for precio, esperado in (
            (1, "$1"), (1250, "$1.250"), (1450, "$1.450"),
            (2147483647, "$2.147.483.647"),
        ):
            Producto.objects.filter(pk=self.producto.pk).update(
                precio_normal=precio,
            )
            self.assertContains(self.client.get(self.ficha), esperado)

    def test_fotografia_correcta_reutiliza_archivo_comercial(self):
        respuesta = self.client.get(self.foto)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Type"], "image/png")
        self.assertEqual(respuesta["Content-Security-Policy"], "sandbox")
        contenido = b"".join(respuesta.streaming_content)
        self.assertEqual(
            contenido,
            Path(self.producto.fotografia_comercial.path).read_bytes(),
        )
        self.assertNotEqual(
            contenido,
            Path(self.ubicacion.fotografia_referencial.path).read_bytes(),
        )

    def test_descripcion_y_foto_opcionales_independientes(self):
        for descripcion, fotografia in (
            (None, self.producto.fotografia_comercial.name),
            (self.producto.descripcion, ""), (None, ""), ("", None),
        ):
            Producto.objects.filter(pk=self.producto.pk).update(
                descripcion=descripcion, fotografia_comercial=fotografia,
            )
            respuesta = self.client.get(self.ficha)
            self.assertEqual(respuesta.status_code, 200)
            self.assertNotContains(respuesta, "None")
            self.assertNotContains(respuesta, "NULL")
            if not descripcion:
                self.assertContains(
                    respuesta, 'id="descripcion-producto" class="py-2"></div>',
                )
            if not fotografia:
                self.assertNotContains(respuesta, f'src="{self.foto}"')
                self.assertContains(respuesta, "ratio ratio-1x1")
                self.assertEqual(self.client.get(self.foto).status_code, 404)

    def test_fotografia_fisica_ausente_no_produce_error_500(self):
        Path(self.producto.fotografia_comercial.path).unlink()
        respuesta = self.client.get(self.ficha)
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context["fotografia_disponible"])
        self.assertNotContains(respuesta, f'src="{self.foto}"')
        self.assertEqual(self.client.get(self.foto).status_code, 404)
        almacenamiento = self.producto.fotografia_comercial.storage
        with patch.object(almacenamiento, "exists", side_effect=OSError):
            self.assertEqual(self.client.get(self.ficha).status_code, 200)

    def test_estados_no_activos_no_exponen_ficha_ni_fotografia(self):
        for estado in (
            EstadoProducto.PENDIENTE, EstadoProducto.RECHAZADO,
            EstadoProducto.INACTIVO,
        ):
            Producto.objects.filter(pk=self.producto.pk).update(estado=estado)
            for ruta in (self.ficha, self.foto):
                respuesta = self.client.get(ruta)
                self.assertEqual(respuesta.status_code, 404)
                self.assertNotContains(
                    respuesta, self.producto.nombre_producto, status_code=404,
                )

    def test_identificador_inexistente_devuelve_404(self):
        for nombre in ("ficha_producto", "fotografia_producto"):
            respuesta = self.client.get(reverse(
                f"cliente:{nombre}", args=[999999],
            ))
            self.assertEqual(respuesta.status_code, 404)

    def test_solo_get_y_consultas_sin_escrituras(self):
        with CaptureQueriesContext(connection) as consultas:
            self.client.get(self.ficha)
            respuesta = self.client.get(self.foto)
            b"".join(respuesta.streaming_content)
        self.assertEqual(len(consultas), 2)
        for consulta in consultas:
            self.assertTrue(
                consulta["sql"].lstrip().upper().startswith("SELECT"),
            )
        for ruta in (self.ficha, self.foto):
            for metodo in (
                self.client.post, self.client.put, self.client.delete,
            ):
                self.assertEqual(metodo(ruta).status_code, 405)

    def test_tarjetas_enlazan_ficha_conservando_consulta_sin_n_mas_uno(self):
        ruta = reverse("cliente:inicio")
        with self.assertNumQueries(1):
            respuesta = self.client.get(ruta, {"consulta": "gAlLeTaS"})
        self.assertContains(
            respuesta, f'href="{self.ficha}?consulta=gAlLeTaS"',
        )
        self.assertContains(respuesta, "$1.250")
        self.assertContains(respuesta, f'src="{self.foto}"')
        respuesta = self.client.get(self.ficha, {"consulta": "gAlLeTaS"})
        self.assertContains(respuesta, 'href="/?consulta=gAlLeTaS"')
        self.assertContains(respuesta, "Volver a resultados")
        self.assertNotContains(respuesta, self.producto.codigo_barras)

    def test_cp009_cambio_stock_k07_se_refleja_en_ficha_publica(self):
        ubicaciones = list(UbicacionProducto.objects.values())
        referencias = list(PrecioReferencia.objects.values())
        for usuario in (self.usuario, self.jefatura):
            for stock in (0, 25):
                self.client.force_login(usuario)
                respuesta = self.client.post(reverse(
                    "catalogo:actualizar_stock", args=[self.producto.pk],
                ), {"stock": stock})
                self.assertEqual(respuesta.status_code, 302)
                self.client.logout()
                respuesta = self.client.get(self.ficha)
                self.assertContains(
                    respuesta, "Sin stock" if stock == 0
                    else "25 unidades disponibles",
                )
                resultados = self.client.get(
                    reverse("cliente:inicio"), {"consulta": "galletas"},
                )
                self.assertContains(resultados, self.producto.nombre_producto)
        self.assertEqual(list(UbicacionProducto.objects.values()), ubicaciones)
        self.assertEqual(list(PrecioReferencia.objects.values()), referencias)

    def test_precio_k10_se_refleja_sin_alterar_referencia_ni_ubicacion(self):
        referencias = list(PrecioReferencia.objects.values())
        ubicaciones = list(UbicacionProducto.objects.values())
        self.client.force_login(self.jefatura)
        self.client.post(reverse(
            "catalogo:actualizar_precio", args=[self.producto.pk],
        ), {"precio_normal": "1450"})
        self.client.logout()
        self.assertContains(self.client.get(self.ficha), "$1.450")
        self.assertContains(self.client.get(
            reverse("cliente:inicio"), {"consulta": "galletas"},
        ), "$1.450")
        self.assertEqual(list(PrecioReferencia.objects.values()), referencias)
        self.assertEqual(list(UbicacionProducto.objects.values()), ubicaciones)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock, 15)

    def test_aprobacion_y_desactivacion_controlan_ficha_y_foto_publicas(self):
        Producto.objects.filter(pk=self.producto.pk).update(
            estado=EstadoProducto.PENDIENTE, precio_normal=None,
        )
        self.assertEqual(self.client.get(self.ficha).status_code, 404)
        self.assertEqual(self.client.get(self.foto).status_code, 404)
        self.client.force_login(self.jefatura)
        self.client.post(reverse(
            "catalogo:aprobar_producto", args=[self.producto.pk],
        ), {"precio_inicial": "1250"})
        self.client.logout()
        self.assertEqual(self.client.get(self.ficha).status_code, 200)
        respuesta = self.client.get(self.foto)
        self.assertEqual(respuesta.status_code, 200)
        b"".join(respuesta.streaming_content)
        self.client.force_login(self.jefatura)
        self.client.post(reverse(
            "catalogo:desactivar_producto", args=[self.producto.pk],
        ), {"confirmar": "si"})
        self.client.logout()
        self.assertEqual(self.client.get(self.ficha).status_code, 404)
        self.assertEqual(self.client.get(self.foto).status_code, 404)

    def test_ficha_no_expone_datos_internos_ni_referencia(self):
        respuesta = self.client.get(self.ficha)
        for dato in (
            "1390", "$1.390", self.producto.codigo_barras,
            self.usuario.username, self.jefatura.username,
            "Nombre interno privado", "ultimo_responsable",
            "fotografia_referencial", "fecha_ultima_actualizacion",
        ):
            self.assertNotContains(respuesta, dato)
        self.assertNotContains(respuesta, 'href="/personal/')
        self.assertNotContains(respuesta, "<form")

    def test_textos_comerciales_se_escapan(self):
        texto = "<script>alert(1)</script>"
        Producto.objects.filter(pk=self.producto.pk).update(descripcion=texto)
        respuesta = self.client.get(self.ficha)
        self.assertNotContains(respuesta, texto)
        self.assertContains(respuesta, "&lt;script&gt;")
