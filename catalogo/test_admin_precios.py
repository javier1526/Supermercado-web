from io import StringIO
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from personal.models import PerfilPersonal, PersonalTienda

from .formularios_admin import TAMANO_MAXIMO_CSV
from .models import (
    Categoria, EstadoProducto, Pasillo, PrecioReferencia, Producto,
    Supermercado, UbicacionProducto,
)
from .test_importar_precios import CSV_FICTICIO, ENCABEZADO


class ImportacionPreciosAdminPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        usuarios = get_user_model().objects
        cls.administrador = usuarios.create_superuser(
            username="administrador", password=token_urlsafe(24),
        )
        cls.reponedor = usuarios.create_user(
            username="12345678-9", first_name="Camila",
        )
        cls.jefatura = usuarios.create_user(
            username="87654321-K", first_name="Javier",
        )
        cls.responsable = PersonalTienda.objects.create(
            usuario=cls.reponedor, perfil=PerfilPersonal.REPONEDOR,
        )
        PersonalTienda.objects.create(
            usuario=cls.jefatura, perfil=PerfilPersonal.JEFATURA,
        )
        cls.empleado = usuarios.create_user(username="empleado", is_staff=True)
        cls.empleado.user_permissions.set(Permission.objects.filter(
            content_type__app_label="catalogo",
            content_type__model="precioreferencia",
        ))
        cls.inactivo = usuarios.create_superuser(
            username="inactivo", password=token_urlsafe(24), is_active=False,
        )

    def setUp(self):
        self.client.force_login(self.administrador)
        self.listado = reverse("admin:catalogo_precioreferencia_changelist")
        self.importacion = reverse("admin:catalogo_precioreferencia_importar")
        self.temporales = self.enterContext(TemporaryDirectory())
        self.media = self.enterContext(TemporaryDirectory())
        self.enterContext(override_settings(MEDIA_ROOT=self.media))

    def cargar(self, contenido=None, nombre="precios.csv", follow=False):
        if contenido is None:
            contenido = ENCABEZADO + "0000000000001,1250\n"
        if isinstance(contenido, str):
            contenido = contenido.encode("utf-8")
        archivo = SimpleUploadedFile(nombre, contenido, "text/csv")
        return self.client.post(
            self.importacion, {"archivo_csv": archivo}, follow=follow,
        )

    def referencias(self):
        return dict(PrecioReferencia.objects.values_list(
            "codigo_barras", "precio_referencia",
        ))

    def comprobar_error(self, respuesta, texto):
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, texto)
        self.assertIn("archivo_csv", respuesta.context["formulario"].errors)

    def test_superusuario_consulta_listado_y_formulario_estandar(self):
        PrecioReferencia.objects.create(
            codigo_barras="0000000000001", precio_referencia=1250,
        )
        respuesta = self.client.get(self.listado)
        self.assertContains(respuesta, "Precios de referencia")
        self.assertContains(respuesta, "0000000000001")
        self.assertContains(respuesta, "$1.250")
        self.assertContains(respuesta, f'href="{self.importacion}"')
        self.assertNotContains(respuesta, 'name="action"')
        respuesta = self.client.get(self.importacion)
        self.assertContains(respuesta, 'enctype="multipart/form-data"')
        self.assertContains(respuesta, 'name="csrfmiddlewaretoken"')
        self.assertContains(respuesta, 'for="id_archivo_csv"')
        self.assertContains(respuesta, "1 MiB")
        self.assertEqual(PrecioReferencia.objects.count(), 1)

    def test_importa_12_referencias_y_reimporta_sin_duplicar(self):
        contenido = CSV_FICTICIO.read_bytes()
        respuesta = self.cargar(contenido, follow=True)
        self.assertRedirects(respuesta, self.listado)
        self.assertContains(respuesta, "nuevas (12) y existentes (0)")
        referencias = self.referencias()
        self.assertEqual(len(referencias), 12)
        self.assertEqual(referencias["0000000000001"], 1250)
        self.assertEqual(referencias["0000000000012"], 2090)
        self.assertFalse(Producto.objects.exists())
        respuesta = self.cargar(contenido, follow=True)
        self.assertContains(respuesta, "nuevas (0) y existentes (12)")
        self.assertEqual(self.referencias(), referencias)

    def test_actualiza_existentes_y_crea_nuevas(self):
        PrecioReferencia.objects.create(
            codigo_barras="001", precio_referencia=1250,
        )
        respuesta = self.cargar(
            ENCABEZADO + "001,1490\n002,1690\n", follow=True,
        )
        self.assertContains(respuesta, "nuevas (1) y existentes (1)")
        self.assertEqual(self.referencias(), {"001": 1490, "002": 1690})

    def test_acepta_bom_y_codigo_de_32_caracteres(self):
        codigo = "0" * 31 + "1"
        respuesta = self.cargar(f"\ufeff{ENCABEZADO}{codigo},2147483647\r\n")
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.referencias(), {codigo: 2147483647})

    def test_rechaza_encabezados_y_estructura_invalidos(self):
        for contenido, mensaje in (
            ("codigo,precio\n001,1000\n", "El encabezado"),
            ("precio_referencia,codigo_barras\n1000,001\n", "El encabezado"),
            (ENCABEZADO + "001,1000,otra\n", "dos columnas"),
            (ENCABEZADO + '"001,1000\n', "formato inválido"),
            (ENCABEZADO, "al menos una referencia"),
            (b"%PDF-1.4", "El encabezado"),
        ):
            with self.subTest(mensaje=mensaje):
                self.comprobar_error(self.cargar(contenido), mensaje)
                self.assertEqual(self.referencias(), {})

    def test_rechaza_codigos_duplicados_vacios_y_fuera_de_longitud(self):
        for fila, mensaje in (
            (" 001 ,3000", "se repite"),
            (",1500", "está vacío"),
            ("   ,1500", "está vacío"),
            (f"{'0' * 33},1500", "excede 32 caracteres"),
        ):
            with self.subTest(mensaje=mensaje):
                respuesta = self.cargar(ENCABEZADO + f"001,1000\n{fila}\n")
                self.comprobar_error(respuesta, mensaje)
                self.assertEqual(self.referencias(), {})

    def test_rechaza_precios_invalidos_sin_cambios_parciales(self):
        PrecioReferencia.objects.create(
            codigo_barras="001", precio_referencia=1250,
        )
        for precio in ("", "0", "-1", "15.0", "1.5", "abc", "2147483648"):
            with self.subTest(precio=precio):
                respuesta = self.cargar(
                    ENCABEZADO + f"001,1490\n002,1690\n003,{precio}\n",
                )
                self.assertEqual(respuesta.status_code, 200)
                self.assertIn(
                    "archivo_csv", respuesta.context["formulario"].errors,
                )
                self.assertEqual(self.referencias(), {"001": 1250})

    def test_archivo_obligatorio_vacio_o_codificacion_invalida(self):
        respuesta = self.client.post(self.importacion, {})
        self.comprobar_error(respuesta, "Selecciona un archivo CSV")
        self.comprobar_error(self.cargar(b""), "El archivo CSV está vacío")
        respuesta = self.cargar(b"\xff\xfe")
        self.comprobar_error(respuesta, "CSV en UTF-8")
        self.assertNotContains(respuesta, "referencias.csv")
        self.assertNotContains(respuesta, "Traceback")
        self.assertEqual(self.referencias(), {})

    def test_rechaza_archivos_grandes_antes_de_invocar_el_comando(self):
        with patch("catalogo.admin.call_command") as importar:
            respuesta = self.cargar(b"x" * (TAMANO_MAXIMO_CSV + 1))
            self.comprobar_error(respuesta, "supera el máximo de 1 MiB")
            importar.assert_not_called()
        self.assertEqual(self.referencias(), {})

    def test_limite_de_tamano_es_inclusivo(self):
        contenido = ENCABEZADO + "".join(
            f"{indice:03d},1250{' ' * 100000}\n" for indice in range(1, 11)
        )
        restante = TAMANO_MAXIMO_CSV - len(contenido)
        contenido += "011,1250" + " " * (restante - len("011,1250\n")) + "\n"
        self.assertEqual(len(contenido.encode("utf-8")), TAMANO_MAXIMO_CSV)
        respuesta = self.cargar(contenido)
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(PrecioReferencia.objects.count(), 11)

    def test_se_valida_contenido_y_no_solo_extension(self):
        respuesta = self.cargar(nombre="referencias.txt")
        self.assertEqual(respuesta.status_code, 302)
        self.comprobar_error(self.cargar(b"PK\x03\x04", nombre="precios.csv"),
                             "El encabezado")

    def test_no_se_aceptan_rutas_en_lugar_de_archivos(self):
        respuesta = self.client.post(self.importacion, {
            "archivo_csv": str(CSV_FICTICIO),
        })
        self.comprobar_error(respuesta, "Selecciona un archivo CSV")
        self.assertEqual(self.referencias(), {})

    def test_limpia_copia_temporal_en_exito_y_error_sin_guardar_en_media(self):
        rutas = []

        def directorio_temporal():
            temporal = TemporaryDirectory(dir=self.temporales)
            rutas.append(Path(temporal.name))
            return temporal

        with patch("catalogo.admin.TemporaryDirectory",
                   side_effect=directorio_temporal):
            for contenido in (None, b"\xff\xfe", b"encabezado_invalido"):
                self.cargar(contenido).wsgi_request.close()
                self.assertTrue(all(not ruta.exists() for ruta in rutas))
                self.assertEqual(list(Path(self.temporales).iterdir()), [])
                self.assertEqual(list(Path(self.media).iterdir()), [])

    def test_fallo_temporal_no_expone_detalles_ni_modifica_referencias(self):
        with patch("catalogo.admin.TemporaryDirectory",
                   side_effect=OSError("detalle privado del servidor")):
            respuesta = self.cargar()
        self.comprobar_error(
            respuesta, "No se pudo procesar el archivo temporal",
        )
        self.assertNotContains(respuesta, "detalle privado")
        self.assertEqual(self.referencias(), {})

    def test_error_al_escribir_archivo_limpia_temporales_y_no_importa(self):
        with patch(
            "catalogo.admin.TemporaryDirectory",
            side_effect=lambda: TemporaryDirectory(dir=self.temporales),
        ):
            with patch("catalogo.admin.Path.open",
                       side_effect=OSError("ruta privada del servidor")):
                respuesta = self.cargar()
        self.comprobar_error(
            respuesta, "No se pudo procesar el archivo temporal",
        )
        self.assertNotContains(respuesta, "ruta privada")
        self.assertEqual(list(Path(self.temporales).iterdir()), [])
        self.assertEqual(self.referencias(), {})

    def test_temporales_de_django_se_cierran_en_exito_y_rechazo(self):
        with override_settings(
            FILE_UPLOAD_MAX_MEMORY_SIZE=0,
            FILE_UPLOAD_TEMP_DIR=self.temporales,
        ):
            contenidos = (
                None, b"incorrecto", b"x" * (TAMANO_MAXIMO_CSV + 1),
            )
            for contenido in contenidos:
                respuesta = self.cargar(contenido)
                archivo = respuesta.wsgi_request.FILES["archivo_csv"]
                ruta = Path(archivo.temporary_file_path())
                respuesta.wsgi_request.close()
                self.assertFalse(ruta.exists())
                self.assertEqual(list(Path(self.temporales).iterdir()), [])

    def test_error_real_de_mysql_revierte_carga_y_limpia_archivos(self):
        PrecioReferencia.objects.create(
            codigo_barras="001", precio_referencia=1250,
        )
        guardar = PrecioReferencia.objects.update_or_create

        def guardar_con_error(codigo_barras, defaults):
            if codigo_barras == "002":
                defaults = {"precio_referencia": -1}
            return guardar(codigo_barras=codigo_barras, defaults=defaults)

        with patch(
            "catalogo.admin.TemporaryDirectory",
            side_effect=lambda: TemporaryDirectory(dir=self.temporales),
        ):
            with patch.object(PrecioReferencia.objects, "update_or_create",
                              side_effect=guardar_con_error):
                respuesta = self.cargar(ENCABEZADO + "001,1490\n002,1690\n")
        self.comprobar_error(respuesta, "MySQL no pudo guardar la carga")
        self.assertEqual(self.referencias(), {"001": 1250})
        self.assertEqual(list(Path(self.temporales).iterdir()), [])

    def test_importacion_no_altera_productos_ubicaciones_ni_marcas(self):
        categoria = Categoria.objects.create(nombre="Alimentos")
        producto = Producto.objects.create(
            codigo_barras="0000000000001", nombre_producto="Arroz",
            marca="Marca de prueba", categoria=categoria,
            precio_normal=1390, stock=5, estado=EstadoProducto.ACTIVO,
            fotografia_comercial="productos/anterior.png",
        )
        supermercado = Supermercado.objects.create()
        pasillo = Pasillo.objects.create(
            supermercado=supermercado, numero_pasillo=1,
        )
        UbicacionProducto.objects.create(
            producto=producto, pasillo=pasillo,
            fotografia_referencial="ubicaciones/anterior.png",
            fecha_ultima_actualizacion=timezone.now(),
            ultimo_responsable=self.responsable,
        )
        modelos = (
            Producto, UbicacionProducto, Categoria, Pasillo, Supermercado,
        )
        anterior = [list(modelo.objects.values()) for modelo in modelos]
        self.cargar()
        self.cargar(ENCABEZADO + "0000000000001,1990\n")
        self.assertEqual(self.referencias(), {"0000000000001": 1990})
        self.assertEqual(
            [list(modelo.objects.values()) for modelo in modelos], anterior,
        )
        producto.refresh_from_db()
        self.assertEqual(producto.precio_normal, 1390)

    def test_resultados_equivalentes_a_terminal(self):
        contenido = CSV_FICTICIO.read_bytes()
        self.cargar(contenido)
        esperadas = self.referencias()
        PrecioReferencia.objects.all().delete()
        call_command("importar_precios", str(CSV_FICTICIO), stdout=StringIO())
        self.assertEqual(self.referencias(), esperadas)
        respuesta = self.cargar(
            ENCABEZADO + "0000000000001,1990\n", follow=True,
        )
        self.assertContains(respuesta, "nuevas (0) y existentes (1)")

    def test_bloquea_accesos_get_y_post_de_usuarios_no_autorizados(self):
        for usuario in (
            None, self.reponedor, self.jefatura, self.empleado, self.inactivo,
        ):
            cliente = Client()
            if usuario:
                cliente.force_login(usuario)
            esperado = 403 if usuario == self.empleado else 302
            for url in (self.listado, self.importacion):
                with self.subTest(usuario=usuario, url=url):
                    self.assertEqual(cliente.get(url).status_code, esperado)
            archivo = SimpleUploadedFile(
                "precios.csv", (ENCABEZADO + "001,1250\n").encode(),
            )
            self.assertEqual(cliente.post(
                self.importacion, {"archivo_csv": archivo},
            ).status_code, esperado)
        self.assertEqual(self.referencias(), {})

    def test_csrf_obligatorio_para_importar(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.administrador)
        archivo = SimpleUploadedFile("precios.csv", CSV_FICTICIO.read_bytes())
        respuesta = cliente.post(self.importacion, {"archivo_csv": archivo})
        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(self.referencias(), {})

    def test_no_habilita_creacion_edicion_o_eliminacion_individual(self):
        referencia = PrecioReferencia.objects.create(
            codigo_barras="001", precio_referencia=1250,
        )
        for accion, argumentos in (
            ("add", []), ("change", [referencia.pk]),
            ("delete", [referencia.pk]),
        ):
            url = reverse(f"admin:catalogo_precioreferencia_{accion}",
                          args=argumentos)
            respuesta = self.client.post(url, {
                "codigo_barras": "001", "precio_referencia": 1990,
                "post": "yes",
            })
            self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(self.referencias(), {"001": 1250})

    def test_metodos_no_permitidos_no_importan(self):
        for metodo in ("put", "patch", "delete"):
            respuesta = getattr(self.client, metodo)(self.importacion)
            self.assertEqual(respuesta.status_code, 405)
        self.assertEqual(self.client.post(self.listado, {}).status_code, 405)
        self.assertEqual(self.referencias(), {})
