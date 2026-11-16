from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from personal.models import PerfilPersonal, PersonalTienda

from . import test_ubicacion
from .models import Categoria, EstadoProducto, PrecioReferencia, Producto


class CategoriasPublicasPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.snacks = Categoria.objects.create(nombre="Snacks")
        cls.bebidas = Categoria.objects.create(nombre="Bebidas")
        cls.vacia = Categoria.objects.create(nombre="Vacía")
        cls.sin_activos = Categoria.objects.create(nombre="Solo ocultos")
        cls.activos = [Producto.objects.create(
            codigo_barras=f"000{indice}", nombre_producto=f"Snack {nombre}",
            marca="Otra" if indice == 5 else "DelSol", categoria=cls.snacks,
            estado=EstadoProducto.ACTIVO, precio_normal=precio,
            stock=0 if indice == 1 else 15,
        ) for indice, (nombre, precio) in enumerate((
            ("Zeta", 3000), ("Avena", 1500), ("Maní", 1500),
            ("Beta", 800), ("Arroz", 1200), ("Avena", 1500),
        ))]
        cls.agua = Producto.objects.create(
            codigo_barras="agua", nombre_producto="Agua mineral", marca="Río",
            categoria=cls.bebidas, estado=EstadoProducto.ACTIVO,
            precio_normal=850,
        )
        cls.ocultos = [Producto.objects.create(
            codigo_barras=estado, nombre_producto=f"Snack Abarrote {estado}",
            marca="DelSol", categoria=cls.snacks, estado=estado,
        ) for estado in (
            EstadoProducto.PENDIENTE, EstadoProducto.RECHAZADO,
            EstadoProducto.INACTIVO,
        )]
        Producto.objects.create(
            codigo_barras="solo-oculto", nombre_producto="Registro oculto",
            marca="Otra", categoria=cls.sin_activos,
            estado=EstadoProducto.INACTIVO,
        )
        PrecioReferencia.objects.create(
            codigo_barras=cls.activos[0].codigo_barras, precio_referencia=10,
        )

    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        configuracion = override_settings(MEDIA_ROOT=temporal.name)
        configuracion.enable()
        self.addCleanup(configuracion.disable)
        self.activos[1].fotografia_comercial.save(
            "comercial.png", test_ubicacion.imagen_ubicacion("comercial.png"),
        )
        self.inicio = reverse("cliente:inicio")
        self.lista = reverse("cliente:categorias")
        self.categoria = reverse(
            "cliente:productos_categoria", args=[self.snacks.pk],
        )
        self.por_nombre = [self.activos[i].pk for i in (4, 1, 5, 3, 2, 0)]
        self.por_precio = [self.activos[i].pk for i in (3, 4, 1, 5, 2, 0)]

    def identificadores(self, respuesta):
        return [producto.pk for producto in respuesta.context["productos"]]

    def iniciar_jefatura(self):
        usuario = get_user_model().objects.create_user(
            username="87654321-K", first_name="Jefatura de prueba",
        )
        PersonalTienda.objects.create(
            usuario=usuario, perfil=PerfilPersonal.JEFATURA,
        )
        self.client.force_login(usuario)

    def test_categorias_publicas_incluyen_vacias_y_sin_activos(self):
        with self.assertNumQueries(1):
            respuesta = self.client.get(self.lista)
        self.assertEqual(respuesta.status_code, 200)
        for categoria in (
            self.snacks, self.bebidas, self.vacia, self.sin_activos,
        ):
            self.assertContains(respuesta, categoria.nombre)
            self.assertContains(respuesta, reverse(
                "cliente:productos_categoria", args=[categoria.pk],
            ))
        self.assertNotContains(respuesta, 'href="/personal/')

    def test_categoria_solo_activos_propios_y_stock_cero(self):
        with self.assertNumQueries(2):
            respuesta = self.client.get(self.categoria)
        self.assertEqual(
            self.identificadores(respuesta), self.por_nombre,
        )
        self.assertContains(respuesta, "Sin stock")
        self.assertNotContains(respuesta, self.agua.nombre_producto)
        for producto in self.ocultos:
            self.assertNotContains(respuesta, producto.nombre_producto)

    def test_categorias_vacias_muestran_mensaje_exacto(self):
        for categoria in (self.vacia, self.sin_activos):
            respuesta = self.client.get(reverse(
                "cliente:productos_categoria", args=[categoria.pk],
            ))
            self.assertEqual(respuesta.status_code, 200)
            self.assertContains(respuesta, "No se encontraron productos")
            self.assertEqual(respuesta.context["productos"], [])

    def test_categoria_inexistente_devuelve_404(self):
        self.assertEqual(self.client.get(reverse(
            "cliente:productos_categoria", args=[999999],
        )).status_code, 404)

    def test_carruseles_maximo_cuatro_en_orden_alfabetico(self):
        with self.assertNumQueries(2):
            respuesta = self.client.get(self.inicio)
        categorias = {c.pk: c for c in respuesta.context["categorias"]}
        self.assertEqual(
            [p.pk for p in categorias[self.snacks.pk].productos_inicio],
            self.por_nombre[:4],
        )
        self.assertEqual(len(categorias[self.bebidas.pk].productos_inicio), 1)
        self.assertEqual(categorias[self.vacia.pk].productos_inicio, [])
        self.assertEqual(categorias[self.sin_activos.pk].productos_inicio, [])
        self.assertContains(respuesta, 'id="buscador-publico"')
        self.assertContains(respuesta, "Ver categoría")
        for producto in self.ocultos:
            self.assertNotContains(respuesta, producto.nombre_producto)

    def test_carruseles_reutilizan_foto_precio_stock_y_enlaces(self):
        respuesta = self.client.get(self.inicio)
        self.assertContains(respuesta, reverse(
            "cliente:fotografia_producto", args=[self.activos[1].pk],
        ))
        self.assertContains(respuesta, "$1.500")
        self.assertContains(respuesta, "Sin stock")
        self.assertContains(respuesta, f'href="{self.categoria}"')
        self.assertContains(respuesta, 'tabindex="0"')

    def test_inicio_sin_categorias_no_inventa_registros(self):
        Producto.objects.all().delete()
        Categoria.objects.all().delete()
        respuesta = self.client.get(self.inicio)
        self.assertContains(respuesta, "No hay categorías disponibles.")
        self.assertContains(
            self.client.get(self.lista), "No hay categorías disponibles.",
        )

    def test_cien_productos_y_varias_categorias_mantienen_dos_consultas(self):
        categorias = [
            Categoria.objects.create(nombre=f"Grupo {i}") for i in range(10)
        ]
        Producto.objects.bulk_create([Producto(
            codigo_barras=f"extra-{i}", nombre_producto=f"Producto {i:03}",
            marca="Prueba", categoria=categorias[i % 10],
            estado=EstadoProducto.ACTIVO, precio_normal=1000,
        ) for i in range(100)])
        with self.assertNumQueries(2):
            respuesta = self.client.get(self.inicio)
        for categoria in respuesta.context["categorias"]:
            self.assertLessEqual(len(categoria.productos_inicio), 4)

    def test_ambas_modalidades_y_empates_deterministas(self):
        for ruta, parametros in (
            (self.categoria, {}), (self.inicio, {"consulta": "sNaCk"}),
        ):
            for orden, esperado in (
                ("nombre", self.por_nombre), ("precio", self.por_precio),
            ):
                respuesta = self.client.get(
                    ruta, {**parametros, "orden": orden},
                )
                self.assertEqual(self.identificadores(respuesta), esperado)
                self.assertEqual(respuesta.context["orden"], orden)
            self.assertEqual(self.identificadores(
                self.client.get(ruta, parametros),
            ), self.por_nombre)

    def test_orden_manipulado_utiliza_nombre_sin_cambiar_filtros(self):
        for orden in (
            "?", "-precio_normal", "estado", "nombre_producto;DROP TABLE", "",
        ):
            for ruta, parametros in (
                (self.categoria, {}), (self.inicio, {"consulta": "Snack"}),
            ):
                respuesta = self.client.get(
                    ruta, {**parametros, "orden": orden},
                )
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(respuesta.context["orden"], "nombre")
                self.assertEqual(
                    self.identificadores(respuesta), self.por_nombre,
                )

    def test_enlaces_conservan_busqueda_categoria_y_orden(self):
        respuesta = self.client.get(self.inicio, {
            "consulta": "sNaCk", "orden": "precio",
        })
        self.assertEqual(respuesta.context["consulta"], "sNaCk")
        self.assertEqual(
            respuesta.context["parametros_tarjetas"],
            "consulta=sNaCk&orden=precio",
        )
        producto = self.activos[0]
        ficha = reverse("cliente:ficha_producto", args=[producto.pk])
        respuesta = self.client.get(
            ficha, {"consulta": "sNaCk", "orden": "precio"},
        )
        self.assertContains(
            respuesta, 'href="/?consulta=sNaCk&amp;orden=precio"',
        )
        respuesta = self.client.get(
            ficha, {"categoria": self.snacks.pk, "orden": "precio"},
        )
        self.assertContains(respuesta, f'href="{self.categoria}?orden=precio"')
        self.assertContains(respuesta, "Volver a categoría")
        for dato in ("invalido", "9" * 5000):
            self.assertEqual(
                self.client.get(ficha, {"categoria": dato}).status_code, 404,
            )

    def test_precio_actualizado_k10_reordena_sin_usar_referencia(self):
        self.iniciar_jefatura()
        self.client.post(reverse(
            "catalogo:actualizar_precio", args=[self.activos[0].pk],
        ), {"precio_normal": "500"})
        self.client.logout()
        respuesta = self.client.get(self.categoria, {"orden": "precio"})
        self.assertEqual(
            self.identificadores(respuesta)[0], self.activos[0].pk,
        )
        self.assertEqual(PrecioReferencia.objects.get(
            pk=self.activos[0].codigo_barras,
        ).precio_referencia, 10)
        self.assertNotContains(respuesta, "$10")

    def test_aprobacion_y_baja_se_reflejan_en_categoria_y_carrusel(self):
        pendiente = self.ocultos[0]
        self.iniciar_jefatura()
        self.client.post(reverse(
            "catalogo:aprobar_producto", args=[pendiente.pk],
        ), {"precio_inicial": "1250"})
        self.client.logout()
        self.assertContains(
            self.client.get(self.categoria), pendiente.nombre_producto,
        )
        self.assertContains(
            self.client.get(self.inicio), pendiente.nombre_producto,
        )
        usuario = get_user_model().objects.get(username="87654321-K")
        self.client.force_login(usuario)
        self.client.post(reverse(
            "catalogo:desactivar_producto", args=[pendiente.pk],
        ), {"confirmar": "si"})
        self.client.logout()
        self.assertNotContains(
            self.client.get(self.categoria), pendiente.nombre_producto,
        )
        self.assertNotContains(
            self.client.get(self.inicio), pendiente.nombre_producto,
        )

    def test_consultas_solo_get_sin_escrituras_ni_datos_internos(self):
        antes = list(Producto.objects.values())
        with CaptureQueriesContext(connection) as consultas:
            self.client.get(self.inicio)
            self.client.get(self.lista)
            respuesta = self.client.get(self.categoria, {"orden": "precio"})
        self.assertEqual(len(consultas), 5)
        for consulta in consultas:
            self.assertTrue(
                consulta["sql"].lstrip().upper().startswith("SELECT"),
            )
            self.assertNotIn("catalogo_precioreferencia", consulta["sql"])
        for ruta in (self.lista, self.categoria):
            for metodo in (
                self.client.post, self.client.put, self.client.delete,
            ):
                self.assertEqual(metodo(ruta).status_code, 405)
        self.assertEqual(list(Producto.objects.values()), antes)
        for dato in (
            "precio_referencia", "ultimo_responsable", 'href="/personal/',
        ):
            self.assertNotContains(respuesta, dato)
