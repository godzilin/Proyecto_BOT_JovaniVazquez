# El mundo por trozos (celdas)

Meta (Yeyo, 7/11/2026): el Jovaniverso acaba siendo Canarias entera (no a escala 1:1, pero
grande), sin cambiar de mundo entre islas, con interiores de pisos y negocios en los que se entra.
Con el mundo en un solo documento JSON eso no escala: se guarda, se valida, se deshace y se
descarga entero, con un tope de 2 MB (unos 15 000 objetos) y el terreno de una sola isla de 320 m.

Lo que no cambia: el formato de cada asset, objeto, zona y del terreno. Cambia cómo se parte, se
guarda y se transporta.

## Trozos

El mundo es un mapa de **trozos**, cada uno un JSON pequeño con su clave:

| Clave | Qué lleva |
|---|---|
| `zonas` | `{"zonas": {...}}`: los lugares de logros que se mueven desde el editor. |
| `teleportes` | `{"teleportes": [{nombre, x, z}, ...]}`: los sitios del menú Viajar (hasta 24). Sin el trozo, los de serie; con la lista vacía, ninguno. |
| `a/<id>` | Un asset (el mismo objeto que antes en `assets[<id>]`). |
| `c/<cx>/<cz>` | Una celda de `CELDA` = 80 m de lado: `{"objetos": [...], "terreno"?: {...}}`. |

- La celda `(cx, cz)` cubre `[cx·80, (cx+1)·80)` en x y lo mismo en z. Un objeto va en la celda
  de su `pos` (x, z), en coordenadas del mundo, no relativas a la celda.
- Su terreno, si lo tiene, es la rejilla de 41 × 41 vértices cada 2 m desde la esquina
  `(cx·80, cz·80)`, con los bordes repetidos en la celda de al lado. Las alturas van en enteros de
  16 bits en cm (±327 m) y los colores en un byte por vértice, igual que antes. Sin terreno en una
  celda, la fórmula de la isla de serie.
- 80 m porque la isla esculpible (320 m centrados en 0) son justo 4 × 4 celdas, de la −2 a la 1.
  No influye en el dibujo: `obras.ts` y `culling.ts` siguen con sus celdas medidas (128 y 96 m).
- Las celdas llegan como mucho a ±`CELL_LIMIT` = 500 (±40 km, un cuadrado de 80 km: `universo_editor.py`
  y `LIMITE_CELDAS` de `mundo/borde.ts`, que vigila `test_universo_borde_ts.py`), lo que acepta la
  sala (fase 3). Los objetos del editor siguen de −20 a 120 m de altura y el terreno, en cm con `int16`.

**Topes por trozo:** 1 MB y 4000 objetos por celda (la densidad es lo que pesa al dibujar). El
mundo entero no tiene tope de objetos ni de celdas. Los assets tienen un tope de cordura
(`MAX_ASSETS` = 2000) porque la página los descarga todos al entrar (ver «Fases»).

**Tope del mundo entero (temporal, hasta la fase 2):** el borrador pesa como mucho
`MAX_WORLD_BYTES` = 8 MB (la suma de sus trozos en JSON), porque hoy todo se baja entero: el bot
guarda el mundo publicado entero en memoria por servidor (el texto y su gzip; `mem_limit` de 2 GB) y
cada página lo descarga y lo monta entero al entrar. La isla de serie pesa ~170 KB
(`docs/volcar_mundo_serie.py`); un mundo de 60 000 objetos, ~8 MB, que son ~700 KB con gzip por el
túnel y ~250 ms de montar en un PC (`docs/medir_recarga.py`), más de un segundo en un móvil. Caben
unas 50 islas de serie. Se comprueba al guardar (todo o nada, en la misma transacción) y el editor
enseña en la barra de estado cuánto ocupa frente al tope. Un borrador que ya pasaba (de antes del
tope) se sigue pudiendo editar y publicar: solo se rechaza un guardado que lo haga crecer. Con la
fase 2 (cargar por cercanía) lo que pesa es lo cercano y este tope se sube o se quita.

## Guardar, publicar y versiones

Tablas de SQLite (`repositories/universo_mundo.py`):

- `universo_trozos_borrador (guild_id, clave, doc, refs, rev, …)`: el borrador, una fila por
  trozo. Cada trozo tiene su `rev`: dos editores solo chocan si tocan el mismo trozo. Un trozo
  borrado se queda con `doc` vacío (lápida) y sigue subiendo el `rev`, para que un editor
  desfasado no lo resucite. `refs` son los assets que usa una celda: no se guarda nada que deje
  un objeto sin su asset.
- `universo_trozos_publicados (guild_id, clave, version, doc, refs)`: solo los trozos que cambian
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

- `GET /api/mundo?guild_id=&v=N` → `{"version": N, "trozos": {clave: doc}}` (gzip). Con `v` de la versión publicada, el navegador lo guarda para siempre; sin ella, sin caché.
- `GET /api/mundo?guild_id=&v=N&desde=M` → `{"version": N, "desde": M, "trozos": {clave: doc | null}}`: solo
  los trozos que cambiaron después de M (`null` los borrados), con la misma caché y guardado en memoria
  por versión de partida. Si M ya no se conserva (se podó), el mundo entero, sin `desde`.
- `GET /api/editor/borrador` → `{"version": N, "trozos": {clave: {"doc": doc | null, "rev": r}}}`.
- `PUT /api/editor/borrador` `{guild_id, v, trozos: {clave: {"rev": r, "doc": doc | null}}}`
  → `{"revs": {clave: r}}`. 409 si algún `rev` no coincide (no se guarda nada).
- `POST /api/editor/publicar` `{guild_id}` → `{"version": N}`.
- `POST /api/editor/volver` `{guild_id, version}` → `{}`: el borrador pasa a ser esa versión.

En la página, el editor y el mundo siguen trabajando con el documento plano (`MundoDoc`): se parte
en trozos al guardar (`aTrozos`) y se junta al cargar (`deTrozos`), en `editor/trozos.ts`.

**Solo lo que cambia (10.10.4):**

- **El editor** (`editor/borrador.ts`): cada `commit` dice lo que ha editado en su sitio (`Toca`: los
  objetos elegidos, el asset elegido, los objetos que siguen al suelo tras un trazo) y el borrador ve
  solo lo demás (objetos nuevos, borrados o que cambian de celda, assets nuevos o borrados, zonas,
  teleportes y el terreno de cada celda). Solo esos trozos se pasan a JSON y se comparan; deshacer y
  rehacer leen solo los trozos del paso (`parchear`) y guardar compara solo los cambiados desde el
  último guardado. Un `commit` sin `Toca` compara el mundo entero, y en desarrollo cada `commit` rápido
  se compara con el entero (`Borrador.comprobar`): si no cuadra, lo dice en la consola y usa el entero.
  Lo prueba `docs/comparar_trozos.py` con un mundo de 120 000 objetos: el `commit` baja de ~110 ms a
  ~13 y deshacer de ~150 a ~13.
- **Al publicar**, la sala avisa (`{t: "mundo", version}`) y cada página espera hasta 3 s al azar, pide
  `?desde=` con la versión que tiene, la aplica sobre su documento (`parchear`) y rehace solo esas celdas
  (`Mundo.actualizarMundo`: `Obras.actualizar`, las colisiones, el culling y el terreno de esas celdas).
  Con assets cambiados, un grupo que pasa de suelto a instanciado o más de 64 celdas, se monta entero
  (sin volver a bajar nada). `docs/medir_recarga.py` lo mide y comprueba que da la misma escena que
  montar entero: en el mundo de serie, 2,5 ms y 1,2 KB por una celda frente a 16 ms y 28 KB; en uno de
  60 000 objetos, ~30 ms y 2,7 KB frente a ~245 ms y 700 KB.

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
se llama con el índice de celdas publicadas. El editor no se limita con el borde, que solo crece al
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

## Fases

1. **Trozos en el almacenamiento, el editor y la descarga** (esta). Los datos quedan ya con su
   forma final; lo que se publique desde ahora no hay que migrarlo otra vez.
2. **La página carga por cercanía:** pedir las celdas que hay alrededor y soltar las lejanas;
   terreno, colisiones y dibujo por celdas, con menos detalle a lo lejos. Assets bajo demanda (los
   que usan las celdas cargadas). Es lo que hace falta antes de la segunda isla.
3. **Red** (10.11.0, «La red (fase 3)» arriba): posiciones relativas a la región, interés por
   cercanía, el borde del mundo publicado y altura hasta 4000 m.
4. **Interiores y estado de cada jugador:** los interiores como trozos propios que se cargan al
   acercarse a la puerta. Lo que cambia jugando (muebles de tu piso, reparaciones, estanterías del
   Ultramarino) va en sus tablas, nunca en el documento del mundo.
