#!/usr/bin/env bash
# Autoactualización del bot en el NAS.
#
# Uso: sudo ./actualizar.sh              (a mano o desde cron; ver
#                                         "Actualización automática" en el README)
#      sudo ./actualizar.sh --convertir  (solo la primera vez, si la carpeta del
#                                         bot se copió en vez de clonarse)
#      ./actualizar.sh --solicitud       (cron cada minuto: atiende `reinicio`)
#
# Qué hace, en orden:
#   1. Descarga la rama main de GitHub (git fetch) sin tocar nada aún.
#   2. Si no hay commits nuevos desde el último despliegue y la imagen tiene
#      menos de 7 días, termina sin hacer nada (el bot ni se entera).
#   3. Guarda la imagen actual como `bot-jovani-vazquez:anterior`, pone el
#      código en el último commit y reconstruye la imagen. Si la construcción
#      falla, el bot viejo sigue funcionando y se reintenta al día siguiente.
#   4. Reinicia el contenedor con la imagen nueva y espera 90 s. Si en ese
#      tiempo el bot se cae o entra en bucle de reinicios (un PR roto), vuelve
#      a la imagen anterior y no reintenta ese commit hasta que haya otro.
#   5. Si el bot nuevo arranca bien, deja en el buzón la lista de PR que trae
#      (`novedades.txt`) para que el bot la publique en Discord.
#   6. Borra las imágenes huérfanas para no llenar el disco.
#
# La reconstrucción semanal aunque no haya cambios trae la última versión de
# yt-dlp, que YouTube deja inservible cada pocas semanas.
#
# Requisitos: docker con el plugin compose (o docker-compose). Si el sistema
# no tiene git (el NAS UGREEN no lo trae), usa la imagen `alpine/git` con el
# mismo usuario dueño de la carpeta, así que los archivos no cambian de dueño.
# flock es opcional: evita dos ejecuciones a la vez si está instalado.
# Nunca toca `.env` ni los datos: el token está ignorado por git y la base de
# datos vive en el volumen `bot-jovani-vazquez-data`.
#
# Si alguien ha modificado a mano archivos versionados del clon (por ejemplo
# docker-compose.yml), el script se niega a actualizar para no pisarlos.
#
# Archivos que deja junto a este script (todos ignorados por git):
#   .despliegue/commit      commit desplegado ahora mismo
#   .despliegue/fallido     último commit que tumbó el bot (no se reintenta)
#   .despliegue/actualizar.log   registro de cada ejecución (últimas 2000 líneas)
#
# --convertir: la carpeta no es un clon de git (se copió a mano). La enlaza
# con el repositorio de GitHub y pone los archivos versionados en la última
# versión de main, sobrescribiendo los que haya. `.env`, `.despliegue/` y los
# demás archivos ignorados por git no se tocan. Después sigue como siempre.
#
# --solicitud: atiende el comando `reinicio` de Discord. El bot deja la nota
# `.despliegue/buzon/solicitud.json` (carpeta montada en el contenedor); si no
# hay nota, sale al momento sin escribir nada en el log, así que se puede
# lanzar cada minuto. Si la hay, actualiza aunque no haya commits nuevos (o el
# último ya fallara) y deja `resultado.txt` en el buzón para que el bot avise
# en el canal donde se pidió. Detalle del protocolo en bot/services/deploy.py.
#
# Novedades: entre el commit que había y el nuevo, cada `Merge pull request`
# aporta una línea con el título del PR. Los PR que juntan el main del fork
# (rama `main`) se saltan si hay otros PR dentro, que ya cuentan lo mismo con
# más detalle. Si un título es el que GitHub inventa con el nombre de la rama
# ("Claude/adoring tesla ybmnco"), se usan los asuntos de sus commits. Cada
# línea son cuatro campos separados por tabuladores: `#número`, `dueño/rama`
# de origen, `titulo` o `commit`, y el texto. Con el número y la rama, el bot
# pide a GitHub la descripción del PR. El bot lee el archivo, lo publica y lo
# borra (bot/services/deploy.py).
#
# Variables opcionales: RAMA (main), ESPERA_ARRANQUE (90), DIAS_RECONSTRUIR (7),
# REPO (el de godzilin), GIT_EN_DOCKER=1 para usar alpine/git aunque haya git.

# Todo va dentro de funciones y la llamada final cabe en una sola línea: bash
# lee los scripts a trozos mientras los ejecuta, y este se sobrescribe a sí
# mismo con el `git reset`. Así está leído entero antes de que cambie.

set -uo pipefail

# Globales para avisar al bot tras main(): modo, buzón y resumen final.
MODO_SOLICITUD=0
BUZON=""
RESUMEN=""

# Escribe un mensaje en el log y lo guarda como resumen para el bot.
fin() {
    RESUMEN="$*"
    echo "$*"
}

main() {
    local dir estado log rama espera dias contenedor imagen compose repo convertir=0
    case "${1:-}" in
        --convertir) convertir=1 ;;
        --solicitud) MODO_SOLICITUD=1 ;;
    esac
    dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    estado="$dir/.despliegue"
    BUZON="$estado/buzon"
    log="$estado/actualizar.log"
    rama="${RAMA:-main}"
    espera="${ESPERA_ARRANQUE:-90}"
    dias="${DIAS_RECONSTRUIR:-7}"
    contenedor="bot-jovani-vazquez"
    imagen="bot-jovani-vazquez"
    repo="${REPO:-https://github.com/godzilin/Proyecto_BOT_JovaniVazquez.git}"

    # cron arranca con un PATH mínimo; los NAS suelen tener docker en /usr/local/bin.
    export PATH="$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

    # El buzón lo monta el contenedor, cuyo usuario (uid 1000) tiene que poder
    # escribir y borrar en él. Se crea antes de arrancar el bot: si lo crease
    # Docker, sería de root y el bot no podría dejar notas.
    mkdir -p "$estado" "$BUZON"
    chmod 777 "$BUZON" 2>/dev/null || true

    # Modo cron de cada minuto: sin nota, nada que hacer (ni siquiera el log).
    if [[ "$MODO_SOLICITUD" == 1 && ! -f "$BUZON/solicitud.json" ]]; then
        return 0
    fi

    # Desde cron todo va al log; a mano, además, se ve en pantalla.
    if [[ -t 1 ]]; then
        exec > >(tee -a "$log") 2>&1
    else
        exec >>"$log" 2>&1
    fi

    # Una sola ejecución a la vez (por si alguien lo lanza a mano a las 5:00).
    exec 9>"$estado/lock"
    if command -v flock >/dev/null 2>&1 && ! flock -n 9; then
        echo "$(date '+%F %T') Ya hay otra actualización en marcha; salgo."
        return 0
    fi

    echo "===== $(date '+%F %T') ====="
    if [[ "$MODO_SOLICITUD" == 1 ]]; then
        mv -f "$BUZON/solicitud.json" "$BUZON/en_curso.json"
        echo "Reinicio pedido desde Discord: $(cat "$BUZON/en_curso.json" 2>/dev/null)"
    fi
    cd "$dir" || return 1

    if docker compose version >/dev/null 2>&1; then
        compose=(docker compose)
    elif command -v docker-compose >/dev/null 2>&1; then
        compose=(docker-compose)
    else
        fin "No encuentro docker compose ni docker-compose."
        return 1
    fi
    if ! docker info >/dev/null 2>&1; then
        fin "No puedo hablar con Docker. ¿Falta sudo? Prueba: sudo $dir/actualizar.sh"
        return 1
    fi

    # safe.directory evita el error de "dubious ownership" si cron corre como
    # otro usuario distinto del que clonó. core.fileMode=false ignora los
    # permisos: en las carpetas compartidas del NAS todo sale como 777 y git
    # lo tomaría por cambios locales en todos los archivos.
    local git opciones=(-c "safe.directory=$dir" -c core.fileMode=false)
    if command -v git >/dev/null 2>&1 && [[ -z "${GIT_EN_DOCKER:-}" ]]; then
        git=(git "${opciones[@]}")
    else
        # Sin git en el sistema: git dentro de un contenedor desechable, con el
        # usuario dueño de la carpeta para no dejar archivos de root.
        git=(docker run --rm --user "$(stat -c '%u:%g' "$dir")" -e HOME=/tmp
            -v "$dir:$dir" -w "$dir" alpine/git:latest "${opciones[@]}")
    fi

    if [[ ! -d "$dir/.git" ]]; then
        if [[ "$convertir" != 1 ]]; then
            fin "Esta carpeta no es un clon de git (se copió a mano)."
            echo "Ejecuta una vez: sudo $dir/actualizar.sh --convertir"
            return 1
        fi
        echo "Convirtiendo la carpeta en un clon de $repo ($rama)..."
        if ! "${git[@]}" init --quiet \
            || ! "${git[@]}" remote add origin "$repo" \
            || ! "${git[@]}" fetch --quiet origin "$rama" \
            || ! "${git[@]}" checkout --quiet --force -B "$rama" "origin/$rama"; then
            fin "No he podido convertirla; borra .git y vuelve a intentarlo."
            return 1
        fi
        echo "Hecho: la carpeta ya sigue a origin/$rama."
    fi

    if ! "${git[@]}" rev-parse --verify --quiet HEAD >/dev/null; then
        fin "git no funciona en esta carpeta; revisa el mensaje de arriba."
        return 1
    fi

    if ! "${git[@]}" diff --quiet HEAD --; then
        fin "Hay archivos tocados a mano en el NAS; no actualizo para no pisarlos."
        "${git[@]}" status --short --untracked-files=no
        return 1
    fi

    if ! "${git[@]}" fetch --quiet origin "$rama"; then
        fin "git fetch ha fallado (¿sin red o GitHub caído?)."
        return 1
    fi

    local nuevo actual fallido
    nuevo="$("${git[@]}" rev-parse "origin/$rama")"
    actual="$(cat "$estado/commit" 2>/dev/null || "${git[@]}" rev-parse HEAD)"
    fallido="$(cat "$estado/fallido" 2>/dev/null || true)"

    if [[ "$nuevo" == "$fallido" && "$MODO_SOLICITUD" != 1 ]]; then
        echo "origin/$rama sigue en ${nuevo:0:7}, que ya tumbó el bot; espero a un commit nuevo."
        return 0
    fi

    local motivo=""
    if [[ "$MODO_SOLICITUD" == 1 ]]; then
        motivo="pedido desde Discord (${actual:0:7} -> ${nuevo:0:7})"
    elif [[ "$nuevo" != "$actual" ]]; then
        motivo="commits nuevos (${actual:0:7} -> ${nuevo:0:7})"
    elif [[ -z "$(find "$estado/commit" -mtime "-$dias" 2>/dev/null)" ]]; then
        motivo="reconstrucción periódica (imagen de más de $dias días, yt-dlp al día)"
    else
        echo "Sin cambios (${actual:0:7})."
        return 0
    fi
    echo "Actualizo: $motivo"

    # Imagen de respaldo para volver atrás sin reconstruir.
    local hay_respaldo=0
    if docker image inspect "$imagen:latest" >/dev/null 2>&1; then
        docker tag "$imagen:latest" "$imagen:anterior" && hay_respaldo=1
    fi

    "${git[@]}" reset --quiet --hard "$nuevo"

    if ! "${compose[@]}" build --pull; then
        fin "La imagen no se ha podido construir; el bot sigue con la versión anterior."
        # Se deja el código como estaba para que el próximo intento parta limpio.
        "${git[@]}" reset --quiet --hard "$actual" 2>/dev/null || true
        return 1
    fi

    # --force-recreate deja el contador de reinicios a cero para la comprobación.
    if ! "${compose[@]}" up -d --force-recreate --no-build; then
        fin "docker compose up ha fallado."
        volver_atras
        return 1
    fi

    echo "Esperando ${espera} s para comprobar que el bot arranca..."
    sleep "$espera"

    local corriendo reinicios
    corriendo="$(docker inspect -f '{{.State.Running}}' "$contenedor" 2>/dev/null || echo false)"
    reinicios="$(docker inspect -f '{{.RestartCount}}' "$contenedor" 2>/dev/null || echo 99)"
    if [[ "$corriendo" != "true" || "$reinicios" != "0" ]]; then
        fin "El bot nuevo (${nuevo:0:7}) se caía al arrancar; he vuelto a la versión anterior."
        echo "En marcha: $corriendo, reinicios: $reinicios. Últimas líneas del log:"
        docker logs --tail 40 "$contenedor" 2>&1 || true
        echo "$nuevo" >"$estado/fallido"
        volver_atras
        return 1
    fi

    echo "$nuevo" >"$estado/commit"
    rm -f "$estado/fallido"
    apuntar_novedades "$actual" "$nuevo"
    docker image prune -f >/dev/null 2>&1 || true
    fin "Desplegado ${nuevo:0:7}: $("${git[@]}" log -1 --format=%s "$nuevo")"
}

# Vuelve a la imagen guardada antes de construir. El código queda en el
# commit desplegado para que el clon refleje lo que corre de verdad.
volver_atras() {
    if [[ "$hay_respaldo" != 1 ]]; then
        echo "No hay imagen anterior a la que volver; revisa el bot a mano."
        return
    fi
    echo "Vuelvo a la imagen anterior (${actual:0:7})."
    docker tag "$imagen:anterior" "$imagen:latest"
    "${git[@]}" reset --quiet --hard "$actual" 2>/dev/null || true
    "${compose[@]}" up -d --force-recreate --no-build || echo "No he podido arrancar la imagen anterior; revisa el bot a mano."
}

# Escribe en la salida una línea por PR fusionado entre $1 y $2 (ver
# "Novedades" arriba). Usa el `git` de main().
listar_novedades() {
    local merge asunto numero dueno rama titulo auto pr
    local -a propios=() paquetes=()
    while IFS=$'\t' read -r merge asunto; do
        [[ "$asunto" =~ ^Merge\ pull\ request\ \#([0-9]+)\ from\ ([^/]+)/(.+)$ ]] || continue
        numero="${BASH_REMATCH[1]}"
        dueno="${BASH_REMATCH[2]}"
        rama="${BASH_REMATCH[3]}"
        pr="#$numero"$'\t'"$dueno/$rama"
        # El título del PR es la primera línea no vacía del cuerpo del merge.
        titulo="$("${git[@]}" log -1 --format=%b "$merge" | sed -n '/[^[:space:]]/{p;q;}')"
        auto="${rama//[-_]/ }"
        if [[ -z "$titulo" || "${titulo,,}" == "${auto,,}" ]]; then
            # Título de relleno: lo que cuentan sus commits, sin los merges.
            while IFS= read -r titulo; do
                [[ -n "$titulo" ]] && propios+=("$pr"$'\tcommit\t'"$titulo")
            done < <("${git[@]}" log --no-merges --reverse --format=%s "$merge^1..$merge^2")
            continue
        fi
        if [[ "$rama" == main ]]; then
            paquetes+=("$pr"$'\ttitulo\t'"$titulo")
        else
            propios+=("$pr"$'\ttitulo\t'"$titulo")
        fi
    done < <("${git[@]}" log --merges --reverse --format=$'%H\t%s' "$1..$2")
    if ((${#propios[@]} == 0)); then
        propios=("${paquetes[@]}")
    fi
    # Un mismo texto sale una vez aunque llegue por dos PR (el del fork y el suyo).
    ((${#propios[@]})) && printf '%s\n' "${propios[@]}" | awk -F'\t' '!visto[$4]++'
    return 0
}

# Añade las novedades de $1..$2 al buzón para que el bot las publique. Si el
# bot aún no había publicado las anteriores, se juntan.
apuntar_novedades() {
    local lista archivo="$BUZON/novedades.txt"
    lista="$(listar_novedades "$1" "$2")"
    [[ -n "$lista" ]] || return 0
    { cat "$archivo" 2>/dev/null; printf '%s\n' "$lista"; } >"$archivo.tmp"
    # El bot (uid 1000) tiene que poder borrarlo después de publicarlo.
    chmod 666 "$archivo.tmp" 2>/dev/null || true
    mv -f "$archivo.tmp" "$archivo"
    echo "Novedades para Discord:"
    cut -f1,4 <<<"$lista" | sed 's/^/  - /'
}

# Deja en el buzón el resultado para que el bot lo publique en Discord.
avisar_bot() {
    [[ "$MODO_SOLICITUD" == 1 && -f "$BUZON/en_curso.json" ]] || return 0
    local estado=error
    [[ "$1" == 0 ]] && estado=ok
    printf '%s\n%s\n' "$estado" "${RESUMEN:-Sin detalles.}" >"$BUZON/resultado.txt.tmp"
    chmod 666 "$BUZON/resultado.txt.tmp" 2>/dev/null || true
    mv -f "$BUZON/resultado.txt.tmp" "$BUZON/resultado.txt"
}

recortar_log() {
    local log="$1"
    [[ -f "$log" ]] || return 0
    if (($(wc -l <"$log") > 2000)); then
        tail -n 2000 "$log" >"$log.tmp" && mv "$log.tmp" "$log"
    fi
}

main "$@"; codigo=$?; avisar_bot "$codigo"; recortar_log "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.despliegue/actualizar.log"; exit "$codigo"
