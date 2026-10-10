# Instrucciones para agentes

Antes de tocar código, lee `Biblia.txt`: son las normas del proyecto (estructura,
nombres de comandos, documentación, pruebas). Están en español y mandan sobre
cualquier costumbre propia.

Lo que más se olvida:

- **Dinero:** todo lo que gane o gaste yapdollars pasa por `EconomyService`, tiene un
  tratamiento fiscal decidido y documentado (IRPF, juego, exento u otro impuesto),
  manda lo recaudado a la cuenta del Estado y, si es una interacción (botón o
  slash), llama a `renta.remind(bot, interaction)` para el aviso de la Renta. Detalle
  en la sección "Dinero, impuestos y la Renta" de `Biblia.txt`.
- **Logros:** cada funcionalidad del bot tiene logros asociados; una nueva no está
  terminada sin los suyos, y cuantos más, mejor (con gracia y sátira de la España
  actual). Se alimentan con `casino_play`, `track`, `note` o `note_for` de
  `bot.cogs.achievements`. La rareza sigue la escala por esfuerzo de la Biblia; los de
  suerte del casino se calibran con `docs/auditoria_logros.py`. Sección "Logros" de
  `Biblia.txt`, que tiene la lista de familias de logros que hay que cubrir.
- **Tienda y mascotas:** un artículo nuevo sigue la ficha de alta de la Biblia (tipo por lo
  que hace, pasillo por su tema). Todo resultado nuevo deja hablar a la mascota activa:
  pasa un `Moment` a `renta.hint` o llama a `mascotas.cameo`. Sección «Mascotas y cameos»
  de `Biblia.txt`.
- **Comandos:** un solo nombre en español de 8 caracteres como máximo, idéntico con `/`
  y con `.`, sin alias ni subcomandos (`/poner`, no `/play`). Sección "Cogs y comandos"
  de `Biblia.txt`.
- **Entre cogs:** las funciones puente buscan el cog con `bot.utils.cogs.find_cog`, nunca
  con `isinstance`. Lo que cruza de un cog a otro se prueba también con el bot real
  (`BotClient` + `INITIAL_EXTENSIONS`, ver `tests/integration/test_cog_bridges.py`): los
  cogs montados a mano no reproducen producción. Si algo falla solo en el bot desplegado,
  reprodúcelo así antes de culpar a Docker o a la base de datos. Sección "Pruebas y
  calidad" de `Biblia.txt`.
- **Botones:** contestan a Discord antes de tocar la base de datos. Sección siguiente.
- **Subagentes:** solo Haiku (lo mecánico) o Sonnet (programar), en su última versión, uno a
  la vez y para una tarea bien acotada. Si pueden hacerlo, se les delega y se espera, revisando
  siempre su trabajo antes de fusionar. Sección «Agentes y subagentes» de `Biblia.txt`.
- **Comprobar antes de entregar:** `ruff check src tests`, `ruff format --check src tests`
  y `python -m pytest -q`.

## Botones rápidos

Discord da 3 segundos para contestar a un clic (botón, menú o formulario). Si no
llega nada, el botón se queda «pensando» y acaba en «Esta interacción ha fallado»,
aunque el bot termine el trabajo después. Python casi nunca es lo lento. Lo lento
es la base de datos (sobre todo cuando espera a otra escritura), dibujar una
imagen, llamar a la API de Discord (roles, apodos) y esperar un `asyncio.Lock`.

Todo botón nuevo, o que se toque, sigue estas reglas. Las funciones están en
`bot.utils.interactions`.

1. **Si antes de enseñar el resultado hay que esperar algo de lo de arriba, la
   primera línea útil del callback es `await ack(interaction)`.** Las
   comprobaciones que solo miran memoria (¿es el dueño?, ¿está ocupado?) pueden ir
   antes y contestar directamente. El `ack` va también antes de coger un candado.
2. **Después de `ack`, se contesta con `edit(interaction, ...)` para cambiar el
   mensaje del botón y con `notify(interaction, "...")` para un aviso aparte**
   (privado por defecto, `ephemeral=False` para uno público). Nunca con
   `interaction.response.*`, que ya está gastado y lanza `InteractionResponded`.
   `edit` y `notify` valen igual sin `ack`, así que una función compartida puede
   usarlas sin saber de dónde la llaman.
3. **Si la respuesta es un mensaje privado nuevo** (abrir tu propio panel, una
   ficha, unas estadísticas), se usa `ack(interaction, new_message=True)`:
   Discord enseña «pensando…» en privado y `edit(interaction, ...)` lo rellena,
   también con los errores (`edit(interaction, content="...")`). Así no queda un
   «pensando…» colgado.
4. **Si el botón solo cambia memoria** (pasar de página con los datos ya
   cargados, el minijuego de `pala`), se contesta directamente con
   `interaction.response.edit_message`: es lo más rápido y no hace falta `ack`.
5. **Un botón que abre un formulario no puede hacer `ack`**: `send_modal` tiene
   que ser la primera respuesta. Las comprobaciones previas a abrirlo solo pueden
   mirar memoria. El `ack` va en el `on_submit` del formulario.
6. **Lo que no hace falta para enseñar el resultado va después de contestar:**
   logros (`casino_play`, `track`), `apuestas.record`, `renta.remind`, anuncios en
   el canal.
7. **Clics en ráfaga contra un mismo candado** (como el minijuego de `pala`): el
   clic que llega tarde se acepta fuera del candado. Si se acepta dentro, cada
   clic espera a que el anterior termine de hablar con Discord y los últimos pasan
   de 3 segundos.
8. **La base de datos se abre siempre con `bot.repositories.sqlite.connect`**, que
   activa el modo WAL. Una escritura no sincroniza el disco del NAS y una lectura
   no espera a una escritura. Un repositorio nuevo no llama a `sqlite3.connect` a
   mano.

Para probarlo, el doble de `tests/interaction_fakes.py` (`fake_interaction`) se porta
como Discord: una sola respuesta y `is_done()` de verdad. Con un `MagicMock` sin más,
`is_done()` siempre es falso y la prueba no ve si el botón contesta tarde o dos veces.
Un botón nuevo que toque la base de datos se añade a
`tests/integration/test_button_speed.py`, que pulsa botones del bot real y falla si
la primera conexión a SQLite llega antes que la respuesta a Discord.

## Rendimiento en el NAS (4 hilos)

El bot corre en un NAS con 4 hilos. Lo lento de un juego casi nunca es la lógica:
es dibujar, comprimir y mover imágenes. Antes de optimizar, se mide por tramos
(navegador, montar fotogramas, codificar el GIF) con el navegador ya abierto: el
primer dibujo tras arrancar Chromium siempre es más lento. Lo aprendido con la
ruleta, que pasó de 4-7 s a ~2 s por tirada:

- **Escenas de Chromium: `BrowserScene.render`.** Reparte los fotogramas entre
  `TABS` (2) pestañas que pintan a la vez, cada una en su proceso: el navegador
  tarda la mitad. Cada pestaña hace `setup` y su primer fotograma sale entero, y
  `renderFrames(states, first)` recibe el índice absoluto, así que una escena
  nueva no puede guardar estado entre lotes que no se reinicie en `setup`. Se
  comprueba que dos pestañas dan los mismos fotogramas que una. Más de 2
  pestañas no compensa: deja sin hilos al bot y a la codificación del GIF.
- **Sin navegador: `NodeScene`.** Una escena que solo usa canvas 2D se pinta
  en Node con `@napi-rs/canvas` (Skia, el mismo motor que Chrome) cargando la
  misma `escena.html` (`assets/escena_node.cjs`). La escena devuelve cada
  recuadro con `window.exportFrame(lienzo)` y no con `toDataURL`, y así Node lo
  manda en RGBA crudo, sin PNG. Con la moneda: arranca en 0,35 s (Chromium,
  2,9 s), ocupa 177 MB frente a 580 y la tirada baja de 1,3 a 1,05 s. Pinta
  lo mismo salvo algún borde de letra. Lo que queda es el raster de Skia, igual
  en los dos: para bajar más hay que pintar menos, no cambiar de motor. En
  Node, usar un lienzo como origen de `drawImage` lo copia entero cada vez.
- **En el navegador, lo caro es el PNG.** `toDataURL` comprime en un solo hilo
  (`toBlob` no ayuda sin GPU) y cada fotograma viaja en base64 a Python. Hay
  que devolver solo el recuadro que cambia y no marcar `full` sin necesidad: un
  marcador que cambia en cada fotograma obliga a mandarlos enteros. WebP sin
  pérdida pesa menos pero tarda más en comprimirse.
- **En Python, lo que no depende del orden va en varios hilos.** Pillow suelta el
  GIL al cuantizar y al descomprimir: `local_palette_gif` calcula las paletas
  con `QUANTIZE_THREADS` (4) y `browser_scene.assemble` descomprime los PNG igual.
  Lo que sí depende del fotograma anterior (comparar, pegar recuadros) va en orden.
  Los pools se crean dentro de la función, que ya corre en `asyncio.to_thread`.
- **Trabajar solo sobre lo que cambia.** Cuantizar la caja que cambia y no el
  fotograma entero: sin tramado, el resultado es idéntico. Antes de tocar
  `bot.utils.gif` se comprueba que el GIF sale con los mismos bytes.
- **Menos fotogramas donde no se notan.** Una fase rápida (la bola en la pista) va
  a 20 fps sin que se vea; la cámara lenta se hace con fotogramas más largos, no
  con más fotogramas.
- **Comprobar que llega al NAS.** La imagen instala el paquete y borra `src/`:
  un archivo nuevo de `assets/` tiene que entrar en `package-data` de
  `pyproject.toml` (lo vigila `tests/unit/test_packaging.py`). Si una escena no
  llega, el juego se dibuja con Pillow sin dar error: así estuvieron la moneda,
  los dados y la ruleta. Lo mismo con la memoria: cada Chromium abierto suma
  ~170 MB (la ruleta, ~370 con sus dos pestañas) contra el `mem_limit` de
  `docker-compose.yml`; un juego nuevo con navegador se suma a la cuenta de ahí.
- **Tapar la espera.** Si el dibujo tarda más de un segundo, la jugada se enseña
  ya («🎲 No va más…» con los botones apagados) y el GIF llega después. El cambio
  va en paralelo al dibujo, nunca antes.
- **Mejor aún, pintar antes del clic.** Si el resultado de la próxima jugada se
  puede sortear por adelantado sin enseñarlo (la moneda: `CoinGame.upcoming`),
  la mesa pinta en segundo plano los GIF posibles (uno por botón) mientras se ve
  el GIF actual, y al pulsar solo queda subirlo (`CoinView.prepare`). En la
  moneda, del clic al GIF pasó de ~1,1 s de dibujo a 0. Los dibujos por
  adelantado no se cancelan a medias (el pintor de Node no admite cortar uno):
  van de uno en uno y cada uno comprueba antes de empezar si sigue valiendo.

## Dos repositorios

- `godzilin/Proyecto_BOT_JovaniVazquez` es el del bot: su `main` es lo que se despliega.
- `Yeyo-Yeyex/Proyecto_BOT_JovaniVazquez_forkyeyo` es un fork que se usa para dar
  contexto en cada chat. Se trabaja aquí.
- Al terminar un cambio, los dos `main` tienen que quedar con la versión más
  actualizada. Primero se trae el `main` de godzilin (`git remote add upstream
  https://github.com/godzilin/Proyecto_BOT_JovaniVazquez`, `git fetch upstream main`) y
  se fusiona con el del fork, resolviendo conflictos y pasando las comprobaciones. El
  resultado se sube al `main` del fork.
- El agente no tiene permiso de escritura en el repo de godzilin. Para llevar el `main`
  del fork allí, se le da a la persona el enlace para abrir la PR desde la web:
  https://github.com/godzilin/Proyecto_BOT_JovaniVazquez/compare/main...Yeyo-Yeyex:Proyecto_BOT_JovaniVazquez_forkyeyo:main
  Junto al enlace va el título y la descripción listos para pegar (ver abajo).

## Títulos y descripciones de PR

El título y la descripción de cada PR acaban en Discord: al desplegar, el bot publica
los PR nuevos como «📜 Novedades del bot», con la lista de títulos y, en modo
detallado, la descripción de cada uno (README, «Novedades»). Lo leen los miembros del
servidor, no programadores.

- **Título:** qué cambia para quien usa el bot, en español y sin jerga, con la forma
  «Funcionalidad: qué cambia» y el comando entre comillas invertidas si lo hay. 70
  caracteres como mucho: GitHub corta el resto con «…» en el commit de fusión.
  - Bien: «Caballos: `caballo`, carreras con cuotas de verdad», «Pala: los clics ya
    no se pierden».
  - Mal: «Claude/adoring tesla ybmnco» (el que propone GitHub con la rama: cámbialo
    siempre), «fix», «Add pets system», «Caballos», «Refactor de deploy.py».
  - Un arreglo interno que nadie nota se dice igual, en cristiano: «Interno: pruebas
    del casino con el bot real».
- **Un tema por PR.** Cada PR es una línea del aviso; si un PR junta tres cosas, el
  título las nombra todas o se parte en tres.
- **La PR del fork a godzilin** se salta en el aviso si trae otros PR dentro, pero si
  lo que trae llegó al `main` del fork sin PR, su título es la única línea que saldrá:
  resume todo lo nuevo («Caballos y una sola semana para el IRPF»), nunca «Caballos»
  ni «Merge main».
- **Asuntos de commit** en español y legibles: si un PR queda con el título
  automático, el aviso usa los asuntos de sus commits.
- **Descripción** (sale en Discord en el aviso detallado y la lee también quien
  revisa): primero qué cambia para quien usa el bot, en párrafos cortos; luego por
  qué, cómo se ha probado y lo que hay que vigilar al desplegar (logros retroactivos
  que se cobran de golpe, cambios de cifras de la economía, pasos a mano en el NAS).
  Sin listas de archivos tocados: eso ya lo enseña el diff. Nada que no deba leer el
  servidor (rutas del NAS con datos, IDs privados). La firma de Claude Code se quita
  sola al publicarla.
