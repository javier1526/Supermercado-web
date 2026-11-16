from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Collate, Lower, Trim


COLACION_EXACTA = "utf8mb4_0900_bin"


class EstadoProducto(models.TextChoices):
    PENDIENTE = "pendiente", "Pendiente de aprobación"
    ACTIVO = "activo", "Activo"
    RECHAZADO = "rechazado", "Rechazado"
    INACTIVO = "inactivo", "Inactivo"


class Categoria(models.Model):
    id_categoria = models.BigAutoField(primary_key=True)
    nombre = models.CharField(max_length=120)

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    id_producto = models.BigAutoField(primary_key=True)
    codigo_barras = models.CharField(
        max_length=32,
        unique=True,
        db_collation=COLACION_EXACTA,
    )
    nombre_producto = models.CharField(max_length=180)
    marca = models.CharField(max_length=140)
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT)
    descripcion = models.TextField(blank=True, null=True)
    fotografia_comercial = models.ImageField(
        upload_to="productos/", max_length=255, blank=True, null=True,
    )
    precio_normal = models.IntegerField(
        validators=[MinValueValidator(1)], blank=True, null=True,
    )
    stock = models.IntegerField(
        validators=[MinValueValidator(0)], default=0, db_default=0,
    )
    estado = models.CharField(
        max_length=16,
        choices=EstadoProducto,
        default=EstadoProducto.PENDIENTE,
        db_collation=COLACION_EXACTA,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(codigo_barras=""),
                name="producto_codigo_obligatorio",
            ),
            models.CheckConstraint(
                condition=models.Q(stock__gte=0),
                name="producto_stock_no_negativo",
            ),
            models.CheckConstraint(
                condition=models.Q(estado__in=EstadoProducto.values),
                name="producto_estado_valido",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(precio_normal__isnull=True)
                    | models.Q(precio_normal__gt=0)
                ),
                name="producto_precio_positivo",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(estado=EstadoProducto.ACTIVO)
                    | models.Q(precio_normal__isnull=False)
                ),
                name="producto_activo_con_precio",
            ),
        ]

    def __str__(self):
        return self.nombre_producto

    @classmethod
    def existe_producto_con_nombre_y_marca(cls, nombre_producto, marca):
        """Consulta duplicados por nombre y marca para el alta de productos."""
        return (
            cls.objects.filter(
                estado__in=(EstadoProducto.PENDIENTE, EstadoProducto.ACTIVO),
            )
            .alias(
                nombre_normalizado=Collate(
                    Lower(Trim("nombre_producto")), COLACION_EXACTA,
                ),
                marca_normalizada=Collate(
                    Lower(Trim("marca")), COLACION_EXACTA,
                ),
            )
            .filter(
                nombre_normalizado=Lower(Trim(models.Value(nombre_producto))),
                marca_normalizada=Lower(Trim(models.Value(marca))),
            )
            .exists()
        )


class Supermercado(models.Model):
    id_supermercado = models.BigAutoField(primary_key=True)
    plano_general = models.CharField(max_length=255, blank=True, null=True)


class Pasillo(models.Model):
    id_pasillo = models.BigAutoField(primary_key=True)
    numero_pasillo = models.IntegerField()
    supermercado = models.ForeignKey(Supermercado, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("supermercado", "numero_pasillo"),
                name="pasillo_numero_por_supermercado",
            ),
        ]

    def __str__(self):
        return f"Pasillo {self.numero_pasillo}"


class UbicacionProducto(models.Model):
    id_ubicacion = models.BigAutoField(primary_key=True)
    producto = models.OneToOneField(Producto, on_delete=models.CASCADE)
    pasillo = models.ForeignKey(
        Pasillo, on_delete=models.PROTECT, blank=True, null=True,
    )
    fotografia_referencial = models.ImageField(
        upload_to="ubicaciones/", max_length=255, blank=True, null=True,
    )
    fecha_ultima_actualizacion = models.DateTimeField(blank=True, null=True)
    ultimo_responsable = models.ForeignKey(
        "personal.PersonalTienda", on_delete=models.PROTECT,
        blank=True, null=True,
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    fecha_ultima_actualizacion__isnull=True,
                    ultimo_responsable__isnull=True,
                ) | models.Q(
                    fecha_ultima_actualizacion__isnull=False,
                    ultimo_responsable__isnull=False,
                ),
                name="ubicacion_fecha_y_responsable_juntos",
            ),
        ]


class PrecioReferencia(models.Model):
    codigo_barras = models.CharField(
        max_length=32,
        primary_key=True,
        db_collation=COLACION_EXACTA,
    )
    precio_referencia = models.IntegerField(validators=[MinValueValidator(1)])

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(codigo_barras=""),
                name="referencia_codigo_obligatorio",
            ),
            models.CheckConstraint(
                condition=models.Q(precio_referencia__gt=0),
                name="referencia_precio_positivo",
            ),
        ]
