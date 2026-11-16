from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from personal.models import PerfilPersonal, PersonalTienda

from .models import (
    Categoria, EstadoProducto, PrecioReferencia, Producto, UbicacionProducto,
)


class StockPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuarios = []
        for rut, perfil in (
            ("12345678-9", PerfilPersonal.REPONEDOR),
            ("87654321-K", PerfilPersonal.JEFATURA),
        ):
            usuario = get_user_model().objects.create_user(
                username=rut, first_name="Personal de prueba",
            )
            personal = PersonalTienda(usuario=usuario, perfil=perfil)
            personal.full_clean()
            personal.save()
            cls.usuarios.append(usuario)
        cls.producto = Producto.objects.create(
            codigo_barras="0000000000001", nombre_producto="Galletas de arroz",
            marca="DelSol",
            categoria=Categoria.objects.create(nombre="Snacks"),
            estado=EstadoProducto.ACTIVO, precio_normal=1250, stock=15,
        )
        UbicacionProducto.objects.create(
            producto=cls.producto, fecha_ultima_actualizacion=timezone.now(),
            ultimo_responsable=cls.usuarios[0].personaltienda,
        )
        PrecioReferencia.objects.create(
            codigo_barras=cls.producto.codigo_barras, precio_referencia=1390,
        )

    def setUp(self):
        self.lista = reverse("catalogo:seleccionar_stock")
        self.ruta = reverse(
            "catalogo:actualizar_stock", args=[self.producto.pk],
        )
        self.client.force_login(self.usuarios[0])

    def test_ambos_paneles_permiten_seleccionar_solo_activos(self):
        Producto.objects.create(
            codigo_barras="002", nombre_producto="Pendiente",
            marca="Otra marca", categoria=self.producto.categoria,
        )
        for usuario in self.usuarios:
            self.client.force_login(usuario)
            panel = self.client.get(reverse("personal:panel"), follow=True)
            self.assertContains(panel, f'href="{self.lista}"')
            respuesta = self.client.get(self.lista)
            self.assertEqual(
                list(respuesta.context["productos"]), [self.producto],
            )
            self.assertContains(respuesta, f'href="{self.ruta}"')
        for consulta in ("gAlLeTaS", "delsol"):
            respuesta = self.client.get(self.lista, {"consulta": consulta})
            self.assertEqual(
                list(respuesta.context["productos"]), [self.producto],
            )
        self.assertContains(
            self.client.get(self.lista, {"consulta": "inexistente"}),
            "No se encontraron productos activos.",
        )

    def test_cp009_cero_y_positivos_persisten_para_ambos_perfiles(self):
        for usuario in self.usuarios:
            self.client.force_login(usuario)
            for cantidad in (0, 30, 2147483647):
                with self.subTest(perfil=usuario.username, cantidad=cantidad):
                    respuesta = self.client.post(
                        self.ruta, {"stock": str(cantidad)}, follow=True,
                    )
                    self.assertRedirects(respuesta, self.ruta)
                    self.assertContains(respuesta, "Stock actualizado")
                    producto = Producto.objects.get(pk=self.producto.pk)
                    self.assertEqual(producto.stock, cantidad)
                    consulta = self.client.get(self.ruta)
                    self.assertEqual(
                        consulta.context["producto"].stock, cantidad,
                    )

    def test_cp010_invalidos_conservan_stock_y_entrada(self):
        invalidos = ("-1", "15.0", "1.5", "abc", "", "2147483648", "9" * 5000)
        for texto in invalidos:
            with self.subTest(texto=texto[:20]):
                respuesta = self.client.post(self.ruta, {"stock": texto})
                formulario = respuesta.context["formulario"]
                self.assertIn("stock", formulario.errors)
                self.assertEqual(formulario["stock"].value(), texto)
                self.producto.refresh_from_db()
                self.assertEqual(self.producto.stock, 15)

    def test_estado_se_verifica_de_nuevo_antes_de_guardar(self):
        for estado in (
            EstadoProducto.PENDIENTE, EstadoProducto.RECHAZADO,
            EstadoProducto.INACTIVO,
        ):
            with self.subTest(estado=estado):
                Producto.objects.filter(pk=self.producto.pk).update(
                    estado=EstadoProducto.ACTIVO,
                )
                self.assertEqual(self.client.get(self.ruta).status_code, 200)
                Producto.objects.filter(pk=self.producto.pk).update(
                    estado=estado,
                )
                respuesta = self.client.post(
                    self.ruta, {"stock": "30"}, follow=True,
                )
                self.assertRedirects(respuesta, self.lista)
                self.assertContains(
                    respuesta, "El producto ya no está activo.",
                )
                self.assertRedirects(self.client.get(self.ruta), self.lista)
                self.producto.refresh_from_db()
                self.assertEqual(self.producto.stock, 15)
                self.assertEqual(self.producto.estado, estado)

    def test_post_manipulado_no_modifica_otros_datos_ni_ubicacion(self):
        original = Producto.objects.values().get(pk=self.producto.pk)
        ubicacion = list(UbicacionProducto.objects.values())
        referencia = list(PrecioReferencia.objects.values())
        respuesta = self.client.post(self.ruta, {
            "stock": "0", "estado": "inactivo", "precio_normal": "1",
            "codigo_barras": "otro", "categoria": "999",
            "nombre_producto": "Otro nombre", "marca": "Otra marca",
            "fecha_ultima_actualizacion": "2020-01-01",
            "ultimo_responsable": self.usuarios[1].personaltienda.pk,
        })
        self.assertRedirects(respuesta, self.ruta)
        original["stock"] = 0
        self.assertEqual(
            Producto.objects.values().get(pk=self.producto.pk), original,
        )
        self.assertEqual(list(UbicacionProducto.objects.values()), ubicacion)
        self.assertEqual(list(PrecioReferencia.objects.values()), referencia)

    def test_get_y_post_sin_autorizacion_no_modifican_stock(self):
        sin_perfil = get_user_model().objects.create_user(
            username="11111111-1", first_name="Ana",
        )
        inactivo = get_user_model().objects.create_user(
            username="22222222-2", first_name="Pedro", is_active=False,
        )
        PersonalTienda.objects.create(
            usuario=inactivo, perfil=PerfilPersonal.REPONEDOR,
        )
        casos = ((None, 302), (sin_perfil, 403), (inactivo, 302))
        for usuario, estado in casos:
            cliente = Client(enforce_csrf_checks=True)
            if usuario:
                cliente.force_login(usuario)
            cliente.get(reverse("personal:acceso"))
            token = cliente.cookies["csrftoken"].value
            for ruta in (self.lista, self.ruta):
                self.assertEqual(cliente.get(ruta).status_code, estado)
            self.assertEqual(
                cliente.post(
                    self.ruta, {"stock": "5"}, HTTP_X_CSRFTOKEN=token,
                ).status_code,
                estado,
            )
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock, 15)

    def test_stock_exige_csrf_valido(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.usuarios[1])
        cliente.get(self.ruta)
        for cabeceras in ({}, {"HTTP_X_CSRFTOKEN": "incorrecto"}):
            respuesta = cliente.post(self.ruta, {"stock": "5"}, **cabeceras)
            self.assertEqual(
                respuesta.status_code, 403,
            )
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.stock, 15)
