from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from PIL import Image

from personal.models import PerfilPersonal, PersonalTienda

from .models import (
    Categoria, EstadoProducto, Pasillo, PrecioReferencia, Producto,
    Supermercado, UbicacionProducto,
)
from .test_ubicacion import imagen_ubicacion


class PlanoYPasillosPublicosPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.supermercado = Supermercado.objects.create()
        cls.pasillos = [Pasillo.objects.create(
            numero_pasillo=numero, supermercado=cls.supermercado,
        ) for numero in (10, 2, 1)]
        cls.otro_supermercado = Supermercado.objects.create(
            plano_general="catalogo/planos/otro.png",
        )
        cls.otro_pasillo = Pasillo.objects.create(
            numero_pasillo=99, supermercado=cls.otro_supermercado,
        )
        cls.categoria = Categoria.objects.create(nombre="Alimentos")
        cls.productos = [Producto.objects.create(
            codigo_barras=f"000{indice}", nombre_producto=nombre,
            marca="Marca pública", categoria=cls.categoria,
            estado=EstadoProducto.ACTIVO, precio_normal=1250 + indice,
            stock=0 if indice == 1 else 8,
        ) for indice, nombre in enumerate(("Zeta arroz", "Avena"))]
        for producto in cls.productos:
            UbicacionProducto.objects.create(
                producto=producto, pasillo=cls.pasillos[1],
            )
        cls.ocultos = []
        for estado in (
            EstadoProducto.PENDIENTE, EstadoProducto.RECHAZADO,
            EstadoProducto.INACTIVO,
        ):
            producto = Producto.objects.create(
                codigo_barras=estado, nombre_producto=f"Oculto {estado}",
                marca="Interna", categoria=cls.categoria, estado=estado,
            )
            UbicacionProducto.objects.create(
                producto=producto, pasillo=cls.pasillos[1],
            )
            cls.ocultos.append(producto)
        cls.otros = []
        for indice, pasillo in enumerate((
            cls.pasillos[0], cls.otro_pasillo, None,
        )):
            producto = Producto.objects.create(
                codigo_barras=f"otro-{indice}",
                nombre_producto=f"Otro producto {indice}", marca="Otra marca",
                categoria=cls.categoria, estado=EstadoProducto.ACTIVO,
                precio_normal=1000,
            )
            UbicacionProducto.objects.create(
                producto=producto, pasillo=pasillo,
            )
            cls.otros.append(producto)
        cls.sin_ubicacion = Producto.objects.create(
            codigo_barras="sin-ubicacion", nombre_producto="Sin ubicación",
            marca="Otra marca", categoria=cls.categoria,
            estado=EstadoProducto.ACTIVO, precio_normal=2000,
        )
        PrecioReferencia.objects.create(
            codigo_barras=cls.productos[0].codigo_barras,
            precio_referencia=987654,
        )

    def setUp(self):
        self.ubicacion = reverse("cliente:ubicacion")
        self.plano = reverse("cliente:plano")
        self.lista = reverse("cliente:pasillos")
        self.pasillo = reverse(
            "cliente:productos_pasillo", args=[self.pasillos[1].pk],
        )
        self.temporal = TemporaryDirectory(
            dir=Path(__file__).parent / "static" / "catalogo" / "planos",
            prefix="prueba_",
        )
        self.addCleanup(self.temporal.cleanup)
        self.carpeta = Path(self.temporal.name)
        media = TemporaryDirectory()
        self.addCleanup(media.cleanup)
        configuracion = override_settings(MEDIA_ROOT=media.name)
        configuracion.enable()
        self.addCleanup(configuracion.disable)

    def configurar_plano(self, nombre="recurso.png", valido=True):
        archivo = self.carpeta / nombre
        if valido:
            Image.new("RGB", (100, 80), "white").save(archivo)
        else:
            archivo.write_bytes(b"contenido invalido")
        ruta = "catalogo/planos/" + self.carpeta.name + "/" + nombre
        Supermercado.objects.filter(pk=self.supermercado.pk).update(
            plano_general=ruta,
        )
        return ruta

    def iniciar_personal(self, perfil):
        usuario = get_user_model().objects.create_user(
            username="12345678-9", first_name="Responsable privado",
        )
        PersonalTienda.objects.create(usuario=usuario, perfil=perfil)
        self.client.force_login(usuario)
        return usuario

    def test_ubicacion_publica_enlaza_plano_y_pasillos(self):
        with self.assertNumQueries(0):
            respuesta = self.client.get(self.ubicacion)
        self.assertContains(respuesta, 'href="' + self.plano + '"')
        self.assertContains(respuesta, 'href="' + self.lista + '"')
        for ruta in (reverse("cliente:inicio"), reverse("cliente:categorias")):
            self.assertContains(respuesta, 'href="' + ruta + '"')
        self.assertContains(respuesta, "Plano general")
        self.assertContains(respuesta, "Consultar por pasillo")
        self.assertNotContains(respuesta, 'href="/personal/')

    def test_plano_no_configurado_muestra_ausencia_sin_zoom(self):
        with self.assertNumQueries(1):
            respuesta = self.client.get(self.plano)
        self.assertContains(respuesta, "Mapa del supermercado")
        self.assertContains(
            respuesta, "Plano general no disponible por el momento",
        )
        self.assertNotContains(respuesta, 'id="ampliar-plano"')
        self.assertIsNone(respuesta.context["plano"])

    def test_plano_inexistente_o_imagen_invalida_muestra_ausencia(self):
        for ruta in (
            "catalogo/planos/no-existe.png",
            self.configurar_plano(valido=False),
        ):
            with self.subTest(ruta=ruta):
                Supermercado.objects.filter(pk=self.supermercado.pk).update(
                    plano_general=ruta,
                )
                respuesta = self.client.get(self.plano)
                self.assertContains(
                    respuesta, "Plano general no disponible por el momento",
                )
                self.assertNotContains(respuesta, 'id="visor-plano"')

    def test_plano_estatico_valido_y_controles_accesibles(self):
        for nombre in ("recurso.png", "recurso.jpg", "recurso.webp"):
            with self.subTest(nombre=nombre):
                ruta = self.configurar_plano(nombre)
                respuesta = self.client.get(self.plano)
                self.assertEqual(respuesta.context["plano"], ruta)
                self.assertContains(respuesta, f'src="/static/{ruta}"')
                self.assertContains(respuesta, 'id="visor-plano"')
                self.assertContains(respuesta, 'aria-controls="visor-plano"')
                self.assertContains(
                    respuesta, "Abrir imagen en tamaño original",
                )
                self.assertContains(respuesta, "catalogo/plano.js")
                self.assertNotContains(
                    respuesta, "Plano general no disponible por el momento",
                )

    def test_plano_rechaza_rutas_externas_privadas_y_manipuladas(self):
        for ruta in (
            "https://ejemplo.invalid/plano.png", "//ejemplo.invalid/mapa.png",
            "/catalogo/planos/mapa.png", "C:/privado/mapa.png",
            "catalogo/planos/../../../.env", "catalogo/planos/../mapa.png",
            "catalogo/planos/%2e%2e/mapa.png", "catalogo/planos\\mapa.png",
            "catalogo/planos//mapa.png", "catalogo/planos/./mapa.png",
            "catalogo/planos/mapa.png?otro=1", "catalogo/planos/mapa.png#x",
            "personal/personal.css",
        ):
            with self.subTest(ruta=ruta):
                Supermercado.objects.filter(pk=self.supermercado.pk).update(
                    plano_general=ruta,
                )
                with patch("catalogo.planos.finders.find") as buscar:
                    respuesta = self.client.get(self.plano)
                buscar.assert_not_called()
                self.assertIsNone(respuesta.context["plano"])

    def test_plano_no_abre_archivos_fuera_de_su_carpeta(self):
        Supermercado.objects.filter(pk=self.supermercado.pk).update(
            plano_general="catalogo/planos/recurso.png",
        )
        with (
            patch("catalogo.planos.finders.find", return_value=__file__),
            patch("catalogo.planos.Image.open") as abrir,
        ):
            respuesta = self.client.get(self.plano)
        abrir.assert_not_called()
        self.assertIsNone(respuesta.context["plano"])

    def test_plano_corrupto_renombrado_y_error_de_lectura(self):
        ruta = self.configurar_plano()
        archivo = self.carpeta / "recurso.png"
        archivo.rename(self.carpeta / "recurso.jpg")
        Supermercado.objects.filter(pk=self.supermercado.pk).update(
            plano_general=ruta.replace(".png", ".jpg"),
        )
        self.assertIsNone(self.client.get(self.plano).context["plano"])
        self.configurar_plano()
        with patch("catalogo.planos.Image.open", side_effect=OSError):
            self.assertIsNone(self.client.get(self.plano).context["plano"])

    def test_pasillos_publicos_en_orden_numerico_del_mismo_supermercado(self):
        with self.assertNumQueries(2):
            respuesta = self.client.get(self.lista)
        self.assertEqual(
            [p.numero_pasillo for p in respuesta.context["pasillos"]],
            [1, 2, 10],
        )
        self.assertNotContains(respuesta, "Pasillo 99")
        for pasillo in self.pasillos:
            self.assertContains(respuesta, reverse(
                "cliente:productos_pasillo", args=[pasillo.pk],
            ))

    def test_pasillo_solo_activos_propios_y_tarjetas_completas(self):
        self.productos[0].fotografia_comercial.save(
            "comercial.png", imagen_ubicacion("comercial.png"),
        )
        with self.assertNumQueries(3):
            respuesta = self.client.get(self.pasillo)
        self.assertEqual(
            [p.pk for p in respuesta.context["productos"]],
            [self.productos[1].pk, self.productos[0].pk],
        )
        self.assertContains(respuesta, "$1.250")
        self.assertContains(respuesta, "Marca pública")
        self.assertContains(respuesta, "Sin stock")
        self.assertContains(respuesta, reverse(
            "cliente:fotografia_producto", args=[self.productos[0].pk],
        ))
        for producto in self.ocultos + self.otros + [self.sin_ubicacion]:
            self.assertNotContains(respuesta, producto.nombre_producto)
        self.assertNotContains(respuesta, "987654")
        self.assertNotContains(respuesta, "precio_referencia")

    def test_pasillo_vacio_inexistente_o_de_otro_supermercado(self):
        respuesta = self.client.get(reverse(
            "cliente:productos_pasillo", args=[self.pasillos[2].pk],
        ))
        self.assertContains(respuesta, "No se encontraron productos")
        for identificador in (999999, self.otro_pasillo.pk):
            self.assertEqual(self.client.get(reverse(
                "cliente:productos_pasillo", args=[identificador],
            )).status_code, 404)

    def test_pasillo_con_solo_productos_ocultos_se_conserva_vacio(self):
        UbicacionProducto.objects.filter(
            producto__in=self.productos,
        ).update(pasillo=None)
        respuesta = self.client.get(self.pasillo)
        self.assertContains(respuesta, "No se encontraron productos")
        self.assertContains(self.client.get(self.lista), self.pasillo)

    def test_sin_pasillos_y_sin_supermercado_se_maneja_ausencia(self):
        UbicacionProducto.objects.all().update(pasillo=None)
        Pasillo.objects.all().delete()
        for sin_supermercado in (False, True):
            with self.subTest(sin_supermercado=sin_supermercado):
                if sin_supermercado:
                    Supermercado.objects.all().delete()
                self.assertContains(
                    self.client.get(self.lista),
                    "No hay pasillos disponibles por el momento.",
                )
                self.assertContains(
                    self.client.get(self.plano),
                    "Plano general no disponible por el momento",
                )
                self.assertEqual(
                    self.client.get(self.pasillo).status_code, 404,
                )

    def test_ficha_vuelve_al_pasillo_y_rechaza_regreso_manipulado(self):
        producto = self.productos[0]
        ficha = reverse("cliente:ficha_producto", args=[producto.pk])
        respuesta = self.client.get(self.pasillo)
        self.assertContains(
            respuesta, f'{ficha}?pasillo={self.pasillos[1].pk}',
        )
        respuesta = self.client.get(
            ficha, {"pasillo": self.pasillos[1].pk},
        )
        self.assertContains(respuesta, f'href="{self.pasillo}"')
        self.assertContains(respuesta, "Volver al pasillo")
        for valor in ("x", "9" * 100, self.otro_pasillo.pk, "//externo"):
            self.assertEqual(self.client.get(
                ficha, {"pasillo": valor},
            ).status_code, 404)

    def test_cambio_de_ubicacion_mueve_producto_entre_pasillos(self):
        usuario = self.iniciar_personal(PerfilPersonal.REPONEDOR)
        producto = self.productos[0]
        respuesta = self.client.post(reverse(
            "catalogo:actualizar_ubicacion", args=[producto.pk],
        ), {"pasillo": self.pasillos[2].pk})
        self.assertEqual(respuesta.status_code, 302)
        self.client.logout()
        self.assertNotContains(
            self.client.get(self.pasillo), producto.nombre_producto,
        )
        self.assertContains(self.client.get(reverse(
            "cliente:productos_pasillo", args=[self.pasillos[2].pk],
        )), producto.nombre_producto)
        ubicacion = UbicacionProducto.objects.get(producto=producto)
        self.assertEqual(ubicacion.ultimo_responsable, usuario.personaltienda)
        self.assertIsNotNone(ubicacion.fecha_ultima_actualizacion)

    def test_desactivar_retira_producto_y_conserva_registro(self):
        self.iniciar_personal(PerfilPersonal.JEFATURA)
        producto = self.productos[0]
        respuesta = self.client.post(reverse(
            "catalogo:desactivar_producto", args=[producto.pk],
        ), {"confirmar": "si"})
        self.assertEqual(respuesta.status_code, 302)
        self.client.logout()
        producto.refresh_from_db()
        self.assertEqual(producto.estado, EstadoProducto.INACTIVO)
        self.assertNotContains(
            self.client.get(self.pasillo), producto.nombre_producto,
        )

    def test_aprobar_y_asignar_pasillo_incorpora_producto(self):
        self.iniciar_personal(PerfilPersonal.JEFATURA)
        producto = self.ocultos[0]
        UbicacionProducto.objects.filter(producto=producto).delete()
        respuesta = self.client.post(reverse(
            "catalogo:aprobar_producto", args=[producto.pk],
        ), {"precio_inicial": "1500"})
        self.assertEqual(respuesta.status_code, 302)
        usuario = self.client.session["_auth_user_id"]
        PersonalTienda.objects.filter(usuario_id=usuario).update(
            perfil=PerfilPersonal.REPONEDOR,
        )
        respuesta = self.client.post(reverse(
            "catalogo:actualizar_ubicacion", args=[producto.pk],
        ), {"pasillo": self.pasillos[1].pk})
        self.assertEqual(respuesta.status_code, 302)
        self.client.logout()
        self.assertContains(
            self.client.get(self.pasillo), producto.nombre_producto,
        )

    def test_consultas_son_lectura_sin_exponer_informacion_privada(self):
        usuario = self.iniciar_personal(PerfilPersonal.REPONEDOR)
        self.client.logout()
        with CaptureQueriesContext(connection) as consultas:
            for ruta in (self.ubicacion, self.plano, self.lista, self.pasillo):
                respuesta = self.client.get(ruta)
                self.assertEqual(respuesta.status_code, 200)
                self.assertNotContains(respuesta, usuario.first_name)
                self.assertNotContains(respuesta, usuario.username)
                self.assertNotContains(respuesta, "987654")
        self.assertTrue(all(
            consulta["sql"].lstrip().upper().startswith("SELECT")
            for consulta in consultas
        ))
        for ruta in (self.ubicacion, self.plano, self.lista, self.pasillo):
            for metodo in ("post", "put", "delete"):
                with self.subTest(ruta=ruta, metodo=metodo):
                    self.assertEqual(getattr(self.client, metodo)(
                        ruta, {"stock": 999},
                    ).status_code, 405)

    def test_listado_mantiene_consultas_constantes_con_mas_productos(self):
        Producto.objects.bulk_create([Producto(
            codigo_barras=f"extra-{indice}", nombre_producto=f"Extra {indice}",
            marca="Marca", categoria=self.categoria,
            estado=EstadoProducto.ACTIVO, precio_normal=1000,
        ) for indice in range(30)])
        UbicacionProducto.objects.bulk_create([UbicacionProducto(
            producto_id=identificador, pasillo=self.pasillos[1],
        ) for identificador in Producto.objects.filter(
            codigo_barras__startswith="extra-",
        ).values_list("pk", flat=True)])
        with self.assertNumQueries(3):
            respuesta = self.client.get(self.pasillo)
        self.assertEqual(len(respuesta.context["productos"]), 32)
