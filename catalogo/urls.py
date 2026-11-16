from django.urls import path

from . import views


app_name = "catalogo"

urlpatterns = [
    path("gestion/", views.seleccionar_producto_gestion,
         name="seleccionar_gestion"),
    path("gestion/<int:producto_id>/", views.ficha_gestion,
         name="ficha_gestion"),
    path("gestion/<int:producto_id>/precio/", views.actualizar_precio,
         name="actualizar_precio"),
    path("gestion/<int:producto_id>/desactivar/", views.desactivar_producto,
         name="desactivar_producto"),
    path(
        "ultima-actualizacion/", views.seleccionar_ultima_actualizacion,
        name="seleccionar_actualizacion",
    ),
    path(
        "ultima-actualizacion/<int:producto_id>/",
        views.consultar_ultima_actualizacion, name="ultima_actualizacion",
    ),
    path(
        "ubicacion/", views.seleccionar_producto_ubicacion,
        name="seleccionar_ubicacion",
    ),
    path(
        "ubicacion/<int:producto_id>/", views.actualizar_ubicacion,
        name="actualizar_ubicacion",
    ),
    path(
        "ubicacion/<int:producto_id>/foto/", views.foto_ubicacion,
        name="foto_ubicacion",
    ),
    path("stock/", views.seleccionar_producto_stock, name="seleccionar_stock"),
    path(
        "stock/<int:producto_id>/",
        views.actualizar_stock,
        name="actualizar_stock",
    ),
    path("registrar/", views.registrar_producto, name="registrar_producto"),
    path("pendientes/", views.listar_pendientes, name="pendientes"),
    path(
        "pendientes/<int:producto_id>/",
        views.revisar_producto,
        name="revisar_producto",
    ),
    path(
        "pendientes/<int:producto_id>/foto/",
        views.foto_producto_pendiente,
        name="foto_producto_pendiente",
    ),
    path(
        "pendientes/<int:producto_id>/aprobar/",
        views.aprobar_producto,
        name="aprobar_producto",
    ),
    path(
        "pendientes/<int:producto_id>/rechazar/",
        views.rechazar_producto,
        name="rechazar_producto",
    ),
]
