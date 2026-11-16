from secrets import token_urlsafe
from unittest.mock import patch

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import identify_hasher
from django.db import DatabaseError
from django.test import Client, TestCase
from django.urls import reverse

from .models import PerfilPersonal, PersonalTienda


class AdministracionPersonalPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.contrasena = token_urlsafe(24)
        cls.administrador = get_user_model().objects.create_superuser(
            username="administrador", password=cls.contrasena,
        )

    def setUp(self):
        self.client.force_login(self.administrador)
        self.url_alta = reverse("admin:auth_user_add")

    def datos_alta(self, **cambios):
        datos = {
            "username": "12.345.678-9",
            "first_name": "Camila",
            "perfil": PerfilPersonal.REPONEDOR,
            "usable_password": "true",
            "password1": self.contrasena,
            "password2": self.contrasena,
            "_save": "Guardar",
        }
        datos.update(cambios)
        return datos

    def crear_personal(self, **cambios):
        respuesta = self.client.post(self.url_alta, self.datos_alta(**cambios))
        self.assertEqual(respuesta.status_code, 302)
        return get_user_model().objects.get(
            username="12345678-9" if "username" not in cambios
            else cambios["username"],
        )

    def url_cambio(self, usuario):
        return reverse("admin:auth_user_change", args=[usuario.pk])

    def datos_cambio(self, usuario, **cambios):
        respuesta = self.client.get(self.url_cambio(usuario))
        formulario = respuesta.context["adminform"].form
        datos = {}
        for nombre in formulario.fields:
            valor = formulario[nombre].value()
            control = formulario.fields[nombre].widget
            if isinstance(control, forms.MultiWidget):
                valores = control.decompress(valor)
                for indice, parte in enumerate(valores):
                    datos[f"{nombre}_{indice}"] = parte or ""
                continue
            if valor is not None and valor is not False:
                datos[nombre] = valor
        datos["_save"] = "Guardar"
        datos.update(cambios)
        return datos

    def test_alta_ambos_perfiles_hash_acceso_y_restricciones(self):
        for rut, nombre, perfil, panel, ajeno in (
            (
                "123456789", "Camila", PerfilPersonal.REPONEDOR,
                "personal:panel_reponedor", "personal:panel_jefatura",
            ),
            (
                "87.654.321-k", "Javier", PerfilPersonal.JEFATURA,
                "personal:panel_jefatura", "personal:panel_reponedor",
            ),
        ):
            with self.subTest(perfil=perfil):
                respuesta = self.client.post(self.url_alta, self.datos_alta(
                    username=rut, first_name=nombre, perfil=perfil,
                ))
                self.assertEqual(respuesta.status_code, 302)
                usuario = get_user_model().objects.get(first_name=nombre)
                self.assertEqual(
                    usuario.username,
                    "12345678-9" if nombre == "Camila" else "87654321-K",
                )
                self.assertEqual(usuario.personaltienda.perfil, perfil)
                self.assertNotEqual(usuario.password, self.contrasena)
                self.assertTrue(identify_hasher(usuario.password).algorithm)
                self.assertTrue(usuario.check_password(self.contrasena))
                self.assertFalse(usuario.is_staff)
                self.assertFalse(usuario.is_superuser)
                self.assertFalse(usuario.groups.exists())
                self.assertFalse(usuario.user_permissions.exists())

                cliente = Client()
                respuesta = cliente.post(reverse("personal:acceso"), {
                    "username": rut, "password": self.contrasena,
                }, follow=True)
                self.assertRedirects(respuesta, reverse(panel))
                self.assertEqual(cliente.get(reverse(ajeno)).status_code, 403)
                self.assertEqual(cliente.post(reverse(ajeno)).status_code, 403)
                self.assertRedirects(
                    cliente.get(reverse("admin:index")),
                    reverse("admin:login") + "?next=/admin/",
                )
                self.assertEqual(cliente.get(self.url_alta).status_code, 302)
                self.assertEqual(cliente.post(
                    self.url_alta, self.datos_alta(),
                ).status_code, 302)

    def test_formulario_admin_muestra_nombre_perfil_y_contrasena(self):
        respuesta = self.client.get(self.url_alta)
        campos = ("username", "first_name", "perfil", "password1", "password2")
        for campo in campos:
            self.assertContains(respuesta, f'id="id_{campo}"')
        for perfil in PerfilPersonal.values:
            self.assertContains(respuesta, f'value="{perfil}"')

    def test_rut_equivalente_no_crea_otro_usuario_ni_perfil(self):
        self.crear_personal()
        for rut in ("123456789", "12.345.6789", " 12345678-9 "):
            with self.subTest(rut=rut):
                respuesta = self.client.post(
                    self.url_alta, self.datos_alta(username=rut),
                )
                self.assertEqual(respuesta.status_code, 200)
                self.assertIn(
                    "username", respuesta.context["adminform"].form.errors,
                )
        self.assertEqual(get_user_model().objects.count(), 2)
        self.assertEqual(PersonalTienda.objects.count(), 1)

    def test_rut_duplicado_con_formato_antiguo_no_altera_cuenta(self):
        usuario = get_user_model().objects.create_user(username="12.345.678-9")
        respuesta = self.client.post(
            self.url_alta, self.datos_alta(username="123456789"),
        )
        self.assertIn("username", respuesta.context["adminform"].form.errors)
        usuario.refresh_from_db()
        self.assertEqual(usuario.username, "12.345.678-9")
        self.assertFalse(PersonalTienda.objects.exists())

    def test_datos_invalidos_no_crean_registros_parciales(self):
        for cambios, campo in (
            ({"first_name": ""}, "first_name"),
            ({"first_name": "   "}, "first_name"),
            ({"perfil": "administrador"}, "perfil"),
            ({"username": "rut-invalido"}, "username"),
            ({"usable_password": "false"}, "usable_password"),
            ({"password2": token_urlsafe(24)}, "password2"),
            ({"password1": "123", "password2": "123"}, "password2"),
        ):
            with self.subTest(cambios=tuple(cambios)):
                respuesta = self.client.post(
                    self.url_alta, self.datos_alta(**cambios),
                )
                self.assertEqual(respuesta.status_code, 200)
                self.assertIn(
                    campo, respuesta.context["adminform"].form.errors,
                )
                self.assertEqual(get_user_model().objects.count(), 1)
                self.assertFalse(PersonalTienda.objects.exists())

    def test_editar_nombre_y_perfil_conserva_usuario_y_contrasena(self):
        usuario = self.crear_personal()
        identificador = usuario.personaltienda.pk
        contrasena_guardada = usuario.password
        datos = self.datos_cambio(
            usuario, first_name="Camila Actualizada",
            perfil=PerfilPersonal.JEFATURA,
            username="12.345.6789",
        )
        respuesta = self.client.post(self.url_cambio(usuario), datos)
        self.assertEqual(respuesta.status_code, 302)
        usuario.refresh_from_db()
        self.assertEqual(usuario.username, "12345678-9")
        self.assertEqual(usuario.first_name, "Camila Actualizada")
        self.assertEqual(usuario.personaltienda.pk, identificador)
        self.assertEqual(
            usuario.personaltienda.perfil, PerfilPersonal.JEFATURA,
        )
        self.assertEqual(usuario.password, contrasena_guardada)
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)

    def test_edicion_invalida_conserva_nombre_rut_y_perfil(self):
        usuario = self.crear_personal()
        for cambios, campo in (
            ({"first_name": "  "}, "first_name"),
            ({"perfil": ""}, "perfil"),
            ({"perfil": "otro"}, "perfil"),
            ({"username": "invalido"}, "username"),
        ):
            with self.subTest(campo=campo):
                datos = self.datos_cambio(usuario, **cambios)
                respuesta = self.client.post(self.url_cambio(usuario), datos)
                self.assertIn(
                    campo, respuesta.context["adminform"].form.errors,
                )
                usuario.refresh_from_db()
                self.assertEqual(usuario.username, "12345678-9")
                self.assertEqual(usuario.first_name, "Camila")
                self.assertEqual(
                    usuario.personaltienda.perfil, PerfilPersonal.REPONEDOR,
                )

    def test_superusuario_sin_perfil_conserva_acceso_admin(self):
        datos = self.datos_cambio(self.administrador, first_name="")
        respuesta = self.client.post(
            self.url_cambio(self.administrador), datos,
        )
        self.assertEqual(respuesta.status_code, 302)
        self.administrador.refresh_from_db()
        self.assertEqual(self.administrador.username, "administrador")
        self.assertTrue(self.administrador.is_staff)
        self.assertTrue(self.administrador.is_superuser)
        self.assertFalse(PersonalTienda.objects.exists())
        self.assertEqual(
            self.client.get(reverse("admin:index")).status_code, 200,
        )

    def test_usuario_existente_recibe_perfil_sin_cambiar_contrasena(self):
        usuario = get_user_model().objects.create_user(
            username="12.345.678-9", password=self.contrasena,
        )
        datos = self.datos_cambio(
            usuario, first_name="Camila", perfil=PerfilPersonal.REPONEDOR,
        )
        respuesta = self.client.post(self.url_cambio(usuario), datos)
        self.assertEqual(respuesta.status_code, 302)
        usuario.refresh_from_db()
        self.assertEqual(usuario.username, "12345678-9")
        self.assertTrue(usuario.check_password(self.contrasena))
        self.assertEqual(
            usuario.personaltienda.perfil, PerfilPersonal.REPONEDOR,
        )

    def test_usuario_sin_perfil_sigue_permitido_para_admin(self):
        respuesta = self.client.post(self.url_alta, self.datos_alta(
            username="cuenta_administrativa", first_name="", perfil="",
        ))
        self.assertEqual(respuesta.status_code, 302)
        usuario = get_user_model().objects.get(
            username="cuenta_administrativa",
        )
        self.assertFalse(
            PersonalTienda.objects.filter(usuario=usuario).exists(),
        )
        self.assertTrue(usuario.check_password(self.contrasena))

    def test_listado_y_edicion_muestran_perfil_existente(self):
        usuario = self.crear_personal()
        respuesta = self.client.get(reverse("admin:auth_user_changelist"))
        self.assertContains(respuesta, "Reponedor")
        self.assertContains(respuesta, "12345678-9")
        respuesta = self.client.get(self.url_cambio(usuario))
        self.assertEqual(
            respuesta.context["adminform"].form["perfil"].value(),
            PerfilPersonal.REPONEDOR,
        )

    def test_edicion_no_permite_rut_de_otro_trabajador(self):
        usuario = self.crear_personal()
        otro = self.crear_personal(
            username="87654321-K", first_name="Javier",
            perfil=PerfilPersonal.JEFATURA,
        )
        datos = self.datos_cambio(usuario, username="87.654.321-k")
        respuesta = self.client.post(self.url_cambio(usuario), datos)
        self.assertIn("username", respuesta.context["adminform"].form.errors)
        usuario.refresh_from_db()
        otro.refresh_from_db()
        self.assertEqual(usuario.username, "12345678-9")
        self.assertEqual(otro.username, "87654321-K")
        self.assertEqual(PersonalTienda.objects.count(), 2)

    def test_alta_no_admite_privilegios_extra_en_post(self):
        usuario = self.crear_personal(is_staff="on", is_superuser="on")
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)

    def test_cambio_contrasena_estandar_conserva_perfil(self):
        usuario = self.crear_personal()
        nueva_contrasena = token_urlsafe(24)
        respuesta = self.client.post(
            reverse("admin:auth_user_password_change", args=[usuario.pk]),
            {
                "usable_password": "true",
                "password1": nueva_contrasena,
                "password2": nueva_contrasena,
                "set-password": "Establecer contraseña",
            },
        )
        self.assertEqual(respuesta.status_code, 302)
        usuario.refresh_from_db()
        self.assertTrue(usuario.check_password(nueva_contrasena))
        self.assertFalse(usuario.check_password(self.contrasena))
        self.assertEqual(
            usuario.personaltienda.perfil, PerfilPersonal.REPONEDOR,
        )

    def test_fallo_del_perfil_revierte_alta_completa(self):
        with patch.object(PersonalTienda, "save", side_effect=DatabaseError):
            with self.assertRaises(DatabaseError):
                self.client.post(self.url_alta, self.datos_alta())
        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertFalse(PersonalTienda.objects.exists())

    def test_fallo_del_perfil_revierte_edicion_del_usuario(self):
        usuario = self.crear_personal()
        datos = self.datos_cambio(
            usuario, first_name="Cambio", perfil=PerfilPersonal.JEFATURA,
        )
        with patch.object(PersonalTienda, "save", side_effect=DatabaseError):
            with self.assertRaises(DatabaseError):
                self.client.post(self.url_cambio(usuario), datos)
        usuario.refresh_from_db()
        self.assertEqual(usuario.first_name, "Camila")
        self.assertEqual(
            usuario.personaltienda.perfil, PerfilPersonal.REPONEDOR,
        )

    def test_admin_requiere_csrf_y_rechaza_visitantes(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.administrador)
        respuesta = cliente.post(self.url_alta, self.datos_alta())
        self.assertEqual(respuesta.status_code, 403)
        visitante = Client()
        self.assertEqual(visitante.get(self.url_alta).status_code, 302)
        self.assertEqual(
            visitante.post(self.url_alta, self.datos_alta()).status_code, 302,
        )
        self.assertEqual(get_user_model().objects.count(), 1)
