"""Genera docs/NextPlay-propuestas.pdf con las propuestas de mejora guardadas en docs/propuestas.json.

    .venv/Scripts/python docs/generar_propuestas.py

Con --hook lee por stdin el aviso de edición de Claude Code y solo regenera si se tocó uno de los dos JSON.
Cada propuesta del JSON tiene: titulo, fecha (AAAA-MM-DD), estado (pendiente, en curso, pendiente de revisión, hecha o
descartada), prioridad (alta, media o baja; solo las abiertas), tipo, resumen, motivo, pasos (lista) y notas.
Dentro de cada prioridad se respeta el orden del JSON, así que la primera es la más urgente. Las pendientes de
revisión ya están hechas y en main, pero falta que el usuario las pruebe: van después de las abiertas y antes de las
hechas y descartadas, y sin prioridad.

Las que se han revisado llevan además revision, que el PDF enseña aparte: su fecha, cómo está hoy (hoy), cómo se haría
(plan, una lista que puede faltar) y formas de profundizar (profundizar, otra lista). La revisión de la lista entera,
lo que solo se ve mirándolas todas juntas, está en docs/propuestas_revision.json y va al principio del PDF.
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    CondPageBreak, Flowable, HRFlowable, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from generar_pdf import ACCENT, CODE_BG, MUTED, register_fonts

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "propuestas.json"
REVIEW_SOURCE = ROOT / "docs" / "propuestas_revision.json"
OUTPUT = ROOT / "docs" / "NextPlay-propuestas.pdf"
# Color de la etiqueta de cada estado.
STATES = {
    "pendiente": colors.HexColor("#d97706"),
    "en curso": colors.HexColor("#2563eb"),
    "pendiente de revisión": colors.HexColor("#7c3aed"),
    "hecha": colors.HexColor("#059669"),
    "descartada": colors.HexColor("#6b7280"),
}
# Color de cada prioridad; las abiertas van de alta a baja y las hechas o descartadas al final.
PRIORITIES = {
    "alta": colors.HexColor("#dc2626"),
    "media": colors.HexColor("#d97706"),
    "baja": colors.HexColor("#6b7280"),
}
CLOSED = ("hecha", "descartada")
# Hecha y en main, pero sin probar por el usuario: va entre las abiertas y las cerradas y, como estas, sin prioridad.
REVIEW = "pendiente de revisión"
RULE = colors.HexColor("#e5e7eb")
# Sitio que tiene que quedar en la página para empezar una sección: si no, pasa a la siguiente con su título.
MIN_SECTION = 2.5 * cm


class Bookmark(Flowable):
    """Entrada del panel de marcadores del PDF: no ocupa sitio y lleva a donde está."""

    def __init__(self, key, title, level=0):
        """key es su nombre interno (único), title lo que se lee en el panel y level, su nivel de sangría."""
        super().__init__()
        self.key, self.title, self.level = key, title, level

    def wrap(self, available_width, available_height):
        """No ocupa nada en la página."""
        return 0, 0

    def draw(self):
        """Apunta este sitio de la página en el panel de marcadores."""
        self.canv.bookmarkPage(self.key)
        self.canv.addOutlineEntry(self.title, self.key, level=self.level)


def load():
    """Lee las propuestas del JSON: abiertas por prioridad (en el orden del JSON), luego las pendientes de revisión
    y al final hechas y descartadas."""
    proposals = json.loads(SOURCE.read_text(encoding="utf-8"))
    order = list(PRIORITIES)

    def rank(p):
        """Posición: abiertas por prioridad, luego las pendientes de revisión y al final las cerradas."""
        if p["estado"] in CLOSED:
            return (2, list(STATES).index(p["estado"]))
        if p["estado"] == REVIEW:
            return (1, 0)
        return (0, order.index(p.get("prioridad")) if p.get("prioridad") in order else len(order))

    return sorted(proposals, key=rank)


def build():
    """Genera el PDF y devuelve cuántas propuestas contiene."""
    regular, bold = register_fonts()
    body = ParagraphStyle("body", fontName=regular, fontSize=10, leading=14)
    styles = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=24, leading=30, textColor=ACCENT, alignment=TA_CENTER),
        "subtitle": ParagraphStyle("subtitle", fontName=regular, fontSize=10, leading=14, textColor=MUTED, alignment=TA_CENTER),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=18, leading=23, textColor=ACCENT, spaceAfter=6),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=14, leading=18, spaceAfter=2),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=12, leading=16, textColor=ACCENT, spaceBefore=14, spaceAfter=2),
        "label": ParagraphStyle("label", fontName=bold, fontSize=9, leading=12, textColor=ACCENT, spaceBefore=6),
        "band": ParagraphStyle(
            "band", fontName=bold, fontSize=9, leading=12, textColor=ACCENT, backColor=CODE_BG,
            borderPadding=(4, 6, 4, 6), leftIndent=6, rightIndent=6, spaceBefore=12, spaceAfter=4,
        ),
        "body": body,
        "block": ParagraphStyle("block", parent=body, spaceBefore=8),
        "item": ParagraphStyle("item", parent=body, leftIndent=16, bulletFontName=regular, bulletFontSize=9, spaceBefore=2),
        "link": ParagraphStyle("link", parent=body, fontSize=9, leading=12, leftIndent=10, spaceBefore=2),
        "muted": ParagraphStyle("muted", fontName=regular, fontSize=9, leading=12, textColor=MUTED),
        "cell": ParagraphStyle("cell", fontName=regular, fontSize=9, leading=11),
    }
    proposals = load()
    numbers = {p["titulo"]: number for number, p in enumerate(proposals, 1)}
    counts = {state: sum(1 for p in proposals if p["estado"] == state) for state in STATES}
    summary = " · ".join(f"{n} {_plural(state, n)}" for state, n in counts.items() if n)

    story = [
        Paragraph("NextPlay · Propuestas", styles["title"]),
        Paragraph(f"{summary} · actualizado el {datetime.now():%d/%m/%Y %H:%M}", styles["subtitle"]),
        Spacer(1, 0.8 * cm),
    ]
    story += _review_story(numbers, styles, bold)

    index = [["#", "Propuesta", "Prioridad", "Tipo", "Estado"]]
    for number, p in enumerate(proposals, 1):
        index.append([
            str(number), Paragraph(f'<a href="#p{number}">{escape(p["titulo"])}</a>', styles["body"]),
            Paragraph(_badge(p.get("prioridad", "—"), PRIORITIES, p.get("prioridad")), styles["body"]),
            p.get("tipo", ""), Paragraph(escape(p["estado"]), styles["cell"]),
        ])
    table = Table(index, colWidths=[0.9 * cm, 8.6 * cm, 2.2 * cm, 2.8 * cm, 2.5 * cm], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), regular, 9),
        ("FONT", (0, 0), (-1, 0), bold, 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CODE_BG]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story += [Bookmark("indice", "Índice"), Paragraph("Índice", styles["h1"]), table, Spacer(1, 0.8 * cm)]

    group = None
    for number, p in enumerate(proposals, 1):
        marks = []
        if _group(p) != group:
            group = _group(p)
            marks.append(Bookmark(f"grupo{number}", group))
        marks.append(Bookmark(f"marca{number}", f"{number}. {p['titulo']}", level=1))
        story += _proposal(p, number, marks, styles) + [Spacer(1, 0.7 * cm)]

    def footer(canvas, doc):
        """Pie de página con el nombre del documento y el número de página."""
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(2 * cm, 1.2 * cm, "NextPlay · Propuestas")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")
        canvas.restoreState()

    def first_page(canvas, doc):
        """Primera página: el pie y que el PDF se abra con el panel de marcadores a la vista."""
        canvas.showOutline()
        footer(canvas, doc)

    doc = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title="NextPlay · Propuestas", author="NextPlay",
    )
    doc.build(story, onFirstPage=first_page, onLaterPages=footer)
    return len(proposals)


def _review_story(numbers, styles, bold):
    """Lo que va antes del índice: la revisión de la lista entera (docs/propuestas_revision.json), con enlaces a las
    propuestas de las que habla (numbers: su número por título). Nada si ese archivo no existe."""
    if not REVIEW_SOURCE.exists():
        return []
    review = json.loads(REVIEW_SOURCE.read_text(encoding="utf-8"))

    def blocks(entries, title, text):
        """Cada bloque de entries: su título en negrita (la clave title), su explicación (text) y sus tareas enlazadas."""
        result = []
        for entry in entries:
            result += [
                CondPageBreak(MIN_SECTION),
                Paragraph(f'<font name="{bold}">{escape(entry[title])}.</font> {escape(entry[text])}', styles["block"]),
                *[_task_link(task, numbers, styles) for task in entry["tareas"]],
            ]
        return result

    return [
        Bookmark("revision", "Revisión de la lista"),
        Paragraph(f"Revisión de la lista · {_date(review['fecha'])}", styles["h1"]),
        Paragraph(escape(review["intro"]), styles["body"]),
        CondPageBreak(MIN_SECTION),
        Paragraph("Lo primero", styles["h3"]),
        *blocks(review["primero"], "titulo", "texto"),
        CondPageBreak(MIN_SECTION),
        Paragraph("Lo que cambia al mirarla entera", styles["h3"]),
        *blocks(review["hallazgos"], "titulo", "texto"),
        CondPageBreak(MIN_SECTION),
        Paragraph("Piezas que sirven a varias tareas", styles["h3"]),
        *blocks(review["piezas"], "titulo", "texto"),
        CondPageBreak(MIN_SECTION),
        Paragraph("En qué orden lo haría", styles["h3"]),
        *blocks(review["orden"], "fase", "texto"),
        CondPageBreak(MIN_SECTION),
        Paragraph("Ajustes que propongo a la lista", styles["h3"]),
        Paragraph("No los he aplicado: es decisión tuya.", styles["muted"]),
        *_items(review["ajustes"], styles["item"], numbered=False),
        PageBreak(),
    ]


def _task_link(title, numbers, styles):
    """Línea con el número y el título de una propuesta, enlazada a ella (sin enlace si ya no hay ninguna con ese título)."""
    number = numbers.get(title)
    if number is None:
        return Paragraph(escape(title), styles["link"])
    return Paragraph(f'<a href="#p{number}" color="{_hex(ACCENT)}">{number}. {escape(title)}</a>', styles["link"])


def _proposal(p, number, marks, styles):
    """Los elementos del PDF de una propuesta. marks son sus marcadores, que van pegados a su título.

    Solo su título y su resumen se quedan juntos: el resto se parte entre páginas por donde caiga, para no dejar medias
    páginas en blanco, y cada sección pasa a la siguiente con su título si no queda sitio para empezarla.
    """
    badges = _badge(p["estado"].upper(), STATES, p["estado"])
    if p.get("prioridad") and p["estado"] not in (*CLOSED, REVIEW):
        badges = f"{_badge('PRIORIDAD ' + p['prioridad'].upper(), PRIORITIES, p['prioridad'])} · {badges}"

    def section(label, *content):
        """Una sección con su título, que no se queda solo al final de una página."""
        return [CondPageBreak(MIN_SECTION), Paragraph(label, styles["label"]), *content]

    story = [KeepTogether([
        HRFlowable(width="100%", thickness=0.5, color=RULE, spaceAfter=8),
        *marks,
        Paragraph(f'<a name="p{number}"/>{number}. {escape(p["titulo"])}', styles["h2"]),
        Paragraph(f"{badges} · {escape(p.get('tipo', ''))} · propuesta el {_date(p['fecha'])}", styles["muted"]),
        Paragraph("Qué es", styles["label"]),
        Paragraph(escape(p["resumen"]), styles["body"]),
    ])]
    if p.get("motivo"):
        story += section("Por qué", Paragraph(escape(p["motivo"]), styles["body"]))
    if p.get("pasos"):
        story += section("Pasos previstos", *_items(p["pasos"], styles["item"]))
    if p.get("notas"):
        story += section("Notas", Paragraph(escape(p["notas"]), styles["muted"]))
    review = p.get("revision")
    if review:
        story += [CondPageBreak(MIN_SECTION + 1 * cm), Paragraph(f"REVISIÓN DEL {_date(review['fecha'])}", styles["band"])]
        story += section("Cómo está hoy", Paragraph(escape(review["hoy"]), styles["body"]))
        if review.get("plan"):
            story += section("Cómo lo haría", *_items(review["plan"], styles["item"]))
        if review.get("profundizar"):
            story += section("Para profundizar", *_items(review["profundizar"], styles["item"], numbered=False))
    return story


def _items(texts, style, numbered=True):
    """Una lista como párrafos sueltos con su número o su punto, para que se parta bien entre páginas."""
    return [
        Paragraph(escape(text), style, bulletText=f"{position}." if numbered else "•")
        for position, text in enumerate(texts, 1)
    ]


def _group(p):
    """Grupo de la propuesta en el panel de marcadores: su prioridad, «Pendientes de revisión» o, si está cerrada,
    «Hechas y descartadas»."""
    if p["estado"] in CLOSED:
        return "Hechas y descartadas"
    if p["estado"] == REVIEW:
        return "Pendientes de revisión"
    return f"Prioridad {p['prioridad']}" if p.get("prioridad") in PRIORITIES else "Sin prioridad"


def _plural(state, n):
    """El estado como va en el resumen de la portada: en plural si son varias («35 pendientes», «4 pendientes de
    revisión»), salvo «en curso», que no cambia."""
    if n == 1 or state == "en curso":
        return state
    first, _, rest = state.partition(" ")
    return f"{first}s {rest}".rstrip()


def _hex(color):
    """Color en el formato que usan las etiquetas de los párrafos: «#rrggbb»."""
    return color.hexval().replace("0x", "#")


def _badge(text, palette, key):
    """Texto en negrita con el color que le toca a key en la paleta (estado o prioridad), o gris si no está."""
    return f'<font color="{_hex(palette.get(key, MUTED))}"><b>{escape(text)}</b></font>'


def _date(value):
    """Fecha AAAA-MM-DD en formato español (17/10/2026)."""
    return date.fromisoformat(value).strftime("%d/%m/%Y")


def touched_proposals():
    """Con --hook, indica si la edición que avisa Claude Code (JSON por stdin) fue a uno de los JSON de las propuestas."""
    try:
        path = (json.load(sys.stdin).get("tool_input") or {}).get("file_path") or ""
    except ValueError:
        return False
    return path.replace("\\", "/").endswith(("docs/propuestas.json", "docs/propuestas_revision.json"))


if __name__ == "__main__":
    if "--hook" in sys.argv and not touched_proposals():
        sys.exit(0)
    count = build()
    print(f"{OUTPUT.relative_to(ROOT)}: {count} propuestas.")
