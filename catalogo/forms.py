from PIL import Image
from django import forms
from django.core.validators import FileExtensionValidator
from django.db import connection

from .models import Pasillo, Producto


MENSAJE_CODIGO_DUPLICADO = "Ya existe un producto con este código de barras."


class FormularioRegistroProducto(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categoria"].empty_label = "Selecciona una categoría"

    class Meta:
        model = Producto
        fields = (
            "codigo_barras", "nombre_producto", "marca", "categoria",
            "descripcion", "fotografia_comercial",
        )
        labels = {
            "codigo_barras": "Código de barras ficticio",
            "nombre_producto": "Nombre completo del producto",
            "marca": "Marca",
            "categoria": "Categoría",
            "descripcion": "Descripción",
            "fotografia_comercial": "Fotografía comercial",
        }
        help_texts = {
            "codigo_barras": "Hasta 32 caracteres, con ceros iniciales.",
            "fotografia_comercial": "Puedes continuar sin foto.",
        }
        error_messages = {
            "codigo_barras": {"unique": MENSAJE_CODIGO_DUPLICADO},
            "categoria": {
                "invalid_choice": "Selecciona una categoría válida.",
            },
            "fotografia_comercial": {
                "invalid_image": "Adjunta un archivo de imagen válido.",
            },
        }
        widgets = {
            "codigo_barras": forms.TextInput(attrs={"class": "form-control"}),
            "nombre_producto": forms.TextInput(
                attrs={"class": "form-control"},
            ),
            "marca": forms.TextInput(attrs={"class": "form-control"}),
            "categoria": forms.Select(attrs={"class": "form-select"}),
            "descripcion": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
            }),
            "fotografia_comercial": forms.FileInput(attrs={
                "class": "form-control",
                "accept": "image/*",
            }),
        }


class FormularioAprobacion(forms.Form):
    precio_inicial = forms.CharField(
        label="Precio inicial",
        help_text="Ingresa un entero mayor que cero en pesos chilenos.",
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "inputmode": "numeric",
        }),
    )

    def clean_precio_inicial(self):
        return validar_precio_normal(self.cleaned_data["precio_inicial"])


class FormularioStock(forms.Form):
    stock = forms.CharField(
        label="Nueva cantidad de stock",
        help_text="Ingresa un entero mayor o igual a cero.",
        widget=forms.TextInput(attrs={
            "class": "form-control", "inputmode": "numeric",
        }),
    )

    def clean_stock(self):
        texto = self.cleaned_data["stock"]
        if not texto.isascii() or not texto.isdecimal():
            raise forms.ValidationError(
                "Ingresa una cantidad entera mayor o igual a cero.",
            )
        campo = Producto._meta.get_field("stock")
        _, maximo = connection.ops.integer_field_range(
            campo.get_internal_type(),
        )
        digitos = texto.lstrip("0") or "0"
        if len(digitos) <= len(str(maximo)):
            cantidad = int(digitos)
            if cantidad <= maximo:
                return cantidad
        raise forms.ValidationError(f"La cantidad máxima es {maximo}.")


class SelectorPasillo(forms.ModelChoiceField):
    def label_from_instance(self, pasillo):
        return (
            f"Pasillo {pasillo.numero_pasillo} · "
            f"Supermercado {pasillo.supermercado_id}"
        )


class FormularioUbicacion(forms.Form):
    pasillo = SelectorPasillo(
        queryset=Pasillo.objects.order_by("supermercado_id", "numero_pasillo"),
        required=False,
        label="Pasillo registrado",
        empty_label="Sin pasillo definido",
        error_messages={"invalid_choice": "Selecciona un pasillo registrado."},
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    fotografia_referencial = forms.ImageField(
        required=False,
        label="Cambiar fotografía de ubicación",
        help_text=(
            "JPG, JPEG o PNG, máximo 1 MB. Sin archivo, conserva la actual."
        ),
        validators=[FileExtensionValidator(["jpg", "jpeg", "png"])],
        error_messages={
            "invalid_image": "Adjunta una imagen JPG, JPEG o PNG válida.",
        },
        widget=forms.FileInput(attrs={
            "class": "form-control", "accept": ".jpg,.jpeg,.png",
        }),
    )

    def clean_fotografia_referencial(self):
        fotografia = self.cleaned_data["fotografia_referencial"]
        if fotografia:
            if fotografia.size > 1024 * 1024:
                raise forms.ValidationError("La fotografía supera 1 MB.")
            if fotografia.image.format not in ("JPEG", "PNG"):
                raise forms.ValidationError("Solo se permite JPG, JPEG o PNG.")
            try:
                # verify() puede aceptar JPEG truncados sin decodificarlos.
                with Image.open(fotografia) as imagen:
                    imagen.load()
            except (OSError, ValueError) as error:
                raise forms.ValidationError(
                    "Adjunta una imagen JPG, JPEG o PNG válida.",
                ) from error
            finally:
                fotografia.seek(0)
        return fotografia


def validar_precio_normal(texto):
    if not texto.isascii() or not texto.isdecimal():
        raise forms.ValidationError("Ingresa un precio entero positivo.")

    campo = Producto._meta.get_field("precio_normal")
    _, maximo = connection.ops.integer_field_range(
        campo.get_internal_type(),
    )
    digitos = texto.lstrip("0")
    if not digitos:
        raise forms.ValidationError("Ingresa un precio mayor que cero.")
    if len(digitos) <= len(str(maximo)):
        precio = int(digitos)
        if precio <= maximo:
            return precio
    raise forms.ValidationError(f"El precio máximo es {maximo} CLP.")


class FormularioPrecioNormal(forms.Form):
    precio_normal = forms.CharField(
        label="Nuevo precio normal",
        help_text="Ingresa un entero mayor que cero en pesos chilenos.",
        widget=forms.TextInput(attrs={
            "class": "form-control", "inputmode": "numeric",
        }),
    )

    def clean_precio_normal(self):
        return validar_precio_normal(self.cleaned_data["precio_normal"])
