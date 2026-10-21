"""Tratamiento de las imágenes que suben los usuarios para su perfil: foto, cabecera y fondo.

Cada imagen se comprueba con Pillow, se gira según su EXIF (las fotos del móvil vienen tumbadas), se
recorta o se reduce al tamaño de su uso y se guarda en WebP con un nombre aleatorio. Al volver a
codificarla se pierden los metadatos EXIF, que en las fotos del móvil pueden llevar la ubicación.
"""

import secrets
from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

# Por tipo de imagen: (ancho, alto, si se recorta a esa proporción o solo se reduce, peso máximo en MB).
KINDS = {
    "avatar": (400, 400, True, 5),
    "banner": (1500, 500, True, 8),
    "background": (1920, 1200, False, 8),
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
    width, height, crop, max_mb = KINDS[kind]
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
    if crop:
        image = ImageOps.fit(image, (width, height), Image.Resampling.LANCZOS)
    else:
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, "WEBP", quality=85)
    return ContentFile(buffer.getvalue(), name=f"{kind}-{secrets.token_hex(8)}.webp")
