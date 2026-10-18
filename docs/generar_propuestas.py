"""Genera docs/NextPlay-propuestas.pdf con las propuestas de mejora guardadas en docs/propuestas.json.

    .venv/Scripts/python docs/generar_propuestas.py

Con --hook lee por stdin el aviso de edición de Claude Code y solo regenera si se tocó propuestas.json.
Cada propuesta del JSON tiene: titulo, fecha (AAAA-MM-DD), estado (pendiente, en curso, hecha o
descartada), prioridad (alta, media o baja; solo las abiertas), tipo, resumen, motivo, pasos (lista) y notas.
Dentro de cada prioridad se respeta el orden del JSON, así que la primera es la más urgente.
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
from reportlab.platypus import KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from generar_pdf import ACCENT, CODE_BG, MUTED, register_fonts

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "propuestas.json"
OUTPUT = ROOT / "docs" / "NextPlay-propuestas.pdf"
# Color de la etiqueta de cada estado.
STATES = {
    "pendiente": colors.HexColor("#d97706"),
    "en curso": colors.HexColor("#2563eb"),
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


def load():
    """Lee las propuestas del JSON: abiertas por prioridad (en el orden del JSON) y luego hechas y descartadas."""
    proposals = json.loads(SOURCE.read_text(encoding="utf-8"))
    order = list(PRIORITIES)

    def rank(p):
        """Posición de la propuesta: cerradas al final y, entre las abiertas, por prioridad."""
        if p["estado"] in CLOSED:
            return (1, list(STATES).index(p["estado"]))
        return (0, order.index(p.get("prioridad")) if p.get("prioridad") in order else len(order))

    return sorted(proposals, key=rank)


def build():
    """Genera el PDF y devuelve cuántas propuestas contiene."""
    regular, bold = register_fonts()
    styles = {
        "title": ParagraphStyle("title", fontName=bold, fontSize=24, leading=30, textColor=ACCENT, alignment=TA_CENTER),
        "subtitle": ParagraphStyle("subtitle", fontName=regular, fontSize=10, leading=14, textColor=MUTED, alignment=TA_CENTER),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=14, leading=18, spaceAfter=2),
        "label": ParagraphStyle("label", fontName=bold, fontSize=9, leading=12, textColor=ACCENT, spaceBefore=6),
        "body": ParagraphStyle("body", fontName=regular, fontSize=10, leading=14),
        "muted": ParagraphStyle("muted", fontName=regular, fontSize=9, leading=12, textColor=MUTED),
    }
    proposals = load()
    counts = {state: sum(1 for p in proposals if p["estado"] == state) for state in STATES}
    summary = " · ".join(f"{n} {state}{'s' if n != 1 else ''}" for state, n in counts.items() if n)

    story = [
        Paragraph("NextPlay · Propuestas", styles["title"]),
        Paragraph(f"{summary} · actualizado el {datetime.now():%d/%m/%Y %H:%M}", styles["subtitle"]),
        Spacer(1, 0.8 * cm),
    ]

    index = [["#", "Propuesta", "Prioridad", "Tipo", "Estado"]]
    for number, p in enumerate(proposals, 1):
        index.append([
            str(number), Paragraph(escape(p["titulo"]), styles["body"]), Paragraph(_badge(p.get("prioridad", "—"), PRIORITIES, p.get("prioridad")), styles["body"]),
            p.get("tipo", ""), p["estado"],
        ])
    table = Table(index, colWidths=[0.9 * cm, 8.6 * cm, 2.2 * cm, 2.8 * cm, 2.5 * cm])
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), regular, 9),
        ("FONT", (0, 0), (-1, 0), bold, 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CODE_BG]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story += [table, Spacer(1, 0.8 * cm)]

    for number, p in enumerate(proposals, 1):
        badges = _badge(p["estado"].upper(), STATES, p["estado"])
        if p.get("prioridad") and p["estado"] not in CLOSED:
            badges = f"{_badge('PRIORIDAD ' + p['prioridad'].upper(), PRIORITIES, p['prioridad'])} · {badges}"
        block = [
            Paragraph(f"{number}. {escape(p['titulo'])}", styles["h2"]),
            Paragraph(f"{badges} · {escape(p.get('tipo', ''))} · propuesta el {_date(p['fecha'])}", styles["muted"]),
            Paragraph("Qué es", styles["label"]),
            Paragraph(escape(p["resumen"]), styles["body"]),
        ]
        if p.get("motivo"):
            block += [Paragraph("Por qué", styles["label"]), Paragraph(escape(p["motivo"]), styles["body"])]
        if p.get("pasos"):
            block += [
                Paragraph("Cómo se haría", styles["label"]),
                ListFlowable(
                    [ListItem(Paragraph(escape(step), styles["body"]), leftIndent=12) for step in p["pasos"]],
                    bulletType="1", bulletFontName=regular, bulletFontSize=9, leftIndent=14,
                ),
            ]
        if p.get("notas"):
            block += [Paragraph("Notas", styles["label"]), Paragraph(escape(p["notas"]), styles["muted"])]
        story += [KeepTogether(block), Spacer(1, 0.7 * cm)]

    def footer(canvas, doc):
        """Pie de página con el nombre del documento y el número de página."""
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(2 * cm, 1.2 * cm, "NextPlay · Propuestas")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
        title="NextPlay · Propuestas", author="NextPlay",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return len(proposals)


def _badge(text, palette, key):
    """Texto en negrita con el color que le toca a key en la paleta (estado o prioridad), o gris si no está."""
    color = palette.get(key, MUTED).hexval().replace("0x", "#")
    return f'<font color="{color}"><b>{escape(text)}</b></font>'


def _date(value):
    """Fecha AAAA-MM-DD en formato español (17/10/2026)."""
    return date.fromisoformat(value).strftime("%d/%m/%Y")


def touched_proposals():
    """Con --hook, indica si la edición que avisa Claude Code (JSON por stdin) fue a docs/propuestas.json."""
    try:
        path = (json.load(sys.stdin).get("tool_input") or {}).get("file_path") or ""
    except ValueError:
        return False
    return path.replace("\\", "/").endswith("docs/propuestas.json")


if __name__ == "__main__":
    if "--hook" in sys.argv and not touched_proposals():
        sys.exit(0)
    count = build()
    print(f"{OUTPUT.relative_to(ROOT)}: {count} propuestas.")
