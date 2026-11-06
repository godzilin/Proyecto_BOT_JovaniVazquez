// Informe de paseo para los logros: cada 15 s y al ocultarse la página manda al servidor los metros, saltos y
// lugares nuevos. Los errores se ignoran y lo no enviado se suma al siguiente informe.
import type { Informe, Mundo } from "./mundo";

export interface Sesion {
  session: string;
  guildId: string | null;
  channelId: string | null;
}

export function iniciarPaseo(mundo: Mundo, s: Sesion): void {
  const enviar = (): void => {
    const informe: Informe | null = mundo.tomarInforme();
    if (!informe) return;
    fetch("/api/paseo", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${s.session}` },
      body: JSON.stringify({ guild_id: s.guildId, channel_id: s.channelId, ...informe }),
      keepalive: true,
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
      })
      .catch((error) => {
        console.warn("No se ha podido enviar el informe de paseo", error);
        mundo.devolverInforme(informe);
      });
  };
  setInterval(enviar, 15000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") enviar();
  });
}
