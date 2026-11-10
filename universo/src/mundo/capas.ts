// Capas de dibujo de three (`Object3D.layers`). Fuera, la cámara ve la 0 (el mundo); dentro de un interior, solo la
// `CAPA_INTERIOR`: la habitación y lo que también se ve dentro (tu robot, los demás, las luces). Así el mundo de fuera no gasta
// ni una llamada de dibujo, sombras incluidas, sin tocarlo.
export const CAPA_INTERIOR = 1;
