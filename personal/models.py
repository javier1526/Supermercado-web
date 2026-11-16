from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import models


class PerfilPersonal(models.TextChoices):
    REPONEDOR = "reponedor", "Reponedor"
    JEFATURA = "jefatura", "Jefatura"


class PersonalTienda(models.Model):
    id_trabajador = models.BigAutoField(primary_key=True)
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
    )
    perfil = models.CharField(
        max_length=12,
        choices=PerfilPersonal,
        db_collation="utf8mb4_0900_bin",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(perfil__in=PerfilPersonal.values),
                name="personal_perfil_valido",
            ),
        ]

    def clean(self):
        super().clean()
        try:
            usuario = self.usuario
        except ObjectDoesNotExist:
            return

        if not usuario.first_name.strip():
            raise ValidationError({
                "usuario": "La cuenta debe tener un nombre.",
            })
