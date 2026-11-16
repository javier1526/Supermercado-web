from secrets import token_urlsafe

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from personal.models import PerfilPersonal, PersonalTienda

from .models import (
    Categoria, EstadoProducto, Pasillo, PrecioReferencia, Producto,
    Supermercado, UbicacionProducto,
)


class AdministracionDatosMaestrosPruebas(TestCase):
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
        cls.categoria = Categoria.objects.create(nombre="Alimentos")
        cls.supermercado = Supermercado.objects.create(pk=5)
        cls.otro_supermercado = Supermercado.objects.create(pk=9)
        cls.pasillo = Pasillo.objects.create(
            numero_pasillo=2, supermercado=cls.supermercado,
        )
        cls.libre = Pasillo.objects.create(
            numero_pasillo=10, supermercado=cls.supermercado,
        )
        cls.ajeno = Pasillo.objects.create(
            numero_pasillo=99, supermercado=cls.otro_supermercado,
        )
        cls.producto = Producto.objects.create(
            codigo_barras="0000000000001", nombre_producto="Arroz de prueba",
            marca="Marca de prueba", categoria=cls.categoria,
            estado=EstadoProducto.ACTIVO, precio_normal=1250, stock=8,
        )
        cls.ubicacion = UbicacionProducto.objects.create(
            producto=cls.producto, pasillo=cls.pasillo,
            fotografia_referencial="ubicaciones/anterior.png",
            fecha_ultima_actualizacion=timezone.now(),
            ultimo_responsable=cls.responsable,
        )
        PrecioReferencia.objects.create(
            codigo_barras=cls.producto.codigo_barras, precio_referencia=999,
        )

    def setUp(self):
        self.client.force_login(self.administrador)

    def url_admin(self, modelo, accion, objeto=None):
        return reverse(
            f"admin:catalogo_{modelo}_{accion}",
            args=[objeto.pk] if objeto else None,
        )

    def crear_categoria(self, nombre="Bebidas"):
        respuesta = self.client.post(
            self.url_admin("categoria", "add"), {"nombre": nombre},
        )
        self.assertEqual(respuesta.status_code, 302)
        return Categoria.objects.get(nombre=nombre)

    def crear_pasillo(self, numero=3, **datos):
        respuesta = self.client.post(
            self.url_admin("pasillo", "add"),
            {"numero_pasillo": numero, **datos},
        )
        self.assertEqual(respuesta.status_code, 302)
        return Pasillo.objects.get(
            numero_pasillo=numero, supermercado=self.supermercado,
        )

    def informacion_productos(self):
        return {
            modelo._meta.label: list(modelo.objects.order_by("pk").values())
            for modelo in (Producto, UbicacionProducto, PrecioReferencia)
        }

    def test_crear_editar_y_buscar_categoria_desde_admin(self):
        categoria = self.crear_categoria()
        respuesta = self.client.post(
            self.url_admin("categoria", "change", categoria),
            {"nombre": "Bebidas sin azucar"},
        )
        self.assertEqual(respuesta.status_code, 302)
        categoria.refresh_from_db()
        self.assertEqual(categoria.nombre, "Bebidas sin azucar")
        respuesta = self.client.get(
            self.url_admin("categoria", "changelist"), {"q": "sin azucar"},
        )
        self.assertEqual(
            list(respuesta.context["cl"].queryset), [categoria],
        )

    def test_categoria_invalida_conserva_datos_y_muestra_error(self):
        anterior = self.informacion_productos()
        for nombre in ("", "   ", "a" * 121):
            with self.subTest(longitud=len(nombre)):
                operaciones = (("add", None), ("change", self.categoria))
                for accion, objeto in operaciones:
                    respuesta = self.client.post(
                        self.url_admin("categoria", accion, objeto),
                        {"nombre": nombre},
                    )
                    self.assertEqual(respuesta.status_code, 200)
                    errores = respuesta.context["adminform"].form.errors
                    self.assertIn("nombre", errores)
        self.categoria.refresh_from_db()
        self.assertEqual(self.categoria.nombre, "Alimentos")
        self.assertEqual(Categoria.objects.count(), 1)
        self.assertEqual(self.informacion_productos(), anterior)

    def test_categoria_conserva_ausencia_de_unicidad_adicional(self):
        respuesta = self.client.post(
            self.url_admin("categoria", "add"), {"nombre": "Alimentos"},
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(
            Categoria.objects.filter(nombre="Alimentos").count(), 2,
        )

    def test_alta_asigna_primer_supermercado_ante_post_manipulado(self):
        pasillo = self.crear_pasillo(
            supermercado=self.otro_supermercado.pk,
        )
        pasillo.refresh_from_db()
        self.assertEqual(pasillo.supermercado_id, self.supermercado.pk)
        respuesta = self.client.get(self.url_admin("pasillo", "add"))
        campo = respuesta.context["adminform"].form.fields["supermercado"]
        self.assertTrue(campo.disabled)
        self.assertEqual(list(campo.queryset), [self.supermercado])
        self.assertEqual(Supermercado.objects.count(), 2)

    def test_pasillos_ordenados_sin_mezclar_supermercados(self):
        primero = self.crear_pasillo(1)
        respuesta = self.client.get(self.url_admin("pasillo", "changelist"))
        self.assertEqual(
            list(respuesta.context["cl"].queryset),
            [primero, self.pasillo, self.libre],
        )
        for accion in ("change", "delete"):
            url = self.url_admin("pasillo", accion, self.ajeno)
            self.assertEqual(self.client.get(url).status_code, 302)
            self.assertEqual(self.client.post(url, {
                "numero_pasillo": 4, "post": "yes",
            }).status_code, 302)
        self.ajeno.refresh_from_db()
        self.assertEqual(self.ajeno.numero_pasillo, 99)
        self.assertEqual(
            self.ajeno.supermercado_id, self.otro_supermercado.pk,
        )

    def test_pasillos_duplicados_se_rechazan_en_alta_y_edicion(self):
        anterior = self.informacion_productos()
        for accion, objeto in (("add", None), ("change", self.libre)):
            with self.subTest(accion=accion):
                respuesta = self.client.post(
                    self.url_admin("pasillo", accion, objeto),
                    {"numero_pasillo": self.pasillo.numero_pasillo},
                )
                self.assertEqual(respuesta.status_code, 200)
                self.assertTrue(
                    respuesta.context["adminform"].form.non_field_errors(),
                )
        self.libre.refresh_from_db()
        self.assertEqual(self.libre.numero_pasillo, 10)
        self.assertEqual(Pasillo.objects.count(), 3)
        self.assertEqual(self.informacion_productos(), anterior)

    def test_numero_de_otro_supermercado_no_bloquea_alta_en_el_principal(self):
        pasillo = self.crear_pasillo(self.ajeno.numero_pasillo)
        self.assertEqual(pasillo.supermercado_id, self.supermercado.pk)
        self.assertNotEqual(pasillo.pk, self.ajeno.pk)
        self.assertEqual(Pasillo.objects.count(), 4)

    def test_numero_invalido_conserva_pasillo_y_ubicaciones(self):
        anterior = self.informacion_productos()
        for numero in ("", "texto", "2147483648"):
            with self.subTest(numero=numero):
                respuesta = self.client.post(
                    self.url_admin("pasillo", "change", self.libre),
                    {"numero_pasillo": numero},
                )
                self.assertEqual(respuesta.status_code, 200)
                self.assertIn(
                    "numero_pasillo",
                    respuesta.context["adminform"].form.errors,
                )
        self.libre.refresh_from_db()
        self.assertEqual(self.libre.numero_pasillo, 10)
        self.assertEqual(self.informacion_productos(), anterior)

    def test_renumerar_pasillo_libre_conserva_datos_de_productos(self):
        anterior = self.informacion_productos()
        respuesta = self.client.post(
            self.url_admin("pasillo", "change", self.libre),
            {"numero_pasillo": 4, "supermercado": self.otro_supermercado.pk},
        )
        self.assertEqual(respuesta.status_code, 302)
        self.libre.refresh_from_db()
        self.assertEqual(self.libre.numero_pasillo, 4)
        self.assertEqual(self.libre.supermercado_id, self.supermercado.pk)
        self.assertEqual(self.informacion_productos(), anterior)

    def test_no_renumera_pasillo_con_ubicaciones_de_cualquier_estado(self):
        for estado in EstadoProducto.values:
            with self.subTest(estado=estado):
                self.producto.estado = estado
                self.producto.save(update_fields=["estado"])
                anterior = self.informacion_productos()
                respuesta = self.client.post(
                    self.url_admin("pasillo", "change", self.pasillo),
                    {"numero_pasillo": 7},
                )
                self.assertEqual(respuesta.status_code, 200)
                self.assertContains(
                    respuesta,
                    "No se puede renumerar un pasillo con ubicaciones",
                )
                self.pasillo.refresh_from_db()
                self.assertEqual(self.pasillo.numero_pasillo, 2)
                self.assertEqual(self.informacion_productos(), anterior)

    def test_asociacion_posterior_a_abrir_formulario_impide_renumerar(self):
        url = self.url_admin("pasillo", "change", self.libre)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.ubicacion.pasillo = self.libre
        self.ubicacion.save(update_fields=["pasillo"])
        anterior = self.informacion_productos()
        respuesta = self.client.post(url, {"numero_pasillo": 4})
        self.assertEqual(respuesta.status_code, 200)
        self.libre.refresh_from_db()
        self.assertEqual(self.libre.numero_pasillo, 10)
        self.assertEqual(self.informacion_productos(), anterior)

    def test_guardar_sin_cambios_conserva_fecha_y_responsable(self):
        anterior = self.informacion_productos()
        respuesta = self.client.post(
            self.url_admin("pasillo", "change", self.pasillo),
            {"numero_pasillo": 2},
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.informacion_productos(), anterior)

    def test_categoria_nueva_aparece_en_registro_y_consulta_publica(self):
        categoria = self.crear_categoria()
        cliente = Client()
        cliente.force_login(self.reponedor)
        respuesta = cliente.get(reverse("catalogo:registrar_producto"))
        formulario = respuesta.context["formulario"]
        categorias = formulario.fields["categoria"].queryset
        self.assertIn(categoria, categorias)
        respuesta = cliente.post(reverse("catalogo:registrar_producto"), {
            "codigo_barras": "0000000000002", "nombre_producto": "Agua nueva",
            "marca": "Marca nueva", "categoria": categoria.pk,
        })
        self.assertEqual(respuesta.status_code, 302)
        producto = Producto.objects.get(codigo_barras="0000000000002")
        self.assertEqual(producto.categoria_id, categoria.pk)
        self.assertEqual(producto.estado, EstadoProducto.PENDIENTE)
        publico = Client()
        respuesta = publico.get(reverse("cliente:categorias"))
        self.assertContains(respuesta, categoria.nombre)
        respuesta = publico.get(reverse(
            "cliente:productos_categoria", args=[categoria.pk],
        ))
        self.assertContains(respuesta, "No se encontraron productos")
        self.assertNotContains(respuesta, producto.nombre_producto)

    def test_categoria_vacia_creada_en_admin_permanece_publica(self):
        categoria = self.crear_categoria()
        publico = Client()
        self.assertContains(
            publico.get(reverse("cliente:categorias")), categoria.nombre,
        )
        respuesta = publico.get(reverse(
            "cliente:productos_categoria", args=[categoria.pk],
        ))
        self.assertContains(respuesta, "No se encontraron productos")
        self.assertTrue(Categoria.objects.filter(pk=categoria.pk).exists())

    def test_pasillo_nuevo_aparece_en_formulario_y_consulta_vacia(self):
        pasillo = self.crear_pasillo()
        cliente = Client()
        cliente.force_login(self.reponedor)
        respuesta = cliente.get(reverse(
            "catalogo:actualizar_ubicacion", args=[self.producto.pk],
        ))
        pasillos = respuesta.context["formulario"].fields["pasillo"].queryset
        self.assertIn(pasillo, pasillos)
        publico = Client()
        self.assertContains(
            publico.get(reverse("cliente:pasillos")), "Pasillo 3",
        )
        respuesta = publico.get(reverse(
            "cliente:productos_pasillo", args=[pasillo.pk],
        ))
        self.assertContains(respuesta, "No se encontraron productos")

    def test_traslado_por_reponedor_sigue_funcionando_con_pasillo_nuevo(self):
        pasillo = self.crear_pasillo()
        cliente = Client()
        cliente.force_login(self.reponedor)
        respuesta = cliente.post(reverse(
            "catalogo:actualizar_ubicacion", args=[self.producto.pk],
        ), {"pasillo": pasillo.pk})
        self.assertEqual(respuesta.status_code, 302)
        self.ubicacion.refresh_from_db()
        self.assertEqual(self.ubicacion.pasillo_id, pasillo.pk)
        self.assertEqual(
            self.ubicacion.ultimo_responsable_id, self.responsable.pk,
        )
        publico = Client()
        self.assertContains(publico.get(reverse(
            "cliente:productos_pasillo", args=[pasillo.pk],
        )), self.producto.nombre_producto)
        self.assertContains(publico.get(reverse(
            "cliente:productos_pasillo", args=[self.pasillo.pk],
        )), "No se encontraron productos")

    def test_eliminar_categoria_utilizada_individual_y_en_lote(self):
        anterior = self.informacion_productos()
        libre = self.crear_categoria()
        url = self.url_admin("categoria", "delete", self.categoria)
        self.assertEqual(self.client.get(url).status_code, 200)
        respuesta = self.client.post(url, {"post": "yes"})
        self.assertEqual(respuesta.status_code, 200)
        respuesta = self.client.post(
            self.url_admin("categoria", "changelist"), {
                "action": "delete_selected", "post": "yes",
                "_selected_action": [self.categoria.pk, libre.pk],
            },
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(Categoria.objects.count(), 2)
        self.assertEqual(self.informacion_productos(), anterior)

    def test_eliminar_pasillo_utilizado_individual_y_en_lote(self):
        anterior = self.informacion_productos()
        url = self.url_admin("pasillo", "delete", self.pasillo)
        self.assertEqual(self.client.get(url).status_code, 200)
        respuesta = self.client.post(url, {"post": "yes"})
        self.assertEqual(respuesta.status_code, 200)
        respuesta = self.client.post(
            self.url_admin("pasillo", "changelist"), {
                "action": "delete_selected", "post": "yes",
                "_selected_action": [self.pasillo.pk, self.libre.pk],
            },
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(Pasillo.objects.count(), 3)
        self.assertEqual(self.informacion_productos(), anterior)

    def test_editar_categoria_utilizada_no_modifica_datos_de_ubicacion(self):
        anterior = self.informacion_productos()
        respuesta = self.client.post(
            self.url_admin("categoria", "change", self.categoria),
            {"nombre": "Abarrotes"},
        )
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.informacion_productos(), anterior)

    def test_sin_supermercado_informa_y_no_crea_datos_automaticamente(self):
        UbicacionProducto.objects.all().delete()
        Pasillo.objects.all().delete()
        Supermercado.objects.all().delete()
        url = self.url_admin("pasillo", "add")
        self.assertContains(
            self.client.get(url), "No hay un supermercado registrado",
        )
        respuesta = self.client.post(url, {"numero_pasillo": 1})
        self.assertContains(respuesta, "No hay un supermercado registrado")
        self.assertFalse(Supermercado.objects.exists())
        self.assertFalse(Pasillo.objects.exists())

    def test_solo_superusuarios_acceden_incluso_con_permisos_de_modelo(self):
        empleado = get_user_model().objects.create_user(
            username="empleado", is_staff=True,
        )
        empleado.user_permissions.set(Permission.objects.filter(
            content_type__app_label="catalogo",
            content_type__model__in=("categoria", "pasillo"),
        ))
        inactivo = get_user_model().objects.create_superuser(
            username="inactivo", password=token_urlsafe(24), is_active=False,
        )
        anterior = self.informacion_productos()
        usuarios = (None, self.reponedor, self.jefatura, empleado, inactivo)
        for usuario in usuarios:
            cliente = Client()
            if usuario:
                cliente.force_login(usuario)
            esperado = 403 if usuario == empleado else 302
            for modelo, objeto in (
                ("categoria", self.categoria), ("pasillo", self.pasillo),
            ):
                for accion in ("changelist", "add", "change", "delete"):
                    url = self.url_admin(
                        modelo, accion,
                        objeto if accion in ("change", "delete") else None,
                    )
                    with self.subTest(usuario=usuario, url=url):
                        self.assertEqual(
                            cliente.get(url).status_code, esperado,
                        )
                        self.assertEqual(cliente.post(url, {
                            "nombre": "Alterado", "numero_pasillo": 7,
                            "post": "yes", "action": "delete_selected",
                            "_selected_action": [objeto.pk],
                        }).status_code, esperado)
        self.assertEqual(self.informacion_productos(), anterior)
        self.assertEqual(Categoria.objects.count(), 1)
        self.assertEqual(Pasillo.objects.count(), 3)

    def test_consultas_admin_no_modifican_datos_ni_marcas_de_ubicacion(self):
        anterior = self.informacion_productos()
        for modelo, objeto in (
            ("categoria", self.categoria), ("pasillo", self.pasillo),
        ):
            for accion in ("changelist", "add", "change", "delete"):
                respuesta = self.client.get(self.url_admin(
                    modelo, accion,
                    objeto if accion in ("change", "delete") else None,
                ))
                self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.informacion_productos(), anterior)

    def test_csrf_protege_las_operaciones_administrativas(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.administrador)
        for modelo in ("categoria", "pasillo"):
            respuesta = cliente.post(self.url_admin(modelo, "add"), {
                "nombre": "Prueba", "numero_pasillo": 3,
            })
            self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(Categoria.objects.count(), 1)
        self.assertEqual(Pasillo.objects.count(), 3)

    def test_admin_no_habilita_gestion_de_productos_ni_supermercados(self):
        modelos = (Producto, Supermercado, UbicacionProducto)
        for modelo in modelos:
            self.assertFalse(admin.site.is_registered(modelo))
