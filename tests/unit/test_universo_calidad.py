"""La calidad adaptativa de la página (`universo/src/mundo/calidad.ts`) baja y vuelve a subir.

Ejecuta `Escalera` (la decisión, sin three ni DOM) con el `node` del sistema, que quita los
tipos de TypeScript él solo (`--experimental-strip-types`), y la pasea por guiones de fps.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

CALIDAD = Path(__file__).parents[2] / "universo" / "src" / "mundo" / "calidad.ts"
NODE = shutil.which("node")

# Cada guion es una lista de tramos: `{"fps": 30, "s": 20}` (fijo), `{"fps": [60, 40], "s": 600}`
# (según el escalón: el 0, el 1… y el último para el resto), `{"tiron": 0.9}` (un fotograma
# largo) o `{"callar": 4}`. Devuelve el escalón final, los cambios y el escalón más bajo.
SIMULAR = """
const { Escalera } = await import(process.argv[1]);
const guiones = JSON.parse(process.argv[2]);
const salida = guiones.map(({ escalones, tramos }) => {
  const e = new Escalera(escalones);
  let cambios = 0, peor = 0;
  const paso = (dt) => {
    if (e.medir(dt) !== 0) cambios++;
    peor = Math.max(peor, e.escalon);
  };
  for (const t of tramos) {
    if (t.callar) e.callar(t.callar);
    else if (t.tiron) paso(t.tiron);
    else for (let s = 0; s < t.s; ) {
      const fps = Array.isArray(t.fps) ? t.fps[Math.min(e.escalon, t.fps.length - 1)] : t.fps;
      paso(1 / fps);
      s += 1 / fps;
    }
  }
  return { final: e.escalon, cambios, peor };
});
console.log(JSON.stringify(salida));
"""


def simular(*guiones: dict) -> list[dict]:
    if NODE is None:
        pytest.skip("hace falta node")
    hecho = subprocess.run(
        [
            NODE,
            "--experimental-strip-types",
            "--no-warnings",
            "--input-type=module",
            "-e",
            SIMULAR,
            CALIDAD.as_uri(),
            json.dumps(guiones),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(hecho.stdout)


def test_baja_hasta_el_ultimo_escalon_y_vuelve_a_subir_con_margen() -> None:
    lento, holgado, justo = simular(
        {"escalones": 4, "tramos": [{"fps": 25, "s": 60}]},
        {"escalones": 4, "tramos": [{"fps": 25, "s": 60}, {"fps": 60, "s": 600}]},
        # Entre 45 y 55 no se toca nada: ni baja ni sube.
        {"escalones": 4, "tramos": [{"fps": 25, "s": 60}, {"fps": 50, "s": 600}]},
    )
    assert lento == {"final": 4, "cambios": 4, "peor": 4}
    assert holgado == {"final": 0, "cambios": 8, "peor": 4}
    assert justo["final"] == 4


def test_un_tiron_al_cargar_no_baja_la_calidad() -> None:
    callado, suelto = simular(
        # Montar el mundo: `callar` y después varios fotogramas larguísimos seguidos.
        {"escalones": 4, "tramos": [{"fps": 60, "s": 5}, {"callar": 4}] + [{"tiron": 0.6}] * 4},
        # Un tirón suelto sin avisar tampoco: la media se recupera antes de los 3 s.
        {"escalones": 4, "tramos": [{"fps": 60, "s": 5}, {"tiron": 0.9}, {"fps": 60, "s": 20}]},
    )
    assert callado["peor"] == 0
    assert suelto["peor"] == 0


def test_no_oscila_si_subir_hace_que_vaya_lento() -> None:
    # Va holgado en el escalón 1 y lento en el 0: sube, baja, y cada vez tarda el doble en volver
    # a probar. En una hora, pocos cambios (sin el freno serían ~200).
    [r] = simular({"escalones": 4, "tramos": [{"fps": [40, 60], "s": 3600}]})
    assert r["final"] == 1
    assert r["cambios"] <= 40
