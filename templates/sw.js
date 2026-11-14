// Service worker de NextPlay (lo sirve accounts.views.service_worker en /sw.js y lo registra base.html).
// - Guarda la página «Sin conexión», los estilos y los iconos, y enseña esa página cuando no hay red.
// - Recibe los avisos push. Llegan sin contenido: pregunta a NextPlay qué enseñar (accounts.push.pending), con su
//   dirección en el servicio push y su secreto.
// - Al pulsar una notificación, abre su enlace o vuelve a la pestaña que ya lo tiene abierto.
// Pantallas: Sin conexión, todas (las notificaciones del móvil o del ordenador).
"use strict";

var CONFIG = {{ config|safe }};
var CACHE = "nextplay-" + CONFIG.version;

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE)
      .then(function (cache) { return cache.addAll(CONFIG.precache); })
      .then(function () { return self.skipWaiting(); })
  );
});

// Borra lo guardado por las versiones anteriores y toma ya el control de las pestañas abiertas.
self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.filter(function (key) {
          return key.indexOf("nextplay-") === 0 && key !== CACHE;
        }).map(function (key) { return caches.delete(key); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

// Primero la red, siempre: lo guardado solo sale sin conexión. Las páginas, con «Sin conexión»; los estáticos, con su
// última copia buena. Lo demás (HTMX, formularios, otras webs) no se toca.
self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(function () { return caches.match(CONFIG.offline); }));
    return;
  }
  if (url.pathname.indexOf(CONFIG.static) === 0) {
    event.respondWith(
      fetch(request).then(function (response) {
        if (response.ok) {
          var copy = response.clone();
          caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
        }
        return response;
      }).catch(function () { return caches.match(request); })
    );
  }
});

// Lo que hay que enseñar: los avisos que este dispositivo aún no ha enseñado, según NextPlay.
function pendingNotices() {
  return self.registration.pushManager.getSubscription().then(function (subscription) {
    if (!subscription) return [];
    var data = subscription.toJSON();
    return fetch(CONFIG.pending, {
      method: "POST",
      credentials: "omit",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({endpoint: data.endpoint, auth: data.keys && data.keys.auth}),
    }).then(function (response) {
      return response.ok ? response.json() : {avisos: []};
    }).then(function (body) { return body.avisos || []; });
  }).catch(function () { return []; });
}

self.addEventListener("push", function (event) {
  event.waitUntil(pendingNotices().then(function (notices) {
    if (notices.length) {
      return Promise.all(notices.map(function (notice) {
        return self.registration.showNotification(notice.title, {
          body: notice.body,
          tag: "aviso-" + notice.id,
          icon: notice.icon || CONFIG.icon,
          badge: CONFIG.badge,
          lang: "es",
          data: {url: notice.url},
        });
      }));
    }
    // Cada aviso push tiene que enseñar algo (si no, el navegador avisa por su cuenta). Si ya se había enseñado todo,
    // la última notificación se vuelve a poner en silencio; si no queda ninguna, un aviso general.
    return self.registration.getNotifications().then(function (shown) {
      var last = shown[shown.length - 1];
      if (last) {
        return self.registration.showNotification(last.title, {
          body: last.body, tag: last.tag, icon: last.icon, badge: last.badge, data: last.data, silent: true,
        });
      }
      return self.registration.showNotification("NextPlay", {
        body: "Tienes avisos nuevos.", tag: "nextplay", icon: CONFIG.icon, badge: CONFIG.badge, lang: "es",
        data: {url: CONFIG.notifications},
      });
    });
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var target = new URL((event.notification.data && event.notification.data.url) || CONFIG.notifications, self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({type: "window", includeUncontrolled: true}).then(function (windows) {
      for (var i = 0; i < windows.length; i++) {
        if (windows[i].url === target && "focus" in windows[i]) return windows[i].focus();
      }
      return self.clients.openWindow(target);
    })
  );
});
