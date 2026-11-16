from django import forms

from .models import Pasillo, Supermercado


TAMANO_MAXIMO_CSV = 1024 * 1024


class FormularioImportacionPrecios(forms.Form):
    archivo_csv = forms.FileField(
        label="Archivo CSV",
        help_text=(
            "UTF-8, con o sin BOM. Máximo 1 MiB (1.048.576 bytes). "
            "Encabezado: codigo_barras,precio_referencia."
        ),
        error_messages={
            "required": "Selecciona un archivo CSV.",
            "empty": "El archivo CSV está vacío.",
        },
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,text/csv"}),
    )

    def clean_archivo_csv(self):
        archivo = self.cleaned_data["archivo_csv"]
        if archivo.size > TAMANO_MAXIMO_CSV:
            raise forms.ValidationError(
                "El archivo CSV supera el máximo de 1 MiB."
            )
        return archivo


class FormularioPasilloAdministrativo(forms.ModelForm):
    class Meta:
        model = Pasillo
        fields = ("numero_pasillo", "supermercado")
        labels = {
            "numero_pasillo": "Número de pasillo",
            "supermercado": "Supermercado del prototipo",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        supermercado = Supermercado.objects.order_by("pk").first()
        campo = self.fields["supermercado"]
        campo.disabled = True
        campo.queryset = Supermercado.objects.filter(
            pk=supermercado.pk if supermercado else None,
        )
        if supermercado is None:
            campo.required = False
            campo.help_text = (
                "No hay un supermercado registrado. Inicialízalo según "
                "las instrucciones del README antes de crear pasillos."
            )
        else:
            self.initial["supermercado"] = supermercado.pk
            campo.help_text = (
                f"Supermercado {supermercado.pk}: primer registro por ID, "
                "utilizado también por las consultas públicas."
            )
        self.fields["numero_pasillo"].help_text = (
            "No se puede cambiar el número si el pasillo tiene ubicaciones "
            "asociadas. Los traslados se realizan desde el panel de Reponedor."
        )

    def clean(self):
        datos = super().clean()
        if datos.get("supermercado") is None:
            raise forms.ValidationError(
                "No hay un supermercado registrado. Consulta el README "
                "para inicializarlo antes de crear pasillos."
            )
        numero = datos.get("numero_pasillo")
        if self.instance.pk and numero is not None:
            # El bloqueo se mantiene hasta terminar la transacción de Admin.
            actual = Pasillo.objects.select_for_update().get(
                pk=self.instance.pk,
            )
            if (
                numero != actual.numero_pasillo
                and actual.ubicacionproducto_set.exists()
            ):
                self.add_error(
                    "numero_pasillo",
                    "No se puede renumerar un pasillo con ubicaciones "
                    "asociadas. Traslada los productos desde el panel "
                    "de Reponedor.",
                )
        return datos
