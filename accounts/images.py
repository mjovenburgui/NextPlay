"""Tratamiento de las imágenes que suben los usuarios: la foto, la cabecera y el fondo del perfil y las de las reseñas y publicaciones.

Cada imagen se comprueba con Pillow, se gira según su EXIF (las fotos del móvil vienen tumbadas), se
reduce hasta caber en el tamaño de su uso y se guarda en WebP con un nombre aleatorio. No se recorta: se
guarda entera y el usuario elige qué parte se ve, o que se vea entera, con el encuadre del perfil
(Profile.avatar_x, avatar_y, avatar_zoom, avatar_fit y los de la cabecera y el fondo). De las imágenes de
las reseñas y de las publicaciones se guardan dos tamaños: la grande, para el visor, y una miniatura. Al volver a codificarla se
pierden los metadatos EXIF, que en las fotos del móvil pueden llevar la ubicación.
"""

import secrets
from io import BytesIO
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError


class Kind(NamedTuple):
    """Medidas máximas (en píxeles) y peso máximo de la subida (en MB) de un tipo de imagen."""

    width: int
    height: int
    max_mb: int


# Editar perfil y el editor de reseñas le pasan estas medidas al navegador, que reduce antes de subirlas las
# fotos que no caben.
KINDS = {
    "avatar": Kind(800, 800, 5),
    "banner": Kind(2000, 1500, 8),
    "background": Kind(1920, 1200, 8),
    "review": Kind(1920, 1920, 8),
    "review_thumb": Kind(720, 720, 8),
    "post": Kind(1920, 1920, 8),
    "post_thumb": Kind(720, 720, 8),
}
FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
# Las más grandes se rechazan antes de descomprimirlas: una imagen pequeña en disco puede ocupar gigas en memoria.
MAX_PIXELS = 40_000_000
INVALID = "No se ha podido leer la imagen: sube un JPG, PNG, WebP o GIF."


def process(upload, kind):
    """Devuelve la imagen subida lista para guardar: un ContentFile en WebP con nombre «<tipo>-<aleatorio>.webp».

    kind es una clave de KINDS. Lanza ValidationError con un mensaje para el usuario si no es una
    imagen, es de otro formato, pesa más de lo permitido o tiene demasiados píxeles. De los GIF
    animados se queda con el primer fotograma.
    """
    width, height, max_mb = KINDS[kind]
    if upload.size > max_mb * 1024 * 1024:
        raise ValidationError(f"La imagen pesa demasiado: el máximo son {max_mb} MB.")
    upload.seek(0)
    try:
        image = Image.open(upload)
        if image.format not in FORMATS:
            raise ValidationError(INVALID)
        if image.width * image.height > MAX_PIXELS:
            raise ValidationError("La imagen tiene demasiados píxeles: redúcela antes de subirla.")
        image = ImageOps.exif_transpose(image)
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        image = image.convert("RGBA" if has_alpha else "RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError(INVALID) from exc
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, "WEBP", quality=85)
    return ContentFile(buffer.getvalue(), name=f"{kind}-{secrets.token_hex(8)}.webp")
