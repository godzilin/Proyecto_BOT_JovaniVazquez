// Los pintores comunes de las tragaperras: los comparten las escenas de la clásica
// (assets/slots/escena.html) y las de Botes (Volcán, Mina y Olimpo) con un
// <script src="../tragas.js"></script> antes de su propio <script> (Node y Chromium lo leen igual).
// Cada máquina los tiñe con sus colores y pinta aparte lo suyo: la clásica, rodillos, mueble,
// cristal, flechas, línea de pago, doble o nada y `paint`.
//
// Todo va dentro de Tragas para no pisar los nombres de las escenas. Lo que dibuja no lleva
// azar (nada de Math.random). Guarda muy poco estado: la caché de bombillas y, en output, el
// fotograma anterior; los sprites los hace la escena una vez (`setup`, `buildStatics`) y los guarda.
//
// Escala
//   Tragas.S      píxeles por unidad lógica (2 por defecto). Se pone UNA vez, antes de usar nada:
//                 los sprites y el lienzo grande se hacen con la que haya entonces (la de Botes
//                 será 816 / 382, no entera). Lo que antes leía `S` (tamaño de los lienzos de
//                 `layer`, `shadowBlur = k * S`...) lee Tragas.S.
//
// Utilidades (g es un CanvasRenderingContext2D; los colores, "#rrggbb" donde se mezclan)
//   clamp(v, a, b)  lerp(a, b, t)
//   rrPath(g, x, y, w, h, r)    rectángulo redondeado como trazado; roundRect lo empieza con beginPath
//   spaced(g, px)               letterSpacing si el motor lo tiene
//   hexRgb(h)  rgba(h, a)  mix(a, b, t)     "#rrggbb" a [r,g,b], a "rgba(...)", mezcla de dos
//   lin(g, x0, y0, x1, y1, stops)  rad(g, x0, y0, r0, x1, y1, r1, stops)    degradados [[offset, color]...]
//   layer(w, h)                 {c, g, w, h}: lienzo de w x h unidades lógicas, ya a escala S
//   capMid(g, y)  fillMid(g, texto, x, y)  inked(g, texto, x, y, relleno, ancho, trazo)
//                               texto centrado en vertical sobre las mayúsculas (Node y Chromium no
//                               ponen igual el textBaseline "middle"); inked lleva contorno debajo
//   fitFont(g, texto, maxW, tamaño, familia, peso = "")   baja la letra hasta que cabe; devuelve el tamaño
//   insetShadow(g, x, y, w, h, r, blur, color)             sombra interior suave de un rectángulo redondeado
//   loadImage(src)              promesa de una Image desde un data URL
//
// Símbolos de los rodillos (se hacen una vez por símbolo, en setup)
//   symbolSprites(img, opts)    [nítido, desenfoque vertical, estela de bandas], capas de layer.
//                               opts, con la clásica entre paréntesis: size (100) lado del símbolo,
//                               pad (14) margen para la sombra, shadow ("rgba(0,0,0,0.38)") color de la
//                               sombra, blur (7) y dy (4) su desenfoque y su desplazamiento en unidades
//                               lógicas, smear (14) radio del desenfoque, band (46) radio de la estela y
//                               gain (1.7) cuánto sube su alfa.
//   smearSprite(src, R)         desenfoque vertical de radio R sobre una capa
//   bandSprite(src, R, gain)    estela de color sin forma sobre una capa
//
// Luces
//   bulbShape(g, shape)         trazado de la bombilla (round, diamond, star o burst), centrada en (0, 0)
//   bulbSprite(shape, color)    capa con la bombilla encendida y su halo, con caché por forma y color
//
// Monedas que caen y destellos
//   coinSprites()               las cuatro fases de la moneda (cara, medio, canto, medio); usa la
//                               fuente Montserrat 700, que la escena declara en su @font-face
//   drawCoins(g, coins, sprites)  coins = [[x, y, fase 0..3, alfa]...], sprites = lo de coinSprites()
//   drawSparkles(g, list)       list = [[x, y, intensidad 0..1]...]
//
// Carteles (Luckiest, que la escena declara en su @font-face)
//   drawBanner(g, b, opts)      b = {tier: big|mega|epic, scale, spin, flash, text?}: scale 0 no pinta,
//                               spin son los grados que giran los rayos del épico, flash aclara el cartel
//                               y text sustituye al de serie (¡GRAN PREMIO!, ¡MEGAPREMIO!, ¡ÉPICO!).
//                               opts = {x, y, size = 1, tint}: (x, y) es el centro del cartel, size escala
//                               el cartel entero (se multiplica por b.scale) y tint = {big, mega, epic}, cada uno
//                               con los colores de Tragas.TINT que quiera cambiar.
//   bannerBig(g, b, tint)  bannerMega(g, b, tint)  bannerEpic(g, b, tint)
//                               cada cartel centrado en (0, 0); tint es el de ese cartel (ya sin tier)
//   TINT                        los colores de serie de la clásica. Cada pintor toma cada color de tint y,
//                               si falta, el de TINT. Lo que se mezcla con blanco al hacer flash (las
//                               placas, plate) tiene que ser "#rrggbb"; el resto, cualquier color de CSS.
//     big   plate [arriba, medio, abajo] placa dorada · rim borde claro · edge filete interior y rombos ·
//           textShadow sombra de la letra · text [arriba, abajo] relleno de la letra · textStroke su trazo
//     mega  tail [arriba, abajo] colas de la cinta · fold pliegue · tailEdge filo de las colas ·
//           plate [arriba, medio, abajo] cinta · rim [arriba, medio, abajo] filo de la cinta ·
//           edge filete interior · textShadow · text [arriba, medio, abajo] · textStroke
//     epic  halo [centro, borde] resplandor de detrás · rays [centro, medio, borde] rayos ·
//           raysFlash los mismos con flash · rayEdge filo de los rayos · oval [arriba, medio, abajo]
//           óvalo · ovalEdge su filo · textShadow · text [arriba, medio, abajo] · textFlash letra con
//           flash · textStroke
//
// Marcador del premio
//   meterSprites(w, h, label = "PREMIO", opts)  {panel, glow}: cristal oscuro con borde dorado y su aura.
//                               opts.gap (52) separa de la letra del rótulo los rombos de los lados.
//   drawMeter(g, m, sprites, x, y, w, h, opts)  m = {text, fit?, glow?}: pinta el importe (con `fit`, el
//                               final, se elige la letra para que no cambie mientras cuenta). (x, y, w, h)
//                               es la caja del marcador, la misma que se dio a meterSprites; h no se usa
//                               hoy. opts: size (70) letra más grande, step (6) lo que baja cada vez,
//                               min (26) la letra deja de bajar al pasar de ahí, inset (56) margen que
//                               tiene que quedar a los lados y textY (62) dónde cae el centro del
//                               importe contando desde y. En un marcador más pequeño se bajan size y step.
//
// Salida: reducir el lienzo grande y mandar solo el recuadro que cambia
//   Tragas.output(big)          big es el lienzo donde pinta la escena (con S no entera, redondeado a
//                               un número par de píxeles de ancho y de alto: si no, lanza un error). Devuelve:
//     resize(ow, oh)  la medida de la salida (meta.out); la deja como estaba si no cambia
//     reset()         olvida el fotograma anterior: el siguiente sale entero (en cada setup)
//     snapshot(full)  reduce, compara con el fotograma anterior y devuelve {x, y, u}: el recuadro
//                     que cambia y window.exportFrame del recuadro. `full` fuerza el fotograma entero.
//   Si big mide exactamente el doble de la salida, un solo paso con calidad alta. Si no, dos: a la mitad
//   justa de big con calidad alta y de ahí a la salida, bilineal. Reducir de una vez a menos de la mitad
//   usa mipmaps y Skia saca el primer fotograma tras setup con otro redondeo: el dibujo repartido entre
//   procesos dejaría de dar los mismos bytes. La escena pone window.exportFrame ??= (c) => c.toDataURL(...).
"use strict";
window.Tragas = (() => {
  // La página del Jovaniverso pinta a escala 1 (pone window.TRAGAS_S antes de cargar esto); Node y Chromium, a 2.
  const T = { S: Number(globalThis.TRAGAS_S) || 2 };
  const TAU = Math.PI * 2;
  const CACHE = new Map();    // bombillas: se hacen la primera vez que se piden

  // -- Utilidades -----------------------------------------------------------------------------------

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  function rrPath(g, x, y, w, h, r) {
    g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }
  function roundRect(g, x, y, w, h, r) { g.beginPath(); rrPath(g, x, y, w, h, r); }
  function spaced(g, px) { if ("letterSpacing" in g) g.letterSpacing = px + "px"; }
  function hexRgb(h) { const n = parseInt(h.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; }
  function rgba(h, a) { const [r, g, b] = hexRgb(h); return `rgba(${r},${g},${b},${a})`; }
  function mix(a, b, t) {
    const A = hexRgb(a), B = hexRgb(b);
    return "#" + A.map((v, i) => Math.round(lerp(v, B[i], t)).toString(16).padStart(2, "0")).join("");
  }
  function lin(g, x0, y0, x1, y1, stops) {
    const gr = g.createLinearGradient(x0, y0, x1, y1);
    for (const [o, c] of stops) gr.addColorStop(o, c);
    return gr;
  }
  function rad(g, x0, y0, r0, x1, y1, r1, stops) {
    const gr = g.createRadialGradient(x0, y0, r0, x1, y1, r1);
    for (const [o, c] of stops) gr.addColorStop(o, c);
    return gr;
  }
  // Un lienzo de `w` x `h` unidades lógicas, ya a escala S.
  function layer(w, h) {
    const c = document.createElement("canvas");
    c.width = Math.ceil(w * T.S); c.height = Math.ceil(h * T.S);
    const g = c.getContext("2d"); g.scale(T.S, T.S);
    return { c, g, w, h };
  }
  // Node y Chromium no ponen igual el textBaseline "middle", así que el texto va sobre la línea base y
  // `y` es el centro de las mayúsculas: con la altura de la H se calcula dónde queda la línea base.
  function capMid(g, y) { return y + g.measureText("H").actualBoundingBoxAscent / 2; }
  function fillMid(g, text, x, y) { g.save(); g.textBaseline = "alphabetic"; g.fillText(text, x, capMid(g, y)); g.restore(); }
  // Texto con contorno (el contorno va debajo del relleno), centrado en `y` como arriba.
  function inked(g, text, x, y, fill, width, stroke) {
    g.save(); g.textBaseline = "alphabetic"; y = capMid(g, y);
    g.lineJoin = "round"; g.miterLimit = 2; g.lineWidth = width; g.strokeStyle = stroke;
    g.strokeText(text, x, y); g.fillStyle = fill; g.fillText(text, x, y); g.restore();
  }
  // Reduce la letra hasta que el texto cabe en `maxW`.
  function fitFont(g, text, maxW, size, family, weight = "") {
    for (; size > 10; size -= 1) { g.font = `${weight}${size}px ${family}`; if (g.measureText(text).width <= maxW) break; }
    return size;
  }
  // Una sombra interior suave: se pinta un marco enorme con un agujero, recortado a la forma.
  function insetShadow(g, x, y, w, h, r, blur, color) {
    g.save(); roundRect(g, x, y, w, h, r); g.clip();
    g.shadowColor = color; g.shadowBlur = blur * T.S;
    g.beginPath(); g.rect(x - 80, y - 80, w + 160, h + 160); rrPath(g, x, y, w, h, r);
    g.fillStyle = "#000"; g.fill("evenodd");
    g.restore();
  }
  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("No se pudo leer un símbolo de la tragaperras"));
      img.src = src;
    });
  }

  // -- Símbolos: sprites nítidos y desenfocados (una vez) -------------------------------------------

  // Tres niveles: nítido (con su sombra suave), desenfoque vertical (velocidad media) y estela de bandas
  // (a tope). `blur` de cada rodillo elige: < 0,15 nítido, < 0,55 desenfoque, más estela.
  function symbolSprites(img, opts = {}) {
    const { size = 100, pad = 14, shadow = "rgba(0,0,0,0.38)", blur = 7, dy = 4, smear = 14, band = 46, gain = 1.7 } = opts;
    const SZ = size + 2 * pad;
    const sharp = layer(SZ, SZ);
    sharp.g.shadowColor = shadow; sharp.g.shadowBlur = blur * T.S; sharp.g.shadowOffsetY = dy * T.S;
    sharp.g.drawImage(img, pad, pad, size, size);
    return [sharp, smearSprite(sharp, smear), bandSprite(sharp, band, gain)];
  }
  // Desenfoque vertical de radio `R` (lógico): el promedio de copias del símbolo movidas un píxel cada una.
  function smearSprite(src, R) {
    const o = layer(src.w, src.h + 2 * R), n = 2 * R * T.S + 1;
    for (let k = 0; k < n; k++) { o.g.globalAlpha = 1 / (k + 1); o.g.drawImage(src.c, 0, k / T.S, src.w, src.h); }
    return o;
  }
  // A toda velocidad el rodillo es una estela de color sin forma: cada fila, del mismo color en todo el
  // ancho, y desenfocada en vertical con radio `R` (dos pasadas de caja). Con degradados en horizontal
  // cada fotograma del GIF costaría el triple. `gain` sube el alfa, que al promediar queda pálido. Se hace
  // en setup, una vez, con arrays pequeños: el bucle de píxeles a mano tardaba un segundo por símbolo.
  function bandSprite(src, R, gain) {
    const w = src.w, h = src.h, oh = h + 2 * R;
    const lo = document.createElement("canvas"); lo.width = w; lo.height = h;
    const lg = lo.getContext("2d"); lg.drawImage(src.c, 0, 0, w, h);
    const px = lg.getImageData(0, 0, w, h).data;
    const rows = [0, 1, 2, 3].map(() => new Float32Array(oh));    // r, g, b y alfa, con el alfa ya multiplicado
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4, a = px[i + 3] / 255;
        rows[0][y + R] += px[i] * a; rows[1][y + R] += px[i + 1] * a; rows[2][y + R] += px[i + 2] * a; rows[3][y + R] += a;
      }
    }
    const half = Math.ceil(R / 2), pre = new Float32Array(oh + 1);
    for (const row of rows) {
      for (let pass = 0; pass < 2; pass++) {
        for (let k = 0; k < oh; k++) pre[k + 1] = pre[k] + row[k];
        for (let k = 0; k < oh; k++) row[k] = (pre[Math.min(oh, k + half + 1)] - pre[Math.max(0, k - half)]) / (2 * half + 1);
      }
    }
    const o = layer(w, oh);
    for (let y = 0; y < oh; y++) {
      const a = rows[3][y];
      if (a < 0.002) continue;
      o.g.fillStyle = `rgba(${Math.round(rows[0][y] / a)},${Math.round(rows[1][y] / a)},${Math.round(rows[2][y] / a)},${Math.min(1, a / w * gain).toFixed(4)})`;
      o.g.fillRect(0, y, w, 1);
    }
    return o;
  }

  // -- Luces: una bombilla por forma y color, con su halo -------------------------------------------

  function bulbShape(g, shape) {
    g.beginPath();
    if (shape === "diamond") {
      g.moveTo(0, -12); g.lineTo(9, 0); g.lineTo(0, 12); g.lineTo(-9, 0); g.closePath();
    } else if (shape === "star" || shape === "burst") {
      const n = shape === "star" ? 5 : 8, ro = shape === "star" ? 13 : 12.5, ri = shape === "star" ? 5.6 : 6;
      for (let k = 0; k < n * 2; k++) {
        const a = -Math.PI / 2 + k * Math.PI / n, r = k % 2 ? ri : ro;
        g.lineTo(Math.cos(a) * r, Math.sin(a) * r);
      }
      g.closePath();
    } else g.arc(0, 0, 8.6, 0, TAU);
  }
  function bulbSprite(shape, color) {
    const key = "bulb" + shape + color;
    if (CACHE.has(key)) return CACHE.get(key);
    const R = 32, o = layer(2 * R, 2 * R), g = o.g;
    g.translate(R, R);
    const hr = shape === "burst" ? 18 : shape === "star" ? 15.5 : 14.5;   // pequeño: cada bombilla que cambia son píxeles de GIF
    g.fillStyle = rad(g, 0, 0, 2, 0, 0, hr, [[0, rgba(color, 0.8)], [0.4, rgba(color, 0.3)], [1, rgba(color, 0)]]);
    g.beginPath(); g.arc(0, 0, hr, 0, TAU); g.fill();
    if (shape === "burst") {                                       // la aureola
      g.beginPath(); g.arc(0, 0, 15.5, 0, TAU);
      g.strokeStyle = rgba(color, 0.75); g.lineWidth = 1.6; g.stroke();
      g.beginPath(); g.arc(0, 0, 18.5, 0, TAU);
      g.strokeStyle = rgba(color, 0.28); g.lineWidth = 1.2; g.stroke();
    }
    bulbShape(g, shape);
    g.fillStyle = rad(g, -2.6, -3.4, 1, 0, 0, 14,
      [[0, "#ffffff"], [0.22, mix(color, "#ffffff", 0.78)], [0.62, color], [1, mix(color, "#000000", 0.5)]]);
    g.fill();
    g.lineJoin = "round"; g.lineWidth = 1.1; g.strokeStyle = rgba(mix(color, "#000000", 0.55), 0.9); g.stroke();
    g.beginPath(); g.ellipse(-2.6, -3.4, shape === "round" ? 3 : 2.2, 1.7, -0.6, 0, TAU);
    g.fillStyle = "rgba(255,255,255,0.8)"; g.fill();
    CACHE.set(key, o);
    return o;
  }

  // -- Monedas que caen y destellos -----------------------------------------------------------------

  // Moneda de oro con canto: cuatro fases (de cara, medio, canto, medio).
  function coinSprites() {
    const R = 16, widths = [28, 18, 8, 18], D = 28;
    return widths.map((cw, phase) => {
      const o = layer(2 * R, 2 * R), g = o.g;
      g.translate(R, R);
      const w = cw / 2;
      roundRect(g, -w, -D / 2, cw, D, w);                                // el canto, más oscuro
      g.fillStyle = lin(g, -w, 0, w, 0, [[0, "#8a5a0a"], [0.5, "#e0a82c"], [1, "#7a4c06"]]); g.fill();
      if (cw >= 14) {                                                    // la cara
        const fw = w - 1.6, fh = D / 2 - 1.6;
        g.beginPath(); g.ellipse(0, 0, fw, fh, 0, 0, TAU);
        g.fillStyle = rad(g, -fw * 0.3, -fh * 0.35, 1, 0, 0, fh * 1.1, [[0, "#fff7c0"], [0.45, "#ffd54a"], [1, "#d8941a"]]); g.fill();
        g.lineWidth = 1; g.strokeStyle = "#a36a08"; g.stroke();
        g.beginPath(); g.ellipse(0, 0, fw * 0.68, fh * 0.68, 0, 0, TAU);
        g.strokeStyle = "rgba(150,96,6,0.75)"; g.lineWidth = 0.9; g.stroke();
        if (cw >= 26) {                                                  // la marca de la casa
          g.font = "700 11px Montserrat"; g.textAlign = "center"; g.textBaseline = "middle";
          g.fillStyle = "rgba(255,255,255,0.65)"; fillMid(g, "$", -0.3, 0.2 - 0.8);
          g.fillStyle = "#a36a08"; fillMid(g, "$", 0, 0.2);
        }
      } else {                                                           // de canto: las muescas
        g.strokeStyle = "rgba(90,52,0,0.6)"; g.lineWidth = 0.8;
        for (let k = -D / 2 + 3; k < D / 2 - 1; k += 3) { g.beginPath(); g.moveTo(-w + 1, k); g.lineTo(w - 1, k); g.stroke(); }
        g.fillStyle = "rgba(255,240,170,0.5)"; g.fillRect(-w + 1, -D / 2 + 3, 1.2, D - 6);
      }
      return o;
    });
  }
  function drawCoins(g, coins, sprites) {
    for (const [x, y, phase, a] of coins || []) {
      const s = sprites[((phase | 0) % 4 + 4) % 4];
      if (!(a > 0)) continue;
      g.globalAlpha = clamp(a, 0, 1);
      g.drawImage(s.c, x - s.w / 2, y - s.h / 2, s.w, s.h);
    }
    g.globalAlpha = 1;
  }
  function drawSparkles(g, list) {
    for (const [x, y, k] of list || []) {
      if (!(k > 0.02)) continue;
      const r = 6 + 10 * k;
      g.save(); g.translate(x, y);
      g.fillStyle = rad(g, 0, 0, 0, 0, 0, r * 1.25, [[0, `rgba(255,236,150,${0.55 * k})`], [1, "rgba(255,220,110,0)"]]);
      g.beginPath(); g.arc(0, 0, r * 1.25, 0, TAU); g.fill();
      g.beginPath();
      for (let j = 0; j < 8; j++) { const rr = j % 2 ? r * 0.26 : r, an = j * Math.PI / 4 - Math.PI / 2; g.lineTo(Math.cos(an) * rr, Math.sin(an) * rr); }
      g.closePath(); g.fillStyle = `rgba(255,255,245,${0.35 + 0.65 * k})`; g.fill();
      g.restore();
    }
  }

  // -- Marcador del premio: panel de cristal oscuro con borde dorado --------------------------------

  const M_PAD = 26;
  function meterSprites(w, h, label = "PREMIO", opts = {}) {
    const { gap = 52 } = opts;
    const panel = layer(w + 2 * M_PAD, h + 2 * M_PAD), g = panel.g;
    g.translate(M_PAD, M_PAD);
    g.save(); g.shadowColor = "rgba(0,0,0,0.7)"; g.shadowBlur = 16 * T.S; g.shadowOffsetY = 5 * T.S;
    roundRect(g, 0, 0, w, h, 17); g.fillStyle = "#050507"; g.fill(); g.restore();
    roundRect(g, 0, 0, w, h, 17);
    g.fillStyle = lin(g, 0, 0, 0, h, [[0, "rgba(34,36,48,0.97)"], [0.5, "rgba(12,13,19,0.97)"], [1, "rgba(24,25,34,0.97)"]]); g.fill();
    g.save(); roundRect(g, 0, 0, w, h, 17); g.clip();                               // el reflejo del cristal
    g.fillStyle = "rgba(255,255,255,0.07)"; g.fillRect(0, 0, w, h * 0.42);
    g.beginPath(); g.moveTo(w * 0.1, 0); g.lineTo(w * 0.3, 0); g.lineTo(w * 0.16, h); g.lineTo(-w * 0.04, h); g.closePath();
    g.fillStyle = "rgba(255,255,255,0.05)"; g.fill();
    g.restore();
    insetShadow(g, 0, 0, w, h, 17, 12, "rgba(0,0,0,0.9)");
    roundRect(g, 1.5, 1.5, w - 3, h - 3, 15.5);
    g.lineWidth = 3.4; g.strokeStyle = lin(g, 0, 0, w, h, [[0, "#fff1a8"], [0.3, "#e6b53f"], [0.55, "#a97616"], [0.8, "#f4d472"], [1, "#9a680f"]]); g.stroke();
    roundRect(g, 5.2, 5.2, w - 10.4, h - 10.4, 12.5); g.lineWidth = 1; g.strokeStyle = "rgba(255,226,140,0.35)"; g.stroke();
    g.font = "700 11px Montserrat"; spaced(g, 5); g.textAlign = "center"; g.textBaseline = "middle";
    g.fillStyle = "rgba(255,226,150,0.62)"; fillMid(g, label, w / 2 + 2.5, 20); spaced(g, 0);
    g.fillStyle = "rgba(255,214,110,0.55)";                                          // rombos a los lados del rótulo
    for (const dx of [-gap, gap]) { g.beginPath(); g.moveTo(w / 2 + dx, 16.5); g.lineTo(w / 2 + dx + 3, 20); g.lineTo(w / 2 + dx, 23.5); g.lineTo(w / 2 + dx - 3, 20); g.closePath(); g.fill(); }
    const glow = layer(w + 2 * M_PAD, h + 2 * M_PAD), q = glow.g;
    q.translate(M_PAD, M_PAD);
    q.shadowColor = "rgba(255,214,80,1)"; q.shadowBlur = 20 * T.S;
    roundRect(q, 1.5, 1.5, w - 3, h - 3, 15.5); q.lineWidth = 4; q.strokeStyle = "#ffd75a"; q.stroke();
    return { panel, glow };
  }
  function drawMeter(g, m, sprites, x, y, w, h, opts = {}) {
    if (!m) return;
    const { size: maxSize = 70, step = 6, min = 26, inset = 56, textY = 62 } = opts;
    const glow = Math.round(clamp(m.glow || 0, 0, 1) * 4) / 4;
    g.drawImage(sprites.panel.c, x - M_PAD, y - M_PAD, sprites.panel.w, sprites.panel.h);
    if (glow > 0.01) { g.save(); g.globalAlpha = glow * 0.85; g.drawImage(sprites.glow.c, x - M_PAD, y - M_PAD, sprites.glow.w, sprites.glow.h); g.restore(); }
    g.save(); g.textAlign = "center"; g.textBaseline = "middle";
    let size = maxSize;
    for (; size > min; size -= step) { g.font = `700 ${size}px Montserrat`; spaced(g, 1); if (g.measureText(m.fit || m.text).width <= w - inset) break; }
    const cy = y + textY;
    if (glow > 0.01) { g.shadowColor = `rgba(255,205,70,${glow})`; g.shadowBlur = 16 * T.S * glow; }
    inked(g, m.text, x + w / 2, cy + 3, "rgba(0,0,0,0.55)", Math.max(6, size * 0.14), "rgba(0,0,0,0.55)");
    g.shadowBlur = 0;
    inked(g, m.text, x + w / 2, cy, lin(g, 0, cy - size * 0.42, 0, cy + size * 0.42, [[0, "#fff8c4"], [0.45, "#ffd848"], [1, "#d98e14"]]),
      Math.max(5, size * 0.12), "#4a2a02");
    g.restore();
  }

  // -- Carteles: GRAN PREMIO (placa), MEGAPREMIO (cinta) y ÉPICO (rayos) -------------------------------

  const EPIC_RAYS = 18;
  const TEXT = { big: "¡GRAN PREMIO!", mega: "¡MEGAPREMIO!", epic: "¡ÉPICO!" };
  // Los colores de serie de la clásica; cada pintor toma de `tint` los que le pasen y de aquí el resto.
  const TINT = {
    big: {
      plate: ["#fff3b0", "#ffd24a", "#d98e14"], rim: "#fffbe0", edge: "#a86e0a",
      textShadow: "rgba(70,36,0,0.45)", text: ["#ffffff", "#fff0a8"], textStroke: "#7a4606",
    },
    mega: {
      tail: ["#a81c30", "#5e0c1a"], fold: "#3e0710", tailEdge: "rgba(255,214,110,0.55)",
      plate: ["#ff5a68", "#c8233a", "#860f22"], rim: ["#fff0a0", "#e8b84a", "#a87614"], edge: "rgba(255,226,140,0.5)",
      textShadow: "rgba(40,0,8,0.5)", text: ["#fff8c0", "#ffd23c", "#f0a418"], textStroke: "#5a0a18",
    },
    epic: {
      halo: ["rgba(255,200,60,0.55)", "rgba(255,200,60,0)"],
      rays: ["#fff6b8", "#ffd23c", "#e68a14"], raysFlash: ["#ffffff", "#ffb347", "#ff8a1c"], rayEdge: "#5a2a02",
      oval: ["#9a5ae6", "#5c28a8", "#2a0f5c"], ovalEdge: "#ffe9a0",
      textShadow: "rgba(8,0,30,0.5)", text: ["#ffffff", "#fff0a8", "#ffc83a"], textFlash: "#fff6b8", textStroke: "#220a4e",
    },
  };
  function bannerFill(g, y0, y1, flash, a, b, c) {
    const gr = g.createLinearGradient(0, y0, 0, y1);
    gr.addColorStop(0, flash ? mix(a, "#ffffff", 0.5) : a); gr.addColorStop(0.5, flash ? mix(b, "#ffffff", 0.35) : b); gr.addColorStop(1, flash ? mix(c, "#ffffff", 0.2) : c);
    return gr;
  }
  // Tres colores en un degradado: arriba, en `mid` y abajo.
  const three = (c, mid = 0.5) => [[0, c[0]], [mid, c[1]], [1, c[2]]];
  // Cartel de GRAN PREMIO: rectángulo redondeado dorado.
  function bannerBig(g, b, tint) {
    const t = { ...TINT.big, ...tint }, flash = b.flash;
    const w = 384, h = 74, text = b.text || TEXT.big;
    g.save(); g.shadowColor = "rgba(0,0,0,0.65)"; g.shadowBlur = 14 * T.S; g.shadowOffsetY = 5 * T.S;
    roundRect(g, -w / 2, -h / 2, w, h, 20); g.fillStyle = bannerFill(g, -h / 2, h / 2, flash, ...t.plate); g.fill(); g.restore();
    g.save(); roundRect(g, -w / 2, -h / 2, w, h, 20); g.clip();
    g.fillStyle = "rgba(255,255,255,0.3)"; g.fillRect(-w / 2, -h / 2, w, h * 0.46); g.restore();
    roundRect(g, -w / 2, -h / 2, w, h, 20); g.lineWidth = 4; g.strokeStyle = flash ? "#ffffff" : t.rim; g.stroke();
    roundRect(g, -w / 2 + 6, -h / 2 + 6, w - 12, h - 12, 15); g.lineWidth = 1.6; g.strokeStyle = t.edge; g.stroke();
    g.fillStyle = t.edge;
    for (const sx of [-1, 1]) { const dx = sx * (w / 2 - 22); g.beginPath(); g.moveTo(dx, -6); g.lineTo(dx + 5, 0); g.lineTo(dx, 6); g.lineTo(dx - 5, 0); g.closePath(); g.fill(); }
    g.textAlign = "center"; g.textBaseline = "middle"; spaced(g, 1);
    g.font = `${fitFont(g, text, w - 84, 42, "Luckiest")}px Luckiest`;
    inked(g, text, 0, 3, t.textShadow, 9, t.textShadow);
    inked(g, text, 0, 0, lin(g, 0, -20, 0, 20, [[0, t.text[0]], [1, t.text[1]]]), 8, t.textStroke);
    spaced(g, 0);
  }
  // Cartel de MEGAPREMIO: cinta roja con colas en V.
  function bannerMega(g, b, tint) {
    const t = { ...TINT.mega, ...tint }, flash = b.flash;
    const w = 402, h = 70, text = b.text || TEXT.mega, tail = 36, drop = 14;
    g.save(); g.shadowColor = "rgba(0,0,0,0.6)"; g.shadowBlur = 12 * T.S; g.shadowOffsetY = 5 * T.S;
    for (const s of [-1, 1]) {                                                       // las colas, detrás
      const x0 = s * (w / 2 - 14), x1 = s * (w / 2 + tail), top = -h / 2 + drop, bot = h / 2 + drop;
      g.beginPath(); g.moveTo(x0, top); g.lineTo(x1, top); g.lineTo(x1 - s * 17, (top + bot) / 2); g.lineTo(x1, bot); g.lineTo(x0, bot); g.closePath();
      g.fillStyle = lin(g, 0, top, 0, bot, [[0, t.tail[0]], [1, t.tail[1]]]); g.fill();
    }
    g.restore();
    for (const s of [-1, 1]) {                                                       // el pliegue
      const x = s * (w / 2 - 14), y1 = h / 2;
      g.beginPath(); g.moveTo(x, y1); g.lineTo(x + s * 14, y1); g.lineTo(x, y1 + drop); g.closePath();
      g.fillStyle = t.fold; g.fill();
      g.strokeStyle = t.tailEdge; g.lineWidth = 1.4; g.lineJoin = "round";
      const top = -h / 2 + drop, bot = h / 2 + drop, x1 = s * (w / 2 + tail);
      g.beginPath(); g.moveTo(x, top); g.lineTo(x1, top); g.lineTo(x1 - s * 17, (top + bot) / 2); g.lineTo(x1, bot); g.lineTo(x, bot); g.stroke();
    }
    g.save(); g.shadowColor = "rgba(0,0,0,0.5)"; g.shadowBlur = 8 * T.S; g.shadowOffsetY = 3 * T.S;
    roundRect(g, -w / 2, -h / 2, w, h, 6); g.fillStyle = bannerFill(g, -h / 2, h / 2, flash, ...t.plate); g.fill(); g.restore();
    g.save(); roundRect(g, -w / 2, -h / 2, w, h, 6); g.clip();
    g.fillStyle = "rgba(255,255,255,0.22)"; g.fillRect(-w / 2, -h / 2, w, h * 0.44); g.restore();
    roundRect(g, -w / 2, -h / 2, w, h, 6); g.lineWidth = 4;
    g.strokeStyle = flash ? "#ffffff" : lin(g, 0, -h / 2, 0, h / 2, three(t.rim)); g.stroke();
    roundRect(g, -w / 2 + 6, -h / 2 + 6, w - 12, h - 12, 3); g.lineWidth = 1.2; g.strokeStyle = t.edge; g.stroke();
    g.textAlign = "center"; g.textBaseline = "middle"; spaced(g, 1);
    g.font = `${fitFont(g, text, w - 60, 42, "Luckiest")}px Luckiest`;
    inked(g, text, 0, 3, t.textShadow, 9, t.textShadow);
    inked(g, text, 0, 0, flash ? "#ffffff" : lin(g, 0, -20, 0, 20, three(t.text)), 8, t.textStroke);
    spaced(g, 0);
  }
  // Cartel ÉPICO: estallido de rayos que giran, con un óvalo morado.
  function bannerEpic(g, b, tint) {
    const t = { ...TINT.epic, ...tint }, flash = b.flash;
    const rx = 234, ry = 66, text = b.text || TEXT.epic, step = TAU / EPIC_RAYS, base = (b.spin || 0) * Math.PI / 180;
    g.save(); g.scale(1, ry / rx);                                                   // el halo de detrás
    g.fillStyle = rad(g, 0, 0, rx * 0.4, 0, 0, rx * 1.12, [[0, t.halo[0]], [1, t.halo[1]]]);
    g.beginPath(); g.arc(0, 0, rx * 1.12, 0, TAU); g.fill(); g.restore();
    g.save(); g.shadowColor = "rgba(0,0,0,0.6)"; g.shadowBlur = 12 * T.S; g.shadowOffsetY = 4 * T.S;
    g.beginPath();
    for (let k = 0; k < EPIC_RAYS * 2; k++) {
      const a = base + k * step / 2, outer = k % 2 === 0;
      const px = Math.cos(a) * rx * (outer ? 1 : 0.76), py = Math.sin(a) * ry * (outer ? 1 : 0.7);
      if (k) g.lineTo(px, py); else g.moveTo(px, py);
    }
    g.closePath();
    g.fillStyle = rad(g, 0, -8, 20, 0, 0, rx, flash ? three(t.raysFlash, 0.55) : three(t.rays, 0.6));
    g.fill(); g.restore();
    g.lineJoin = "round"; g.lineWidth = 2.6; g.strokeStyle = t.rayEdge; g.stroke();
    g.save(); g.beginPath(); g.ellipse(0, 0, rx * 0.69, ry * 0.7, 0, 0, TAU);          // el óvalo morado
    g.fillStyle = rad(g, 0, -20, 6, 0, 0, rx * 0.7, three(t.oval, 0.55)); g.fill();
    g.lineWidth = 3.4; g.strokeStyle = flash ? "#ffffff" : t.ovalEdge; g.stroke();
    g.clip(); g.fillStyle = "rgba(255,255,255,0.16)"; g.fillRect(-rx, -ry, rx * 2, ry * 0.8); g.restore();
    g.textAlign = "center"; g.textBaseline = "middle"; spaced(g, 1.5);
    g.font = `${fitFont(g, text, rx * 1.24, 56, "Luckiest")}px Luckiest`;
    inked(g, text, 0, 4, t.textShadow, 11, t.textShadow);
    inked(g, text, 0, 1, flash ? t.textFlash : lin(g, 0, -24, 0, 26, three(t.text)), 9.5, t.textStroke);
    spaced(g, 0);
  }
  // El cartel de `b` centrado en (x, y): ver la cabecera.
  function drawBanner(g, b, opts = {}) {
    if (!b || !(b.scale > 0)) return;
    const { x = 0, y = 0, size = 1, tint = {} } = opts;
    g.save();
    g.translate(x, y);
    g.scale(b.scale * size, b.scale * size);
    if (b.tier === "mega") bannerMega(g, b, tint.mega); else if (b.tier === "epic") bannerEpic(g, b, tint.epic); else bannerBig(g, b, tint.big);
    g.restore();
  }

  // -- Salida: solo el recuadro que cambia ----------------------------------------------------------

  function output(big) {
    // Con una medida impar la mitad no es justa y Skia pinta el primer fotograma distinto de los demás.
    if (big.width % 2 || big.height % 2) throw new Error("Tragas.output: el lienzo grande ha de medir un número par de píxeles");
    const canvas =(w, h) => { const c = document.createElement("canvas"); c.width = w; c.height = h; return c; };
    const out = canvas(Math.round(big.width / T.S), Math.round(big.height / T.S));
    const crop = document.createElement("canvas");
    const octx = out.getContext("2d", { willReadFrequently: true });
    const cctx = crop.getContext("2d");
    let half = null, hctx = null;     // el paso intermedio: solo si la salida no es la mitad justa
    let prev = null;                  // píxeles del fotograma anterior (Uint32Array)

    return {
      resize(ow, oh) {
        if (out.width !== ow || out.height !== oh) { out.width = ow; out.height = oh; prev = null; }
      },
      reset() { prev = null; },
      snapshot(full) {
        const OW = out.width, OH = out.height;
        if (big.width === 2 * OW && big.height === 2 * OH) {
          octx.imageSmoothingEnabled = true; octx.imageSmoothingQuality = "high";
          octx.drawImage(big, 0, 0, OW, OH);
        } else {
          if (!half) { half = canvas(big.width / 2, big.height / 2); hctx = half.getContext("2d"); }
          hctx.imageSmoothingEnabled = true; hctx.imageSmoothingQuality = "high";
          hctx.drawImage(big, 0, 0, half.width, half.height);
          octx.imageSmoothingEnabled = true; octx.imageSmoothingQuality = "low";
          octx.drawImage(half, 0, 0, OW, OH);
        }
        const cur = new Uint32Array(octx.getImageData(0, 0, OW, OH).data.buffer);
        let x0 = 0, y0 = 0, x1 = OW, y1 = OH;
        if (!full && prev) {
          x0 = OW; y0 = OH; x1 = 0; y1 = 0;
          for (let y = 0; y < OH; y++) {
            const row = y * OW;
            for (let x = 0; x < OW; x++) {
              if (cur[row + x] !== prev[row + x]) {
                if (x < x0) x0 = x; if (x >= x1) x1 = x + 1;
                if (y < y0) y0 = y; if (y >= y1) y1 = y + 1;
              }
            }
          }
          if (x1 <= x0) { x0 = 0; y0 = 0; x1 = 1; y1 = 1; }   // nada ha cambiado
        }
        prev = cur;
        const w = x1 - x0, h = y1 - y0;
        crop.width = w; crop.height = h;
        cctx.drawImage(out, x0, y0, w, h, 0, 0, w, h);
        return { x: x0, y: y0, u: window.exportFrame(crop) };
      },
    };
  }

  return Object.assign(T, {
    clamp, lerp, rrPath, roundRect, spaced, hexRgb, rgba, mix, lin, rad, layer, capMid, fillMid, inked, fitFont,
    insetShadow, loadImage,
    symbolSprites, smearSprite, bandSprite,
    bulbShape, bulbSprite,
    coinSprites, drawCoins, drawSparkles,
    meterSprites, drawMeter,
    TINT, bannerBig, bannerMega, bannerEpic, drawBanner,
    output,
  });
})();
