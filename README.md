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

### Historial de precios y lista de deseos

La ficha enseña el historial del precio más barato en PC del último año (de IsThereAnyDeal, con la
misma clave) y un veredicto: «mínimo histórico», «buen precio» o «suele bajar más: espera». Se pide
al abrir la ficha, se carga con HTMX para no frenarla y se guarda un día.

Con «Lo quiero» el juego pasa a la lista de deseos (`/biblioteca/lista-de-deseos/`), con un precio de
aviso opcional. Al terminar, `update_prices` avisa (en la campana de la cabecera y por correo, que se
desactiva en Editar perfil) de los juegos que bajan de ese precio o están rebajados a su mínimo
histórico. Para que los enlaces de los correos apunten a la web pública, pon `SITE_URL` en `.env`.

En Editar perfil › Tiendas en las que compras, cada usuario elige sus tiendas: la ficha solo enseña
sus precios (con «Ver todas» para comparar un momento), la gráfica sale por defecto de sus tiendas y
la lista de deseos y los avisos solo cuentan esas. El historial se guarda por tienda, así que la
gráfica también puede verse de todas o de una tienda concreta.

## Importar de Xbox (aplicación de Azure)

Para el botón «Vincular con Microsoft» hace falta una aplicación de Azure (gratis):

1. En https://portal.azure.com › Microsoft Entra ID › **Registros de aplicaciones** › Nuevo registro.
2. Tipos de cuenta: **Solo cuentas personales de Microsoft**. URI de redirección: **Web** y
   `http://localhost:8000/cuenta/xbox/vuelta/` (en producción, la misma ruta con tu dominio).
3. Copia el «Id. de aplicación (cliente)» a `XBOX_CLIENT_ID`. En **Certificados y secretos** › Nuevo
   secreto de cliente, copia el **Valor** (no el «Id. de secreto») a `XBOX_CLIENT_SECRET`.
4. Reinicia `runserver`. No se guarda ningún token de Microsoft: cada sincronización pide iniciar sesión.

## Juegos importados sin emparejar

Cada juego importado de Steam, PlayStation o Xbox se guarda como título de su plataforma
(`ExternalTitle`) con el juego del catálogo que le corresponde, y cada usuario con sus horas
(`ImportedTitle`). Los que no se encuentran aparecen en `/cuenta/juegos-importados/` para emparejarlos
a mano, y la ficha de un juego importado ofrece «¿No es este el juego que jugaste?» para corregir los
mal emparejados (remakes que se llaman igual). La corrección sirve para todos los usuarios en su
próxima importación. En `/admin/` › Títulos importados se revisan las correcciones («emparejado por:
Usuario»), se corrigen al momento para todos y se pueden bloquear.

## Tareas de cada noche

`nightly` sincroniza la biblioteca de Steam de quien tenga activada la sincronización nocturna (se
desactiva desde el panel de Steam del perfil) y después actualiza los precios:

```bash
.venv/Scripts/python manage.py nightly
.venv/Scripts/python manage.py sync_libraries --user ana   # solo un usuario, aunque la tenga desactivada
```

`sync_libraries` espera 3 segundos entre usuarios (`--pause`) para repartir las llamadas a Steam e IGDB
y se salta a quien se haya sincronizado en las últimas 12 horas (`--min-hours`). PlayStation y Xbox no
se sincronizan solas porque no se guarda el código ni el token de la cuenta.

Para programarlo, en Linux con cron (todos los días a las 4:00):

```bash
0 4 * * * cd /ruta/a/NextPlay && .venv/bin/python manage.py nightly >> nightly.log 2>&1
```

En Windows, con el Programador de tareas (desde la carpeta del proyecto):

```bash
schtasks /Create /TN "NextPlay nightly" /SC DAILY /ST 04:00 /TR "cmd /c cd /d %CD% && .venv\Scripts\python.exe manage.py nightly >> nightly.log 2>&1"
```

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
