from datetime import datetime, timezone as zona_utc
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from personal.models import PerfilPersonal, PersonalTienda

from .models import EstadoProducto, Producto, UbicacionProducto
from . import test_ubicacion


class ConsultaActualizacionPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_ubicacion.UbicacionPruebas.setUpTestData.__func__(cls)
        cls.jefatura = get_user_model().objects.create_user(
            username="87654321-K", first_name="Javier",
        )
        PersonalTienda.objects.create(
            usuario=cls.jefatura, perfil=PerfilPersonal.JEFATURA,
        )

    def setUp(self):
        test_ubicacion.UbicacionPruebas.setUp(self)
        self.lista = reverse("catalogo:seleccionar_actualizacion")
        self.ficha = reverse(
            "catalogo:ultima_actualizacion", args=[self.producto.pk],
        )

    def test_ambos_perfiles_acceden_desde_panel_y_buscan_activos(self):
        for usuario in (self.usuario, self.jefatura):
            self.client.force_login(usuario)
            self.assertContains(self.client.get(
                reverse("personal:panel"), follow=True,
            ), f'href="{self.lista}"')
            for consulta in ("gAlLeTaS", "delsol"):
                respuesta = self.client.get(self.lista, {"consulta": consulta})
                self.assertEqual(list(respuesta.context["productos"]), [
                    self.producto,
                ])
                self.assertContains(respuesta, f'href="{self.ficha}"')
            respuesta = self.client.get(self.ficha)
            self.assertContains(respuesta, "Galletas de arroz")
            self.assertContains(respuesta, "DelSol")
            self.assertContains(respuesta, "Pasillo 3")
            self.assertContains(respuesta, "Personal de prueba")
            self.assertNotContains(respuesta, self.anterior.username)
            self.assertNotContains(respuesta, "Guardar cambios")

    def test_utc_se_convierte_a_chile_en_verano_e_invierno(self):
        for mes, hora in ((1, "00:05"), (7, "23:05")):
            instante = datetime(2026, mes, 2, 3, 5, 59, tzinfo=zona_utc.utc)
            UbicacionProducto.objects.filter(pk=self.ubicacion.pk).update(
                fecha_ultima_actualizacion=instante,
            )
            dia = "02" if mes == 1 else "01"
            respuesta = self.client.get(self.ficha)
            self.assertContains(respuesta, f"{dia}/{mes:02}/2026 {hora}")
            self.assertContains(respuesta, "America/Santiago")
            self.assertNotContains(respuesta, "03:05:59")

    def test_cp011_y_cp012_reflejan_cambios_y_conservan_marcas(self):
        instante = datetime(2026, 11, 13, 21, 45, tzinfo=zona_utc.utc)
        ruta = reverse(
            "catalogo:actualizar_ubicacion", args=[self.producto.pk],
        )
        with patch("catalogo.ubicacion.timezone.now", return_value=instante):
            self.client.post(ruta, {"pasillo": self.nuevo_pasillo.pk})
        respuesta = self.client.get(self.ficha)
        self.assertContains(respuesta, "13/11/2026 18:45")
        self.assertContains(respuesta, "Pasillo 4")
        self.ubicacion.refresh_from_db()
        antes = UbicacionProducto.objects.values().get(pk=self.ubicacion.pk)
        self.client.post(ruta, {"pasillo": self.nuevo_pasillo.pk})
        self.client.post(reverse(
            "catalogo:actualizar_stock", args=[self.producto.pk],
        ), {"stock": "0"})
        self.assertEqual(
            UbicacionProducto.objects.values().get(pk=self.ubicacion.pk),
            antes,
        )
        self.assertContains(self.client.get(self.ficha), "13/11/2026 18:45")

    def test_cambio_de_foto_se_refleja_en_la_consulta(self):
        instante = datetime(2026, 11, 13, 22, 5, tzinfo=zona_utc.utc)
        with patch("catalogo.ubicacion.timezone.now", return_value=instante):
            self.client.post(reverse(
                "catalogo:actualizar_ubicacion", args=[self.producto.pk],
            ), {"fotografia_referencial": test_ubicacion.imagen_ubicacion(
                color="blue",
            )})
        self.assertContains(self.client.get(self.ficha), "13/11/2026 19:05")

    def test_sin_ubicacion_o_sin_actualizacion_no_inventa_datos(self):
        self.ubicacion.delete()
        for crear in (False, True):
            if crear:
                UbicacionProducto.objects.create(producto=self.producto)
            antes = list(UbicacionProducto.objects.values())
            respuesta = self.client.get(self.ficha)
            for mensaje in (
                "Sin pasillo definido",
                "Todavía no hay una actualización de ubicación registrada",
                "Fecha de actualización no disponible",
            ):
                self.assertContains(respuesta, mensaje)
            self.assertNotContains(respuesta, "None")
            self.assertEqual(list(UbicacionProducto.objects.values()), antes)

    def test_consultas_no_ejecutan_escrituras_en_mysql(self):
        with CaptureQueriesContext(connection) as consultas:
            self.client.get(self.lista)
            self.client.get(self.ficha)
        self.assertTrue(consultas.captured_queries)
        for consulta in consultas:
            self.assertTrue(
                consulta["sql"].lstrip().upper().startswith("SELECT"),
            )

    def test_estados_no_activos_no_exponen_ficha(self):
        for estado in (
            EstadoProducto.PENDIENTE, EstadoProducto.RECHAZADO,
            EstadoProducto.INACTIVO,
        ):
            Producto.objects.filter(pk=self.producto.pk).update(estado=estado)
            self.assertEqual(self.client.get(self.ficha).status_code, 404)
            self.assertEqual(
                list(self.client.get(self.lista).context["productos"]), [],
            )

    def test_sin_autorizacion_se_rechaza_y_solo_se_acepta_get(self):
        sin_perfil = get_user_model().objects.create_user(
            username="11111111-1",
        )
        self.anterior.is_active = False
        self.anterior.save(update_fields=["is_active"])
        for usuario, estado in (
            (None, 302), (sin_perfil, 403), (self.anterior, 302),
        ):
            cliente = Client()
            if usuario:
                cliente.force_login(usuario)
            for ruta in (self.lista, self.ficha):
                self.assertEqual(cliente.get(ruta).status_code, estado)
        for ruta in (self.lista, self.ficha):
            for metodo in (
                self.client.post, self.client.put, self.client.delete,
            ):
                self.assertEqual(metodo(ruta).status_code, 405)
        with patch("personal.permisos.obtener_personal_autorizado") as obtener:
            obtener.return_value.perfil = "otro"
            self.assertEqual(self.client.get(self.ficha).status_code, 403)
