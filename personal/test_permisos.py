from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import include, path, reverse

from .models import PerfilPersonal, PersonalTienda
from .permisos import requiere_perfil


def vista_prueba(request):
    request.session["vista_ejecutada"] = request.method
    return HttpResponse(status=204)


urlpatterns = [
    path("personal/", include("personal.urls")),
    path(
        "prueba/reponedor/",
        requiere_perfil(PerfilPersonal.REPONEDOR)(vista_prueba),
        name="prueba_reponedor",
    ),
    path(
        "prueba/jefatura/",
        requiere_perfil(PerfilPersonal.JEFATURA)(vista_prueba),
        name="prueba_jefatura",
    ),
    path(
        "prueba/compartida/",
        requiere_perfil(PerfilPersonal.REPONEDOR, PerfilPersonal.JEFATURA)(
            vista_prueba,
        ),
        name="prueba_compartida",
    ),
]


@override_settings(ROOT_URLCONF=__name__)
class AutorizacionPorPerfilPruebas(TestCase):
    @classmethod
    def setUpTestData(cls):
        usuarios = get_user_model().objects
        cls.reponedor = usuarios.create_user(
            username="12345678-9", first_name="Camila",
        )
        cls.jefatura = usuarios.create_user(
            username="87654321-K", first_name="Javier",
        )
        cls.sin_perfil = usuarios.create_user(
            username="11111111-1", first_name="Ana",
        )
        for usuario, perfil in (
            (cls.reponedor, PerfilPersonal.REPONEDOR),
            (cls.jefatura, PerfilPersonal.JEFATURA),
        ):
            personal = PersonalTienda(usuario=usuario, perfil=perfil)
            personal.full_clean()
            personal.save()

    def cliente_con_csrf(self, usuario=None):
        cliente = Client(enforce_csrf_checks=True)
        if usuario is not None:
            cliente.force_login(usuario)
        cliente.get(reverse("personal:acceso"))
        return cliente

    def verificar_metodos(self, cliente, ruta, estado, perfil_enviado):
        token = cliente.cookies["csrftoken"].value
        for metodo in ("get", "post"):
            with self.subTest(metodo=metodo):
                respuesta = getattr(cliente, metodo)(
                    reverse(ruta),
                    {"perfil": perfil_enviado},
                    HTTP_X_CSRFTOKEN=token,
                )
                self.assertEqual(respuesta.status_code, estado)
                if estado == 302:
                    self.assertEqual(
                        respuesta["Location"], reverse("personal:acceso"),
                    )
                marca = metodo.upper() if estado == 204 else None
                self.assertEqual(cliente.session.get("vista_ejecutada"), marca)

    def test_sin_sesion_no_ejecuta_la_vista_protegida(self):
        self.verificar_metodos(
            self.cliente_con_csrf(), "prueba_compartida", 302,
            PerfilPersonal.JEFATURA,
        )

    def test_sin_perfil_no_ejecuta_la_vista_protegida(self):
        self.verificar_metodos(
            self.cliente_con_csrf(self.sin_perfil), "prueba_compartida", 403,
            PerfilPersonal.JEFATURA,
        )

    def test_cuenta_desactivada_con_sesion_pierde_acceso(self):
        cliente = self.cliente_con_csrf(self.reponedor)
        self.reponedor.is_active = False
        self.reponedor.save(update_fields=["is_active"])
        self.verificar_metodos(
            cliente, "prueba_compartida", 302, PerfilPersonal.REPONEDOR,
        )

    def test_perfil_invalido_no_se_autoriza(self):
        self.reponedor.personaltienda.perfil = "invalido"
        vista = requiere_perfil(*PerfilPersonal.values)(vista_prueba)
        fabrica = RequestFactory()
        for metodo in ("get", "post"):
            with self.subTest(metodo=metodo):
                request = getattr(fabrica, metodo)("/")
                request.user = self.reponedor
                with self.assertRaises(PermissionDenied):
                    vista(request)

    def test_el_servidor_aplica_perfiles_exclusivos_y_compartidos(self):
        usuarios = (self.reponedor, self.jefatura)
        for ruta, estados, perfil in (
            ("prueba_reponedor", (204, 403), PerfilPersonal.REPONEDOR),
            ("prueba_jefatura", (403, 204), PerfilPersonal.JEFATURA),
            ("prueba_compartida", (204, 204), PerfilPersonal.JEFATURA),
        ):
            for usuario, estado in zip(usuarios, estados):
                with self.subTest(ruta=ruta, usuario=usuario.username):
                    self.verificar_metodos(
                        self.cliente_con_csrf(usuario), ruta, estado, perfil,
                    )

    def test_post_con_perfil_correcto_tambien_requiere_csrf(self):
        cliente = self.cliente_con_csrf(self.jefatura)
        respuesta = cliente.post(reverse("prueba_jefatura"))
        self.assertEqual(respuesta.status_code, 403)
        self.assertNotIn("vista_ejecutada", cliente.session)
