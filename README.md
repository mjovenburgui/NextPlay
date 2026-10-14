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
