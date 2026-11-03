"""Genera docs/NextPlay-funciones.pdf: el mapa de pantallas de NextPlay y todas sus funciones con sus docstrings.

Lee el código con ast (no importa Django), así que funciona sin base de datos ni .env:

    .venv/Scripts/python -m pip install -r requirements-dev.txt
    .venv/Scripts/python docs/generar_pdf.py

Cada función termina su docstring con un párrafo «Pantallas: …» que dice en qué pantallas se nota lo que hace, con
los nombres de docs/pantallas.json (o «todas» / «ninguna»). Con eso el PDF arma primero el mapa de pantallas (qué es
cada una, su dirección, sus plantillas y qué funciones la afectan) y después las funciones archivo por archivo. Las
que no dicen sus pantallas, o nombran una que no está en el catálogo, salen en rojo.

Con --hook lee por stdin el aviso de edición de Claude Code y solo regenera si se tocó un .py o docs/pantallas.json.
"""

import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
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
SCREENS_FILE = ROOT / "docs" / "pantallas.json"
TEMPLATES = ROOT / "templates"
# Archivos que no se documentan: tests, migraciones y el arranque estándar de Django.
SKIP = ("migrations/", "tests.py", "manage.py", "asgi.py", "wsgi.py", "docs/")
APPS = {
    "config": "Configuración del proyecto: ajustes y URL principales.",
    "accounts": "Usuarios: registro, inicio de sesión, perfil público o privado, vinculación con Steam, Xbox y PlayStation y cuentas de otras plataformas.",
    "games": "Catálogo de juegos, importación desde IGDB y precios de PC (IsThereAnyDeal), Xbox y Nintendo.",
    "library": "Biblioteca de cada usuario: qué juegos tiene, en qué estado, su valoración y su reseña.",
    "social": "Lo social: seguir a otros jugadores, las listas de seguidores y seguidos y la actividad de cada jugador.",
    "lists": "Listas de juegos de cada usuario: crearlas, ordenarlas y comentarlas, y verlas en su página, en el perfil y en la ficha de cada juego.",
    "challenges": "Retos: crearlos, unirse y contar el progreso de cada uno con su biblioteca (juegos completados y horas jugadas).",
}
ACCENT = colors.HexColor("#7c3aed")
MUTED = colors.HexColor("#6b7280")
CODE_BG = colors.HexColor("#f3f0ff")
SCREEN_BG = colors.HexColor("#ecfdf5")
SCREEN_INK = colors.HexColor("#047857")
WARN = colors.HexColor("#b45309")
# Las vistas de Django para entrar y recuperar la contraseña (django.contrib.auth.urls): nombre → (dirección, plantilla).
AUTH_URLS = {
    "login": ("login/", "registration/login.html"),
    "logout": ("logout/", ""),
    "password_change": ("password_change/", ""),
    "password_change_done": ("password_change/done/", ""),
    "password_reset": ("password_reset/", "registration/password_reset_form.html"),
    "password_reset_done": ("password_reset/done/", "registration/password_reset_done.html"),
    "password_reset_confirm": ("reset/<uidb64>/<token>/", "registration/password_reset_confirm.html"),
    "password_reset_complete": ("reset/done/", "registration/password_reset_complete.html"),
}

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
    # Lo que git aún tiene pero ya no está (un archivo borrado o movido sin confirmar) no se puede leer.
    files = [f for f in files if (ROOT / f).exists()]
    order = list(APPS)
    return sorted(files, key=lambda f: (order.index(f.split("/")[0]) if f.split("/")[0] in order else 99, f))


def signature(node):
    """Firma de una función como texto: «def nombre(args)», con sus decoradores delante."""
    decorators = "".join(f"@{ast.unparse(d)}\n" for d in node.decorator_list)
    return f"{decorators}def {node.name}({ast.unparse(node.args)})"


def parse(path):
    """(docstring del módulo, [(clase o None, docstring de la clase, [funciones])]) de un archivo.

    Cada función es (nombre, firma, docstring, línea); el nombre lleva delante el de su clase si es un método.
    """
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    sections = []
    # En el orden del archivo; las funciones sueltas seguidas van juntas en una sección sin clase.
    for node in tree.body:
        if isinstance(node, functions):
            if not sections or sections[-1][0] is not None:
                sections.append((None, "", []))
            sections[-1][2].append((node.name, signature(node), ast.get_docstring(node) or "", node.lineno))
        elif isinstance(node, ast.ClassDef):
            methods = [
                (f"{node.name}.{f.name}", signature(f), ast.get_docstring(f) or "", f.lineno)
                for f in node.body
                if isinstance(f, functions)
            ]
            if methods or ast.get_docstring(node):
                sections.append((node.name, ast.get_docstring(node) or "", methods))
    return ast.get_docstring(tree) or "", sections


def text(value):
    """Escapa el texto para Paragraph y respeta los saltos de párrafo de la docstring."""
    return "<br/><br/>".join(escape(" ".join(p.split())) for p in value.split("\n\n"))


def load_screens():
    """Las pantallas de docs/pantallas.json, en su orden: [{"nombre", "urls", "que_es", ...}]."""
    return json.loads(SCREENS_FILE.read_text(encoding="utf-8"))["pantallas"]


def _outside_parens(value, stop=";"):
    """El texto hasta el primer stop que no está dentro de unos paréntesis (todo, si no hay)."""
    depth = 0
    for i, ch in enumerate(value):
        depth += ch == "("
        depth -= ch == ")"
        if ch == stop and depth == 0:
            return value[:i]
    return value


def _items(value):
    """Trozos de una lista separada por comas, sin partir lo que va entre paréntesis."""
    items, depth, current = [], 0, ""
    for ch in value:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += ch
    return [item for item in items + [current.strip()] if item]


def split_screens(doc, names):
    """Separa el párrafo «Pantallas:» de una docstring.

    Devuelve (el resto de la docstring, el texto de sus pantallas o None si no lo tiene, {nombre: detalle} de las
    pantallas que nombra, [lo que nombra y no está en el catálogo]). «todas» y «ninguna» van en ese dict con esos
    nombres; su detalle es lo que llevan entre paréntesis y lo que va tras el punto y coma.
    """
    paragraphs = doc.split("\n\n")
    last = " ".join(paragraphs[-1].split())
    if not last.startswith("Pantallas:"):
        return doc, None, {}, []
    rest = "\n\n".join(paragraphs[:-1])
    value = last[len("Pantallas:"):].strip().rstrip(".")
    head = _outside_parens(value)
    found, unknown = {}, []
    for item in _items(head):
        match = re.match(r"^(.*?)\s*(?:\((.*)\))?$", item)
        base, detail = match.group(1).strip(), (match.group(2) or "").strip()
        special = next((word for word in ("todas", "ninguna") if base.startswith(word)), None)
        if special:
            # «ninguna directamente (…); lo que guardan se ve en…»: el detalle junta el paréntesis y lo del final.
            tail = value[len(head) + 1:].strip()
            found[special] = "; ".join(part for part in (base[len(special):].strip(), detail, tail) if part)
        elif base in names:
            found[base] = detail
        else:
            unknown.append(item)
    return rest, value, found, unknown


def _resolve(module, node):
    """Módulo e importaciones de un archivo: {alias: módulo o módulo.nombre}, para seguir las vistas de urls.py."""
    names = {}
    for child in ast.walk(node):
        if isinstance(child, ast.ImportFrom):
            base = child.module or ""
            if child.level:
                parent = module.split(".")[: -child.level]
                base = ".".join(parent + ([base] if base else []))
            for alias in child.names:
                names[alias.asname or alias.name] = f"{base}.{alias.name}" if base else alias.name
    return names


def locate(view):
    """Dónde está de verdad una vista dada con su nombre completo: (archivo, función, nombre completo), o None.

    Sigue los reexports de los __init__.py: accounts.views.profile está en accounts/views/perfil.py, porque
    accounts/views/__init__.py la trae con «from .perfil import profile».
    """
    module, _, function = view.rpartition(".")
    for _ in range(4):
        path = ROOT / (module.replace(".", "/") + ".py")
        if path.exists():
            return path, function, f"{module}.{function}"
        package = ROOT / module.replace(".", "/") / "__init__.py"
        if not package.exists():
            return None
        for node in ast.parse(package.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.ImportFrom) and any((a.asname or a.name) == function for a in node.names):
                parts = module.split(".")
                if node.level:
                    parts = parts[: len(parts) - node.level + 1]
                module = ".".join(parts + ([node.module] if node.module else []))
                break
        else:
            return None
    return None


def url_index():
    """{nombre de la URL: (dirección, vista)} de todas las URL del proyecto, siguiendo los include() desde config.urls.

    La vista es el nombre completo de su función (accounts.views.profile); las de Django para entrar y recuperar
    la contraseña llevan «django» delante.
    """
    index = {}

    def walk(module, prefix):
        path = ROOT / (module.replace(".", "/") + ".py")
        if not path.exists():
            return
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = _resolve(module, tree)
        namespace = ""
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "app_name" for t in node.targets):
                namespace = node.value.value
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "path" and len(node.args) >= 2):
                continue
            route = node.args[0].value if isinstance(node.args[0], ast.Constant) else ""
            target = node.args[1]
            if isinstance(target, ast.Call) and getattr(target.func, "id", "") == "include":
                included = target.args[0].value
                if included == "django.contrib.auth.urls":
                    for name, (sub, _) in AUTH_URLS.items():
                        index[name] = ("/" + prefix + route + sub, f"django.contrib.auth ({name})")
                else:
                    walk(included, prefix + route)
                continue
            name = next((kw.value.value for kw in node.keywords if kw.arg == "name"), None)
            if not name:
                continue
            view = ast.unparse(target)
            head, _, attr = view.partition(".")
            if head in imports:
                view = f"{imports[head]}.{attr}" if attr else imports[head]
            found = locate(view)
            index[f"{namespace}:{name}" if namespace else name] = ("/" + prefix + route, found[2] if found else view)

    walk("config.urls", "")
    return index


def view_templates(view):
    """Plantillas que pinta una vista: las .html que nombra ella o los ayudantes privados (_algo) de su módulo."""
    if view.startswith("django.contrib.auth"):
        name = view[view.index("(") + 1 : -1]
        return [AUTH_URLS[name][1]] if AUTH_URLS.get(name, ("", ""))[1] else []
    found = locate(view)
    if not found:
        return []
    path, function, _ = found
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    found, pending, seen = [], [function], set()
    while pending:
        current = pending.pop()
        if current in seen or current not in defs:
            continue
        seen.add(current)
        for node in ast.walk(defs[current]):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith(".html"):
                if node.value not in found:
                    found.append(node.value)
            elif isinstance(node, ast.Name) and node.id.startswith("_"):
                pending.append(node.id)
    return found


def included_templates(names):
    """Las plantillas que incluyen (con {% include %}) las de names, una y otra vez; sin base.html."""
    found, pending = [], list(names)
    while pending:
        name = pending.pop(0)
        path = TEMPLATES / name
        if name in found or name == "base.html" or not path.exists():
            continue
        found.append(name)
        source = path.read_text(encoding="utf-8")
        pending += re.findall(r"""\{%\s*include\s+["']([^"']+\.html)["']""", source)
        pending += re.findall(r"""\{%\s*extends\s+["']([^"']+\.html)["']""", source)
    return [name for name in found if name not in names]


def template_scripts():
    """Los bloques <script> de las plantillas (sin los que solo cargan un archivo), en orden de plantilla.

    Cada uno es (plantilla, línea, comentario de arriba, texto de su línea «// Pantallas: …» o None, [(función,
    comentario)]): el comentario de arriba son las líneas // con las que empieza el bloque, y el de cada función con
    nombre, las // que lleva justo encima ("" si no tiene).
    """
    blocks = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", source, re.S):
            line = source[: match.start()].count("\n") + 1
            lines = [l.strip() for l in match.group(1).strip().splitlines()]
            lead = []
            for l in lines:
                if not l.startswith("//"):
                    break
                lead.append(l[2:].strip())
            screens = next((l[len("Pantallas:"):].strip() for l in lead if l.startswith("Pantallas:")), None)
            lead = [l for l in lead if not l.startswith("Pantallas:")]
            functions = []
            for i, l in enumerate(lines):
                found = re.search(r"function\s+([A-Za-z_]\w*)\s*\(", l)
                if not found:
                    continue
                comment = []
                for previous in reversed(lines[:i]):
                    if not previous.startswith("//"):
                        break
                    comment.insert(0, previous[2:].strip())
                functions.append((found.group(1), " ".join(comment)))
            blocks.append((path.relative_to(TEMPLATES).as_posix(), line, " ".join(lead), screens, functions))
    return blocks


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
        "small": ParagraphStyle("small", fontName=regular, fontSize=9, leading=12.5, spaceAfter=3),
        "muted": ParagraphStyle("muted", fontName=regular, fontSize=9, leading=12, textColor=MUTED),
        "code": ParagraphStyle("code", fontName="Courier-Bold", fontSize=8.5, leading=11),
        "screens": ParagraphStyle("screens", fontName=regular, fontSize=9, leading=12, textColor=SCREEN_INK),
        "missing": ParagraphStyle("missing", fontName=regular, fontSize=10, textColor=colors.red),
        "warn": ParagraphStyle("warn", fontName=regular, fontSize=9, leading=12, textColor=WARN),
    }

    screens = load_screens()
    names = [screen["nombre"] for screen in screens]
    files = [(path, *parse(path)) for path in source_files()]
    files = [f for f in files if f[2]]  # fuera los archivos sin funciones (urls.py, apps.py...)
    total = sum(len(methods) for _, _, sections in files for _, _, methods in sections)

    # Quién afecta a cada pantalla: {pantalla: {archivo: [(función, detalle)]}}, y las de «todas» y «ninguna».
    by_screen = {name: defaultdict(list) for name in names}
    every, nowhere = defaultdict(list), defaultdict(list)
    pending = 0
    parsed = {}
    for path, _, sections in files:
        for _, _, methods in sections:
            for qualname, _, doc, _ in methods:
                rest, value, found, unknown = split_screens(doc, names)
                parsed[(path, qualname)] = (rest, value, unknown)
                if value is None or unknown:
                    pending += 1
                for name, detail in found.items():
                    if name == "todas":
                        every[path].append((qualname, detail))
                    elif name == "ninguna":
                        nowhere[path].append((qualname, detail))
                    else:
                        by_screen[name][path].append((qualname, detail))

    # El JavaScript de las plantillas, repartido igual: {pantalla: [(plantilla, línea, detalle)]}.
    scripts = template_scripts()
    js_by_screen, js_every = defaultdict(list), []
    js_functions = sum(len(functions) for *_, functions in scripts)
    for template, line, _, value, _ in scripts:
        if value is None:
            pending += 1
            continue
        _, _, found, unknown = split_screens("Pantallas: " + value, names)
        pending += bool(unknown)
        for name, detail in found.items():
            if name == "todas":
                js_every.append((template, line, detail))
            elif name != "ninguna":
                js_by_screen[name].append((template, line, detail))

    def js_lines(items):
        """Un párrafo con los bloques de JavaScript: «plantilla (línea N): detalle · …»."""
        if not items:
            return []
        parts = [
            f"<b>{escape(template)}</b> (línea {line})" + (f" <font color='#6b7280'>({escape(detail)})</font>" if detail else "")
            for template, line, detail in items
        ]
        return [Paragraph("<font color='#7c3aed'>JavaScript</font>: " + " · ".join(parts), styles["small"])]

    urls = url_index()
    story = [
        Spacer(1, 4 * cm),
        Paragraph("NextPlay", styles["title"]),
        Paragraph("Pantallas y funciones", styles["subtitle"]),
        Spacer(1, 0.4 * cm),
        Paragraph(
            f"{len(screens)} pantallas · {total} funciones de Python en {len(files)} archivos y {js_functions} de "
            f"JavaScript en {len(scripts)} bloques de las plantillas · "
            f"generado el {datetime.now():%d/%m/%Y %H:%M}",
            styles["subtitle"],
        ),
        Spacer(1, 1.5 * cm),
        Paragraph("Cómo leer este documento", styles["h2"]),
        Paragraph(
            text(
                "La primera parte es el mapa de pantallas: qué es cada pantalla de la web, en qué dirección está, qué "
                "vista y qué plantillas la pintan y qué funciones la afectan, archivo por archivo y con la parte de la "
                "pantalla entre paréntesis. La segunda parte son todas las funciones del proyecto, archivo por "
                "archivo, con lo que hace cada una y sus pantallas, y la tercera, el JavaScript de las plantillas.\n\n"
                "Las pantallas de una función son las pantallas en las que se nota lo que hace: donde se ve lo que "
                "calcula o pinta, desde donde se lanza (sus botones y formularios) y donde sale lo que guarda. Si la "
                "cambias, son las pantallas que tienes que mirar. «todas» quiere decir que sale en cualquier pantalla "
                "(la barra de arriba, la foto y el nombre de cada jugador); «ninguna», que no se ve en la web (el "
                "arranque de Django, las opciones de un comando...).\n\n"
                "En el código cada función lo dice al final de su docstring, en un párrafo «Pantallas: …» con los "
                "nombres de docs/pantallas.json, y cada bloque de JavaScript, en una línea «// Pantallas: …» de su "
                "comentario. Las que no lo dicen, o nombran una pantalla que no está en el catálogo, salen en rojo."
            ),
            styles["body"],
        ),
    ]
    if pending:
        story.append(Paragraph(f"{pending} funciones sin sus pantallas bien puestas: búscalas en rojo.", styles["missing"]))
    story.append(PageBreak())

    def table(rows, widths):
        """Tabla con cabecera violeta y filas alternas, como el índice."""
        t = Table(rows, colWidths=widths, repeatRows=1)
        t.setStyle(
            TableStyle([
                ("FONT", (0, 0), (-1, -1), regular, 9),
                ("FONT", (0, 0), (-1, 0), bold, 9),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CODE_BG]),
                ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ])
        )
        return t

    def count(groups):
        """Cuántas funciones hay en un {archivo: [funciones]}."""
        return sum(len(items) for items in groups.values())

    # Parte 1: el mapa de pantallas.
    story.append(Paragraph("Parte 1 · Mapa de pantallas", styles["h1"]))
    rows = [["Pantalla", "Dirección", "Funciones"]]
    for screen in screens:
        address = screen.get("direccion") or (urls[screen["urls"][0]][0] if screen["urls"] and screen["urls"][0] in urls else "")
        rows.append([screen["nombre"], address, str(count(by_screen[screen["nombre"]]))])
    rows.append(["En todas las pantallas", "", str(count(every))])
    rows.append(["En ninguna pantalla", "", str(count(nowhere))])
    story += [table(rows, [6 * cm, 8 * cm, 2.5 * cm]), PageBreak()]

    def function_groups(groups):
        """Un párrafo por archivo con sus funciones: «archivo: función (detalle) · función...»."""
        flow = []
        for path in sorted(groups, key=lambda p: [f[0] for f in files].index(p)):
            parts = []
            for qualname, detail in groups[path]:
                part = f"<b>{escape(qualname)}</b>"
                if detail:
                    part += f" <font color='#6b7280'>({escape(detail)})</font>"
                parts.append(part)
            flow.append(Paragraph(f"<font color='#7c3aed'>{escape(path)}</font>: " + " · ".join(parts), styles["small"]))
        return flow

    for screen in screens:
        name = screen["nombre"]
        block = [Paragraph(escape(name), styles["h2"]), Paragraph(text(screen["que_es"]), styles["body"])]
        addresses, views, main = [], [], []
        for url_name in screen["urls"]:
            if url_name not in urls:
                block.append(Paragraph(f"La URL «{escape(url_name)}» no existe.", styles["warn"]))
                continue
            route, view = urls[url_name]
            addresses.append(f"{escape(route)} <font color='#6b7280'>({escape(url_name)} → {escape(view)})</font>")
            for template in view_templates(view):
                if template not in main:
                    main.append(template)
        if screen.get("direccion"):
            addresses.append(escape(screen["direccion"]))
        if addresses:
            block.append(Paragraph("<b>Dirección:</b> " + "<br/>".join(addresses), styles["small"]))
        if main:
            parts = included_templates(main)
            line = "<b>Plantillas:</b> " + ", ".join(escape(t) for t in main)
            if parts:
                line += "<br/><b>Trozos que incluye:</b> " + ", ".join(escape(t) for t in parts)
            block.append(Paragraph(line, styles["small"]))
        story.append(KeepTogether(block))
        groups = by_screen[name]
        story.append(Paragraph(f"Funciones que la afectan ({count(groups)})", styles["h3"]))
        story += function_groups(groups) or [Paragraph("Ninguna.", styles["muted"])]
        story += js_lines(js_by_screen[name])

    story.append(Paragraph("En todas las pantallas", styles["h2"]))
    story.append(
        Paragraph(
            text("Lo que sale en cualquier pantalla: la barra de arriba (la campana de avisos y el menú de la cuenta), "
                 "y la foto y el nombre de cada jugador."),
            styles["body"],
        )
    )
    story += function_groups(every)
    story += js_lines(js_every)
    story.append(Paragraph("En ninguna pantalla", styles["h2"]))
    story.append(
        Paragraph(
            text("Lo que no se ve en la web: el arranque de Django, las opciones de los comandos de consola, lo que ya no "
                 "usa nadie..."),
            styles["body"],
        )
    )
    story += function_groups(nowhere)
    story.append(PageBreak())

    # Parte 2: las funciones, archivo por archivo.
    story.append(Paragraph("Parte 2 · Funciones por archivo", styles["h1"]))
    index = [["Archivo", "Funciones"]]
    for path, _, sections in files:
        index.append([path, str(sum(len(m) for _, _, m in sections))])
    story += [table(index, [11 * cm, 3 * cm]), PageBreak()]

    order = {name: i for i, name in enumerate(names)}
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
        file_screens = sorted(
            {name for name in names if path in by_screen[name]}, key=lambda name: order[name]
        )
        if file_screens:
            story.append(Paragraph("<b>Pantallas de este archivo:</b> " + escape(", ".join(file_screens)), styles["screens"]))
        for cls, cls_doc, methods in sections:
            if cls:
                story.append(Paragraph(f"Clase {escape(cls)}", styles["h3"]))
                if cls_doc:
                    story.append(Paragraph(text(cls_doc), styles["muted"]))
            for qualname, sig, doc, line in methods:
                rest, value, unknown = parsed[(path, qualname)]
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
                    Paragraph(text(rest), styles["body"])
                    if rest
                    else Paragraph("Sin documentar.", styles["missing"])
                )
                if value is None:
                    screens_line = Paragraph("Falta a qué pantallas afecta (el párrafo «Pantallas:»).", styles["missing"])
                else:
                    screens_line = Table(
                        [[Paragraph(f"<b>Pantallas:</b> {escape(value)}.", styles["screens"])]],
                        colWidths=[16.5 * cm],
                    )
                    screens_line.setStyle(
                        TableStyle([
                            ("BACKGROUND", (0, 0), (-1, -1), SCREEN_BG),
                            ("LEFTPADDING", (0, 0), (-1, -1), 6),
                            ("TOPPADDING", (0, 0), (-1, -1), 3),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                        ])
                    )
                flow = [Spacer(1, 4), code, Spacer(1, 3), body, screens_line]
                if unknown:
                    flow.append(
                        Paragraph("Pantalla que no está en docs/pantallas.json: " + escape(", ".join(unknown)), styles["missing"])
                    )
                flow.append(Paragraph(f"{escape(path)}, línea {line}", styles["muted"]))
                story.append(KeepTogether(flow))

    story.append(PageBreak())
    story.append(Paragraph("Parte 3 · JavaScript de las plantillas", styles["h1"]))
    story.append(
        Paragraph(
            text("El JavaScript va dentro de las plantillas, en bloques <script>. Cada bloque empieza con un comentario "
                 "que dice qué hace y en qué pantallas («// Pantallas: …»), y cada función con nombre lleva encima "
                 "una línea que dice qué hace. HTMX (las peticiones que cambian un trozo de la página) y SortableJS "
                 "(arrastrar) se cargan de fuera."),
            styles["body"],
        )
    )
    current_template = None
    for template, line, lead, value, functions in scripts:
        if template != current_template:
            story.append(Paragraph(escape(template), styles["h2"]))
            current_template = template
        block = [Paragraph(f"Bloque de la línea {line}", styles["h3"])]
        block.append(Paragraph(text(lead), styles["body"]) if lead else Paragraph("Sin comentario.", styles["missing"]))
        if value is None:
            block.append(Paragraph("Falta a qué pantallas afecta (la línea «// Pantallas:»).", styles["missing"]))
        else:
            box = Table([[Paragraph(f"<b>Pantallas:</b> {escape(value)}", styles["screens"])]], colWidths=[16.5 * cm])
            box.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), SCREEN_BG),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ])
            )
            block.append(box)
        story.append(KeepTogether(block))
        for name, comment in functions:
            if comment:
                story.append(Paragraph(f"<b>{escape(name)}()</b>: {escape(comment)}", styles["small"]))
            else:
                story.append(Paragraph(f"<b>{escape(name)}()</b>: sin documentar.", styles["missing"]))

    def footer(canvas, doc):
        """Pie de página con el nombre del documento y el número de página."""
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(2 * cm, 1.2 * cm, "NextPlay · Pantallas y funciones")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title="NextPlay · Pantallas y funciones",
        author="NextPlay",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return total


def touched_python():
    """Con --hook, indica si la edición que avisa Claude Code (JSON por stdin) fue a un .py del proyecto o al catálogo
    de pantallas (docs/pantallas.json)."""
    try:
        path = (json.load(sys.stdin).get("tool_input") or {}).get("file_path") or ""
    except ValueError:
        return False
    path = path.replace("\\", "/")
    if path.endswith("/docs/pantallas.json"):
        return True
    return path.endswith(".py") and "/docs/" not in path and "/migrations/" not in path


if __name__ == "__main__":
    if "--hook" in sys.argv and not touched_python():
        sys.exit(0)
    count = build()
    print(f"{OUTPUT.relative_to(ROOT)}: {count} funciones documentadas.")
