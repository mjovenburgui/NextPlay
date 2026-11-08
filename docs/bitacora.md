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

Las ramas van una encima de otra, en este orden. Ninguna está subida a GitHub: cuando me digas «súbelas», abro un PR por rama y se fusionan en este orden.

| Rama | Qué lleva | Estado |
|---|---|---|
| `avisos-en-un-solo-sitio` | Avisos en un solo sitio, con preferencias por tipo | Commit `d025255`, local. Lista para revisar. |
| `retos-fase-2` | Retos: insignias, sección en el perfil, feed, avisos y cierre cada noche | Commit `3f2532d`, local, encima de la anterior. Lista para revisar. |
| `repaso-de-la-biblioteca` | Repaso de la biblioteca y valor del pendiente | Commit `f736952`, local, encima de la anterior. Lista para revisar. |
| `remates-rapidos` | Siete remates rápidos, un commit por cada uno, y los PDF al día | Commits locales, encima de la anterior. Lista para revisar. |

**Servidor para revisar:** puerto 8001 (`nexplay-avisos` en `.claude/launch.json`).
- Usa una copia de tu base de datos (`db.sqlite3` de este worktree), así que lo que pruebes ahí no toca la tuya.
- La copia ya tiene aplicadas las migraciones de las cuatro ramas: `accounts.0022` a `accounts.0025`, `challenges.0002`, `social.0008`, `library.0014` y `social.0009`.
- La app de escritorio apaga el servidor cuando la sesión se queda quieta: si no responde, pídeme que lo levante.

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
