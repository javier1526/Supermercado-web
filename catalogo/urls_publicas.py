from django.urls import path

from . import views_publicas


app_name = "cliente"

urlpatterns = [
    path("", views_publicas.buscar_productos, name="inicio"),
    path("sugerencias/", views_publicas.sugerir_productos, name="sugerencias"),
    path("categorias/", views_publicas.listar_categorias, name="categorias"),
    path("ubicacion/", views_publicas.ubicacion_general, name="ubicacion"),
    path("ubicacion/plano/", views_publicas.plano_general, name="plano"),
    path(
        "ubicacion/pasillos/", views_publicas.listar_pasillos, name="pasillos",
    ),
    path(
        "ubicacion/pasillos/<int:pasillo_id>/",
        views_publicas.productos_pasillo,
        name="productos_pasillo",
    ),
    path(
        "categorias/<int:categoria_id>/", views_publicas.productos_categoria,
        name="productos_categoria",
    ),
    path(
        "productos/<int:producto_id>/", views_publicas.ficha_producto,
        name="ficha_producto",
    ),
    path(
        "productos/<int:producto_id>/foto/",
        views_publicas.fotografia_producto,
        name="fotografia_producto",
    ),
    path(
        "productos/<int:producto_id>/ubicacion/foto/",
        views_publicas.fotografia_ubicacion,
        name="fotografia_ubicacion",
    ),
]
