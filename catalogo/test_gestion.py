from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from personal.models import PerfilPersonal, PersonalTienda

from . import test_ubicacion
from .models import (
    Categoria, EstadoProducto, PrecioReferencia, Producto, UbicacionProducto,
)


class GestionPruebas(TestCase):

    @classmethod
    def setUpTestData(cls):
        test_ubicacion.UbicacionPruebas.setUpTestData.__func__(cls)
        cls.jefatura = get_user_model().objects.create_user(
            username='87654321-K',
            first_name='Javier'
        )
        PersonalTienda.objects.create(
            usuario=cls.jefatura,
            perfil=PerfilPersonal.JEFATURA
        )

    def setUp(self):
        test_ubicacion.UbicacionPruebas.setUp(self)
        self.client.force_login(self.jefatura)
        self.lista = reverse('catalogo:seleccionar_gestion')
        self.ficha = reverse('catalogo:ficha_gestion', args=[self.producto.pk])
        self.precio = reverse(
            'catalogo:actualizar_precio',
            args=[self.producto.pk]
        )
        self.baja = reverse(
            'catalogo:desactivar_producto',
            args=[self.producto.pk]
        )

    def test_panel_busqueda_ficha_y_precio_vigente(self):
        self.assertContains(
            self.client.get(reverse('personal:panel_jefatura')),
            f'href="{self.lista}"'
        )
        for consulta in ('gAlLeTaS', 'delsol'):
            respuesta = self.client.get(self.lista, {'consulta': consulta})
            self.assertEqual(
                list(respuesta.context['productos']),
                [self.producto]
            )
        respuesta = self.client.get(self.ficha)
        for dato in ('Galletas de arroz', 'DelSol', 'Snacks', '0000000000001'):
            self.assertContains(respuesta, dato)
        self.assertEqual(
            respuesta.context['formulario']['precio_normal'].value(),
            1250
        )
        self.assertContains(respuesta, 'data-bs-toggle="modal"')
        self.assertContains(respuesta, 'name="confirmar" value="si"')

    def test_precio_valido_persiste_sin_modificar_otros_datos(self):
        ubicaciones = list(UbicacionProducto.objects.values())
        referencias = list(PrecioReferencia.objects.values())
        anterior = Producto.objects.values().get(pk=self.producto.pk)
        for importe in (1, 1450, 2147483647):
            respuesta = self.client.post(
                self.precio,
                {
                    'precio_normal': importe,
                    'estado': 'inactivo',
                    'stock': 999,
                    'codigo_barras': 'otro',
                    'categoria': 999
                },
                follow=True
            )
            self.assertRedirects(respuesta, self.ficha)
            self.assertContains(respuesta, 'Precio normal actualizado')
            anterior['precio_normal'] = importe
            self.assertEqual(
                Producto.objects.values().get(pk=self.producto.pk),
                anterior
            )
            consulta = self.client.get(self.ficha)
            self.assertEqual(
                consulta.context['formulario']['precio_normal'].value(),
                importe,
            )
        self.assertEqual(list(UbicacionProducto.objects.values()), ubicaciones)
        self.assertEqual(list(PrecioReferencia.objects.values()), referencias)

    def test_cp008_precios_invalidos_conservan_valor_y_entrada(self):
        for valor in (
            '0', '-1', '1250.0', '1.5', 'abc', '', '2147483648', '9' * 5000,
        ):
            respuesta = self.client.post(self.precio, {'precio_normal': valor})
            formulario = respuesta.context['formulario']
            self.assertIn('precio_normal', formulario.errors)
            self.assertEqual(formulario['precio_normal'].value(), valor)
            self.producto.refresh_from_db()
            self.assertEqual(self.producto.precio_normal, 1250)

    def test_cp013_baja_logica_conserva_datos_y_oculta_controles(self):
        anterior = Producto.objects.values().get(pk=self.producto.pk)
        ubicaciones = list(UbicacionProducto.objects.values())
        referencias = list(PrecioReferencia.objects.values())
        respuesta = self.client.post(
            self.baja,
            {
                'confirmar': 'si',
                'precio_normal': 1,
                'stock': 999,
                'codigo_barras': 'otro',
                'estado': 'rechazado'
            },
            follow=True
        )
        self.assertRedirects(respuesta, self.ficha)
        self.assertContains(respuesta, 'Producto desactivado')
        self.assertContains(respuesta, 'Inactivo')
        self.assertNotContains(respuesta, 'data-bs-toggle="modal"')
        self.assertNotContains(respuesta, 'Actualizar precio')
        anterior['estado'] = EstadoProducto.INACTIVO
        self.assertEqual(
            Producto.objects.values().get(pk=self.producto.pk),
            anterior
        )
        self.assertEqual(list(UbicacionProducto.objects.values()), ubicaciones)
        self.assertEqual(list(PrecioReferencia.objects.values()), referencias)
        self.assertEqual(
            list(self.client.get(self.lista).context['productos']),
            []
        )
        self.assertTrue(
            self.ubicacion.fotografia_referencial.storage.exists(
                self.ubicacion.fotografia_referencial.name,
            )
        )

    def test_sin_confirmacion_no_desactiva(self):
        for datos in ({}, {'confirmar': 'no'}):
            self.client.post(self.baja, datos)
            self.producto.refresh_from_db()
            self.assertEqual(self.producto.estado, EstadoProducto.ACTIVO)

    def test_estado_se_revalida_y_solicitudes_repetidas_no_modifican(self):
        for estado in (
            EstadoProducto.PENDIENTE,
            EstadoProducto.RECHAZADO,
            EstadoProducto.INACTIVO
        ):
            Producto.objects.filter(pk=self.producto.pk).update(estado=estado)
            antes = Producto.objects.values().get(pk=self.producto.pk)
            for ruta in (self.precio, self.baja):
                respuesta = self.client.post(
                    ruta,
                    {
                        'precio_normal': 2000,
                        'confirmar': 'si'
                    },
                    follow=True
                )
                self.assertContains(respuesta, 'El producto ya no está activo')
                self.assertEqual(
                    Producto.objects.values().get(pk=self.producto.pk),
                    antes
                )
            self.assertEqual(
                self.client.get(self.ficha).status_code,
                200 if estado == EstadoProducto.INACTIVO else 404
            )

    def test_permisos_servidor_get_y_post(self):
        sin_perfil = get_user_model().objects.create_user(
            username='11111111-1',
        )
        self.anterior.is_active = False
        self.anterior.save(update_fields=['is_active'])
        for usuario, estado in (
            (None, 302), (self.usuario, 403),
            (sin_perfil, 403), (self.anterior, 302),
        ):
            cliente = Client(enforce_csrf_checks=True)
            if usuario:
                cliente.force_login(usuario)
            cliente.get(reverse('personal:acceso'))
            token = cliente.cookies['csrftoken'].value
            for ruta in (self.lista, self.ficha):
                self.assertEqual(cliente.get(ruta).status_code, estado)
            for ruta in (self.precio, self.baja):
                self.assertEqual(
                    cliente.post(
                        ruta,
                        {
                            'precio_normal': 2000,
                            'confirmar': 'si'
                        },
                        HTTP_X_CSRFTOKEN=token
                    ).status_code,
                    estado
                )
        self.producto.refresh_from_db()
        self.assertEqual(self.producto.estado, EstadoProducto.ACTIVO)
        self.assertEqual(self.producto.precio_normal, 1250)

    def test_csrf_y_metodos(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.jefatura)
        for ruta in (self.precio, self.baja):
            self.assertEqual(self.client.get(ruta).status_code, 405)
            for cabeceras in ({}, {'HTTP_X_CSRFTOKEN': 'incorrecto'}):
                self.assertEqual(
                    cliente.post(
                        ruta,
                        {
                            'precio_normal': 2000,
                            'confirmar': 'si'
                        },
                        **cabeceras
                    ).status_code,
                    403
                )
        self.assertEqual(self.client.post(self.ficha).status_code, 405)

    def test_fallo_mysql_revierte_precio_y_estado(self):
        original = Producto.save

        def fallar(producto, *args, **kwargs):
            original(producto, *args, **kwargs)
            raise DatabaseError
        antes = Producto.objects.values().get(pk=self.producto.pk)
        for ruta in (self.precio, self.baja):
            with patch.object(Producto, 'save', new=fallar):
                respuesta = self.client.post(
                    ruta,
                    {
                        'precio_normal': 2000,
                        'confirmar': 'si'
                    }
                )
            self.assertContains(respuesta, 'No se pudo guardar')
            self.assertEqual(
                Producto.objects.values().get(pk=self.producto.pk),
                antes
            )


class GestionSimultaneaPruebas(TransactionTestCase):

    def test_precio_y_baja_simultaneos_respetan_estado_activo(self):
        usuario = get_user_model().objects.create_user(
            username='87654321-K',
            first_name='Javier'
        )
        PersonalTienda.objects.create(
            usuario=usuario,
            perfil=PerfilPersonal.JEFATURA
        )
        producto = Producto.objects.create(
            codigo_barras='001',
            nombre_producto='Galletas',
            marca='DelSol',
            categoria=Categoria.objects.create(nombre='Snacks'),
            estado=EstadoProducto.ACTIVO,
            precio_normal=1250
        )
        barrera = Barrier(2)

        def operar(accion):
            try:
                cliente = Client()
                cliente.force_login(usuario)
                barrera.wait(timeout=10)
                return cliente.post(
                    reverse(
                        'catalogo:' + accion,
                        args=[producto.pk]
                    ),
                    {
                        'precio_normal': 1450,
                        'confirmar': 'si'
                    },
                    follow=True
                )
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as ejecutor:
            precio, baja = ejecutor.map(
                operar,
                (
                    'actualizar_precio',
                    'desactivar_producto'
                )
            )
        producto.refresh_from_db()
        self.assertEqual(producto.estado, EstadoProducto.INACTIVO)
        self.assertContains(baja, 'Producto desactivado')
        if b'Precio normal actualizado' in precio.content:
            self.assertEqual(producto.precio_normal, 1450)
        else:
            self.assertContains(precio, 'El producto ya no está activo')
            self.assertEqual(producto.precio_normal, 1250)
