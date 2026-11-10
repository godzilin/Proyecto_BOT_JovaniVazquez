# Mundo como código: el generador de islas

La meta es Canarias entera sin cambiar de mundo (`docs/mundo-celdas.md`). Colocar a mano
100 000 objetos en el editor no se puede: el generador escribe las celdas a partir de datos y
reglas, y las personas solo pulen a mano los sitios con alma. Piloto: **La Graciosa** a escala
1:10 en horizontal y 1:5 en vertical, con Caleta de Sebo, colocada a 3,6 km de la isla de serie.

Es una herramienta de los desarrolladores: no la usa el bot, no tiene logros y **no se aplica
sola al mundo de ningún servidor**. Aplicarla en producción es decisión de los desarrolladores.

## Cómo se usa

Con `PYTHONPATH=src` y el Python del repo:

```
python docs/generar_mundo.py docs/islas/la_graciosa.json --db mundo.db --dry-run
python docs/generar_mundo.py docs/islas/la_graciosa.json --db mundo.db [--publicar]
```

- `--db` es la base de datos del mundo (la del bot, o la de `docs/editor_local.py`), `--guild` el
  servidor (1 en local) y `--semilla` la del azar (1 por defecto).
- Escribe en el **borrador**, por tandas de 100 trozos (`--tanda`), cada tanda en una transacción
  y leyendo del borrador solo sus claves (`draft_pieces`). Cada trozo pasa antes por la validación
  del servidor (`validate_piece`). Nada se publica salvo con `--publicar`, pensado para probar en
  local; en un servidor de verdad se revisa en el editor y se publica desde ahí.
- `--dry-run` dice qué celdas y assets crearía, reescribiría, dejaría igual y respetaría, sin
  guardar nada. Al acabar, lo mismo de lo que ha hecho.
- Para verlo en local: `python docs/editor_local.py --db mundo.db`, `npm run dev` en `universo/`
  y `?editor`. Las medidas, `python docs/medir_universo.py --recorrido --extra "&editor" --puntos
  docs/islas/recorrido_la_graciosa.json [--capturas DIR]`.

El código: la lógica está en `bot.services.universo_generador` (importable y con pruebas,
`tests/unit/test_universo_generador.py`); los datos y los scripts, en `docs/`:

| Archivo | Qué es |
|---|---|
| `docs/islas/la_graciosa.json` | Las reglas de la isla: escala, sitio en el mundo, costa, suelo, pueblos, vegetación. |
| `docs/islas/la_graciosa.png` | El relieve ya reducido: 405 × 364 muestras de 30 m, en decímetros (32 KB). |
| `docs/islas/kit_canario.json` | El kit de assets: casas, muro y vegetación. |
| `docs/islas/recorrido_la_graciosa.json` | Los puntos de `medir_universo.py --puntos`. |
| `docs/reducir_dem.py` | Saca el PNG de una tesela de un modelo de elevación. |
| `docs/generar_mundo.py` | Junta todo y escribe en el borrador. |

## Qué genera

**Suelo.** El relieve se pasa a la rejilla de cada celda (41 × 41 vértices cada 2 m) con
interpolación bilineal, así que dos celdas vecinas comparten el borde exacto. La tierra baja a
escala (`escala_vertical`) con un mínimo de 0,3 m; el mar baja desde −0,5 m junto a la orilla
hasta −10 m (el fondo del mar de serie, lo que hay donde no hay celdas) en 120 m del mundo, con un
`smoothstep`: ni escalón en la orilla ni en el borde de lo generado. Una celda que queda entera a
−10 m y sin objetos no se escribe. Si el relieve no deja mar de sobra alrededor, el generador se
niega (`Campo`).

**Colores.** Cada vértice lleva una pintura de la paleta del editor (`PALETA` de
`mundo/terreno.ts`), de la primera regla de `suelo` que se cumple: altura, pendiente y un ruido
propio para que las manchas no sean bandas. En La Graciosa: fondo del mar, arena en la orilla,
acantilado y roca por pendiente, tierra y ceniza en los conos, jable (duna) con manchas de hierba
seca en el llano. Las calles del pueblo van en `pintura_calle`.

**Pueblos.** Manzanas en rejilla girada (`giro`, `manzana`, `calle`) dentro de un círculo de borde
irregular (`radio`). Cada manzana tiene una fila de casas en cada fachada larga, mirando a la
calle, elegidas por peso (`casas`); de vez en cuando (`hueco`) un tramo de muro de piedra. Una
casa solo se pone si su huella está entre `alt` y no tiene más de `desnivel_max` de cuesta, y el
suelo bajo ella (más 0,8 m) se aplana a su altura media (la explanada): la puerta queda a ras y el
zócalo, que baja 1,5 m bajo el suelo, tapa lo que sobra en cuesta. Caleta de Sebo sale con 73
casas y 16 tramos de muro.

**Vegetación.** En cada celda, un candidato cada `paso` m con algo de desorden. Cada especie dice
en qué altura y pendiente vive, en qué zona (`campo` o `pueblo`: en el pueblo, nunca en la calle
ni en una casa) y con qué probabilidad; tamaño, giro y tinte varían. En La Graciosa, tabaibas en el
llano bajo, aulagas hasta las laderas y palmeras en los patios del pueblo.

**Kit canario** (`kit_canario.json`), todo formas básicas y pocas piezas: casa terrera (8 piezas,
96 triángulos), casa terrera ancha, casa de tejado a cuatro aguas, casa de dos plantas con balcón
de madera, muro de piedra, tabaiba, aulaga y palmera canaria. Las casas varían en carpintería
(verde, marrón, azul), zócalo y tamaño. En el JSON los colores van en `#RRGGBB` y `rot` se puede
omitir; `huellas` dice lo que ocupa cada casa en el trazado. Retocar el kit es cambiar ese JSON.

**Medido** (8 puntos de `recorrido_la_graciosa.json`, 1280 × 720 y 390 × 844 táctil, la isla sola
en un `editor_local.py` y publicada): como mucho **92 llamadas y 91 600 triángulos** (móvil, 81 y
77 500), 29-44 MB de memoria de JavaScript, ~150 celdas montadas. Presupuesto: 120 y 250 000. La
isla son 121 celdas, 8 assets y 1665 objetos (73 casas, 16 muros, 833 tabaibas, 740 aulagas y 3
palmeras), 994 KB de JSON; generarla, validarla y guardarla tarda 2,7 s. Las capturas y el JSON de
la medida se sacan con `--capturas docs/capturas_universo --json …` (la carpeta no va al repo).

## El contrato de `gen` (no pisar lo hecho a mano)

- Cada celda que escribe el generador lleva `"gen": "<isla>:<versión>:<hash>"`. El hash es de toda
  la entrada (reglas, kit, relieve, semilla y `VERSION` del generador), así que cambiar cualquier
  cosa cambia la firma. La validación del servidor lo acepta en una celda (texto de 1 a 80
  caracteres `[A-Za-z0-9_.:-]`) y lo guarda tal cual; la página no lo usa.
- **El generador** (`decidir`): crea las celdas que no existen; reescribe las que llevan `gen` (de
  la versión que sea) si cambian; deja las iguales; y **respeta** una celda sin `gen`, una lápida
  (una celda borrada a mano) y un asset que ya existe distinto (alguien retocó el kit en el
  editor: las celdas lo usan tal como esté). Nunca borra nada.
- **El editor** (lo hace su hilo): conserva los campos que no conoce de una celda que no toca y,
  al guardar una celda que sí ha tocado, no escribe `gen`. Así, retocar a mano una celda generada
  la saca del generador para siempre. Para devolvérsela, se borra en el editor… y eso deja una
  lápida, que también se respeta: hoy, para regenerar una celda tocada hay que quitar su fila del
  borrador a mano.
- Un riesgo hasta que el editor trabaje por celdas: hoy carga el borrador entero como `MundoDoc` y
  solo conoce el terreno de la isla de 320 m. Antes de abrir el editor sobre un mundo con islas
  generadas, hay que comprobar que guardar algo cerca no reescribe ni pierde el terreno de las
  celdas generadas.

## Alturas y `base`

El terreno guarda centímetros en `int16` (±327 m) y la validación de hoy lo limita de −20 a 120 m.
La Graciosa a 1:5 en vertical llega a 52 m y cabe sin más. Para lo que no quepa, el generador ya
escribe `base` (cm) en el terreno de la celda cuando sus alturas no caben en `int16`, con las
alturas relativas a ella (`terreno()`); la validación y la página lo aceptan cuando entre el
trabajo de «terreno alto». El piloto no lo usa.

## Fuentes y licencias

- **Relieve: Copernicus DEM GLO-30** (versión 2021), tesela `N29 W014`, del cubo público de AWS
  (`copernicus-dem-30m`, sin registro). Es un modelo de superficie (incluye casas y vegetación) de
  1″ (~30 m). Su licencia es gratuita para todo el mundo, también para productos derivados, que
  tienen que llevar este aviso:

  > produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space
  > GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved

  Comprobado en la página de la colección (dataspace.copernicus.eu, «COP-DEM») y en el registro de
  datos abiertos de AWS. El texto entero de la licencia (el PDF de la ESA) no se pudo leer desde
  aquí: antes de publicar en el servidor de verdad, conviene leerlo y poner el aviso en algún sitio
  que vean los jugadores (los créditos del Jovaniverso o el teleporte de la isla).
- En el repo solo está el dato reducido (`la_graciosa.png`, 32 KB), con la tierra de La Graciosa y
  el resto a 0 (Lanzarote, Montaña Clara y Alegranza salen como mar).
- Alternativas abiertas para otras islas: el MDT del IGN/CNIG (CC BY 4.0, «© Instituto Geográfico
  Nacional») y los de GRAFCAN (Canarias, más detalle). No se han usado.
- El kit, las reglas y el código son del proyecto.

## Añadir una isla

1. Bajar la tesela (o teselas) de Copernicus que la cubren (`N27`–`N29`, `W014`–`W019` para
   Canarias) y reducirla con `docs/reducir_dem.py`: recuadro con 1,5 km reales de mar de sobra por
   cada lado, `--semilla` (lat, lon) en tierra de la isla y `--paso` 30 (60 para las grandes).
2. Copiar `la_graciosa.json` y cambiar `id`, `nombre`, `relieve` (`sur` y `oeste` del recuadro),
   `origen` (dónde cae en el mundo la esquina suroeste) y las reglas. Las escalas, las mismas en
   todas las islas, para que casen entre ellas.
3. `--dry-run`, luego en local con `editor_local.py` y `medir_universo.py --puntos` con un recorrido
   propio (puntos en el pueblo más denso, en la cumbre y el cénit).
4. Pruebas: la de La Graciosa entera (`lenta`) se copia para la isla nueva.

Al pasar del piloto al archipiélago: dos islas vecinas (La Graciosa y Lanzarote, a 1 km de mar)
comparten celdas de mar, y cada una cree que es la única. Para eso se genera un **solo relieve por
región** (Lanzarote, La Graciosa y los islotes juntos, con varias semillas de tierra) en vez de
uno por isla. Y como el generador nunca borra, mover una isla (cambiar su `origen`) deja sus celdas
viejas: haría falta un modo que borre las celdas con su `gen` que ya no salen.

## Tenerife

| | La Graciosa (medido) | Tenerife (estimado) |
|---|---|---|
| Tamaño real | 8 × 4,5 km, 29 km² | 80 × 50 km, 2034 km² |
| A 1:10 | 0,8 × 0,45 km | 8 × 5 km |
| Celdas | 121 | ~4000 (6300 en el recuadro, menos el mar lejano) |
| JSON en el borrador | 1 MB (8 KB por celda) | ~35-40 MB |
| En SQLite | ×2 al publicar (borrador y publicado) | ~80 MB la primera publicación |
| Generar | 2,7 s | ~1,5 min en este PC; el doble o el triple en el NAS |
| Relieve | 405 × 364 a 30 m | 1350 × 850 a 60 m (~2 MB de PNG) |

Lo que haría falta:

- **Alturas:** el Teide (3715 m) a 1:5 son 743 m: necesita `base` y que la validación pase de los
  120 m de hoy (terreno y objetos), y más niebla o un horizonte más largo para que se vea.
- **Memoria y tiempo del generador:** en Python puro, el relieve se guarda en listas; con 1,1 M de
  muestras ya son ~100 MB y la distancia a la costa tarda decenas de segundos. Bastaría con
  `numpy` en el script (no en el bot) o con generar por regiones.
- **Publicar:** cada publicación guarda solo los trozos que cambian, pero regenerar con otra regla
  reescribe todas las celdas (40 MB nuevos por versión, y se guardan 50). Las reglas se afinan en
  local y se publica pocas veces.
- **Más kit:** pinar (Pinus canariensis), laurisilva, retama del Teide, malpaís, casas de pueblo
  de medianías, iglesias y plazas. Y pueblos con trazado que siga la costa o las curvas de nivel,
  no solo rejillas.
- El índice del mundo crece ~15 bytes por celda (60 KB más con Tenerife), dentro de lo medido con
  5000 celdas en `docs/medir_mundo_servidor.py`.
