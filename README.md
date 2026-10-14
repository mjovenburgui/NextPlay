# NextPlay

«Tu vida gamer. En un solo lugar». Red social para jugadores: biblioteca, reseñas, listas y comunidad.

Estado actual — **corte 1**: registro e inicio de sesión, catálogo de juegos (importado de IGDB),
biblioteca personal con estados y perfil público.

## Stack

Django 6 con plantillas del servidor + HTMX, CSS propio. PostgreSQL en producción (`DATABASE_URL`);
SQLite en local si no se define.

## Puesta en marcha

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Linux/macOS: .venv/bin/python
cp .env.example .env
.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py seed_demo                   # 12 juegos de prueba, sin credenciales
.venv/Scripts/python manage.py runserver
```

La app queda en http://localhost:8000. Para entrar en `/admin/`: `manage.py createsuperuser`.

## Catálogo real (IGDB)

1. Crea una aplicación en https://dev.twitch.tv/console/apps y copia su Client ID y Client Secret
   a `IGDB_CLIENT_ID` e `IGDB_CLIENT_SECRET` en `.env`.
2. Importa:

```bash
.venv/Scripts/python manage.py import_igdb --limit 2000    # los más populares
.venv/Scripts/python manage.py import_igdb --search "hollow knight"
```

La importación es idempotente y adopta los juegos de `seed_demo` en lugar de duplicarlos. Con las
credenciales configuradas, un usuario con sesión iniciada también puede traer de IGDB los
resultados de una búsqueda desde el catálogo.

### Nota de la crítica y precios

La importación de IGDB guarda también la nota media de la crítica (`aggregated_rating`) y el ID de
Steam de cada juego. Con ese ID, la ficha del juego muestra los precios actuales en tiendas de PC
(Steam, GOG, Epic…) y el mínimo histórico, obtenidos de [IsThereAnyDeal](https://isthereanydeal.com/):

1. Registra una app en https://isthereanydeal.com/apps/my/ y copia su clave a `ITAD_API_KEY` en `.env`
   (`ITAD_COUNTRY` elige la tienda regional, `ES` por defecto). Sin clave solo se actualiza Xbox.
2. Actualiza los precios (pensado para programarse una vez al día):

```bash
.venv/Scripts/python manage.py update_prices              # todos los juegos con ID de Steam
.venv/Scripts/python manage.py update_prices --limit 500  # solo los más populares
```

El precio de Xbox (consola) sale del catálogo público de la tienda de Microsoft, con los IDs de producto
que da IGDB; no necesita clave y usa el mismo país que `ITAD_COUNTRY`. Es una API sin documentar, así que
podría cambiar. Para PlayStation y Nintendo no hay fuente de precios: la ficha enlaza a una búsqueda en
su tienda si el juego está en esas consolas.

Si la versión original de un juego ya no se vende (p. ej. Skyrim), se muestran los precios de su edición
o remaster que sí está a la venta, según la relación que mantiene IGDB. Cuando IGDB no la tiene (p. ej.
GTA V → GTA V Enhanced), pon el ID de Steam correcto en el campo «ID de Steam manual» del juego en
`/admin/`; la importación de IGDB no lo sobrescribe.

Los juegos importados antes de este cambio necesitan reimportarse con `import_igdb` para obtener nota
e ID de Steam.

## Tests

```bash
.venv/Scripts/python manage.py test
```

## Estructura

| App        | Responsabilidad                                              |
|------------|--------------------------------------------------------------|
| `accounts` | Usuario propio, `Profile`, registro, perfil público/privado  |
| `games`    | `Game`, `Genre`, `Platform`, catálogo y cliente de IGDB      |
| `library`  | `LibraryEntry`: un estado por usuario y juego                |

## Siguientes cortes

2. Importación de Steam, puntuaciones y reseñas, tarjeta compartible.
3. Follows, feed de actividad, likes y comentarios.
4. Listas, bloqueo y denuncia, beta.
