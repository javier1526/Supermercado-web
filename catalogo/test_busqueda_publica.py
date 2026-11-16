from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from personal.models import PerfilPersonal, PersonalTienda

from .models import (
    Categoria, EstadoProducto, PrecioReferencia, Producto,
)


class BusquedaPublicaPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.categoria = Categoria.objects.create(nombre="Snacks")
        cls.activos = [Producto.objects.create(
            codigo_barras=f"000{numero}",
            nombre_producto=f"Papas fritas presentación {numero}",
            marca="DelSol", categoria=cls.categoria,
            estado=EstadoProducto.ACTIVO, precio_normal=1250, stock=0,
        ) for numero in range(4)]
        cls.ocultos = [Producto.objects.create(
            codigo_barras=f"oculto-{estado}",
            nombre_producto=f"Papas {estado}",
            marca="DelSol", categoria=cls.categoria, estado=estado,
        ) for estado in (
            EstadoProducto.PENDIENTE, EstadoProducto.RECHAZADO,
            EstadoProducto.INACTIVO,
        )]
        PrecioReferencia.objects.create(
            codigo_barras="referencia_secreta", precio_referencia=987654,
        )

    def setUp(self):
        self.inicio = reverse("cliente:inicio")
        self.sugerencias = reverse("cliente:sugerencias")

    def test_inicio_publico_muestra_carruseles_sin_error(self):
        with self.assertNumQueries(2):
            respuesta = self.client.get(self.inicio)
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(
            respuesta, "Ingresa un nombre o marca para buscar",
        )
        self.assertContains(respuesta, "Nombre o marca")
        self.assertNotContains(respuesta, 'href="/personal/')

    def test_cp001_nombre_completo_parcial_marca_y_mayusculas(self):
        for consulta in (
            "Papas fritas presentación 0", "fritas", "pApAs", "DelSol",
            "dEl", "p", "  fritas  ",
        ):
            with self.subTest(consulta=consulta), self.assertNumQueries(1):
                respuesta = self.client.get(
                    self.inicio, {"consulta": consulta},
                )
                self.assertContains(respuesta, "Papas fritas presentación 0")
                self.assertEqual(respuesta.context["consulta"], consulta)

    def test_cp002_vacio_y_espacios_no_ejecutan_consultas(self):
        for consulta in ("", "   ", "\t\n"):
            with self.assertNumQueries(0):
                respuesta = self.client.get(
                    self.inicio, {"consulta": consulta},
                )
            self.assertContains(
                respuesta, "Ingresa un nombre o marca para buscar.",
            )
            self.assertEqual(respuesta.context["consulta"], consulta)
            self.assertEqual(respuesta.context["productos"], [])

    def test_cp002_sin_coincidencias_muestra_mensaje_exacto(self):
        respuesta = self.client.get(self.inicio, {"consulta": "zzproducto999"})
        self.assertEqual(
            respuesta.context["mensaje"], "No se encontraron productos",
        )
        self.assertContains(respuesta, "No se encontraron productos")
        self.assertEqual(respuesta.context["consulta"], "zzproducto999")

    def test_solo_activos_incluido_stock_cero(self):
        respuesta = self.client.get(self.inicio, {"consulta": "papas"})
        self.assertEqual(len(respuesta.context["productos"]), 4)
        for producto in self.ocultos:
            self.assertNotContains(respuesta, producto.nombre_producto)
        for producto in self.activos:
            self.assertContains(respuesta, producto.nombre_producto)

    def test_sugerencias_maximo_tres_solo_nombre_y_marca(self):
        for consulta in ("pApAs", "dEl", "p"):
            with self.assertNumQueries(1):
                respuesta = self.client.get(
                    self.sugerencias, {"consulta": consulta},
                )
            productos = respuesta.json()["productos"]
            self.assertEqual(len(productos), 3)
            self.assertEqual(productos, [
                {
                    "nombre_producto": producto.nombre_producto,
                    "marca": "DelSol",
                }
                for producto in self.activos[:3]
            ])

    def test_sugerencias_vacias_no_consultan_mysql(self):
        for datos in ({}, {"consulta": ""}, {"consulta": "   "}):
            with self.assertNumQueries(0):
                respuesta = self.client.get(self.sugerencias, datos)
            self.assertEqual(respuesta.json(), {"productos": []})

    def test_codigo_y_referencias_no_se_buscan_ni_exponen(self):
        for texto in ("0000", "referencia_secreta", "987654"):
            respuesta = self.client.get(self.inicio, {"consulta": texto})
            self.assertEqual(respuesta.context["productos"], [])
            self.assertEqual(self.client.get(
                self.sugerencias, {"consulta": texto},
            ).json()["productos"], [])
        respuesta = self.client.get(self.inicio, {"consulta": "papas"})
        self.assertNotContains(respuesta, "987654")
        self.assertNotContains(respuesta, "precio_referencia")
        self.assertNotContains(respuesta, "ultimo_responsable")

    def test_entradas_manipuladas_se_tratan_como_texto(self):
        for texto in (
            "%' OR 1=1 --", "%", "_", "'", "<script>alert(1)</script>",
        ):
            respuesta = self.client.get(self.inicio, {"consulta": texto})
            self.assertEqual(respuesta.status_code, 200)
            self.assertEqual(respuesta.context["productos"], [])
            self.assertNotContains(respuesta, "<script>alert(1)</script>")
        self.assertEqual(Producto.objects.count(), 7)

    def test_nombre_de_producto_se_escapa_en_html(self):
        Producto.objects.filter(pk=self.activos[0].pk).update(
            nombre_producto="Papas <img src=x onerror=alert(1)>",
        )
        respuesta = self.client.get(self.inicio, {"consulta": "papas"})
        self.assertNotContains(respuesta, "<img src=x onerror=alert(1)>")
        self.assertContains(respuesta, "&lt;img src=x onerror=alert(1)&gt;")

    def test_consultas_publicas_solo_get_y_sin_escrituras(self):
        with CaptureQueriesContext(connection) as consultas:
            self.client.get(self.inicio, {"consulta": "papas"})
            self.client.get(self.sugerencias, {"consulta": "papas"})
        self.assertEqual(len(consultas), 2)
        for consulta in consultas:
            self.assertTrue(
                consulta["sql"].lstrip().upper().startswith("SELECT"),
            )
        for ruta in (self.inicio, self.sugerencias):
            for metodo in (
                self.client.post, self.client.put, self.client.delete,
            ):
                self.assertEqual(metodo(ruta).status_code, 405)

    def test_aprobacion_y_cp013_baja_cambian_visibilidad_publica(self):
        usuario = get_user_model().objects.create_user(
            username="87654321-K", first_name="Jefatura de prueba",
        )
        PersonalTienda.objects.create(
            usuario=usuario, perfil=PerfilPersonal.JEFATURA,
        )
        self.client.force_login(usuario)
        pendiente = self.ocultos[0]
        self.client.post(reverse(
            "catalogo:aprobar_producto", args=[pendiente.pk],
        ), {"precio_inicial": "1450"})
        self.client.logout()
        self.assertContains(self.client.get(
            self.inicio, {"consulta": "papas"},
        ), pendiente.nombre_producto)
        self.client.force_login(usuario)
        self.client.post(reverse(
            "catalogo:desactivar_producto", args=[pendiente.pk],
        ), {"confirmar": "si"})
        self.client.logout()
        respuesta = self.client.get(self.inicio, {"consulta": "papas"})
        self.assertNotContains(respuesta, pendiente.nombre_producto)
        self.assertEqual(
            Producto.objects.get(pk=pendiente.pk).estado,
            EstadoProducto.INACTIVO,
        )
        self.assertRedirects(
            self.client.get(reverse("personal:panel")),
            reverse("personal:acceso"),
        )
