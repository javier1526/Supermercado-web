from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import (
    Client, TestCase, TransactionTestCase, override_settings,
)
from django.urls import reverse
from PIL import Image

from personal.models import PerfilPersonal, PersonalTienda

from .forms import FormularioRegistroProducto
from .models import Categoria, EstadoProducto, Producto


def crear_personal(rut, perfil, activo=True):
    usuario = get_user_model().objects.create_user(
        username=rut, first_name="Personal de prueba", is_active=activo,
    )
    PersonalTienda.objects.create(usuario=usuario, perfil=perfil)
    return usuario


class RegistroProductoPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.categoria = Categoria.objects.create(nombre="Snacks")
        cls.reponedor = crear_personal("12345678-9", PerfilPersonal.REPONEDOR)

    def setUp(self):
        self.ruta = reverse("catalogo:registrar_producto")
        self.datos = {
            "codigo_barras": "0000000000001",
            "nombre_producto": "Galletas de arroz 100 g",
            "marca": "DelSol",
            "categoria": self.categoria.pk,
            "descripcion": "Galletas crujientes.",
        }
        self.client.force_login(self.reponedor)
        directorio = self.enterContext(TemporaryDirectory())
        self.media = Path(directorio)
        self.enterContext(override_settings(MEDIA_ROOT=directorio))

    def foto_png(self):
        archivo = BytesIO()
        Image.new("RGB", (16, 16), "green").save(archivo, format="PNG")
        return SimpleUploadedFile("foto.png", archivo.getvalue(), "image/png")

    def producto_existente(self, estado=EstadoProducto.PENDIENTE, **cambios):
        datos = {
            **self.datos,
            "categoria": self.categoria,
            "estado": estado,
            "precio_normal": 1000 if estado == EstadoProducto.ACTIVO else None,
            **cambios,
        }
        return Producto.objects.create(**datos)

    def test_formulario_accesible_desde_panel_sin_campos_internos(self):
        panel = self.client.get(reverse("personal:panel_reponedor"))
        self.assertContains(panel, f'href="{self.ruta}"')
        respuesta = self.client.get(self.ruta)
        self.assertContains(respuesta, 'enctype="multipart/form-data"')
        self.assertEqual(
            set(respuesta.context["formulario"].fields),
            {*self.datos, "fotografia_comercial"},
        )

    def test_cp004_registro_pendiente_sin_precio_y_con_ceros_iniciales(self):
        respuesta = self.client.post(self.ruta, self.datos, follow=True)
        self.assertRedirects(respuesta, reverse("personal:panel_reponedor"))
        self.assertContains(respuesta, "Producto registrado.")
        producto = Producto.objects.get()
        self.assertEqual(producto.codigo_barras, "0000000000001")
        self.assertEqual(producto.categoria, self.categoria)
        self.assertEqual(producto.descripcion, self.datos["descripcion"])
        self.assertEqual(producto.estado, EstadoProducto.PENDIENTE)
        self.assertIsNone(producto.precio_normal)
        self.assertEqual(producto.stock, 0)
        self.assertFalse(producto.fotografia_comercial)

    def test_cp005_identifica_cada_campo_obligatorio_faltante(self):
        campos = ("codigo_barras", "nombre_producto", "marca", "categoria")
        for campo in campos:
            with self.subTest(campo=campo):
                datos = self.datos.copy()
                del datos[campo]
                respuesta = self.client.post(self.ruta, datos)
                self.assertFormError(
                    respuesta.context["formulario"], campo,
                    "Este campo es obligatorio.",
                )
                self.assertFalse(Producto.objects.exists())

    def test_codigo_vacio_en_blanco_o_demasiado_largo_es_invalido(self):
        for codigo in ("", " \t ", "0" * 33):
            with self.subTest(codigo=codigo):
                respuesta = self.client.post(
                    self.ruta, {**self.datos, "codigo_barras": codigo},
                )
                self.assertIn(
                    "codigo_barras", respuesta.context["formulario"].errors,
                )
                self.assertFalse(Producto.objects.exists())

    def test_nombre_y_marca_compuestos_solo_por_espacios_son_invalidos(self):
        for campo in ("nombre_producto", "marca"):
            with self.subTest(campo=campo):
                respuesta = self.client.post(
                    self.ruta, {**self.datos, campo: "   "},
                )
                self.assertFormError(
                    respuesta.context["formulario"], campo,
                    "Este campo es obligatorio.",
                )
                self.assertFalse(Producto.objects.exists())

    def test_cp020_codigo_duplicado_se_rechaza_en_todos_los_estados(self):
        producto = self.producto_existente()
        datos = {**self.datos, "nombre_producto": "Otro producto"}
        for estado in EstadoProducto:
            with self.subTest(estado=estado):
                producto.estado = estado
                producto.precio_normal = (
                    1000 if estado == EstadoProducto.ACTIVO else None
                )
                producto.save(update_fields=["estado", "precio_normal"])
                respuesta = self.client.post(self.ruta, datos)
                self.assertContains(
                    respuesta, "Ya existe un producto con este",
                )
                self.assertIn(
                    "codigo_barras", respuesta.context["formulario"].errors,
                )
                self.assertEqual(Producto.objects.count(), 1)

    def test_conflicto_al_guardar_no_deja_fotografia_huerfana(self):
        self.producto_existente()
        datos = {
            **self.datos,
            "nombre_producto": "Otro producto",
            "fotografia_comercial": self.foto_png(),
        }
        with patch.object(Producto, "validate_unique", return_value=None):
            respuesta = self.client.post(self.ruta, datos)
        self.assertIn("codigo_barras", respuesta.context["formulario"].errors)
        self.assertContains(respuesta, "Ya existe un producto con este")
        self.assertEqual(Producto.objects.count(), 1)
        self.assertEqual(list(self.media.iterdir()), [])

    def test_nombre_y_marca_en_pendientes_y_activos_bloquean_el_registro(self):
        producto = self.producto_existente()
        datos = {
            **self.datos,
            "codigo_barras": "0000000000002",
            "nombre_producto": "  GALLETAS DE ARROZ 100 G  ",
            "marca": " delsol ",
        }
        for estado in (EstadoProducto.PENDIENTE, EstadoProducto.ACTIVO):
            with self.subTest(estado=estado):
                producto.estado = estado
                producto.precio_normal = (
                    1000 if estado == EstadoProducto.ACTIVO else None
                )
                producto.save(update_fields=["estado", "precio_normal"])
                respuesta = self.client.post(self.ruta, datos)
                self.assertIn(
                    "nombre_producto", respuesta.context["formulario"].errors,
                )
                self.assertEqual(Producto.objects.count(), 1)

    def test_nombre_y_marca_no_bloquean_en_rechazados_o_inactivos(self):
        self.producto_existente(EstadoProducto.RECHAZADO)
        self.producto_existente(
            EstadoProducto.INACTIVO, codigo_barras="0000000000002",
        )
        datos = {**self.datos, "codigo_barras": "0000000000003"}
        self.assertEqual(self.client.post(self.ruta, datos).status_code, 302)
        self.assertEqual(Producto.objects.count(), 3)

    def test_acepta_variantes_de_nombre_completo_o_marca(self):
        self.producto_existente(nombre_producto="Té negro 100 g")
        variantes = (
            ("Té negro 200 g", "DelSol"),
            ("Té negro 100 g", "Otra marca"),
            ("Te negro 100 g", "DelSol"),
            ("Té  negro 100 g", "DelSol"),
        )
        for numero, (nombre, marca) in enumerate(variantes, start=2):
            with self.subTest(nombre=nombre, marca=marca):
                datos = {
                    **self.datos, "codigo_barras": f"{numero:013d}",
                    "nombre_producto": nombre, "marca": marca,
                }
                self.assertEqual(
                    self.client.post(self.ruta, datos).status_code, 302,
                )
        self.assertEqual(Producto.objects.count(), 5)

    def test_categoria_inexistente_o_invalida_conserva_datos_ingresados(self):
        for categoria in (self.categoria.pk + 1, "invalida"):
            with self.subTest(categoria=categoria):
                respuesta = self.client.post(
                    self.ruta, {**self.datos, "categoria": categoria},
                )
                self.assertContains(
                    respuesta, "Selecciona una categoría válida.",
                )
                self.assertContains(respuesta, self.datos["nombre_producto"])
                self.assertContains(respuesta, self.datos["codigo_barras"])
                self.assertFalse(Producto.objects.exists())

    def test_fotografia_valida_se_guarda_en_media(self):
        datos = {**self.datos, "fotografia_comercial": self.foto_png()}
        self.assertEqual(self.client.post(self.ruta, datos).status_code, 302)
        foto = Producto.objects.get().fotografia_comercial
        self.assertTrue(foto.name.startswith("productos/"))
        self.assertTrue((self.media / foto.name).is_file())

    def test_archivo_invalido_no_crea_fila_ni_archivo(self):
        archivo = SimpleUploadedFile("foto.png", b"%PDF-1.7", "image/png")
        respuesta = self.client.post(
            self.ruta, {**self.datos, "fotografia_comercial": archivo},
        )
        self.assertContains(respuesta, "Adjunta un archivo de imagen válido.")
        self.assertContains(respuesta, "Vuelve a adjuntar la fotografía")
        self.assertFalse(Producto.objects.exists())
        self.assertEqual(list(self.media.iterdir()), [])

    def test_otro_campo_invalido_no_guarda_una_foto_valida(self):
        respuesta = self.client.post(self.ruta, {
            **self.datos, "marca": "", "fotografia_comercial": self.foto_png(),
        })
        self.assertContains(respuesta, "Vuelve a adjuntar la fotografía")
        self.assertFalse(Producto.objects.exists())
        self.assertEqual(list(self.media.iterdir()), [])

    def test_error_despues_de_adjuntar_foto_revierte_fila_y_archivo(self):
        guardar = Producto.save

        def guardar_con_error(producto, *args, **kwargs):
            if kwargs.get("update_fields"):
                raise OSError("Error simulado al asociar el archivo")
            return guardar(producto, *args, **kwargs)

        datos = {**self.datos, "fotografia_comercial": self.foto_png()}
        with patch.object(Producto, "save", guardar_con_error):
            respuesta = self.client.post(self.ruta, datos)
        self.assertContains(respuesta, "No se pudo guardar la fotografía.")
        self.assertFalse(Producto.objects.exists())
        self.assertEqual(list(self.media.rglob("*.png")), [])

    def test_post_manipulado_no_puede_imponer_precio_estado_ni_stock(self):
        datos = {
            **self.datos, "precio_normal": 999, "estado": "activo", "stock": 7,
        }
        self.assertEqual(self.client.post(self.ruta, datos).status_code, 302)
        producto = Producto.objects.get()
        self.assertIsNone(producto.precio_normal)
        self.assertEqual(producto.estado, EstadoProducto.PENDIENTE)
        self.assertEqual(producto.stock, 0)

    def test_accesos_get_y_post_restringidos_por_perfil(self):
        jefatura = crear_personal("87654321-K", PerfilPersonal.JEFATURA)
        inactivo = crear_personal(
            "10101010-1", PerfilPersonal.REPONEDOR, activo=False,
        )
        sin_perfil = get_user_model().objects.create_user(
            username="20202020-2",
        )
        for usuario, estado in (
            (None, 302), (jefatura, 403), (inactivo, 302), (sin_perfil, 403),
        ):
            with self.subTest(usuario=usuario):
                cliente = Client(enforce_csrf_checks=True)
                if usuario:
                    cliente.force_login(usuario)
                cliente.get(reverse("personal:acceso"))
                token = cliente.cookies["csrftoken"].value
                self.assertEqual(cliente.get(self.ruta).status_code, estado)
                respuesta = cliente.post(
                    self.ruta, self.datos, HTTP_X_CSRFTOKEN=token,
                )
                self.assertEqual(respuesta.status_code, estado)
                self.assertFalse(Producto.objects.exists())

    def test_post_sin_csrf_valido_no_crea_producto(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.reponedor)
        cliente.get(self.ruta)
        for cabeceras in ({}, {"HTTP_X_CSRFTOKEN": "token-incorrecto"}):
            respuesta = cliente.post(self.ruta, self.datos, **cabeceras)
            self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(Producto.objects.exists())


class RegistroSimultaneoPruebas(TransactionTestCase):
    def test_altas_simultaneas_del_mismo_nombre_y_marca_crean_una_fila(self):
        reponedor = crear_personal("12345678-9", PerfilPersonal.REPONEDOR)
        categoria = Categoria.objects.create(nombre="Snacks")
        barrera = Barrier(2)
        es_valido = FormularioRegistroProducto.is_valid

        def validar_y_esperar(formulario):
            valido = es_valido(formulario)
            if valido:
                barrera.wait(timeout=10)
            return valido

        def enviar(codigo):
            try:
                cliente = Client()
                cliente.force_login(reponedor)
                respuesta = cliente.post(
                    reverse("catalogo:registrar_producto"),
                    {
                        "codigo_barras": codigo, "nombre_producto": "Galletas",
                        "marca": "DelSol", "categoria": categoria.pk,
                    },
                )
                return respuesta.status_code
            finally:
                connections.close_all()

        with patch.object(
            FormularioRegistroProducto, "is_valid", validar_y_esperar,
        ), ThreadPoolExecutor(max_workers=2) as ejecutor:
            estados = list(ejecutor.map(enviar, ("00001", "00002")))
        self.assertCountEqual(estados, (200, 302))
        self.assertEqual(Producto.objects.count(), 1)
