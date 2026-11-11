# Bitácora de trabajo

Aquí apunto qué estoy haciendo en NextPlay mientras trabajo por mi cuenta. Lo último va arriba.

Cada entrada dice:
- qué cambia para ti;
- qué he tocado;
- cómo lo he comprobado;
- en qué rama y commit está;
- qué falta, sobre todo lo que depende de ti.

El detalle de cada función está en `NextPlay-funciones.pdf`, y el de cada propuesta, en `NextPlay-propuestas.pdf`.

## Estado de las ramas

Todas las ramas de abajo están fusionadas con `main`, cada una con su PR, menos las de arriba del todo (`sesiones-segunda-cuenta` y las que van encima), que esperan tu revisión. Las migraciones de las cuatro del 08/11 ya están en tu base de datos de verdad (comprobado el 08/11/2026). También está la de `pasada-a-igdb`, `games.0010`, con la pasada a IGDB hecha (comprobado el 09/11/2026: sin migraciones pendientes y con la ficha técnica de 2235 de sus 2236 juegos). `ficha-ligera-y-dlc` no trae migraciones. Justo después de fusionarla, en el mismo paso, traigo los DLC y las expansiones a tu base de datos con `import_addons`, con una copia de seguridad antes. Lo que salga te lo digo al terminar.

| Rama | Qué lleva | Estado |
|---|---|---|
| `gente-que-sigues` | La gente que sigues en la ficha y en los perfiles: su recuadro y sus reseñas delante, «En común», «A quién seguir» y un aviso nuevo | Commit local, sin subir, encima de `diario-de-partidas`. Trae la migración `accounts.0027`. |
| `diario-de-partidas` | Diario de partidas: pestaña Diario del perfil con el calendario de cada año, línea en la ficha y juegos en «Jugando» sin jugar en el repaso | Commit local, sin subir, encima de `sesiones-segunda-cuenta`. Trae las migraciones `library.0015` y `accounts.0026`. |
| `sesiones-segunda-cuenta` | Las cuentas de Steam recién vinculadas ya no cuentan como sesiones de juego | Commit local, sin subir: espera tu revisión. Sin migraciones; las sesiones de `tester_steam` ya están corregidas en tu base de datos. |
| `ficha-ligera-y-dlc` | Ficha del juego más ligera: tres columnas, lo secundario plegado y cada DLC con su ficha y su precio | Fusionada con `main`: [PR #39](https://github.com/mjovenburgui/NextPlay/pull/39). Sin migraciones; los DLC llegan a tu base de datos con `import_addons`. |
| `pasada-a-igdb` | Una pasada a IGDB: ficha técnica, duración, ediciones agrupadas, sin DLC en las búsquedas y catálogo con filtros | Fusionada con `main`: [PR #38](https://github.com/mjovenburgui/NextPlay/pull/38). Trae la migración `games.0010`. |
| `avisos-en-un-solo-sitio` | Avisos en un solo sitio, con preferencias por tipo | Fusionada con `main`: [PR #34](https://github.com/mjovenburgui/NextPlay/pull/34). |
| `retos-fase-2` | Retos: insignias, sección en el perfil, feed, avisos y cierre cada noche | Fusionada con `main`: [PR #35](https://github.com/mjovenburgui/NextPlay/pull/35). |
| `repaso-de-la-biblioteca` | Repaso de la biblioteca y valor del pendiente | Fusionada con `main`: [PR #36](https://github.com/mjovenburgui/NextPlay/pull/36). |
| `remates-rapidos` | Siete remates rápidos, un commit por cada uno, y los PDF al día | Fusionada con `main`: [PR #37](https://github.com/mjovenburgui/NextPlay/pull/37). |

**Migraciones de estas cuatro ramas:**
- `accounts.0022` a `accounts.0025`;
- `challenges.0002`;
- `social.0008` y `social.0009`;
- `library.0014`.

Si un servidor de `main` da error de columnas que no existen, es que a esa base de datos le faltan estas migraciones: `manage.py migrate`.

---

## 11/11/2026 · La gente que sigues, en la ficha y en los perfiles

**Rama:** `gente-que-sigues`, encima de `diario-de-partidas`. Commit local, sin subir.

**Por qué:** era la dos de tu «ve haciendo la uno y la dos». Seguir a alguien solo servía para el feed; ahora su opinión sale justo donde decides qué jugar.

**Qué cambia para ti:**
- **En la ficha de un juego**, debajo de «Notas», un recuadro «Gente que sigues»: quién lo tiene, en qué estado, con qué nota y cuántas horas importadas, un enlace a su reseña y la media de sus notas. Con `tester_steam` en la copia, demo1 sale en la de The Witcher 3.
- **Las reseñas de la gente que sigues van delante** de las demás, con la etiqueta «Siguiendo», tanto en «Recientes» como en «Más gustadas».
- **En el perfil de otro**, una pestaña nueva, «En común»:
  - los juegos que tenéis los dos, con las horas y la nota de cada uno;
  - una frase que dice con qué se compara: con 5 o más juegos valorados por los dos, cuánto se parecen vuestras notas («de media os separan 0,8 estrellas»); si no, los juegos en común y si os van los mismos géneros, que salen como etiquetas;
  - el juego en el que más discrepáis, si vuestras notas se separan dos estrellas o más.
  - No sale ni en tu propio perfil ni sin sesión.
- **En Inicio**, «A quién seguir» en la columna derecha: gente con tus mismos juegos a la que aún no sigues. Si no sigues a nadie, el feed ya te la propone y la columna no la repite.
- **Un aviso nuevo**: cuando alguien a quien sigues reseña un juego que tienes en «Lo quiero» o en tu pendiente («Laura ha reseñado Hades, que tienes en «Lo quiero»»). Se apaga en Editar perfil › Avisos, en «Reseñas de la gente que sigues». Llega una vez por persona y reseña. Si el perfil del autor es privado, no llega a nadie.
- De un perfil privado no sale nada en ninguno de estos sitios.
- **Y en el diario:** en el móvil, el calendario del año en curso empieza por el final, que es lo último jugado.

**Qué he tocado:**
- `social/affinity.py`, nuevo: la gente que sigues en un juego, lo que tenéis en común, la frase de compatibilidad y el aviso.
- `games/views.py`, `templates/games/detail.html` y el nuevo `templates/games/_friends.html`: el recuadro de la ficha.
- `library/views/ficha_del_juego.py` y `templates/library/_review.html`: las reseñas de la gente que sigues delante.
- `accounts/views/perfil.py`, `templates/accounts/profile.html` y el nuevo `templates/social/_in_common.html`: la pestaña En común.
- `templates/games/home.html`: «A quién seguir».
- `accounts/models.py` y `accounts/notices.py`: el tipo de aviso, su regla y su grupo (migración `accounts.0027`). `library/views/escribir_resena.py` lo lanza al publicar.
- `library/diary.py` y `templates/library/_diary.html`: el calendario del año en curso empieza por el final.
- `static/css/app.css`, `docs/pantallas.json`, la propuesta y los dos PDF.

**Cómo lo he comprobado:**
- 6 tests nuevos: el recuadro y quién sale en él, el orden de las reseñas, En común con notas y sin ellas, el aviso y «A quién seguir».
- La batería completa, sin claves y con claves falsas y un proxy muerto: 584 tests en cada pasada, todos bien.
- Capturas en la copia con `tester_steam`: la ficha de The Witcher 3, En común con demo1 e Inicio.

**Qué falta:**
- Que lo revises. Al fusionar, tu base de datos necesita `migrate` (`accounts.0027`).
- Cuando existan los bloqueos, respetarlos aquí también, y ordenar «A quién seguir» por las notas cuando haya más.

---

## 11/11/2026 · Diario de partidas: lo que juegas y cuándo, sin apuntar nada

**Rama:** `diario-de-partidas`, encima de `sesiones-segunda-cuenta`. Commit local, sin subir.

**Por qué:** me pediste «ve haciendo la uno y la dos», y la uno era «Diario de partidas que se escribe solo», la más gorda del núcleo.

**Qué cambia para ti:**
- **Pestaña Diario en el perfil:**
  - arriba, tu última partida: el juego, el día y la hora, la plataforma y lo que jugaste ese día si hay sesión;
  - el calendario del año, una casilla por día: el tono, en el color del perfil, dice cuánto jugaste, y en dorado salen los días en que conseguiste algún logro, aunque no haya sesión;
  - el año se cambia arriba, con todos los que tienen algo;
  - las cifras del año: horas en sesiones, días con partida o logro, la racha más larga y lo que más has jugado;
  - las últimas sesiones, con su día exacto o «entre el 1 y el 4 nov» si vienen de una sincronización tardía.
- **Con tus datos**, en la copia: 167 días con algo en 2026 aunque solo haya 10 sesiones, gracias a los logros, y años desde 2022.
- **En la ficha**, debajo de tus horas: «Primer logro el 25 oct 2026 · 2 sesiones, la última el 31 oct (5 h 47 min, aproximado) · Tu diario». Con PlayStation dice «Empezado el …», porque PlayStation da la primera partida.
- **Privacidad:** un interruptor nuevo en Editar perfil, «Diario de partidas visible». Apagado, la pestaña solo la ves tú.
- **Repasar la biblioteca** pregunta también por los juegos en «Jugando» cuya última partida importada es de hace más de un mes, si no los has tocado desde entonces. Antes hacían falta seis meses sin tocarlos. Es lo que la propuesta llamaba «estados sugeridos», pero en el repaso, que ya tiene sus botones, en vez de en un aviso nuevo.

**Qué he tocado:**
- `library/models.py`: `PlaySession.since` (el día de la partida anterior) y `LibraryEntry.timeline`. Migración `library.0015`.
- `accounts/matching.py`: al apuntar una sesión, desde qué día viene.
- `library/diary.py`, nuevo: el calendario, la última partida, las últimas sesiones y la línea de la ficha.
- `accounts/views/perfil.py`, `templates/accounts/profile.html` y el nuevo `templates/library/_diary.html`: la pestaña.
- `templates/library/_widget.html`: la línea de la ficha.
- `accounts/models.py` y `accounts/forms.py`: `Profile.diary_visible`. Migración `accounts.0026`.
- `library/backlog.py`: `IDLE_DAYS` para el repaso.
- `static/css/app.css`, la semilla (`seed_test_profile`: sus sesiones son de un solo día), `docs/pantallas.json`, la propuesta y los dos PDF.

**Cómo lo he comprobado:**
- 7 tests nuevos: el calendario y sus cifras, las sesiones del mismo día, desde cuándo viene cada sesión al importar, la última partida, la pestaña y quién la ve, la línea de la ficha y el repaso.
- La batería completa, sin claves y con claves falsas y un proxy muerto: 578 tests. Fallaba uno, que contaba un solo interruptor en Privacidad; ya está ajustado.
- Capturas de la pestaña y de la ficha con `tester_steam`, en una copia de tu base de datos con las migraciones.

**Qué falta:**
- Que lo revises. Al fusionar, tu base de datos necesita `migrate` por las dos migraciones.
- Lo pendiente de la propuesta: las horas de la semana en el feed y repartir las sesiones aproximadas en los retos de horas.

---

## 10/11/2026 · Las cuentas de Steam recién vinculadas ya no cuentan como sesiones de juego

**Rama:** `sesiones-segunda-cuenta`, encima de `main`. Commit local, sin subir.

**Por qué:** me preguntaste «¿se puede traer de Steam la última sesión del usuario?». Al mirarlo salió que, al vincular tu segunda cuenta de Steam el 02/11, la importación apuntó su historial entero como si lo hubieras jugado esos días. Elegiste «Arreglar las sesiones».

**Qué cambia para ti:**
- Al vincular otra cuenta de Steam, sus horas se suman a tu biblioteca como siempre, pero ya no cuentan como sesiones de juego: son de antes de vincularla. Las sesiones son lo que miden los retos de horas y las plantillas «Jugados en…» de las tier lists, y serán la base del diario de partidas.
- Desde la siguiente importación, lo que juegues en esa cuenta sí cuenta.
- En tu base de datos, `tester_steam` tenía 7 sesiones infladas por la segunda cuenta, no 2: 25.252 min (420,9 h) de 27.679. La peor, 19.841 min de 7 Days to Die en un solo día. He borrado 6 y he dejado la de Diablo IV del 01/11 en sus 57 min reales. Se queda con 10 sesiones y 2427 min. No estaba en ningún reto, así que no se ha dado ninguno por conseguido.

**Qué he tocado:**
- `accounts/steam.py`: `import_library` junta los minutos de las cuentas que se importan por primera vez (sin `synced_at`) y se los pasa a `update_library`.
- `accounts/matching.py`: `update_library(prior_minutes=…)` los descuenta de lo que ha subido cada juego antes de apuntar la sesión.
- `library/models.py`: el docstring de `PlaySession`.
- `accounts/tests.py`: un test con dos cuentas. Lo que trae la nueva no es sesión; en la importación siguiente, lo que sube en ella sí.
- `docs/propuestas.json`: notas y revisión de «Diario de partidas que se escribe solo», también con lo comprobado de la API de Steam. Los dos PDF, regenerados.

**Cómo lo he comprobado:**
- La batería completa, sin claves y con claves falsas y un proxy muerto: 571 tests en cada pasada, todos bien.
- La corrección de datos, primero en seco y con dos comprobaciones: que la segunda cuenta no hubiera jugado esos juegos después y que ninguna sesión anterior del mismo juego y día hubiera recibido la suma. Antes, una copia de seguridad de la base de datos (`db-antes-de-corregir-sesiones.sqlite3`, en el scratchpad de la sesión).

**Qué falta:**
- Que lo revises. Después: subirla, abrir el PR y fusionarla.
- Lo pendiente de las sesiones sigue en la propuesta: el día de cada sesión, porque todo va al de la última partida, y confirmar si Steam solo da la última partida a la cuenta dueña de la clave.

---

## 09/11/2026 · Ficha del juego más ligera: tres columnas, lo secundario plegado y cada DLC con su ficha y su precio (fusionado con main)

**Rama:** `ficha-ligera-y-dlc`, encima de `main`; en local era la del worktree `game-card-layout-design-39a461`. Fusionada con `main` el 09/11/2026: [PR #39](https://github.com/mjovenburgui/NextPlay/pull/39).

**Por qué:** me preguntaste «¿No crees que muestra demasiada información? Casi que debería verse en desplegables o aprovechar todo el ancho de la web». Sí. Después de la pasada a IGDB, la ficha de GTA V medía unos 4490 px de alto en una pantalla de 1440 px y solo usaba 1100 px de ancho. Además, la columna de la carátula se acababa a media página.

**Qué cambia para ti:**
- **Pantallas de 1280 px o más:** tres columnas en un contenedor de hasta 1440 px, como el de la portada.
  - A la izquierda: la carátula, «Notas», Cuánto dura y tus logros. «Notas» junta en una caja la nota de la crítica, la de los jugadores de IGDB y la de la comunidad.
  - En medio: tu biblioteca, la sinopsis, las imágenes y vídeos, «Dónde comprarlo», «Expansiones y DLC», «Ediciones y versiones» y las reseñas.
  - A la derecha: la ficha técnica, los idiomas, los juegos parecidos y las listas en las que está.
- **Entre 641 y 1279 px:** dos columnas; los datos bajan debajo de la carátula.
- **En el móvil:** una sola columna, en este orden: notas, tu biblioteca, Cuánto dura, sinopsis, imágenes, precios, expansiones y DLC, ficha técnica e idiomas, ediciones, parecidos, reseñas y, al final, logros y listas.
- **A la vista, «Expansiones y DLC»** (me preguntaste «¿y que salgan los DLC?»): una tira propia debajo de «Dónde comprarlo», con las expansiones primero y después los DLC. Cada uno lleva su tipo, su año, su precio más barato de ahora (con la rebaja) y el nombre sin el del juego delante («Shadow of the Erdtree»); el nombre entero sale al pasar el ratón. En la copia, 700 de los 2231 juegos con ficha técnica tienen alguno; The Witcher 3 tiene 8. En GTA V solo sale Grand Theft Auto Online: IGDB no le da más.
- **Cada DLC, con su ficha y su precio** (me preguntaste «¿y pueden salir con ficha, con precio y todo?»):
  - cada expansión o DLC lleva a su propia ficha, con «Dónde comprarlo», historial, imágenes y ficha técnica, como cualquier juego;
  - los que aún no están en el catálogo los trae de IGDB la ficha de su juego al abrirse, por HTMX, y busca a la vez sus precios. Mientras tanto, la tira dice «Trayendo la ficha y el precio de cada uno…»;
  - la ficha de un DLC dice de qué juego es («DLC de Borderlands 2») desde la primera visita;
  - en la copia: Borderlands 2 trae sus 11 DLC en 3,4 s y sus precios en 1,3 s, y 10 salen con precio y rebaja. The Witcher 3 trae 6; salen sin precio los 5 «New Quest», que eran gratis, y Songs of the Past, que sale en 2027;
  - una vez en el catálogo, `update_prices` les pone el precio al día cada noche, y `fill_game_fields`, la ficha técnica. El catálogo los sigue escondiendo salvo que se pidan con `?tipo=`.
- **Todos traídos de golpe** (me preguntaste «¿puedes traer todos los que puedas?»):
  - el comando nuevo `import_addons` trae de IGDB los DLC y las expansiones que faltan, con su ficha técnica, y busca sus precios de PC. Va también en `nightly`; sin nada pendiente no pide nada, y con `--simular` solo cuenta;
  - en la copia ha traído, en unos 4 minutos, los 3014 que faltaban, y en una segunda pasada otros 30: los DLC de las expansiones independientes (como Grand Theft Auto Online), que salen al traer su ficha. Ya tienen ficha propia los 3162 (2314 DLC, 689 expansiones y 121 expansiones independientes);
  - salen con precio 1761. Los demás son gratis, aún no han salido o no se venden en PC;
  - la copia pasa de 2260 a 5304 juegos, pero el catálogo sin filtros solo pasa de 2148 a 2239: los DLC y las expansiones siguen escondidos, y solo se suman las expansiones independientes, que son juegos;
  - Xbox y Nintendo ya no buscan los DLC por su nombre: sus buscadores solo miran juegos y nunca los encontrarían. Con 3000 DLC más, eso habrían sido miles de peticiones inútiles cada noche. Les ponen precio solo si IGDB da su ID de tienda;
  - **tu base de datos de verdad no los tiene.** Al fusionar, hay que lanzar `manage.py import_addons` en ella, con una copia de seguridad antes, o esperar a que `nightly` lo haga cuando lo programes.
- **Plegado:**
  - «Ediciones y versiones», ya sin los DLC: cerrada, dice cuántas ediciones, versiones y recopilatorios hay; si vienes de una edición, viene abierta.
  - «Idiomas».
  - La ficha técnica, si tiene más de seis filas: enseña las cinco que más se miran y deja el resto en «Más datos». La edad (PEGI) sube a esas cinco.
- **Resultado:** la ficha de GTA V pasa de unos 4490 px de alto a unos 2530.

**Qué he tocado:**
- Plantillas:
  - `templates/games/detail.html`: los tres bloques;
  - `_community.html`: ahora es una fila de «Notas»;
  - `_sheet.html` y el nuevo `_sheet_rows.html`: «Más datos»;
  - `_versions.html`: el desplegable;
  - el nuevo `_addons.html`: la tira de expansiones y DLC con sus precios, que también llega por HTMX con la ficha técnica (`_details.html`, que ahora puede traer la cabecera);
  - `_similar.html`: la rejilla de la columna;
  - un comentario de `accounts/_achievement_card.html`.
- `static/css/app.css`:
  - la rejilla de la ficha en tres, dos y una columna;
  - los desplegables, la caja «Notas» y la cabecera al ancho nuevo;
  - fuera las reglas viejas de la columna lateral.
- Código:
  - `games/details.py`: la edad va justo después del cooperativo; las expansiones y los DLC salen de `VERSION_GROUPS` y los prepara `addons`, con el nombre corto de `short_name` y su precio; `import_addons` trae de IGDB los que faltan;
  - `games/views.py`: la ficha y su trozo de HTMX llevan `addons`; la vista nueva `game_addons` (`/juegos/<juego>/expansiones-y-dlc/`) trae los que faltan y busca sus precios; `game_details` manda la cabecera cuando ya se sabe de qué juego es;
  - `games/urls.py`: la dirección nueva;
  - el comando nuevo `games/management/commands/import_addons.py`, y `accounts/management/commands/nightly.py`, que lo lanza después de `fill_game_fields`;
  - `games/models.py` (`STORE_ADDON_TYPES`), `games/xbox.py` y `games/nintendo.py`: los DLC y las expansiones no se buscan por su nombre en las tiendas de consola;
  - docstrings de `games/details.py`, `games/views.py` y `games/igdb.py`: decían «Si te gustó», un nombre que la ficha ya no usa.
- Tests (`games/tests.py`): las notas con sus nombres nuevos, los desplegables, la tira de DLC (también por HTMX) y pruebas nuevas de «Más datos», de los nombres cortos, del precio en la tira, de traer de IGDB los que faltan, de la cabecera de un DLC, del comando `import_addons` y de que Xbox y Nintendo no buscan los DLC por su nombre.
- `README.md` (el comando nuevo), `docs/pantallas.json` (lo que hay en la ficha y su dirección nueva), la propuesta «Ficha del juego más ligera: tres columnas y lo secundario plegado» y los dos PDF.

**Cómo lo he comprobado:**
- En el servidor de revisión del puerto 8001, con una copia de tu base de datos y la cuenta `tester`. Hice capturas a 1440, 1100 y 375 px con estas fichas:
  - GTA V y The Witcher 3;
  - Batman: Arkham Asylum, viniendo de su GOTY y sin sesión;
  - Star Wars: X-Wing, que carga la ficha técnica por HTMX.
- En el móvil no se sale nada de lado. La tira de DLC va justo después de los precios y se pasa de lado.
- Al valorar el juego, la fila «Comunidad» de «Notas» se pone al día por HTMX, como antes.
- Los DLC de The Witcher 3 y de Borderlands 2 se han traído de verdad (IGDB e IsThereAnyDeal) a la copia, y la ficha de uno de ellos (Captain Scarlett and Her Pirate's Booty) sale con sus precios.
- `import_addons` ha traído a la copia todos los que faltaban; antes guardé una copia de seguridad de la base de datos de revisión en el scratchpad. Fallout 4 enseña sus 6 DLC con precio y rebaja.
- Tests: los 570 pasan sin claves y también con claves falsas detrás de un proxy muerto.

**Qué falta:** nada tuyo. Justo después de fusionar traigo los DLC a tu base de datos de verdad con `import_addons`, con una copia de seguridad antes. Lo que salga te lo digo al terminar.

---

## 08/11/2026 · Una pasada a IGDB y las ediciones de cada juego (fusionado con main)

**Rama:** `pasada-a-igdb`, encima de `main`. Fusionada con `main` el 08/11/2026: [PR #38](https://github.com/mjovenburgui/NextPlay/pull/38).

**Por qué:** me dijiste «empieza con la 2 a ver si puedes hacer que también salgan las ediciones de cada juego». Una sola pasada a IGDB trae para todo el catálogo lo que piden cinco propuestas: «Quitar DLC y actualizaciones de las búsquedas en IGDB», «Ficha del juego más completa», «Duración estimada de los juegos», «Ediciones agrupadas bajo su juego» y «Catálogo con filtros y orden al nivel de Backloggd (y más)».

**Plan:**
- [x] **La pasada:** comando `fill_game_fields`. Para los 2235 juegos de la copia tarda menos de un minuto: unas tres peticiones por cada 500 juegos. Guarda tipo, estado, modos, temas, perspectiva, estudio, editora, nota de los usuarios de IGDB, duración, PEGI, sagas, ediciones, DLC, remakes y juegos parecidos. Lo que se filtra va en campos; lo que solo se lee, en `Game.details`.
- [x] **Ediciones:** la ficha enseña las ediciones de cada juego (GOTY, Deluxe…) con su portada. Si alguien tiene una edición importada, cuenta como el juego:
  - la ficha de la edición lleva a la del juego, que lo dice y la marca;
  - lo que se importa de una edición va al juego;
  - lo que ya estaba guardado lo junta `merge_editions`.
- [x] **Sin DLC en las búsquedas de IGDB.** El catálogo los esconde salvo que los pidas, y las tarjetas dicen si es remake, remaster, expansión…
- [x] **Ficha técnica y duración en la ficha.** Estudio, editora, PEGI, modos, cooperativo, temas, saga, motor, nota de los usuarios de IGDB y cuánto dura. Si lo estás jugando, cuánto te falta.
- [x] **Catálogo con filtros y orden.** Año, tipo, nota, duración, modo, tema, estudio, precio, rebajas y tu biblioteca, con la dirección lista para compartir.
- [x] **Extra para el repaso de la biblioteca:**
  - la pista «Suele durar unas 30 h, y tú llevas más», que propone «Lo terminé»;
  - los juegos sin final (Dead by Daylight, Fortnite…) van al fondo, como apuntamos el día 4.
- [x] Tests, propuestas, PDF y esta bitácora.
- [x] Comprobarlo en el servidor, con la copia de tu base de datos.

**Qué cambia para ti:**
- **Ficha del juego:**
  - **Ficha técnica**, debajo de la sinopsis. El estudio, los modos, la perspectiva y los temas llevan al catálogo filtrado por ellos.
  - **Nota de los jugadores de IGDB**, junto a la de la crítica.
  - **«Cuánto dura»** en la columna de la carátula: normal, con prisa y al 100 %. Si lo estás jugando, «Te faltan unas 4 h». Con menos de 5 respuestas dice «dato orientativo», y por encima de 200 h, que no tiene un final claro.
  - **«Ediciones y versiones»**, antes de «Dónde comprarlo»: las ediciones, los remakes, remasters y ports, las expansiones y DLC y los recopilatorios, con portada. Lo que no está en el catálogo lleva a buscarlo para traerlo de IGDB.
  - **«Juegos parecidos»**, después de los precios, primero los de tu Pendiente.
  - **En la cabecera**, de qué juego es («Remake de Resident Evil 4») y su estado si conviene saberlo («Ya no se vende», «Servidores cerrados»).
- **Ediciones juntadas:** la ficha de «Batman: Arkham Asylum - Game of the Year Edition» lleva a la de Batman: Arkham Asylum. Tus horas, notas, reseñas y listas de la edición cuentan en el juego.
- **Catálogo:**
  - por defecto, sin ediciones sueltas ni DLC; con «Tipo» se ven, y si tu búsqueda tiene alguno escondido, lo dice;
  - tipo, género, plataforma y orden a la vista, y el resto en «Más filtros»;
  - los filtros puestos salen como etiquetas que se quitan una a una;
  - la dirección se puede compartir: `/juegos/?duracion=corta&tema=terror`.
- **Repaso de la biblioteca:** dice cuánto suele durar cada juego y propone «Lo terminé» si llevas más. Los juegos sin final van al fondo y no se proponen.
- **Cada noche** (`nightly`): `fill_game_fields` pone al día las fichas de hace más de 30 días y `merge_editions` junta las ediciones nuevas.

**Qué he tocado:**
- **`games/models.py`:**
  - tipo, estado, edición de (`parent`), nota de IGDB, duración y `details` en `Game`;
  - modos, temas, perspectivas y compañías como tablas (`GameMode`, `Theme`, `Perspective`, `Company`).
  - Migración `games.0010`.
- **`games/igdb.py`:**
  - los campos nuevos en todas las consultas;
  - el filtro que deja fuera DLC, packs y actualizaciones;
  - `games_with_details`, `time_to_beat`, `classify` y `link_editions`.
- **Nuevos:**
  - `games/details.py`: la pasada y lo que enseña la ficha;
  - `games/editions.py`: juntar ediciones;
  - `games/filters.py`: los filtros del catálogo;
  - los comandos `fill_game_fields` y `merge_editions`.
- **Vistas y plantillas:**
  - `games/views.py`, con el trozo nuevo `games:details`, que la ficha pide con HTMX si un juego nunca ha tenido ficha técnica;
  - `_sheet.html`, `_duration.html`, `_versions.html`, `_similar.html` y `_details.html`;
  - `detail.html`, `_hero.html`, `_card.html` y `catalog.html`;
  - `static/css/app.css`.
- **Importaciones:**
  - `accounts/matching.py` y `accounts/steam.py`: una edición cuenta como su juego;
  - `lists/views/editar_lista.py`: al buscar juegos para una lista no salen ediciones.
- **Repaso:** `library/backlog.py`.
- **Admin:** «es una edición de», para los recopilatorios que IGDB no enlaza.
- **Documentación:**
  - `docs/pantallas.json` y el README;
  - cinco propuestas actualizadas y dos nuevas: «Páginas de saga con tu progreso» y «Ampliar el catálogo por tandas».

**Cómo lo he comprobado:**
- **Tests:** 24 nuevos, 562 en total. Todos pasan sin claves y con claves falsas detrás de un proxy muerto.
- **La pasada de verdad, en la copia de tu base de datos:**
  - los 2235 juegos tienen tipo y 1530, duración;
  - 899 tienen ediciones en IGDB, 1896 tienen PEGI y hay 1465 compañías.
- **`merge_editions` en la copia:** ha juntado 5 ediciones. Por ejemplo, `tester_steam` tenía «Battlefield 1» (383 min de PlayStation) y «Battlefield 1: Revolution» (278 min de Steam) como dos juegos, y ahora es uno con las dos horas.
- **En el navegador** (servidor del puerto 8004, sin sesión):
  - la ficha de The Witcher 3 enseña su ficha técnica, «Cuánto dura» (71 h según 42 jugadores), sus 3 ediciones, sus 3 expansiones y 5 DLC y los juegos parecidos;
  - la de la GOTY de Batman lleva a Batman: Arkham Asylum con la edición marcada;
  - en el catálogo, «Remakes» deja los 5 de Resident Evil, y «terror de menos de 10 h» da 84 juegos sin recargar la página;
  - a 375 px nada se sale por los lados;
  - sin errores en la consola ni en el servidor.
- **Sin ver en el navegador:** «Te faltan unas X h» y la carga con HTMX de una ficha técnica que nunca se había pedido, porque necesitan sesión o un juego sin pasar. Los comprueban los tests.

**Qué falta:**
1. Me pediste fusionarla al terminar, así que está en `main` sin que la hayas revisado: mírala cuando quieras.
2. En tu base de datos de verdad, justo después de fusionar y con una copia de seguridad antes:
   - la migración `games.0010`;
   - `fill_game_fields`, menos de un minuto;
   - `merge_editions --simular`, para que veas qué juntaría; lo junto de verdad cuando me digas.
3. Lo que se queda para otro día:
   - las páginas de saga con tu progreso;
   - ampliar el catálogo, que con 2235 juegos deja cortos los filtros finos;
   - la duración propia de la comunidad.

   Lo he apuntado en sus propuestas.

---

## 04-08/11/2026 · Siete remates rápidos (hecho, sin subir)

**Rama:** `remates-rapidos`, encima de `repaso-de-la-biblioteca`. Lleva un commit por cada remate, para que se puedan revisar por separado.

**Por qué:** me preguntaste qué se podía hacer rápido y elegiste las siete. Todas son propuestas que ya estaban abiertas y no dependen de nada tuyo.

**Plan:**
- [x] 1. **Arreglos pequeños que salieron al medir:**
  - **«Mejor precio ahora»** sale ya en todas las fichas con precios, también cuando la tienda más barata está a la vista; antes solo salía si estaba plegada. El mínimo histórico va siempre a su lado.
  - **La vuelta de «Lo quiero»** comprueba que la página es de esta web, como las demás: `/\otra.web` ya no cuela.
  - **Las reseñas y los comentarios** traen ya la cuenta de Xbox de cada autor, en vez de una consulta por autor.
  - **Fuera 6 clases de CSS sin usar.** La séptima, `shop-logo`, sí se usa: la llevan los iconos de las tiendas.
  - 3 tests nuevos.
- [x] 2. **Páginas de error propias**, hecho en la rama `remates-rapidos`:
  - `404.html`, con la cabecera de siempre y un buscador;
  - `500.html`, que va sola, sin la base de datos, por si es eso lo que falla;
  - formulario caducado (`config.views.csrf_failure`), con tu sesión y un botón para volver atrás sin perder lo escrito.

  Está en `docs/pantallas.json` como «Páginas de error». Tiene 3 tests nuevos (`config/tests.py`). En el servidor de revisión no se ven: con el modo de desarrollo, Django pone sus propias páginas de error.
- [x] 3. **Aviso a quien abre una tier list a la comunidad** cuando alguien hace su versión. Llega uno por persona, con el enlace a su versión, y se apaga en Editar perfil › Avisos › «Tus listas», un grupo nuevo. Migración `accounts.0024`.
- [x] 4. **Listas colaborativas:**
  - **solo se puede invitar a quien te sigue:** si no, sale «leo no te sigue: solo puedes invitar a gente que te sigue». Las sugerencias del campo pasan a ser tus seguidores aún no invitados.
  - **aviso al dueño** cuando alguien acepta, una vez por persona, en el grupo «Tus listas». Migración `accounts.0025`.
  - **un nivel con un color raro** coge el de serie de su puesto, en vez de romper la tier list.
- [x] 5. **Editar perfil avisa al salir con cambios sin guardar.**
  - En cuanto tocas algo salen «Cambios sin guardar» y «Descartar» en la barra de guardar.
  - Si cierras, recargas o sales sin guardar, el navegador pregunta. Al guardar, no.
  - «Descartar» vuelve a lo guardado.
  - Es solo JavaScript: el test comprueba el HTML, pero el aviso del navegador hay que verlo en el servidor.
- [x] 6. **Inicio en el móvil**, solo con CSS:
  - tu tarjeta de resumen ya no sale (la barra de abajo lleva a tu perfil);
  - la caja de publicar se queda en su campo de texto hasta que la tocas, y no se pliega mientras tenga texto o imágenes.

  Así el feed sale antes. En escritorio no cambia nada.
- [x] 7. **Un test que obliga a que cada función diga sus pantallas** con nombres del catálogo (`config/tests.py`).
  - Usa las mismas reglas que el PDF y hoy pasa con todo el proyecto.
  - Lo he probado con un archivo que incumplía las dos cosas: falló como debía, y luego lo borré.

**Cómo lo he comprobado:**
- **Tests:** 10 nuevos, 538 en total. Todos pasan sin claves y con claves falsas detrás de un proxy muerto.
- **En el navegador, con la cuenta `tester`** (en la copia de tu base de datos):
  - **Editar perfil:**
    - al cargar no hay aviso, y el navegador no pregunta al salir;
    - al escribir en el nombre salen «Cambios sin guardar» (en violeta) y «Descartar», y el navegador ya preguntaría;
    - «Descartar» recarga la página y el nombre vuelve al guardado.
  - **Inicio a 375 px:**
    - no sale tu columna;
    - la caja de publicar va plegada y se abre al tocarla;
    - las pestañas del feed y la primera actividad caben en la primera pantalla.

    El campo plegado cortaba su texto de ayuda a media línea y enseñaba una barra de desplazamiento: lo he dejado en dos líneas enteras.
  - Sin errores en la consola ni en el servidor.
- **Migraciones:** `accounts.0024` y `accounts.0025` están aplicadas en la copia.
- **Los dos PDF están regenerados.**
- **Sin ver en el navegador:**
  - **las páginas de error:** con el modo de desarrollo, Django pone las suyas; solo las comprueban los tests;
  - **las listas colaborativas y el aviso de versiones de tier list:** también solo con tests.

---

## 04/11/2026 · Repaso de la biblioteca y valor del pendiente (hecho, sin subir)

**Rama:** `repaso-de-la-biblioteca`, encima de `retos-fase-2`.

**Por qué ahora:** «Completado» está casi vacío porque las importaciones nunca completan nada. `tester_steam` tiene 1 juego completado y 263 en «Jugado» sin saber si los terminó. Con eso, los retos de terminar juegos y las estadísticas se quedan cortos. El repaso es lo que llena esa columna.

**Plan:**
- [x] **Página «Repasa tu biblioteca»** (`/biblioteca/repasar/`). Va de juego en juego con botones grandes: «Lo terminé», «Lo dejé», «Sigo jugando», «Al pendiente» y «Saltar». Al contestar sale el siguiente sin recargar.
  - Primero, los que están en «Jugando» y llevan 6 meses sin tocarse.
  - Después, los de «Jugado» que nunca has repasado: arriba los que tienen todos sus logros y, detrás, los de más horas.
- [x] **Pistas** con lo que ya sabe NextPlay: horas, última partida y logros. Si tienes todos los logros (o el platino), propone «Lo terminé».
- [x] **Fecha aproximada** al marcarlo terminado: la de tu último logro o la de tu última partida, no la de hoy, para que no cuente en un reto que no le toca. La tarjeta dice qué fecha va a apuntar.
- [x] **Lo repasado no se vuelve a preguntar** (`LibraryEntry.reviewed_at`). En el feed sale una sola entrada agrupada («ordenó su biblioteca: 12 terminados y 3 abandonados»), no una por juego.
- [x] **Resumen al terminar**, un aviso en tu perfil cuando hay juegos por repasar, y el valor del pendiente en la pestaña Pendiente del perfil («al precio de hoy, 34 de los 120 juegos del pendiente suman 447,91 €»).
- [x] **Extra del plan de la propuesta:** al acabar, un botón «Ponte un reto para este año». Abre Nuevo reto con 12 juegos del 1 de enero al 31 de diciembre, solo para ti.
- [x] Tests (6 nuevos, 528 en total, todos bien sin claves y con claves falsas), propuestas y PDF, y esta bitácora.
- [x] Commit en su rama y comprobarlo en el servidor.

**Cómo lo he comprobado:**
- **Tests:** 6 nuevos, 528 en total. Todos pasan sin claves y con claves falsas detrás de un proxy muerto.
- **En el navegador, con la cuenta de prueba `tester`** (en la copia de tu base de datos):
  - el perfil avisa de «121 en «Jugado» sin saber si los terminaste»;
  - la pestaña Pendiente dice «Al precio de hoy, 119 de los 127 juegos del pendiente suman 1202,52 €»;
  - la primera tarjeta del repaso es Dead by Daylight, con sus horas y su última partida;
  - al pulsar «Saltar», la siguiente llega sin recargar la página y el contador pasa a «Quedan 120 · hoy llevas 1».

**A mejorar:** como ordena por horas, salen primero los juegos online sin final (Dead by Daylight, Rainbow Six Siege…). Para mandarlos al fondo hacen falta los modos de juego de IGDB, que aún no se guardan; es la pieza «Una pasada a IGDB» de la revisión. Mientras tanto, «Saltar» o «Sigo jugando» los quitan de en medio.

**Qué he tocado:**
- **`library/backlog.py` (nuevo):** la cola (`queue`), las pistas (`hint`), las respuestas (`answer`), lo repasado hoy (`today_summary`) y lo que cuesta el pendiente (`value`).
- **La pantalla nueva:** `library/views/repasar_la_biblioteca.py` y las plantillas `library/tidy.html` y `library/_tidy_card.html`. «Repasar la biblioteca» está ya en `docs/pantallas.json`.
- **Migraciones:**
  - `LibraryEntry.reviewed_at`, migración `library.0014`;
  - `Activity.Kind.TIDY`, migración `social.0009`, en `social/activity.py`, `social/views.py` y `templates/social/_activity.html`.
- **Perfil:** el aviso y el valor del pendiente, en `accounts/views/perfil.py` y `templates/accounts/profile.html`.
- **Reto del año:** `challenges/views.py`.
- **Propuesta renombrada:** ahora se llama «Valor del pendiente y repaso de la biblioteca», también en la revisión, como sugería ésta.

**Lo que dejo para después:** la pista «llevas 40 h y suele durar 30». Hace falta guardar antes la duración de cada juego, que es otra propuesta: «Duración estimada de los juegos», donde lo he apuntado.

---

## 04/11/2026 · Retos, fase 2 (hecho, sin subir)

**Rama:** `retos-fase-2`, encima de `avisos-en-un-solo-sitio`.

**Por qué ahora:** los retos son de lo primero que pediste y llevaban desde el 26/10 en la fase 1. No tenían sección en el perfil, no salían en el feed y no avisaban de nada. Con los avisos ya en un solo sitio, sus avisos salen casi solos.

**Plan:**
- [x] **Sección «Retos» en el escaparate del perfil**, con las insignias de los conseguidos y los que lleva en marcha con su barra. Los privados no se enseñan a los demás.
- [x] **El feed y la pestaña Actividad** dicen quién crea un reto, quién se une y quién lo consigue. Si lo deja, se borra. De los privados no ve nada nadie más.
- [x] **Avisos de los retos**, con su interruptor en Editar perfil › Avisos:
  - «¡Has conseguido…!»;
  - al creador, cuando alguien se une;
  - «te quedan 3 días»;
  - el puesto final cuando termina.
- [x] **Cierre de cada noche (`close_challenges`):**
  - da margen de 7 días a las sincronizaciones que llegan tarde;
  - avisa del final y apunta que el reto está cerrado.
- [x] Tests (7 nuevos, 522 en total, todos bien sin claves y con claves falsas), propuestas y PDF, y esta bitácora.
- [x] Commit en su rama y comprobarlo en el servidor.

**Cómo lo he comprobado:**
- **Tests:** 7 nuevos, 522 en total. Todos pasan sin claves y con claves falsas detrás de un proxy muerto.
- **Migraciones:** aplicadas en la copia de tu base de datos. El repaso de cada noche no hizo nada allí, como debía: ningún reto de `tester` estaba conseguido ni le quedaban 3 días.
- **En el navegador, con la cuenta de prueba `tester`** (en la copia):
  - la sección «Retos» del perfil salía con sus tres retos en marcha, el que antes acaba primero;
  - en «Pásate «Mis imprescindibles»» le faltaba GTA V; al marcarlo como Completado en su ficha, el reto pasó a las insignias, llegó el aviso «¡Has conseguido el reto…!» y el feed dice «consiguió el reto…» con el trofeo dorado;
  - en Editar perfil salen el grupo «Retos» de los avisos y la sección «Retos» del escaparate;
  - sin errores en la consola ni en el servidor.
- **Ojo, la copia ha cambiado:** `tester` tiene ahora GTA V como Completado. Tu base de datos de verdad no se ha tocado.
- **Si ves la insignia sin estilo,** recarga con Ctrl+F5: el navegador tenía guardada la hoja de estilos anterior.

**Qué cambia para ti:**
- **Perfil:** nueva sección «Retos» en el escaparate, que puedes mover u ocultar como las demás. Tiene dos partes:
  - **Insignias:** los retos conseguidos, con un trofeo dorado.
  - **En marcha:** hasta 3 retos con su barra de progreso. A los demás les dice «Lleva 2 de 5»; a ti, «Llevas».
- **Feed y Actividad:** «creó el reto…», «se unió al reto…» y «consiguió el reto…», con un trofeo donde los juegos llevan su portada.
  - Si te unes y lo consigues seguido, sale una sola entrada.
  - Si dejas el reto, se borra.
- **Avisos** (se apagan en Editar perfil › Avisos › Retos):
  - «¡Has conseguido el reto…!»;
  - a quien crea un reto, cada persona que se une (una vez por persona);
  - «Te quedan 3 días para el reto…: llevas 1 de 2 juegos»;
  - cuando termina: «lo conseguiste y quedaste 1.º de 2».
- **Página del reto:** ya no guarda nada al abrirla. Si has llegado al objetivo, sale «¡Lo has conseguido!» aunque aún no esté apuntado.
- **Retos que terminan:** siguen contando 7 días más, porque las sincronizaciones de PlayStation y Xbox son a mano. Pasado ese margen, se cierran.

**Qué he tocado:**
- **`challenges/progress.py`:**
  - `mark_completed` devuelve a quién ha apuntado y le avisa;
  - `check` mira también los retos que terminaron hace menos de 7 días;
  - `showcase` prepara la sección del perfil.
- **`challenges/closing.py` (nuevo)** y el comando `close_challenges`, que `nightly` lanza justo después de sincronizar.
- **`Challenge.closed_at`:** migración `challenges.0002`.
- **`Activity.Kind.CHALLENGE`** con el reto en `Activity.challenge` (migración `social.0008`), en `social/activity.py`, `social/views.py` y `templates/social/_activity.html`.
- **Cuatro tipos de aviso nuevos**, con sus reglas y su grupo en `accounts/notices.py` (migración `accounts.0023`).
- **`accounts/views/perfil.py`**, `templates/accounts/profile.html`, `templates/challenges/_card.html`, `templates/challenges/detail.html` y `static/css/app.css`.

**Lo que queda fuera** (es de otras propuestas):
- **Proponer «¿Lo has terminado?»** al llegar a las horas o al logro del final. Va con el repaso de la biblioteca.
- **Juntar en uno los avisos** de varias personas que se unen («Ana y 3 más…»).
- **El repaso de cada noche no corre** hasta que programes `nightly` al desplegar. Mientras tanto, los retos conseguidos se apuntan al momento: al completar un juego, al importar y al unirse.

**Qué falta:**
1. Que lo revises en el servidor del puerto 8001, junto con los avisos, que van debajo.
2. Cuando me digas «súbelas», abro un PR por rama y se fusionan en orden: primero los avisos y después los retos.
3. Después, aplicar a tu base de datos de verdad las cuatro migraciones de las dos ramas. Antes haré una copia de seguridad.

---

## 04/11/2026 · Avisos en un solo sitio, con preferencias por tipo (hecho, sin subir)

**Rama:** `avisos-en-un-solo-sitio`, commit `d025255`, solo en local.

**Qué cambia para ti:**
- **Nueva sección «Avisos» en Editar perfil**, separada de «Privacidad», con seis interruptores:
  - bajadas de precio, con el correo aparte;
  - nuevos seguidores;
  - «me gusta»;
  - comentarios;
  - invitaciones a editar listas;
  - reseñas que se han quedado atrás.

  Lo que no toques sigue encendido. Las denuncias, que solo llegan a los administradores, no se pueden apagar.
- **La lista de deseos dice por dónde te avisamos** de las bajadas de precio, o cómo encender los avisos si están apagados.
- **Enlaces a la sección:** la página de Avisos y el correo de precios llevan a ella.
- **Tocar los avisos no recarga la vista previa del perfil.**

**Qué he tocado:**
- **`accounts/notices.py` (nuevo):** `notify()` es ya el único sitio donde se crean avisos. Decide igual para todos:
  - nadie recibe avisos de lo que hace él mismo;
  - cada tipo tiene su regla para no repetirse, en la tabla `REPEAT`;
  - solo llega lo que cada uno deja encendido, según los grupos de `GROUPS`.
- **Las 10 llamadas que había repartidas** pasan por ahí y se portan igual. Estaban en `library/wishlist.py`, `library/reminders.py`, `library/views/pagina_de_la_resena.py`, `library/views/comentarios_y_denuncias.py`, `lists/views/colaboradores.py`, `lists/views/pagina_de_la_lista.py`, `social/views.py` y `social/models.py`.
- **Una diferencia pequeña:** los «me gusta» y comentarios de las reseñas guardan ya quién los ha hecho, como los demás. Antes, si alguien cambiaba su nombre visible, su «me gusta» podía volver a avisar.
- **`Profile.notification_prefs`** (un JSON por grupos) sustituye a `price_alert_emails`.
  - La migración `accounts.0022` deja el correo apagado a quien lo tenía apagado.
  - Se puede deshacer.
- **Formulario y plantillas:** `accounts/forms.py` (los interruptores), `templates/accounts/edit_profile.html`, `templates/library/wishlist.html`, `templates/accounts/notifications.html` y `static/css/app.css`.

**Cómo lo he comprobado:**
- **Tests:** 14 nuevos, 515 en total. Todos pasan sin claves y con claves falsas detrás de un proxy muerto.
- **Migración, en una copia de tu base de datos:** a una cuenta con el correo apagado le queda apagado. La he deshecho y vuelto a aplicar.
- **En el navegador, con la cuenta de prueba `tester`** (en la copia):
  - apagar «Me gusta» y el correo de precios se guarda y se ve al volver a la página;
  - la lista de deseos dice «Te avisamos en la campana…»;
  - tocar los avisos no pide la vista previa, y tocar «Privacidad» sí, como antes;
  - sin errores en la consola ni en el servidor.

  Después he dejado a `tester` con todo encendido.

**Qué falta:**
1. Que lo revises en el servidor del puerto 8001.
2. Después: subir la rama, abrir el PR y fusionarlo.
3. Aplicar la migración `accounts.0022` a tu base de datos de verdad. Antes haré una copia de seguridad.
