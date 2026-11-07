// Escala de la escena de la tragaperras en esta página: 1 (544 × 412), cuatro veces más barata que la 2 del bot.
// Va en su propio módulo porque tiene que ejecutarse antes que tragas.js, que la lee al cargarse (ver escena.ts).
(window as unknown as { TRAGAS_S: number }).TRAGAS_S = 1;
