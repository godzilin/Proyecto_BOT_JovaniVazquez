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
- Por ahora, las celdas llegan como mucho a ±`LIMITE_CELDAS` = 40 (±3,2 km), porque la red manda
  las posiciones en decímetros con `int16`. La altura sigue de −20 a 120 m.

**Topes por trozo, no por mundo:** 1 MB y 4000 objetos por celda (la densidad es lo que pesa al
dibujar). El mundo entero no tiene tope de objetos ni de celdas. Los assets tienen un tope de
cordura (`MAX_ASSETS` = 2000) porque la página los descarga todos al entrar (ver «Después»).

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

## Fases

1. **Trozos en el almacenamiento, el editor y la descarga** (esta). Los datos quedan ya con su
   forma final; lo que se publique desde ahora no hay que migrarlo otra vez.
2. **La página carga por cercanía:** pedir las celdas que hay alrededor y soltar las lejanas;
   terreno, colisiones y dibujo por celdas, con menos detalle a lo lejos. Assets bajo demanda (los
   que usan las celdas cargadas). Es lo que hace falta antes de la segunda isla.
3. **Red:** posiciones relativas a la celda (o `int32`) para pasar de ±3,2 km, y que cada jugador
   reciba solo a los que tiene cerca. Más altura (el Teide).
4. **Interiores y estado de cada jugador:** los interiores como trozos propios que se cargan al
   acercarse a la puerta. Lo que cambia jugando (muebles de tu piso, reparaciones, estanterías del
   Ultramarino) va en sus tablas, nunca en el documento del mundo.
