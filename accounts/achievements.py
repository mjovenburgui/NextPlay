"""Logros importados de Steam, de PlayStation (sus trofeos) y de Xbox, juntados por juego.

Cada plataforma los apunta en los títulos importados del usuario (models.ImportedTitle.set_achievements): Steam los
lee juego a juego (steam.import_achievements), PlayStation con su lista de trofeos (psn.save_trophies) y Xbox con su
historial de juegos (xbox.save_achievements). Aquí se juntan por juego para la ficha, las portadas y las cifras del
perfil.

De Steam y de PlayStation (sus trofeos, con psn.read_trophies) se guardan además uno a uno: los logros de cada juego
(models.Achievement, con save_info) y cuáles tiene cada usuario y cuándo los consiguió (models.UnlockedAchievement, con
save_unlocked). De ahí salen «Tus logros» de la ficha (detail), la sección «Logros» del perfil (showcase) y, de los
recién conseguidos, lo que merece salir en el feed (social.activity.achievements_read).
"""
from .models import Achievement, ExternalTitle, ImportedTitle, UnlockedAchievement

# Orden en que se enseñan las plataformas.
PLATFORMS = [value for value, _ in ExternalTitle.Platform.choices]
# Órdenes de «Tus logros» en la ficha: valor de ?orden= → nombre del botón.
ORDERS = {"": "Por fecha", "raros": "Más raros", "juego": "Orden del juego"}
# Logros de cada grupo de «Tus logros» que se ven sin desplegar el resto.
DETAIL_SHOWN = 12
# Últimos logros conseguidos que salen en la sección «Logros» del perfil.
SHOWCASE_SHOWN = 6


def progress_by_game(user, game_ids):
    """Cómo va user con los logros de los juegos game_ids: {id del juego: [progreso en cada plataforma]}, solo de los
    juegos que tienen logros y ya se han leído.

    Cada progreso es un dict con la plataforma («platform»), su nombre («label») y cómo llama a sus logros («noun»:
    «trofeos» en PlayStation), los conseguidos («unlocked») y los que hay («total»), el porcentaje («percent», sin
    redondear hacia arriba: el 100 % es tenerlos todos), si los tiene todos («complete») y si tiene el platino de
    PlayStation («platinum»). Con varias cuentas o versiones del juego en una plataforma (la de PS4 y la de PS5),
    cuenta la que más lleva. «detailed» dice si se conocen también uno a uno (detail), y «imported_id» y «title_id»
    son su título importado y el de la plataforma.

    Pantallas: Perfil (insignias de las portadas y cifras de logros), Ficha del juego (tus logros), Nueva tier list
    (plantilla de juegos con todos los logros).
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
            # Leídos uno a uno: los del juego se conocen y los de este título se leyeron después (antes solo se contaban).
            "detailed": row.title.achievements_read_at is not None and row.achievements_at is not None,
            "imported_id": row.pk,
            "title_id": row.title_id,
        })
    return progress


def badge(progress):
    """Insignia de la portada de un juego según su progreso (un valor de progress_by_game): «Platino» si tiene el de
    PlayStation, «100 %» si tiene todos los logros en alguna plataforma y, si no, vacía.

    Pantallas: Perfil (insignia «Platino» o «100 %» de las portadas de la biblioteca).
    """
    if any(item["platinum"] for item in progress):
        return "Platino"
    if any(item["complete"] for item in progress):
        return "100 %"
    return ""


def totals(progress):
    """Cifras del perfil a partir de progress_by_game: cuántos platinos tiene («platinums») y en cuántos juegos tiene
    todos los logros de alguna plataforma («complete»).

    Pantallas: Perfil (platinos y juegos al 100 % de las cifras del perfil).
    """
    games = list(progress.values())
    return {
        "platinums": sum(1 for items in games if any(item["platinum"] for item in items)),
        "complete": sum(1 for items in games if any(item["complete"] for item in items)),
    }


def save_info(title, items, percentages, at):
    """Guarda los logros del juego de title (un ExternalTitle) tal como los da su plataforma y apunta que se han leído
    en at.

    items son sus logros en orden, cada uno con «api_name», «name», «description», «icon», «icon_locked» y «hidden»;
    percentages, el porcentaje de los jugadores que tiene cada uno ({clave: porcentaje}), o None si no se ha podido
    leer: entonces se queda el que había. Los logros que ya no da la plataforma no se borran: alguien puede tenerlos.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; insignias de las
    portadas, cifras y sección «Logros»), Ficha del juego (tus logros).
    """
    rows = [
        Achievement(
            title=title, position=position, percent=(percentages or {}).get(item["api_name"]),
            **{field: item[field] for field in ("api_name", "name", "description", "icon", "icon_locked", "hidden")},
        )
        for position, item in enumerate(items)
    ]
    fields = ["name", "description", "icon", "icon_locked", "hidden", "position"]
    if rows:
        Achievement.objects.bulk_create(
            rows, update_conflicts=True, unique_fields=["title", "api_name"],
            update_fields=fields + (["percent"] if percentages is not None else []),
        )
    ExternalTitle.objects.filter(pk=title.pk).update(achievements_read_at=at)
    title.achievements_read_at = at


def save_unlocked(found):
    """Apunta uno a uno los logros conseguidos en unos títulos importados: found es [(ImportedTitle, logros)], con todos
    los logros de su juego como [(clave, conseguido, fecha o None)], que es lo que da steam.player_achievements (y
    psn.trophy_details con los trofeos).

    Los que no salían en los logros del juego (save_info) se añaden con su clave por nombre, para no perderlos; de los
    que ya estaban apuntados se borran los que ya no tiene y se corrige la fecha si ha cambiado. Devuelve los recién
    conseguidos (los UnlockedAchievement que no estaban), para la actividad.

    Pantallas: Editar perfil (Cuentas vinculadas), Perfil (modal «Vincular cuentas» de su dueño; insignias de las
    portadas, cifras y sección «Logros»), Ficha del juego (tus logros), Inicio (feed: logros).
    """
    if not found:
        return []
    title_ids = {row.title_id for row, _ in found}

    def known():
        """{(título, clave): id del logro} de los juegos de found."""
        return {
            (title_id, api_name): pk
            for pk, title_id, api_name in Achievement.objects.filter(title_id__in=title_ids).values_list(
                "pk", "title_id", "api_name",
            )
        }

    ids = known()
    missing = {
        (row.title_id, api_name): position
        for row, result in found for position, (api_name, _, _) in enumerate(result)
        if (row.title_id, api_name) not in ids
    }
    if missing:
        Achievement.objects.bulk_create(
            [
                Achievement(title_id=title_id, api_name=api_name, name=api_name[:255], position=position)
                for (title_id, api_name), position in missing.items()
            ],
            ignore_conflicts=True,
        )
        ids = known()

    wanted = {
        (row.pk, ids[(row.title_id, api_name)]): unlocked_at
        for row, result in found for api_name, achieved, unlocked_at in result if achieved
    }
    current = {
        (unlock.imported_id, unlock.achievement_id): unlock
        for unlock in UnlockedAchievement.objects.filter(imported__in=[row for row, _ in found])
    }
    UnlockedAchievement.objects.filter(pk__in=[unlock.pk for key, unlock in current.items() if key not in wanted]).delete()
    changed = []
    for key, unlock in current.items():
        if key in wanted and unlock.unlocked_at != wanted[key]:
            unlock.unlocked_at = wanted[key]
            changed.append(unlock)
    UnlockedAchievement.objects.bulk_update(changed, ["unlocked_at"])
    new = [
        UnlockedAchievement(imported_id=imported_id, achievement_id=achievement_id, unlocked_at=unlocked_at)
        for (imported_id, achievement_id), unlocked_at in wanted.items() if (imported_id, achievement_id) not in current
    ]
    UnlockedAchievement.objects.bulk_create(new)
    return new


def _by_rarity(achievement):
    """Clave para ordenar del logro más raro al más común; los que no se sabe, al final.

    Pantallas: Ficha del juego (tus logros, del más raro al más común).
    """
    return achievement.percent is None, achievement.percent or 0


def detail(user, game, order="", platform=""):
    """«Tus logros» de user en game, uno a uno, de una plataforma en que se conocen así (platform, Steam o PlayStation;
    si no se da o no la hay, la primera; con varias versiones, la que más lleva), o None si no hay ninguna.

    Devuelve un dict con la plataforma («platform», su nombre en «label» y cómo llama a sus logros en «noun» y en
    singular en «singular»: trofeos en PlayStation), los que tiene («unlocked», cada logro con su «unlocked_at») y los que
    le faltan («locked»), cada grupo cortado en DETAIL_SHOWN («…_shown») y el resto aparte («…_more»), cuántos hay
    («total»), el más raro de los que tiene («rarest»), las fechas del primero y del último («first» y «last») y los
    órdenes (ORDERS) con el elegido («order»). Con order «raros» los dos grupos van del más raro al más común y con
    «juego», en el orden del juego; si no, los que tiene del más reciente al más antiguo (los que no tienen fecha, al
    final) y los que le faltan del más común al más raro, que suelen ser los más fáciles.

    Pantallas: Ficha del juego («Tus logros», uno a uno).
    """
    items = [item for item in progress_by_game(user, [game.pk]).get(game.pk, []) if item["detailed"]]
    item = next((item for item in items if item["platform"] == platform), items[0] if items else None)
    if item is None:
        return None
    if order not in ORDERS:
        order = ""
    dates = dict(UnlockedAchievement.objects.filter(imported_id=item["imported_id"]).values_list("achievement_id", "unlocked_at"))
    unlocked, locked = [], []
    for achievement in Achievement.objects.filter(title_id=item["title_id"]):
        if achievement.pk in dates:
            achievement.unlocked_at = dates[achievement.pk]
            unlocked.append(achievement)
        else:
            locked.append(achievement)
    if order == "raros":
        unlocked.sort(key=_by_rarity)
        locked.sort(key=_by_rarity)
    elif not order:
        unlocked.sort(key=lambda a: a.unlocked_at.timestamp() if a.unlocked_at else float("-inf"), reverse=True)
        locked.sort(key=lambda a: (a.percent is None, -(a.percent or 0)))
    known_dates = [achievement.unlocked_at for achievement in unlocked if achievement.unlocked_at]
    return {
        "platform": item["platform"],
        "label": item["label"],
        "noun": item["noun"],
        "singular": "trofeo" if item["platform"] == ExternalTitle.Platform.PSN else "logro",
        "unlocked_shown": unlocked[:DETAIL_SHOWN],
        "unlocked_more": unlocked[DETAIL_SHOWN:],
        "locked_shown": locked[:DETAIL_SHOWN],
        "locked_more": locked[DETAIL_SHOWN:],
        "unlocked": len(unlocked),
        "locked": len(locked),
        "total": len(unlocked) + len(locked),
        "rarest": min((a for a in unlocked if a.percent is not None), key=lambda a: a.percent, default=None),
        "first": min(known_dates, default=None),
        "last": max(known_dates, default=None),
        "orders": ORDERS.items(),
        "order": order,
    }


def showcase(user):
    """Sección «Logros» del perfil de user: sus últimos logros conseguidos («latest», SHOWCASE_SHOWN, del más reciente al
    más antiguo y con su juego en achievement.title.game), el más raro («rarest») y cuántos tiene guardados uno a uno
    («count»); None si no tiene ninguno.

    Pantallas: Perfil (sección «Logros» del escaparate).
    """
    unlocks = UnlockedAchievement.objects.filter(imported__user=user).select_related("achievement__title__game")
    count = unlocks.count()
    if not count:
        return None
    return {
        "latest": list(unlocks.filter(unlocked_at__isnull=False)[:SHOWCASE_SHOWN]),
        "rarest": unlocks.filter(achievement__percent__isnull=False).order_by("achievement__percent", "-unlocked_at").first(),
        "count": count,
    }
