import re

from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .permisos import obtener_personal_autorizado


FORMATO_RUT = re.compile(
    r"(?:[0-9]{1,8}|[0-9]{1,3}\.[0-9]{3}|[0-9]{1,2}(?:\.[0-9]{3}){2})"
    r"-?[0-9Kk]"
)


def normalizar_rut(rut):
    rut = rut.strip()
    if not FORMATO_RUT.fullmatch(rut):
        raise forms.ValidationError("Ingresa un RUT con formato válido.")
    compacto = rut.replace(".", "").replace("-", "").upper()
    return f"{compacto[:-1]}-{compacto[-1]}"


class FormularioAcceso(AuthenticationForm):
    username = forms.CharField(
        label="RUT",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "autocomplete": "username",
            "autocapitalize": "none",
            "spellcheck": "false",
            "placeholder": "Ej. 12.345.678-9",
        }),
    )
    password = forms.CharField(
        label="Contraseña",
        strip=False,
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "autocomplete": "current-password",
            "placeholder": "Ingresa tu contraseña",
        }),
    )
    error_messages = {
        "invalid_login": "RUT o contraseña incorrectos.",
    }

    def clean_username(self):
        return normalizar_rut(self.cleaned_data["username"])

    def confirm_login_allowed(self, user):
        if obtener_personal_autorizado(user) is None:
            raise self.get_invalid_login_error()
