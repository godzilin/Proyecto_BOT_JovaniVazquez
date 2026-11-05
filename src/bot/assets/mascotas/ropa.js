// La ropa y los fondos del retrato de las mascotas (assets/mascotas/escena.html), con un
// <script src="ropa.js">. Las prendas salen de bot/services/pet_wardrobe.py: cada una dice
// su forma (shape), sus colores (colors) y, si acaso, su estampado (pattern).
//
// Uso en la escena, en coordenadas lógicas (la escena ya ha escalado el lienzo):
//   Ropa.backdrop(g, drawing, W, H, seed)       el fondo entero.
//   Ropa.behind(slot, drawing)                  true si va detrás de la mascota (capas, alas).
//   Ropa.item(g, slot, drawing, anchor)         una prenda en su anclaje.
// El anclaje lo da el dibujo de la mascota:
//   cabeza  {x, y, w, a}     centro de lo alto de la cabeza, ancho de la cabeza, giro (grados).
//   ojos    {x, y, w, a}     centro entre los ojos y lo que miden las gafas de lado a lado.
//   cuello  {x, y, w, a, head}  centro del cuello y su ancho; `head` = {dy, w}: lo alto de la
//                               cabeza respecto al cuello y su ancho (para el cono).
//   cuerpo  {x, y, w, h, a}  centro del tronco, su ancho y su alto.
// Las prendas se pintan en el orden cuerpo, cuello, ojos, cabeza.
//
// Nada de Math.random: lo que parece azar sale de `seed` (rng). Una forma o un estampado que
// no se conoce no pinta nada y no lanza: un error aquí apagaría el pintor del bot entero.
//
// Todo va dentro de Ropa para no pisar las funciones de la escena.
"use strict";
window.Ropa = (() => {
  const TAU = Math.PI * 2;

  function rng(seed) {
    let s = seed | 0;
    return () => {
      s = s + 0x6D2B79F5 | 0;
      let t = Math.imul(s ^ s >>> 15, 1 | s);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  }
  function hex(c) {
    const m = /^#([0-9a-f]{6})$/i.exec(c || "");
    const n = m ? parseInt(m[1], 16) : 0x808080;
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  // Mezcla un color con negro (k < 0) o con blanco (k > 0).
  function tone(c, k) {
    const [r, gg, b] = hex(c);
    const t = k < 0 ? 0 : 255, f = Math.abs(k);
    const mix = (v) => Math.round(v + (t - v) * f);
    return `rgb(${mix(r)},${mix(gg)},${mix(b)})`;
  }
  function alpha(c, a) { const [r, gg, b] = hex(c); return `rgba(${r},${gg},${b},${a})`; }
  const col = (d, i, fallback) => (d.colors && d.colors[i]) || fallback;

  // Relleno con su contorno un tono más oscuro (el estilo de pegatina de la escena).
  function fill(g, color, lw, outline) {
    g.fillStyle = color;
    g.fill();
    if (lw > 0) {
      g.lineWidth = lw;
      g.strokeStyle = outline || tone(typeof color === "string" && color.startsWith("#") ? color : "#555555", -0.45);
      g.stroke();
    }
  }
  function ellipse(g, x, y, rx, ry, rot = 0) {
    g.beginPath();
    g.ellipse(x, y, Math.max(0.1, rx), Math.max(0.1, ry), rot, 0, TAU);
  }
  function rrect(g, x, y, w, h, r) {
    const k = Math.max(0, Math.min(r, w / 2, h / 2));
    g.beginPath();
    g.moveTo(x + k, y);
    g.arcTo(x + w, y, x + w, y + h, k);
    g.arcTo(x + w, y + h, x, y + h, k);
    g.arcTo(x, y + h, x, y, k);
    g.arcTo(x, y, x + w, y, k);
    g.closePath();
  }
  function star(g, x, y, r, points = 5, inner = 0.45, rot = -Math.PI / 2) {
    g.beginPath();
    for (let i = 0; i < points * 2; i++) {
      const rr = i % 2 ? r * inner : r;
      const t = rot + (i * Math.PI) / points;
      const px = x + Math.cos(t) * rr, py = y + Math.sin(t) * rr;
      if (i) g.lineTo(px, py); else g.moveTo(px, py);
    }
    g.closePath();
  }
  function heart(g, x, y, s) {
    g.beginPath();
    g.moveTo(x, y + s * 0.35);
    g.bezierCurveTo(x - s * 0.9, y - s * 0.25, x - s * 0.45, y - s * 0.95, x, y - s * 0.45);
    g.bezierCurveTo(x + s * 0.45, y - s * 0.95, x + s * 0.9, y - s * 0.25, x, y + s * 0.35);
    g.closePath();
  }
  // Brillos de lentejuela repartidos en un recuadro (repetibles por la semilla).
  function sparkles(g, x, y, w, h, n, seed, color = "#ffffff") {
    const r = rng(seed);
    g.save();
    g.fillStyle = color;
    for (let i = 0; i < n; i++) {
      const px = x + r() * w, py = y + r() * h, s = (0.5 + r()) * Math.min(w, h) * 0.06;
      star(g, px, py, s, 4, 0.35, 0);
      g.globalAlpha = 0.6 + r() * 0.4;
      g.fill();
    }
    g.restore();
  }
  // Estampados dentro del trazado que ya está como clip.
  function pattern(g, d, x, y, w, h, u) {
    const p = d.pattern || "";
    g.save();
    if (p === "rayas") {
      const c = col(d, 1, "#ffffff"), step = Math.max(2, u * 0.12);
      g.fillStyle = c;
      for (let yy = y; yy < y + h; yy += step * 2) g.fillRect(x, yy, w, step);
    } else if (p === "lunares") {
      const c = col(d, 1, "#ffffff"), step = Math.max(3, u * 0.16);
      g.fillStyle = c;
      let row = 0;
      for (let yy = y + step / 2; yy < y + h; yy += step, row++) {
        for (let xx = x + (row % 2 ? step / 2 : 0); xx < x + w; xx += step) {
          ellipse(g, xx, yy, step * 0.18, step * 0.18);
          g.fill();
        }
      }
    } else if (p === "lentejuelas") {
      sparkles(g, x, y, w, h, 14, Math.round(x * 7 + y * 13), "#ffffff");
    } else if (p === "renos") {
      const c = col(d, 1, "#ffffff"), band = h * 0.18, mid = y + h * 0.45;
      g.fillStyle = c;
      g.fillRect(x, mid - band / 2, w, band * 0.18);
      g.fillRect(x, mid + band / 2 - band * 0.18, w, band * 0.18);
      const step = band * 0.8;
      g.beginPath();
      for (let xx = x; xx < x + w; xx += step) {
        g.moveTo(xx, mid + band * 0.25);
        g.lineTo(xx + step / 2, mid - band * 0.25);
        g.lineTo(xx + step, mid + band * 0.25);
      }
      g.lineWidth = Math.max(1, band * 0.12);
      g.strokeStyle = col(d, 2, "#27ae60");
      g.stroke();
      for (let xx = x + step / 2; xx < x + w; xx += step * 1.6) {
        star(g, xx, y + h * 0.18, band * 0.22, 6, 0.4, 0);
        g.fill();
        star(g, xx + step * 0.8, y + h * 0.78, band * 0.22, 6, 0.4, 0);
        g.fill();
      }
    } else if (p === "bandera_boricua") {
      const red = col(d, 0, "#ed0a3f"), white = col(d, 1, "#ffffff"), blue = col(d, 2, "#0050f0");
      const stripe = h / 5;
      for (let i = 0; i < 5; i++) {
        g.fillStyle = i % 2 ? white : red;
        g.fillRect(x, y + i * stripe, w, stripe + 0.5);
      }
      g.beginPath();
      g.moveTo(x, y);
      g.lineTo(x + w * 0.45, y + h / 2);
      g.lineTo(x, y + h);
      g.closePath();
      g.fillStyle = blue;
      g.fill();
      star(g, x + w * 0.15, y + h / 2, Math.min(w, h) * 0.1);
      g.fillStyle = white;
      g.fill();
    }
    g.restore();
  }

  function placed(g, anchor, draw) {
    g.save();
    g.translate(anchor.x, anchor.y);
    if (anchor.a) g.rotate((anchor.a * Math.PI) / 180);
    draw(Math.max(4, anchor.w || 40), anchor.h || 0);
    g.restore();
  }

  // -- Cabeza: el origen es lo alto de la cabeza y `u` su ancho -----------------------------

  const HEAD = {
    chistera(g, d, u) {
      const c = col(d, 0, "#1d1d22"), lw = u * 0.04;
      rrect(g, -u * 0.28, -u * 0.56, u * 0.56, u * 0.54, u * 0.05);
      fill(g, c, lw, "#000000");
      g.fillStyle = col(d, 1, "#7b1e2b");
      g.fillRect(-u * 0.28 + lw / 2, -u * 0.16, u * 0.56 - lw, u * 0.1);
      g.fillStyle = "rgba(255,255,255,0.18)";
      g.fillRect(-u * 0.19, -u * 0.5, u * 0.06, u * 0.32);
      ellipse(g, 0, -u * 0.02, u * 0.52, u * 0.09);
      fill(g, c, lw, "#000000");
    },
    corona(g, d, u) {
      const c = col(d, 0, "#f2c230"), lw = u * 0.035;
      g.beginPath();
      g.moveTo(-u * 0.38, 0);
      g.lineTo(-u * 0.42, -u * 0.42);
      g.lineTo(-u * 0.22, -u * 0.22);
      g.lineTo(0, -u * 0.5);
      g.lineTo(u * 0.22, -u * 0.22);
      g.lineTo(u * 0.42, -u * 0.42);
      g.lineTo(u * 0.38, 0);
      g.closePath();
      fill(g, c, lw);
      g.save();
      g.clip();
      g.fillStyle = tone(c, 0.35);
      g.fillRect(-u * 0.38, -u * 0.5, u * 0.18, u * 0.5);
      if (d.pattern === "lentejuelas") sparkles(g, -u * 0.4, -u * 0.5, u * 0.8, u * 0.5, 10, 3);
      g.restore();
      for (const [x, y] of [[-u * 0.42, -u * 0.42], [0, -u * 0.5], [u * 0.42, -u * 0.42]]) {
        ellipse(g, x, y, u * 0.05, u * 0.05);
        fill(g, tone(c, 0.3), lw * 0.6);
      }
      for (const x of [-u * 0.2, 0, u * 0.2]) {
        ellipse(g, x, -u * 0.1, u * 0.045, u * 0.055);
        fill(g, col(d, 1, "#c0392b"), lw * 0.6);
      }
    },
    cachorro(g, d, u) {
      const c = col(d, 0, "#22201f"), lw = u * 0.04;
      ellipse(g, 0, -u * 0.02, u * 0.62, u * 0.12);
      fill(g, c, lw, "#000000");
      g.beginPath();
      g.moveTo(-u * 0.3, -u * 0.06);
      g.bezierCurveTo(-u * 0.32, -u * 0.38, -u * 0.18, -u * 0.44, 0, -u * 0.36);
      g.bezierCurveTo(u * 0.18, -u * 0.44, u * 0.32, -u * 0.38, u * 0.3, -u * 0.06);
      g.closePath();
      fill(g, c, lw, "#000000");
      g.fillStyle = col(d, 1, "#5a4632");
      g.fillRect(-u * 0.3, -u * 0.13, u * 0.6, u * 0.07);
      g.fillStyle = "rgba(255,255,255,0.15)";
      ellipse(g, -u * 0.14, -u * 0.27, u * 0.05, u * 0.08);
      g.fill();
    },
    sombrero_ala(g, d, u) {
      const c = col(d, 0, "#e8c87a"), lw = u * 0.035, line = col(d, 2, "#a8853c");
      ellipse(g, 0, -u * 0.04, u * 0.78, u * 0.16);
      fill(g, c, lw, line);
      g.save();
      ellipse(g, 0, -u * 0.04, u * 0.78, u * 0.16);
      g.clip();
      g.strokeStyle = alpha(line, 0.6);
      g.lineWidth = u * 0.012;
      for (let r = 0.4; r < 0.8; r += 0.09) {
        ellipse(g, 0, -u * 0.04, u * r, u * r * 0.2);
        g.stroke();
      }
      g.restore();
      g.beginPath();
      g.moveTo(-u * 0.28, -u * 0.08);
      g.bezierCurveTo(-u * 0.3, -u * 0.42, u * 0.3, -u * 0.42, u * 0.28, -u * 0.08);
      g.closePath();
      fill(g, c, lw, line);
      g.fillStyle = col(d, 1, "#c0392b");
      g.fillRect(-u * 0.285, -u * 0.17, u * 0.57, u * 0.08);
    },
    boina(g, d, u) {
      const c = col(d, 0, "#26262b"), lw = u * 0.035;
      g.save();
      g.rotate(-0.18);
      ellipse(g, u * 0.06, -u * 0.12, u * 0.5, u * 0.17);
      fill(g, c, lw, "#000000");
      ellipse(g, u * 0.06, -u * 0.18, u * 0.32, u * 0.05);
      g.fillStyle = "rgba(255,255,255,0.12)";
      g.fill();
      g.beginPath();
      g.moveTo(u * 0.06, -u * 0.28);
      g.lineTo(u * 0.1, -u * 0.38);
      g.lineWidth = u * 0.04;
      g.strokeStyle = c;
      g.stroke();
      g.restore();
    },
    montera(g, d, u) {
      const c = col(d, 0, "#1c1c1c"), trim = col(d, 1, "#d6a630"), lw = u * 0.035;
      g.beginPath();
      g.moveTo(-u * 0.5, -u * 0.02);
      g.bezierCurveTo(-u * 0.48, -u * 0.32, -u * 0.18, -u * 0.36, 0, -u * 0.22);
      g.bezierCurveTo(u * 0.18, -u * 0.36, u * 0.48, -u * 0.32, u * 0.5, -u * 0.02);
      g.closePath();
      fill(g, c, lw, "#000000");
      g.strokeStyle = trim;
      g.lineWidth = u * 0.025;
      g.stroke();
      for (const s of [-1, 1]) {
        ellipse(g, s * u * 0.44, -u * 0.12, u * 0.12, u * 0.13);
        fill(g, c, lw, "#000000");
        ellipse(g, s * u * 0.44, -u * 0.12, u * 0.06, u * 0.065);
        fill(g, trim, 0);
      }
    },
    tricornio(g, d, u) {
      const c = col(d, 0, "#141414"), lw = u * 0.035;
      g.beginPath();
      g.moveTo(-u * 0.4, 0);
      g.lineTo(-u * 0.36, -u * 0.5);
      g.bezierCurveTo(-u * 0.1, -u * 0.62, u * 0.1, -u * 0.62, u * 0.36, -u * 0.5);
      g.lineTo(u * 0.4, 0);
      g.closePath();
      fill(g, c, lw, "#000000");
      g.beginPath();
      g.moveTo(-u * 0.42, -u * 0.04);
      g.bezierCurveTo(-u * 0.3, -u * 0.26, u * 0.3, -u * 0.26, u * 0.42, -u * 0.04);
      g.bezierCurveTo(u * 0.2, u * 0.06, -u * 0.2, u * 0.06, -u * 0.42, -u * 0.04);
      fill(g, col(d, 1, "#3d3d3d"), lw, "#000000");
      g.fillStyle = "rgba(255,255,255,0.35)";
      ellipse(g, -u * 0.18, -u * 0.38, u * 0.05, u * 0.1, -0.3);
      g.fill();
    },
    gorra(g, d, u) {
      const c = col(d, 0, "#2f6fd6"), lw = u * 0.035;
      ellipse(g, 0, -u * 0.02, u * 0.5, u * 0.09);
      fill(g, tone(c, -0.15), lw);
      g.beginPath();
      g.moveTo(-u * 0.4, -u * 0.04);
      g.bezierCurveTo(-u * 0.42, -u * 0.46, u * 0.42, -u * 0.46, u * 0.4, -u * 0.04);
      g.closePath();
      fill(g, c, lw);
      ellipse(g, 0, -u * 0.36, u * 0.04, u * 0.025);
      fill(g, tone(c, -0.2), 0);
      rrect(g, -u * 0.12, -u * 0.26, u * 0.24, u * 0.13, u * 0.03);
      fill(g, col(d, 1, "#f5f5f5"), lw * 0.5);
    },
    casco(g, d, u) {
      const c = col(d, 0, "#f4c20d"), lw = u * 0.035;
      ellipse(g, 0, -u * 0.03, u * 0.56, u * 0.09);
      fill(g, c, lw, col(d, 1, "#c79a00"));
      g.beginPath();
      g.moveTo(-u * 0.42, -u * 0.05);
      g.bezierCurveTo(-u * 0.44, -u * 0.55, u * 0.44, -u * 0.55, u * 0.42, -u * 0.05);
      g.closePath();
      fill(g, c, lw, col(d, 1, "#c79a00"));
      rrect(g, -u * 0.05, -u * 0.45, u * 0.1, u * 0.4, u * 0.04);
      fill(g, tone(c, 0.3), 0);
    },
    gorro_chef(g, d, u) {
      const c = col(d, 0, "#fbfbfb"), line = col(d, 1, "#d8d8d8"), lw = u * 0.03;
      rrect(g, -u * 0.3, -u * 0.22, u * 0.6, u * 0.2, u * 0.03);
      fill(g, c, lw, tone(line, -0.2));
      for (const [x, y, r] of [[-u * 0.22, -u * 0.42, 0.2], [u * 0.22, -u * 0.42, 0.2], [0, -u * 0.55, 0.24]]) {
        ellipse(g, x, y, u * r, u * r);
        fill(g, c, lw, tone(line, -0.2));
      }
      g.fillStyle = c;
      g.fillRect(-u * 0.28, -u * 0.42, u * 0.56, u * 0.22);
    },
    cono(g, d, u) {
      const c = col(d, 0, "#e84393"), lw = u * 0.035;
      g.save();
      g.rotate(0.12);
      g.beginPath();
      g.moveTo(-u * 0.22, 0);
      g.lineTo(0, -u * 0.78);
      g.lineTo(u * 0.22, 0);
      g.closePath();
      fill(g, c, lw);
      g.save();
      g.clip();
      pattern(g, d, -u * 0.25, -u * 0.8, u * 0.5, u * 0.8, u);
      g.restore();
      g.beginPath();
      g.moveTo(-u * 0.22, 0);
      g.lineTo(0, -u * 0.78);
      g.lineTo(u * 0.22, 0);
      g.closePath();
      g.lineWidth = lw;
      g.strokeStyle = tone(c, -0.45);
      g.stroke();
      ellipse(g, 0, -u * 0.8, u * 0.08, u * 0.08);
      fill(g, col(d, 2, "#00b894"), lw * 0.7);
      g.restore();
    },
    papanoel(g, d, u) {
      const c = col(d, 0, "#d63031"), white = col(d, 1, "#ffffff"), lw = u * 0.035;
      g.beginPath();
      g.moveTo(-u * 0.38, -u * 0.06);
      g.bezierCurveTo(-u * 0.3, -u * 0.6, u * 0.2, -u * 0.75, u * 0.55, -u * 0.35);
      g.bezierCurveTo(u * 0.4, -u * 0.38, u * 0.3, -u * 0.3, u * 0.38, -u * 0.06);
      g.closePath();
      fill(g, c, lw);
      ellipse(g, u * 0.56, -u * 0.32, u * 0.1, u * 0.1);
      fill(g, white, lw * 0.7, "#c8c8c8");
      rrect(g, -u * 0.44, -u * 0.14, u * 0.88, u * 0.16, u * 0.08);
      fill(g, white, lw * 0.7, "#c8c8c8");
    },
    bruja(g, d, u) {
      const c = col(d, 0, "#2d1b3d"), lw = u * 0.035;
      ellipse(g, 0, -u * 0.03, u * 0.66, u * 0.12);
      fill(g, c, lw, "#000000");
      g.beginPath();
      g.moveTo(-u * 0.28, -u * 0.06);
      g.bezierCurveTo(-u * 0.18, -u * 0.5, -u * 0.05, -u * 0.8, u * 0.22, -u * 0.9);
      g.bezierCurveTo(u * 0.1, -u * 0.72, u * 0.22, -u * 0.4, u * 0.28, -u * 0.06);
      g.closePath();
      fill(g, c, lw, "#000000");
      g.fillStyle = col(d, 1, "#8e44ad");
      g.fillRect(-u * 0.27, -u * 0.17, u * 0.54, u * 0.09);
      rrect(g, -u * 0.07, -u * 0.19, u * 0.14, u * 0.13, u * 0.02);
      g.lineWidth = u * 0.025;
      g.strokeStyle = col(d, 2, "#f1c40f");
      g.stroke();
    },
    plumas(g, d, u) {
      const lw = u * 0.025;
      const cs = d.colors && d.colors.length ? d.colors : ["#e84393", "#0984e3", "#fdcb6e"];
      for (let i = -3; i <= 3; i++) {
        const t = (i / 3) * 0.75, len = u * (0.75 - Math.abs(i) * 0.06);
        g.save();
        g.rotate(t);
        g.beginPath();
        g.moveTo(0, -u * 0.05);
        g.bezierCurveTo(-u * 0.12, -len * 0.4, -u * 0.08, -len * 0.9, 0, -len);
        g.bezierCurveTo(u * 0.08, -len * 0.9, u * 0.12, -len * 0.4, 0, -u * 0.05);
        fill(g, cs[(i + 3) % cs.length], lw);
        g.beginPath();
        g.moveTo(0, -u * 0.05);
        g.lineTo(0, -len * 0.95);
        g.strokeStyle = "rgba(255,255,255,0.6)";
        g.lineWidth = lw * 0.6;
        g.stroke();
        g.restore();
      }
      rrect(g, -u * 0.4, -u * 0.1, u * 0.8, u * 0.12, u * 0.06);
      fill(g, "#f1c40f", lw);
      sparkles(g, -u * 0.4, -u * 0.1, u * 0.8, u * 0.12, 6, 11);
    },
    laurel(g, d, u) {
      const c = col(d, 0, "#d4a017"), lw = u * 0.02;
      for (const s of [-1, 1]) {
        for (let i = 0; i < 6; i++) {
          const t = Math.PI * (0.95 - i * 0.075);
          const x = s * Math.cos(t) * -u * 0.45, y = -Math.sin(t) * u * 0.22 + u * 0.02;
          ellipse(g, x, y - u * 0.04, u * 0.07, u * 0.035, s * (0.5 + i * 0.25));
          fill(g, i % 2 ? c : tone(c, 0.2), lw, col(d, 1, "#8a6a07"));
        }
      }
      g.beginPath();
      g.arc(0, u * 0.35, u * 0.5, Math.PI * 1.15, Math.PI * 1.85);
      g.strokeStyle = col(d, 1, "#8a6a07");
      g.lineWidth = u * 0.025;
      g.stroke();
    },
    flores(g, d, u) {
      const cs = (d.colors || ["#ffffff", "#e74c3c", "#f1c40f", "#27ae60"]);
      const leaf = cs[3] || "#27ae60", lw = u * 0.02;
      g.beginPath();
      g.arc(0, u * 0.3, u * 0.48, Math.PI * 1.12, Math.PI * 1.88);
      g.strokeStyle = leaf;
      g.lineWidth = u * 0.05;
      g.stroke();
      for (let i = 0; i < 5; i++) {
        const t = Math.PI * (1.18 + i * 0.16);
        const x = Math.cos(t) * u * 0.48, y = u * 0.3 + Math.sin(t) * u * 0.48;
        const petal = cs[i % 2 ? 1 : 0], r = u * 0.06;
        for (let k = 0; k < 5; k++) {
          const a = (k / 5) * TAU;
          ellipse(g, x + Math.cos(a) * r, y + Math.sin(a) * r, r * 0.8, r * 0.8);
          fill(g, petal, lw);
        }
        ellipse(g, x, y, r * 0.55, r * 0.55);
        fill(g, cs[2] || "#f1c40f", lw);
      }
    },
    gorro_lana(g, d, u) {
      const c = col(d, 0, "#e17055"), lw = u * 0.035;
      g.beginPath();
      g.moveTo(-u * 0.42, -u * 0.05);
      g.bezierCurveTo(-u * 0.44, -u * 0.58, u * 0.44, -u * 0.58, u * 0.42, -u * 0.05);
      g.closePath();
      fill(g, c, lw);
      g.save();
      g.clip();
      pattern(g, d, -u * 0.45, -u * 0.6, u * 0.9, u * 0.55, u);
      g.restore();
      rrect(g, -u * 0.46, -u * 0.14, u * 0.92, u * 0.16, u * 0.06);
      fill(g, tone(c, -0.12), lw);
      ellipse(g, 0, -u * 0.52, u * 0.11, u * 0.11);
      fill(g, col(d, 1, "#ffeaa7"), lw);
    },
    pinza(g, d, u) {
      const petal = col(d, 0, "#ffffff"), lw = u * 0.02, x = u * 0.28, y = -u * 0.02, r = u * 0.07;
      for (let k = 0; k < 6; k++) {
        const a = (k / 6) * TAU;
        ellipse(g, x + Math.cos(a) * r, y + Math.sin(a) * r, r * 0.75, r * 0.45, a);
        fill(g, petal, lw, "#c8c8c8");
      }
      ellipse(g, x, y, r * 0.5, r * 0.5);
      fill(g, col(d, 1, "#f1c40f"), lw);
    },
  };

  // -- Ojos: el origen es el centro entre los ojos y `u` lo que miden las gafas -------------

  function pair(g, u, lens) {
    for (const s of [-1, 1]) lens(s * u * 0.27, s);
  }
  function bridge(g, u, color, lw) {
    g.beginPath();
    g.moveTo(-u * 0.08, -u * 0.02);
    g.quadraticCurveTo(0, -u * 0.08, u * 0.08, -u * 0.02);
    g.strokeStyle = color;
    g.lineWidth = lw;
    g.stroke();
  }
  const EYES = {
    gafas_sol(g, d, u) {
      const frame = col(d, 0, "#111111"), lw = u * 0.05;
      bridge(g, u, frame, lw);
      pair(g, u, (x) => {
        rrect(g, x - u * 0.21, -u * 0.13, u * 0.42, u * 0.27, u * 0.1);
        fill(g, col(d, 1, "#2c2c2c"), lw, frame);
        g.fillStyle = "rgba(255,255,255,0.35)";
        g.beginPath();
        g.moveTo(x - u * 0.12, -u * 0.08);
        g.lineTo(x - u * 0.02, -u * 0.08);
        g.lineTo(x - u * 0.12, u * 0.06);
        g.closePath();
        g.fill();
      });
      if (d.pattern === "lentejuelas") sparkles(g, -u * 0.5, -u * 0.16, u, u * 0.32, 10, 7);
    },
    gafas_pasta(g, d, u) {
      const frame = col(d, 0, "#1b1b1b"), lw = u * 0.075;
      bridge(g, u, frame, lw);
      pair(g, u, (x) => {
        rrect(g, x - u * 0.2, -u * 0.15, u * 0.4, u * 0.3, u * 0.06);
        g.fillStyle = alpha(col(d, 1, "#d6eaf8"), 0.35);
        g.fill();
        g.lineWidth = lw;
        g.strokeStyle = frame;
        g.stroke();
      });
    },
    monoculo(g, d, u) {
      const gold = col(d, 0, "#d4a017"), x = u * 0.27, lw = u * 0.04;
      ellipse(g, x, 0, u * 0.17, u * 0.17);
      g.fillStyle = alpha(col(d, 1, "#e8f4fa"), 0.4);
      g.fill();
      g.lineWidth = lw;
      g.strokeStyle = gold;
      g.stroke();
      g.beginPath();
      g.moveTo(x + u * 0.12, u * 0.12);
      g.bezierCurveTo(x + u * 0.25, u * 0.5, x + u * 0.05, u * 0.7, x + u * 0.18, u * 0.95);
      g.setLineDash([u * 0.03, u * 0.02]);
      g.lineWidth = u * 0.02;
      g.stroke();
      g.setLineDash([]);
    },
    gafas_corazon(g, d, u) {
      const c = col(d, 0, "#ff6b9d"), lw = u * 0.04;
      bridge(g, u, col(d, 1, "#c2185b"), lw);
      pair(g, u, (x) => {
        heart(g, x, u * 0.05, u * 0.26);
        fill(g, alpha(c, 0.85), lw, col(d, 1, "#c2185b"));
      });
    },
    gafas_3d(g, d, u) {
      const frame = col(d, 0, "#f5f5f5"), lw = u * 0.05;
      rrect(g, -u * 0.5, -u * 0.15, u, u * 0.3, u * 0.04);
      fill(g, frame, lw, "#9e9e9e");
      pair(g, u, (x, s) => {
        rrect(g, x - u * 0.17, -u * 0.1, u * 0.34, u * 0.2, u * 0.03);
        g.fillStyle = alpha(s < 0 ? col(d, 1, "#e74c3c") : col(d, 2, "#3498db"), 0.85);
        g.fill();
      });
    },
    parche(g, d, u) {
      const c = col(d, 0, "#111111"), x = -u * 0.27;
      g.beginPath();
      g.moveTo(-u * 0.75, -u * 0.32);
      g.lineTo(u * 0.75, u * 0.18);
      g.lineWidth = u * 0.035;
      g.strokeStyle = c;
      g.stroke();
      ellipse(g, x, 0, u * 0.17, u * 0.14);
      fill(g, c, u * 0.03, "#000000");
      const w = col(d, 1, "#f5f5f5");
      ellipse(g, x, -u * 0.02, u * 0.05, u * 0.045);
      fill(g, w, 0);
      g.fillStyle = w;
      g.fillRect(x - u * 0.03, u * 0.02, u * 0.06, u * 0.025);
    },
    antifaz(g, d, u) {
      const c = col(d, 0, "#8e44ad"), lw = u * 0.035;
      g.beginPath();
      g.moveTo(-u * 0.55, -u * 0.1);
      g.bezierCurveTo(-u * 0.35, -u * 0.22, -u * 0.12, -u * 0.18, 0, -u * 0.06);
      g.bezierCurveTo(u * 0.12, -u * 0.18, u * 0.35, -u * 0.22, u * 0.55, -u * 0.1);
      g.bezierCurveTo(u * 0.5, u * 0.14, u * 0.2, u * 0.2, 0, u * 0.08);
      g.bezierCurveTo(-u * 0.2, u * 0.2, -u * 0.5, u * 0.14, -u * 0.55, -u * 0.1);
      fill(g, c, lw);
      g.save();
      g.globalCompositeOperation = "destination-out";
      pair(g, u, (x) => { ellipse(g, x, 0, u * 0.1, u * 0.06); g.fill(); });
      g.restore();
      sparkles(g, -u * 0.5, -u * 0.18, u, u * 0.3, 8, 5, col(d, 1, "#f1c40f"));
      for (const s of [-1, 1]) {
        g.save();
        g.translate(s * u * 0.55, -u * 0.1);
        g.rotate(s * -0.8);
        ellipse(g, 0, -u * 0.14, u * 0.05, u * 0.16);
        fill(g, col(d, 1, "#f1c40f"), lw * 0.6);
        g.restore();
      }
    },
    gafas_natacion(g, d, u) {
      const rim = col(d, 0, "#0fb9b1"), lw = u * 0.04;
      g.beginPath();
      g.moveTo(-u * 0.75, -u * 0.02);
      g.lineTo(u * 0.75, -u * 0.02);
      g.lineWidth = u * 0.05;
      g.strokeStyle = col(d, 1, "#1b1b1b");
      g.stroke();
      pair(g, u, (x) => {
        ellipse(g, x, 0, u * 0.17, u * 0.14);
        g.fillStyle = alpha("#9be7ff", 0.45);
        g.fill();
        g.lineWidth = lw * 1.4;
        g.strokeStyle = rim;
        g.stroke();
      });
    },
    gafas_estrella(g, d, u) {
      const c = col(d, 0, "#f1c40f"), lw = u * 0.04;
      bridge(g, u, col(d, 1, "#e67e22"), lw);
      pair(g, u, (x) => {
        star(g, x, 0, u * 0.21);
        fill(g, alpha(c, 0.9), lw, col(d, 1, "#e67e22"));
      });
    },
  };

  // -- Cuello: el origen es el centro del cuello y `u` su ancho ------------------------------

  const NECK = {
    pajarita(g, d, u) {
      const c = col(d, 0, "#1b1b1b"), lw = u * 0.04;
      for (const s of [-1, 1]) {
        g.beginPath();
        g.moveTo(0, 0);
        g.lineTo(s * u * 0.32, -u * 0.16);
        g.quadraticCurveTo(s * u * 0.38, 0, s * u * 0.32, u * 0.16);
        g.closePath();
        fill(g, c, lw, "#000000");
      }
      ellipse(g, 0, 0, u * 0.07, u * 0.08);
      fill(g, col(d, 1, "#3a3a3a"), lw, "#000000");
    },
    collar(g, d, u) {
      if (d.pattern === "chapa") {
        g.beginPath();
        g.ellipse(0, -u * 0.1, u * 0.5, u * 0.18, 0, 0.1, Math.PI - 0.1);
        g.lineWidth = u * 0.1;
        g.strokeStyle = col(d, 0, "#8b4513");
        g.stroke();
        ellipse(g, 0, u * 0.17, u * 0.11, u * 0.11);
        fill(g, col(d, 1, "#d4a017"), u * 0.03);
        return;
      }
      const bead = col(d, 0, "#f8f4ec"), n = 11, r = u * 0.065;
      for (let i = 0; i < n; i++) {
        const t = 0.15 + (i / (n - 1)) * (Math.PI - 0.3);
        const x = Math.cos(t) * u * 0.5, y = -u * 0.08 + Math.sin(t) * u * 0.22;
        ellipse(g, x, y, r, r);
        fill(g, bead, u * 0.02, col(d, 1, "#d8cfc0"));
        g.fillStyle = "rgba(255,255,255,0.7)";
        ellipse(g, x - r * 0.3, y - r * 0.3, r * 0.3, r * 0.3);
        g.fill();
      }
    },
    bufanda(g, d, u) {
      const c = col(d, 0, "#f4c20d"), lw = u * 0.04;
      const wrap = () => rrect(g, -u * 0.55, -u * 0.12, u * 1.1, u * 0.26, u * 0.12);
      const tail = () => {
        g.beginPath();
        g.moveTo(u * 0.12, u * 0.05);
        g.lineTo(u * 0.42, u * 0.08);
        g.lineTo(u * 0.36, u * 0.72);
        g.lineTo(u * 0.1, u * 0.68);
        g.closePath();
      };
      for (const shape of [tail, wrap]) {
        shape();
        fill(g, c, lw);
        g.save();
        shape();
        g.clip();
        pattern(g, d, -u * 0.6, -u * 0.15, u * 1.2, u * 0.9, u * 0.6);
        g.restore();
        shape();
        g.lineWidth = lw;
        g.strokeStyle = tone(c, -0.45);
        g.stroke();
      }
      g.strokeStyle = tone(c, -0.2);
      g.lineWidth = u * 0.02;
      for (let i = 0; i < 5; i++) {
        const x = u * (0.12 + i * 0.06);
        g.beginPath();
        g.moveTo(x, u * 0.69);
        g.lineTo(x - u * 0.01, u * 0.8);
        g.stroke();
      }
    },
    corbata(g, d, u) {
      const c = col(d, 0, "#7b1e2b"), lw = u * 0.035;
      g.beginPath();
      g.moveTo(-u * 0.09, u * 0.06);
      g.lineTo(u * 0.09, u * 0.06);
      g.lineTo(u * 0.15, u * 0.72);
      g.lineTo(0, u * 0.86);
      g.lineTo(-u * 0.15, u * 0.72);
      g.closePath();
      fill(g, c, lw);
      g.save();
      g.clip();
      g.strokeStyle = alpha(col(d, 1, "#a8323e"), 0.9);
      g.lineWidth = u * 0.04;
      for (let y = 0.1; y < 0.9; y += 0.14) {
        g.beginPath();
        g.moveTo(-u * 0.2, u * y);
        g.lineTo(u * 0.2, u * (y + 0.14));
        g.stroke();
      }
      g.restore();
      g.save();
      g.rotate(0.12);
      rrect(g, -u * 0.1, -u * 0.06, u * 0.2, u * 0.14, u * 0.04);
      fill(g, tone(c, 0.1), lw);
      g.restore();
    },
    pinchos(g, d, u) {
      const c = col(d, 0, "#1b1b1b"), metal = col(d, 1, "#c0c0c0");
      g.beginPath();
      g.ellipse(0, -u * 0.08, u * 0.5, u * 0.2, 0, 0.1, Math.PI - 0.1);
      g.lineWidth = u * 0.12;
      g.strokeStyle = c;
      g.stroke();
      for (let i = 0; i < 7; i++) {
        const t = 0.25 + (i / 6) * (Math.PI - 0.5);
        const x = Math.cos(t) * u * 0.5, y = -u * 0.08 + Math.sin(t) * u * 0.2;
        g.beginPath();
        g.moveTo(x - u * 0.04, y);
        g.lineTo(x + Math.cos(t) * u * 0.1, y + Math.sin(t) * u * 0.13);
        g.lineTo(x + u * 0.04, y);
        g.closePath();
        fill(g, metal, u * 0.015, "#6b6b6b");
      }
    },
    panuelo(g, d, u) {
      const c = col(d, 0, "#d63031"), lw = u * 0.035;
      g.beginPath();
      g.moveTo(-u * 0.48, -u * 0.06);
      g.quadraticCurveTo(0, u * 0.08, u * 0.48, -u * 0.06);
      g.lineTo(0, u * 0.55);
      g.closePath();
      fill(g, c, lw);
      ellipse(g, 0, -u * 0.02, u * 0.08, u * 0.06);
      fill(g, tone(c, -0.1), lw);
      g.fillStyle = col(d, 1, "#ffffff");
      for (const [x, y] of [[-0.15, 0.12], [0.15, 0.12], [0, 0.32]]) {
        ellipse(g, u * x, u * y, u * 0.03, u * 0.03);
        g.fill();
      }
    },
    cono_verguenza(g, d, u, _h, anchor) {
      const c = col(d, 0, "#f5f5f5");
      // Mide según la cabeza (el cuello del hámster es tan ancho como su cara).
      const head = anchor.head || { dy: -u * 1.6, w: u * 1.4 };
      const lw = head.w * 0.025;
      // Va detrás de la mascota (`behind`): un plato alrededor de la cabeza, que asoma por los lados.
      const cy = head.dy * 0.45, rx = head.w * 0.95, ry = Math.abs(head.dy) * 0.75;
      ellipse(g, 0, cy, rx, ry);
      fill(g, alpha(c, 0.95), lw * 1.4, col(d, 1, "#cfd8dc"));
      g.strokeStyle = alpha(col(d, 1, "#cfd8dc"), 0.9);
      g.lineWidth = lw;
      for (let i = 0; i < 14; i++) {
        const t = (i / 14) * TAU;
        g.beginPath();
        g.moveTo(Math.cos(t) * rx * 0.55, cy + Math.sin(t) * ry * 0.55);
        g.lineTo(Math.cos(t) * rx * 0.97, cy + Math.sin(t) * ry * 0.97);
        g.stroke();
      }
    },
    lazo(g, d, u) {
      const c = col(d, 0, "#e74c3c"), lw = u * 0.035;
      for (const s of [-1, 1]) {
        g.beginPath();
        g.moveTo(0, 0);
        g.bezierCurveTo(s * u * 0.25, -u * 0.35, s * u * 0.55, -u * 0.15, s * u * 0.3, u * 0.05);
        g.closePath();
        fill(g, c, lw);
        g.beginPath();
        g.moveTo(0, u * 0.02);
        g.lineTo(s * u * 0.22, u * 0.4);
        g.lineTo(s * u * 0.08, u * 0.36);
        g.closePath();
        fill(g, col(d, 1, "#c0392b"), lw);
      }
      ellipse(g, 0, 0, u * 0.08, u * 0.08);
      fill(g, tone(c, -0.1), lw);
    },
    medalla(g, d, u) {
      const gold = col(d, 0, "#f1c40f"), ribbon = col(d, 1, "#c0392b"), lw = u * 0.03;
      for (const s of [-1, 1]) {
        g.beginPath();
        g.moveTo(s * u * 0.32, -u * 0.08);
        g.lineTo(s * u * 0.14, -u * 0.08);
        g.lineTo(s * u * 0.02, u * 0.38);
        g.lineTo(s * u * 0.16, u * 0.38);
        g.closePath();
        fill(g, ribbon, lw);
      }
      ellipse(g, 0, u * 0.5, u * 0.17, u * 0.17);
      fill(g, gold, lw);
      star(g, 0, u * 0.5, u * 0.09);
      fill(g, tone(gold, 0.35), 0);
    },
    babero(g, d, u) {
      const c = col(d, 0, "#dff9fb"), lw = u * 0.035;
      g.beginPath();
      g.moveTo(-u * 0.32, -u * 0.04);
      g.quadraticCurveTo(0, u * 0.08, u * 0.32, -u * 0.04);
      g.bezierCurveTo(u * 0.4, u * 0.45, -u * 0.4, u * 0.45, -u * 0.32, -u * 0.04);
      fill(g, c, lw, tone(col(d, 1, "#6c5ce7"), -0.1));
      const b = col(d, 1, "#6c5ce7");
      rrect(g, -u * 0.1, u * 0.18, u * 0.2, u * 0.05, u * 0.02);
      fill(g, b, 0);
      for (const x of [-u * 0.1, u * 0.1]) {
        for (const y of [u * 0.18, u * 0.23]) {
          ellipse(g, x, y, u * 0.035, u * 0.035);
          g.fill();
        }
      }
    },
  };

  // -- Cuerpo: el origen es el centro del tronco; `u` su ancho y `h` su alto ----------------

  // La silueta de una prenda del tronco: hombros más estrechos, bajo redondeado y escote.
  function garment(g, u, h) {
    g.beginPath();
    g.moveTo(-u * 0.3, -h * 0.5);
    g.quadraticCurveTo(0, -h * 0.34, u * 0.3, -h * 0.5);
    g.bezierCurveTo(u * 0.48, -h * 0.42, u * 0.56, -h * 0.05, u * 0.52, h * 0.3);
    g.quadraticCurveTo(0, h * 0.58, -u * 0.52, h * 0.3);
    g.bezierCurveTo(-u * 0.56, -h * 0.05, -u * 0.48, -h * 0.42, -u * 0.3, -h * 0.5);
    g.closePath();
  }
  // Ropa que se ciñe al tronco: se pinta dentro de su silueta y deja el trazado puesto.
  function torso(g, u, h, draw) {
    g.save();
    garment(g, u, h);
    g.clip();
    draw();
    g.restore();
    garment(g, u, h);
  }
  const BODY = {
    capa(g, d, u, h) {
      const c = col(d, 0, "#d63031"), lw = u * 0.035;
      const shape = () => {
        g.beginPath();
        g.moveTo(-u * 0.3, -h * 0.5);
        g.bezierCurveTo(-u * 0.75, -h * 0.1, -u * 0.8, h * 0.4, -u * 0.62, h * 0.58);
        g.quadraticCurveTo(0, h * 0.68, u * 0.62, h * 0.58);
        g.bezierCurveTo(u * 0.8, h * 0.4, u * 0.75, -h * 0.1, u * 0.3, -h * 0.5);
        g.closePath();
      };
      shape();
      fill(g, d.pattern === "bandera_boricua" ? "#ffffff" : c, lw, tone(c, -0.45));
      g.save();
      shape();
      g.clip();
      if (d.pattern) pattern(g, d, -u * 0.8, -h * 0.5, u * 1.6, h * 1.2, u);
      g.fillStyle = alpha(col(d, 1, "#a71d1d"), d.pattern ? 0.25 : 0.9);
      g.beginPath();
      g.moveTo(-u * 0.62, h * 0.58);
      g.quadraticCurveTo(0, h * 0.68, u * 0.62, h * 0.58);
      g.quadraticCurveTo(0, h * 0.45, -u * 0.62, h * 0.58);
      g.fill();
      g.restore();
    },
    alas(g, d, u, h) {
      const c = col(d, 0, "#ffffff"), line = col(d, 1, "#dfe6e9"), lw = u * 0.03;
      for (const s of [-1, 1]) {
        g.save();
        g.translate(s * u * 0.32, -h * 0.15);
        g.scale(s, 1);
        g.beginPath();
        g.moveTo(0, 0);
        g.bezierCurveTo(u * 0.3, -h * 0.55, u * 0.85, -h * 0.5, u * 0.8, -h * 0.05);
        g.bezierCurveTo(u * 0.7, h * 0.05, u * 0.6, h * 0.25, u * 0.4, h * 0.3);
        g.bezierCurveTo(u * 0.25, h * 0.35, u * 0.1, h * 0.2, 0, 0);
        fill(g, c, lw, tone(line, -0.25));
        g.strokeStyle = line;
        g.lineWidth = lw;
        for (let i = 1; i < 4; i++) {
          g.beginPath();
          g.moveTo(u * 0.15 * i, h * (0.02 + 0.06 * i));
          g.quadraticCurveTo(u * (0.2 + 0.15 * i), -h * 0.1, u * (0.3 + 0.15 * i), -h * (0.2 - 0.04 * i));
          g.stroke();
        }
        g.restore();
      }
    },
    jersey(g, d, u, h) {
      const c = col(d, 0, "#c0392b"), lw = u * 0.035;
      torso(g, u, h, () => {
        g.fillStyle = c;
        g.fillRect(-u * 0.5, -h * 0.5, u, h);
        pattern(g, d, -u * 0.5, -h * 0.35, u, h * 0.8, u * 0.5);
        g.fillStyle = tone(c, -0.15);
        g.fillRect(-u * 0.5, -h * 0.5, u, h * 0.12);
      });
      g.lineWidth = lw;
      g.strokeStyle = tone(c, -0.45);
      g.stroke();
    },
    chaleco(g, d, u, h) {
      const c = col(d, 0, "#ff7f0e"), strap = col(d, 1, "#f5f5f5"), lw = u * 0.035;
      torso(g, u, h, () => {
        g.fillStyle = c;
        g.fillRect(-u * 0.5, -h * 0.5, u, h * 0.85);
        g.fillStyle = tone(c, -0.15);
        g.fillRect(-u * 0.03, -h * 0.5, u * 0.06, h * 0.85);
        g.fillStyle = strap;
        for (const y of [-h * 0.05, h * 0.2]) g.fillRect(-u * 0.5, y, u, h * 0.07);
        g.fillStyle = "#555555";
        for (const y of [-h * 0.015, h * 0.235]) {
          rrect(g, -u * 0.06, y, u * 0.12, h * 0.05, u * 0.01);
          g.fill();
        }
      });
      g.lineWidth = lw;
      g.strokeStyle = tone(c, -0.45);
      g.stroke();
    },
    manta(g, d, u, h) {
      const c = col(d, 0, "#f5f0e1"), lw = u * 0.035;
      const shape = () => {
        g.beginPath();
        g.moveTo(-u * 0.48, -h * 0.25);
        g.quadraticCurveTo(0, -h * 0.62, u * 0.48, -h * 0.25);
        g.lineTo(u * 0.44, h * 0.22);
        g.quadraticCurveTo(0, h * 0.32, -u * 0.44, h * 0.22);
        g.closePath();
      };
      shape();
      fill(g, c, lw, tone(c, -0.4));
      g.save();
      shape();
      g.clip();
      const stripe = h * 0.06;
      for (let i = 0; i < 6; i++) {
        g.fillStyle = i % 2 ? col(d, 2, "#b22222") : col(d, 1, "#2c2c2c");
        g.fillRect(-u * 0.5, -h * 0.4 + i * stripe * 2.2, u, stripe);
      }
      g.restore();
      g.strokeStyle = tone(c, -0.3);
      g.lineWidth = u * 0.015;
      for (let i = 0; i < 9; i++) {
        const x = -u * 0.4 + i * u * 0.1, y = h * (0.22 + 0.1 * Math.sin((i / 8) * Math.PI));
        g.beginPath();
        g.moveTo(x, y);
        g.lineTo(x, y + h * 0.08);
        g.stroke();
      }
    },
    chubasquero(g, d, u, h) {
      const c = col(d, 0, "#f4d03f"), lw = u * 0.035;
      torso(g, u, h, () => {
        g.fillStyle = c;
        g.fillRect(-u * 0.5, -h * 0.5, u, h);
        g.fillStyle = tone(c, -0.12);
        g.fillRect(-u * 0.5, -h * 0.5, u * 0.12, h);
        g.fillStyle = col(d, 1, "#c9a400");
        for (let i = 0; i < 3; i++) {
          ellipse(g, 0, -h * 0.25 + i * h * 0.22, u * 0.04, u * 0.04);
          g.fill();
        }
        g.strokeStyle = tone(c, -0.25);
        g.lineWidth = u * 0.02;
        g.beginPath();
        g.moveTo(-u * 0.06, -h * 0.5);
        g.lineTo(-u * 0.06, h * 0.5);
        g.stroke();
      });
      g.lineWidth = lw;
      g.strokeStyle = tone(c, -0.45);
      g.stroke();
    },
    volantes(g, d, u, h) {
      const c = col(d, 0, "#d63031"), lw = u * 0.03;
      for (let row = 0; row < 3; row++) {
        const y = h * (0.0 + row * 0.18), w = u * (0.85 + row * 0.12);
        g.beginPath();
        g.moveTo(-w / 2, y);
        const n = 6;
        for (let i = 0; i < n; i++) {
          const x0 = -w / 2 + (i * w) / n;
          g.quadraticCurveTo(x0 + w / n / 2, y + h * 0.2, x0 + w / n, y);
        }
        g.lineTo(w / 2, y - h * 0.08);
        g.lineTo(-w / 2, y - h * 0.08);
        g.closePath();
        fill(g, row % 2 ? tone(c, -0.08) : c, lw);
        g.save();
        g.clip();
        pattern(g, d, -w / 2, y - h * 0.1, w, h * 0.32, u * 0.6);
        g.restore();
      }
    },
    camiseta(g, d, u, h) {
      const c = col(d, 0, "#f9d71c"), trim = col(d, 1, "#1e5aa8"), lw = u * 0.035;
      torso(g, u, h, () => {
        g.fillStyle = c;
        g.fillRect(-u * 0.5, -h * 0.5, u, h * 0.82);
        g.fillStyle = trim;
        g.beginPath();
        g.moveTo(-u * 0.16, -h * 0.5);
        g.lineTo(0, -h * 0.32);
        g.lineTo(u * 0.16, -h * 0.5);
        g.lineTo(u * 0.1, -h * 0.5);
        g.lineTo(0, -h * 0.4);
        g.lineTo(-u * 0.1, -h * 0.5);
        g.closePath();
        g.fill();
        g.fillRect(-u * 0.5, h * 0.27, u, h * 0.05);
        g.font = `700 ${Math.round(h * 0.28)}px Montserrat, sans-serif`;
        g.textAlign = "center";
        g.textBaseline = "middle";
        g.fillText("10", 0, h * 0.02);
      });
      g.lineWidth = lw;
      g.strokeStyle = tone(c, -0.45);
      g.stroke();
    },
  };

  // -- Fondos: ocupan todo el lienzo; el suelo empieza hacia el 70 % del alto ---------------

  function sky(g, W, H, top, bottom) {
    const gr = g.createLinearGradient(0, 0, 0, H);
    gr.addColorStop(0, top);
    gr.addColorStop(1, bottom);
    g.fillStyle = gr;
    g.fillRect(0, 0, W, H);
  }
  function floor(g, W, H, y, top, bottom) {
    const gr = g.createLinearGradient(0, y, 0, H);
    gr.addColorStop(0, top);
    gr.addColorStop(1, bottom);
    g.fillStyle = gr;
    g.fillRect(0, y, W, H - y);
  }
  function cloud(g, x, y, s, color = "rgba(255,255,255,0.9)") {
    g.fillStyle = color;
    for (const [dx, dy, r] of [[0, 0, 1], [0.9, -0.3, 0.8], [1.7, 0, 0.9], [0.8, 0.25, 0.9]]) {
      ellipse(g, x + dx * s, y + dy * s, r * s, r * s * 0.75);
      g.fill();
    }
  }
  function stars(g, W, H, n, seed, top = 0.7) {
    const r = rng(seed);
    for (let i = 0; i < n; i++) {
      g.fillStyle = `rgba(255,255,255,${0.4 + r() * 0.6})`;
      const s = 0.6 + r() * 1.6;
      ellipse(g, r() * W, r() * H * top, s, s);
      g.fill();
    }
  }
  function lights(g, W, y, n, seed, colors) {
    const r = rng(seed);
    g.strokeStyle = "rgba(40,30,20,0.6)";
    g.lineWidth = 1.5;
    g.beginPath();
    g.moveTo(0, y);
    for (let i = 1; i <= 8; i++) g.quadraticCurveTo(((i - 0.5) / 8) * W, y + 22, (i / 8) * W, y);
    g.stroke();
    for (let i = 0; i < n; i++) {
      const t = (i + 0.5) / n, x = t * W, k = (t * 8) % 1, yy = y + 22 * 4 * k * (1 - k) * 0.5 + 6;
      const c = colors[Math.floor(r() * colors.length)];
      g.fillStyle = alpha(c, 0.25);
      ellipse(g, x, yy, 9, 9);
      g.fill();
      g.fillStyle = c;
      ellipse(g, x, yy, 4, 5);
      g.fill();
    }
  }

  const BACKDROP = {
    casa(g, d, W, H) {
      const wall = col(d, 0, "#f3e3c8"), wood = col(d, 1, "#c98f5a"), plant = col(d, 2, "#7a9e7e");
      sky(g, W, H, tone(wall, 0.15), wall);
      g.fillStyle = alpha(tone(wall, -0.2), 0.25);
      for (let x = 20; x < W; x += 40) g.fillRect(x, 0, 2, H * 0.7);
      rrect(g, W * 0.62, H * 0.1, W * 0.26, H * 0.36, 8);
      fill(g, "#bfe3f5", 4, tone(wood, -0.3));
      g.fillStyle = tone(wood, -0.3);
      g.fillRect(W * 0.75 - 2, H * 0.1, 4, H * 0.36);
      g.fillRect(W * 0.62, H * 0.28 - 2, W * 0.26, 4);
      cloud(g, W * 0.65, H * 0.2, 12);
      floor(g, W, H, H * 0.7, wood, tone(wood, -0.25));
      g.strokeStyle = alpha(tone(wood, -0.4), 0.35);
      g.lineWidth = 1.5;
      for (let y = H * 0.74; y < H; y += 18) {
        g.beginPath();
        g.moveTo(0, y);
        g.lineTo(W, y);
        g.stroke();
      }
      ellipse(g, W * 0.5, H * 0.86, W * 0.3, H * 0.08);
      fill(g, alpha("#c0392b", 0.55), 0);
      rrect(g, W * 0.07, H * 0.5, 44, 50, 6);
      fill(g, "#d35400", 3);
      for (const [dx, dy, rot] of [[0, -40, 0], [-20, -28, -0.6], [20, -28, 0.6], [-10, -55, -0.2], [12, -52, 0.3]]) {
        ellipse(g, W * 0.07 + 22 + dx, H * 0.5 + dy, 10, 26, rot);
        fill(g, plant, 2.5);
      }
    },
    canteras(g, d, W, H, seed) {
      sky(g, W, H, tone(col(d, 0, "#7ec8e3"), 0.2), col(d, 0, "#7ec8e3"));
      cloud(g, W * 0.12, H * 0.14, 14);
      cloud(g, W * 0.7, H * 0.1, 11);
      g.fillStyle = "#fff6c9";
      ellipse(g, W * 0.86, H * 0.16, 26, 26);
      g.fill();
      floor(g, W, H, H * 0.45, col(d, 2, "#2e86c1"), tone(col(d, 2, "#2e86c1"), 0.25));
      g.fillStyle = tone(col(d, 2, "#2e86c1"), -0.15);
      g.fillRect(0, H * 0.53, W, 6);
      g.fillStyle = "#8d6e63";
      for (let x = 0; x < W; x += 30) {
        ellipse(g, x + 15, H * 0.535, 16, 5);
        g.fill();
      }
      g.strokeStyle = "rgba(255,255,255,0.7)";
      g.lineWidth = 2;
      for (let i = 0; i < 4; i++) {
        g.beginPath();
        const y = H * (0.58 + i * 0.03);
        for (let x = 0; x <= W; x += 20) g.lineTo(x, y + Math.sin(x / 30 + i) * 2);
        g.stroke();
      }
      floor(g, W, H, H * 0.7, col(d, 1, "#f2d49b"), tone(col(d, 1, "#f2d49b"), -0.12));
      const r = rng(seed);
      g.fillStyle = alpha(tone(col(d, 1, "#f2d49b"), -0.3), 0.5);
      for (let i = 0; i < 60; i++) {
        ellipse(g, r() * W, H * 0.72 + r() * H * 0.28, 1.4, 1);
        g.fill();
      }
      ellipse(g, W * 0.15, H * 0.82, 50, 14, -0.1);
      fill(g, "#e74c3c", 2);
    },
    teide(g, d, W, H) {
      sky(g, W, H, tone(col(d, 0, "#9ad0f5"), -0.1), tone(col(d, 0, "#9ad0f5"), 0.3));
      const rock = col(d, 1, "#8d6e63");
      g.beginPath();
      g.moveTo(W * 0.05, H * 0.62);
      g.lineTo(W * 0.42, H * 0.16);
      g.lineTo(W * 0.5, H * 0.12);
      g.lineTo(W * 0.58, H * 0.17);
      g.lineTo(W * 0.95, H * 0.62);
      g.closePath();
      fill(g, rock, 3);
      g.beginPath();
      g.moveTo(W * 0.42, H * 0.16);
      g.lineTo(W * 0.5, H * 0.12);
      g.lineTo(W * 0.58, H * 0.17);
      g.lineTo(W * 0.62, H * 0.24);
      g.lineTo(W * 0.56, H * 0.22);
      g.lineTo(W * 0.5, H * 0.27);
      g.lineTo(W * 0.44, H * 0.22);
      g.lineTo(W * 0.37, H * 0.23);
      g.closePath();
      fill(g, col(d, 2, "#ffffff"), 2, "#c9d6df");
      for (let i = 0; i < 7; i++) cloud(g, (i / 6) * W - 30, H * 0.58 + (i % 2) * 8, 22, "rgba(255,255,255,0.95)");
      floor(g, W, H, H * 0.7, "#a1887f", "#6d4c41");
      g.fillStyle = "#5d4037";
      for (const [x, s] of [[0.1, 14], [0.3, 9], [0.8, 16], [0.92, 10]]) {
        ellipse(g, W * x, H * 0.76, s, s * 0.6);
        g.fill();
      }
      g.fillStyle = "#c62828";
      for (const x of [0.06, 0.86]) {
        g.fillRect(W * x, H * 0.62, 6, 50);
        for (let k = 0; k < 6; k++) {
          ellipse(g, W * x + 3, H * 0.6 - k * 8, 7 - k, 5);
          g.fill();
        }
      }
    },
    moncloa(g, d, W, H) {
      const stone = col(d, 0, "#efe6d5"), carpet = col(d, 1, "#b03a2e");
      sky(g, W, H, "#9cc9ef", "#d8ecfb");
      g.fillStyle = stone;
      g.fillRect(W * 0.08, H * 0.12, W * 0.84, H * 0.5);
      g.fillStyle = tone(stone, -0.1);
      g.fillRect(W * 0.08, H * 0.12, W * 0.84, 14);
      for (let i = 0; i < 6; i++) {
        const x = W * (0.16 + i * 0.137);
        rrect(g, x - 9, H * 0.18, 18, H * 0.42, 4);
        fill(g, tone(stone, 0.4), 2, tone(stone, -0.2));
      }
      rrect(g, W * 0.43, H * 0.3, W * 0.14, H * 0.32, 4);
      fill(g, "#4e342e", 3);
      for (let i = 0; i < 4; i++) {
        g.fillStyle = tone(stone, -0.05 - i * 0.04);
        g.fillRect(W * (0.2 - i * 0.04), H * (0.62 + i * 0.04), W * (0.6 + i * 0.08), H * 0.04);
      }
      floor(g, W, H, H * 0.78, "#cfd8dc", "#90a4ae");
      g.beginPath();
      g.moveTo(W * 0.42, H * 0.62);
      g.lineTo(W * 0.58, H * 0.62);
      g.lineTo(W * 0.72, H);
      g.lineTo(W * 0.28, H);
      g.closePath();
      fill(g, carpet, 0);
      for (const x of [0.03, 0.97]) {
        g.fillStyle = "#6d6d6d";
        g.fillRect(W * x - 2, H * 0.1, 4, H * 0.6);
        const fx = x < 0.5 ? W * x + 2 : W * x - 46;
        g.fillStyle = "#c60b1e";
        g.fillRect(fx, H * 0.12, 44, 30);
        g.fillStyle = col(d, 2, "#f1c40f");
        g.fillRect(fx, H * 0.12 + 8, 44, 14);
      }
    },
    hongkong(g, d, W, H, seed) {
      const night = col(d, 0, "#120a2a");
      sky(g, W, H, night, tone(night, 0.15));
      const r = rng(seed);
      for (let i = 0; i < 9; i++) {
        const w = 50 + r() * 40, x = i * 72 - 20, h = H * (0.35 + r() * 0.35);
        g.fillStyle = tone(night, 0.12 + r() * 0.08);
        g.fillRect(x, H * 0.72 - h, w, h);
        g.fillStyle = "rgba(255,230,150,0.55)";
        for (let yy = H * 0.72 - h + 8; yy < H * 0.7; yy += 14) {
          for (let xx = x + 6; xx < x + w - 6; xx += 12) if (r() < 0.45) g.fillRect(xx, yy, 5, 7);
        }
      }
      const neon = [col(d, 1, "#ff3fa4"), col(d, 2, "#2de2e6"), "#ffe14d"];
      for (let i = 0; i < 6; i++) {
        const c = neon[i % 3], x = 20 + i * 105 + r() * 20, y = 40 + r() * 120;
        g.save();
        g.shadowColor = c;
        g.shadowBlur = 0;
        rrect(g, x, y, 34 + r() * 30, 20 + r() * 26, 4);
        g.lineWidth = 3;
        g.strokeStyle = c;
        g.stroke();
        g.fillStyle = alpha(c, 0.15);
        g.fill();
        g.restore();
      }
      floor(g, W, H, H * 0.72, "#2b2b3a", "#14141c");
      g.fillStyle = "rgba(255,63,164,0.18)";
      g.fillRect(0, H * 0.74, W, 3);
      g.fillStyle = "rgba(45,226,230,0.15)";
      g.fillRect(0, H * 0.8, W, 2);
    },
    yate(g, d, W, H) {
      const sea = col(d, 0, "#5dade2"), teak = col(d, 1, "#c8a165");
      sky(g, W, H, "#7fc4f0", "#d6f0ff");
      cloud(g, W * 0.2, H * 0.12, 13);
      floor(g, W, H, H * 0.35, sea, tone(sea, -0.25));
      g.strokeStyle = "rgba(255,255,255,0.5)";
      g.lineWidth = 1.5;
      for (let i = 0; i < 5; i++) {
        g.beginPath();
        for (let x = 0; x <= W; x += 16) g.lineTo(x, H * (0.4 + i * 0.05) + Math.sin(x / 25 + i * 2) * 1.5);
        g.stroke();
      }
      g.fillStyle = col(d, 2, "#ffffff");
      g.fillRect(0, H * 0.6, W, H * 0.08);
      g.fillStyle = "#b0bec5";
      for (let x = 10; x < W; x += 36) g.fillRect(x, H * 0.52, 3, H * 0.08);
      g.fillRect(0, H * 0.52, W, 3);
      floor(g, W, H, H * 0.68, teak, tone(teak, -0.2));
      g.strokeStyle = alpha(tone(teak, -0.45), 0.5);
      g.lineWidth = 1.5;
      for (let x = -W; x < W * 2; x += 26) {
        g.beginPath();
        g.moveTo(x, H);
        g.lineTo(W / 2 + (x - W / 2) * 0.35, H * 0.68);
        g.stroke();
      }
      rrect(g, W * 0.82, H * 0.6, 60, 10, 3);
      fill(g, "#ffffff", 2);
      g.beginPath();
      g.moveTo(W * 0.86, H * 0.6);
      g.lineTo(W * 0.84, H * 0.48);
      g.lineTo(W * 0.9, H * 0.48);
      g.lineTo(W * 0.88, H * 0.6);
      g.closePath();
      fill(g, "rgba(230,240,255,0.7)", 1.5, "#90a4ae");
    },
    plaza_mayor(g, d, W, H) {
      const brick = col(d, 0, "#c0763e");
      sky(g, W, H, col(d, 2, "#87ceeb"), tone(col(d, 2, "#87ceeb"), 0.4));
      g.fillStyle = brick;
      g.fillRect(0, H * 0.12, W, H * 0.58);
      for (let i = 0; i < 9; i++) {
        const x = i * 74 + 10;
        for (let k = 0; k < 3; k++) {
          rrect(g, x, H * (0.16 + k * 0.11), 22, 26, 2);
          fill(g, "#3e2723", 2);
          g.fillStyle = "#1b1b1b";
          g.fillRect(x - 3, H * (0.16 + k * 0.11) + 26, 28, 3);
        }
        g.beginPath();
        g.moveTo(x - 12, H * 0.7);
        g.lineTo(x - 12, H * 0.57);
        g.arc(x + 11, H * 0.57, 23, Math.PI, 0);
        g.lineTo(x + 34, H * 0.7);
        g.closePath();
        fill(g, tone(brick, -0.45), 0);
      }
      floor(g, W, H, H * 0.7, "#cfc6b8", "#a89f91");
      g.strokeStyle = "rgba(120,110,100,0.35)";
      g.lineWidth = 1;
      for (let y = H * 0.72; y < H; y += 12) {
        g.beginPath();
        g.moveTo(0, y);
        g.lineTo(W, y);
        g.stroke();
      }
      // Una farola fernandina, como las de la plaza.
      const fx = W * 0.88;
      g.fillStyle = "#2f3b2f";
      g.fillRect(fx - 3, H * 0.38, 6, H * 0.36);
      rrect(g, fx - 10, H * 0.72, 20, 8, 2);
      g.fill();
      g.beginPath();
      g.moveTo(fx - 14, H * 0.36);
      g.lineTo(fx + 14, H * 0.36);
      g.lineTo(fx + 9, H * 0.27);
      g.lineTo(fx - 9, H * 0.27);
      g.closePath();
      fill(g, "#fff3b0", 2.5, "#2f3b2f");
      g.fillStyle = "#2f3b2f";
      g.fillRect(fx - 12, H * 0.25, 24, 4);
      ellipse(g, W * 0.2, H * 0.84, 8, 6);
      fill(g, "#9e9e9e", 1.5);
      ellipse(g, W * 0.2 + 7, H * 0.82, 4, 4);
      fill(g, "#7e7e7e", 1.5);
    },
    patio(g, d, W, H) {
      sky(g, W, H, "#ffd29a", col(d, 0, "#f6c177"));
      g.fillStyle = "#5d4037";
      g.fillRect(W * 0.12, H * 0.25, 14, H * 0.47);
      for (let i = 0; i < 26; i++) {
        const t = i / 25, x = W * 0.12 + 7 + Math.cos(t * Math.PI) * W * 0.16;
        const y = H * 0.24 - Math.sin(t * Math.PI) * H * 0.12 + ((i * 7) % 5) * 3;
        ellipse(g, x, y, 22, 14);
        fill(g, i % 3 ? col(d, 1, "#e74c3c") : col(d, 2, "#27ae60"), 0);
      }
      g.strokeStyle = "#8d6e63";
      g.lineWidth = 3;
      g.beginPath();
      g.moveTo(W * 0.6, H * 0.32);
      g.quadraticCurveTo(W * 0.75, H * 0.58, W * 0.92, H * 0.32);
      g.stroke();
      g.beginPath();
      g.moveTo(W * 0.62, H * 0.35);
      g.quadraticCurveTo(W * 0.75, H * 0.62, W * 0.9, H * 0.35);
      g.quadraticCurveTo(W * 0.75, H * 0.52, W * 0.62, H * 0.35);
      fill(g, "#e67e22", 2);
      floor(g, W, H, H * 0.7, "#d7ccc8", "#bcaaa4");
      g.strokeStyle = "rgba(150,120,110,0.4)";
      for (let x = 0; x < W; x += 40) {
        g.beginPath();
        g.moveTo(x, H * 0.7);
        g.lineTo(x - 30, H);
        g.stroke();
      }
      ellipse(g, W * 0.88, H * 0.82, 8, 6);
      fill(g, "#8d6e63", 1.5);
    },
    espacio(g, d, W, H, seed) {
      sky(g, W, H, col(d, 0, "#0b0d2b"), tone(col(d, 0, "#0b0d2b"), 0.15));
      stars(g, W, H, 120, seed, 1);
      ellipse(g, W * 0.8, H * 0.22, 46, 46);
      fill(g, col(d, 1, "#6c5ce7"), 3);
      g.beginPath();
      g.ellipse(W * 0.8, H * 0.22, 80, 16, -0.3, 0, TAU);
      g.lineWidth = 6;
      g.strokeStyle = alpha(col(d, 2, "#fdcb6e"), 0.85);
      g.stroke();
      floor(g, W, H, H * 0.74, "#b0b4c8", "#6d7189");
      const r = rng(seed + 1);
      for (let i = 0; i < 9; i++) {
        ellipse(g, r() * W, H * 0.78 + r() * H * 0.2, 10 + r() * 18, 4 + r() * 5);
        fill(g, "#8a8ea6", 1.5, "#5c6078");
      }
    },
    halloween(g, d, W, H, seed) {
      sky(g, W, H, col(d, 0, "#1e1336"), tone(col(d, 0, "#1e1336"), 0.2));
      stars(g, W, H, 40, seed);
      ellipse(g, W * 0.8, H * 0.2, 42, 42);
      fill(g, col(d, 2, "#f5f6fa"), 0);
      g.fillStyle = "#1b1b1b";
      for (const [x, y] of [[0.3, 0.18], [0.45, 0.12]]) {
        g.beginPath();
        g.moveTo(W * x, H * y);
        g.quadraticCurveTo(W * x - 12, H * y - 12, W * x - 24, H * y);
        g.quadraticCurveTo(W * x - 12, H * y - 4, W * x, H * y + 4);
        g.quadraticCurveTo(W * x + 12, H * y - 4, W * x + 24, H * y);
        g.quadraticCurveTo(W * x + 12, H * y - 12, W * x, H * y);
        g.fill();
      }
      floor(g, W, H, H * 0.72, "#3e2c1c", "#22180f");
      for (const [x, s] of [[0.1, 30], [0.86, 24], [0.75, 16]]) {
        const pk = col(d, 1, "#e67e22");
        for (const dx of [-0.5, 0, 0.5]) {
          ellipse(g, W * x + dx * s * 0.8, H * 0.8, s * 0.55, s * 0.5);
          fill(g, pk, 2);
        }
        g.fillStyle = "#2e7d32";
        g.fillRect(W * x - 2, H * 0.8 - s * 0.6, 4, 8);
        g.fillStyle = "#ffeb3b";
        for (const dx of [-0.25, 0.25]) {
          g.beginPath();
          g.moveTo(W * x + dx * s, H * 0.78);
          g.lineTo(W * x + dx * s - 5, H * 0.8);
          g.lineTo(W * x + dx * s + 5, H * 0.8);
          g.closePath();
          g.fill();
        }
      }
    },
    navidad(g, d, W, H, seed) {
      const wall = col(d, 0, "#7a1f1f");
      sky(g, W, H, tone(wall, -0.1), tone(wall, 0.05));
      lights(g, W, 26, 18, seed, ["#f1c40f", "#e74c3c", "#2ecc71", "#3498db"]);
      const tree = col(d, 1, "#1e8449");
      for (let k = 0; k < 3; k++) {
        g.beginPath();
        g.moveTo(W * 0.85, H * (0.12 + k * 0.13));
        g.lineTo(W * (0.85 - 0.08 - k * 0.025), H * (0.32 + k * 0.13));
        g.lineTo(W * (0.85 + 0.08 + k * 0.025), H * (0.32 + k * 0.13));
        g.closePath();
        fill(g, tree, 2.5);
      }
      star(g, W * 0.85, H * 0.11, 12);
      fill(g, col(d, 2, "#f1c40f"), 2);
      const r = rng(seed);
      for (let i = 0; i < 10; i++) {
        ellipse(g, W * 0.85 + (r() - 0.5) * W * 0.14, H * (0.25 + r() * 0.35), 4, 4);
        fill(g, ["#e74c3c", "#f1c40f", "#3498db"][i % 3], 1);
      }
      rrect(g, W * 0.04, H * 0.3, W * 0.2, H * 0.42, 6);
      fill(g, "#8d6e63", 3);
      rrect(g, W * 0.07, H * 0.42, W * 0.14, H * 0.3, 4);
      fill(g, "#2b1b12", 0);
      g.fillStyle = "#ff9800";
      ellipse(g, W * 0.14, H * 0.66, 18, 10);
      g.fill();
      for (let i = 0; i < 3; i++) {
        rrect(g, W * (0.06 + i * 0.06), H * 0.3, 14, 22, 3);
        fill(g, i % 2 ? "#ffffff" : "#e74c3c", 1.5);
      }
      floor(g, W, H, H * 0.72, "#6d4c41", "#4e342e");
      for (const [x, c] of [[0.74, "#3498db"], [0.92, "#f1c40f"]]) {
        rrect(g, W * x - 18, H * 0.66, 36, 26, 3);
        fill(g, c, 2);
        g.fillStyle = "#e74c3c";
        g.fillRect(W * x - 3, H * 0.66, 6, 26);
      }
    },
    carnaval(g, d, W, H, seed) {
      sky(g, W, H, col(d, 0, "#2d1b4e"), tone(col(d, 0, "#2d1b4e"), 0.15));
      lights(g, W, 30, 20, seed, [col(d, 1, "#e84393"), col(d, 2, "#fdcb6e"), "#00cec9", "#ffffff"]);
      lights(g, W, 80, 16, seed + 3, ["#fdcb6e", "#e84393", "#74b9ff"]);
      const r = rng(seed + 7);
      for (let i = 0; i < 70; i++) {
        g.save();
        g.translate(r() * W, r() * H * 0.95);
        g.rotate(r() * TAU);
        g.fillStyle = ["#e84393", "#fdcb6e", "#00cec9", "#74b9ff", "#55efc4"][i % 5];
        g.fillRect(-3, -1.5, 6, 3);
        g.restore();
      }
      g.lineWidth = 2;
      for (let i = 0; i < 6; i++) {
        g.strokeStyle = ["#e84393", "#fdcb6e", "#00cec9"][i % 3];
        g.beginPath();
        let x = r() * W;
        g.moveTo(x, 0);
        for (let y = 0; y < H * 0.6; y += 10) g.lineTo(x + Math.sin(y / 12 + i) * 8, y);
        g.stroke();
      }
      floor(g, W, H, H * 0.74, "#4a3d6b", "#2a2140");
    },
    hacienda(g, d, W, H) {
      const wall = col(d, 0, "#dfe4ea");
      sky(g, W, H, tone(wall, 0.2), wall);
      g.fillStyle = "#ffffff";
      for (const x of [0.2, 0.55, 0.85]) {
        g.fillRect(W * x - 50, 10, 100, 8);
        g.fillStyle = "rgba(255,255,255,0.35)";
        g.fillRect(W * x - 70, 18, 140, 40);
        g.fillStyle = "#ffffff";
      }
      const metal = col(d, 1, "#7f8c8d");
      for (let i = 0; i < 4; i++) {
        rrect(g, W * 0.05 + i * 56, H * 0.3, 50, H * 0.42, 3);
        fill(g, metal, 2);
        for (let k = 0; k < 3; k++) {
          rrect(g, W * 0.05 + i * 56 + 5, H * 0.33 + k * 38, 40, 32, 2);
          fill(g, tone(metal, 0.2), 1.5);
          g.fillStyle = "#4b5658";
          g.fillRect(W * 0.05 + i * 56 + 18, H * 0.33 + k * 38 + 12, 14, 4);
        }
      }
      rrect(g, W * 0.72, H * 0.24, 110, 54, 6);
      fill(g, col(d, 2, "#2c3e50"), 3);
      g.fillStyle = "#e74c3c";
      g.font = "700 30px Montserrat, sans-serif";
      g.textAlign = "center";
      g.textBaseline = "middle";
      g.fillText("47", W * 0.72 + 55, H * 0.24 + 29);
      floor(g, W, H, H * 0.72, "#b2bec3", "#95a5a6");
      g.strokeStyle = "rgba(255,255,255,0.4)";
      for (let x = 0; x < W; x += 50) {
        g.beginPath();
        g.moveTo(x, H * 0.72);
        g.lineTo(x, H);
        g.stroke();
      }
    },
    estudio(g, d, W, H) {
      const grey = col(d, 0, "#9aa5b1");
      const gr = g.createRadialGradient(W / 2, H * 0.55, 20, W / 2, H * 0.55, W * 0.7);
      gr.addColorStop(0, tone(grey, 0.35));
      gr.addColorStop(1, tone(grey, -0.35));
      g.fillStyle = gr;
      g.fillRect(0, 0, W, H);
      for (const s of [-1, 1]) {
        const x = W / 2 + s * W * 0.42;
        g.strokeStyle = "#2d3436";
        g.lineWidth = 3;
        g.beginPath();
        g.moveTo(x, H * 0.95);
        g.lineTo(x, H * 0.3);
        g.stroke();
        g.save();
        g.translate(x, H * 0.28);
        g.rotate(s * 0.5);
        rrect(g, -18, -14, 36, 28, 4);
        fill(g, "#2d3436", 2);
        ellipse(g, s * -2, 0, 10, 10);
        fill(g, col(d, 1, "#f5f6fa"), 0);
        g.restore();
      }
    },
    alfombra(g, d, W, H, seed) {
      const dark = col(d, 1, "#1b1b1b");
      sky(g, W, H, dark, tone(dark, 0.15));
      const r = rng(seed);
      g.fillStyle = "rgba(255,255,255,0.08)";
      for (let i = 0; i < 6; i++) {
        for (let k = 0; k < 4; k++) {
          g.font = "700 14px Montserrat, sans-serif";
          g.fillText("PREMIERE", i * 120 - 10 + (k % 2) * 60, 30 + k * 60);
        }
      }
      for (let i = 0; i < 9; i++) {
        const x = r() * W, y = r() * H * 0.6;
        g.fillStyle = "rgba(255,255,255,0.6)";
        star(g, x, y, 8 + r() * 10, 4, 0.2, 0);
        g.fill();
      }
      g.beginPath();
      g.moveTo(W * 0.38, H * 0.6);
      g.lineTo(W * 0.62, H * 0.6);
      g.lineTo(W * 0.85, H);
      g.lineTo(W * 0.15, H);
      g.closePath();
      fill(g, col(d, 0, "#b71c1c"), 0);
      for (const s of [-1, 1]) {
        for (let i = 0; i < 3; i++) {
          const x = W / 2 + s * W * (0.32 + i * 0.06), y = H * (0.7 + i * 0.09);
          g.fillStyle = col(d, 2, "#f1c40f");
          g.fillRect(x - 2, y - 30, 4, 30);
          ellipse(g, x, y - 32, 6, 6);
          g.fill();
        }
      }
    },
  };

  function behind(slot, drawing) {
    if (!drawing) return false;
    // El cono de la vergüenza rodea la cabeza: delante taparía la cara.
    if (slot === "cuello") return drawing.shape === "cono_verguenza";
    return slot === "cuerpo" && (drawing.shape === "capa" || drawing.shape === "alas");
  }

  function item(g, slot, drawing, anchor) {
    if (!drawing || !anchor) return;
    const table = { cabeza: HEAD, ojos: EYES, cuello: NECK, cuerpo: BODY }[slot];
    const draw = table && table[drawing.shape];
    if (!draw) return;
    g.save();
    g.lineJoin = "round";
    g.lineCap = "round";
    try {
      placed(g, anchor, (u, h) => draw(g, drawing, u, h || u * 0.8, anchor));
    } finally {
      g.restore();
    }
  }

  function backdrop(g, drawing, W, H, seed = 0) {
    const draw = BACKDROP[(drawing && drawing.shape) || "casa"] || BACKDROP.casa;
    g.save();
    g.lineJoin = "round";
    g.lineCap = "round";
    try {
      draw(g, drawing || {}, W, H, seed | 0);
    } finally {
      g.restore();
    }
  }

  const SHAPES = {
    cabeza: Object.keys(HEAD),
    ojos: Object.keys(EYES),
    cuello: Object.keys(NECK),
    cuerpo: Object.keys(BODY),
    fondo: Object.keys(BACKDROP),
  };

  return { item, backdrop, behind, SHAPES };
})();
