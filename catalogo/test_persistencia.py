from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import (
    DatabaseError, IntegrityError, connection, connections, transaction,
)
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from personal.models import PerfilPersonal, PersonalTienda

from .models import (
    Categoria, EstadoProducto, Pasillo, PrecioReferencia, Producto,
    Supermercado, UbicacionProducto,
)
from .test_ubicacion import imagen_ubicacion
from .ubicacion import guardar_ubicacion


class PersistenciaPruebas(TransactionTestCase):
    def setUp(self):
        temporal = TemporaryDirectory()
        self.addCleanup(temporal.cleanup)
        self.media = Path(temporal.name)
        configuracion = override_settings(MEDIA_ROOT=temporal.name)
        configuracion.enable()
        self.addCleanup(configuracion.disable)
        self.categoria = Categoria.objects.create(nombre="Snacks")
        supermercado = Supermercado.objects.create()
        self.pasillos = [Pasillo.objects.create(
            numero_pasillo=numero, supermercado=supermercado,
        ) for numero in (3, 4)]
        self.clientes = {}
        self.personales = {}
        for rut, perfil in (
            ("12345678-9", PerfilPersonal.REPONEDOR),
            ("87654321-K", PerfilPersonal.JEFATURA),
        ):
            usuario = get_user_model().objects.create_user(
                username=rut, first_name="Personal de prueba",
            )
            self.personales[perfil] = PersonalTienda.objects.create(
                usuario=usuario, perfil=perfil,
            )
            cliente = Client()
            cliente.force_login(usuario)
            self.clientes[perfil] = cliente
        self.reponedor = self.clientes[PerfilPersonal.REPONEDOR]
        self.jefatura = self.clientes[PerfilPersonal.JEFATURA]
        self.publico = Client()
        self.datos = {
            "codigo_barras": "0000000000001",
            "nombre_producto": "Galletas de arroz 100 g", "marca": "DelSol",
            "categoria": self.categoria.pk,
            "descripcion": "Descripción comercial de prueba.",
        }

    def estado_guardado(self):
        archivos = {}
        for archivo in self.media.rglob("*"):
            if archivo.is_file():
                nombre = archivo.relative_to(self.media).as_posix()
                archivos[nombre] = archivo.read_bytes()
        return {
            "productos": list(Producto.objects.order_by("pk").values()),
            "ubicaciones": list(
                UbicacionProducto.objects.order_by("pk").values(),
            ),
            "referencias": list(
                PrecioReferencia.objects.order_by("pk").values(),
            ),
            "archivos": archivos,
        }

    def crear_activo_con_ubicacion(self):
        producto = Producto.objects.create(
            codigo_barras=self.datos["codigo_barras"],
            nombre_producto=self.datos["nombre_producto"],
            marca=self.datos["marca"], categoria=self.categoria,
            descripcion=self.datos["descripcion"], precio_normal=1390,
            stock=15, estado=EstadoProducto.ACTIVO,
            fotografia_comercial=imagen_ubicacion("comercial.png"),
        )
        ubicacion = UbicacionProducto.objects.create(
            producto=producto, pasillo=self.pasillos[0],
            fotografia_referencial=imagen_ubicacion("anterior.png"),
            fecha_ultima_actualizacion=timezone.now(),
            ultimo_responsable=self.personales[PerfilPersonal.REPONEDOR],
        )
        PrecioReferencia.objects.create(
            codigo_barras=producto.codigo_barras, precio_referencia=1250,
        )
        return producto, ubicacion

    def importar_referencia(self, precio):
        with TemporaryDirectory() as temporal:
            ruta = Path(temporal) / "precios.csv"
            ruta.write_text(
                "codigo_barras,precio_referencia\n"
                f"{self.datos['codigo_barras']},{precio}\n",
                encoding="utf-8",
            )
            call_command("importar_precios", str(ruta), stdout=StringIO())

    def leer_fotografia(self, cliente, nombre, producto):
        respuesta = cliente.get(reverse(nombre, args=[producto.pk]))
        self.assertEqual(respuesta.status_code, 200)
        contenido = b"".join(respuesta.streaming_content)
        respuesta.close()
        return contenido

    def test_fallo_almacenamiento_comercial_no_deja_fila_ni_archivo(self):
        existente = Producto.objects.create(
            codigo_barras="anterior", nombre_producto="Otro producto",
            marca="Otra marca", categoria=self.categoria,
            fotografia_comercial=imagen_ubicacion("anterior.png"),
        )
        almacenamiento = existente.fotografia_comercial.storage
        guardar = almacenamiento.save
        antes = self.estado_guardado()

        def fallar_despues_de_escribir(*args, **kwargs):
            guardar(*args, **kwargs)
            raise OSError("Fallo controlado después de escribir el archivo")

        with patch.object(
            almacenamiento, "save", side_effect=fallar_despues_de_escribir,
        ):
            respuesta = self.reponedor.post(reverse(
                "catalogo:registrar_producto",
            ), {
                **self.datos,
                "fotografia_comercial": imagen_ubicacion("comercial.png"),
            })
        self.assertContains(respuesta, "No se pudo guardar la fotografía.")
        self.assertEqual(self.estado_guardado(), antes)

    def test_flujo_confirmado_persiste_hasta_la_consulta_publica_y_baja(self):
        self.assertTrue(connection.get_autocommit())
        self.importar_referencia(1250)
        comercial = imagen_ubicacion("comercial.png")
        contenido_comercial = comercial.read()
        comercial.seek(0)
        respuesta = self.reponedor.post(reverse(
            "catalogo:registrar_producto",
        ), {**self.datos, "fotografia_comercial": comercial})
        self.assertEqual(respuesta.status_code, 302)
        producto = Producto.objects.get(
            codigo_barras=self.datos["codigo_barras"],
        )
        self.assertEqual(producto.estado, EstadoProducto.PENDIENTE)
        self.assertIsNone(producto.precio_normal)
        self.assertEqual(producto.stock, 0)
        self.assertEqual(producto.codigo_barras, "0000000000001")
        self.assertEqual(producto.descripcion, self.datos["descripcion"])
        self.assertEqual(self.leer_fotografia(
            self.jefatura, "catalogo:foto_producto_pendiente", producto,
        ), contenido_comercial)
        ficha = reverse("cliente:ficha_producto", args=[producto.pk])
        self.assertEqual(self.publico.get(ficha).status_code, 404)
        revision = self.jefatura.get(reverse(
            "catalogo:revisar_producto", args=[producto.pk],
        ))
        self.assertEqual(
            revision.context["formulario"]["precio_inicial"].value(), 1250,
        )
        self.assertEqual(self.jefatura.post(reverse(
            "catalogo:aprobar_producto", args=[producto.pk],
        ), {"precio_inicial": "1390"}).status_code, 302)
        producto.refresh_from_db()
        self.assertEqual(producto.estado, EstadoProducto.ACTIVO)
        self.assertEqual(producto.precio_normal, 1390)
        self.assertEqual(self.reponedor.post(reverse(
            "catalogo:actualizar_stock", args=[producto.pk],
        ), {"stock": "27"}).status_code, 302)
        producto.refresh_from_db()
        self.assertEqual(producto.stock, 27)
        ruta_ubicacion = reverse(
            "catalogo:actualizar_ubicacion", args=[producto.pk],
        )
        self.assertEqual(self.reponedor.post(ruta_ubicacion, {
            "pasillo": self.pasillos[0].pk,
        }).status_code, 302)
        ubicacion = UbicacionProducto.objects.get(producto=producto)
        fecha_pasillo = ubicacion.fecha_ultima_actualizacion
        referencial = imagen_ubicacion("referencial.png", color="blue")
        contenido_referencial = referencial.read()
        referencial.seek(0)
        self.assertEqual(self.reponedor.post(ruta_ubicacion, {
            "fotografia_referencial": referencial,
        }).status_code, 302)
        ubicacion.refresh_from_db()
        self.assertEqual(ubicacion.pasillo, self.pasillos[0])
        self.assertGreater(ubicacion.fecha_ultima_actualizacion, fecha_pasillo)
        self.assertTrue(timezone.is_aware(
            ubicacion.fecha_ultima_actualizacion,
        ))
        self.assertEqual(
            ubicacion.ultimo_responsable,
            self.personales[PerfilPersonal.REPONEDOR],
        )
        antes_ubicacion = UbicacionProducto.objects.values().get(
            producto=producto,
        )
        self.assertEqual(self.jefatura.post(reverse(
            "catalogo:actualizar_precio", args=[producto.pk],
        ), {"precio_normal": "1450"}).status_code, 302)
        self.assertEqual(
            UbicacionProducto.objects.values().get(producto=producto),
            antes_ubicacion,
        )
        consulta = self.publico.get(ficha)
        self.assertContains(consulta, "$1.450")
        self.assertContains(consulta, "Stock: 27 unidades disponibles")
        self.assertContains(consulta, "Pasillo 3")
        self.assertContains(consulta, self.datos["descripcion"])
        self.assertContains(consulta, timezone.localtime(
            ubicacion.fecha_ultima_actualizacion,
        ).strftime("%d/%m/%Y %H:%M"))
        self.assertEqual(consulta.context["producto"].precio_normal, 1450)
        self.assertEqual(self.leer_fotografia(
            self.publico, "cliente:fotografia_producto", producto,
        ), contenido_comercial)
        self.assertEqual(self.leer_fotografia(
            self.publico, "cliente:fotografia_ubicacion", producto,
        ), contenido_referencial)
        for nombre, clave in (
            ("catalogo:actualizar_stock", "producto"),
            ("catalogo:ficha_gestion", "producto"),
        ):
            cliente = self.jefatura
            self.assertEqual(cliente.get(reverse(
                nombre, args=[producto.pk],
            )).context[clave].stock, 27)
        consulta_ubicacion = self.reponedor.get(ruta_ubicacion)
        ubicacion_consultada = consulta_ubicacion.context["ubicacion"]
        self.assertEqual(
            ubicacion_consultada.fotografia_referencial.name,
            ubicacion.fotografia_referencial.name,
        )
        self.assertEqual(self.reponedor.post(ruta_ubicacion, {
            "pasillo": self.pasillos[1].pk,
        }).status_code, 302)
        ubicacion.refresh_from_db()
        self.assertEqual(
            ubicacion.fotografia_referencial.name,
            antes_ubicacion["fotografia_referencial"],
        )
        resultados = ((self.pasillos[0], 0), (self.pasillos[1], 1))
        for pasillo, cantidad in resultados:
            respuesta = self.publico.get(reverse(
                "cliente:productos_pasillo", args=[pasillo.pk],
            ))
            self.assertEqual(len(respuesta.context["productos"]), cantidad)
        antes = self.estado_guardado()
        self.assertEqual(self.reponedor.post(ruta_ubicacion, {
            "fotografia_referencial": imagen_ubicacion(
                "identica.png", color="blue",
            ),
        }).status_code, 302)
        self.assertEqual(self.estado_guardado(), antes)
        self.importar_referencia(1490)
        self.assertEqual(PrecioReferencia.objects.get(
            codigo_barras=producto.codigo_barras,
        ).precio_referencia, 1490)
        self.assertEqual(self.publico.get(
            ficha,
        ).context["producto"].precio_normal, 1450)
        rutas_listados = (
            reverse("cliente:inicio") + "?consulta=galletas",
            reverse("cliente:productos_categoria", args=[self.categoria.pk]),
            reverse("cliente:productos_pasillo", args=[self.pasillos[1].pk]),
        )
        for ruta in rutas_listados:
            self.assertEqual([p.pk for p in self.publico.get(
                ruta,
            ).context["productos"]], [producto.pk])
        inicio = self.publico.get(reverse("cliente:inicio"))
        self.assertEqual(
            [p.pk for p in inicio.context["categorias"][0].productos_inicio],
            [producto.pk],
        )
        antes = self.estado_guardado()
        self.assertEqual(self.jefatura.post(reverse(
            "catalogo:desactivar_producto", args=[producto.pk],
        ), {"confirmar": "si"}).status_code, 302)
        antes["productos"][0]["estado"] = EstadoProducto.INACTIVO
        self.assertEqual(self.estado_guardado(), antes)
        for ruta in rutas_listados:
            self.assertEqual(list(self.publico.get(
                ruta,
            ).context["productos"]), [])
        inicio = self.publico.get(reverse("cliente:inicio"))
        self.assertEqual(inicio.context["categorias"][0].productos_inicio, [])
        for nombre in (
            "cliente:ficha_producto", "cliente:fotografia_producto",
            "cliente:fotografia_ubicacion",
        ):
            self.assertEqual(self.publico.get(reverse(
                nombre, args=[producto.pk],
            )).status_code, 404)

    def test_invalidos_conservan_registros_archivos_y_ficha_publica(self):
        producto, _ = self.crear_activo_con_ubicacion()
        antes = self.estado_guardado()
        ficha = reverse("cliente:ficha_producto", args=[producto.pk])
        contenido = self.publico.get(ficha).content
        for nombre, cliente, campo, invalidos in (
            ("catalogo:actualizar_stock", self.reponedor, "stock",
             ("-1", "15.0", "abc", "", "2147483648")),
            ("catalogo:actualizar_precio", self.jefatura, "precio_normal",
             ("0", "-1", "1250.0", "abc", "", "2147483648")),
        ):
            for valor in invalidos:
                with self.subTest(operacion=nombre, valor=valor):
                    respuesta = cliente.post(reverse(
                        nombre, args=[producto.pk],
                    ), {campo: valor})
                    self.assertIn(
                        campo, respuesta.context["formulario"].errors,
                    )
                    self.assertEqual(self.estado_guardado(), antes)
                    self.assertEqual(
                        self.publico.get(ficha).content, contenido,
                    )

    def test_reemplazo_retira_anterior_solo_despues_del_commit_real(self):
        producto, ubicacion = self.crear_activo_con_ubicacion()
        anterior = ubicacion.fotografia_referencial.path
        with transaction.atomic():
            _, _, formulario, cambio = guardar_ubicacion(
                producto.pk, {}, {
                    "fotografia_referencial": imagen_ubicacion(
                        "nueva.png", color="blue",
                    ),
                }, self.personales[PerfilPersonal.REPONEDOR],
            )
            self.assertTrue(formulario.is_valid())
            self.assertTrue(cambio)
            ubicacion.refresh_from_db()
            nueva = ubicacion.fotografia_referencial.path
            self.assertNotEqual(anterior, nueva)
            self.assertTrue(Path(anterior).is_file())
            self.assertTrue(Path(nueva).is_file())
        ubicacion.refresh_from_db()
        self.assertEqual(ubicacion.fotografia_referencial.path, nueva)
        self.assertFalse(Path(anterior).exists())
        self.assertTrue(Path(nueva).is_file())

    def test_error_mysql_real_revierte_escrituras_de_producto_y_archivo(self):
        producto, _ = self.crear_activo_con_ubicacion()
        guardar = Producto.save

        def guardar_y_violar_restriccion(instancia, *args, **kwargs):
            guardar(instancia, *args, **kwargs)
            if kwargs.get("update_fields"):
                PrecioReferencia.objects.create(
                    codigo_barras="fallo-controlado", precio_referencia=-1,
                )

        pendiente = Producto.objects.create(
            codigo_barras="pendiente", nombre_producto="Otro producto",
            marca="Otra marca", categoria=self.categoria,
        )
        operaciones = (
            ("catalogo:registrar_producto", self.reponedor, [], {
                **self.datos, "codigo_barras": "nuevo",
                "nombre_producto": "Galletas nuevas",
                "fotografia_comercial": imagen_ubicacion("nueva.png"),
            }, False),
            ("catalogo:actualizar_stock", self.reponedor, [producto.pk],
             {"stock": "30"}, False),
            ("catalogo:aprobar_producto", self.jefatura, [pendiente.pk],
             {"precio_inicial": "1450"}, False),
            ("catalogo:rechazar_producto", self.jefatura, [pendiente.pk],
             {}, False),
            ("catalogo:actualizar_precio", self.jefatura, [producto.pk],
             {"precio_normal": "1450"}, True),
            ("catalogo:desactivar_producto", self.jefatura, [producto.pk],
             {"confirmar": "si"}, True),
        )
        antes = self.estado_guardado()
        for nombre, cliente, argumentos, datos, controlada in operaciones:
            with self.subTest(operacion=nombre), patch.object(
                Producto, "save", new=guardar_y_violar_restriccion,
            ):
                ruta = reverse(nombre, args=argumentos)
                if controlada:
                    respuesta = cliente.post(ruta, datos)
                    self.assertContains(respuesta, "No se pudo guardar.")
                else:
                    with self.assertRaises(IntegrityError):
                        cliente.post(ruta, datos)
            self.assertEqual(self.estado_guardado(), antes)
        with connection.cursor() as cursor:
            cursor.execute("SELECT IS_FREE_LOCK(%s)", [
                f"{connection.settings_dict['NAME']}.registro_producto",
            ])
            self.assertEqual(cursor.fetchone()[0], 1)

    def test_fallo_commit_del_registro_revierte_fila_y_fotografia(self):
        antes = self.estado_guardado()
        with patch.object(connection, "commit", side_effect=DatabaseError):
            with self.assertRaises(DatabaseError):
                self.reponedor.post(reverse("catalogo:registrar_producto"), {
                    **self.datos,
                    "fotografia_comercial": imagen_ubicacion("comercial.png"),
                })
        self.assertEqual(self.estado_guardado(), antes)

    def test_fallo_mysql_real_conserva_ubicacion_y_archivos(self):
        producto, _ = self.crear_activo_con_ubicacion()
        antes = self.estado_guardado()
        guardar = UbicacionProducto.save

        def guardar_y_violar_restriccion(ubicacion, *args, **kwargs):
            guardar(ubicacion, *args, **kwargs)
            PrecioReferencia.objects.create(
                codigo_barras="fallo-controlado", precio_referencia=-1,
            )

        with patch.object(
            UbicacionProducto, "save", new=guardar_y_violar_restriccion,
        ):
            respuesta = self.reponedor.post(reverse(
                "catalogo:actualizar_ubicacion", args=[producto.pk],
            ), {
                "pasillo": self.pasillos[1].pk,
                "fotografia_referencial": imagen_ubicacion(color="blue"),
            })
        self.assertContains(respuesta, "No se pudo guardar la ubicación.")
        self.assertEqual(self.estado_guardado(), antes)

    def test_fallo_commit_del_reemplazo_conserva_fotografia_anterior(self):
        producto, _ = self.crear_activo_con_ubicacion()
        antes = self.estado_guardado()
        with patch.object(connection, "commit", side_effect=DatabaseError):
            respuesta = self.reponedor.post(reverse(
                "catalogo:actualizar_ubicacion", args=[producto.pk],
            ), {"fotografia_referencial": imagen_ubicacion(color="blue")})
        self.assertContains(respuesta, "No se pudo guardar la ubicación.")
        self.assertEqual(self.estado_guardado(), antes)

    def test_baja_concurrente_impide_modificar_stock_precio_y_ubicacion(self):
        producto, _ = self.crear_activo_con_ubicacion()
        for nombre, perfil, datos in (
            ("catalogo:actualizar_stock", PerfilPersonal.REPONEDOR,
             {"stock": "30"}),
            ("catalogo:actualizar_precio", PerfilPersonal.JEFATURA,
             {"precio_normal": "1450"}),
            ("catalogo:actualizar_ubicacion", PerfilPersonal.REPONEDOR,
             {"pasillo": self.pasillos[1].pk,
              "fotografia_referencial": imagen_ubicacion(color="blue")}),
        ):
            Producto.objects.filter(pk=producto.pk).update(
                estado=EstadoProducto.ACTIVO,
            )
            antes = self.estado_guardado()
            intentando_bloquear = Event()

            def esperar_bloqueo(execute, sql, params, many, context):
                if "FOR UPDATE" in sql.upper():
                    intentando_bloquear.set()
                return execute(sql, params, many, context)

            def modificar():
                try:
                    cliente = Client()
                    cliente.force_login(self.personales[perfil].usuario)
                    with connection.execute_wrapper(esperar_bloqueo):
                        return cliente.post(reverse(
                            nombre, args=[producto.pk],
                        ), datos, follow=True)
                finally:
                    connections.close_all()

            with self.subTest(operacion=nombre):
                with ThreadPoolExecutor(max_workers=1) as ejecutor:
                    with transaction.atomic():
                        Producto.objects.select_for_update().get(
                            pk=producto.pk,
                        )
                        solicitud = ejecutor.submit(modificar)
                        self.assertTrue(intentando_bloquear.wait(timeout=10))
                        self.assertFalse(solicitud.done())
                        Producto.objects.filter(pk=producto.pk).update(
                            estado=EstadoProducto.INACTIVO,
                        )
                    respuesta = solicitud.result(timeout=10)
                self.assertContains(
                    respuesta, "El producto ya no está activo.",
                )
                antes["productos"][0]["estado"] = EstadoProducto.INACTIVO
                self.assertEqual(self.estado_guardado(), antes)
