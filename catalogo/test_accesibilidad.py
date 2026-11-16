from html.parser import HTMLParser
from secrets import token_urlsafe
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from personal.models import PerfilPersonal, PersonalTienda

from .models import (
    Categoria, EstadoProducto, Pasillo, PrecioReferencia, Producto,
    Supermercado, UbicacionProducto,
)
from .test_ubicacion import imagen_ubicacion


class EtiquetasHTML(HTMLParser):
    def __init__(self, contenido):
        super().__init__()
        self.elementos = []
        self.feed(contenido)

    def handle_starttag(self, tag, attrs):
        self.elementos.append((tag, dict(attrs)))


class AccesibilidadPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reponedor = get_user_model().objects.create_user(
            username="12345678-9", first_name="Responsable privado",
        )
        cls.personal = PersonalTienda.objects.create(
            usuario=cls.reponedor, perfil=PerfilPersonal.REPONEDOR,
        )
        cls.jefatura = get_user_model().objects.create_user(
            username="87654321-K", first_name="Jefatura privada",
        )
        PersonalTienda.objects.create(
            usuario=cls.jefatura, perfil=PerfilPersonal.JEFATURA,
        )
        cls.categoria = Categoria.objects.create(nombre="Snacks")
        cls.pasillo = Pasillo.objects.create(
            numero_pasillo=2, supermercado=Supermercado.objects.create(),
        )
        cls.producto = Producto.objects.create(
            codigo_barras="0001", nombre_producto="Galletas de arroz 100 g",
            marca="DelSol", categoria=cls.categoria,
            estado=EstadoProducto.ACTIVO, precio_normal=1250, stock=0,
        )
        cls.pendiente = Producto.objects.create(
            codigo_barras="0002", nombre_producto="Galletas de avena 200 g",
            marca="DelSol", categoria=cls.categoria,
        )
        PrecioReferencia.objects.create(
            codigo_barras=cls.pendiente.codigo_barras, precio_referencia=1390,
        )

    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        configuracion = override_settings(MEDIA_ROOT=temporal.name)
        configuracion.enable()
        self.addCleanup(configuracion.disable)

    def comprobar_etiquetas(self, respuesta):
        self.assertEqual(respuesta.status_code, 200)
        elementos = EtiquetasHTML(respuesta.content.decode()).elementos
        identificadores = [a["id"] for _, a in elementos if a.get("id")]
        self.assertEqual(len(identificadores), len(set(identificadores)))
        etiquetas = {a.get("for") for t, a in elementos if t == "label"}
        for tipo, atributos in elementos:
            if tipo in ("input", "select", "textarea"):
                if atributos.get("type") == "hidden":
                    continue
                self.assertIn(atributos.get("id"), etiquetas)
                referencias = atributos.get("aria-describedby", "").split()
                for referencia in referencias:
                    self.assertIn(referencia, identificadores)
        return elementos

    def test_formularios_principales_tienen_etiquetas_y_ayudas_asociadas(self):
        self.comprobar_etiquetas(self.client.get(reverse("personal:acceso")))
        self.comprobar_etiquetas(self.client.get(reverse("cliente:inicio")))
        self.client.force_login(self.reponedor)
        for nombre, argumentos in (
            ("catalogo:registrar_producto", []),
            ("catalogo:seleccionar_stock", []),
            ("catalogo:actualizar_stock", [self.producto.pk]),
            ("catalogo:actualizar_ubicacion", [self.producto.pk]),
        ):
            with self.subTest(ruta=nombre):
                respuesta = self.client.get(reverse(
                    nombre, args=argumentos,
                ))
                self.comprobar_etiquetas(respuesta)
                if nombre == "catalogo:registrar_producto":
                    self.assertContains(respuesta, "Selecciona una categoría")
                    self.assertNotContains(respuesta, "Select an option")
        self.client.force_login(self.jefatura)
        for nombre, identificador in (
            ("catalogo:revisar_producto", self.pendiente.pk),
            ("catalogo:ficha_gestion", self.producto.pk),
        ):
            self.comprobar_etiquetas(self.client.get(reverse(
                nombre, args=[identificador],
            )))

    def test_errores_registro_identifican_campos_y_conservan_valores(self):
        self.client.force_login(self.reponedor)
        ruta = reverse("catalogo:registrar_producto")
        respuesta = self.client.post(ruta, {"descripcion": "Conservar texto"})
        self.comprobar_etiquetas(respuesta)
        campos = ("codigo_barras", "nombre_producto", "marca", "categoria")
        for campo in campos:
            self.assertContains(respuesta, f'id="id_{campo}_error"')
        self.assertContains(respuesta, "Conservar texto")
        datos = {
            "codigo_barras": self.producto.codigo_barras,
            "nombre_producto": "Nombre que se conserva", "marca": "Otra marca",
            "categoria": self.categoria.pk,
        }
        respuesta = self.client.post(ruta, datos)
        self.comprobar_etiquetas(respuesta)
        self.assertContains(
            respuesta, "Ya existe un producto con este código de barras.",
        )
        self.assertContains(respuesta, 'value="Nombre que se conserva"')
        datos.update(
            codigo_barras="0003",
            nombre_producto=self.producto.nombre_producto,
            marca=self.producto.marca,
        )
        respuesta = self.client.post(ruta, datos)
        self.comprobar_etiquetas(respuesta)
        self.assertContains(
            respuesta,
            "Ya existe un producto pendiente o activo con este nombre "
            "completo y marca.",
        )
        self.assertTrue(
            respuesta.context["formulario"].errors["nombre_producto"],
        )
        self.assertContains(respuesta, 'role="alert"')
        self.assertEqual(Producto.objects.count(), 2)

    def test_errores_numericos_conservan_entrada_y_descripcion_accesible(self):
        for usuario, nombre, producto, campo in (
            (self.reponedor, "catalogo:actualizar_stock", self.producto,
             "stock"),
            (self.jefatura, "catalogo:aprobar_producto", self.pendiente,
             "precio_inicial"),
            (self.jefatura, "catalogo:actualizar_precio", self.producto,
             "precio_normal"),
        ):
            with self.subTest(campo=campo):
                self.client.force_login(usuario)
                respuesta = self.client.post(reverse(
                    nombre, args=[producto.pk],
                ), {campo: "15.0"})
                elementos = self.comprobar_etiquetas(respuesta)
                control = next(
                    a for t, a in elementos if a.get("name") == campo
                )
                self.assertEqual(control["value"], "15.0")
                self.assertEqual(control["aria-invalid"], "true")
                self.assertIn(f"id_{campo}_error", control["aria-describedby"])
                self.assertContains(respuesta, 'role="alert"')
                self.assertTrue(respuesta.context["formulario"].errors[campo])

    def test_errores_ubicacion_explican_pasillo_archivo_y_reseleccion(self):
        self.client.force_login(self.reponedor)
        ruta = reverse(
            "catalogo:actualizar_ubicacion", args=[self.producto.pk],
        )
        respuesta = self.client.post(ruta, {"pasillo": 999999})
        self.comprobar_etiquetas(respuesta)
        self.assertContains(respuesta, "Selecciona un pasillo registrado.")
        archivos = (
            SimpleUploadedFile("archivo.pdf", b"%PDF-1.4 prueba"),
            SimpleUploadedFile(
                "grande.png", imagen_ubicacion().read() + b" " * (1024 * 1024),
                content_type="image/png",
            ),
        )
        for archivo in archivos:
            with self.subTest(archivo=archivo.name):
                respuesta = self.client.post(ruta, {
                    "pasillo": self.pasillo.pk,
                    "fotografia_referencial": archivo,
                })
                self.comprobar_etiquetas(respuesta)
                self.assertContains(
                    respuesta, 'id="id_fotografia_referencial_error"',
                )
                self.assertContains(
                    respuesta,
                    "Vuelve a adjuntar la fotografía antes de guardar.",
                )
                self.assertEqual(
                    respuesta.context["formulario"]["pasillo"].value(),
                    str(self.pasillo.pk),
                )
                if archivo.name == "grande.png":
                    self.assertContains(
                        respuesta, "La fotografía supera 1 MB.",
                    )

    def test_errores_acceso_identifican_campo_y_credenciales(self):
        ruta = reverse("personal:acceso")
        respuesta = self.client.post(ruta, {
            "username": "rut inválido", "password": token_urlsafe(12),
        })
        self.comprobar_etiquetas(respuesta)
        self.assertContains(respuesta, "Ingresa un RUT con formato válido.")
        self.assertContains(respuesta, 'value="rut inválido"')
        respuesta = self.client.post(ruta, {
            "username": "12.345.678-9", "password": token_urlsafe(12),
        })
        self.comprobar_etiquetas(respuesta)
        self.assertContains(respuesta, "RUT o contraseña incorrectos.")
        self.assertContains(respuesta, 'role="alert"')
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_alt_publicos_describen_fotografias_sin_datos_privados(self):
        self.producto.fotografia_comercial.save(
            "comercial.png", imagen_ubicacion("comercial.png"),
        )
        UbicacionProducto.objects.create(
            producto=self.producto, pasillo=self.pasillo,
            fotografia_referencial=imagen_ubicacion(),
            fecha_ultima_actualizacion=timezone.now(),
            ultimo_responsable=self.personal,
        )
        for ruta in (
            reverse("cliente:inicio"),
            reverse("cliente:productos_categoria", args=[self.categoria.pk]),
            reverse("cliente:productos_pasillo", args=[self.pasillo.pk]),
            reverse("cliente:ficha_producto", args=[self.producto.pk]),
        ):
            with self.subTest(ruta=ruta):
                respuesta = self.client.get(ruta)
                elementos = EtiquetasHTML(respuesta.content.decode()).elementos
                imagenes = [a for t, a in elementos if t == "img"]
                self.assertTrue(imagenes)
                for imagen in imagenes:
                    self.assertIn(self.producto.nombre_producto, imagen["alt"])
                    self.assertIn("Fotografía", imagen["alt"])
                    self.assertNotIn(self.reponedor.first_name, imagen["alt"])
                    self.assertNotIn(self.reponedor.username, imagen["alt"])
                if ruta == reverse(
                    "cliente:ficha_producto", args=[self.producto.pk],
                ):
                    self.assertEqual(len(imagenes), 2)
                    self.assertIn("comercial", imagenes[0]["alt"])
                    self.assertIn(
                        "referencial de ubicación", imagenes[1]["alt"],
                    )

    def test_rechazo_independiente_y_modal_con_confirmacion_identificada(self):
        self.client.force_login(self.jefatura)
        respuesta = self.client.get(reverse(
            "catalogo:revisar_producto", args=[self.pendiente.pk],
        ))
        self.assertEqual(
            respuesta.context["formulario"]["precio_inicial"].value(), 1390,
        )
        contenido = respuesta.content.decode()
        rechazo = next(formulario for formulario in contenido.split("<form")
                       if reverse("catalogo:rechazar_producto", args=[
                           self.pendiente.pk,
                       ]) in formulario)
        self.assertNotIn('name="precio_inicial"', rechazo)
        self.assertContains(
            respuesta, "El precio solo se requiere para aprobar.",
        )
        respuesta = self.client.get(reverse(
            "catalogo:ficha_gestion", args=[self.producto.pk],
        ))
        self.assertContains(
            respuesta, 'aria-labelledby="titulo-desactivacion"',
        )
        self.assertContains(
            respuesta, 'aria-describedby="detalle-desactivacion"',
        )
        self.assertContains(respuesta, 'data-bs-dismiss="modal">Cancelar')
        self.assertContains(respuesta, 'name="confirmar" value="si"')

    def test_navegacion_publica_y_paneles_conservan_accesos_funcionales(self):
        respuesta = self.client.get(reverse("cliente:inicio"))
        for nombre in ("inicio", "categorias", "ubicacion"):
            ruta = reverse("cliente:" + nombre)
            self.assertContains(respuesta, f'href="{ruta}"')
        self.assertNotContains(respuesta, 'href="/personal/')
        for usuario, nombre in (
            (self.reponedor, "personal:panel_reponedor"),
            (self.jefatura, "personal:panel_jefatura"),
        ):
            self.client.force_login(usuario)
            respuesta = self.client.get(reverse(nombre))
            self.assertContains(
                respuesta, "Selecciona la función que necesitas.",
            )
            self.assertNotContains(
                respuesta, "se habilitarán en etapas posteriores",
            )
            for _, _, enlace in respuesta.context["secciones"]:
                self.assertEqual(self.client.get(enlace).status_code, 200)
