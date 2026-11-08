"""Filtros y orden del Catálogo: CatalogFilters valida los parámetros de la dirección (?tipo=, ?anio=, ?orden=…) y da
los juegos que los cumplen, así que una búsqueda se puede compartir con su enlace («RPG de 2015 por menos de 10 €,
doblado al castellano»). Las ediciones de otro juego no salen nunca; los DLC, expansiones y demás trozos de un juego
(models.HIDDEN_TYPES), solo si se piden con ?tipo=.

Pantallas: Catálogo (filtros y orden).
"""

from datetime import timedelta

from django import forms
from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, Subquery, When
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.http import urlencode

from library.models import LibraryEntry, WishlistItem

from .models import (
    DURATION_USABLE_ANSWERS,
    ENDLESS_HOURS,
    HIDDEN_TYPES,
    LANGUAGE_FILTERS,
    Company,
    Game,
    GameMode,
    GamePrice,
    GameType,
    Genre,
    Perspective,
    Platform,
    Theme,
    language_filter,
    search_filter,
)

TRENDING_DAYS = 30
# Popular o de nicho, por los votos que tiene en IGDB (Game.popularity): en el catálogo de noviembre de 2026, 400 o más
# es el 20 % más conocido y menos de 120, el 25 % menos.
POPULAR_MIN = 400
NICHE_MAX = 120
# Votos que hacen falta para ordenar por una nota: con menos, un juego con un solo 100 iría el primero.
MIN_CRITICS = 3
MIN_PLAYERS = 10

# ?tipo=: valor → (etiqueta, tipos de IGDB). Sin tipo salen todos menos HIDDEN_TYPES; con «todo», también esos.
TYPES = {
    "todo": ("Todo, también DLC y expansiones", None),
    "remakes": ("Remakes", (GameType.REMAKE,)),
    "remasters": ("Remasters", (GameType.REMASTER,)),
    "ampliados": ("Ediciones ampliadas", (GameType.EXPANDED,)),
    "ports": ("Ports", (GameType.PORT,)),
    "recopilatorios": ("Recopilatorios", (GameType.BUNDLE,)),
    "expansiones": ("Expansiones", (GameType.EXPANSION, GameType.STANDALONE_EXPANSION)),
    "dlc": ("DLC y packs", (GameType.DLC, GameType.PACK)),
    "episodios": ("Episodios y temporadas", (GameType.EPISODE, GameType.SEASON)),
    "mods": ("Mods", (GameType.MOD,)),
}
RELEASES = {"lanzados": "Ya han salido", "proximos": "Aún no han salido"}
# ?nota=: valor → (etiqueta, campo, mínimo).
SCORES = {
    "critica-90": ("Crítica: 90 o más", "critic_rating", 90),
    "critica-80": ("Crítica: 80 o más", "critic_rating", 80),
    "critica-70": ("Crítica: 70 o más", "critic_rating", 70),
    "jugadores-90": ("Jugadores de IGDB: 90 o más", "igdb_rating", 90),
    "jugadores-80": ("Jugadores de IGDB: 80 o más", "igdb_rating", 80),
    "jugadores-70": ("Jugadores de IGDB: 70 o más", "igdb_rating", 70),
}
# ?duracion=: valor → (etiqueta, desde, hasta) en horas de la duración normal.
DURATIONS = {
    "corta": ("Menos de 10 h", 0, 10),
    "media": ("De 10 a 30 h", 10, 30),
    "larga": ("De 30 a 60 h", 30, 60),
    "muy-larga": ("Más de 60 h", 60, ENDLESS_HOURS),
}
FAME = {"popular": "Muy conocidos", "nicho": "De nicho"}
# ?precio=: valor → (etiqueta, precio máximo en euros) del mejor precio de ahora.
PRICES = {
    "gratis": ("Gratis", 0),
    "5": ("Hasta 5 €", 5),
    "10": ("Hasta 10 €", 10),
    "20": ("Hasta 20 €", 20),
    "30": ("Hasta 30 €", 30),
}
MINE = {
    "no-lo-tengo": "No lo tengo",
    "lo-tengo": "En mi biblioteca",
    "pendiente": "En mi pendiente",
    "deseados": "En mi lista de deseos",
}
ORDERS = {
    "": "Más populares",
    "tendencia": "En tendencia",
    "nombre": "Nombre",
    "recientes": "Más recientes",
    "antiguos": "Más antiguos",
    "critica": "Mejor nota de la crítica",
    "jugadores": "Mejor nota de los jugadores",
    "cortos": "Más cortos",
    "largos": "Más largos",
    "baratos": "Más baratos ahora",
}
# Nombre de cada filtro en las etiquetas de los filtros activos.
LABELS = {
    "tipo": "Tipo", "genero": "Género", "plataforma": "Plataforma", "anio": "Año", "salida": "Salida", "nota": "Nota",
    "duracion": "Duración", "fama": "Fama", "modo": "Modo", "tema": "Tema", "perspectiva": "Perspectiva",
    "idioma": "Idioma", "precio": "Precio", "mio": "Tu biblioteca", "estudio": "Estudio",
}


def eras(today):
    """Opciones de ?anio=: este año, el anterior y las décadas, {valor: (etiqueta, (desde, hasta) o None)}.

    Pantallas: Catálogo (filtro de año).
    """
    found = {
        str(today.year): (str(today.year), (today.year, today.year)),
        str(today.year - 1): (str(today.year - 1), (today.year - 1, today.year - 1)),
    }
    for decade in (2020, 2010, 2000, 1990, 1980):
        label = f"Años {decade}" if decade >= 2000 else f"Años {decade % 100}"
        found[f"{decade}s"] = (label, (decade, decade + 9))
    found["antiguos"] = ("Antes de 1980", (None, 1979))
    return found


class CatalogFilters(forms.Form):
    """Los filtros y el orden del Catálogo. Lo que no es válido (un valor que no existe en la dirección) se ignora en
    vez de dar error: el resto de la búsqueda sigue funcionando.
    """

    # Los que van siempre a la vista; los demás, en «Más filtros», con sus casillas.
    MAIN = ("tipo", "genero", "plataforma", "orden")
    MORE = ("anio", "salida", "nota", "duracion", "fama", "modo", "tema", "perspectiva", "idioma", "precio", "mio")
    CHECKS = (("rebajado", "Rebajados ahora"), ("minimo", "En su mínimo histórico"))

    q = forms.CharField(required=False, max_length=200)
    tipo = forms.ChoiceField(required=False)
    genero = forms.ChoiceField(required=False)
    plataforma = forms.ChoiceField(required=False)
    anio = forms.ChoiceField(required=False)
    salida = forms.ChoiceField(required=False)
    nota = forms.ChoiceField(required=False)
    duracion = forms.ChoiceField(required=False)
    fama = forms.ChoiceField(required=False)
    modo = forms.ChoiceField(required=False)
    tema = forms.ChoiceField(required=False)
    perspectiva = forms.ChoiceField(required=False)
    idioma = forms.ChoiceField(required=False)
    precio = forms.ChoiceField(required=False)
    mio = forms.ChoiceField(required=False)
    orden = forms.ChoiceField(required=False)
    estudio = forms.CharField(required=False, max_length=255)
    rebajado = forms.BooleanField(required=False)
    minimo = forms.BooleanField(required=False)

    def __init__(self, data, user=None, today=None):
        """Prepara las opciones de cada filtro (las de géneros, plataformas, modos, temas y perspectivas, solo las que
        tienen juegos) y valida data, los parámetros de la dirección. «Tu biblioteca» solo se ofrece con sesión.

        Pantallas: Catálogo (filtros y orden).
        """
        super().__init__(data)
        self.user = user if user is not None and user.is_authenticated else None
        self.today = today or timezone.localdate()
        self.eras = eras(self.today)
        self.shops = set(self.user.profile.shops) if self.user is not None else set()
        used = {"games__isnull": False}
        options = {
            "tipo": [("", "Juegos, sin DLC")] + [(key, label) for key, (label, _) in TYPES.items()],
            "genero": [("", "Todos los géneros")] + list(Genre.objects.filter(**used).distinct().values_list("slug", "name")),
            "plataforma": [("", "Todas las plataformas")]
            + list(Platform.objects.filter(**used).distinct().values_list("slug", "name")),
            "anio": [("", "Cualquier año")] + [(key, label) for key, (label, _) in self.eras.items()],
            "salida": [("", "Ya salidos o por salir")] + list(RELEASES.items()),
            "nota": [("", "Cualquier nota")] + [(key, label) for key, (label, _, _) in SCORES.items()],
            "duracion": [("", "Cualquier duración")] + [(key, label) for key, (label, _, _) in DURATIONS.items()],
            "fama": [("", "Conocidos o no")] + list(FAME.items()),
            "modo": [("", "Cualquier modo")] + list(GameMode.objects.filter(**used).distinct().values_list("slug", "name")),
            "tema": [("", "Cualquier tema")] + list(Theme.objects.filter(**used).distinct().values_list("slug", "name")),
            "perspectiva": [("", "Cualquier perspectiva")]
            + list(Perspective.objects.filter(**used).distinct().values_list("slug", "name")),
            "idioma": [("", "Cualquier idioma")] + list(LANGUAGE_FILTERS.items()),
            "precio": [("", "Cualquier precio")] + [(key, label) for key, (label, _) in PRICES.items()],
            "mio": [("", "Lo tenga o no")] + (list(MINE.items()) if self.user is not None else []),
            "orden": list(ORDERS.items()),
        }
        for name, choices in options.items():
            self.fields[name].choices = choices
        self.is_valid()

    def clean_estudio(self):
        """El estudio o editora de ?estudio= (un slug de Company): se ignora si no está en el catálogo.

        Pantallas: Catálogo (juegos de un estudio, desde la ficha técnica de la ficha).
        """
        slug = self.cleaned_data.get("estudio", "")
        if slug and not Company.objects.filter(slug=slug).exists():
            raise forms.ValidationError("No es un estudio del catálogo.")
        return slug

    def value(self, name):
        """Valor válido de un filtro, o vacío (False en las casillas) si no está o no es válido.

        Pantallas: Catálogo (filtros y orden).
        """
        return self.cleaned_data.get(name) or ("" if name not in dict(self.CHECKS) else False)

    def games(self):
        """Juegos que cumplen los filtros, en el orden elegido.

        Pantallas: Catálogo (los juegos que salen).
        """
        return self._order(self._filtered())

    def hidden_count(self):
        """Cuántos juegos más saldrían con ?tipo=todo, es decir, entre los DLC, expansiones y demás que se esconden si
        no se pide su tipo; 0 si ya se ha elegido un tipo.

        Pantallas: Catálogo («hay N más entre DLC y expansiones»).
        """
        if self.value("tipo"):
            return 0
        return self._filtered(by_type=False).filter(game_type__in=HIDDEN_TYPES).count()

    def _prices(self):
        """Precios de ahora de cada juego (para usar dentro de una consulta de juegos), solo de las tiendas en las que
        compra el usuario si las ha elegido.

        Pantallas: Catálogo (filtros de precio, rebajas y mínimo histórico, y orden por precio).
        """
        prices = GamePrice.objects.filter(game=OuterRef("pk"))
        return prices.filter(shop_id__in=self.shops) if self.shops else prices

    def _filtered(self, by_type=True):
        """Juegos que cumplen los filtros, sin ordenar; con by_type=False, sin mirar el tipo.

        Pantallas: Catálogo (los juegos que salen y cuántos se esconden).
        """
        value = self.value
        games = Game.objects.filter(parent__isnull=True)
        if value("q"):
            games = games.filter(search_filter(value("q")))
        if by_type:
            kinds = TYPES.get(value("tipo"), ("", None))[1]
            if kinds:
                games = games.filter(game_type__in=kinds)
            elif value("tipo") != "todo":
                games = games.exclude(game_type__in=HIDDEN_TYPES)
        for name, field in (("genero", "genres"), ("plataforma", "platforms"), ("modo", "modes"), ("tema", "themes"),
                            ("perspectiva", "perspectives")):
            if value(name):
                games = games.filter(**{f"{field}__slug": value(name)})
        if value("anio"):
            first, last = self.eras[value("anio")][1]
            if first:
                games = games.filter(first_release_date__year__gte=first)
            games = games.filter(first_release_date__year__lte=last)
        if value("salida") == "lanzados":
            games = games.filter(first_release_date__lte=self.today)
        elif value("salida") == "proximos":
            games = games.filter(first_release_date__gt=self.today)
        if value("nota"):
            _, field, minimum = SCORES[value("nota")]
            games = games.filter(**{f"{field}__gte": minimum})
        if value("duracion"):
            # Cada tramo va desde «desde» hasta justo antes de «hasta»; el último llega hasta ENDLESS_HOURS incluido.
            _, low, high = DURATIONS[value("duracion")]
            top = {"ttb_normal__lte": high * 60} if high >= ENDLESS_HOURS else {"ttb_normal__lt": high * 60}
            games = games.filter(ttb_count__gte=DURATION_USABLE_ANSWERS, ttb_normal__gte=low * 60, **top)
        if value("fama") == "popular":
            games = games.filter(popularity__gte=POPULAR_MIN)
        elif value("fama") == "nicho":
            games = games.filter(popularity__lt=NICHE_MAX)
        if value("estudio"):
            made = Game.objects.filter(Q(developers__slug=value("estudio")) | Q(publishers__slug=value("estudio")))
            games = games.filter(pk__in=made.values("pk"))
        if value("idioma"):
            games = games.filter(language_filter(value("idioma")))
        prices = self._prices()
        if value("precio"):
            games = games.filter(Exists(prices.filter(price__lte=PRICES[value("precio")][1])))
        if value("rebajado"):
            games = games.filter(Exists(prices.filter(cut__gt=0)))
        if value("minimo"):
            games = games.filter(Exists(
                prices.filter(source=GamePrice.Source.ITAD, price__lte=OuterRef("historical_low"))
            ))
        if value("mio") and self.user is not None:
            library = LibraryEntry.objects.filter(user=self.user, game=OuterRef("pk"))
            games = games.filter({
                "no-lo-tengo": ~Exists(library),
                "lo-tengo": Exists(library),
                "pendiente": Exists(library.filter(status=LibraryEntry.Status.BACKLOG)),
                "deseados": Exists(WishlistItem.objects.filter(user=self.user, game=OuterRef("pk"))),
            }[value("mio")])
        return games

    def _order(self, games):
        """Ordena los juegos según ?orden= (por defecto, los más populares). Los que no tienen el dato por el que se
        ordena (fecha, nota con pocos votos, duración con la que contar o precio) van al final.

        Pantallas: Catálogo (orden).
        """
        order = self.value("orden")
        if order == "tendencia":
            since = timezone.now() - timedelta(days=TRENDING_DAYS)
            recent = (
                LibraryEntry.objects.filter(game=OuterRef("pk"), created_at__gte=since)
                .values("game").annotate(n=Count("pk")).values("n")
            )
            return games.annotate(recent=Coalesce(Subquery(recent), 0)).order_by("-recent", "-popularity", "name")
        if order in ("cortos", "largos"):
            usable = Q(ttb_count__gte=DURATION_USABLE_ANSWERS, ttb_normal__lte=ENDLESS_HOURS * 60)
            games = games.annotate(hours=Case(When(usable, then=F("ttb_normal")), output_field=IntegerField()))
            hours = F("hours").asc(nulls_last=True) if order == "cortos" else F("hours").desc(nulls_last=True)
            return games.order_by(hours, "-popularity", "name")
        if order == "baratos":
            cheapest = self._prices().order_by("price").values("price")[:1]
            return games.annotate(cheapest=Subquery(cheapest)).order_by(
                F("cheapest").asc(nulls_last=True), "-popularity", "name"
            )
        if order in ("critica", "jugadores"):
            field, count, minimum = (
                ("critic_rating", "critic_rating_count", MIN_CRITICS) if order == "critica"
                else ("igdb_rating", "igdb_rating_count", MIN_PLAYERS)
            )
            games = games.annotate(
                score=Case(When(**{f"{count}__gte": minimum}, then=F(field)), output_field=games.model._meta.get_field(field))
            )
            return games.order_by(F("score").desc(nulls_last=True), "-popularity", "name")
        return games.order_by(*{
            "nombre": ["name"],
            "recientes": [F("first_release_date").desc(nulls_last=True), "name"],
            "antiguos": [F("first_release_date").asc(nulls_last=True), "name"],
        }.get(order, ["-popularity", "name"]))

    def params(self, exclude=()):
        """Los filtros válidos que hay puestos (también la búsqueda y el orden), {nombre: valor}, sin los de exclude.

        Pantallas: Catálogo (paginación, quitar un filtro y «ver también DLC y expansiones»).
        """
        names = ["q", *self.MAIN, *self.MORE, "estudio"] + [name for name, _ in self.CHECKS]
        found = {}
        for name in names:
            current = self.value(name)
            if current and name not in exclude:
                found[name] = "1" if current is True else current
        return found

    def query_string(self, exclude=(), **extra):
        """Los filtros puestos (params) como parámetros de una dirección, con los de extra cambiados.

        Pantallas: Catálogo (paginación, quitar un filtro y «ver también DLC y expansiones»).
        """
        return urlencode({**self.params(exclude), **extra})

    def selects(self, names):
        """Desplegables de esos filtros para la plantilla: [{"name", "label", "options", "value"}].

        Pantallas: Catálogo (filtros y orden).
        """
        return [
            {"name": name, "label": LABELS.get(name, "Orden"), "options": self.fields[name].choices, "value": self.value(name)}
            for name in names
        ]

    @property
    def main_selects(self):
        """Desplegables que van siempre a la vista: tipo, género, plataforma y orden.

        Pantallas: Catálogo (filtros y orden).
        """
        return self.selects(self.MAIN)

    @property
    def more_selects(self):
        """Desplegables de «Más filtros» (sin «Tu biblioteca» si no hay sesión).

        Pantallas: Catálogo («Más filtros»).
        """
        return self.selects([name for name in self.MORE if name != "mio" or self.user is not None])

    @property
    def checks(self):
        """Casillas de «Más filtros»: [{"name", "label", "value"}].

        Pantallas: Catálogo («Más filtros»).
        """
        return [{"name": name, "label": label, "value": self.value(name)} for name, label in self.CHECKS]

    @property
    def more_active(self):
        """Cuántos filtros de «Más filtros» hay puestos, para decirlo en su botón.

        Pantallas: Catálogo («Más filtros»).
        """
        return sum(1 for name in [*self.MORE, *dict(self.CHECKS)] if self.value(name))

    @property
    def active(self):
        """Filtros puestos (sin la búsqueda ni el orden), cada uno con el enlace que lo quita: [(texto, enlace)].

        Pantallas: Catálogo (los filtros puestos, que se quitan uno a uno).
        """
        chips = []
        for name in [*self.MAIN, *self.MORE, "estudio"]:
            current = self.value(name)
            if not current or name == "orden":
                continue
            if name == "estudio":
                text = Company.objects.filter(slug=current).values_list("name", flat=True).first() or current
            else:
                text = dict(self.fields[name].choices).get(current, current)
            chips.append((f"{LABELS[name]}: {text}", f"?{self.query_string(exclude=(name,))}"))
        for name, label in self.CHECKS:
            if self.value(name):
                chips.append((label, f"?{self.query_string(exclude=(name,))}"))
        return chips
