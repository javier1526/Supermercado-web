from django import forms
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.core.exceptions import ValidationError
from django.db.models import Value
from django.db.models.functions import Replace, Trim, Upper

from .forms import normalizar_rut
from .models import PerfilPersonal, PersonalTienda


class FormularioPersonalAdministrativo(forms.ModelForm):
    perfil = forms.ChoiceField(
        label="Perfil de tienda",
        choices=[("", "Sin perfil de tienda"), *PerfilPersonal.choices],
        required=False,
        help_text="Selecciona Reponedor o Jefatura para el personal.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.personal_existente = getattr(
            self.instance, "personaltienda", None,
        )
        if self.personal_existente is not None:
            self.initial["perfil"] = self.personal_existente.perfil
        self.fields["username"].help_text = (
            "Para personal, ingresa su RUT con o sin puntos y guion. "
            "Las cuentas administrativas conservan su nombre de usuario."
        )
        self.fields["first_name"].label = "Nombre"
        self.fields["first_name"].help_text = (
            "Obligatorio cuando se asigna un perfil de tienda."
        )

    def clean(self):
        datos = super().clean()
        perfil = datos.get("perfil")
        if not perfil:
            if self.personal_existente is not None:
                self.add_error(
                    "perfil", "Selecciona un perfil de tienda válido.",
                )
            return datos

        if datos.get("set_usable_password") is False:
            self.add_error(
                "usable_password", "El personal necesita una contraseña."
            )
        rut = datos.get("username")
        if rut:
            try:
                rut = normalizar_rut(rut)
            except ValidationError as error:
                self.add_error("username", error)
            else:
                datos["username"] = rut
                usuarios = self._meta.model.objects.exclude(
                    pk=self.instance.pk,
                )
                usuarios = usuarios.annotate(rut_compacto=Replace(
                    Replace(Upper(Trim("username")), Value("."), Value("")),
                    Value("-"), Value(""),
                ))
                if usuarios.filter(rut_compacto=rut.replace("-", "")).exists():
                    self.add_error(
                        "username", "Ya existe una cuenta con ese RUT.",
                    )
        return datos

    def _post_clean(self):
        super()._post_clean()
        perfil = self.cleaned_data.get("perfil")
        if perfil:
            personal = PersonalTienda(usuario=self.instance, perfil=perfil)
            try:
                personal.clean()
            except ValidationError as error:
                self.add_error("first_name", error.message_dict["usuario"])


class FormularioAltaUsuario(
    FormularioPersonalAdministrativo, AdminUserCreationForm,
):
    class Meta(AdminUserCreationForm.Meta):
        fields = ("username", "first_name", "perfil")


class FormularioCambioUsuario(
    FormularioPersonalAdministrativo, UserChangeForm,
):
    pass
