from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from tempfile import TemporaryDirectory
from threading import Barrier

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connections
from django.test import (
    Client, TestCase, TransactionTestCase, override_settings,
)
from django.urls import reverse
from PIL import Image

from personal.models import PerfilPersonal, PersonalTienda

from .models import Categoria, EstadoProducto, PrecioReferencia, Producto


def crear_personal(rut, perfil, activo=True):
    usuario = get_user_model().objects.create_user(
        username=rut, first_name="Personal de prueba", is_active=activo,
    )
    PersonalTienda.objects.create(usuario=usuario, perfil=perfil)
    return usuario


def ruta_producto(accion, producto):
    return reverse(f"catalogo:{accion}", args=[producto.pk])


class RevisionProductoPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.jefatura = crear_personal("87654321-K", PerfilPersonal.JEFATURA)
        cls.categoria = Categoria.objects.create(nombre="Snacks")
        cls.producto = Producto.objects.create(
            codigo_barras="0000000000001",
            nombre_producto="Galletas pendientes",
            marca="Marca de prueba",
            categoria=cls.categoria,
            descripcion="Descripción de prueba",
        )

    def setUp(self):
        self.client.force_login(self.jefatura)
        self.pendientes = reverse("catalogo:pendientes")
        self.revision = ruta_producto("revisar_producto", self.producto)
        self.aprobacion = ruta_producto("aprobar_producto", self.producto)
        self.rechazo = ruta_producto("rechazar_producto", self.producto)

    def crear_referencia(self):
        return PrecioReferencia.objects.create(
            codigo_barras=self.producto.codigo_barras, precio_referencia=1250,
        )

    def test_panel_y_listado_muestran_solo_solicitudes_pendientes(self):
        for numero, estado in enumerate(
            (EstadoProducto.ACTIVO, EstadoProducto.RECHAZADO,
             EstadoProducto.INACTIVO), start=2,
        ):
            Producto.objects.create(
                codigo_barras=f"{numero:013d}", nombre_producto=estado.label,
                marca="Marca de prueba", categoria=self.categoria,
                estado=estado, precio_normal=1200,
            )
        panel = self.client.get(reverse("personal:panel_jefatura"))
        self.assertContains(panel, f'href="{self.pendientes}"')
        listado = self.client.get(self.pendientes)
        self.assertEqual(list(listado.context["productos"]), [self.producto])
        self.assertContains(listado, self.producto.nombre_producto)
        self.assertContains(listado, f'href="{self.revision}"')

    def test_ficha_muestra_datos_y_foto_con_acceso_protegido(self):
        directorio = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=directorio))
        imagen = BytesIO()
        Image.new("RGB", (16, 16), "green").save(imagen, format="PNG")
        foto = ContentFile(imagen.getvalue())
        self.producto.fotografia_comercial.save("foto.png", foto)
        respuesta = self.client.get(self.revision)
        for texto in (
            self.producto.nombre_producto, self.producto.codigo_barras,
            self.producto.marca, self.categoria.nombre,
            self.producto.descripcion, "Fotografía comercial de",
        ):
            self.assertContains(respuesta, texto)
        ruta_foto = ruta_producto("foto_producto_pendiente", self.producto)
        self.assertContains(respuesta, ruta_foto)
        respuesta_foto = self.client.get(ruta_foto)
        self.assertEqual(respuesta_foto["Content-Type"], "image/png")
        self.assertEqual(
            b"".join(respuesta_foto.streaming_content),
            imagen.getvalue(),
        )
        self.assertEqual(Client().get(ruta_foto).status_code, 302)
        self.producto.estado = EstadoProducto.RECHAZADO
        self.producto.save(update_fields=["estado"])
        self.assertEqual(self.client.get(ruta_foto).status_code, 404)

    def test_cp006_valida_y_permite_conservar_el_precio_sugerido(self):
        referencia = self.crear_referencia()
        ficha = self.client.get(self.revision)
        self.assertEqual(
            ficha.context["formulario"]["precio_inicial"].value(), 1250,
        )
        self.assertContains(ficha, 'value="1250"')
        invalida = self.client.post(self.aprobacion, {"precio_inicial": "0"})
        self.assertContains(invalida, 'value="0"')
        self.assertContains(invalida, "Ingresa un precio mayor que cero.")
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.estado, EstadoProducto.PENDIENTE)
        self.assertIsNone(self.producto.precio_normal)

        respuesta = self.client.post(
            self.aprobacion, {"precio_inicial": "1250"}, follow=True,
        )
        self.assertRedirects(respuesta, self.pendientes)
        self.assertContains(respuesta, "Producto aprobado. Quedó activo.")
        self.assertContains(respuesta, "No hay productos pendientes")
        self.producto.refresh_from_db()
        referencia.refresh_from_db()
        self.assertEqual(self.producto.estado, EstadoProducto.ACTIVO)
        self.assertEqual(self.producto.precio_normal, 1250)
        self.assertEqual(referencia.precio_referencia, 1250)

    def test_cp021_aprueba_con_precio_editado_y_conserva_referencia(self):
        referencia = self.crear_referencia()
        ficha = self.client.get(self.revision)
        self.assertContains(ficha, 'value="1250"')
        respuesta = self.client.post(
            self.aprobacion, {"precio_inicial": "1390"},
        )
        self.assertRedirects(respuesta, self.pendientes)
        self.producto.refresh_from_db()
        referencia.refresh_from_db()
        self.assertEqual(self.producto.estado, EstadoProducto.ACTIVO)
        self.assertEqual(self.producto.precio_normal, 1390)
        self.assertEqual(referencia.precio_referencia, 1250)

    def test_post_respeta_el_importe_si_se_recargo_la_referencia(self):
        referencia = self.crear_referencia()
        self.assertContains(self.client.get(self.revision), 'value="1250"')
        referencia.precio_referencia = 1490
        referencia.save(update_fields=["precio_referencia"])
        respuesta = self.client.post(
            self.aprobacion, {"precio_inicial": "1250"},
        )
        self.assertRedirects(respuesta, self.pendientes)
        self.producto.refresh_from_db()
        referencia.refresh_from_db()
        self.assertEqual(self.producto.precio_normal, 1250)
        self.assertEqual(referencia.precio_referencia, 1490)

    def test_cp022_sin_referencia_exacta_permite_precio_manual(self):
        self.producto.codigo_barras = "0000000000999"
        self.producto.save(update_fields=["codigo_barras"])
        PrecioReferencia.objects.create(
            codigo_barras="999", precio_referencia=1250,
        )
        ficha = self.client.get(self.revision)
        formulario = ficha.context["formulario"]
        self.assertIsNone(formulario["precio_inicial"].value())
        respuesta = self.client.post(
            self.aprobacion, {"precio_inicial": "1590"},
        )
        self.assertRedirects(respuesta, self.pendientes)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.estado, EstadoProducto.ACTIVO)
        self.assertEqual(self.producto.precio_normal, 1590)

    def test_importes_invalidos_se_conservan_y_no_aprueban(self):
        self.crear_referencia()
        for valor in ("", "0", "-1", "1250.0", "1.5", "abc", "2147483648"):
            with self.subTest(valor=valor):
                respuesta = self.client.post(
                    self.aprobacion, {"precio_inicial": valor},
                )
                formulario = respuesta.context["formulario"]
                self.assertIn("precio_inicial", formulario.errors)
                self.assertEqual(formulario["precio_inicial"].value(), valor)
                self.producto.refresh_from_db()
                self.assertEqual(
                    self.producto.estado, EstadoProducto.PENDIENTE,
                )
                self.assertIsNone(self.producto.precio_normal)
        referencia = PrecioReferencia.objects.get()
        self.assertEqual(referencia.precio_referencia, 1250)

    def test_cp007_rechaza_sin_precio_ni_motivo_y_sin_referencia(self):
        respuesta = self.client.post(self.rechazo, follow=True)
        self.assertRedirects(respuesta, self.pendientes)
        self.assertContains(respuesta, "Producto rechazado.")
        self.assertContains(respuesta, "No hay productos pendientes")
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.estado, EstadoProducto.RECHAZADO)
        self.assertIsNone(self.producto.precio_normal)
        self.assertFalse(PrecioReferencia.objects.exists())

    def test_rechaza_tras_error_de_aprobacion_y_conserva_referencia(self):
        referencia = self.crear_referencia()
        error = self.client.post(self.aprobacion, {"precio_inicial": "abc"})
        self.assertContains(error, 'value="abc"')
        respuesta = self.client.post(
            self.rechazo, {"precio_inicial": "abc", "precio_normal": "333"},
        )
        self.assertRedirects(respuesta, self.pendientes)
        self.producto.refresh_from_db()
        referencia.refresh_from_db()
        self.assertEqual(self.producto.estado, EstadoProducto.RECHAZADO)
        self.assertIsNone(self.producto.precio_normal)
        self.assertEqual(referencia.precio_referencia, 1250)

    def test_post_manipulado_solo_guarda_campos_autorizados(self):
        consulta = Producto.objects.filter(pk=self.producto.pk)
        campos_protegidos = consulta.values(
            "stock", "codigo_barras", "categoria_id", "nombre_producto",
            "marca", "descripcion", "fotografia_comercial",
        )
        datos_originales = campos_protegidos.get()
        for ruta, destino in (
            (self.aprobacion, EstadoProducto.ACTIVO),
            (self.rechazo, EstadoProducto.RECHAZADO),
        ):
            with self.subTest(destino=destino):
                consulta.update(
                    estado=EstadoProducto.PENDIENTE, precio_normal=None,
                )
                respuesta = self.client.post(ruta, {
                    "precio_inicial": "1390", "precio_normal": "999",
                    "estado": "inactivo", "stock": 40, "codigo_barras": "123",
                    "categoria": 999, "nombre_producto": "Otro nombre",
                    "marca": "Otra marca", "descripcion": "Otra descripción",
                    "fotografia_comercial": "otra.png",
                })
                self.assertRedirects(respuesta, self.pendientes)
                producto = consulta.get()
                self.assertEqual(producto.estado, destino)
                self.assertEqual(
                    producto.precio_normal,
                    1390 if destino == EstadoProducto.ACTIVO else None,
                )
                self.assertEqual(campos_protegidos.get(), datos_originales)

    def test_solicitudes_procesadas_no_admiten_nueva_decision(self):
        for estado, precio in (
            (EstadoProducto.ACTIVO, 1390),
            (EstadoProducto.RECHAZADO, None),
            (EstadoProducto.INACTIVO, 1390),
        ):
            with self.subTest(estado=estado):
                Producto.objects.filter(pk=self.producto.pk).update(
                    estado=estado, precio_normal=precio,
                )
                for ruta in (self.revision, self.aprobacion, self.rechazo):
                    respuesta = (
                        self.client.get(ruta, follow=True)
                        if ruta == self.revision else self.client.post(
                            ruta, {"precio_inicial": "999"}, follow=True,
                        )
                    )
                    self.assertRedirects(respuesta, self.pendientes)
                    self.assertContains(
                        respuesta, "Esta solicitud ya fue procesada.",
                    )
                    self.producto.refresh_from_db()
                    self.assertEqual(self.producto.estado, estado)
                    self.assertEqual(self.producto.precio_normal, precio)

    def test_requiere_jefatura_en_get_y_post_directos(self):
        reponedor = crear_personal("12345678-9", PerfilPersonal.REPONEDOR)
        inactivo = crear_personal(
            "11111111-1", PerfilPersonal.JEFATURA, activo=False,
        )
        sin_perfil = get_user_model().objects.create_user(
            username="22222222-2",
        )
        for usuario, estado in (
            (None, 302), (reponedor, 403), (inactivo, 302), (sin_perfil, 403),
        ):
            with self.subTest(usuario=usuario):
                cliente = Client(enforce_csrf_checks=True)
                if usuario:
                    cliente.force_login(usuario)
                cliente.get(reverse("personal:acceso"))
                token = cliente.cookies["csrftoken"].value
                for ruta in (
                    self.pendientes, self.revision,
                    ruta_producto("foto_producto_pendiente", self.producto),
                ):
                    self.assertEqual(cliente.get(ruta).status_code, estado)
                for ruta in (self.aprobacion, self.rechazo):
                    respuesta = cliente.post(
                        ruta,
                        {"precio_inicial": "1390"},
                        HTTP_X_CSRFTOKEN=token,
                    )
                    self.assertEqual(respuesta.status_code, estado)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.estado, EstadoProducto.PENDIENTE)
        self.assertIsNone(self.producto.precio_normal)

    def test_decisiones_exigen_post_y_csrf_valido(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.jefatura)
        cliente.get(self.revision)
        for ruta in (self.aprobacion, self.rechazo):
            self.assertEqual(cliente.get(ruta).status_code, 405)
            for cabeceras in ({}, {"HTTP_X_CSRFTOKEN": "incorrecto"}):
                respuesta = cliente.post(
                    ruta, {"precio_inicial": "1390"}, **cabeceras,
                )
                self.assertEqual(respuesta.status_code, 403)
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.estado, EstadoProducto.PENDIENTE)
        self.assertIsNone(self.producto.precio_normal)


class RevisionSimultaneaPruebas(TransactionTestCase):
    def test_aprobacion_y_rechazo_simultaneos_solo_procesan_una_decision(self):
        jefatura = crear_personal("87654321-K", PerfilPersonal.JEFATURA)
        producto = Producto.objects.create(
            codigo_barras="0000000000001", nombre_producto="Galletas",
            marca="Marca de prueba", categoria=Categoria.objects.create(
                nombre="Snacks",
            ),
        )
        barrera = Barrier(2)

        def decidir(accion):
            try:
                cliente = Client()
                cliente.force_login(jefatura)
                barrera.wait(timeout=10)
                return cliente.post(
                    ruta_producto(accion, producto),
                    {"precio_inicial": "1390"}, follow=True,
                )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            aprobacion, rechazo = ejecutor.map(
                decidir, ("aprobar_producto", "rechazar_producto"),
            )
        producto.refresh_from_db()
        if producto.estado == EstadoProducto.ACTIVO:
            self.assertContains(aprobacion, "Producto aprobado.")
            self.assertContains(rechazo, "Esta solicitud ya fue procesada.")
            self.assertEqual(producto.precio_normal, 1390)
        else:
            self.assertEqual(producto.estado, EstadoProducto.RECHAZADO)
            self.assertContains(rechazo, "Producto rechazado.")
            self.assertContains(aprobacion, "Esta solicitud ya fue procesada.")
            self.assertIsNone(producto.precio_normal)
