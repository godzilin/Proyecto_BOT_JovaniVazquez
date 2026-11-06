# Solares: la rama Ladrillo construye en `pisos` (9.56.0)

Hasta la 9.55 la obra nueva era pasiva: cada lunes, uno de los que fichaban en la rama 🏖️
Ladrillo de `pala` «levantaba» un piso suelto en un barrio sin promoción y cobraba el 5 % de lo
que otros compraran en él. Salían pocas viviendas y el promotor no hacía nada. Ahora el promotor
elige dónde y qué construye, lo paga, espera la obra y se queda con el edificio entero.

Las promociones de los lunes siguen como estaban (la décima tarjeta de cada barrio y su 5 %).

## Qué ve el jugador

- Cada calle de cada barrio tiene **un solar**: 3 por barrio, **60 en el servidor**
  (`housing_solars.SOLARS`). Se ven en 🗺️ Calles con el botón 🏗️ Solares, que abre la pantalla
  de los tres solares del barrio.
- Un solar libre enseña los proyectos que caben en él, con lo que cuesta, lo que valdrá y los días
  de obra. Quien ficha en la rama Ladrillo puede construir el proyecto de su puesto o uno más
  pequeño:

  | Puesto | Proyecto | Unidades | Días |
  |---|---|---|---|
  | P6 Promotor | 🏡 Casa (terrera, chalé o casona según el barrio) | 1 | 10 |
  | P7 Urbanizador | 🏘️ Bloque de 3 plantas | 7 | 14 |
  | P8 Rey del ladrillo | 🏢 Bloque de 5 plantas con ático | 12 | 20 |
  | P9 Magnate | 🏬 Residencial de 7 plantas | 21 | 24 |
  | P10 Presidente | 🗼 Torre de 8 plantas | 22-23 | 28 |

  Un día del bot es un mes: una casa tarda diez meses y una torre, algo más de dos años.

  El barrio pone su límite de alturas (`MAX_FLOORS`): en el campo, hasta 3 plantas; en los cascos
  históricos y los barrios marineros, hasta 5.
- **Construir es una sola operación:** compra el solar, paga la obra y empieza. Las unidades
  existen desde ese momento y son del promotor (100 participaciones de cada una), «En obras»
  hasta el último día (`OBRAS` con `works_until`). Al acabar, quedan vacías y listas para
  alquilar, vender o hipotecar como cualquier inmueble.
- **Sobre plano:** mientras dura la obra, las unidades no se pueden vender al mercado
  (`housing_holdings.locked_until`, lo mismo que lo adjudicado en una subasta judicial), pero sí a
  otros miembros con 🤝 Vender a un miembro.
- **Una obra a la vez** por promotor. Un solar construido no se vuelve a construir: con los 60
  hechos no hay más (el tope de «Nada crece sin tope»).
- El edificio terminado sale en 🏗️ Solares y se abre como cualquier edificio de las calles.

## El dinero

Con `V` lo que valen las unidades al índice del barrio y `B` lo que valen con el índice a 1:

| Concepto | Cuánto | A dónde va | Norma |
|---|---|---|---|
| Solar | 15 % de `V` | desaparece (el vendedor no existe) | — |
| ITP del solar | 6,5 % | Estado (`itp`) | art. 31 del DL 1/2009 de Canarias |
| Notaría del solar | 1,5 % + IGIC 7 % | desaparece; el IGIC, al Estado | art. 51 de la Ley 4/2012 |
| Obra | 62 % de `B` (+ IPC del crédito) | desaparece (la constructora no existe) | — |
| IGIC de la obra | 7 % | Estado (`igic_gastos`) | art. 51 de la Ley 4/2012 |
| ICIO | 4 % de la obra | Estado (`icio`) | art. 102 del RDL 2/2004 |
| Licencia de obra mayor | 1 % de la obra | Estado (`licencia_obra`) | tasa municipal (art. 20.4.h RDL 2/2004) |
| AJD de la obra nueva y la división horizontal | 0,75 % de `V` | Estado (`ajd`) | art. 36 del DL 1/2009 |

Con el índice a 1, todo junto es el ~86 % de lo que valen las unidades: el promotor gana un
~16 % sobre lo que pone (el margen de una promotora de verdad, un 14 % de lo que vende). El solar sigue al índice y la obra no, así que construir en un barrio
caliente rinde más y en uno hundido apenas compensa.

- Lo pagado es una mejora y un coste de adquisición: va a `paid` (sin impuestos) y a `cost` (con
  ellos) de cada unidad, repartido por lo que vale cada una, como al dividir por plantas.
- Mientras dura la obra, las unidades pagan cada noche sus gastos de siempre (IBI, comunidad,
  seguro), como cualquier piso en obras.
- **Venderlas es una ganancia patrimonial** (19 % al vender al mercado, como cualquier piso).
  En la vida real, lo que vende un promotor es existencias y tributa como actividad económica
  (art. 27 LIRPF). El juego trata las viviendas terminadas como patrimonio del promotor
  (inmovilizado), porque se quedan en su cartera y las puede alquilar: decisión del proyecto.
- Se puede pagar con 💳 Financiar (obras), como una reforma o una división.

## Por qué esas cifras

Con `docs/economia_sim.py --solares`, un promotor que se queda en P10 de Ladrillo, construye lo más grande que paga al contado en el solar que más deja y lo vende todo al acabar (el techo: sin burbuja y sin perder un día):

| Obra | Días | Margen | Falcon |
|---|---|---|---|
| sin solares, pisos con hipoteca | — | — | día 340 |
| sin solares, solo sueldo | — | — | más allá del 400 |
| 55 % | los de la tabla / 2 | 27 % | día 171 |
| 62 % | los de la tabla / 2 | 16 % | día 231 |
| **62 %** | **los de la tabla** | **16 %** | **día 305** |
| 66 % | los de la tabla | 10 % | día 359 |

El primer diseño (27 % y obras cortas) duplicaba lo que rinde reinvertir: una fuente que crece con el capital, justo lo que prohíbe «Nada crece sin tope». Con el 16 % y plazos de verdad, construir rinde más que comprar pisos con hipoteca, que es lo que pide la rama, y el Falcon sigue cerca de su objetivo. El tope de lo que se gana de una vez es la torre más cara (~86 M en Mogán: ~13 M de margen en 28 días).

## La burbuja

Cada lunes, al cerrar el día, el banco mira **cuánta obra nueva entra frente a cuánto compran los
miembros** en las últimas 4 semanas (`BUBBLE_WINDOW`):

- **Oferta:** lo que valen las obras empezadas en esas 4 semanas (`housing_solars.value`).
- **Demanda:** lo que han pagado los miembros por pisos (al mercado y entre ellos: noticias
  `compra` y `compra_miembro`), con un mínimo (`BUBBLE_DEMAND_FLOOR`) para no dividir por casi
  nada.
- **Presión** = oferta / demanda. Con poca oferta (`BUBBLE_SUPPLY_MIN`) no se mira nada.

Si la presión pasa de 1, puede estallar: la probabilidad sube un 25 % por cada punto de más
(hasta el 50 %, `bubble_odds`). Si estalla, en cada barrio con obra empezada en esas 4 semanas:

- el índice baja entre un 10 % y un 30 % según la presión (`bubble_cut`), sin bajar de
  `INDEX_MIN`, y vuelve poco a poco a su nivel como siempre (un 3 % cada noche);
- las obras en marcha se retrasan 3 días (`BUBBLE_DELAY`): el banco corta el crédito;
- sale un aviso en `#chat-general`.

No mueve dinero: baja lo que valen los pisos (lo que cobra quien vende y el Patrimonio). Quien
construye cuando nadie compra se arriesga a terminar en un barrio hundido; quien espera, lo
recupera.

## Logros

En 🏠 Pisos: obras empezadas (1, 5, 20) y terminadas, cada proyecto (y todos), unidades levantadas
(10, 50, 200), lo invertido (10 y 100 millones), barrios e islas distintos, el ICIO pagado (con su
explicación), que te pille la burbuja (secreto) y construir con la burbuja estallada (secreto).
