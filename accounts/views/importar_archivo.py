"""Vista de Importar desde un archivo (cuenta/importar-archivo/): subir un CSV de Playnite o de otro tracker, decir qué
columna es cada cosa con una vista previa e importarlo (accounts.csv_import). Lo leído del archivo se guarda en la
sesión entre un paso y otro y se olvida al importar.
"""

import logging
from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.template.defaultfilters import pluralize

from games import igdb

from .. import csv_import
from ..models import ExternalTitle
from .cuentas_vinculadas import _fetch_library_prices, _with_unmatched

logger = logging.getLogger(__name__)
SESSION_KEY = "csv_import"
# Tiendas que se pueden elegir para las filas sin tienda: las que no se importan desde su cuenta.
DEFAULT_STORES = [
    (value, label) for value, label in ExternalTitle.Platform.choices if value not in csv_import.ACCOUNT_PLATFORMS
]


def _rows(stored):
    """Las filas guardadas en la sesión (listas de celdas, en el orden de las cabeceras) como dicts.

    Pantallas: Importar desde un archivo.
    """
    return [dict(zip(stored["headers"], values)) for values in stored["rows"]]


def _choices(request, stored):
    """Lo elegido en el formulario sobre lo guardado (stored): las columnas de cada campo (col_<campo>, solo cabeceras que
    existen), la unidad del tiempo, la escala de la nota y la tienda de las filas sin tienda. Si cambia la columna del
    tiempo o la de la nota, su unidad o su escala se vuelven a adivinar.

    Pantallas: Importar desde un archivo.
    """
    headers = set(stored["headers"])
    posted = {field: request.POST.get(f"col_{field}", "") for field in csv_import.FIELDS}
    mapping = {field: value if value in headers else "" for field, value in posted.items()}
    rows = _rows(stored)
    unit = request.POST.get("unidad", stored["unit"])
    if mapping["time"] != stored["mapping"]["time"] or unit not in csv_import.TIME_UNITS:
        unit = csv_import.guess_unit(rows, mapping["time"]) if mapping["time"] else "horas"
    scale = request.POST.get("escala", stored["scale"])
    if mapping["rating"] != stored["mapping"]["rating"] or scale not in csv_import.RATING_SCALES:
        scale = csv_import.guess_scale(rows, mapping["rating"]) if mapping["rating"] else "10"
    store = request.POST.get("tienda", "")
    if store not in dict(DEFAULT_STORES):
        store = ""
    return {**stored, "mapping": mapping, "unit": unit, "scale": scale, "store": store}


def _upload(request):
    """Lee el archivo subido (csv_import.read) y devuelve lo que se guarda en la sesión: su nombre, sus cabeceras, sus
    filas como listas, las columnas que reconoce solas, la unidad del tiempo y la escala de la nota que adivina. Lanza
    csv_import.CSVError si no se puede leer.

    Pantallas: Importar desde un archivo.
    """
    upload = request.FILES.get("archivo")
    if upload is None:
        raise csv_import.CSVError("Elige un archivo CSV.")
    headers, rows = csv_import.read(upload.read(csv_import.MAX_BYTES + 1))
    mapping = csv_import.guess(headers)
    return {
        "file": upload.name[:120],
        "headers": headers,
        "rows": [[row[header] for header in headers] for row in rows],
        "mapping": mapping,
        "unit": csv_import.guess_unit(rows, mapping["time"]) if mapping["time"] else "horas",
        "scale": csv_import.guess_scale(rows, mapping["rating"]) if mapping["rating"] else "10",
        "store": "",
    }


def _import(request, stored):
    """Importa lo guardado con las columnas elegidas (csv_import.import_items), pide los precios de lo nuevo, lo cuenta
    con un mensaje (con el enlace a Juegos importados si alguno no se ha encontrado) y lleva al perfil.

    Pantallas: Importar desde un archivo, Perfil (biblioteca y horas), Juegos importados (los que no se encuentran),
    Inicio (feed: la importación).
    """
    items = csv_import.interpret(_rows(stored), stored["mapping"], stored["unit"], stored["scale"], stored["store"])
    try:
        result = csv_import.import_items(request.user, items)
    except igdb.IGDBError:
        logger.exception("Falló la importación del archivo de %s", request.user.username)
        messages.error(request, "No se han podido buscar los juegos en el catálogo. Inténtalo de nuevo en un rato.")
        return redirect("import_file")
    request.session.pop(SESSION_KEY, None)
    _fetch_library_prices(request.user)
    added, updated, skipped = result["added"], result["updated"], result["skipped"]
    text = f"{stored['file']}: {added} juego{pluralize(added)} añadido{pluralize(added)}"
    if updated:
        text += f", {updated} con horas actualizadas"
    if skipped:
        text += f" y {skipped} fila{pluralize(skipped)} sin importar"
    messages.success(request, _with_unmatched(text, result["unmatched"]))
    return redirect("profile", request.user.username)


@login_required
def import_file(request):
    """Importar desde un archivo, en tres pasos con el mismo formulario: «subir» lee el CSV (_upload), lo guarda en la
    sesión y elige solo las columnas que reconoce; «revisar» vuelve a enseñar la vista previa con lo que se haya cambiado
    (_choices); e «importar» lo trae todo (_import). Con ?empezar=1 se olvida el archivo y se vuelve a pedir otro.

    La vista previa enseña las primeras PREVIEW_ROWS filas como se van a leer y cuántas van a cada tienda y cuántas no se
    importan, con su motivo.

    Pantallas: Importar desde un archivo, Juegos importados (los que no se encuentran), Perfil (biblioteca y horas).
    """
    step = request.POST.get("paso", "")
    stored = request.session.get(SESSION_KEY)
    context = {
        "units": csv_import.TIME_UNITS.items(), "scales": csv_import.RATING_SCALES.items(), "stores": DEFAULT_STORES,
    }
    if request.method == "POST" and step == "subir":
        try:
            stored = _upload(request)
        except csv_import.CSVError as exc:
            return render(request, "accounts/import_file.html", {**context, "error": str(exc)})
        request.session[SESSION_KEY] = stored
    elif request.method == "POST" and stored and step in ("revisar", "importar"):
        stored = _choices(request, stored)
        request.session[SESSION_KEY] = stored
        if step == "importar":
            if stored["mapping"]["name"]:
                return _import(request, stored)
            messages.error(request, "Elige qué columna tiene el nombre de cada juego.")
    elif request.GET.get("empezar") == "1":
        request.session.pop(SESSION_KEY, None)
        return redirect("import_file")
    if not stored:
        return render(request, "accounts/import_file.html", context)

    items = csv_import.interpret(_rows(stored), stored["mapping"], stored["unit"], stored["scale"], stored["store"])
    importable = [item for item in items if not item["skip"]]
    return render(request, "accounts/import_file.html", {
        **context,
        "stored": stored,
        "columns": [(field, label, stored["mapping"][field]) for field, label in csv_import.FIELDS.items()],
        # «sample» y no «preview»: con «preview», base.html no enseña los mensajes (es lo que usa la vista previa del perfil).
        "sample": items[:csv_import.PREVIEW_ROWS],
        "more": max(0, len(items) - csv_import.PREVIEW_ROWS),
        "total": len(items),
        "importable": len(importable),
        "by_store": Counter(item["platform_label"] for item in importable).most_common(),
        "skipped": Counter(item["skip"] for item in items if item["skip"]).most_common(),
        "without_store": any(item["skip"].startswith("Sin tienda") for item in items),
    })
