"""Vista de Importar desde un archivo (cuentas/importar-archivo/): subir un CSV de Playnite o de otro tracker, decir qué
columna es cada cosa con una vista previa e importarlo (accounts.csv_import). El archivo se guarda en la sesión entre
un paso y otro y se olvida al importar.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.template.defaultfilters import pluralize

from .. import csv_import
from ..models import ExternalTitle

SESSION_KEY = "csv_import"
# Tiendas que se pueden elegir para las filas sin tienda: las que no se importan desde su cuenta.
DEFAULT_STORES = [
    (value, label) for value, label in ExternalTitle.Platform.choices if value not in csv_import.ACCOUNT_PLATFORMS
]


def _mapping(request):
    """Qué columna es cada campo según el formulario (col_<campo>), sin las que no existen en el archivo.

    Pantallas: Importar desde un archivo.
    """
    return {field: request.POST.get(f"col_{field}", "") for field in csv_import.FIELDS}


@login_required
def import_file(request):
    """Importar desde un archivo, en tres pasos con el mismo formulario: «subir» lee el CSV (accounts.csv_import.read),
    lo guarda en la sesión y elige solo las columnas que reconoce; «revisar» vuelve a enseñar la vista previa con las
    columnas, la unidad del tiempo, la escala de la nota y la tienda por defecto que se hayan cambiado; e «importar» lo
    trae todo (csv_import.import_items), cuenta el resultado con un mensaje y lleva a Juegos importados si hay alguno sin
    encontrar o, si no, al perfil.

    Pantallas: Importar desde un archivo, Juegos importados (los que no se encuentran), Perfil (biblioteca y horas).
    """
    step = request.POST.get("paso", "")
    stored = request.session.get(SESSION_KEY)
    context = {"stores": DEFAULT_STORES, "fields": csv_import.FIELDS.items(), "units": csv_import.TIME_UNITS.items(),
               "scales": csv_import.RATING_SCALES.items()}
    if request.method == "POST" and step == "subir":
        upload = request.FILES.get("archivo")
        try:
            if upload is None:
                raise csv_import.CSVError("Elige un archivo CSV.")
            headers, rows = csv_import.read(upload.read(csv_import.MAX_BYTES + 1))
        except csv_import.CSVError as exc:
            return render(request, "accounts/import_file.html", {**context, "error": str(exc)})
        mapping = csv_import.guess(headers)
        stored = {
            "file": upload.name[:120], "headers": headers, "rows": rows, "mapping": mapping,
            "unit": csv_import.guess_unit(rows, mapping["time"]) if mapping["time"] else "horas",
            "scale": "100" if mapping["rating"] and csv_import._key(mapping["rating"]) == "userscore" else "10",
            "store": "",
        }
        request.session[SESSION_KEY] = stored
    elif request.method == "POST" and stored and step in ("revisar", "importar"):
        stored = {
            **stored, "mapping": _mapping(request), "unit": request.POST.get("unidad", stored["unit"]),
            "scale": request.POST.get("escala", stored["scale"]), "store": request.POST.get("tienda", ""),
        }
        request.session[SESSION_KEY] = stored
        if step == "importar":
            if not stored["mapping"]["name"]:
                messages.error(request, "Elige qué columna tiene el nombre de cada juego.")
            else:
                items = csv_import.interpret(stored["rows"], stored["mapping"], stored["unit"], stored["scale"], stored["store"])
                result = csv_import.import_items(request.user, items)
                del request.session[SESSION_KEY]
                text = (
                    f"{stored['file']}: {result['added']} juego{pluralize(result['added'])} añadido{pluralize(result['added'])}"
                    f", {result['updated']} con horas actualizadas"
                )
                if result["skipped"]:
                    text += f" y {result['skipped']} fila{pluralize(result['skipped'])} saltada{pluralize(result['skipped'])}"
                messages.success(request, text + ".")
                if result["unmatched"]:
                    messages.warning(
                        request, f"{result['unmatched']} no se han encontrado en el catálogo: búscalos aquí para añadirlos.",
                    )
                    return redirect("imported_titles")
                return redirect("profile", request.user.username)
    elif request.method == "GET" and request.GET.get("empezar") == "1":
        request.session.pop(SESSION_KEY, None)
        stored = None
    if not stored:
        return render(request, "accounts/import_file.html", context)
    items = csv_import.interpret(stored["rows"], stored["mapping"], stored["unit"], stored["scale"], stored["store"])
    return render(request, "accounts/import_file.html", {
        **context, "stored": stored, "preview": items[:csv_import.PREVIEW_ROWS], "total": len(items),
        "importable": sum(1 for item in items if not item["skip"]),
    })
