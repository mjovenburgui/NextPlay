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

Todo está en `main` menos tres ramas de hoy, una encima de otra, que esperan tu revisión: `playnite-y-csv`, `formularios-que-avisan` y `cabecera-automatica`. Las tres se ven juntas en el servidor de revisión (http://localhost:8001/, con una copia de tu base de datos) y cada una lleva su guion para comprobarla. Las siete ramas de `sesiones-segunda-cuenta` a `que-jugar-despues` entraron en `main` el 11/11 por la noche, sin PR, junto con un commit («Cambios de Claude para NextPlay», `5e4bd89`) que traía a medias la importación desde un archivo. A tu base de datos de verdad le faltaban sus seis migraciones: se las apliqué el 12/11, con una copia de seguridad antes. Las de las ramas anteriores ya estaban (comprobado el 08/11 y el 09/11). Si un servidor de `main` da error de columnas que no existen, es que a esa base de datos le faltan migraciones: `manage.py migrate`.

| Rama | Qué lleva | Estado |
|---|---|---|
| `cabecera-automatica` | Cabecera «Lo que estoy jugando», que sigue sola al juego al que juegas, y el color del perfil sacado de la cabecera | Commit local, sin subir, encima de `formularios-que-avisan`. Trae las migraciones `accounts.0030` y `library.0018`. |
| `formularios-que-avisan` | Los formularios que recargan la página avisan mientras trabajan: el botón gira, dice qué hace («Importando tus juegos…») y no se puede pulsar dos veces | Commit local `ee08633`, sin subir, encima de `playnite-y-csv`. Sin migraciones. |
| `playnite-y-csv` | Importar desde un archivo: tus juegos y horas de Epic, GOG, EA, Ubisoft, Battle.net y Amazon con Playnite, y el CSV de otros trackers | Commit local `0c4431f`, sin subir, encima de `main`: espera tu revisión. Trae `accounts.0029` y `library.0017`, que solo cambian las opciones de plataforma y no tocan las tablas. |
| `que-jugar-despues` | Qué jugar después: de tu pendiente y del catálogo, según cómo juegas, con su porqué | En `main` desde el 11/11, sin PR. Su migración `accounts.0028` está aplicada en tu base de datos. |
| `estadisticas-y-resumen` | Pestaña Estadísticas del perfil y Resumen del año con su imagen para compartir | En `main` desde el 11/11, sin PR. Sin migraciones. |
| `horas-por-plataforma` | Horas totales y última partida en la base de datos, y cualquier plataforma nueva sin migraciones | En `main` desde el 11/11, sin PR. Su migración `library.0016`, que rellena los totales, está aplicada. |
| `logros-fase-2` | Logros: trofeos de PlayStation uno a uno, logros en el feed y «Jugados en» el año pasado | En `main` desde el 11/11, sin PR. Su migración `social.0010` está aplicada. |
| `gente-que-sigues` | La gente que sigues en la ficha y en los perfiles: su recuadro y sus reseñas delante, «En común», «A quién seguir» y un aviso nuevo | En `main` desde el 11/11, sin PR. Su migración `accounts.0027` está aplicada. |
| `diario-de-partidas` | Diario de partidas: pestaña Diario del perfil con el calendario de cada año, línea en la ficha y juegos en «Jugando» sin jugar en el repaso | En `main` desde el 11/11, sin PR. Sus migraciones `library.0015` y `accounts.0026` están aplicadas. |
| `sesiones-segunda-cuenta` | Las cuentas de Steam recién vinculadas ya no cuentan como sesiones de juego | En `main` desde el 11/11, sin PR. Sin migraciones. |
| `ficha-ligera-y-dlc` | Ficha del juego más ligera: tres columnas, lo secundario plegado y cada DLC con su ficha y su precio | Fusionada con `main`: [PR #39](https://github.com/mjovenburgui/NextPlay/pull/39). Sin migraciones; los DLC llegan a tu base de datos con `import_addons`. |
| `pasada-a-igdb` | Una pasada a IGDB: ficha técnica, duración, ediciones agrupadas, sin DLC en las búsquedas y catálogo con filtros | Fusionada con `main`: [PR #38](https://github.com/mjovenburgui/NextPlay/pull/38). Trae la migración `games.0010`. |
| `avisos-en-un-solo-sitio` | Avisos en un solo sitio, con preferencias por tipo | Fusionada con `main`: [PR #34](https://github.com/mjovenburgui/NextPlay/pull/34). |
| `retos-fase-2` | Retos: insignias, sección en el perfil, feed, avisos y cierre cada noche | Fusionada con `main`: [PR #35](https://github.com/mjovenburgui/NextPlay/pull/35). |
| `repaso-de-la-biblioteca` | Repaso de la biblioteca y valor del pendiente | Fusionada con `main`: [PR #36](https://github.com/mjovenburgui/NextPlay/pull/36). |
| `remates-rapidos` | Siete remates rápidos, un commit por cada uno, y los PDF al día | Fusionada con `main`: [PR #37](https://github.com/mjovenburgui/NextPlay/pull/37). |

**Migraciones de las cuatro ramas del 08/11** (aplicadas en tu base de datos ese día):
- `accounts.0022` a `accounts.0025`;
- `challenges.0002`;
- `social.0008` y `social.0009`;
- `library.0014`.

---

## 12/11/2026 · Cabecera y color automáticos según lo que juegas

**Rama:** `cabecera-automatica`, encima de `formularios-que-avisan`. Commit local, sin subir.

**Por qué:** es de prioridad alta y del perfil, que es de lo primero que pediste («10000 veces más modificable»). Hasta ahora todo en el perfil era a mano y se quedaba viejo.

**Qué cambia para ti:**
- **Una cabecera nueva en Editar perfil › Apariencia: «Lo que estoy jugando».** Lleva el arte del juego al que estás jugando y cambia sola:
  - el juego es el de «Jugando» con lo último que ha pasado con él: su última partida importada o, si es después, cuándo lo pusiste en «Jugando» desde su ficha. Ponerlo a mano cuenta como jugarlo en ese momento, aunque su última partida importada sea de hace años;
  - si no tienes nada en «Jugando», lo último que has jugado del último mes; si tampoco, se queda la cabecera que tuvieras;
  - la imagen es el mejor arte de IGDB, el mismo que sale en la cabecera de su ficha; si no tiene ninguno, su portada desenfocada, como al elegir un juego a mano.
- **Cuándo cambia:** al sincronizar tus juegos (también la sincronización de cada noche), al pasar un juego a «Jugando» desde su ficha (ese sale enseguida, aunque otro tenga una partida más reciente) y al guardar Editar perfil. Nunca al abrir tu perfil, que así no espera a nadie.
- **Editar perfil te dice qué juego saldría** («Ahora sale Fortnite, lo último que has jugado de lo que tienes en «Jugando»») y el encuadre enseña su imagen.
- **«Sacarlo de la cabecera», en Color del perfil:**
  - el color del perfil sale de la imagen de la cabecera: la tuya subida, la de un juego o la automática;
  - con «Lo que estoy jugando», cambia con cada juego;
  - se aclara y se aviva lo justo para que se lea sobre el fondo oscuro, y nunca coge grises, negros ni blancos. Si la imagen es toda apagada, como el arte de The Witcher 3, coge su tono aunque sea leve (ahí, un azul);
  - si eliges un color a mano, la casilla se quita sola;
  - para quedarte con el color de una imagen y que ya no cambie: márcala, guarda y desmárcala.
- Con `tester`, en el servidor de revisión:
  - al elegirla, la cabecera ha pasado a Fortnite, que tiene en «Jugando» desde la importación de ejemplo, y el color del perfil, del cian a un naranja (#cb7851) sacado de su arte;
  - al poner Cyberpunk 2077 en «Jugando» desde su ficha, la cabecera ha pasado a su arte y el color, a un ámbar (#d48c44), con el texto encima en oscuro porque es un color claro.

**Cómo comprobarlo tú (guion, 5 minutos, en el servidor de revisión http://localhost:8001/):**
1. **Entra con `tester_steam`** y ve a Editar perfil › Apariencia › Cabecera. Hay una opción nueva, «Lo que estoy jugando». Al elegirla, debajo dice qué juego saldría ahora.
2. **En Color del perfil**, marca «Sacarlo de la cabecera».
3. **Pulsa «Guardar cambios».** En tu perfil, la cabecera es el arte de ese juego y el color de los botones y pestañas sale de ella.
4. **Haz que cambie:** abre la ficha de otro juego de tu biblioteca y ponlo en «Jugando». Vuelve a tu perfil: la cabecera ya es la suya, y el color también.
5. **Elige un color a mano** en Editar perfil: la casilla «Sacarlo de la cabecera» se desmarca sola.
6. **Con una imagen tuya:** elige «Una imagen mía», sube una, marca «Sacarlo de la cabecera» y guarda. El color sale de tu imagen.

**Qué he tocado:**
- `accounts/banner.py`, nuevo:
  - qué juego sale (`playing_game`) y con qué imagen;
  - ponerla al día (`refresh_playing`, `refresh_user`) y la vista previa;
  - el color de la cabecera (`banner_color`, `dominant_color`, con Pillow).
- `accounts/models.py`: `Profile.BannerMode.PLAYING` y `Profile.accent_from_banner` (migración `accounts.0030`); `banner_url` y `banner_blurred` valen también para la automática.
- `library/models.py`: `LibraryEntry.marked_playing_at`, cuándo pusiste un juego en «Jugando» desde su ficha (migración `library.0018`). Lo apunta `set_status` y se vacía si cambia a otro estado.
- `games/media.py`: `best_art`, la mejor imagen de un juego para una cabecera, que ahora comparten la ficha y el perfil.
- Dónde se pone al día:
  - `accounts/matching.py`, al importar, y `accounts/csv_import.py` otra vez al final, ya con los estados del archivo: un juego que Playnite da por terminado no se queda de cabecera;
  - `library/views/ficha_del_juego.py`, al cambiar el estado;
  - `accounts/forms.py`, al guardar y en la vista previa.
- `accounts/views/editar_perfil.py` y `templates/accounts/edit_profile.html`: la opción, el juego que saldría, su encuadre y la casilla del color.
- Tests, `docs/pantallas.json`, la propuesta (hecha) y los dos PDF.
- De paso, en Importar desde un archivo: «Se importará 1 juego», en singular (decía «Se importarán 1 juego»).

**Cómo lo he comprobado:**
- 7 tests nuevos:
  - qué juego sale, también el que acabas de poner en «Jugando»;
  - el juego con su imagen, su encuadre y su color, y que con otra cabecera no se toca;
  - un juego sin arte sale con su portada desenfocada;
  - el cambio de estado y la importación la mueven;
  - el color: sin grises, aclarado para leerse, y una sola petición a IGDB por imagen;
  - Editar perfil la ofrece, la guarda y la enseña en la vista previa.
- En el servidor de revisión, con `tester`:
  - elegida la cabecera y la casilla del color, guardado: la cabecera es el arte de Fortnite, que IGDB ha mandado al guardar, y el color, #cb7851;
  - la prueba sacó dos fallos, ya arreglados:
    - **The Witcher 3 puesto en «Jugando» desde su ficha** salía en la cabecera, pero al guardar Editar perfil volvía Fortnite: su partida importada era más antigua y ponerlo a mano no contaba. Para eso está ahora `marked_playing_at`. Repetido con Cyberpunk 2077: se queda.
    - **El color de The Witcher 3** no cambiaba: su arte es casi todo gris y negro y no encontraba ningún color vivo, así que se quedaba el anterior. Ahora coge el tono más presente aunque sea leve: un azul (#599dc0).
  - una importación de un CSV de Playnite en inglés («Time Played», «Last Played» con PM, «Beaten», «User Score»): se reconoce todo solo y se importa pulsando el botón nuevo que gira.
- La batería completa, sin claves y con claves falsas y un proxy muerto: 609 tests en cada pasada, todos bien.

**Qué falta:**
- Que lo revises. Al fusionar, tu base de datos necesita `migrate` (`accounts.0030` y `library.0018`).
- Lo que la propuesta deja para más adelante: un escaparate que cambie solo («Este mes he jugado a…»), una vitrina de logros elegidos y un aspecto guardado para cada juego.

---

## 12/11/2026 · Formularios que avisan mientras trabajan

**Rama:** `formularios-que-avisan`, encima de `playnite-y-csv`. Commit local `ee08633`, sin subir.

**Por qué esta y no «Tareas en segundo plano»**, que era la siguiente que apunté ayer:
- Las tareas en segundo plano solo se notan con un proceso aparte que las ejecute, y eso se decide al desplegar. La propuesta ya dice que no corre prisa mientras NextPlay esté en local.
- Lo que sí se nota ya es su otra mitad: que una importación larga diga que está trabajando y no se envíe dos veces. PlayStation y Xbox se van a quedar siempre en la petición, porque el código de Sony no se puede guardar en una cola.
- «Nintendo Switch con capturas» necesita 10 capturas tuyas de la app para probar la lectura antes de construir nada: la dejo para cuando me las pases.

**Plan:**
- [x] Un script en `base.html`: al enviar un formulario normal (no los de HTMX), el botón pulsado gira y dice lo que está pasando; los botones se desactivan justo después, cuando el navegador ya ha recogido cuál se pulsó, para que «Guardar borrador» no publique la reseña. Al volver atrás, todo vuelve a su sitio.
- [x] `data-busy` en los botones lentos: Steam, PlayStation, Xbox, importar un archivo, Publicar, Guardar cambios, reseñas, listas, tier lists, retos y crear la cuenta.
- [x] Tests, comprobarlo en el servidor de revisión (sobre todo el borrador de una reseña), propuesta, PDF y esta bitácora.

**Qué cambia para ti:**
- **Al pulsar un botón que recarga la página, se nota que está trabajando:** el botón gira y dice lo que hace. Por ejemplo, «Importando tus juegos…» en Steam y PlayStation, «Guardando…» en Editar perfil, «Publicando…» en una reseña o una publicación y «Creando…» en una lista, una tier list o un reto. Los demás botones del formulario se apagan.
- **Ya no se envía dos veces** aunque toques otra vez mientras espera: antes, una publicación con imágenes o una importación se podían duplicar.
- **Si vuelves atrás con el navegador**, el formulario está como lo dejaste, sin el botón girando para siempre.
- **Los botones desactivados se ven apagados**, como «Importar 0 juegos» en la importación desde un archivo. Antes parecían activos.
- Lo que va por HTMX (me gusta, estados, comentarios…) ya tenía su aviso y no cambia.

**Cómo comprobarlo tú (guion, 3 minutos, en el mismo servidor de revisión, http://localhost:8001/):**
1. **Editar perfil:** cambia cualquier cosa y pulsa «Guardar cambios». Durante un instante, el botón gira y dice «Guardando…». En tu PC va muy rápido; se ve mejor en lo lento.
2. **Lo lento:** en Cuentas vinculadas, pulsa «Sincronizar juegos» de Steam con `tester_steam`. Mientras trae tus juegos, el botón dice «Importando tus juegos…» y no se puede volver a pulsar.
3. **La trampa que había:** escribe una reseña y pulsa «Guardar borrador». Tiene que salir «Borrador guardado. Solo lo ves tú.», no publicarse.
4. **Volver atrás:** envía cualquier formulario y, en cuanto cargue la página siguiente, pulsa Atrás en el navegador. El botón ya no gira y se puede pulsar.

**Qué he tocado:**
- `templates/base.html`: el script, junto a los demás de todas las páginas. No va en la vista previa del perfil, como los otros.
- `static/css/app.css`: el giro (`.is-busy`, que se queda quieto si el sistema pide menos movimiento) y los botones desactivados.
- `data-busy` en `accounts/linking/_steam.html`, `_psn.html` y `_xbox.html`, `accounts/edit_profile.html`, `accounts/import_file.html`, `social/_composer.html`, `library/review_form.html`, `lists/form.html`, `edit.html` y `tier_form.html`, `challenges/form.html` y `edit.html`, `registration/signup.html` y `password_reset_form.html`.
- `accounts/import_file.html` ya no lleva su propio aviso al importar: usa el de todas.
- `config/tests.py`, la propuesta (hecha) y los dos PDF.

**Cómo lo he comprobado:**
- 2 tests nuevos: el script va en todas las páginas salvo en la vista previa del perfil, y los botones lentos llevan su `data-busy`.
- La batería completa, en las dos variantes. La primera vez fallaron 4 tests: el comentario del script nombraba pantallas («Editar perfil», «Nueva lista»…) y, como los comentarios de JavaScript salen en el HTML de todas las páginas, esas palabras aparecían donde no debían. Ahora el comentario no nombra ninguna, y pasan los 602 tests, sin claves y con claves falsas y un proxy muerto.
- En el servidor de revisión, con `tester`:
  - en Editar perfil, al enviar: «Guardando…», el giro, `aria-busy`, los botones apagados justo después y un segundo envío cancelado; al simular la vuelta atrás, todo vuelve a como estaba;
  - una reseña guardada con «Guardar borrador» se ha quedado en borrador, sin horas verificadas porque Battlefield 2042 solo tenía las del archivo. Después la he borrado.

**Qué falta:**
- Que lo revises.
- Lo que la propuesta deja para más adelante: evitar duplicados también en el servidor (un token de un solo uso en Publicar) y el progreso de verdad en las importaciones, que llega con las tareas en segundo plano.

---

## 12/11/2026 · Importar desde un archivo: Playnite (Epic, GOG, EA, Ubisoft, Battle.net, Amazon) y CSV de otros trackers

**Rama:** `playnite-y-csv`, encima de `main`. Commit local `0c4431f`, sin subir.

**Por qué:** me dijiste «sigue con lo que haya pendiente». Lo pendiente era esto: el 11/11 se quedó a medias por el límite de uso y, tal cual, entró en `main` esa noche con el resto de ramas.

**Antes de nada, tu base de datos:** con esas ramas en `main`, a tu base de datos le faltaban seis migraciones (`accounts.0026` a `0028`, `library.0015` y `0016` y `social.0010`), y el servidor de `main` habría fallado al abrir casi cualquier página. Se las he aplicado. Antes hice una copia de seguridad, por si quieres volver atrás: `C:\Users\joven\AppData\Local\Temp\claude\d396e005d3d5d5ae\fd38f2ed-e13e-4d47-86a7-7223ea21c063\scratchpad\db-antes-de-migrar-2026-11-12.sqlite3`.

**Qué cambia para ti:**
- **Una tarjeta nueva en Editar perfil › Cuentas vinculadas: «Desde un archivo»**, con la franja dorada, y lo mismo en el modal «Vincular cuentas» de tu perfil. Lleva a la pantalla nueva, **Importar desde un archivo** (`/cuenta/importar-archivo/`).
- **Sirve para las tiendas sin API.** Subes el CSV que exporta Playnite con su complemento Library Exporter Advanced y te traes los juegos y las horas de Epic, GOG, EA, Ubisoft, Battle.net y Amazon. La página explica cómo sacarlo.
- **Las columnas de Playnite se eligen solas**, lo tengas en castellano o en inglés: nombre, tienda, tiempo jugado, última partida, estado y nota. Lo he sacado del código del complemento. Las cabeceras salen en el idioma de Playnite, el tiempo va en segundos, las fechas con el formato de Windows (día o mes delante: se adivina) y la nota sobre 100.
- **Vale también el CSV de cualquier otro tracker.** Eliges qué columna es cada cosa, en qué viene el tiempo (horas, minutos, segundos o 12:30:00) y sobre cuánto va la nota.
- **Antes de importar ves cómo queda:**
  - las 10 primeras filas tal como se van a leer;
  - cuántos juegos van a cada tienda;
  - cuántas filas no se importan y por qué;
  - la tabla se recarga sola al cambiar cualquier desplegable.
- **Lo de Steam, PlayStation y Xbox se salta**, porque se importa desde su cuenta y contaría dos veces. Las filas sin tienda se saltan, salvo que elijas una para ellas.
- **Cada tienda es una plataforma más**, con su logo en las portadas del perfil, en «Tus horas» de la ficha y en «Horas por plataforma» de Estadísticas.
- **Lo que no se encuentra en el catálogo** va a Juegos importados, con la píldora de su tienda, como lo de PlayStation.
- **El estado y la nota del archivo solo se ponen en los juegos que entran nuevos.** Lo que ya tenías se queda como lo dejaste, con las horas de la tienda sumadas.
  - Un juego completado lleva como fecha su última partida, nunca la de hoy, para que no cuente en un reto que no le toca.
  - Un juego sin horas entra como Pendiente, porque Playnite lista también los que nunca has abierto.
- **Las horas de un archivo no son verificadas.** Cuentan en tu biblioteca, tus estadísticas y tus retos, pero no salen en el sello de horas de tus reseñas ni en la media «con horas jugadas» de la ficha: un archivo se puede retocar a mano.
- **Un detalle de paso:** el aviso de juegos sin encontrar ya habla en singular cuando es uno («1 no se encontró en el catálogo: emparéjalo a mano si es un juego»). Antes decía «1 no se encontraron». Vale para todas las importaciones.

**Cómo comprobarlo tú (guion, unos 10 minutos):**
1. **Abre el servidor de revisión:** http://localhost:8001/. Es esta rama con una copia de tu base de datos de hoy, así que lo que hagas ahí no toca la de verdad. Si no responde (la app lo para cuando la sesión se queda quieta), arráncalo así y vuelve a abrirlo:

   ```bash
   C:/Users/joven/Desktop/NextPlay/.venv/Scripts/python.exe C:/Users/joven/Desktop/NextPlay/.claude/worktrees/pending-tasks-19995d/manage.py runserver 8001
   ```
2. **Entra con `tester_steam`**, tu cuenta con tus datos de verdad. Yo lo he probado con `tester` para dejarte la tuya sin tocar.
3. **Ve a Editar perfil › Cuentas vinculadas.** Debajo de «Otras plataformas» está «Desde un archivo». Pulsa «Importar un archivo».
4. **Sube un archivo:**
   - el de ejemplo que he dejado en la rama: `C:\Users\joven\Desktop\NextPlay\.claude\worktrees\pending-tasks-19995d\docs\ejemplos\playnite-ejemplo.csv`, con 14 juegos en el formato de Playnite en castellano;
   - o, si usas Playnite, el tuyo, con los pasos que da la página. Es lo que más me interesa que pruebes: yo no he podido ver un archivo real.
5. **Mira la vista previa** (con el de ejemplo):
   - las seis columnas ya elegidas y «En segundos (Playnite)» en el tiempo;
   - «Se importarán 12 juegos», repartidos así: Epic Games 4, GOG 3, Battle.net 2, y EA app, Ubisoft Connect y Amazon Games 1 cada una;
   - «No se importan: Steam: se importa desde tu cuenta (1) · Sin tienda: elige una para estas filas (1)»;
   - cambia «Filas sin tienda» a «Otras tiendas»: la página se recarga sola y Super Mario Odyssey pasa a contar (13).
6. **Pulsa «Importar 13 juegos».** Vuelves a tu perfil con el resumen, más o menos «… juegos añadidos, … con horas actualizadas y 1 fila sin importar. 1 no se encontró en el catálogo…». A `tester` le salieron 7 añadidos y 5 actualizados.
7. **Comprueba con `tester_steam`:**
   - **The Witcher 3** (lo tienes como Jugado, con 5 estrellas y 131 h: 70 de Steam y 61 de PlayStation): sigue Jugado y con tus 5 estrellas. En «Tus horas» de su ficha sale una fila más, la de GOG, con 100 h y su logo, y el total sube a unas 231 h.
   - **Diablo IV** (Jugando, 80 h): suma 70 h de Battle.net.
   - **Assassin's Creed Valhalla** (Pendiente, sin horas): pasa a Jugado con 30 h de Ubisoft. Es la regla de siempre: un Pendiente con horas pasa a Jugado.
   - **Battlefield 2042** (no lo tienes): entra como Abandonado, con 2 estrellas y 7,5 h de EA app.
   - **Disco Elysium**: entra como Pendiente, sin horas.
   - **Alan Wake 2 y Overwatch 2**: no están en tu catálogo, así que se buscan en IGDB. Con `tester`, Alan Wake 2 salió como «Alan Wake II»: Completado, 5 estrellas y completado el 28/12/2024, la fecha de su última partida. Overwatch 2 se emparejó con «Overwatch», porque en IGDB es otro nombre del mismo juego. Si no te parece bien, se corrige desde su ficha con «¿No es este el juego que jugaste?».
   - **Juegos importados**: «Juego Inventado de Prueba», con la píldora de Epic Games, para emparejarlo a mano o ocultarlo.
   - **Perfil › Estadísticas**: en «Horas por plataforma» salen ya GOG, Epic Games, Battle.net y las demás.
   - **Inicio**: en tu actividad, «importó … juegos de Epic Games» y una entrada por cada tienda con juegos nuevos.
8. **Las horas no verificadas:** escribe una reseña de Battlefield 2042, que solo tiene horas del archivo. No sale el sello de horas verificadas. En una de The Witcher 3 sí, pero con sus 131 h de Steam y PlayStation, sin las 100 de GOG.
9. **Si quieres repetir la prueba desde cero**, dímelo y vuelvo a copiar tu base de datos en la de revisión.

**Qué he tocado:**
- `accounts/csv_import.py` (terminado):
  - leer el CSV: UTF-8 o Latin-1, coma, punto y coma o tabulador, sin las columnas larguísimas como la descripción;
  - adivinar las columnas, la unidad del tiempo, la escala de la nota y si las fechas llevan el mes delante;
  - interpretar cada fila e importarla por tienda.
- `accounts/views/importar_archivo.py` (terminada) y su dirección en `accounts/urls.py`.
- Plantillas: la nueva `accounts/import_file.html`, la nueva `accounts/linking/_file.html` (la tarjeta), `edit_profile.html`, `linking/_dialog.html`, `linking/_platforms.html`, `imported_titles.html` y los logos de las tiendas nuevas en `library/_platform_symbols.html`.
- `accounts/matching.py`: `OPENED_ONLY`. Solo PlayStation y Xbox dan únicamente los juegos abiertos alguna vez; un archivo trae también los nunca jugados. Lo usan también `admin.py`, `games/editions.py` y `rematch_titles`, que ahora busca también los títulos de los archivos.
- `accounts/views/cuentas_vinculadas.py` (el aviso en singular) y `juegos_importados.py` (docstrings).
- Las migraciones `accounts.0029` y `library.0017`. Solo cambian las opciones de plataforma y no tocan las tablas.
- `static/css/app.css`: la página, la tarjeta y las píldoras de cada tienda.
- `docs/ejemplos/playnite-ejemplo.csv`, nuevo; `README.md`, `docs/pantallas.json`, la propuesta (en curso) y los dos PDF.

**Cómo lo he comprobado:**
- 7 tests nuevos:
  - el CSV de Playnite en castellano y en inglés y el de otro tracker con punto y coma y Latin-1;
  - las unidades del tiempo, los estados y las tiendas;
  - las fechas con el día o el mes delante;
  - la importación completa: lo nuevo, lo que ya tenías, lo que no se encuentra y la sesión al volver a importar;
  - que las horas de un archivo no son verificadas;
  - la página paso a paso;
  - los enlaces desde Cuentas vinculadas y el perfil.
- La batería completa, sin claves y con claves falsas y un proxy muerto: 600 tests en cada pasada, todos bien.
- En el servidor de revisión, con `tester`:
  - he subido el CSV de ejemplo, cambiado la tienda de las filas sin tienda e importado;
  - he mirado la ficha de The Witcher 3, Juegos importados, el perfil con los logos nuevos y Estadísticas;
  - a 375 px la página no se sale por los lados y la tabla se desliza dentro de su caja;
  - sin errores en la consola ni en el servidor.

**Qué falta:**
- Que lo revises y lo pruebes con un archivo real de Playnite. Al fusionar, `migrate` (las dos migraciones nuevas, que no tocan nada).
- Lo que sigue en la propuesta: una extensión propia de Playnite que sincronice sola, exportar tu biblioteca en el mismo CSV y plantillas por tracker. Y, con bibliotecas muy grandes, llevar la importación a segundo plano, que es la siguiente propuesta de la lista.

---

## 11/11/2026 · Qué jugar después

**Rama:** `que-jugar-despues`, encima de `estadisticas-y-resumen`. Commit local, sin subir.

**Por qué:** era la siguiente de la lista. Es lo que da nombre a NextPlay, y tu pendiente tiene más de cien juegos.

**Qué cambia para ti:**
- **Pantalla nueva, «Qué jugar después»**, en el menú de Inicio y en la pestaña Pendiente de tu perfil:
  - «De tu pendiente»: por cuál empezar;
  - «Para descubrir»: del catálogo, sin lo que ya tienes ni lo que está en Lo quiero.
- **Cada juego dice por qué**: «Parecido a Dying Light, al que has jugado 112 h» si sale entre los juegos parecidos de IGDB de los tuyos que más pesan, o «Como Destiny 2: aventura y disparos» si comparte con ellos géneros y temas.
- **Tus gustos salen de cómo juegas**, porque notas casi no hay:
  - tus horas respecto a lo que dura cada juego (40 h en uno de 10 h dicen más que en uno de 100 h);
  - tu nota, si la hay;
  - medio punto más si lo terminaste y uno menos si lo dejaste.
- **En tandas de seis**, con «Ver otros». De un mismo juego tuyo solo salen dos parecidos seguidos, para que haya variedad.
- **«No me interesa»** lo quita para siempre.
- Con `tester_steam`, lo que más pesa es Final Fantasy XVI, The Witcher 3 y Apex Legends. Te propone, por ejemplo, State of Decay, Halo Infinite y Saints Row IV, y de tu pendiente, Ark y Final Fantasy XV. Tarda medio segundo.

**Qué he tocado:**
- `library/taste.py`, nuevo: el perfil de gustos, el porqué y las recomendaciones.
- `library/views/que_jugar_despues.py`, nuevo, con sus direcciones en `library/urls.py`.
- `Profile.not_interested` (migración `accounts.0028`).
- Las plantillas nuevas `library/next.html` y `_next_card.html`, el menú de `games/home.html` y la pestaña Pendiente de `profile.html`.
- `static/css/app.css` y `docs/pantallas.json`, con la pantalla nueva.

**Cómo lo he comprobado:**
- 2 tests nuevos: el peso de cada juego y las recomendaciones con su porqué, la variedad y «No me interesa».
- La batería completa en las dos variantes.
- Capturas y tiempos en la copia con `tester_steam`.

**Qué falta:**
- Que lo revises. Al fusionar, tu base de datos necesita `migrate` (`accounts.0028`).
- «¿Y ahora?» al completar un juego, y el filtrado colaborativo cuando haya más gente.

---

## 11/11/2026 · Estadísticas del perfil y resumen del año

**Rama:** `estadisticas-y-resumen`, encima de `horas-por-plataforma`. Commit local, sin subir.

**Por qué:** era la siguiente de la lista y se apoya en lo de antes: las horas totales, el diario y los logros con fecha.

**Qué cambia para ti:**
- **Pestaña Estadísticas en el perfil:**
  - arriba, las cifras: juegos, horas, completados, nota media, reseñas, logros, platinos y juegos al 100 %, con enlaces al resumen de cada año que tiene algo;
  - en barras, las horas por plataforma, cómo está tu biblioteca, los géneros por horas, las notas que pones, de qué época son tus juegos y cuántos jugaste y completaste cada año;
  - lo más jugado.
  - Con `tester_steam`: 13.324 h, 2612 logros y años de 2016 a 2026.
- **Resumen del año** (`/u/tester_steam/resumen/2025/`, pantalla nueva):
  - los juegos que jugaste y terminaste, las horas en sesiones, a lo que más jugaste (por sesiones o, si ese año no hay, por logros), el mes con más actividad, tus géneros, tu logro más raro y tus reseñas;
  - al lado, una imagen vertical para compartir o descargar, con el color de tu perfil.
  - Tu 2025: 62 juegos y 686 logros; a lo que más jugaste, Warframe, Dead by Daylight y Yakuza: Like a Dragon.
- Respeta el perfil privado: los demás reciben un 404.

**Qué he tocado:**
- `library/stats.py`, nuevo: las estadísticas, el resumen del año y la imagen. La imagen usa la DejaVu del servidor, o Segoe o Arial en Windows; sin ninguna, quita las tildes para que no salgan cuadros.
- `accounts/views/resumen_anual.py`, nuevo, con sus dos direcciones en `accounts/urls.py`, y `accounts/views/perfil.py` (la pestaña).
- Las plantillas nuevas `accounts/_stats.html`, `_stat_bars.html` y `year_summary.html`, y `profile.html`.
- `static/css/app.css` y `docs/pantallas.json`, con la pantalla nueva.

**Cómo lo he comprobado:**
- 2 tests nuevos: las cifras y las barras, y el resumen con su imagen, su letra de reserva, los años sin sentido y el perfil privado.
- La batería completa en las dos variantes.
- Capturas en la copia con `tester_steam`.

**Qué falta:**
- Que lo revises.
- Dejar ocultar cifras concretas y compartir la imagen sin descargarla. Esto último va con «Vistas previas al compartir».

---

## 11/11/2026 · Horas por plataforma sin una columna por tienda

**Rama:** `horas-por-plataforma`, encima de `logros-fase-2`. Commit local, sin subir.

**Por qué:** es la base de lo que viene después: importar Playnite (Epic, GOG, EA, Battle.net…) y Nintendo sin añadir dos columnas por tienda cada vez.

**Qué cambia para ti:** a la vista, nada; es por dentro.
- Cada juego de la biblioteca guarda sus horas totales y su última partida de cualquier plataforma en la base de datos, con un índice. La migración `library.0016` las rellena: en la copia, 403 juegos y 13.324 h de `tester_steam`.
- Las horas de una plataforma nueva van en un campo aparte, `extra_hours`. Todo lo que leía las tres columnas de Steam, PlayStation y Xbox pasa ahora por un mismo sitio: el perfil, la ficha, las plantillas de tier lists, el repaso, el diario, el feed y la media «con horas jugadas».
- Las tres columnas se quedan de momento. Quitarlas es un paso aparte.

**Qué he tocado:**
- `library/models.py`: `extra_hours`, `total_minutes` y `last_played`, y los métodos `minutes_on`, `last_played_on`, `set_platform` y `refresh_totals`, que llama `save`.
- La migración `library.0016`, que rellena los totales de lo que ya había.
- Los que leían las columnas: `accounts/matching.py`, `games/models.py` (la media «con horas jugadas»), `games/editions.py`, `social/activity.py`, `accounts/views/perfil.py`, `library/backlog.py`, `library/diary.py` y `lists/presets.py`.

**Cómo lo he comprobado:**
- 2 tests nuevos: los totales al día y una plataforma inventada que funciona sin tocar nada más, también al importarla.
- La batería completa en las dos variantes.
- La migración aplicada a la copia de tu base de datos.

**Qué falta:**
- Que lo revises. Al fusionar, tu base de datos necesita `migrate` (`library.0016`).
- Pasar Steam, PlayStation y Xbox también al campo genérico y quitar sus seis columnas, cuando no las lea nada.

---

## 11/11/2026 · Logros, fase 2: trofeos de PlayStation uno a uno, logros en el feed y el año pasado

**Rama:** `logros-fase-2`, encima de `gente-que-sigues`. Commit local, sin subir.

**Por qué:** me dijiste «ahora sigue con las demás», y la siguiente de la lista era «Logros uno a uno», que tenía hecha solo la fase de Steam.

**Qué cambia para ti:**
- **Trofeos de PlayStation uno a uno.** Al sincronizar PlayStation, de cada lista de trofeos que ha cambiado (o que nunca se había leído así) se piden sus trofeos en castellano y cuáles tienes, con su fecha y lo raros que son. Se guardan igual que los logros de Steam. En la ficha, cada plataforma despliega los suyos: «Ver tus trofeos de PlayStation», con «Tu trofeo más raro».
- **Logros en el feed**, solo los que merecen salir y son de la última semana: «consiguió todos los logros de Hades en Steam», «consiguió el platino de…» o «consiguió un logro rarísimo en…», con los rarísimos nombrados (menos del 5 % de los jugadores). Los ocultos salen como «Un logro oculto», para no destripar nada. Una primera importación, que trae años de logros, no llena el feed.
- **«Jugados en 2025»** en Nueva tier list, además de la del año en curso. Cuenta tus últimas partidas, tus sesiones y los días en que conseguiste logros, así que entran los juegos que volviste a jugar después. Con `tester_steam` salen 62 juegos. La del año en curso también cuenta ya los logros.

**Qué he tocado:**
- `accounts/psn.py`: `trophy_details` y `read_trophies`. `save_trophies` dice qué títulos hay que leer.
- `accounts/achievements.py`: `save_unlocked` devuelve los recién conseguidos y `detail` elige la plataforma.
- `accounts/steam.py`: al leer los logros, pasa a la actividad lo que merece salir.
- `social/activity.py` (`achievements_read`) y `social/models.py` (`Activity.Kind.TROPHY` y `trophy_verb`). Migración `social.0010`.
- `lists/presets.py`: los logros en «Jugados en» y la plantilla del año pasado.
- `accounts/views/ficha_del_juego.py` y las plantillas `_achievements.html`, `_achievement_card.html` y `social/_activity.html`.

**Cómo lo he comprobado:**
- 3 tests nuevos:
  - los trofeos uno a uno con las respuestas de Sony simuladas, que no se vuelven a pedir sin cambios;
  - lo que sale y lo que no en el feed;
  - el año pasado con un logro.
- La batería completa en las dos variantes.

**Qué falta:**
- **Probar los trofeos con tu cuenta.** Necesita tu NPSSO, como cada sincronización de PlayStation: es lo primero que conviene mirar al revisarlo.
- Xbox uno a uno, cuando se pueda probar Xbox de verdad.

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

## 11/11/2026 · playnite-y-csv, a medias (terminado el 12/11: ver «Importar desde un archivo», arriba)

Cortado por el límite de uso. En el worktree, sin commit: accounts/csv_import.py (leer el CSV, adivinar las columnas, interpretar cada fila e importar por tienda), accounts/views/importar_archivo.py, las tiendas nuevas en ExternalTitle.Platform y HOURS_SOURCES, las horas verificadas solo de Steam, PlayStation y Xbox (Review.snapshot, Review.hours_since y la media de la ficha) y la pantalla nueva en pantallas.json. `manage.py check` pasa.

Falta: la plantilla accounts/import_file.html, la URL y el enlace desde Otras plataformas, los símbolos de las tiendas nuevas en library/_platform_symbols.html, las migraciones (choices de ExternalTitle.platform y PlaySession.platform), los tests, la propuesta, los dos PDF y el commit. Después: Tareas en segundo plano y Nintendo Switch con capturas.
