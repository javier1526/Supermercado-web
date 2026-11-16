from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection, connections
from django.test import (
    Client, TestCase, TransactionTestCase, override_settings,
)
from django.urls import reverse
from django.utils import timezone

from personal.models import PerfilPersonal, PersonalTienda

from .models import (
    Categoria, EstadoProducto, Pasillo, PrecioReferencia, Producto,
    Supermercado, UbicacionProducto,
)


def imagen_ubicacion(nombre="ubicacion.png", formato="PNG", color="green"):
    contenido = BytesIO()
    Image.new("RGB", (12, 12), color=color).save(contenido, format=formato)
    return SimpleUploadedFile(nombre, contenido.getvalue())


def crear_reponedor(rut):
    usuario = get_user_model().objects.create_user(
        username=rut, first_name="Personal de prueba",
    )
    PersonalTienda.objects.create(
        usuario=usuario, perfil=PerfilPersonal.REPONEDOR,
    )
    return usuario


class UbicacionPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = crear_reponedor("12345678-9")
        cls.anterior = crear_reponedor("22222222-2")
        supermercado = Supermercado.objects.create()
        cls.pasillo = Pasillo.objects.create(
            numero_pasillo=3, supermercado=supermercado,
        )
        cls.nuevo_pasillo = Pasillo.objects.create(
            numero_pasillo=4, supermercado=supermercado,
        )
        cls.producto = Producto.objects.create(
            codigo_barras="0000000000001", nombre_producto="Galletas de arroz",
            marca="DelSol",
            categoria=Categoria.objects.create(nombre="Snacks"),
            estado=EstadoProducto.ACTIVO, precio_normal=1250, stock=15,
        )
        PrecioReferencia.objects.create(
            codigo_barras=cls.producto.codigo_barras, precio_referencia=1390,
        )
        otro_producto = Producto.objects.create(
            codigo_barras="0000000000002", nombre_producto="Otro producto",
            marca="Otra marca", categoria=cls.producto.categoria,
        )
        UbicacionProducto.objects.create(producto=otro_producto)

    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.carpeta = Path(temporal.name)
        configuracion = override_settings(MEDIA_ROOT=temporal.name)
        configuracion.enable()
        self.addCleanup(configuracion.disable)
        self.ubicacion = UbicacionProducto.objects.create(
            producto=self.producto, pasillo=self.pasillo,
            fotografia_referencial=imagen_ubicacion(),
            fecha_ultima_actualizacion=timezone.now() - timedelta(days=1),
            ultimo_responsable=self.anterior.personaltienda,
        )
        self.lista = reverse("catalogo:seleccionar_ubicacion")
        self.ruta = reverse(
            "catalogo:actualizar_ubicacion", args=[self.producto.pk],
        )
        self.foto = reverse("catalogo:foto_ubicacion", args=[self.producto.pk])
        self.client.force_login(self.usuario)

    def datos_guardados(self):
        return list(UbicacionProducto.objects.order_by("pk").values())

    def archivos_guardados(self):
        return sorted(p.name for p in self.carpeta.rglob("*") if p.is_file())

    def test_panel_busqueda_y_seleccion_muestran_solo_activos(self):
        self.assertContains(
            self.client.get(reverse("personal:panel_reponedor")),
            f'href="{self.lista}"',
        )
        for consulta in ("", "gAlLeTaS", "delsol"):
            respuesta = self.client.get(self.lista, {"consulta": consulta})
            self.assertEqual(list(respuesta.context["productos"]), [
                self.producto,
            ])
            self.assertContains(respuesta, f'href="{self.ruta}"')
        self.assertContains(
            self.client.get(self.lista, {"consulta": "inexistente"}),
            "No se encontraron productos activos.",
        )
        respuesta = self.client.get(self.ruta)
        self.assertContains(respuesta, "Galletas de arroz")
        self.assertContains(respuesta, "DelSol")
        self.assertEqual(
            respuesta.context["formulario"]["pasillo"].value(),
            self.pasillo.pk,
        )
        self.assertContains(respuesta, f'src="{self.foto}"')

    def test_cp011_cambiar_pasillo_conserva_foto_y_registra_responsable(self):
        antes = self.ubicacion.fotografia_referencial.name
        instante = timezone.now()
        with patch("catalogo.ubicacion.timezone.now", return_value=instante):
            respuesta = self.client.post(
                self.ruta, {"pasillo": self.nuevo_pasillo.pk}, follow=True,
            )
        self.assertRedirects(respuesta, self.ruta)
        self.assertContains(respuesta, "Ubicación actualizada correctamente.")
        self.ubicacion.refresh_from_db()
        self.assertEqual(self.ubicacion.pasillo, self.nuevo_pasillo)
        self.assertEqual(self.ubicacion.fotografia_referencial.name, antes)
        self.assertEqual(self.ubicacion.fecha_ultima_actualizacion, instante)
        self.assertTrue(timezone.is_aware(instante))
        self.assertEqual(
            self.ubicacion.ultimo_responsable, self.usuario.personaltienda,
        )
        self.assertEqual(
            self.ubicacion.pasillo.supermercado_id,
            self.nuevo_pasillo.supermercado_id,
        )
        self.assertEqual(
            self.client.get(self.ruta).context["ubicacion"].pasillo,
            self.nuevo_pasillo,
        )

    def test_cambiar_solo_foto_y_retirar_anterior_al_confirmar(self):
        anterior = self.ubicacion.fotografia_referencial.path
        for nombre, formato in (("nueva.jpg", "JPEG"), ("otra.jpeg", "JPEG")):
            with self.captureOnCommitCallbacks(execute=True):
                respuesta = self.client.post(self.ruta, {
                    "fotografia_referencial": imagen_ubicacion(
                        nombre, formato, color="blue" if "nueva" in nombre
                        else "red",
                    ),
                })
            self.assertRedirects(respuesta, self.ruta)
            self.ubicacion.refresh_from_db()
            self.assertEqual(self.ubicacion.pasillo, self.pasillo)
            self.assertFalse(Path(anterior).exists())
            self.assertTrue(
                Path(self.ubicacion.fotografia_referencial.path).is_file(),
            )
            anterior = self.ubicacion.fotografia_referencial.path
        self.assertTrue(
            self.ubicacion.fotografia_referencial.name.startswith(
                "ubicaciones/",
            ),
        )
        respuesta = self.client.get(self.foto)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta["Content-Security-Policy"], "sandbox")
        self.assertTrue(b"".join(respuesta.streaming_content))

    def test_cambiar_ambos_datos_y_primera_ubicacion(self):
        self.ubicacion.delete()
        respuesta = self.client.get(self.ruta)
        self.assertContains(respuesta, "Sin pasillo definido")
        self.assertContains(respuesta, "Todavía no hay una fotografía")
        self.assertEqual(self.client.get(self.foto).status_code, 404)
        respuesta = self.client.post(self.ruta, {
            "pasillo": self.nuevo_pasillo.pk,
            "fotografia_referencial": imagen_ubicacion(
                "nueva.png", color="red",
            ),
        })
        self.assertRedirects(respuesta, self.ruta)
        ubicacion = UbicacionProducto.objects.get(producto=self.producto)
        self.assertEqual(ubicacion.pasillo, self.nuevo_pasillo)
        self.assertTrue(Path(ubicacion.fotografia_referencial.path).exists())
        self.assertIsNotNone(ubicacion.fecha_ultima_actualizacion)
        self.assertEqual(
            ubicacion.ultimo_responsable, self.usuario.personaltienda,
        )

    def test_primera_ubicacion_admite_solo_foto_o_solo_pasillo(self):
        self.ubicacion.delete()
        for datos in (
            {"pasillo": self.pasillo.pk},
            {"fotografia_referencial": imagen_ubicacion("primera.png")},
        ):
            respuesta = self.client.post(self.ruta, datos)
            self.assertRedirects(respuesta, self.ruta)
            ubicacion = UbicacionProducto.objects.get(producto=self.producto)
            self.assertEqual(bool(ubicacion.pasillo), "pasillo" in datos)
            self.assertEqual(
                bool(ubicacion.fotografia_referencial),
                "fotografia_referencial" in datos,
            )
            ubicacion.delete()

    def test_cp012_sin_cambios_conserva_exactamente_fecha_y_responsable(self):
        antes = self.datos_guardados()
        archivos = self.archivos_guardados()
        for datos in ({}, {"pasillo": self.pasillo.pk}):
            respuesta = self.client.post(self.ruta, datos, follow=True)
            self.assertContains(respuesta, "No hubo cambios en la ubicación.")
            self.assertEqual(self.datos_guardados(), antes)
            self.assertEqual(self.archivos_guardados(), archivos)

    def test_foto_identica_con_otro_nombre_no_registra_un_cambio(self):
        antes = self.datos_guardados()
        archivos = self.archivos_guardados()
        respuesta = self.client.post(self.ruta, {
            "fotografia_referencial": imagen_ubicacion("otro_nombre.png"),
        }, follow=True)
        self.assertContains(respuesta, "No hubo cambios en la ubicación.")
        self.assertEqual(self.datos_guardados(), antes)
        self.assertEqual(self.archivos_guardados(), archivos)

    def test_sin_datos_no_crea_ubicacion_ni_marcas(self):
        self.ubicacion.delete()
        antes = self.datos_guardados()
        respuesta = self.client.post(self.ruta, {"pasillo": ""}, follow=True)
        self.assertContains(respuesta, "No hubo cambios en la ubicación.")
        self.assertEqual(self.datos_guardados(), antes)

    def test_pasillo_opcional_puede_quedar_vacio_conservando_fotografia(self):
        anterior = self.ubicacion.fotografia_referencial.name
        self.client.post(self.ruta, {"pasillo": ""})
        self.ubicacion.refresh_from_db()
        self.assertIsNone(self.ubicacion.pasillo)
        self.assertEqual(self.ubicacion.fotografia_referencial.name, anterior)
        self.assertEqual(
            self.ubicacion.ultimo_responsable, self.usuario.personaltienda,
        )

    def test_pasillos_inexistentes_no_guardan_fotografia_ni_cambios(self):
        antes = self.datos_guardados()
        archivos = self.archivos_guardados()
        for pasillo in ("999999", "texto", "-1"):
            respuesta = self.client.post(self.ruta, {
                "pasillo": pasillo,
                "fotografia_referencial": imagen_ubicacion(color="red"),
            })
            self.assertIn("pasillo", respuesta.context["formulario"].errors)
            self.assertEqual(
                respuesta.context["formulario"]["pasillo"].value(), pasillo,
            )
            self.assertEqual(self.datos_guardados(), antes)
            self.assertEqual(self.archivos_guardados(), archivos)

    def test_cp017_formatos_invalidos_y_camuflados_no_modifican_datos(self):
        antes = self.datos_guardados()
        archivos = self.archivos_guardados()
        invalidos = [
            SimpleUploadedFile("documento.pdf", b"%PDF-1.4 documento"),
            SimpleUploadedFile("imagen.png", b"no es imagen"),
            SimpleUploadedFile("corrupta.jpg", b"\xff\xd8\xff"),
            imagen_ubicacion("imagen.gif", "GIF"),
            imagen_ubicacion("camuflada.jpg", "GIF"),
            imagen_ubicacion("imagen.pdf", "PNG"),
            SimpleUploadedFile(
                "truncada.jpg",
                imagen_ubicacion("valida.jpg", "JPEG").read()[:-2],
            ),
        ]
        for archivo in invalidos:
            with self.subTest(nombre=archivo.name):
                respuesta = self.client.post(self.ruta, {
                    "pasillo": self.nuevo_pasillo.pk,
                    "fotografia_referencial": archivo,
                })
                self.assertIn(
                    "fotografia_referencial",
                    respuesta.context["formulario"].errors,
                )
                self.assertEqual(self.datos_guardados(), antes)
                self.assertEqual(self.archivos_guardados(), archivos)

    def test_cp016_limite_un_mb_y_rechazo_sobre_el_limite(self):
        contenido = imagen_ubicacion("limite.jpg", "JPEG").read()
        for tamano, valido in ((1024 * 1024 + 1, False), (1024 * 1024, True)):
            antes = self.datos_guardados()
            respuesta = self.client.post(self.ruta, {
                "fotografia_referencial": SimpleUploadedFile(
                    "limite.jpg",
                    contenido + b"\0" * (tamano - len(contenido)),
                ),
            })
            if valido:
                self.assertRedirects(respuesta, self.ruta)
                self.ubicacion.refresh_from_db()
                self.assertEqual(
                    self.ubicacion.fotografia_referencial.size, tamano,
                )
            else:
                self.assertContains(respuesta, "La fotografía supera 1 MB.")
                self.assertEqual(self.datos_guardados(), antes)

    def test_post_manipulado_solo_modifica_la_ubicacion_del_producto(self):
        producto = Producto.objects.values().get(pk=self.producto.pk)
        referencias = list(PrecioReferencia.objects.values())
        otras = list(UbicacionProducto.objects.exclude(
            pk=self.ubicacion.pk,
        ).values())
        instante = timezone.now()
        with patch("catalogo.ubicacion.timezone.now", return_value=instante):
            self.client.post(self.ruta, {
                "pasillo": self.nuevo_pasillo.pk, "stock": "999",
                "precio_normal": "1", "estado": "inactivo",
                "codigo_barras": "otra", "categoria": "999",
                "fecha_ultima_actualizacion": "2020-01-01",
                "ultimo_responsable": self.anterior.personaltienda.pk,
                "nombre_producto": "Otro nombre",
            })
        self.assertEqual(
            Producto.objects.values().get(pk=self.producto.pk), producto,
        )
        self.assertEqual(list(PrecioReferencia.objects.values()), referencias)
        self.assertEqual(list(UbicacionProducto.objects.exclude(
            pk=self.ubicacion.pk,
        ).values()), otras)
        self.ubicacion.refresh_from_db()
        self.assertEqual(self.ubicacion.fecha_ultima_actualizacion, instante)
        self.assertEqual(
            self.ubicacion.ultimo_responsable, self.usuario.personaltienda,
        )

    def test_estado_se_verifica_de_nuevo_al_guardar(self):
        antes = self.datos_guardados()
        for estado in (
            EstadoProducto.PENDIENTE, EstadoProducto.RECHAZADO,
            EstadoProducto.INACTIVO,
        ):
            Producto.objects.filter(pk=self.producto.pk).update(
                estado=EstadoProducto.ACTIVO,
            )
            self.assertEqual(self.client.get(self.ruta).status_code, 200)
            Producto.objects.filter(pk=self.producto.pk).update(estado=estado)
            for metodo in (self.client.get, self.client.post):
                respuesta = metodo(self.ruta, {
                    "pasillo": self.nuevo_pasillo.pk,
                })
                self.assertRedirects(respuesta, self.lista)
            self.assertEqual(self.client.get(self.foto).status_code, 404)
            self.assertEqual(self.datos_guardados(), antes)

    def test_get_y_post_requieren_reponedor_autorizado(self):
        jefatura = get_user_model().objects.create_user(
            username="87654321-K", first_name="Jefatura",
        )
        PersonalTienda.objects.create(
            usuario=jefatura, perfil=PerfilPersonal.JEFATURA,
        )
        sin_perfil = get_user_model().objects.create_user(
            username="11111111-1",
        )
        inactivo = crear_reponedor("33333333-3")
        inactivo.is_active = False
        inactivo.save(update_fields=["is_active"])
        antes = self.datos_guardados()
        for usuario, estado in (
            (None, 302), (jefatura, 403), (sin_perfil, 403), (inactivo, 302),
        ):
            cliente = Client(enforce_csrf_checks=True)
            if usuario:
                cliente.force_login(usuario)
            cliente.get(reverse("personal:acceso"))
            token = cliente.cookies["csrftoken"].value
            for ruta in (self.lista, self.ruta, self.foto):
                self.assertEqual(cliente.get(ruta).status_code, estado)
            self.assertEqual(cliente.post(
                self.ruta, {"pasillo": self.nuevo_pasillo.pk},
                HTTP_X_CSRFTOKEN=token,
            ).status_code, estado)
            self.assertEqual(self.datos_guardados(), antes)

    def test_csrf_y_metodos_no_permitidos(self):
        antes = self.datos_guardados()
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.usuario)
        cliente.get(self.ruta)
        for cabeceras in ({}, {"HTTP_X_CSRFTOKEN": "incorrecto"}):
            self.assertEqual(cliente.post(
                self.ruta, {"pasillo": self.nuevo_pasillo.pk}, **cabeceras,
            ).status_code, 403)
        self.assertEqual(self.client.post(self.lista).status_code, 405)
        self.assertEqual(self.client.post(self.foto).status_code, 405)
        self.assertEqual(self.client.put(self.ruta).status_code, 405)
        self.assertEqual(self.datos_guardados(), antes)

    def test_fallo_mysql_revierta_cambios_y_elimina_solo_archivo_nuevo(self):
        antes = self.datos_guardados()
        archivos = self.archivos_guardados()
        guardar = UbicacionProducto.save

        def fallar_despues_de_guardar(ubicacion, *args, **kwargs):
            guardar(ubicacion, *args, **kwargs)
            raise DatabaseError

        with patch.object(
            UbicacionProducto, "save", new=fallar_despues_de_guardar,
        ):
            respuesta = self.client.post(self.ruta, {
                "pasillo": self.nuevo_pasillo.pk,
                "fotografia_referencial": imagen_ubicacion(color="red"),
            })
        self.assertContains(respuesta, "No se pudo guardar la ubicación.")
        self.assertEqual(self.datos_guardados(), antes)
        self.assertEqual(self.archivos_guardados(), archivos)
        self.assertContains(respuesta, f'src="{self.foto}"')

    def test_fallo_de_almacenamiento_conserva_fotografia_y_ubicacion(self):
        antes = self.datos_guardados()
        archivos = self.archivos_guardados()
        almacenamiento = self.ubicacion.fotografia_referencial.storage
        guardar = almacenamiento.save

        def fallar_despues_de_escribir(*args, **kwargs):
            guardar(*args, **kwargs)
            raise OSError

        with patch.object(
            almacenamiento, "save", side_effect=fallar_despues_de_escribir,
        ):
            respuesta = self.client.post(self.ruta, {
                "pasillo": self.nuevo_pasillo.pk,
                "fotografia_referencial": imagen_ubicacion(color="blue"),
            })
        self.assertContains(respuesta, "No se pudo guardar la ubicación.")
        self.assertEqual(self.datos_guardados(), antes)
        self.assertEqual(self.archivos_guardados(), archivos)


class UbicacionSimultaneaPruebas(TransactionTestCase):
    def test_fallo_al_confirmar_transaccion_elimina_el_archivo_nuevo(self):
        with (
            TemporaryDirectory() as carpeta,
            override_settings(MEDIA_ROOT=carpeta),
        ):
            usuario = crear_reponedor("12345678-9")
            producto = Producto.objects.create(
                codigo_barras="001", nombre_producto="Galletas",
                marca="DelSol",
                categoria=Categoria.objects.create(nombre="Snacks"),
                estado=EstadoProducto.ACTIVO, precio_normal=1250,
            )
            cliente = Client()
            cliente.force_login(usuario)
            with patch.object(connection, "commit", side_effect=DatabaseError):
                respuesta = cliente.post(reverse(
                    "catalogo:actualizar_ubicacion", args=[producto.pk],
                ), {"fotografia_referencial": imagen_ubicacion()})
            self.assertContains(respuesta, "No se pudo guardar la ubicación.")
            self.assertFalse(UbicacionProducto.objects.exists())
            self.assertFalse(list(Path(carpeta).rglob("*.png")))

    def test_primera_ubicacion_simultanea_es_coherente(self):
        with (
            TemporaryDirectory() as carpeta,
            override_settings(MEDIA_ROOT=carpeta),
        ):
            usuarios = [crear_reponedor(rut) for rut in (
                "12345678-9", "22222222-2",
            )]
            supermercado = Supermercado.objects.create()
            pasillos = [Pasillo.objects.create(
                numero_pasillo=numero, supermercado=supermercado,
            ) for numero in (3, 4)]
            producto = Producto.objects.create(
                codigo_barras="001", nombre_producto="Galletas",
                marca="DelSol",
                categoria=Categoria.objects.create(nombre="Snacks"),
                estado=EstadoProducto.ACTIVO, precio_normal=1250,
            )
            barrera = Barrier(2)

            def modificar(indice):
                try:
                    cliente = Client()
                    cliente.force_login(usuarios[indice])
                    barrera.wait(timeout=10)
                    return cliente.post(reverse(
                        "catalogo:actualizar_ubicacion", args=[producto.pk],
                    ), {
                        "pasillo": pasillos[indice].pk,
                        "fotografia_referencial": imagen_ubicacion(
                            color=("red", "blue")[indice],
                        ),
                    }).status_code
                finally:
                    connections.close_all()

            with ThreadPoolExecutor(max_workers=2) as ejecutor:
                self.assertEqual(
                    list(ejecutor.map(modificar, (0, 1))), [302, 302],
                )
            self.assertEqual(UbicacionProducto.objects.count(), 1)
            ubicacion = UbicacionProducto.objects.get(producto=producto)
            indice = [pasillo.pk for pasillo in pasillos].index(
                ubicacion.pasillo_id,
            )
            self.assertEqual(
                ubicacion.ultimo_responsable, usuarios[indice].personaltienda,
            )
            with Image.open(ubicacion.fotografia_referencial.path) as imagen:
                self.assertEqual(imagen.getpixel((0, 0)), (
                    (255, 0, 0), (0, 0, 255),
                )[indice])
            self.assertEqual(len(list(Path(carpeta).rglob("*.png"))), 1)
