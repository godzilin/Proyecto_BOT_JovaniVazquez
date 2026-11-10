// Capas de dibujo de three (`Object3D.layers`). Fuera, la cámara ve la 0 (el mundo); dentro de un interior, solo la
// `CAPA_INTERIOR`: la habitación y lo que también se ve dentro (tu robot, los demás, las luces). Así el mundo de fuera no gasta
// ni una llamada de dibujo, sombras incluidas, sin tocarlo. La 1 es la de tu robot en primera persona (`CAPA_PROPIA` de
// `camara.ts`, que la cámara solo ve en la pasada de sombras).
export const CAPA_INTERIOR = 2;
