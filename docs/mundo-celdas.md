# El mundo por trozos (celdas)

Meta (Yeyo, 7/11/2026): el Jovaniverso acaba siendo Canarias entera (no a escala 1:1, pero
grande), sin cambiar de mundo entre islas, con interiores de pisos y negocios en los que se entra.
Con el mundo en un solo documento JSON eso no escala: se guardaba, se validaba, se deshacía y se
descargaba entero, con un tope de 2 MB (unos 15 000 objetos) y el terreno de una sola isla de 320 m.
Ahora se guarda por trozos (fase 1) y la página baja solo lo de alrededor (fase 2): el mundo no
tiene tope de peso ni de objetos, solo topes de cordura por trozo y por petición.

Lo que no cambia: el formato de cada asset, objeto, zona y del terreno. Cambia cómo se parte, se
guarda y se transporta.

## Trozos

El mundo es un mapa de **trozos**, cada uno un JSON pequeño con su clave:

| Clave | Qué lleva |
|---|---|
| `zonas` | `{"zonas": {...}}`: los lugares de logros que se mueven desde el editor. |
| `teleportes` | `{"teleportes": [{nombre, x, z}, ...]}`: los sitios del menú Viajar (hasta 24). Sin el trozo, los de serie; con la lista vacía, ninguno. |
| `a/<id>` | Un asset (el mismo objeto que antes en `assets[<id>]`). |
| `c/<cx>/<cz>` | Una celda de `CELDA` = 80 m de lado: `{"objetos": [...], "terreno"?: {...}, "gen"?: "..."}`. `gen` es la firma del generador de islas (`docs/generador-mundo.md`): solo reescribe las celdas que la llevan. |
| `i/<id>` | Un interior (fase 4): una habitación con sus objetos, en la que se entra por una puerta. |

- La celda `(cx, cz)` cubre `[cx·80, (cx+1)·80)` en x y lo mismo en z. Un objeto va en la celda
  de su `pos` (x, z), en coordenadas del mundo, no relativas a la celda.
- Su terreno, si lo tiene, es la rejilla de 41 × 41 vértices cada 2 m desde la esquina
  `(cx·80, cz·80)`, con los bordes repetidos en la celda de al lado: `{"alturas", "colores",
  "base"?}`. Las alturas van en enteros de 16 bits en cm **sobre la `base`** de la celda (un entero
  en cm; sin ella, 0): la altura de un vértice es `base + alturas[i]`, de −20 a 4000 m (lo que
  acepta la sala). Los colores, en un byte por vértice. Sin terreno en una celda, la fórmula de la
  isla de serie (y fuera de ella, mar). Ver «Terreno alto» abajo.
- 80 m porque la isla esculpible (320 m centrados en 0) son justo 4 × 4 celdas, de la −2 a la 1.
  Las celdas de dibujo de `obras.ts` siguen con sus medidas (128 m las construcciones, 96 m lo
  instanciado); el suelo sí va por celdas de 80 m.
- Las celdas llegan como mucho a ±`CELL_LIMIT` = 500 (±40 km, un cuadrado de 80 km: `universo_editor.py`
  y `LIMITE_CELDAS` de `mundo/borde.ts`, que vigila `test_universo_borde_ts.py`), lo que acepta la
  sala (fase 3). Los objetos del editor y el terreno van de −20 a 4000 m de altura (`MIN_Y`, `MAX_Y`).

**Sin tope del mundo entero (10.12.0).** La 10.10.7 puso uno de 8 MB porque la página lo bajaba y
lo montaba todo y el bot lo guardaba entero en memoria; con la carga por cercanía (fase 2) ya no
hace falta y se quitó, con el de 2000 assets (`MAX_ASSETS`). Lo que queda son topes de cordura
contra abusos, por trozo o por petición: 1 MB y 4000 objetos por trozo (`MAX_PIECE_BYTES`,
`MAX_CELL_OBJECTS`; el pincel de vegetación del editor cuenta por celda), 300 piezas por asset
(`MAX_PIECES`: lo que cuesta dibujar cada copia), 5000 trozos y 8 MB por guardado
(`MAX_SAVE_PIECES`, `MAX_EDITOR_BYTES`; el editor parte los grandes en tandas), 64 trozos por lote
de descarga (`MAX_LOTE`), 1 MB y 6000 triángulos por GLB y 200 MB de GLB entre todos los
servidores (el disco del NAS). El editor enseña en su barra cuánto ocupa el borrador, como dato.

## Guardar, publicar y versiones

Tablas de SQLite (`repositories/universo_mundo.py`):

- `universo_trozos_borrador (guild_id, clave, doc, refs, resumen, rev, …)`: el borrador, una fila por
  trozo. Cada trozo tiene su `rev`: dos editores solo chocan si tocan el mismo trozo. Un trozo
  borrado se queda con `doc` vacío (lápida) y sigue subiendo el `rev`, para que un editor
  desfasado no lo resucite. `refs` son los assets que usa una celda: no se guarda nada que deje
  un objeto sin su asset (se mira solo lo que cambia: los assets de las celdas que se guardan y,
  si se borra un asset, que ninguna celda lo use). `resumen` es, en las celdas con terreno, su
  punto más alto y 5 × 5 alturas cada 20 m (`cell_summary`): el horizonte de la página y la cima
  de los logros, sin leer el terreno entero.
- `universo_trozos_publicados (guild_id, clave, version, doc, refs, resumen)`: solo los trozos que cambian
  en cada publicación. Lo publicado en la versión N es, para cada clave, su fila más nueva con
  versión ≤ N.
- `universo_mundo_publicaciones (guild_id, version, author_id, published_at, bytes)`: una fila
  por publicación. Se guardan las 50 últimas; al podar, cada clave conserva su fila base.
- Las tablas viejas (`universo_mundo_borrador` y `universo_mundo_versiones`) se parten en trozos
  al arrancar, una sola vez, y se quedan renombradas con `_v1` como copia.

El editor guarda solo los trozos que han cambiado desde el último guardado, todos en una
transacción, y publica lo que haya en el borrador. El deshacer guarda en cada paso solo los trozos
que cambian, no una copia del mundo entero.

## Rutas

- `GET /api/mundo/indice?guild_id=&v=N` → `{"version": N, "zonas": doc | null, "teleportes": doc | null,
  "trozos": {clave: versión de su fila}}`: qué trozos (celdas y assets) hay y desde qué versión no han
  cambiado, más lo pequeño que va siempre. Unos 15 bytes por celda. Con `v` de una versión que se
  conserva, el navegador lo guarda para siempre; sin ella, la de ahora sin caché.
- `GET /api/mundo/trozos?guild_id=&t=c/0/0@7,a/pino@3` → `{"trozos": {clave: doc}}`: hasta 64 trozos,
  cada uno con la versión de su fila (la que da el índice), así que la URL no cambia nunca de
  contenido (`immutable`). Con `resumen=1`, `{"resumenes": {clave: base64}}`: solo el horizonte. 410 si
  alguna fila ya se ha podado: la página pide el índice otra vez.
- `GET /api/editor/indice` → `{"version": N, "trozos": {clave: rev}, "borrados": {clave: rev}, "docs": {clave: doc}}`:
  qué trozos tiene el borrador y su `rev` (las lápidas aparte), sin las celdas, y los documentos de lo que no es celda (assets,
  zonas, teleportes). Sale del índice `universo_trozos_borrador_indice` sin leer las celdas. Sin caché (`no-store`).
- `GET /api/editor/trozos?guild_id=&t=c/0/0,c/1/0` → `{"trozos": {clave: {"doc": doc | null, "rev": r}}}`: hasta 64 trozos
  del borrador; `doc` nulo en una lápida y `rev` 0 en lo que no existe.
- `GET /api/editor/usos?guild_id=&asset=id` → `{"celdas": [clave]}`: las celdas del borrador que usan un asset (lee solo `refs`).
- `PUT /api/editor/borrador` `{guild_id, v, trozos: {clave: {"rev": r, "doc": doc | null}}}`
  → `{"revs": {clave: r}}`. 409 si algún `rev` no coincide (no se guarda nada).
- `POST /api/editor/publicar` `{guild_id}` → `{"version": N}`.
- `POST /api/editor/volver` `{guild_id, version}` → `{}`: el borrador pasa a ser esa versión.

**En el servidor** (`cogs/universo/mundo.py`) no hay ningún mundo entero en memoria: el índice sale
de SQLite sin leer los documentos y cada lote, de sus filas por clave y versión. Lo servido se
guarda en una caché LRU de `CACHE_BYTES` = 48 MB para todos los servidores (el índice y su gzip por
versión, el texto de cada trozo y el gzip de cada lote: la entrada de todos los jugadores en el
mismo sitio pide los mismos), y todo lo pesado va en `asyncio.to_thread`. Los lugares de los logros
salen del índice (las zonas y la cima de los resúmenes). `docs/medir_mundo_servidor.py` lo mide con
un mundo sintético de 5000 celdas y decenas de clientes.

**En la página** (`mundo/cercania.ts`), el mundo llega de una fuente: la del servidor o una local
hecha de un `MundoDoc` (el de serie, el borrador del editor, el extraplano), con el mismo camino:

- Baja el índice al entrar y las celdas que hay a menos de 480 m de la cámara (menos con la
  calidad baja), lo más cercano antes, en lotes de 64 y dos a la vez, con los assets que usan y que
  aún no tiene (los GLB se bajan al montar, con su caché `immutable`). Las que se quedan a más de
  540 m se sueltan: sus mallas, su geometría si ya nadie la usa y sus círculos de colisión.
- Lo bajado se monta por tandas de 4 ms por fotograma (`trabajar`): obras (`obras.ts`, que rehace
  solo las regiones de dibujo que toca cada celda), suelo (`suelo.ts`) y colisiones (`colision.ts`,
  en una rejilla de cubos de 16 m). Al entrar, la pantalla de carga espera a que esté montado lo de
  alrededor (6 s como mucho).
- El suelo: cada celda cargada tiene su rejilla (la de su trozo, o en la isla sin terreno la de
  la fórmula). A menos de 150 m se dibuja entera (2 m), hasta 250 m uno de cada dos vértices, con un
  faldón que tapa las rendijas entre detalles. Más allá, hasta 900 m (donde la niebla lo tapa
  todo), el horizonte: teselas de 8 × 8 celdas con las 5 × 5 alturas del resumen de cada una (que se
  pide aparte, solo el resumen), una llamada de dibujo por tesela. Donde no hay nada cargado, el
  suelo es el del resumen (o el de la isla de serie), para que nadie caiga al vacío.
- Al publicarse otra versión (`{t: "mundo", version}` de la sala), cada página espera hasta 3 s al
  azar, pide el índice nuevo y solo vuelve a bajar las celdas cercanas, assets y resúmenes cuya
  versión ha cambiado. El editor hace lo mismo con su borrador en cada cambio (`cargarMundo`): solo
  se rehacen las celdas cuyo trozo ha cambiado.
- **Presupuesto de dibujo** (`mundo.ts`): si un fotograma pasa del 92 % de 120 llamadas o de
  250 000 triángulos, se acorta la distancia de dibujo de lo que no es suelo (`alcance`, hasta un
  25 %); si baja del 75 %, se devuelve poco a poco. En `obras.ts`, un asset va instanciado en una
  región de vegetación desde 16 copias (con 8 pasaba de 120 llamadas); las copias fundidas sueltan
  su copia en memoria al subir a la GPU.
- **Medido** (`docs/medir_universo.py --recorrido` con `docs/editor_local.py --sintetico`, 1280 × 720
  y 390 × 844 táctil, recorriendo el centro, los cuatro bordes y el cénit): con 3000 celdas de 60
  objetos (unos 180 000), como mucho 93 llamadas y 229 000 triángulos, ~150 celdas montadas y
  85-110 MB de memoria de JavaScript que no crece al moverse. La isla de serie, 95 llamadas y
  165 000 triángulos (antes, 90 y 159 000). Con 300 objetos por celda (uno cada 21 m²) lo de
  menos de 100 m ya pasa de 500 000 triángulos: eso es densidad del sitio, no tamaño del mundo
  (un mundo de 25 celdas así cuesta lo mismo que uno de 3000), y lo avisa el editor en su barra.
  El servidor (`docs/medir_mundo_servidor.py`, 5000 celdas y 30 clientes a la vez): índice de 61 KB
  (11 KB con gzip), ~110 ms por índice y ~360 ms por lote de 64 celdas, y la caché se queda en
  sus 48 MB.
- `tests/integration/test_universo_cercania.py` lo comprueba en Node con un mundo de 3600 celdas
  (solo baja las ~140 de alrededor, suelta las que deja atrás, una versión nueva solo baja lo
  cambiado, rehacer celdas da la misma escena que montar entero).

**El borrador del editor solo trabaja con lo que cambia (10.10.4)** (`editor/borrador.ts`): cada
`commit` dice lo que ha editado en su sitio (`Toca`: los objetos elegidos, el asset elegido, los
objetos que siguen al suelo tras un trazo) y el borrador ve solo lo demás (objetos nuevos, borrados o
que cambian de celda, assets nuevos o borrados, zonas, teleportes y el terreno de cada celda). Solo
esos trozos se pasan a JSON y se comparan; deshacer y rehacer leen solo los trozos del paso
(`parchear`) y guardar compara solo los cambiados desde el último guardado. Un `commit` sin `Toca`
compara el mundo entero, y en desarrollo cada `commit` rápido se compara con el entero
(`Borrador.comprobar`): si no cuadra, lo dice en la consola y usa el entero. Lo prueba
`docs/comparar_trozos.py` con un mundo de 120 000 objetos: el `commit` baja de ~110 ms a ~13 y
deshacer de ~150 a ~13. El documento del editor es el `MundoDoc` plano, que se parte en trozos al
guardar (`aTrozos`) y se junta al cargar (`deTrozos`), en `editor/trozos.ts`. Su `terreno` ya va por
celdas (clave `c/<cx>/<cz>` → terreno de la celda), igual que en los trozos: antes era la rejilla de
la isla entera y `deTrozos` tiraba el de las celdas de fuera.

**El editor por celdas** (`editor/borrador.ts`): al abrir solo baja el índice del borrador
(`/api/editor/indice`: el `rev` de cada trozo, los assets, las zonas y los teleportes), ninguna celda.
Las celdas llegan como en el juego: las que hay a menos de 480 m de la cámara, las más cercanas antes,
por lotes de 64 (`Borrador.cerca`, desde el `pintar` de la vista); sus objetos van al final de
`doc.objetos` (lo elegido sigue valiendo) y su terreno a `doc.terreno`. Las que pasan de 540 m se
sueltan si no tienen nada sin guardar, no están en el historial de deshacer y no hay nada elegido ni a
medias, ni la herramienta de terreno puesta (soltar cambia los índices de `doc.objetos`). El `MundoDoc`
solo lleva lo bajado.

- **Nunca se pisa una celda sin bajar.** El borrador sabe qué celdas existen sin tenerlas (`porBajar`,
  del índice). Si un cambio lleva algo a una de ellas (mover un objeto a 2 km escribiendo su x), `commit`
  lo deshace, pide la celda y lo avisa: con la celda ya bajada, el mismo cambio vale. Una celda que el
  servidor no tiene cuenta como bajada y vacía; si otro editor la crea mientras, el guardado da 409.
  Guardar sigue mandando solo los trozos cambiados con su `rev`.
- **Para quien cambie celdas lejos de la cámara:** `await borrador.asegurar(claves)` las baja y
  `borrador.bajada(clave)` dice si ya se puede tocar. Una celda que no existe no hace falta pedirla: se
  crea con el primer cambio. El pincel lo usa (ver «Terreno alto»).
- **Lo que no es de la cámara:** Ctrl+A elige solo entre lo bajado (lo dice la ayuda). La barra dice
  «Bajado: X MB, N celdas (M más sin bajar)»: el peso es solo de lo bajado. Borrar un asset pregunta al
  servidor (`/usos`) qué celdas lo usan, y si alguna no está bajada no deja (y el servidor tampoco lo
  guardaría: `MissingAssets`). Publicar y volver a una versión son del servidor y valen para el borrador
  entero; volver recarga el índice. Deshacer y rehacer solo tocan celdas del historial, que nunca se
  sueltan.
- **Lo que trae una celda y el editor no conoce** (campos nuevos) va en `MundoDoc.celdas` y se guarda tal
  cual al tocarla; una celda sin tocar no se manda nunca. `gen`, la marca del generador, se quita al
  guardarla desde el editor: ya está hecha a mano. El terreno no va ahí: va en `MundoDoc.terreno`.
- **Medido** con el mundo de `docs/editor_local.py --sintetico` (unos 32 KB por celda con terreno):
  abrir el editor con 3000 celdas bajaba 94 MB (279 ms de SQLite y unos 2 s solo en leer el JSON); ahora,
  un índice de 38 KB (8 ms). Con 400 celdas, 12,4 MB antes y 6 KB ahora: lo único que crece con el mundo
  es el índice, ~13 bytes por celda. Lo de alrededor de la cámara se baja después, como en el juego
  (~130 celdas). Lo prueban `test_universo_editor_celdas.py` (Node, un mundo de 3600 celdas contra un
  servidor falso, y el pincel en el borde con una celda sin bajar) y `test_universo_editor_bot.py` (las
  rutas).

## Terreno alto (la `base` de cada celda)

El Teide a escala 1:10 mide 371 m y los `int16` en cm solo llegan a ±327 m. En vez de cambiar el
formato, cada celda lleva un campo opcional `base` (entero, cm) y sus alturas van sobre ella: cabe
cualquier altura hasta los 4000 m de la sala, con 655,35 m de desnivel como mucho dentro de una
celda de 80 m (un 800 % de pendiente media: de sobra para cualquier isla). Sin `base`, 0: todo lo
guardado antes se lee igual sin migrar nada, y el editor solo la pone cuando la celda no cabe sin
ella (`terrenoDe` de `mundo/terreno.ts`: si no cabe, la más baja). El servidor la valida
(`_terrain`: entero, y `base + alturas` dentro de `MIN_Y`..`MAX_Y`) y la quita si es 0.

- **Bordes:** como `base` y alturas son enteros en cm, la altura absoluta de un vértice repetido es
  la misma en las dos celdas aunque sus bases sean distintas: no hay escalón.
- **Resumen** (`cell_summary`): la cima es `base + max` y las 25 muestras siguen siendo `int16`
  relativas, seguidas de la `base` en `int32` si no es 0 (54 bytes en vez de 50). La página
  (`ponerResumen`) acepta las dos longitudes; los resúmenes ya guardados (sin base) valen.
- **Generador del mundo:** escribe cada celda con su `base` según este contrato. Una celda con más
  de 655 m de desnivel no cabe: que la parta (o la recorte) él.

**El pincel esculpe cualquier celda** (`editor/pincel.ts`): trabaja sobre las rejillas por celda que
dibuja `suelo.ts` (`rejillas`), no sobre una rejilla de la isla. Al primer trazo en una celda sin
rejilla, la crea (`rejillaEditable`): con su terreno del documento si lo tiene, con la fórmula si es de
la isla y, si no, con el suelo que se ve ahí (el resumen o el fondo del mar), tomando el borde de la
vecina si ya existe. Cada paso calcula los vértices nuevos con el suelo de antes del paso y escribe
cada uno en todas las celdas que lo repiten (`escribirPaso`, con `celdasDelVertice`), así que un trazo
que cruza el borde lo deja igual en las dos. **Nunca toca una celda que existe y no se ha bajado**: un
vértice de una de ellas no se escribe en ninguna de las celdas que lo repiten (el borde se queda como
estaba en las dos), la celda se pide (`asegurar`) y se avisa; en cuanto llega, el mismo trazo ya la
esculpe. Lo mismo al sembrar vegetación. Mientras dura un trazo la vista no cambia de foco, así que no
se suelta ninguna celda a medio esculpir. Al soltar, cada celda tocada pasa al documento con su `base`;
si una se pasa de 655 m de desnivel, se sube lo más bajo y se avisa. «Volver a la isla de serie» quita
solo el terreno de las 16 celdas de la isla. `alturas` de `terreno.ts` es ya solo la fórmula de la isla
de serie (de ahí salen sus celdas sin terreno y el suelo de lo que aún no ha llegado). Lo prueba
`tests/integration/test_universo_terreno.py` (el Teide en el borde de dos celdas con bases distintas,
el resumen igual en la página y el servidor, el recorte del desnivel).

## La red (fase 3)

La sala (`bot.services.universo_sala`, con el protocolo en su docstring) pasa de ±300 m a las
celdas del editor (±40 km) y de 200 m a 4000 m de altura, y cada jugador recibe solo a los que
tiene cerca.

**Coordenadas.** La página manda su pose absoluta (`i32 x, u16 y, i32 z`, en dm; la altura, desde
−50 m) y la sala manda las de los demás **relativas a su región**: grupos `i16 rx, i16 rz, u8 n`
seguidos de `n` poses de 9 bytes con `i16` desde la esquina de la región. Así cada jugador sigue
pesando 9 bytes (lo mismo que con `int16` absoluto) y 1 dm de precisión en cualquier punto, y la
región cuesta 5 bytes por grupo. Medido en bytes por tick y por cliente con 50 jugadores:

| Con 50 jugadores | Bytes por tick | KB/s por cliente |
|---|---|---|
| Antes (`int16` absoluto, todos a todos) | 2 + 50 · 9 = 452 | 4,1 |
| `int32` absoluto, todos a todos | 2 + 50 · 13 = 652 (+44 %) | ~6 (calculado) |
| Por región, todos en la plaza (una región) | 1 + 5 + 50 · 9 = 456 | 4,2 |
| Por región, repartidos en 600 m | ~210 | 2,1 |
| Por región, repartidos en 20 × 20 km | casi nada | 0,0 |

**Interés por cercanía.** Las regiones miden `REGION_DM` = 160 m (dos celdas) y cada uno ve las
3 × 3 de alrededor de la suya; la región cambia al salirse de ella más de `HYSTERESIS_DM` = 10 m,
así que se ve seguro a quien esté a menos de 140 m y, como mucho, a unos 330 m. La página no
dibuja más que los 12 más cercanos y no pone nombre a más de 60 m, y la niebla se come lo que está
a 300 m. Codificar una vez sigue valiendo: cada región con cambios se codifica una vez por tick
(solo si alguien que no es quien se mueve la va a recibir) y quienes están en la misma región
reciben el mismo objeto `bytes`. A quien está solo no se le manda su propia pose.

- **Entrar o acercarse:** al cambiar de región (también al llegar o con un teleporte), al entrar y
  tras saltarse ticks por ir atrasado, uno recibe un mensaje de tipo 3 con todos los de sus 9
  regiones, quietos o no. Quien no viene en él deja de dibujarse.
- **Alejarse:** a quien veía a alguien que se sale de su vista se le manda `{t: "lejos", slots}`.
  La página vacía su estela (deja de dibujarlo) pero lo sigue teniendo: `entra`, `sale` y el menú
  de jugadores (`/api/jugadores`) siguen siendo de toda la sala.
- Los gestos (`near`), las tiradas cercanas (`announce(around_dm=...)`), el paseo y el descarte
  de poses imposibles miran las posiciones absolutas, como antes.

**Versión del protocolo.** El saludo lleva `v: 2` (`PROTOCOL`). Una página abierta desde antes
del despliegue no lo manda: la sala le contesta `{t: "error"}` pidiendo que recargue y la página
vieja no reintenta. Su pose de 9 bytes y su tipo 1 tampoco valdrían (la sala los descarta).

**El borde** (`mundo/borde.ts`): es la caja de las celdas del mundo publicado (las claves
`c/<cx>/<cz>`) más 140 m de mar, nunca menos que la isla de serie (±300 m) ni más que las celdas
del editor. `fijarBorde` lo pone al bajar el mundo y lo amplía con los trozos cambiados de una
publicación (una celda borrada no lo encoge hasta recargar). Con la carga por cercanía (fase 2),
se llama con las claves del índice. El editor no se limita con el borde, que solo crece al
publicar, sino con `LIMITE`.

**Cuánto cabe en una sala.** Medido con `docs/sala_local.py --medir N [--repartir lado]` (clientes
falsos de verdad por WebSocket en otro proceso, servidor atado a 4 hilos, 15 s), alternando el
código de antes y el de ahora en la misma tanda:

| Jugadores (8/11/2026, PC a 4 hilos, dos rondas) | CPU (% de un hilo) | Tick medio (ms) | KB/s por cliente |
|---|---|---|---|
| Antes: 50 en la plaza | 2,8-3,3 | 1,0-1,2 | 4,1 |
| Ahora: 50 en la plaza | 2,7-3,1 | 1,0-1,4 | 4,2 |
| Antes: 50 repartidos en 600 m | 3,0-4,1 | 1,0-1,3 | 4,0 |
| Ahora: 50 repartidos en 600 m | 2,8-4,2 | 1,1-1,5 | 2,1 |
| Ahora: 50 repartidos en 20 × 20 km | 2,0-2,1 | 0,3 | 0,0 |
| Ahora: 100 repartidos en 20 × 20 km | 3,8-4,0 | 0,8 | 0,0 |
| Ahora: 100 en la plaza | 5,3-5,8 | 2,0 | 8,2 |

Lo que gasta la sala con mucha gente ya no son los envíos sino recibir las poses: 10 por segundo
de cada uno, que crecen con cada jugador esté donde esté. El interés por cercanía quita los bytes y
los envíos cuando la gente está repartida, pero todos juntos en la plaza siguen viéndose todos
(cada uno, 9 bytes a todos los demás 10 veces por segundo). `MAX_PLAYERS` se queda en 50:
con 100, ni repartidos por todo el mundo se gasta lo mismo que 50 antes (un 25 % más), y todos
juntos gastan casi el doble y suben 0,8 MB/s por el túnel. Subirlo pide recibir menos (por
ejemplo, poses a menos ritmo de quien no se mueve), no repartir mejor.

Lo que queda para la página en un mundo de decenas de km (fase 2): las mallas fundidas guardan
los vértices en coordenadas del mundo con `float32`, que a 40 km tiene 4 mm de paso; si se nota
temblor lejos del centro, cada celda se dibuja desde su esquina.

## Interiores y estado de cada jugador (fase 4)

Un **interior** es una habitación en la que se entra por una puerta, sin cambiar de mundo ni de sala.
Es un trozo propio, `i/<id>`, que no está en ninguna celda:

```
"i/<id>"  {"nombre", "ancho": m, "fondo": m, "alto": m, "suelo": 0xRRGGBB, "pared": 0xRRGGBB,
           "piso"?: true, "objetos": [Objeto, ...]}
```

- Planta de 2 a 40 m, alto de 2 a 12 m, hasta 400 objetos (`MAX_ROOM_OBJECTS`). Los objetos van en
  coordenadas de la habitación (el centro del suelo es el 0) y usan assets normales (`refs`, como una
  celda: no se puede borrar un asset que usa un interior). La salida está en el centro de la pared +z.
- **Funciones nuevas de un asset** (la lista ampliable de `FUNCTIONS`): `puerta` (cada objeto dice a qué
  interior lleva en `datos.interior`) y `usable` con su `uso`: `mueble` (se pone y se quita; de serie,
  puesto) o `reparar` (de serie, roto; se arregla y vuelve a romperse a las 24 h). Cada usable lleva su
  número en el interior (`datos.n`, de 1 a `MAX_USABLES` = 24, sin repetirse): su estado se guarda por
  número, nunca por posición. Una función nueva se añade igual: `universo_editor.py`, `formato.ts`, sus
  campos en `editor/funciones.ts` y sus reglas en `bot.services.universo_interiores`.
- Con `piso`, quien tiene vivienda habitual en `pisos` (`HousingRepository.home_of`) entra en el suyo; el
  resto, en el **piso piloto**, que es de todos.

**Cómo se entra.** La página baja el interior (y los assets que usa y no tiene) de la misma fuente que el
mundo cuando el jugador está a menos de 40 m de una puerta, y lo olvida a 90 m. Con E pide entrar
(`POST /api/interior`, `bot.cogs.universo.interiores`) con las claves y versiones de la celda de la puerta, el
interior y los assets de sus usables. El servidor:

1. Busca la puerta en esa celda publicada (sin nada publicado, en el piso de serie,
   `src/bot/assets/universo_piso.json`) y mira con la **pose de la sala** que esté a menos de 4 m.
2. Decide el **lugar**: `<id>` (de todos) o `<id>@<dueño>` (un piso); en el extraplano, con `pruebas/`
   delante (allí la página dice el origen y los usables, porque el mundo es suyo; solo con `devtools`).
3. Pasa al jugador a la **capa** de ese lugar en la sala (`Salas.set_layer`): las regiones del interés por
   cercanía son de cada capa, así que dentro solo se ve a los del mismo lugar y los de fuera no ven a los de
   dentro. Cambiar de capa cuesta lo mismo que cambiar de región (un `lejos` y un tipo 3); el tick no
   cambia. `near` (gestos, tiradas de las tragaperras) tampoco cruza de capa. Si la sala se corta, la página
   dice en el saludo que sigue dentro (`dentro: true`) y vuelve a su capa; si ha recargado, se olvida.
4. Devuelve el estado de cada usable y si el jugador puede usarlos (en un piso, solo su dueño).

**La página** monta la habitación en el sitio de la puerta (`interiores/montaje.ts`): suelo, cuatro paredes
que solo se ven desde dentro (la cámara en tercera persona mira a través de la de detrás), la puerta de
salida, lo fijo fundido en una malla y cada usable en la suya. Todo va en la capa 1 de three
(`mundo/capas.ts`) y, mientras se está dentro, la cámara solo ve esa capa (con las luces, tu robot y los
demás): el mundo de fuera no gasta ni una llamada de dibujo, sombras incluidas, y no hay que ocultarlo ni
soltarlo (al salir está como estaba; la carga por cercanía sigue alrededor de la puerta). El suelo y el
borde pasan a ser los de la habitación (`mundo/dentro.ts`, que el jugador y la cámara usan en lugar de
`terreno.ts` y `borde.ts`) y solo chocan sus muebles (`soloEstos` de `colision.ts`).

**Usar.** `{accion: "usar", n}`: el servidor mira que el jugador esté dentro, a 3 m del usable (con la
pose de la sala), que el lugar sea suyo o de todos y que no use nada más de una vez cada 0,4 s; calcula el
estado nuevo, lo guarda y avisa a los demás del lugar (`{t: "uso", n, estado}` por la sala, JSON: es raro).

**Estado en tablas propias** (`bot.repositories.universo_interiores`, nunca en el documento del mundo):
`universo_interior_estado (guild_id, lugar, n, estado, user_id, updated_at)`, una fila por usable cambiado
de cada lugar. Lo de reparar se rompe solo al leerlo (`view`: arreglado hace 24 h o más, roto): no hay
ninguna tarea que simule nada. Un lugar tiene 24 filas como mucho, así que la tabla crece con los pisos de
los miembros, no con el uso.

**Coste.** En el servidor, entrar es leer un lote de trozos ya en la caché (`world_batch`) y una consulta
de la tabla; usar, una lectura y una escritura. Nada por tick. En la página, la habitación del piso de serie son 11
llamadas de dibujo y ~1.100 triángulos con sombras, y quita todo lo de fuera (medido abajo).

**Medido** (`docs/medir_universo.py`, vistas `pisos`, delante de la puerta, y `pisodentro`, dentro, en el
mismo sitio que antes de los interiores; 10/11/2026):

MEDIDAS

**El piso de serie** está al oeste de la plaza (el hueco del primer anillo de casas): un edificio con su
puerta y el interior `piso-plaza` (un salón con sofá y mesa, que se ponen y se quitan, y una lámpara y un
fregadero, que se arreglan). Lo usan el mundo de serie (`mundo/serie.ts`) y el extraplano (a la derecha de
la plaza, `SITIOS_PISTA.pisos`). Un mundo ya publicado no lo recibe solo: lo coloca un editor (las puertas
e interiores se editan en el editor de niveles: cada puerta elige su interior o crea uno, y ahí se cambian
sus medidas, colores y la lista de objetos; no se colocan en 3D).

**Lo que queda:** visitar el piso de otro miembro (el lugar `<id>@<dueño>` ya existe: falta decidir quién
puede entrar, por ejemplo con el dueño dentro o con una llave de `pisos`, y cómo se elige en la puerta);
editar los interiores en 3D y no solo con la lista; ver el estado de los muebles en la Confort de `casa`
(los muebles de verdad de la fase 2 de `docs/casa.md`); el Ultramarino (`docs/ultramarino.md`).

## Fases

1. **Trozos en el almacenamiento, el editor y la descarga** (hecha, 10.5.0). Los datos quedan ya con su
   forma final; lo que se publique desde ahora no hay que migrarlo otra vez.
2. **La página carga por cercanía** (hecha, 10.12.0): índice, celdas alrededor de la cámara,
   assets bajo demanda, suelo por celdas con horizonte, sin tope del mundo, y el editor por celdas
   (ver «Rutas»). El pincel esculpe cualquier celda y sin tope de altura («Terreno alto»).
3. **Red** (10.11.0, «La red (fase 3)» arriba): posiciones relativas a la región, interés por
   cercanía, el borde del mundo publicado y altura hasta 4000 m.
4. **Interiores y estado de cada jugador** (la base, hecha: «Interiores y estado de cada jugador
   (fase 4)» arriba): los interiores como trozos propios que se cargan al acercarse a la puerta, las
   puertas y los usables como funciones de un asset, las capas de la sala y el estado de lo que se usa
   en su tabla, nunca en el documento del mundo. Falta visitar el piso de otro, editarlos en 3D y el
   Ultramarino (`docs/ultramarino.md`).
