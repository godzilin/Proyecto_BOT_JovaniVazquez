"""Buzón entre el bot y `actualizar.sh` para pedir un reinicio desde Discord.

El bot corre dentro de Docker y no puede (ni debe) reiniciarse a sí mismo: eso
exigiría darle el socket de Docker, que equivale a ser root en el NAS. En su
lugar deja una nota en una carpeta compartida con el NAS y es el propio NAS
quien actualiza y reinicia:

1. `reinicio` escribe `solicitud.json` (canal y autor).
2. Cada minuto, `actualizar.sh --solicitud` mira si hay nota. Si la hay, la
   renombra a `en_curso.json`, descarga `main`, reconstruye y reinicia.
3. Al terminar, el script escribe `resultado.txt`: `ok` o `error` en la
   primera línea y un resumen en la segunda.
4. El bot (el mismo o el recién arrancado) ve el resultado, lo publica en el
   canal de la petición y borra la nota y el resultado.

Aparte, cada vez que despliega commits nuevos (de noche o por `reinicio`), el
script deja `novedades.txt`: una línea por PR fusionado desde el despliegue
anterior. El bot lo recoge y lo borra (`take_news`); qué lleva cada línea y
cómo se completa con GitHub está en `bot.services.changelog`.

La carpeta es `.despliegue/buzon`, montada en el contenedor por
`docker-compose.yml`. Este módulo no depende de discord.py.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

#: Carpeta del buzón, relativa al directorio de trabajo (/app en Docker).
DEFAULT_MAILBOX = Path(".despliegue/buzon")

REQUEST_FILE = "solicitud.json"
RUNNING_FILE = "en_curso.json"
RESULT_FILE = "resultado.txt"
NEWS_FILE = "novedades.txt"

#: Una petición sin resultado tras este tiempo se da por perdida (el script se
#: cortó o el cron no está puesto) y deja pedir otra.
STALE_AFTER = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class DeployRequest:
    """Quién pidió el reinicio, desde qué canal y cuándo."""

    channel_id: int
    user_id: int
    requested_at: datetime

    def to_json(self) -> str:
        return json.dumps(
            {
                "channel_id": self.channel_id,
                "user_id": self.user_id,
                "requested_at": self.requested_at.isoformat(),
            }
        )

    @classmethod
    def from_json(cls, text: str) -> DeployRequest:
        data = json.loads(text)
        return cls(
            channel_id=int(data["channel_id"]),
            user_id=int(data["user_id"]),
            requested_at=datetime.fromisoformat(data["requested_at"]),
        )


@dataclass(frozen=True, slots=True)
class DeployResult:
    """Lo que escribió `actualizar.sh` al acabar."""

    ok: bool
    summary: str
    request: DeployRequest | None


class Mailbox:
    """Lee y escribe las notas del buzón. Toda la E/S de archivos está aquí."""

    def __init__(self, path: Path = DEFAULT_MAILBOX) -> None:
        self.path = path

    def pending(self, now: datetime) -> DeployRequest | None:
        """La petición en cola o en curso, si la hay y no está caducada.

        Las caducadas se borran: si el script se cortó a medias, no deben
        bloquear el comando para siempre.
        """
        for name in (RUNNING_FILE, REQUEST_FILE):
            file = self.path / name
            request = _read_request(file)
            if request is None:
                continue
            if now - request.requested_at > STALE_AFTER:
                logger.warning("Petición de reinicio caducada; la descarto: %s", request)
                file.unlink(missing_ok=True)
                continue
            return request
        return None

    def request(self, request: DeployRequest) -> None:
        """Deja la petición para el NAS.

        Raises:
            OSError: Si no se puede escribir (carpeta sin montar o sin permisos).
        """
        self.path.mkdir(parents=True, exist_ok=True)
        # Se escribe aparte y se renombra para que el script nunca lea una
        # nota a medio escribir.
        tmp = self.path / f"{REQUEST_FILE}.tmp"
        tmp.write_text(request.to_json(), encoding="utf-8")
        tmp.replace(self.path / REQUEST_FILE)

    def take_result(self) -> DeployResult | None:
        """Recoge el resultado del script, si ya lo hay, y vacía el buzón."""
        result_file = self.path / RESULT_FILE
        try:
            lines = result_file.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return None
        except OSError:
            logger.exception("No se pudo leer el resultado del reinicio")
            return None
        request = _read_request(self.path / RUNNING_FILE)
        for name in (RESULT_FILE, RUNNING_FILE):
            try:
                (self.path / name).unlink(missing_ok=True)
            except OSError:
                logger.exception("No se pudo borrar %s del buzón", name)
        status = lines[0].strip() if lines else ""
        summary = " ".join(line.strip() for line in lines[1:] if line.strip())
        return DeployResult(ok=status == "ok", summary=summary, request=request)

    def take_news(self) -> list[str] | None:
        """Recoge la lista de PR desplegados, si la hay, y la borra del buzón.

        Returns:
            Una línea del archivo por elemento, sin las vacías; `None` si no hay
            novedades pendientes (o el archivo no se pudo leer o borrar: mejor
            no publicarlas que publicarlas en bucle cada 15 s).
        """
        news_file = self.path / NEWS_FILE
        try:
            text = news_file.read_text(encoding="utf-8")
            news_file.unlink()
        except FileNotFoundError:
            return None
        except OSError:
            logger.exception("No se pudieron recoger las novedades del buzón")
            return None
        items = [line.strip() for line in text.splitlines() if line.strip()]
        return items or None


def _read_request(file: Path) -> DeployRequest | None:
    """Lee una nota; `None` si no existe o está estropeada."""
    try:
        return DeployRequest.from_json(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("Nota de reinicio ilegible: %s", file)
        return None
