// En qué mundo está la página: la isla o el extraplano de pruebas de los desarrolladores (`?mundo=pruebas`). Se lee una
// sola vez al cargar, antes que el terreno, porque cambia la altura del suelo para todos los módulos (jugador, cámara, casino).
export const PRUEBAS = new URLSearchParams(location.search).get("mundo") === "pruebas";

/** Recarga la página en el extraplano (`true`) o en la isla (`false`), con los parámetros de Discord intactos. */
export function irAMundo(pruebas: boolean): void {
  const u = new URL(location.href);
  if (pruebas) u.searchParams.set("mundo", "pruebas");
  else u.searchParams.delete("mundo");
  location.replace(u);
}
