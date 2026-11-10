"""Reduce un trozo de un modelo de elevación (GeoTIFF en grados) a un PNG pequeño para el generador.

Lo usa `docs/generar_mundo.py` (`docs/generador-mundo.md`). Pensado para las teselas de
Copernicus DEM GLO-30 (1″ de paso, en grados, la esquina noroeste en la etiqueta GeoTIFF), que se
bajan sin registro (la de La Graciosa, `N29_00_W014_00`):

    curl -O https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N29_00_W014_00_DEM/Copernicus_DSM_COG_10_N29_00_W014_00_DEM.tif
    python -I docs/reducir_dem.py tesela.tif --sur 29.204 --norte 29.302 \\
        --oeste -13.571 --este -13.446 --semilla 29.2316 -13.5029 --paso 30 \\
        --salida docs/islas/la_graciosa.png

Sale un PNG en gris de 16 bits con la altura en decímetros (0 es mar), en una rejilla cuadrada de
`--paso` metros reales desde la esquina suroeste del recuadro, con la fila 0 al norte (como una
imagen). Solo se queda la tierra unida a `--semilla` (lat, lon): las islas de al lado salen como
mar.
El recuadro tiene que dejar mar de sobra alrededor (el generador baja el fondo en ~1,2 km reales).
"""

from __future__ import annotations

import argparse
import math
from array import array
from collections import deque
from pathlib import Path

from PIL import Image

#: Metros de un grado de latitud (y de longitud en el ecuador): basta para islas de pocos km.
GRADO = 111_320.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("tif", type=Path)
    for lado in ("sur", "norte", "oeste", "este"):
        ap.add_argument(f"--{lado}", type=float, required=True)
    ap.add_argument("--semilla", type=float, nargs=2, required=True, metavar=("LAT", "LON"))
    ap.add_argument("--paso", type=float, default=30.0, help="metros reales entre muestras")
    ap.add_argument("--tierra", type=float, default=0.5, help="metros: por debajo, mar")
    ap.add_argument("--salida", type=Path, required=True)
    a = ap.parse_args()

    im = Image.open(a.tif)
    escala = im.tag_v2[33550]  # ModelPixelScale: grados por píxel
    _, _, _, lon0, lat0, _ = im.tag_v2[33922]  # ModelTiepoint: esquina noroeste
    dx, dy = escala[0], escala[1]
    c0 = math.floor((a.oeste - lon0) / dx) - 1
    r0 = math.floor((lat0 - a.norte) / dy) - 1
    c1 = math.ceil((a.este - lon0) / dx) + 2
    r1 = math.ceil((lat0 - a.sur) / dy) + 2
    recorte = im.crop((c0, r0, c1, r1))
    ancho_px = c1 - c0
    px = array("f", recorte.tobytes())  # modo F: float32 en el orden de la máquina

    def dem(lat: float, lon: float) -> float:
        """Bilineal en el recorte."""
        fx = (lon - lon0) / dx - c0 - 0.5
        fy = (lat0 - lat) / dy - r0 - 0.5
        ix, iy = math.floor(fx), math.floor(fy)
        u, v = fx - ix, fy - iy
        p = lambda c, r: max(0.0, px[r * ancho_px + c])  # noqa: E731
        top = p(ix, iy) * (1 - u) + p(ix + 1, iy) * u
        bot = p(ix, iy + 1) * (1 - u) + p(ix + 1, iy + 1) * u
        return top * (1 - v) + bot * v

    lat_media = (a.sur + a.norte) / 2
    m_lat, m_lon = GRADO, GRADO * math.cos(math.radians(lat_media))
    ancho = int((a.este - a.oeste) * m_lon / a.paso) + 1
    alto = int((a.norte - a.sur) * m_lat / a.paso) + 1
    # Fila 0 al norte, como una imagen.
    h = [
        dem(a.norte - j * a.paso / m_lat, a.oeste + i * a.paso / m_lon)
        for j in range(alto)
        for i in range(ancho)
    ]
    # Solo la tierra unida a la semilla.
    si = round((a.semilla[1] - a.oeste) * m_lon / a.paso)
    sj = round((a.norte - a.semilla[0]) * m_lat / a.paso)
    if h[sj * ancho + si] <= a.tierra:
        raise SystemExit("La semilla cae en el mar: muévela tierra adentro.")
    unida = bytearray(ancho * alto)
    cola = deque([sj * ancho + si])
    unida[sj * ancho + si] = 1
    while cola:
        k = cola.popleft()
        j, i = divmod(k, ancho)
        for vj, vi in ((j - 1, i), (j + 1, i), (j, i - 1), (j, i + 1)):
            n = vj * ancho + vi
            if 0 <= vj < alto and 0 <= vi < ancho and not unida[n] and h[n] > a.tierra:
                unida[n] = 1
                cola.append(n)
    dm = array("H", (round(h[k] * 10) if unida[k] else 0 for k in range(ancho * alto)))
    Image.frombytes("I;16", (ancho, alto), dm.tobytes()).save(a.salida, optimize=True)
    tierra = sum(unida)
    print(
        f"{ancho} × {alto} muestras de {a.paso:g} m; tierra {tierra * a.paso**2 / 1e6:.1f} km², "
        f"máximo {max(dm) / 10:.0f} m; {a.salida.stat().st_size / 1024:.1f} KB"
    )


if __name__ == "__main__":
    main()
