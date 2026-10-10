"""Pruebas de `actualizar.sh`, la autoactualización del bot en el NAS.

Montan un repositorio "origin" y un clon en un directorio temporal, y ponen
en el PATH un `docker` falso que apunta cada llamada en un archivo y simula
el estado del contenedor con variables de entorno. Así se comprueba la lógica
del script (cuándo reconstruye, cuándo vuelve atrás, cuándo no toca nada) sin
Docker ni red.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "actualizar.sh"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(cmd) for cmd in ("bash", "git", "flock")),
    reason="hacen falta bash, git y flock",
)

# `docker` falso: registra los argumentos y responde según FAKE_*. Para
# `docker run ... alpine/git:latest <args>` ejecuta el git real con <args>,
# como haría el contenedor.
FAKE_DOCKER = """#!/usr/bin/env bash
if [[ "$1" == run ]]; then
  echo "run alpine/git" >> "$FAKE_LOG"
  while [[ $# -gt 0 && "$1" != alpine/git:latest ]]; do shift; done
  shift
  exec git "$@"
fi
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "compose version") exit 0 ;;
  "compose build") [[ -n "${FAKE_BUILD_FAIL:-}" ]] && exit 1; exit 0 ;;
  "image inspect") exit 0 ;;
  "inspect -f")
    if [[ "$3" == *Running* ]]; then echo "${FAKE_RUNNING:-true}"
    else echo "${FAKE_RESTARTS:-0}"; fi
    exit 0 ;;
esac
exit 0
"""


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def entorno(tmp_path: Path) -> dict:
    """Origin con un commit, clon de trabajo y docker falso en el PATH."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@t")
    _git(origin, "config", "user.name", "t")
    shutil.copy(SCRIPT, origin / "actualizar.sh")
    (origin / ".gitignore").write_text(".despliegue/\n")
    (origin / "bot.txt").write_text("v1\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "v1")

    clon = tmp_path / "clon"
    _git(tmp_path, "clone", "-q", str(origin), str(clon))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)

    log = tmp_path / "docker.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_LOG": str(log),
        "ESPERA_ARRANQUE": "0",
    }
    return {"origin": origin, "clon": clon, "env": env, "log": log}


def _ejecutar(entorno: dict, **extra: str) -> subprocess.CompletedProcess:
    env = {**entorno["env"], **extra}
    return subprocess.run(
        ["bash", str(entorno["clon"] / "actualizar.sh")],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def _nuevo_commit(origin: Path, texto: str) -> str:
    (origin / "bot.txt").write_text(texto)
    _git(origin, "commit", "-q", "-am", texto)
    return _git(origin, "rev-parse", "HEAD")


def _llamadas(entorno: dict) -> str:
    return entorno["log"].read_text() if entorno["log"].exists() else ""


def _limpiar_llamadas(entorno: dict) -> None:
    entorno["log"].unlink(missing_ok=True)


def test_despliega_un_commit_nuevo(entorno):
    _ejecutar(entorno)  # primer despliegue: deja apuntado el commit actual
    _limpiar_llamadas(entorno)
    nuevo = _nuevo_commit(entorno["origin"], "v2")

    resultado = _ejecutar(entorno)

    assert resultado.returncode == 0
    estado = entorno["clon"] / ".despliegue"
    assert (estado / "commit").read_text().strip() == nuevo
    assert (entorno["clon"] / "bot.txt").read_text() == "v2"
    llamadas = _llamadas(entorno)
    assert "compose build --pull" in llamadas
    assert "compose up -d --force-recreate --no-build" in llamadas
    assert "tag bot-jovani-vazquez:latest bot-jovani-vazquez:anterior" in llamadas


def test_sin_cambios_no_toca_el_bot(entorno):
    _ejecutar(entorno)
    _limpiar_llamadas(entorno)

    resultado = _ejecutar(entorno)

    assert resultado.returncode == 0
    assert "compose build" not in _llamadas(entorno)
    assert "Sin cambios" in (entorno["clon"] / ".despliegue/actualizar.log").read_text()


def test_reconstruye_si_la_imagen_es_vieja(entorno):
    _ejecutar(entorno)
    _limpiar_llamadas(entorno)
    commit = entorno["clon"] / ".despliegue/commit"
    viejo = commit.stat().st_mtime - 8 * 86400
    os.utime(commit, (viejo, viejo))

    _ejecutar(entorno)

    assert "compose build --pull" in _llamadas(entorno)


def test_vuelve_atras_si_el_bot_no_arranca(entorno):
    _ejecutar(entorno)
    anterior = (entorno["clon"] / ".despliegue/commit").read_text().strip()
    _limpiar_llamadas(entorno)
    roto = _nuevo_commit(entorno["origin"], "roto")

    resultado = _ejecutar(entorno, FAKE_RESTARTS="3")

    assert resultado.returncode == 1
    estado = entorno["clon"] / ".despliegue"
    assert (estado / "fallido").read_text().strip() == roto
    assert (estado / "commit").read_text().strip() == anterior
    assert _git(entorno["clon"], "rev-parse", "HEAD") == anterior
    assert "tag bot-jovani-vazquez:anterior bot-jovani-vazquez:latest" in _llamadas(entorno)

    # Al día siguiente no reintenta el mismo commit roto...
    _limpiar_llamadas(entorno)
    _ejecutar(entorno)
    assert "compose build" not in _llamadas(entorno)

    # ...pero sí en cuanto llega un arreglo.
    arreglo = _nuevo_commit(entorno["origin"], "arreglo")
    _ejecutar(entorno)
    assert (estado / "commit").read_text().strip() == arreglo
    assert not (estado / "fallido").exists()


def test_si_falla_la_construccion_deja_el_bot_como_estaba(entorno):
    _ejecutar(entorno)
    anterior = (entorno["clon"] / ".despliegue/commit").read_text().strip()
    _limpiar_llamadas(entorno)
    _nuevo_commit(entorno["origin"], "v2")

    resultado = _ejecutar(entorno, FAKE_BUILD_FAIL="1")

    assert resultado.returncode == 1
    assert "compose up" not in _llamadas(entorno)
    assert _git(entorno["clon"], "rev-parse", "HEAD") == anterior
    assert not (entorno["clon"] / ".despliegue/fallido").exists()


def test_no_pisa_cambios_locales(entorno):
    _nuevo_commit(entorno["origin"], "v2")
    (entorno["clon"] / "bot.txt").write_text("tocado a mano\n")

    resultado = _ejecutar(entorno)

    assert resultado.returncode == 1
    assert (entorno["clon"] / "bot.txt").read_text() == "tocado a mano\n"
    assert "compose build" not in _llamadas(entorno)


def test_sin_git_en_el_sistema_usa_alpine_git(entorno):
    _ejecutar(entorno, GIT_EN_DOCKER="1")
    _limpiar_llamadas(entorno)
    nuevo = _nuevo_commit(entorno["origin"], "v2")

    resultado = _ejecutar(entorno, GIT_EN_DOCKER="1")

    assert resultado.returncode == 0
    assert "run alpine/git" in _llamadas(entorno)
    assert (entorno["clon"] / ".despliegue/commit").read_text().strip() == nuevo


def _carpeta_copiada(entorno: dict) -> Path:
    """Simula la carpeta del NAS: los archivos del bot sin `.git`, con un `.env`."""
    copia = entorno["clon"].parent / "copia"
    shutil.copytree(entorno["clon"], copia, ignore=shutil.ignore_patterns(".git"))
    (copia / "bot.txt").write_text("versión vieja copiada a mano\n")
    (copia / ".env").write_text("DISCORD_TOKEN=secreto\n")
    return copia


def test_carpeta_copiada_pide_convertir(entorno):
    copia = _carpeta_copiada(entorno)

    resultado = subprocess.run(
        ["bash", str(copia / "actualizar.sh")],
        env=entorno["env"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )

    assert resultado.returncode == 1
    assert "--convertir" in (copia / ".despliegue/actualizar.log").read_text()
    assert "compose build" not in _llamadas(entorno)


def test_convertir_enlaza_la_carpeta_y_despliega(entorno):
    copia = _carpeta_copiada(entorno)
    nuevo = _nuevo_commit(entorno["origin"], "v2")
    env = {**entorno["env"], "REPO": str(entorno["origin"]), "GIT_EN_DOCKER": "1"}

    resultado = subprocess.run(
        ["bash", str(copia / "actualizar.sh"), "--convertir"],
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )

    assert resultado.returncode == 0, (copia / ".despliegue/actualizar.log").read_text()
    assert _git(copia, "rev-parse", "HEAD") == nuevo
    assert (copia / "bot.txt").read_text() == "v2"
    assert (copia / ".env").read_text() == "DISCORD_TOKEN=secreto\n"
    assert (copia / ".despliegue/commit").read_text().strip() == nuevo
    assert "compose build --pull" in _llamadas(entorno)


def test_permisos_777_del_nas_no_cuentan_como_cambios(entorno):
    """En las carpetas compartidas del UGREEN todos los archivos salen 777."""
    _ejecutar(entorno, GIT_EN_DOCKER="1")
    for archivo in entorno["clon"].rglob("*"):
        if ".git" not in archivo.parts:
            archivo.chmod(0o777)
    nuevo = _nuevo_commit(entorno["origin"], "v2")

    resultado = _ejecutar(entorno, GIT_EN_DOCKER="1")

    assert resultado.returncode == 0, (entorno["clon"] / ".despliegue/actualizar.log").read_text()
    assert (entorno["clon"] / ".despliegue/commit").read_text().strip() == nuevo


# -- --solicitud: el comando `reinicio` de Discord ----------------------------------------


def _pedir_reinicio(entorno: dict) -> Path:
    buzon = entorno["clon"] / ".despliegue/buzon"
    buzon.mkdir(parents=True, exist_ok=True)
    (buzon / "solicitud.json").write_text('{"channel_id": 1, "user_id": 2}')
    return buzon


def test_solicitud_sin_nota_no_hace_nada_ni_escribe_log(entorno):
    resultado = _ejecutar_con(entorno, "--solicitud")

    assert resultado.returncode == 0
    assert _llamadas(entorno) == ""
    assert not (entorno["clon"] / ".despliegue/actualizar.log").exists()
    assert (entorno["clon"] / ".despliegue/buzon").is_dir()


def test_solicitud_reinicia_aunque_no_haya_commits_y_avisa_al_bot(entorno):
    _ejecutar(entorno)
    _limpiar_llamadas(entorno)
    buzon = _pedir_reinicio(entorno)

    resultado = _ejecutar_con(entorno, "--solicitud")

    assert resultado.returncode == 0
    assert "compose build --pull" in _llamadas(entorno)
    assert not (buzon / "solicitud.json").exists()
    assert (buzon / "en_curso.json").exists()  # el bot la recoge con el resultado
    estado, resumen = (buzon / "resultado.txt").read_text().splitlines()
    assert estado == "ok"
    assert resumen.startswith("Desplegado")


def test_solicitud_reintenta_un_commit_que_fallo(entorno):
    _ejecutar(entorno)
    _nuevo_commit(entorno["origin"], "roto")
    _ejecutar(entorno, FAKE_RESTARTS="3")
    _limpiar_llamadas(entorno)
    _pedir_reinicio(entorno)

    _ejecutar_con(entorno, "--solicitud")

    assert "compose build --pull" in _llamadas(entorno)


def test_solicitud_que_falla_deja_el_error_para_el_bot(entorno):
    _ejecutar(entorno)
    buzon = _pedir_reinicio(entorno)

    resultado = _ejecutar_con(entorno, "--solicitud", FAKE_BUILD_FAIL="1")

    assert resultado.returncode == 1
    estado, resumen = (buzon / "resultado.txt").read_text().splitlines()
    assert estado == "error"
    assert "no se ha podido construir" in resumen


def _ejecutar_con(entorno: dict, *args: str, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(entorno["clon"] / "actualizar.sh"), *args],
        env={**entorno["env"], **extra},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )


# -- Novedades: los PR desplegados, para el aviso de Discord ------------------------------


def _merge_pr(origin: Path, numero: int, rama: str, titulo: str) -> None:
    """Fusiona `rama` en la rama actual con el mensaje que pone GitHub."""
    mensaje = f"Merge pull request #{numero} from yeyo/{rama}"
    _git(origin, "merge", "-q", "--no-ff", rama, "-m", mensaje, "-m", titulo)


def _fusionar_pr(origin: Path, numero: int, rama: str, titulo: str, *commits: str) -> None:
    """Crea `rama` con `commits` y la fusiona en main como el botón de GitHub."""
    _git(origin, "checkout", "-q", "-b", rama)
    for asunto in commits:
        (origin / f"{rama.replace('/', '_')}.txt").write_text(asunto)
        _git(origin, "add", ".")
        _git(origin, "commit", "-q", "-m", asunto)
    _git(origin, "checkout", "-q", "main")
    _merge_pr(origin, numero, rama, titulo)


def _lineas_novedades(entorno: dict) -> list[str] | None:
    archivo = entorno["clon"] / ".despliegue/buzon/novedades.txt"
    return archivo.read_text().splitlines() if archivo.exists() else None


def _novedades(entorno: dict) -> list[str] | None:
    """Solo el texto de cada línea (el cuarto campo)."""
    lineas = _lineas_novedades(entorno)
    return None if lineas is None else [linea.split("\t")[3] for linea in lineas]


def test_despliegue_deja_los_titulos_de_los_pr_para_el_bot(entorno):
    _ejecutar(entorno)
    origin = entorno["origin"]
    _fusionar_pr(origin, 1, "pollo", "Pollo: carriles más rápidos", "pollo 1", "pollo 2")
    # GitHub titula con el nombre de la rama si nadie lo cambia: valen sus commits.
    _fusionar_pr(origin, 2, "claude/adoring-tesla-x1", "Claude/adoring tesla x1", "Mascotas")

    resultado = _ejecutar(entorno)

    assert resultado.returncode == 0
    # Número y rama de cada PR van delante para que el bot busque su descripción.
    assert _lineas_novedades(entorno) == [
        "#1\tyeyo/pollo\ttitulo\tPollo: carriles más rápidos",
        "#2\tyeyo/claude/adoring-tesla-x1\tcommit\tMascotas",
    ]


def test_el_pr_que_junta_el_fork_cede_ante_los_pr_que_trae(entorno):
    _ejecutar(entorno)
    origin = entorno["origin"]
    # El fork fusiona su PR en su main y luego todo su main entra de golpe.
    _git(origin, "checkout", "-q", "-b", "fork")
    _git(origin, "checkout", "-q", "-b", "caballos")
    (origin / "caballos.txt").write_text("caballos")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "caballos")
    _git(origin, "checkout", "-q", "fork")
    _merge_pr(origin, 3, "caballos", "Carreras de caballos")
    _git(origin, "checkout", "-q", "main")
    _git(origin, "branch", "-q", "-m", "fork", "main-del-fork")
    _git(origin, "merge", "-q", "--no-ff", "main-del-fork",
         "-m", "Merge pull request #46 from yeyo/main", "-m", "Caballos")  # fmt: skip

    _ejecutar(entorno)

    assert _novedades(entorno) == ["Carreras de caballos"]


def test_sin_pr_nuevos_no_hay_novedades(entorno):
    _ejecutar(entorno)
    _nuevo_commit(entorno["origin"], "v2 sin PR")

    _ejecutar(entorno)

    assert _novedades(entorno) is None


def test_un_despliegue_que_vuelve_atras_no_anuncia_nada(entorno):
    _ejecutar(entorno)
    _fusionar_pr(entorno["origin"], 4, "rota", "Esto tumba el bot", "roto")

    _ejecutar(entorno, FAKE_RESTARTS="3")

    assert _novedades(entorno) is None


def test_las_novedades_sin_publicar_se_juntan_con_las_nuevas(entorno):
    _ejecutar(entorno)
    _fusionar_pr(entorno["origin"], 5, "a", "Primera", "a")
    _ejecutar(entorno)
    _fusionar_pr(entorno["origin"], 6, "b", "Segunda", "b")

    _ejecutar(entorno)

    assert _novedades(entorno) == ["Primera", "Segunda"]
