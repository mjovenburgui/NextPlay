"""Formato sencillo y seguro de las reseñas y los comentarios.

Se escribe como en muchos foros: **negrita**, *cursiva*, ||spoiler|| y [texto](https://enlace); una línea
en blanco separa párrafos. Primero se escapa todo el HTML y después se cambian solo esas marcas, así que
no se puede colar ninguna etiqueta ni script. Los enlaces solo pueden ser http o https y llevan
rel="nofollow ugc noopener" para que no sirvan de spam.
"""

import re

from django.utils.html import escape
from django.utils.safestring import mark_safe

PARAGRAPHS = re.compile(r"\n[ \t]*\n")
LINK = re.compile(r"\[([^\[\]\n]{1,200})\]\((https?://[^\s()<>\"']{1,500})\)")
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
ITALIC = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])")
SPOILER = re.compile(r"\|\|(?=\S)(.+?)(?<=\S)\|\|")
# Hueco donde va cada enlace mientras se cambian las otras marcas (para no tocar su dirección).
LINK_SLOT = re.compile(r"\x00(\d+)\x00")


def _inline(text):
    """Un párrafo ya escapado con sus marcas cambiadas por HTML."""
    links = []

    def keep(match):
        """Guarda el enlace aparte y deja su hueco."""
        links.append(match.groups())
        return f"\x00{len(links) - 1}\x00"

    def restore(match):
        """Pone el enlace guardado en su hueco."""
        label, url = links[int(match.group(1))]
        return f'<a href="{url}" rel="nofollow ugc noopener" target="_blank">{label}</a>'

    text = LINK.sub(keep, text)
    text = BOLD.sub(r"<strong>\1</strong>", text)
    text = ITALIC.sub(r"<em>\1</em>", text)
    text = SPOILER.sub(
        r'<span class="spoiler-inline" tabindex="0" role="button" aria-expanded="false" '
        r'title="Spoiler: pulsa para leerlo">\1</span>',
        text,
    )
    return LINK_SLOT.sub(restore, text)


def render(text):
    """HTML seguro de un texto con formato: un <p> por párrafo, <br> en cada salto de línea y las marcas cambiadas."""
    text = (text or "").replace("\x00", "").replace("\r\n", "\n").strip()
    paragraphs = [part.strip() for part in PARAGRAPHS.split(text) if part.strip()]
    return mark_safe("".join(f"<p>{_inline(escape(part)).replace(chr(10), '<br>')}</p>" for part in paragraphs))


def render_inline(text):
    """HTML seguro de un texto corto de una sola línea (un punto de «Lo mejor» o «Lo peor»), sin párrafo alrededor."""
    text = " ".join((text or "").replace("\x00", "").split())
    return mark_safe(_inline(escape(text)))


def plain(text):
    """El texto sin las marcas de formato y con los spoilers tapados («[spoiler]»), en una sola línea.

    Sirve para resúmenes, la portada y la descripción de la página.
    """
    text = (text or "").replace("\x00", "")
    text = SPOILER.sub("[spoiler]", text)
    text = LINK.sub(r"\1", text)
    text = BOLD.sub(r"\1", text)
    text = ITALIC.sub(r"\1", text)
    return " ".join(text.split())
