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
| `retos-fase-2` | Retos: insignias, sección en el perfil, feed, avisos y cierre cada noche | Commit local, encima de la anterior. Lista para revisar. |

**Servidor para revisar:** puerto 8001 (`nexplay-avisos` en `.claude/launch.json`).
- Usa una copia de tu base de datos (`db.sqlite3` de este worktree), así que lo que pruebes ahí no toca la tuya.
- La copia ya tiene aplicadas las migraciones de las dos ramas: `accounts.0022`, `accounts.0023`, `challenges.0002` y `social.0008`.
- La app de escritorio apaga el servidor cuando la sesión se queda quieta: si no responde, pídeme que lo levante.

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
