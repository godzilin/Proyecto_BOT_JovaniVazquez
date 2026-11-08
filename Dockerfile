# Imagen del bot de Discord. Pensada para x86_64 (p. ej. NAS con Intel N100).

# Node y @napi-rs/canvas (Skia) para pintar las escenas de canvas sin navegador
# (src/bot/assets/escena_node.cjs). Solo se copian el ejecutable y node_modules.
FROM node:22-slim AS escenas
WORKDIR /opt/escenas
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# Página del Jovaniverso (la Activity de `universo`, carpeta universo/): Vite la
# compila y a la imagen solo llega el resultado, ~1,3 MB (three.js y el robot). Si esta fase falla,
# falla la construcción y actualizar.sh vuelve a la imagen anterior.
FROM node:22-slim AS universo
WORKDIR /opt/universo
COPY universo/package.json universo/package-lock.json ./
RUN npm ci
COPY universo/ ./
# Las tragaperras de la plaza pintan en directo la escena de la clásica del bot: la página la importa
# de ../src/bot/assets (universo/src/casino/escena.ts), con sus símbolos y fuentes, y dónde va cada
# máquina (casino_plaza.json, que importa universo/src/casino/serie.ts).
COPY src/bot/assets/tragas.js src/bot/assets/casino_plaza.json /opt/src/bot/assets/
COPY src/bot/assets/slots/ /opt/src/bot/assets/slots/
COPY src/bot/assets/botes/fonts/luckiest-guy.woff /opt/src/bot/assets/botes/fonts/
COPY src/bot/assets/memes/fonts/MontserratBold.ttf /opt/src/bot/assets/memes/fonts/
RUN npm run build -- --outDir /opt/universo/dist

FROM python:3.13-slim

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
# La página compilada entra en el paquete como un asset más (package-data).
COPY --from=universo /opt/universo/dist ./src/bot/assets/universo
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
