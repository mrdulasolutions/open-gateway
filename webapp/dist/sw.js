/* OpenGateway service worker — Web Push + offline shell hook */
self.addEventListener("push", (event) => {
  let data = { title: "OpenGateway", body: "New activity" };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch (_) {
    try {
      data.body = event.data.text();
    } catch (__) {}
  }
  const title = data.title || "OpenGateway";
  const options = {
    body: data.body || "",
    icon: "/ui/og-logo.png",
    badge: "/ui/favicon.svg",
    tag: data.tag || "opengateway",
    data: data.data || data,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const room = event.notification.data && event.notification.data.room_id;
  const url = room ? `/ui/#${room}` : "/ui/";
  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if (c.url.includes("/ui") && "focus" in c) return c.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
