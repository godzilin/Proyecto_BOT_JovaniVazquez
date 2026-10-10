# Imagen del bot de Discord. Pensada para x86_64 (p. ej. NAS con Intel N100).

# Node y @napi-rs/canvas (Skia) para pintar las escenas de canvas sin navegador
# (src/bot/assets/escena_node.cjs). Solo se copian el ejecutable y node_modules.
FROM node:22-slim AS escenas
WORKDIR /opt/escenas
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg y libopus: necesarios para reproducir música en canales de voz.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Se instala el paquete; las dependencias (incluida la última yt-dlp
# disponible en el momento de construir) salen de pyproject.toml.
COPY pyproject.toml ./
COPY src ./src
RUN pip install . && rm -rf /app/src /app/build

# Chromium sin ventana (y sus librerías del sistema) para dibujar las carreras
# de caballos, cara o cruz, los dados y la ruleta. Ocupa unos 300 MB de disco; si
# falta, el bot dibuja con Pillow. Las escenas (assets/*/escena.html) entran en el
# paquete por pyproject.toml: lo que no esté ahí no llega a la imagen.
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

# Node para las escenas que ya no necesitan Chromium (de momento, la moneda).
# Unos 150 MB de disco; si falta, esas escenas vuelven a Chromium.
COPY --from=escenas /usr/local/bin/node /usr/local/bin/node
COPY --from=escenas /opt/escenas/node_modules /opt/escenas/node_modules
ENV NODE_PATH=/opt/escenas/node_modules

# Usuario sin privilegios. La base de datos vive en /app/.data (volumen).
RUN useradd --create-home --uid 1000 bot \
    && mkdir -p /app/.data \
    && chown bot:bot /app/.data
USER bot
VOLUME /app/.data

CMD ["python", "-m", "bot"]
