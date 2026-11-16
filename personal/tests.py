from secrets import token_urlsafe

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import identify_hasher
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from .models import PerfilPersonal, PersonalTienda


class PersonalTiendaPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_user(
            username="12345678-9", first_name="Camila",
        )

    def test_perfil_unico_para_usuario_de_django(self):
        personal = PersonalTienda.objects.create(
            usuario=self.usuario, perfil=PerfilPersonal.REPONEDOR,
        )
        self.assertEqual(personal.usuario, self.usuario)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonalTienda.objects.create(
                usuario=self.usuario, perfil=PerfilPersonal.JEFATURA,
            )

    def test_rechaza_perfil_invalido_en_mysql(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PersonalTienda.objects.create(usuario=self.usuario, perfil="otro")

    def test_validacion_exige_nombre_en_la_cuenta(self):
        self.usuario.first_name = "  "
        personal = PersonalTienda(
            usuario=self.usuario, perfil=PerfilPersonal.REPONEDOR,
        )
        with self.assertRaises(ValidationError) as error:
            personal.full_clean()
        self.assertIn("usuario", error.exception.message_dict)


class AccesoPersonalPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.contrasena = token_urlsafe(24)
        usuarios = get_user_model().objects
        cls.reponedor = usuarios.create_user(
            username="12345678-9",
            first_name="Camila",
            password=cls.contrasena,
        )
        cls.jefatura = usuarios.create_user(
            username="87654321-K",
            first_name="Javier",
            password=cls.contrasena,
        )
        cls.sin_perfil = usuarios.create_user(
            username="11111111-1", first_name="Ana", password=cls.contrasena,
        )
        for usuario, perfil in (
            (cls.reponedor, PerfilPersonal.REPONEDOR),
            (cls.jefatura, PerfilPersonal.JEFATURA),
        ):
            personal = PersonalTienda(usuario=usuario, perfil=perfil)
            personal.full_clean()
            personal.save()

    def ingresar(self, rut, contrasena=None):
        if contrasena is None:
            contrasena = self.contrasena
        return self.client.post(
            reverse("personal:acceso"),
            {"username": rut, "password": contrasena},
            follow=True,
        )

    def test_reponedor_ingresa_con_o_sin_puntos_y_guion(self):
        formatos = ("12.345.678-9", "12345678-9", "12.345.6789", " 123456789 ")
        for rut in formatos:
            with self.subTest(rut=rut):
                respuesta = self.ingresar(rut)
                self.assertRedirects(
                    respuesta, reverse("personal:panel_reponedor"),
                )
                self.assertContains(respuesta, "Panel del Reponedor")
                self.assertEqual(
                    self.client.session["_auth_user_id"],
                    str(self.reponedor.pk),
                )
                self.client.logout()

    def test_jefatura_ingresa_con_k_minuscula_y_contrasena_en_hash(self):
        respuesta = self.ingresar("87.654.321-k")
        self.assertRedirects(respuesta, reverse("personal:panel_jefatura"))
        self.assertContains(respuesta, "Panel de Jefatura")
        self.jefatura.refresh_from_db()
        self.assertNotEqual(self.jefatura.password, self.contrasena)
        self.assertTrue(identify_hasher(self.jefatura.password))
        self.assertTrue(self.jefatura.check_password(self.contrasena))

    def test_credenciales_incorrectas_no_abren_sesion(self):
        for rut, contrasena in (
            ("12345678-9", "clave incorrecta"),
            ("99999999-9", self.contrasena),
        ):
            with self.subTest(rut=rut):
                respuesta = self.ingresar(rut, contrasena)
                self.assertContains(respuesta, "RUT o contraseña incorrectos.")
                self.assertNotIn("_auth_user_id", self.client.session)

    def test_rut_mal_formado_muestra_error_del_campo(self):
        respuesta = self.ingresar("12--345678-9")
        self.assertContains(respuesta, "Ingresa un RUT con formato válido.")
        self.assertContains(respuesta, 'aria-describedby="id_username_error"')
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_cuenta_inactiva_no_obtiene_acceso(self):
        self.reponedor.is_active = False
        self.reponedor.save(update_fields=["is_active"])
        respuesta = self.ingresar("12345678-9")
        self.assertContains(respuesta, "RUT o contraseña incorrectos.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_cuenta_sin_perfil_no_puede_ingresar_ni_abrir_panel(self):
        respuesta = self.ingresar("11111111-1")
        self.assertContains(respuesta, "RUT o contraseña incorrectos.")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.force_login(self.sin_perfil)
        respuesta = self.client.get(reverse("personal:panel"))
        self.assertEqual(respuesta.status_code, 403)

    def test_visitante_es_redirigido_desde_los_paneles(self):
        for nombre in ("panel", "panel_reponedor", "panel_jefatura"):
            with self.subTest(nombre=nombre):
                self.assertRedirects(
                    self.client.get(reverse(f"personal:{nombre}")),
                    reverse("personal:acceso"),
                )

    def test_perfiles_no_pueden_entrar_al_panel_del_otro(self):
        for usuario, panel in (
            (self.reponedor, "panel_jefatura"),
            (self.jefatura, "panel_reponedor"),
        ):
            with self.subTest(panel=panel):
                self.client.force_login(usuario)
                respuesta = self.client.get(reverse(f"personal:{panel}"))
                self.assertEqual(respuesta.status_code, 403)
                self.client.logout()

    def test_perfil_retirado_no_mantiene_acceso_interno(self):
        self.client.force_login(self.reponedor)
        self.reponedor.personaltienda.delete()
        respuesta = self.client.get(reverse("personal:panel_reponedor"))
        self.assertEqual(respuesta.status_code, 403)

    def test_ingreso_sin_token_csrf_es_rechazado(self):
        cliente = Client(enforce_csrf_checks=True)
        respuesta = cliente.post(reverse("personal:acceso"), {
            "username": self.reponedor.username, "password": self.contrasena,
        })
        self.assertEqual(respuesta.status_code, 403)

    def test_cierre_solo_post_con_csrf_e_invalida_la_sesion(self):
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(self.reponedor)
        clave_sesion = cliente.session.session_key
        panel = reverse("personal:panel_reponedor")
        salida = reverse("personal:cerrar_sesion")
        self.assertEqual(cliente.get(panel).status_code, 200)
        token = cliente.cookies["csrftoken"].value
        self.assertEqual(cliente.get(salida).status_code, 405)
        self.assertEqual(cliente.post(salida).status_code, 403)
        self.assertEqual(cliente.get(panel).status_code, 200)
        respuesta = cliente.post(
            salida, {"csrfmiddlewaretoken": token}, follow=True,
        )
        self.assertRedirects(respuesta, reverse("personal:acceso"))
        self.assertContains(respuesta, "Sesión cerrada")
        sesion = Session.objects.filter(session_key=clave_sesion)
        self.assertFalse(sesion.exists())
        self.assertRedirects(cliente.get(panel), reverse("personal:acceso"))
