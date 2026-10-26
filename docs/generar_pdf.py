"""Genera docs/NextPlay-funciones.pdf con todas las funciones del proyecto y sus docstrings.

Lee el código con ast (no importa Django), así que funciona sin base de datos ni .env:

    .venv/Scripts/python -m pip install -r requirements-dev.txt
    .venv/Scripts/python docs/generar_pdf.py

Con --hook lee por stdin el aviso de edición de Claude Code y solo regenera si se tocó un .py.
"""

import ast
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "NextPlay-funciones.pdf"
# Archivos que no se documentan: tests, migraciones y el arranque estándar de Django.
SKIP = ("migrations/", "tests.py", "manage.py", "asgi.py", "wsgi.py", "docs/")
APPS = {
    "config": "Configuración del proyecto: ajustes y URL principales.",
    "accounts": "Usuarios: registro, inicio de sesión, perfil público o privado, vinculación con Steam, Xbox y PlayStation y cuentas de otras plataformas.",
    "games": "Catálogo de juegos, importación desde IGDB y precios de PC (IsThereAnyDeal), Xbox y Nintendo.",
    "library": "Biblioteca de cada usuario: qué juegos tiene, en qué estado, su valoración y su reseña.",
    "social": "Lo social: seguir a otros jugadores, las listas de seguidores y seguidos y la actividad de cada jugador.",
}
ACCENT = colors.HexColor("#7c3aed")
MUTED = colors.HexColor("#6b7280")
CODE_BG = colors.HexColor("#f3f0ff")

# Fuentes con tildes, «», → y ™: Arial en Windows, DejaVu en Linux; si no, Vera, que trae reportlab.
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]


def register_fonts():
    """Registra la fuente del documento y devuelve (normal, negrita)."""
    for regular, bold in FONT_CANDIDATES:
        if Path(regular).exists() and Path(bold).exists():
            pdfmetrics.registerFont(TTFont("Doc", regular))
            pdfmetrics.registerFont(TTFont("Doc-Bold", bold))
            return "Doc", "Doc-Bold"
    pdfmetrics.registerFont(TTFont("Doc", "Vera.ttf"))
    pdfmetrics.registerFont(TTFont("Doc-Bold", "VeraBd.ttf"))
    return "Doc", "Doc-Bold"


def source_files():
    """Archivos .py versionados en git que se documentan, ordenados por app y nombre."""
    files = subprocess.check_output(["git", "ls-files", "*.py"], cwd=ROOT, text=True).split()
    files = [f for f in files if not any(part in f for part in SKIP) and not f.endswith("__init__.py")]
    order = list(APPS)
    return sorted(files, key=lambda f: (order.index(f.split("/")[0]) if f.split("/")[0] in order else 99, f))


def signature(node):
    """Firma de una función como texto: «def nombre(args)», con sus decoradores delante."""
    decorators = "".join(f"@{ast.unparse(d)}\n" for d in node.decorator_list)
    return f"{decorators}def {node.name}({ast.unparse(node.args)})"


def parse(path):
    """(docstring del módulo, [(clase o None, docstring de la clase, [funciones])]) de un archivo.

    Cada función es (firma, docstring, línea).
    """
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    sections = []
    # En el orden del archivo; las funciones sueltas seguidas van juntas en una sección sin clase.
    for node in tree.body:
        if isinstance(node, functions):
            if not sections or sections[-1][0] is not None:
                sections.append((None, "", []))
            sections[-1][2].append((signature(node), ast.get_docstring(node) or "", node.lineno))
        elif isinstance(node, ast.ClassDef):
            methods = [
                (signature(f), ast.get_docstring(f) or "", f.lineno) for f in node.body if isinstance(f, functions)
            ]
            if methods or ast.get_docstring(node):
                sections.append((node.name, ast.get_docstring(node) or "", methods))
    return ast.get_docstring(tree) or "", sections


def text(value):
    """Escapa el texto para Paragraph y respeta los saltos de párrafo de la docstring."""
    return "<br/><br/>".join(escape(" ".join(p.split())) for p in value.split("\n\n"))


def build():
    """Genera el PDF y devuelve cuántas funciones documenta."""
    regular, bold = register_fonts()
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName=bold, fontSize=26, leading=34, textColor=ACCENT
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName=regular, fontSize=11, leading=15, textColor=MUTED, alignment=TA_CENTER
        ),
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=18, leading=24, textColor=ACCENT, spaceAfter=4),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=13, leading=17, spaceBefore=14, spaceAfter=4),
        "h3": ParagraphStyle(
            "h3", fontName=bold, fontSize=11, leading=15, textColor=ACCENT, spaceBefore=8, spaceAfter=2
        ),
        "body": ParagraphStyle("body", fontName=regular, fontSize=10, leading=14, spaceAfter=4),
        "muted": ParagraphStyle("muted", fontName=regular, fontSize=9, leading=12, textColor=MUTED),
        "code": ParagraphStyle("code", fontName="Courier-Bold", fontSize=8.5, leading=11),
        "missing": ParagraphStyle("missing", fontName=regular, fontSize=10, textColor=colors.red),
    }

    files = [(path, *parse(path)) for path in source_files()]
    files = [f for f in files if f[2]]  # fuera los archivos sin funciones (urls.py, apps.py...)
    total = sum(len(methods) for _, _, sections in files for _, _, methods in sections)

    story = [
        Spacer(1, 5 * cm),
        Paragraph("NextPlay", styles["title"]),
        Paragraph("Documentación de funciones", styles["subtitle"]),
        Spacer(1, 0.4 * cm),
        Paragraph(
            f"{total} funciones en {len(files)} archivos · generado el {datetime.now():%d/%m/%Y %H:%M}",
            styles["subtitle"],
        ),
        Spacer(1, 2 * cm),
    ]

    index = [["Archivo", "Funciones"]]
    for path, _, sections in files:
        index.append([path, str(sum(len(m) for _, _, m in sections))])
    table = Table(index, colWidths=[11 * cm, 3 * cm])
    table.setStyle(
        TableStyle([
            ("FONT", (0, 0), (-1, -1), regular, 9),
            ("FONT", (0, 0), (-1, 0), bold, 9),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CODE_BG]),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    story += [table, PageBreak()]

    current_app = None
    for path, module_doc, sections in files:
        app = path.split("/")[0]
        if app != current_app:
            if current_app is not None:
                story.append(PageBreak())
            current_app = app
            story.append(Paragraph(f"App «{app}»", styles["h1"]))
            if app in APPS:
                story.append(Paragraph(text(APPS[app]), styles["body"]))
        story.append(Paragraph(escape(path), styles["h2"]))
        if module_doc:
            story.append(Paragraph(text(module_doc), styles["muted"]))
        for cls, cls_doc, methods in sections:
            if cls:
                story.append(Paragraph(f"Clase {escape(cls)}", styles["h3"]))
                if cls_doc:
                    story.append(Paragraph(text(cls_doc), styles["muted"]))
            for sig, doc, line in methods:
                code = Table(
                    [[Paragraph(escape(sig).replace("\n", "<br/>"), styles["code"])]],
                    colWidths=[16.5 * cm],
                )
                code.setStyle(
                    TableStyle([
                        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ])
                )
                body = (
                    Paragraph(text(doc), styles["body"])
                    if doc
                    else Paragraph("Sin documentar.", styles["missing"])
                )
                story.append(
                    KeepTogether([
                        Spacer(1, 4),
                        code,
                        Spacer(1, 3),
                        body,
                        Paragraph(f"{escape(path)}, línea {line}", styles["muted"]),
                    ])
                )

    def footer(canvas, doc):
        """Pie de página con el nombre del documento y el número de página."""
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(2 * cm, 1.2 * cm, "NextPlay · Documentación de funciones")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title="NextPlay · Documentación de funciones",
        author="NextPlay",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return total


def touched_python():
    """Con --hook, indica si la edición que avisa Claude Code (JSON por stdin) fue a un .py del proyecto."""
    try:
        path = (json.load(sys.stdin).get("tool_input") or {}).get("file_path") or ""
    except ValueError:
        return False
    path = path.replace("\\", "/")
    return path.endswith(".py") and "/docs/" not in path and "/migrations/" not in path


if __name__ == "__main__":
    if "--hook" in sys.argv and not touched_python():
        sys.exit(0)
    count = build()
    print(f"{OUTPUT.relative_to(ROOT)}: {count} funciones documentadas.")
