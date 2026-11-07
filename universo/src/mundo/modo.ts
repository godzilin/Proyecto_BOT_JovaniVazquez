// En qué mundo está la página: la isla o el extraplano de pruebas de los desarrolladores (`?mundo=pruebas`). Se lee una
// sola vez al cargar, antes que el terreno, porque cambia la altura del suelo para todos los módulos (jugador, cámara, casino).
// Fuera del navegador (el volcado del mundo de serie en Node) no hay `location`: siempre la isla.
export const PRUEBAS = typeof location !== "undefined" && new URLSearchParams(location.search).get("mundo") === "pruebas";

/** Recarga la página en el extraplano (`true`) o en la isla (`false`), con los parámetros de Discord intactos. */
export function irAMundo(pruebas: boolean): void {
  const u = new URL(location.href);
  if (pruebas) u.searchParams.set("mundo", "pruebas");
  else u.searchParams.delete("mundo");
  // Sin esto la página nueva tendría de `document.referrer` a sí misma, y el SDK de Discord manda el saludo a ese origen
  // (no al de Discord): nunca le contestan y se queda en «Entrando en el Jovaniverso…». Sin referrer, lo manda a '*'.
  const meta = document.createElement("meta");
  meta.name = "referrer";
  meta.content = "no-referrer";
  document.head.append(meta);
  location.replace(u);
}
