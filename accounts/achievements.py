"""Logros importados de Steam, de PlayStation (sus trofeos) y de Xbox, juntados por juego.

Cada plataforma los apunta en los títulos importados del usuario (models.ImportedTitle.set_achievements): Steam los
lee juego a juego (steam.import_achievements), PlayStation con su lista de trofeos (psn.save_trophies) y Xbox con su
historial de juegos (xbox.save_achievements). Aquí se juntan por juego para la ficha, las portadas y las cifras del
perfil.
"""
from .models import ExternalTitle, ImportedTitle

# Orden en que se enseñan las plataformas.
PLATFORMS = [value for value, _ in ExternalTitle.Platform.choices]


def progress_by_game(user, game_ids):
    """Cómo va user con los logros de los juegos game_ids: {id del juego: [progreso en cada plataforma]}, solo de los
    juegos que tienen logros y ya se han leído.

    Cada progreso es un dict con la plataforma («platform»), su nombre («label») y cómo llama a sus logros («noun»:
    «trofeos» en PlayStation), los conseguidos («unlocked») y los que hay («total»), el porcentaje («percent», sin
    redondear hacia arriba: el 100 % es tenerlos todos), si los tiene todos («complete») y si tiene el platino de
    PlayStation («platinum»). Con varias cuentas o versiones del juego en una plataforma (la de PS4 y la de PS5),
    cuenta la que más lleva.
    """
    best = {}
    rows = ImportedTitle.objects.filter(
        user=user, title__game_id__in=list(game_ids), achievements_total__gt=0,
    ).select_related("title")
    for row in rows:
        key = (row.title.game_id, row.title.platform)
        score = (row.platinum, row.achievements_unlocked / row.achievements_total)
        if key not in best or score > best[key][0]:
            best[key] = (score, row)
    progress = {}
    for (game_id, platform), (_, row) in sorted(best.items(), key=lambda item: PLATFORMS.index(item[0][1])):
        progress.setdefault(game_id, []).append({
            "platform": platform,
            "label": row.title.get_platform_display(),
            "noun": "trofeos" if platform == ExternalTitle.Platform.PSN else "logros",
            "unlocked": row.achievements_unlocked,
            "total": row.achievements_total,
            "percent": row.achievements_unlocked * 100 // row.achievements_total,
            "complete": row.achievements_unlocked >= row.achievements_total,
            "platinum": row.platinum,
        })
    return progress


def badge(progress):
    """Insignia de la portada de un juego según su progreso (un valor de progress_by_game): «Platino» si tiene el de
    PlayStation, «100 %» si tiene todos los logros en alguna plataforma y, si no, vacía."""
    if any(item["platinum"] for item in progress):
        return "Platino"
    if any(item["complete"] for item in progress):
        return "100 %"
    return ""


def totals(progress):
    """Cifras del perfil a partir de progress_by_game: cuántos platinos tiene («platinums») y en cuántos juegos tiene
    todos los logros de alguna plataforma («complete»)."""
    games = list(progress.values())
    return {
        "platinums": sum(1 for items in games if any(item["platinum"] for item in items)),
        "complete": sum(1 for items in games if any(item["complete"] for item in items)),
    }
