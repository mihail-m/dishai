// DishAI service worker: shows push notifications and focuses the app when one is tapped. It caches nothing.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch { d = { body: event.data ? event.data.text() : "" }; }
  event.waitUntil(self.registration.showNotification(d.title || "DishAI", {
    body: d.body || "", tag: d.tag || "dishai", icon: "/icon.svg", badge: "/icon.svg", data: { url: d.url || "/" },
    requireInteraction: d.level === "warning",
  }));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((all) => {
    for (const c of all) if ("focus" in c) return c.focus();
    return self.clients.openWindow(url);
  }));
});
