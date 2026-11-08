"""Un solo sitio para crear los avisos de la campana (accounts.models.Notification) y decidir si llegan.

Todo lo que avisa a alguien en NextPlay pasa por notify(): las bajadas de precio, los nuevos seguidores, los «me
gusta», los comentarios, las invitaciones a editar listas, las reseñas que se han quedado atrás y las denuncias.
notify() decide lo mismo para todos: nadie recibe avisos de lo que hace él mismo, no se repiten (cada tipo tiene su
regla en REPEAT) y solo llega lo que cada uno ha dejado encendido en la sección «Avisos» de Editar perfil (GROUPS y
wants()). Un tipo de aviso nuevo necesita su regla y su grupo: los tests lo comprueban.

Pantallas: Avisos, todas (el número de la campana), Editar perfil (Avisos).
"""

from dataclasses import dataclass

from .models import Notification

Kind = Notification.Kind
TEXT_MAX_LENGTH = Notification._meta.get_field("text").max_length

# Por dónde llega un aviso: la campana de la barra de arriba o el correo.
BELL = "campana"
EMAIL = "correo"


@dataclass(frozen=True)
class Repeat:
    """Regla para no repetir un aviso: no se crea si la misma persona ya tiene otro del mismo tipo que coincide en
    fields («actor», quién lo provoca, y «url», aquello de lo que avisa) y, con unread, que aún no ha leído. Sin
    fields se avisa siempre, porque quien avisa ya sabe si toca.
    """

    fields: tuple = ()
    unread: bool = False


# Siempre: la lista de deseos ya guarda a qué precio avisó, y nadie puede estar invitado dos veces a la misma lista.
ALWAYS = Repeat()
# Una vez por persona y cosa, aunque quite el «me gusta» y lo vuelva a dar.
ONCE_PER_PERSON = Repeat(("actor", "url"))
# Mientras no haya leído el anterior de esa persona en esa cosa: cinco comentarios seguidos son un solo aviso.
UNTIL_READ = Repeat(("actor", "url"), unread=True)

# La regla de cada tipo de aviso.
REPEAT = {
    Kind.PRICE: ALWAYS,
    Kind.LIKE: ONCE_PER_PERSON,
    Kind.COMMENT: UNTIL_READ,
    # Uno por reseña hasta que la vuelva a guardar: library.reminders pasa since con la fecha en que la guardó.
    Kind.REVIEW_UPDATE: Repeat(("url",)),
    # Una vez por persona aunque deje de seguirte y vuelva; el enlace cambia según puedas ver su perfil.
    Kind.FOLLOW: Repeat(("actor",)),
    Kind.POST_LIKE: ONCE_PER_PERSON,
    Kind.POST_COMMENT: UNTIL_READ,
    Kind.LIST_LIKE: ONCE_PER_PERSON,
    Kind.LIST_COMMENT: UNTIL_READ,
    Kind.LIST_INVITE: ALWAYS,
    # Una vez por persona que hace su versión de una tier list abierta o acepta editar una lista.
    Kind.LIST_VERSION: ONCE_PER_PERSON,
    Kind.LIST_ACCEPT: ONCE_PER_PERSON,
    # De los retos, uno de cada por reto: conseguirlo, que le quedan pocos días y cómo ha terminado.
    Kind.CHALLENGE_WON: Repeat(("url",)),
    Kind.CHALLENGE_ENDING: Repeat(("url",)),
    Kind.CHALLENGE_RESULT: Repeat(("url",)),
    # A quien lo creó, una vez por cada persona que se une, aunque lo deje y vuelva.
    Kind.CHALLENGE_JOINED: ONCE_PER_PERSON,
    # Una por denuncia mientras el administrador no haya leído la anterior.
    Kind.REPORT: Repeat(("url",), unread=True),
}


@dataclass(frozen=True)
class Group:
    """Una línea de la sección «Avisos» de Editar perfil: su nombre, lo que explica, los tipos de aviso que enciende o
    apaga y si también pueden llegar por correo.
    """

    label: str
    help_text: str
    kinds: tuple
    email: bool = False


# Lo que se puede elegir en Editar perfil, en el orden en que sale. Las denuncias no están: solo llegan a los
# administradores y no se pueden apagar.
GROUPS = {
    "precios": Group(
        "Bajadas de precio", "Cuando un juego de tu lista de deseos baja de tu límite o llega a su mínimo histórico.",
        (Kind.PRICE,), email=True,
    ),
    "seguidores": Group("Nuevos seguidores", "Cuando alguien empieza a seguirte.", (Kind.FOLLOW,)),
    "me_gusta": Group(
        "«Me gusta»", "Cuando a alguien le gusta una reseña, una publicación o una lista tuya.",
        (Kind.LIKE, Kind.POST_LIKE, Kind.LIST_LIKE),
    ),
    "comentarios": Group(
        "Comentarios", "Cuando alguien comenta una reseña, una publicación o una lista tuya.",
        (Kind.COMMENT, Kind.POST_COMMENT, Kind.LIST_COMMENT),
    ),
    "invitaciones": Group(
        "Invitaciones a editar listas", "Cuando alguien te invita a añadir y ordenar los juegos de una lista suya.",
        (Kind.LIST_INVITE,),
    ),
    "listas": Group(
        "Tus listas",
        "Cuando alguien hace su versión de una tier list tuya abierta a la comunidad o acepta editar una lista tuya.",
        (Kind.LIST_VERSION, Kind.LIST_ACCEPT),
    ),
    "resenas": Group(
        "Reseñas que se han quedado atrás",
        "Cuando llevas muchas más horas en un juego que al escribir su reseña, por si quieres actualizarla.",
        (Kind.REVIEW_UPDATE,),
    ),
    "retos": Group(
        "Retos",
        "Cuando consigues un reto, cuando a uno en el que estás le quedan pocos días, cuando termina y cuando alguien "
        "se une a uno tuyo.",
        (Kind.CHALLENGE_WON, Kind.CHALLENGE_ENDING, Kind.CHALLENGE_RESULT, Kind.CHALLENGE_JOINED),
    ),
}
# El grupo de cada tipo de aviso.
GROUP_OF = {kind: key for key, group in GROUPS.items() for kind in group.kinds}


def enabled(prefs, key, channel=BELL):
    """Si el grupo key está encendido para channel en prefs (lo guardado en Profile.notification_prefs). Lo que no
    está guardado, sí.

    Pantallas: Editar perfil (Avisos: cómo sale cada interruptor), Avisos, todas (el número de la campana).
    """
    return ((prefs or {}).get(key) or {}).get(channel, True) is not False


def wants(user, kind, channel=BELL):
    """Si user quiere los avisos de tipo kind por channel (BELL o EMAIL), según lo que eligió en Editar perfil.

    Por correo solo llegan los grupos que lo admiten (hoy, las bajadas de precio). Los tipos sin grupo, las
    denuncias, llegan siempre a la campana y nunca por correo.

    Pantallas: Avisos, todas (el número de la campana), Lista de deseos (cómo te avisamos de las bajadas de precio).
    """
    key = GROUP_OF.get(kind)
    if key is None:
        return channel == BELL
    if channel == EMAIL and not GROUPS[key].email:
        return False
    profile = getattr(user, "profile", None)
    return enabled(profile.notification_prefs if profile else None, key, channel)


def notify(user, kind, text, url="", *, game=None, actor=None, since=None):
    """Avisa a user en la campana: crea y devuelve el aviso de tipo kind con text, url, el juego cuya portada sale
    al lado y quién lo ha provocado (actor). No lo crea, y devuelve None, si:

    - actor es el propio user: nadie recibe avisos de lo que hace él mismo;
    - user ha apagado ese tipo de aviso en Editar perfil (wants);
    - ya tiene uno que la regla de ese tipo no deja repetir (REPEAT); con since, solo cuentan los de desde entonces.

    El texto se corta a lo que cabe en Notification.text. Cuando existan los bloqueos y los silencios, este es el
    único sitio que tendrá que mirarlos.

    Pantallas: Avisos, todas (el número de la campana).
    """
    if actor is not None and actor.pk == user.pk:
        return None
    if not wants(user, kind):
        return None
    rule = REPEAT[kind]
    if rule.fields:
        values = {"actor": actor, "url": url}
        earlier = Notification.objects.filter(user=user, kind=kind, **{name: values[name] for name in rule.fields})
        if rule.unread:
            earlier = earlier.filter(read_at=None)
        if since is not None:
            earlier = earlier.filter(created_at__gte=since)
        if earlier.exists():
            return None
    return Notification.objects.create(
        user=user, kind=kind, text=text[:TEXT_MAX_LENGTH], url=url, game=game, actor=actor,
    )
