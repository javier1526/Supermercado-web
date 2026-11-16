from pathlib import Path, PurePosixPath

from django.contrib.staticfiles import finders
from PIL import Image


def obtener_plano_disponible(supermercado):
    if not supermercado or not supermercado.plano_general:
        return None
    ruta = supermercado.plano_general
    partes = ruta.split("/")
    if (
        partes[:2] != ["catalogo", "planos"]
        or any(parte in ("", ".", "..") for parte in partes)
        or any(caracter in ruta for caracter in ("\\", ":", "%", "?", "#"))
    ):
        return None
    carpeta = Path(__file__).parent / "static" / "catalogo" / "planos"
    try:
        archivo = finders.find(ruta)
        if not archivo:
            return None
        if not Path(archivo).resolve().is_relative_to(carpeta.resolve()):
            return None
        with Image.open(archivo) as imagen:
            if Image.registered_extensions().get(
                PurePosixPath(ruta).suffix.lower(),
            ) != imagen.format:
                return None
            imagen.verify()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    return ruta
