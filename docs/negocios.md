# Negocios: tu propia empresa dentro de `pala`

Diseño del sistema de negocios. Se acordó con el usuario el 20/10/2026, inspirado en *Big Ambitions*.
Este documento recoge el sistema entero, sus cinco fases y cada una al detalle; la fase 1 es la versión 8.14.0 y la 5, la 9.11.0.

## La idea

- **El tiempo es la moneda.** El día tiene `FRANJAS_DIA` (8) franjas. La jornada de la `pala` reserva las suyas
  (4, o menos con la reducción) aunque no fiches. Cada hora extra, guardia o turno de estudio gasta franjas
  libres, y cada turno en tu negocio también. Lo que haces de más en el curro se lo quitas al negocio.
  Acabadas, se pueden trasnochar 4 más (9.19.0), con más riesgo de Inspección para el B.
- **Respondes con lo tuyo.** En la fase 1 el negocio es de un autónomo: no tiene caja propia y todo sale y
  entra del monedero de su dueño. Los gastos fijos (alquiler del local, suministros y cuota) corren aunque no
  abras, y un turno malo puede perder dinero.
- **Pesa lo que le dedicas.** Sin empleados, cerrado es cerrado. Lo pasivo solo llega con empleados (fase 4).
- **Visual y personal.** Todo el panel son escenas de Node, como `pisos`. Cada negocio lleva su nombre, sus
  colores, su emblema y sus clientes habituales, que te recuerdan.
- **La demanda es artificial y limitada.** Cada barrio tiene cada día un cupo de clientes por sector, y lo
  reparten los negocios del mismo sector que hay en él. El dinero nuevo que entra está topado.

## Las fases

| Fase | Qué trae | Versión |
|---|---|---|
| 1 | Franjas, reducción de jornada, excedencia, local en un barrio, 3 sectores con su minijuego, stock y precios, gastos fijos, cierre de cada noche y renta de actividad | 8.14.0 |
| 2 | Crecer el local: mejoras que se amortizan, traslado a un local mayor o a uno tuyo de `pisos`, campañas de publicidad, reseñas, dos sectores más, precio por producto, traspasar o liquidar al cerrar | 9.5.0 |
| 3 | Sociedad limitada: caja propia, Impuesto sobre Sociedades, sueldo de administrador o dividendos, quiebra limitada, compatibilidades por oficio, pluriactividad | 9.6.0 |
| 4 | Empleados artificiales estilo inquilinos (rasgos, habilidades, formación, humor, contrato y despido), turnos pasivos, nómina completa con la cotización de la empresa, B con Inspección, bonificaciones, RIC y ZEC | 9.8.0 |
| 5 | Cadenas: hasta 4 locales (con una sola sociedad para todos), una nave para importar de la Península con el AIEM y el IGIC de importación, gerentes que reponen y cubren las bajas, vender la sociedad | 9.11.0 |

Dónde engancha cada fase con lo de la 1:

- **Fase 2 (hecha).** `Business.size`, `equipment`, `campaign` y las reseñas multiplican los clientes de
  un turno (`turn_clients`); el traslado cambia `barrio`, `size` y `own_listing`. Detalle en «Fase 2 al
  detalle».
- **Fase 3 (hecha).** `Business.form` pasa a `"sl"` y `Business.company` lleva la sociedad. El dinero va a su
  cuenta (`economy.company_account`) y tributa en Sociedades, no en `INCOME_BUSINESS`. Detalle en «Fase 3 al
  detalle».
- **Fase 4 (hecha).** Un turno pasivo es el mismo `TurnGame` jugado por el empleado con sus habilidades
  (`business_staff.play_turn`), y su nómina lleva por fin la cotización de la empresa. No usa `compute_payslip`
  (que mira la renta de un miembro): la retención es la de su propio sueldo. Detalle en «Fase 4 al detalle».
- **Fase 5 (hecha).** Cada local es un `Business` que se busca por su `id` (`biz`); los de una sociedad llevan
  su `company_id` y una copia de su estado. La nave es aparte (`business_nave`) y las noches van dueño a
  dueño. Detalle en «Fase 5 al detalle».

## Fase 1 al detalle

### El día en franjas (`services/agenda.py`)

- `FRANJAS_DIA = 8`: unas 16 horas despierto en bloques de dos.
- **Trasnochar** (9.19.0): `FRANJAS_NOCHE = 4` más después de las del día, y ninguna después
  (`FRANJAS_MAX = 12`, el día entero en bloques de dos). Valen para lo mismo, pero el B de
  noche lo pilla la Inspección un 30, 40, 50 y 60 % de las veces (`work.NIGHT_INSPECTION`; de
  día, un 10 %) y se devuelve doble (`work.NIGHT_SURCHARGE`), y cada franja trasnochada en la `pala`
  cuesta 5 de familia (`work.FAMILY_LATE`; el negocio no toca la familia, ni de día). Las franjas no van por horas: se trasnocha al acabar las
  del día, aunque sean las ocho de la tarde. La plantilla del negocio no trasnocha: sus
  turnos son de las franjas 0 a 7.
- **Jornada:** 4 franjas (`ORDINARY_SHIFTS`), 3 con la reducción de 1/4 o 2 con la de 1/2. Queda reservada
  todo el día aunque no se fiche.
- **Lo que gasta cada cosa:**
  - turno ordinario, extra, en B, de estudio o en teletrabajo: 1 franja;
  - guardia: 2 franjas;
  - turno en el negocio: 1 franja.
- **Cuentas del día:**
  - en el curro se usa lo que sea mayor entre la jornada y lo fichado de jornada;
  - lo libre es `FRANJAS_DIA` menos lo usado en el curro, menos las extras, menos el negocio.
- **Qué se bloquea sin franjas, ni de noche:** las horas extra, el B, las guardias y el estudio de la
  `pala`, y los turnos del negocio. Los turnos de jornada nunca se bloquean, porque ya están reservados.
- **El día** es el local de Canarias (`local_day`), como en el resto de la `pala`.
- **Datos que se guardan:**
  - en `Contract`: `jornada` (4, 3 o 2), `jornada_next` y su día (`jornada_from`), `excedencia_since` y
    `franjas_day`/`franjas_work` (franjas gastadas hoy en el curro);
  - en el negocio: `franjas_day`/`franjas_used`.

### Reducción de jornada

Es el art. 37.6 ET: de 1/8 a 1/2 de la jornada, con el sueldo reducido en proporción. En el juego:

- **Tramos:** completa (4 franjas), 3/4 (3) o media (2). Cada turno paga lo mismo, así que el sueldo baja
  porque hay menos turnos de jornada.
- **Preaviso** (art. 37.7 ET, 15 días): el cambio vale desde el día siguiente.
- **Sin horas extra, B ni guardias.** Es decisión del proyecto, por analogía con el tiempo parcial
  (art. 12.4.c ET). Las guardias mínimas del residente no se exigen mientras dure.
- **La pensión** sale de la media diaria cotizada en la rama, así que con reducción cotizas menos. En la vida
  real se protege la de cuidado de menores (art. 237 LGSS); aquí no, y se apunta como posible añadido.

### Excedencia voluntaria

Es el art. 46.2 ET; en la escala de la `pala`, un año son 7 días (los trienios son de 21).

- **Requisitos para pedirla:**
  - al menos 7 días en el oficio (`job_since`), el «año» de antigüedad;
  - que hayan pasado 28 días (los «4 años») desde la anterior.
- **Mientras dura:**
  - no reserva franjas y no se ficha;
  - el contrato se conserva;
  - para la pensión se sigue contando como trabajando: no hay pensión entera por pedirla.
- **Volver:**
  - pasados 2 días como mínimo (los «4 meses»);
  - vuelves al mismo puesto, con la barra a la mitad, como al volver de 7 días sin fichar;
  - en la vida real solo hay derecho preferente al reingreso (art. 46.5); es una simplificación del proyecto.
- **Cambiar de oficio** estando de excedencia la termina.

### El negocio (`services/business.py`, `business_catalog.py`)

- **Uno por miembro y servidor** en la fase 1. Forma: autónomo (`form = "autonomo"`).
- **Sectores** (`SECTORS`), cada uno con su minijuego:
  - ☕ **Cafetería** · minijuego «Comandas». Productos: café solo, cortado, barraquito, zumo, bocadillo y dulce.
    Cada cliente es una mesa que pide de 2 a 5 cosas.
  - 🛒 **Tienda de barrio** · minijuego «La caja». Productos: pan, leche, fruta, conservas, limpieza y bebidas.
    El cliente trae una cesta; se cobra y se da el cambio.
  - 🔧 **Taller** · minijuego «Diagnóstico». Recambios: pastillas de freno, batería, aceite, correa, bujías y
    neumático. Primero se diagnostica la avería y luego se elige el presupuesto.
- **Barrio:** uno de los 20 de `pisos`.
  - El tema del barrio (`THEMES`) multiplica la demanda de cada sector (`THEME_FIT`).
  - En lujo se aguantan mejor los precios altos.
  - El alquiler del local sale de `rent_m2` del barrio, los metros del sector y el `rent_factor` de los
    locales de `pisos`, a la misma escala que la renta de un local en `pisos`. Así un local cuesta lo mismo
    si te lo alquilan que si lo alquilas tú.
- **Personalización:**
  - nombre (hasta 24 caracteres; se valida y se limpia de menciones y saltos de línea);
  - paleta del rótulo (`PALETTES`, 8) y emblema (`EMBLEMS`, 8);
  - **clientes habituales** (`REGULARS`): cinco por negocio, elegidos por semilla. Cada uno tiene su nombre,
    su pedido de siempre y su fidelidad (0-10). Con buena fidelidad dejan más propina y piden
    «lo de siempre» sin decirlo: hay que recordarlo.
- **Precio:** un nivel para todo el negocio (`PRICE_LEVELS`): barato (−15 %), normal, caro (+15 %) o
  carísimo (+30 %).
  - Más caro, menos clientes y más margen.
  - Barato sube la reputación un poco.
  - En los barrios de lujo, lo caro espanta menos.
- **Reputación:** de 0 a 100, en estrellas de 1 a 5 (`stars`). Arranca en 50. Sube con los clientes bien
  atendidos y baja con los errores, las esperas y las acusaciones falsas.
- **Stock:** unidades por producto, que se compran al mayorista por lotes. Las perecederas se pierden en parte
  cada noche (`SPOIL`). Sin stock, ese producto no se vende: el cliente se va o pide otra cosa.

### La demanda (artificial y limitada)

- **Clientes de un turno:**
  `base del sector × THEME_FIT × curva horaria (hora canaria) × reputación (0,5 + estrellas / 6) × precio`.
- **Cupo diario por barrio y sector** (`DAILY_POOL`, en turnos de clientes base): un servidor no puede sacar
  de un barrio más que eso al día. Lo comparten los negocios del mismo sector en ese barrio, por orden de
  llegada, y se guarda en `business_demand`.
- **Curva horaria por sector:**
  - cafetería: mañana y merienda;
  - tienda: media mañana y tarde;
  - taller: horario de taller;
  - de madrugada, poco.

### Un turno (los minijuegos, `services/business_games.py`)

- Cada turno gasta 1 franja y batería (`SHIFT_COST`), tenga curro o no: la batería es de la persona.
  - Sin contrato se guarda aparte (`work_people`) y el inicio de `pala` la enseña.
  - Al firmar el primer oficio pasa al contrato: quien llega reventado del negocio no estrena batería.
  - Con la batería en `BATTERY_MIN` no se abre; por debajo de `TIRED`, los clientes esperan menos.
- Llega una cola de clientes (los de la demanda). A cada uno se le atiende en su pantalla (una imagen por
  cliente) y tiene paciencia; si se agota, se va. El turno acaba al vaciarse la cola o a los `TURN_SECONDS`.
- **Comandas** (cafetería):
  - en el bocadillo del cliente sale su pedido; se pulsan los productos y luego ✅ **Servir**;
  - acertar cobra la mesa; servir rápido deja propina;
  - fallar deja una queja y lo servido se pierde;
  - el habitual con fidelidad pide «lo de siempre» y no dice qué.
- **La caja** (tienda):
  - la cesta, el total y el billete con que paga; se da el cambio con monedas y billetes y se pulsa ✅ **Cobrar**;
  - si das de menos, queja; si das de más, lo pierdes;
  - a veces alguien esconde algo: 👀 **Bolsillo** lo pilla, pero acusar a quien no es baja mucho la reputación.
- **Diagnóstico** (taller):
  - el coche llega con un síntoma (texto y una pista en el dibujo); se elige la avería entre cuatro;
  - luego el presupuesto: ajustado, justo o caro; cuanto más caro, más fácil que no lo acepte;
  - si fallas el diagnóstico, gastas el recambio, no cobras (garantía) y baja la reputación.
- **El resultado** (`settle_turn`): ventas, propinas, coste de lo vendido, mermas, reputación, fidelidad de
  los habituales y la nota del turno (para los logros).
- **Velocidad:**
  - el siguiente cliente se conoce de antemano (semilla del turno), así que su imagen se pinta mientras
    atiendes al de ahora;
  - los clics dentro de un cliente solo cambian el texto: contestan sin subir imagen.
- **Relojes justos** (9.38.1, informe del 04/11/2026: subir la imagen de un cliente tarda 835 ms de media y más
  de 1 s una de cada ocho veces):
  - desde que empieza una ronda (cliente nuevo o presupuesto del taller) hasta que Discord acepta la imagen,
    el jugador no tiene botones: ese rato se suma a la paciencia del cliente y al cierre del turno
    (`TurnGame.shown`, una vez por ronda y 5 s como mucho);
  - un clic cuenta a la hora en que se pulsó (`created_at` de Discord), con 1 s de margen como mucho
    (`pressed_at`): servir con «1 segundo» en pantalla ya no echa al cliente.

### Dinero e impuestos

Todo por `EconomyService.business`, en una transacción con lo que guarda el negocio, como `pisos`.

- **Ventas:** las crea la demanda, que no existe; son el grifo.
  - Son rendimiento de actividad económica (art. 27 LIRPF). El autónomo del juego opta por el criterio de
    cobros y pagos (art. 7.2 RIRPF, que es una opción y no la regla): ingreso al cobrar y gasto al pagar.
  - Retienen a cuenta con el tipo estable sobre el margen del turno (ventas menos el coste de lo vendido), en
    lugar del 20 % del pago fraccionado (art. 110.1.a RIRPF): una sola vara para toda la renta (Biblia).
  - Se integran con el resto de la base general (art. 48.a LIRPF): la Renta las suma a las nóminas.
  - El embargo del 30 % de quien debe a Hacienda alcanza a las ventas.
- **Compras al mayorista:** la base desaparece y el IGIC va al Estado.
  - El autónomo está en el régimen especial del pequeño empresario (arts. 89-91 del Decreto Legislativo
    1/2025 de Canarias): sus ventas están exentas (art. 90.1), así que no repercute IGIC y no deduce el
    soportado, que pasa a ser coste. El tipo general es el 7 % (art. 32.1).
  - Simplificación: no se mira el tope del régimen (30.000 € de facturación al año; 50.000 € desde 2027, y en
    julio de 2026 pudo optar quien no pasó de 50.000 € en 2025, DA 7.ª del Decreto-ley 3/2026).
  - Base más IGIC se apuntan como gasto de la actividad (`INCOME_BUSINESS` en negativo).
- **Montar el negocio:**
  - la tasa municipal de la apertura va al Estado, porque no hay ayuntamientos. Es la de la comunicación
    previa (art. 20.4.i TRLRHL): la Ley 7/2011 de Canarias para la cafetería y el taller, y la Ley 12/2012
    para la tienda (pequeño comercio);
  - la fianza del local son 2 mensualidades (art. 36.1 LAU) y va al depósito de fianzas
    (`HOUSING_DEPOSIT_ACCOUNT_ID`, como la de los pisos); vuelve al cerrar, menos lo que se deba;
  - el acondicionamiento lleva IGIC y se apunta entero como gasto de esa semana. Es una simplificación: la
    libertad de amortización (art. 12.3.e LIS, por el art. 28.1 LIRPF) solo vale para elementos nuevos de
    hasta 300 € cada uno y 25.000 € al año, y unas obras se amortizarían (fase 2).
- **Cada noche, el alquiler y los suministros:**
  - el alquiler del local lleva IGIC y la retención del 19 % del arrendador (arts. 100 RIRPF y 101.8 LIRPF), que el inquilino
    ingresa en el Estado; el resto desaparece (el casero no existe);
  - los suministros llevan IGIC;
  - se cobra lo que llegue del monedero y el resto queda como deuda del local;
  - con `EVICTION_NIGHTS` (3) noches sin pagar, el casero rescinde: el negocio cierra y la fianza cubre lo
    que pueda.
- **Cuota de autónomo:** cada lunes, `SELF_EMPLOYED_FEE` (la tarifa plana `FLAT_RATE_FEE` la primera semana,
  art. 38 ter de la Ley 20/2007) por `charge_self_employed_fee`.
  - La tarifa plana real fue de 80 € al mes de 2023 a 2025 (DT 5.ª del RDL 13/2022). Desde 2026 la fija cada
    Ley de Presupuestos; con los de 2026 prorrogados (RDL 3/2026), no hay cifra oficial nueva.
  - Si su puesto de la `pala` ya es de autónomo (reparto, influencer), no paga dos.
  - Con curro por cuenta ajena es pluriactividad: paga las dos cotizaciones. El reintegro del art. 313 LGSS
    se deja para la fase 3.
- **Mostrar la mordida:**
  - cada resultado con dinero lleva su línea de impuestos (🐶 IRPF a cuenta, IGIC);
  - el aviso de la Renta (`renta.remind`) y la mascota (`renta.hint` con el momento
    `Event.BUSINESS`: contenta con una S o una A, de bajón con una D);
  - `hacienda` cuenta el IGIC, las tasas y la retención del alquiler (`economy_business_tax`);
  - `saldo` enseña el alquiler de esta noche, la deuda del local y la cuota de la semana (`pending`).
- **El IMV y el banco ven el negocio** (`EconomyRepository.work_net`):
  - a las nóminas se suma lo que deja el negocio: ventas menos su retención y menos los gastos de la
    actividad (compras, local, apertura) de la misma ventana, 7 días para el IMV y 30 para el banco;
  - el IMV real mira todos los ingresos, también los de actividades económicas (art. 18 de la Ley 19/2021),
    y el banco los ve en la declaración;
  - nunca resta: una semana en pérdidas, o de montar y comprar stock, cuenta como cero. Así comprar no sube
    el IMV. La cuota de autónomo sí resta, como siempre;
  - con eso, quien solo tiene negocio ya no es «sin nómina» para los préstamos, y el VPO, el Bono Alquiler
    Joven y la garantía del alquiler lo cuentan como ingresos.

### Las pantallas (escena de Node, `assets/negocio/escena.html`)

1000 × 640, al doble (S = 2), con las fuentes de `pisos`. Sin emojis: los iconos son trazos de canvas. Python
decide todo (`services/business_view.py`) y la escena solo pinta. Si no hay Node ni Chromium, el panel sale en
texto (`ui.TextDisplay`), como la `pala`.

| `screen` | Para qué |
|---|---|
| `dia` | El inicio de `pala`: las 8 franjas del día, la tarjeta del curro y la del negocio (o la de montar uno) |
| `montar` | Elegir sector: los tres locales con su fachada y sus cifras |
| `negocio` | La portada: la fachada con su rótulo, el barrio de fondo y las cifras del día. También es la vista previa al montarlo |
| `almacen` | El stock: cada producto con sus unidades, coste, precio y si caduca |
| `cuentas` | La cuenta de resultados de esta semana y la anterior |
| `cliente` | El minijuego: el mostrador con el cliente, su pedido, cesta o coche, y la cola |
| `cierre` | El resumen del turno: atendidos, perdidos, caja, propinas, estrellas y nota |

El contrato de cada pantalla está en la docstring de `services/business_view.py`.

### Panel dentro de `pala`

- **`pala` abre el inicio** (`dia`), un `LayoutView` con la imagen en un `MediaGallery` (PNG; nunca GIF, por
  lo de la Torre).
  - Con curro y negocio: 🪏 **Trabajo** y 🏪 **Negocio**.
  - Con uno solo: su botón y otro pequeño para lo que falta (🏪 **Montar negocio** o 💼 **Buscar curro**).
  - Sin nada: los dos botones de empezar.
- **🪏 Trabajo** es el panel de siempre (texto), con 🏠 **Inicio** para volver. Su menú de la jornada:
  - reducción de jornada;
  - excedencia y volver de ella.
- **🏪 Negocio** es la portada, con:
  - 🔑 **Abrir** (un turno, 1 franja);
  - 📦 **Almacén**, 🏷️ **Precios**, 📊 **Cuentas**;
  - 🚪 **Cerrar el negocio** (con confirmación);
  - 🏠 **Inicio**.
- **Montar:**
  - el sector en la escena `montar`, con un desplegable;
  - el barrio en un desplegable con el alquiler y la demanda de cada uno;
  - nombre en un formulario, paleta y emblema en desplegables, con la vista previa en la escena;
  - una confirmación con lo que cuesta (tasa, fianza, acondicionamiento y la cuota de la semana).
- **Pintar antes del clic:** las pantallas vecinas (almacén, cuentas, portada) se pintan en segundo plano
  (`Painter` de `pisos`) y el siguiente cliente también, mientras atiendes al de ahora.
  - El primer cliente de un turno también: en la portada y en el tique, el panel sortea ya el azar del
    próximo turno (`TurnPlan`, `BusinessService.plan`) y pinta su primer cliente (`preview`). Al pulsar
    🔑 Abrir, `start_turn` gasta ese mismo azar: si nada ha cambiado (la hora, el stock, la batería), la
    imagen ya está pintada.

### Logros (categoría 🏪 Negocio, en el grupo 🪏 Trabajo)

Salen de las estadísticas de `business_stats` y de las de la jornada. Familias:

- escalones de turnos (también por sector), clientes, ventas y propinas;
- lo gastado en el mayorista, las compras, las noches con el negocio y los negocios cerrados;
- los clientes servidos rápido y los que se van sin atender;
- el mejor turno;
- el turno perfecto;
- la caja exacta y el ladrón pillado;
- el diagnóstico certero;
- los presupuestos caros aceptados;
- las 5 estrellas;
- habituales fieles y «lo de siempre» acertado;
- fracasos: la acusación falsa, el cambio de más o de menos, el presupuesto rechazado, el
  desahucio, cerrar en números rojos;
- colecciones: tres sectores y los cinco, todos los temas de barrio, las dos islas, las tres
  campañas de publicidad;
- fase 2: escalones de turnos en la heladería y la ferretería; reseñas (en total, de cinco
  estrellas y, ocultas las altas, de una), el distintivo de «recomendado»; mejoras instaladas,
  tres a la vez y lo gastado en ellas; campañas y lo gastado en publicidad; precio por
  producto; traslados, el local propio, el mediano y el grande; traspasos, el traspaso gordo
  y lo cobrado traspasando;
- fase 3, la sociedad: la primera y tres constituidas (oculto), el capital de 3.000 € y el de
  10.000 €; lo aportado, lo prestado y lo devuelto; los sueldos de administrador (cuántos y
  cuánto) y los dividendos (cuántos, oculto el alto, y cuánto); semanas declaradas en
  Sociedades y lo pagado; las semanas en pérdidas (ocultas); el mejor beneficio de una semana;
  liquidarla, hundirse y pagar sus deudas (oculto) y la gran cuota de liquidación; lo devuelto
  por la pluriactividad;
- fase 5, las cadenas: los locales, sectores e islas a la vez, la marca repetida, la sociedad con varios
  locales, la nave, las importaciones con su AIEM y lo ahorrado, los gerentes y vender la sociedad («Fase 5
  al detalle»);
- fechas: madrugada, domingo, Día de Canarias;
- rachas de días abriendo;
- las 8 franjas usadas y, en la `pala`, trasnochar (9.19.0: turnos de noche, el B de noche sin que
  te pillen, la última franja de la noche, que te pillen de noche y con la familia en crisis);
- el día de pluriactividad;
- la reducción y la excedencia;
- Hacienda: primera retención de la actividad, primer IGIC soportado;
- secretos.

### Equilibrio

Se calibra con `docs/negocio_sim.py`, que juega días enteros con los minijuegos y las reglas
reales. Cifras del 20/10/2026, con 28 días y la media desde el día 15, ya con las estrellas
asentadas. Neto al día, tras IRPF y cuota:

| | 4 turnos al día | 8 turnos al día |
|---|---|---|
| `pala` en obra (4 turnos de jornada) | P1 6.700 · P3 14.300 · P5 29.800 | — |
| Buen jugador (falla 1 de cada 8) | 7.500-18.000 | 18.000-38.000 |
| Jugador perfecto | 12.000-22.000 | 29.000-46.000 |
| Jugador flojo (falla 1 de cada 3) | pierde en la cafetería y el taller; 5.500-9.600 en la tienda | casi nada |

- **Montarlo** cuesta de 129.000 a 195.000 Y$ y se recupera entre el día 4 y el 28: lo que
  ahorra en una o dos semanas alguien con un P3.
- **Las 5 estrellas** llegan en dos a cuatro semanas a 4 turnos al día.
  - `REPUTATION` y `REPUTATION_CLIENTS` de `business.py` igualan el ritmo de los sectores:
    el taller, con 4 clientes por turno, sube por turno lo mismo que la cafetería.
  - Un error pesa como unos cinco aciertos.
- **La tienda** es el sector fácil y de poco margen: el flojo no se hunde (se queda en 3★).
- **Barrio y alquiler:** un barrio caro solo compensa con su demanda o subiendo precios.
  La cafetería de Santa Cruz (centro) gana más que la de Puerto de Mogán (lujo), que paga
  casi el doble de local.
- **Lo que no mira el simulador:**
  - la batería (cada turno del negocio la gasta como un turno de la `pala`, con curro o sin él);
  - la competencia en el barrio;
  - los niveles de precio distintos del normal;
  - el Falcon. En la fase 1 el negocio da lo de un P3-P5; los puestos altos de la `pala`
    siguen mandando al final.
- **El cupo de franjas** cambia la `pala` sola. En la 8.14.0 era un muro: quien no tenía
  negocio podía hacer como mucho 4 horas extra al día (8 turnos en total). Antes solo lo
  frenaban la batería y la familia, y con el café a 25 Y$ la batería no frenaba: el B no tenía
  fin. Al muro le faltaba lo que gustaba del B, elegir si arriesgar otra vez, y en la 9.19.0
  pasó a ser una cuesta: 4 franjas de noche en las que el B rinde cada vez menos (de media, la
  última pierde) y el café vale unas 8 veces más (110 y 200 Y$). `docs/noche_pala.py` mide que
  quien lo apura todo saca casi lo mismo que con el muro (en el P1, un 10 % menos; en un
  puesto medio, un 0,5 % menos) y
  el jugador normal no nota nada.

### Medido (21/10/2026)

- **Dibujo** (`docs/medir_dibujos.py`, 4 hilos de este PC, dos pasadas, ms con 1 proceso abierto / en caliente):
  inicio 61 / 59, portada 64 / 63, cliente de la cafetería 61 / 63, cliente del taller 56 / 57 (la ficha de
  `pisos`, 75 / 75). En el NAS, ~1,5 veces más.
- **Node contra Chromium** (Chrome 155 de este PC, las 32 muestras): diferencia media de 0,9 a 2,2 sobre 255,
  por debajo de 4 en todas. La prueba de `tests/unit/test_business_scene.py` sigue saltándose aquí porque busca
  Chromium en `/opt/pw-browsers`.
- **Memoria:** el proceso de Node que siempre está abierto pasa de 78 a 131 MB con la escena cargada (~53 MB),
  lo que cuenta `docker-compose.yml` por escena. No sube el tope: cada proceso carga 3 escenas como mucho.

### Normas comprobadas en fuente oficial (21/10/2026)

En el BOE consolidado:

| Norma | Qué dice | Estado |
|---|---|---|
| Art. 36.1 LAU | Fianza de dos mensualidades en el uso distinto de vivienda | Confirmado |
| Art. 110.1.a RIRPF | Pago fraccionado del 20 % del rendimiento neto en estimación directa | Confirmado |
| Art. 7.2 RIRPF | Criterio de cobros y pagos: es una opción, que dura 3 años | Corregido (antes se daba como regla) |
| Art. 48.a LIRPF | Los rendimientos se integran y compensan sin límite en la base general | Confirmado |
| Ley 7/2011 de Canarias, arts. 5.1, 34 y 35; Ley 12/2012, arts. 3-4; art. 20.4.i TRLRHL | Comunicación previa ante el ayuntamiento y su tasa municipal | Confirmado |
| Decreto Legislativo 1/2025 de Canarias, arts. 32.1 y 89-91 | IGIC general del 7 %; pequeño empresario exento, tope de 30.000 € (50.000 € desde 2027) | Corregido (antes, 50.000 € desde julio de 2026) |
| Ley 19/1994, arts. 29, 31, 43 y 44 | ZEC: IS al 4 %, 5 empleos en Gran Canaria y Tenerife (3 en el resto), inversión de 100.000 o 50.000 €, 1,8 M€ de base más 0,5 M€ por empleo de más | Confirmado. La inscripción acaba con el reglamento europeo de ayudas (31/12/2026, prorrogable); sus incentivos duran 6 años más |
| Art. 38 ter Ley 20/2007 | Tarifa plana 12 meses (y 12 más con rendimientos bajo el SMI) | Confirmado. El importe de 2026 no tiene cifra oficial |
| Arts. 100 RIRPF y 101.8 LIRPF | Retención del 19 % sobre el alquiler de un local | Confirmado (el 101.8 es de la ley, no del reglamento) |
| Art. 12.3.e LIS, por el art. 28.1 LIRPF | Libertad de amortización hasta 300 € por elemento y 25.000 € al año | Corregida la remisión (no es el art. 30 LIRPF) |

Para la fase 3: el RDL 3/2026 fija en 2026 el reintegro del 50 % de lo cotizado en pluriactividad por encima
de 17.323,68 €. Para la fase 4: el anexo de la ZEC no admite el comercio minorista ni la hostelería, pero sí la
reparación de vehículos (CNAE 45), así que solo el taller podría acogerse.

Sin comprobar: el articulado del Decreto 86/2013 y la cuota reducida de 2026.

## Fase 2 al detalle: crecer el local (9.5.0)

Lo que trae, en `business.py` (reglas), `business_catalog.py` (cifras), `negocio.py` (casos de uso),
`business_view.py` (pantallas) y `cogs/business.py` (panel). Decidido por Claude, como la fase 1; todo se
puede cambiar.

### Dos sectores más

- 🍦 **Heladería** (minijuego «Comandas»): helados de vainilla, chocolate y gofio, sorbete de mango,
  granizado de limón y batido. Es un local de 40 m². Las tardes y la playa mandan.
- 🔩 **Ferretería** (minijuego «La caja»): tornillería, bombillas, pintura, herramientas, fontanería y
  cerraduras. Es un local de 70 m², con pocos clientes y tiques caros. Mandan los barrios obreros y rurales.

### El tamaño del local (`SIZES`)

| Tamaño | Metros | Clientes por turno | Cupo del barrio | Suministros y obra |
|---|---|---|---|---|
| Pequeño | ×1 | ×1 | ×1 | ×1 |
| Mediano | ×1,6 | ×1,3 | ×1,25 | ×1,4 |
| Grande | ×2,4 | ×1,6 | ×1,5 | ×1,8 |

- El alquiler sale de los metros, como en la fase 1.
- Un local mayor llega a más clientes y saca más del cupo del barrio, que siguen compartiendo los negocios
  del mismo sector.

### Mejoras (`EQUIPMENT`) y amortización

- **Lo que hacen:** dos o tres por sector. Suben los clientes por turno, la paciencia o la propina, o bajan
  la merma.
- **La terraza** (cafetería y heladería) paga además una tasa municipal cada noche por ocupar la vía pública
  (art. 20.3.l TRLRHL). Va al Estado.
- **Comprarlas no es gasto:** base más IGIC (el pequeño empresario no lo deduce y forma parte del precio de
  adquisición, norma 2.ª del PGC) se amortizan cada noche (`amortize`).
  - Se usa la tabla simplificada (Orden de 27 de marzo de 1998, art. 30.1.ª RIRPF): instalaciones y
    mobiliario 10 %, maquinaria 12 %, equipos informáticos 26 %, útiles y herramientas 30 %.
  - El año fiscal es la semana: cada noche se amortiza la séptima parte del coeficiente.
  - La amortización es gasto de la actividad sin pago (`economy_tax_records`, `negocio:amortizacion`).
  - Interpretación del proyecto: con el criterio de cobros y pagos se sigue amortizando por la dotación,
    porque la amortización no es un pago (el art. 7.2 RIRPF no lo dice expresamente).
- **El acondicionamiento** de montar o trasladarse tampoco es ya gasto entero de la semana: es una
  instalación al 10 %.
  - Los negocios abiertos antes de la 9.5.0 ya lo apuntaron entero y no cambian.
  - La obra del local que se deja al trasladarse se queda allí, y su pérdida no se apunta (simplificación).

### Campañas de publicidad (`CAMPAIGNS`)

| Campaña | Coste (sin IGIC) | Días | Clientes y cupo |
|---|---|---|---|
| Buzoneo y carteles | 4.000 Y$ | 3 | +10 % |
| Anuncios en redes | 15.000 Y$ | 5 | +20 % |
| Cuña en la radio local | 45.000 Y$ | 7 | +30 % |

- Una a la vez.
- Es gasto entero de la actividad (art. 15.e LIS, por el art. 28.1 LIRPF: sin límite).
- Lleva el IGIC general.

### Precio por producto

Cada producto puede tener su propio nivel de precios (`Business.prices`), y los que no lo tienen siguen el
general:

- en el almacén, el desplegable «🏷️ Precio de este producto»;
- los clientes de un turno y lo que suma a la reputación son la media de los niveles, pesada por lo que se
  pide de cada producto (`price_clients`, `price_reputation`).

### Reseñas (`review_of`)

- **Quién escribe:** al acabar cada turno, un 25 % de los clientes atendidos y la mitad de los que se fueron
  sin atender (el enfadado escribe más). Quien seguía en la cola al cerrar (`gone`) no escribe: no llegó al
  mostrador, igual que no mueve la reputación (9.38.1; antes dejaba una estrella la mitad de las veces y un
  local con mucha clientela salía castigado).
- **Las estrellas** salen de la nota del cliente, con algo de azar, y un habitual es un poco más generoso.
- **El azar es propio del turno** (la semilla es la del negocio y la hora del turno): no gasta el de las
  colas.
- **Se guardan las 20 últimas.** Con 4,5 de media y 10 reseñas o más, el negocio sale **recomendado**: un
  10 % más de clientes.
- **Las frases** (`REVIEW_LINES`) son al menos la mitad normales.
- Inventar o encargar reseñas sería práctica desleal (art. 27.8 de la Ley 3/1991): el juego no deja.

### Trasladarse (`move`, `move_to_own`)

- **A un local alquilado:** de otro barrio, de otro tamaño o los dos.
  - Se paga de nuevo la tasa (el traslado necesita su comunicación previa, art. 34.2 de la Ley 7/2011 de
    Canarias), el acondicionamiento del local nuevo y su fianza.
  - Vuelve la fianza de ahora, menos lo que se deba al casero; lo que no cubra se paga antes de irse.
- **Cambiar de barrio** cuesta clientela: la reputación se acerca a la de partida a la mitad y los
  habituales pierden la mitad de su fidelidad.
- **A un local propio de `pisos`:** un local o una nave entera tuya (local para la cafetería, la tienda, la
  heladería y la ferretería; nave para el taller), vacía y sin anticipo de rentas.
  - El tamaño sale de sus metros (`size_for`): desde 0,75 veces los del sector cabe un pequeño, desde 1,3
    un mediano y desde 2 un grande.
  - Sin alquiler y sin fianza. Sus recibos (IBI, comunidad, seguro) los sigue pagando `pisos`.
    Simplificación: en la vida real serían gasto de la actividad, y la construcción se amortizaría al 3 %.
  - El inmueble pasa al estado `negocio` (`housing.OWN_BUSINESS`): no se alquila, no se vende (la venta
    voluntaria se para) y no imputa renta (art. 85.1 LIRPF, porque está afecto a la actividad, art.
    29.1.a).
  - Si deja de ser entero tuyo (una ejecución, una división), esa noche el negocio cierra («sin local») y se
    liquida.

### Cerrar: traspasar o liquidar (`closing_offer`)

- **Traspasar:** hace falta llevar 7 días abierto, 3 estrellas y estar en un local alquilado (quien se lo
  queda se subroga en el alquiler, art. 32 LAU).
  - Paga el stock al coste, las mejoras y la obra por lo que queda por amortizar (su valor neto contable,
    art. 40 RIRPF) y un **fondo de comercio**: tres días de lo que dejó de media la semana pasada, por las
    estrellas sobre cinco.
  - El stock es venta de la actividad (art. 27 LIRPF).
  - Lo demás es ganancia patrimonial de elementos afectos (art. 28.2 LIRPF), en la base del ahorro (art.
    46.b). La ganancia es el fondo de comercio (lo demás se cobra por su valor contable) y paga el 19 % en
    el momento, como el Rastro.
  - Sin IGIC: es una unidad económica autónoma (art. 9.1.º de la Ley 20/1991).
- **Liquidar** (cerrar sin traspaso, o por desahucio o sin local): el mayorista recompra el stock al 50 % y
  las mejoras se venden de segunda mano al 40 % de lo que queda por amortizar. La obra se pierde.
- En los dos casos vuelve la fianza, menos lo que se deba al casero.

### El IGIC de lo que se compra al mayorista

Comprobado en el Decreto Legislativo 1/2025 de Canarias (21/10/2026). La 9.5.0 corrige los productos de la
fase 1 que iban al 7 %:

- **Tipo cero (art. 33.Uno):** el pan, la leche, la fruta fresca, el café tostado (café solo y cortado) y el
  agua.
- **3 % (art. 34.Uno.e y a):** los demás alimentos (zumo, dulce, conservas, helados, sorbete y batido), los
  detergentes (limpieza) y la pintura.
- **7 % (art. 32.1):** la comida preparada (el bocadillo), los refrescos con azúcar (bebidas, granizado) y
  el resto (ferretería, salvo la pintura).
- **Interpretaciones del proyecto:**
  - el barraquito (café, leche condensada y licor, que iría al 15 %) va al general;
  - el zumo y el batido, al 3 %;
  - el granizado, como un refresco con azúcar.

### Pantallas nuevas

| `screen` | Para qué |
|---|---|
| `mejoras` | Las mejoras del sector (instaladas o no, con su amortización) y las tres campañas, con la que está en marcha |
| `resenas` | La media, el distintivo de recomendado y las seis últimas reseñas |

- **La portada** gana la media de las reseñas, la campaña en marcha, los iconos de las mejoras (la terraza se
  ve en la fachada) y el tamaño del local.
- **El almacén** gana el nivel de precio de cada producto.
- **`montar`** enseña los cinco sectores.
- **El traslado** se enseña con la vista previa de `negocio` en el barrio nuevo (`traslado_state`).
- **El panel:** 🛠️ **Mejoras** y ⭐ **Reseñas** en la fila de arriba, 🚚 **Traslado** en la de abajo, y en
  🚪 **Cerrar**, 🤝 **Traspasar** (si hay quien lo compre) o 🚪 **Cerrar y liquidar**.
- **Pintar antes del clic:** desde la portada y el tique se pintan las mejoras y las reseñas, además del
  almacén, las cuentas y el primer cliente.
- **Medido** (21/10/2026, este PC atado a 4 hilos): mejoras 61/65 ms y reseñas 59/62 ms (1 abierto /
  caliente); `montar`, con cinco fachadas, 98 ms. Node contra Chrome, peor diferencia 2,47/255. Con todas
  las pantallas, la escena suma ~74 MB al proceso de Node (eran ~53 en la fase 1).

### Equilibrio de la fase 2

`python docs/negocio_sim.py --fase2`: un jugador bueno en el barrio del medio de cada sector, con el cupo del
barrio y la amortización, media de 6 semillas y días 15 a 28. Neto al día y en cuántos días se paga lo
invertido (las campañas ya restan lo que cuestan):

| Sector | Sin nada (4 / 8 turnos) | Mejoras (4 turnos) | Local mediano | Local grande | Campañas (al día) |
|---|---|---|---|---|---|
| Cafetería | 16.800 / 23.900 | 11-18 días | 15-17 días | 37-39 días | +1.100 a +3.800 |
| Tienda | 14.000 / 18.300 | 12-32 días | 24-27 días | 28-33 días | +700 a +2.700 |
| Taller | 5.900 / 16.700 | 13-19 días | 26-82 días | 33-98 días | −1.000 a +1.700 |
| Heladería | 12.800 / 16.200 | 14-19 días | 13-15 días | 19 días | +800 a +2.100 |
| Ferretería | 17.100 / 23.800 | 13-20 días | 17-27 días | 18-30 días | +900 a +4.000 |

- **Las mejoras que suben los clientes** rinden a quien abre pocos turnos (4 al día, con curro). A 8 turnos el
  cupo del barrio ya se agota y suman poco: ahorran franjas, no dan más dinero.
- **El local mayor** sube el techo, porque le toca más cupo. Es la mejora de quien abre 8 turnos.
- **Las mejoras de paciencia** (TPV, diagnosis) cuestan menos porque al jugador rápido le sirven poco. A quien
  tarda en atender, le sirven más.
- **El taller con 4 turnos** gana poco y las campañas no le compensan: tiene pocos clientes y caros.
- Las cifras de los sectores de la fase 1 cambian un poco por el IGIC corregido y por el cupo del barrio, que
  la tabla de la fase 1 no miraba.

### Normas de la fase 2 comprobadas (21/10/2026)

En el BOE consolidado:

| Norma | Qué dice | Uso |
|---|---|---|
| Orden de 27-3-1998; art. 30.1.ª RIRPF | Tabla simplificada: 10 / 12 / 26 / 30 % (y 3 % los edificios) | Amortización de mejoras y obra |
| Norma 2.ª del PGC (RD 1514/2007); art. 35.1.b LIRPF | El impuesto indirecto no recuperable va al precio de adquisición | El IGIC del pequeño empresario se amortiza |
| Art. 7.2 RIRPF | El criterio de cobros y pagos es para ingresos y gastos; no dice nada de amortizar | Interpretación: se amortiza igual |
| Art. 20.3.l TRLRHL | Tasa por mesas y sillas en la vía pública | La terraza |
| Art. 15.e LIS (art. 28.1 LIRPF) | Lo que promociona las ventas es deducible | Publicidad, entera |
| Art. 32 LAU | Cesión del local al transmitir el negocio, sin consentimiento, con un 20 % más de renta y aviso en un mes | El traspaso (la renta del comprador no nos toca) |
| Arts. 27, 28.2, 33.1, 35.1, 46.b LIRPF; art. 40 RIRPF | Las existencias son rendimiento; los elementos afectos, ganancia del ahorro sobre el valor contable | Tributación del traspaso |
| Art. 9.1.º Ley 20/1991 | No sujeta la transmisión de una unidad económica autónoma | Traspaso sin IGIC |
| Arts. 29.1.a y 85.1 LIRPF; art. 22 RIRPF | El inmueble donde se ejerce la actividad está afecto y no imputa renta | Local propio |
| Art. 27.8 Ley 3/1991 | Encargar o inventar reseñas es práctica engañosa | Las reseñas no se compran |
| Art. 34.2 Ley 7/2011 de Canarias | El traslado necesita nueva comunicación previa | La tasa del traslado |
| DL 1/2025 de Canarias, arts. 32-36 | Tipos del IGIC | Tabla de arriba |

Sin comprobar:

- si una tienda o una heladería entran en la Ley 7/2011 o solo en la declaración responsable municipal;
- qué es «modificación sustancial» al ampliar el local;
- las consultas de la DGT y de la Agencia Tributaria Canaria sobre helados y bocadillos.

## Fase 3 al detalle: la sociedad limitada (9.6.0)

Reglas en `business_sl.py` (puras), casos de uso en `negocio.py`, el dinero en `EconomyService.business` (con
`BusinessMove.account`), la pantalla `sociedad` en `business_view.py` y el panel 🏢 **Sociedad** en
`cogs/business.py`. Verificado en el BOE consolidado el 21/10/2026 (tabla al final).

### Constituirla

- **Solo desde un negocio de autónomo:** se convierte el suyo, no se monta una nueva desde cero.
- **Qué hace falta:**
  - el negocio tiene que estar en un local alquilado: una sociedad en un local del socio sería una
    operación vinculada (art. 18 LIS), que el juego no lleva;
  - su curro tiene que dejarle administrarla (abajo, «Incompatibilidades»).
- **El capital**, a escoger: 1 €, 3.000 € o 10.000 € (100, 300.000 o 1.000.000 Y$).
  - Desde la Ley 18/2022, el mínimo es 1 € (art. 4.1 LSC).
  - Por debajo de 3.000 €, el 20 % del beneficio va a la reserva legal hasta que capital y reserva sumen
    3.000 €, y al liquidar el socio responde de lo que falte hasta 3.000 € (art. 4.1 LSC).
- **Lo que cuesta:**
  - notaría y registro con estatutos tipo: 60 € y 40 € con hasta 3.100 € de capital, o 150 € y 100 €
    (art. 5 del RDL 13/2010), con su IGIC;
  - la constitución está exenta del ITP-AJD (art. 45.I.B.11 TRLITPAJD).
  - El dueño mete en la caja el capital y lo de la notaría (aportación de socio), y la sociedad lo paga:
    es su primer gasto.
- **El negocio pasa entero a la sociedad** por lo que valía en sus cuentas (stock, mejoras, obra, fianza).
  - Es la aportación de una rama de actividad, neutral en el IRPF (art. 87 LIS) y no sujeta al IGIC (art.
    9.1.º de la Ley 20/1991).
  - Lo de la semana anterior a constituirla es del autónomo: no entra en Sociedades
    (`company["offset"]`).
- **Sin el 15 % de las entidades de nueva creación:** la actividad ya la ejercía como autónomo su socio
  único (art. 29.1.b LIS). Tributa como microempresa desde el primer día.
- **La cuota de autónomo sigue igual:** el dueño pasa a autónomo societario (art. 305.2.b LGSS). Su base
  mínima en 2026, 1.424,40 € al mes por el 30,6 %, es lo que ya cobra el juego. Al convertirlo no hay tarifa
  plana: no es un alta inicial (art. 38 ter de la Ley 20/2007).

### La caja es suya

- **Las ventas** van a la caja de la sociedad, sin retención de IRPF y sin los embargos de las deudas del
  dueño.
- **De la caja salen** las compras, las mejoras, la publicidad, el local, el traslado y, cada noche, la
  gestoría (1.000 Y$ con su IGIC).
- **Si la caja no llega**, el dueño tiene que meter dinero: el negocio no tira de su monedero.
- **`saldo`** ya no le enseña el local como algo que pague él.

### Del socio a su sociedad y al revés

- **💶 Aportar:** aportación de socio (cuenta 118 del PGC). No es ingreso de la sociedad ni se devuelve: se
  recupera con dividendos o al liquidarla. Exenta como operación societaria.
- **🏦 Prestar:** sin intereses. Simplificación: entre socio y sociedad, el art. 18 LIS pediría valor de
  mercado. Se devuelve cuando hay caja (↩️ **Devolverme**). Al liquidar, cobra el último: es subordinado
  (arts. 281 y 283 TRLC).
- **💼 Sueldo de administrador:**
  - es rendimiento del trabajo del dueño (art. 17.2.e LIRPF);
  - retiene el 35 %, o el 19 % si la sociedad factura menos de 100.000 € al año (art. 101.2 LIRPF, art.
    80.1.3.º RIRPF). La facturación se mide con lo vendido la semana pasada, pasado al año con la vara del
    IRPF;
  - entra en la Renta del lunes y, como una nómina, en el IMV y en el banco;
  - en Sociedades es gasto deducible: no es una liberalidad (art. 15.e LIS), porque los estatutos tipo
    prevén la retribución (art. 217 LSC).
- **💰 Dividendos:**
  - solo de lo disponible tras Sociedades y la reserva legal, y sin dejar el patrimonio neto por debajo del
    capital (art. 273 LSC);
  - con deuda con Hacienda, nada;
  - es rendimiento del capital mobiliario (art. 25.1.a LIRPF) con retención del 19 % (art. 101.4).
    Simplificación: como en el Rastro, ese 19 % es lo que paga, sin la escala del ahorro (19/21/23/27/30 %).

### El Impuesto sobre Sociedades

Cada domingo por la noche, la última de la semana fiscal (el año del juego).

- **La base** son las partidas de la semana (`TAX_ITEMS`):
  - ventas, compras, local, publicidad, amortización, tasas, sueldo, gestoría y notaría;
  - por devengo: el local cuenta la noche que toca, aunque no llegue para pagarlo;
  - no entran la cuota de autónomo (es del dueño), los dividendos ni el propio impuesto.
- **Las pérdidas** se compensan con las semanas siguientes sin límite (art. 26 LIS: el 70 % empieza en el
  millón de euros).
- **Microempresa** (factura menos de un millón al año): el 19 % hasta 50.000 € de base y el 21 % el resto
  (art. 29.1 LIS y disposición transitoria 44.ª de la Ley 7/2024 para 2026). Si factura más, el general,
  el 25 %. Los tramos en euros se pasan a la semana con la vara del IRPF.
- **Se paga de la caja.** Lo que no llegue queda como deuda con Hacienda: se cobra antes que nada las
  noches siguientes.
- Simplificación: no hay pagos fraccionados (art. 40 LIS); se liquida entero cada semana.
- **Del beneficio tras el impuesto**, primero la reserva legal: el 10 % hasta el 20 % del capital (art. 274
  LSC), o el 20 % del art. 4.1. Lo demás queda disponible para dividendos.

### Quiebra y responsabilidades

- **Causa de disolución:** si las pérdidas dejan el patrimonio neto por debajo de la mitad del capital (art.
  363.1.e LSC), el panel avisa.
  - El patrimonio neto es caja, stock y lo que queda por amortizar, menos deudas y el préstamo del socio.
  - Con dos noches (los dos meses del art. 367 LSC) sin remediarlo, el socio responde de todas sus deudas
    al liquidarla.
- **Liquidarla** (al cerrar, traspasarlo, en un desahucio o sin local):
  1. lo cobrado entra en la caja;
  2. se paga el último Sociedades, con el traspaso o la liquidación de las mejoras y la obra en la base;
  3. luego Hacienda, el casero y el préstamo del socio, por ese orden;
  4. lo que quede es la cuota de liquidación del dueño, con el 19 % de lo que pase de lo que metió.
- **Lo que la sociedad no llegue a pagar:**
  - a Hacienda se lo paga el administrador (derivación, art. 43.1.b LGT): lo que no tenga, a su deuda;
  - al casero, el socio lo cubre hasta lo que falte para 3.000 € de capital, o entero si dejó la causa de
    disolución sin remediar;
  - si no, se pierde: es una sociedad de responsabilidad limitada (art. 1 LSC).

### Incompatibilidades (`INCOMPATIBILITIES`)

Si el curro de la `pala` no deja administrar una sociedad, no se puede constituir ni cobrar sueldo de
administrador. Los dividendos sí: ser socio no está prohibido. De excedencia no hay incompatibilidad.

| Curro | Por qué | Norma |
|---|---|---|
| Política (todo el oficio) | Dedicación exclusiva de los altos cargos y los diputados | art. 13 Ley 3/2015; art. 157 LOREG |
| Funcionariado, Alta Administración | El complemento específico lleva el factor de incompatibilidad | art. 16.1 Ley 53/1984 |
| Sanidad pública | Personal estatutario con complemento por incompatibilidad | art. 76 Ley 55/2003; art. 16 Ley 53/1984 |
| Oficina, unicornio | Pacto de plena dedicación | art. 21.1 ET |
| Hostelería, con cafetería o heladería | Concurrencia desleal con tu empresa | art. 21.1 ET |

- Simplificación: el resto del funcionariado necesitaría el reconocimiento previo de compatibilidad (art. 14
  Ley 53/1984); el juego lo da por concedido.
- Las incompatibilidades miran solo la sociedad: el negocio de autónomo no cambia.

### Pluriactividad (art. 313 LGSS)

- **Quién:** quien cotiza a la vez por cuenta ajena (sus nóminas de la `pala`) y en el RETA (la cuota del
  negocio).
- **Cuánto:** cada domingo recupera el 50 % de lo que pase de 17.323,68 € al año (RDL 3/2026, art. 3.5),
  con el tope del 50 % de lo pagado al RETA. El límite se pasa a la semana con la vara.
- **Lo paga el Estado.** Simplificación: no tributa (en la vida real reduce el gasto de la cuota).
- Vale también para el autónomo, no solo para la sociedad.

### El Patrimonio

- Las participaciones cuentan a su valor teórico (art. 16.Uno de la Ley 19/1991): caja, stock y lo que
  queda por amortizar, menos lo que debe la sociedad (`repositories.business.companies_in`).
- Lo que le debe al socio no se resta: es un crédito del propio dueño.
- Salen en `perfil`, en `fortunas` y en el Impuesto sobre el Patrimonio.
- Simplificación: no se aplica la exención de la empresa familiar (art. 4.Ocho.Dos).

### Equilibrio

`python docs/negocio_sim.py --sociedad`: lo que se lleva el dueño al bolsillo cada semana, según el beneficio
del negocio antes de sus impuestos, con el mejor reparto entre sueldo y dividendos. La cuota del RETA es igual
en los dos casos.

| Beneficio por semana | Autónomo | Sociedad | Mejor sueldo | Diferencia |
|---|---|---|---|---|
| 50.000 | 37.561 | 32.506 | 42.500 | −5.055 |
| 100.000 | 76.959 | 75.154 | 72.150 | −1.805 |
| 250.000 | 178.252 | 174.911 | 184.300 | −3.341 |
| 400.000 | 263.029 | 273.343 | 180.550 | +10.314 |
| 700.000 | 420.454 | 465.756 | 180.050 | +45.302 |
| 1.200.000 | 677.954 | 785.703 | 190.800 | +107.749 |

- En impuestos, la sociedad compensa a partir de unos 300.000 Y$ de beneficio a la semana (unos 60.000 € al
  año con la vara), como en la vida real.
- Un negocio de la fase 2 deja 100.000-210.000 Y$ a la semana: solo los grandes (local grande, 8 turnos)
  llegan.
- Por debajo, la sociedad sirve para limitar lo que se arriesga, no para pagar menos.
- El mejor sueldo ronda los 180.000 Y$ a la semana: hasta ahí el IRPF del trabajo pesa menos que Sociedades
  más el 19 % de los dividendos.

### Normas de la fase 3 comprobadas (21/10/2026)

En el BOE consolidado:

| Norma | Qué dice | Uso |
|---|---|---|
| Art. 4.1 LSC (Ley 18/2022) | Capital mínimo de 1 €; por debajo de 3.000 €, reserva del 20 % y responsabilidad hasta 3.000 € | Capital y liquidación |
| Arts. 273 y 274 LSC | Dividendos solo si el patrimonio neto no baja del capital; reserva legal del 10 % hasta el 20 % del capital | Dividendos y reserva |
| Arts. 363.1.e y 367 LSC | Causa de disolución por pérdidas; el administrador responde si no convoca en dos meses | El aviso y responder de todo |
| Art. 45.I.B.11 TRLITPAJD; RDL 13/2010, art. 5 | Constitución exenta; aranceles de 60 + 40 € o 150 + 100 € | Lo que cuesta |
| Art. 29.1 LIS y DT 44.ª (Ley 7/2024) | 25 % general; microempresa en 2026, 19 % hasta 50.000 € y 21 % el resto; el 15 % de nueva creación no vale si el socio ya ejercía la actividad | Los tipos |
| Art. 26 LIS | Compensación de bases negativas | Las pérdidas |
| Art. 15.e LIS; art. 217 LSC | La retribución del administrador no es liberalidad si los estatutos la prevén | El sueldo es gasto |
| Arts. 17.2.e y 101.2 LIRPF; art. 80.1.3.º RIRPF | El sueldo del administrador es trabajo; retención del 35 %, o del 19 % con menos de 100.000 € de facturación | La retención |
| Arts. 25.1.a, 46, 66, 76 y 101.4 LIRPF | Dividendos en la base del ahorro, retención del 19 %; escala 19/21/23/27/30 % | Los dividendos |
| Art. 305.2.b LGSS; Orden PJC/297/2026 | El administrador con control es autónomo societario; base mínima de 1.424,40 € en 2026 | La cuota |
| Art. 38 ter Ley 20/2007 | Tarifa plana solo con alta inicial | Sin tarifa al convertir |
| Art. 87 LIS; art. 9.1.º Ley 20/1991 | Aportación de rama de actividad neutral; no sujeta al IGIC | Constituirla |
| Art. 18 LIS | Operaciones vinculadas a valor de mercado | No al local propio; el préstamo sin intereses es simplificación |
| Arts. 281 y 283 TRLC | El préstamo del socio es subordinado | Cobra el último |
| Art. 313 LGSS; RDL 3/2026, art. 3.5 | Reintegro del 50 % de lo cotizado por encima de 17.323,68 €, con tope | La pluriactividad |
| Art. 21.1 ET; arts. 14 y 16 Ley 53/1984; Ley 55/2003; arts. 13 y 15 Ley 3/2015; art. 157 LOREG | Concurrencia, plena dedicación y las incompatibilidades públicas | Incompatibilidades |
| Art. 1 LSC; art. 43.1.b LGT | Responsabilidad limitada; derivación al administrador de los impuestos que queden | La quiebra |

Sin comprobar:

- la deducibilidad del sueldo de un administrador único sin estatutos (doctrina);
- el efecto fiscal concreto del préstamo gratuito socio-sociedad;
- el importe de la tarifa plana de 2026.

## Préstamos al negocio (9.7.0)

Pedido el 22/10/2026: «que se puedan pedir préstamos en nombre del negocio, con acceso tanto en `banco` como en
`pala`». No es una fase: es un mostrador más del banco que mira el negocio.

- **Reglas:** `loans.py` (`business_rate`, `MAX_BUSINESS_LOANS`) y `business_sl.ebitda`.
- **El banco:** `HousingRepository` (`business_desk`, `take_business_loan_in`, `business_night_in`,
  `settle_business_night_in`, `business_loans_to_owner_in`). Van en `housing_loans` con `kind` `negocio` (autónomo) o
  `sociedad` y el id del negocio en `listing`.
- **El dinero:** `BusinessService` (`loan_desk`, `take_loan`, `repay_loan`, la noche y el cierre), con
  `BusinessMove.bank_loan`.
- **El mostrador:** `cogs/banco_negocio.py`; la portada del negocio lo abre con `banco.open_business`.

### Quién lo firma y cuánto presta

- **Autónomo:** lo firma él, porque el negocio no es una persona y responde con todo lo suyo (art. 1911 CC).
  - Entra en su monedero.
  - Cabe en su 35 %: nóminas, lo que deja el negocio (`work_net`) y el 70 % de las rentas, como un préstamo personal.
- **Sociedad:** lo firma ella, con el aval de su socio.
  - Entra en su caja.
  - Cabe en el 35 % de lo que dejó la semana pasada (`ebitda`: ventas menos gastos, sin amortizaciones ni intereses,
    al día) más lo que ingresa el socio (cero si está en pérdidas).
  - No lleva el punto de «sin nómina»: lo paga la sociedad.
- **Su cuota cuenta en el 35 % del dueño** siempre: la firma él o la avala (riesgo indirecto en la CIRBE, como
  cualquier aval del banco).

### Condiciones

- **Tipo:** Euríbor + 4 % (`BUSINESS_SPREAD`, el de una pyme sin historial) más el perfil del dueño, entre el 3 y el
  15 %.
- **Plazos y topes:**
  - 12, 24, 48 o 96 meses, de 1.000 a 750.000 Y$, con la comisión de apertura del 1 %;
  - hasta 2 por negocio, que no cuentan en los 3 personales.
- **No es crédito al consumo** (art. 2 de la Ley 16/2011: quien pide actúa en su actividad):
  - no hay desistimiento ni seguro de protección de pagos;
  - amortizar antes cuesta lo mismo que un personal (1 % o 0,5 %). Entre empresas sería lo pactado.
- **Con deuda con Hacienda no presta:** lo embargaría al entrar, como a un particular.

### Cada noche

- **Lo cobra la noche del negocio** de su caja, lo último: después del local, la gestoría y Sociedades.
  - Lo que no llega es primero del banco.
  - Las noches de `pisos` no lo tocan.
- **Lo que es gasto:** los intereses y la comisión, que van a la partida `intereses` de las cuentas (y a `TAX_ITEMS`). El
  capital no.
  - De autónomo, en su IRPF por lo pagado (criterio de caja).
  - De sociedad, en Sociedades por lo devengado.
  - El límite de los gastos financieros (art. 16 LIS: el 30 % del beneficio operativo, siempre deducible hasta un millón
    de euros al año) no se alcanza a esta escala.
- **Si no paga:** como un personal.
  - Con 2 cuotas seguidas, el dueño entra en el fichero de morosos.
  - Con 3 vence, y lo que queda pasa a deuda del dueño con el banco, que se cobra cada noche y se embarga (art. 607 LEC).
  - De una sociedad, el banco reclama a su socio, que la avalaba, y la sociedad pasa a deberle lo que pague por ella
    (art. 1838 CC): se suma a su préstamo de socio.

### Constituir la sociedad y cerrar

- **Al constituir la sociedad**, los préstamos del autónomo siguen a su nombre como personales:
  - el dinero se lo quedó él;
  - cambiar de deudor pide el consentimiento del banco (art. 1205 CC);
  - si pasaran a la sociedad, pagarlos con su caja sería un dividendo sin impuestos.
- **Al cerrar de autónomo** (cierre, traspaso, desahucio o sin local), siguen igual como personales y los cobra `pisos`.
- **Al liquidar la sociedad**, vencen:
  - su caja los paga después de Hacienda y del casero, y antes del préstamo del socio;
  - lo que no llegue lo asume el socio, que los avalaba, como préstamo personal con el mismo tipo y los meses que
    quedaban;
  - eso no entra en lo que responde por el capital (art. 4.1 LSC): ya lo paga como avalista.

### Patrimonio y `saldo`

- **Lo que debe una sociedad al banco** resta de su valor (`companies_in`) y de su patrimonio neto, que miran los
  dividendos (art. 273 LSC) y la causa de disolución. No resta del patrimonio del socio ni de `perfil`: solo lo avala.
- **En `saldo`:**
  - el de un autónomo sale como cuota de esta noche;
  - el de una sociedad, como un aval («Avalas el préstamo #… de tu sociedad»).

### Decidido por Claude (cambiable)

- De autónomo es un préstamo suyo con otro tipo, y no un préstamo de un negocio sin caja.
- La sociedad siempre con el aval de su socio, como piden los bancos a casi todas las microempresas.
- Euríbor + 4 %, 2 por negocio y la vara del 35 % también para la sociedad.
- Al cerrar o constituir, lo que queda pasa a personal en vez de vencer de golpe.

### Normas comprobadas (22/10/2026)

En el BOE consolidado:

| Norma | Qué dice | Uso |
|---|---|---|
| Art. 2 Ley 16/2011 | Consumidor es quien actúa al margen de su actividad comercial o profesional | Sin desistimiento ni seguro |
| Arts. 28 y 30 Ley 16/2011 | Desistimiento de 14 días; compensación de hasta el 1 % o el 0,5 % | Solo los personales; la del negocio copia el 30 |
| Art. 1911 CC | El deudor responde con todos sus bienes | El autónomo lo firma él |
| Art. 1205 CC | Cambiar de deudor pide el consentimiento del acreedor | Al constituir y al liquidar |
| Art. 1838 CC | El fiador que paga debe ser indemnizado por el deudor | La sociedad pasa a deberle al socio |
| Art. 28.1 LIRPF | Rendimiento de actividades según las normas del IS | Intereses deducibles del autónomo |
| Art. 16.1 LIS | Gastos financieros netos hasta el 30 % del beneficio operativo; siempre hasta 1 millón | No se alcanza |

Sin comprobar: que el diferencial de una pyme ronde los 4 puntos en 2026 (estimación).

### Simplificaciones

- Los intereses de las cuotas atrasadas no deducen cuando se pagan tarde (autónomo).
- 📒 Cuentas de `banco` cuenta lo prestado a los negocios, pero no suma sus cuotas en «Cobrado en cuotas de préstamos».
- No hay líneas ICO ni préstamos participativos.


## Fase 4 al detalle: la plantilla (9.8.0)

Pedida el 22/10/2026 con «implementa la fase 4 de los negocios». Reglas en `business_staff.py` (puras), casos
de uso en `negocio.py` (`hire`, `dismiss`, `raise_wage`, `make_permanent`, `train`, `staff_turns` y la noche),
las pantallas `plantilla` y `candidatos` en `business_view.py` y el panel 👥 **Plantilla** en `cogs/business.py`.
Normas comprobadas en el BOE consolidado el 22/10/2026 (tabla al final).

### Escalas

- **Dinero, a la de los sueldos de la `pala`** (100 Y$ el euro): una noche de jornada completa es un día de
  trabajo. Lo que la ley da en euros al año se reparte entre sus 365 días (`night_amount`): el salario mínimo de
  2026 (17.094 € al año, RD 126/2026) son **4.683 Y$ la noche**, algo menos que el P1 de la obra (4 turnos de
  1.800). Con la vara del IRPF (11.740 Y$ la noche), un empleado costaba tanto como todo lo que deja el negocio
  (medido con el simulador).
- **Plazos:** los meses pasan a noches a 7/12 de noche por mes, como la excedencia de la `pala` (el año son 7
  días), y los años, a semanas: el período de prueba de 3 meses son **2 noches**, el temporal de 6 meses son **4**
  y la bonificación de 3 años, **21**.
- **Indemnizaciones:** la misma parte de lo cobrado que en la vida real: 33 días por año son 33/365 de lo cobrado.
- **Multas** de una vez, a 100 Y$ el euro (como la notaría de la sociedad): 3.750 € son 375.000 Y$, unos 66
  días de salario mínimo, como en la vida real.
- **La retención del IRPF** de cada empleado es la de la `pala`: su semana de nóminas pasada a un año con la vara.
  Con el salario mínimo sale casi cero, como en la vida real.

### Los candidatos (`candidates`)

- Llegan **3 cada día**, siempre los mismos para el negocio y el día (salen de su semilla). Uno contratado no
  vuelve a salir.
- **Perfil** (`PROFILES`):
  - con experiencia (45 %);
  - joven, de 18 a 29 años (30 %): más rápido y menos atinado;
  - parado de larga duración, de 30 a 62 (25 %): atina más y va más despacio. Contratarle indefinido está
    **bonificado** (art. 21 del RDL 1/2023): 110 € al mes de la cuota de la empresa durante 3 años, o 128 € con 45
    años o más.
  - La de Garantía Juvenil (275 €) solo valió el primer año del RDL 1/2023 y no está.
  - El juego no mira el sexo ni ofrece los colectivos protegidos (víctimas, exclusión, discapacidad).
- **Tres habilidades** (0-100, `SKILLS`):
  - **acierto** (cuánto falla: un 30 % con 0, un 3 % con 100);
  - **rapidez** (de 3,2 a 1,2 segundos por pulsación: propinas y paciencia);
  - **trato** (pillar al listillo, acordarse de lo de siempre de un habitual y atreverse con el presupuesto caro).
- **Dos rasgos** de 16 (`TRAITS`), que no se contradicen y van con la edad:
  - madrugador o noctámbulo, que rinden más en su turno;
  - simpático, despistado, meticuloso o puro nervio;
  - estudiante, que solo quiere media jornada y aprende rápido;
  - veterano, que llega sabiendo y pide más;
  - quejica, tranquilo, leal, impuntual o ambicioso;
  - el especialista de un minijuego: barista, buena con las cuentas o manitas.
- **Lo que pide** (`ask`): el salario mínimo más la mitad de lo que sabe (con todo a 100, un 50 % más), por sus
  rasgos. Nunca por debajo del salario mínimo. A media jornada, la mitad.

### Contratar (`hire`)

- **Sin dinero:** el alta es gratis; la nómina se paga cada noche.
- **Contrato:**
  - **indefinido**;
  - **temporal** por circunstancias de la producción (art. 15.2 ET), que se acaba solo a las 4 noches;
  - **en B**, sin alta ni contrato.
  - Los dos primeros, con **período de prueba** de 2 noches (art. 14 ET: 3 meses en una empresa de menos de 25).
- **Jornada y turno:**
  - completa de mañana (8 a 16 h, franjas 0-3) o de tarde (16 a 24 h, franjas 4-7);
  - media de mañana (10 a 14) o de tarde (18 a 22), las horas de más clientes.
- **Cuántos:** 2 en un local pequeño, 4 en uno mediano y 6 en uno grande (`STAFF_CAP`).
- **Empieza ya,** en las franjas de su turno que aún no han acabado (el alta va antes de empezar, art. 139 LGSS).
- **Para la bonificación** hay que estar al corriente con Hacienda (art. 8 del RDL 1/2023) y no haber despedido de
  forma improcedente a un bonificado en los últimos 12 meses (7 noches, art. 11.4).

### Los turnos de la plantilla (`play_turn`, `staff_turns`)

- **Cada franja de su turno, el empleado juega** con el mismo `TurnGame` que el dueño: la cola, la paciencia, el
  cambio, el diagnóstico y el presupuesto. Su nota, sus propinas, las reseñas, la reputación y la fidelidad de los
  habituales salen igual que las del dueño.
- **Cómo juega:** falla según su acierto, sus rasgos (en su turno, ×0,8; en el otro, ×1,15; en su minijuego,
  ×0,75) y su humor (×1,3 por debajo de 40; ×1,6 por debajo de 20). Va a la velocidad de su rapidez. Lo de siempre
  de un habitual lo sabe según su trato; si no, lo pregunta (tarda tres pulsaciones más).
- **Los clientes** son los de la hora del medio de la franja y salen del **mismo cupo del barrio** que los del
  dueño: una plantilla que trabaja las horas en las que ya abres tú se come tu cupo.
- **Cuándo:** una tarea cada 10 minutos juega las franjas que ya han acabado. La noche juega las que falten del día
  antes de cerrarlo. No toca un negocio cuyo dueño está atendiendo un turno (comparten el stock): se espera a la
  siguiente pasada.
- **El dinero:** las ventas entran como las del dueño: con su retención a cuenta si es autónomo y en la caja si es
  una sociedad.
- **El azar es repetible:** el mismo turno da siempre lo mismo.
- **Aprenden trabajando:** cada turno sube cada habilidad un 0,6 % de lo que le falta para 100 (más o menos según
  sus rasgos).
- **Faltas:** un 2 % de los días (un 15 % si está quemado; el doble si es impuntual) no viene ni avisa. Ese día no
  cobra (`docked`) y cuenta para el despido disciplinario.

### La nómina de cada noche (`payslip`, `payroll`)

Cada noche, el bruto de su jornada (lo pactado; nada si faltó) con las cotizaciones de 2026 (Orden PJC/297/2026):

| | Trabajador | Empresa |
|---|---|---|
| Contingencias comunes | 4,70 % | 23,60 % |
| Desempleo (indefinido / temporal) | 1,55 / 1,60 % | 5,50 / 6,70 % |
| FOGASA | — | 0,20 % |
| Formación profesional | 0,10 % | 0,60 % |
| MEI | 0,15 % | 0,75 % |
| Accidentes de trabajo (DA 61.ª LGSS) | — | 1,50 % (hostelería), 1,65 % (comercio), 4,45 % (taller) |

- **La cuota de la empresa** baja con la bonificación, si la hay.
- **La retención** sale del bruto.
- **Lo que le llega** desaparece (el empleado no existe). Las dos cotizaciones y la retención van al Estado
  (`hacienda` las cuenta como Seguridad Social de la empresa e IRPF de otros).
- **Es gasto de la actividad:** el bruto más la cuota de la empresa, en tu IRPF por lo pagado o en Sociedades por lo
  devengado (partida `personal`).

**Orden de los recibos de la noche** (lo que no llega es de los últimos, `_tail_missing`):
1. lo que se debe a la plantilla y sus nóminas (los salarios van primero, art. 32 ET);
2. lo que la sociedad debía a Hacienda;
3. el local (deuda, alquiler, suministros, terraza y gestoría);
4. los seguros sociales y las retenciones;
5. la Inspección;
6. Sociedades;
7. las cuotas del banco.

**Si no llega:**
- **Lo que no cobra el empleado se le debe** (`wage_debt`, los atrasos) y es lo primero que se paga cada noche.
  Le baja mucho el humor.
- **Con 2 noches sin cobrar en una semana** (3 mensualidades en un año, art. 50.1.b ET desde la LO 1/2025), se va
  con la indemnización del despido improcedente (art. 50.2).
- **Los seguros sociales y las retenciones que no se pagan pasan a deuda con un recargo del 20 %** (art. 30.1 LGSS:
  10 % el primer mes y 20 % después; cada noche es más de un mes):
  - del autónomo, a su deuda con Hacienda (la cobran el embargo y el barrido de los lunes);
  - de la sociedad, a lo que debe a Hacienda (lo primero de cada noche, y en la liquidación se deriva al
    administrador).
  - Simplificación: las retenciones llevan el mismo recargo que la cotización.

### Humor, sueldo y formación

- **Humor** de 0 a 100. Se empieza en 70 con indefinido, 55 con temporal y 45 en B. Cada noche
  (`night_mood`):
  - sube 2 si cobra al menos un 10 % más de lo que vale (lo que pediría hoy por lo que sabe) y 1 si cobra lo que
    vale;
  - si cobra menos del 95 % de lo que vale, baja de 2 a 8 (aprende cada turno, así que siempre acaba valiendo
    algo más: hay que subirle el sueldo de vez en cuando);
  - sus turnos de hoy: +1 por cada S, +0,5 por cada A y −1 por cada D;
  - −1 en B y −20 si no cobra.
  - Los rasgos lo multiplican: el quejica, ×1,6 lo malo; el tranquilo, ×0,5 todo.
- **Quemado** (menos de 15): un 30 % de las noches se va (dimisión, sin indemnización). El leal no se va así.
- **💶 Subirle el sueldo** un 5, 10 o 20 %: le sube el humor. Bajarlo no se puede: sería una modificación
  sustancial (art. 41 ET), que pide causas que el juego no tiene.
- **🎓 Curso** de una habilidad (`train`):
  - 9.000 Y$ más IGIC de una academia; la noche siguiente, +15 puntos, y le sube el humor;
  - el crédito de formación de FUNDAE (420 € al año para una empresa de 1 a 5 trabajadores, la semana el año) se
    descuenta de la base: en la vida real se bonifica en la cuota;
  - a quien está en B no le llega: no está de alta;
  - es gasto de personal.
- **📄 Hacerle fijo:** el temporal pasa a indefinido y su contrato ya no se acaba.

### Despedir (`dismissals`, `dismiss`)

| Forma | Cuándo | Cuánto | Norma |
|---|---|---|---|
| No supera el período de prueba | Las 2 primeras noches | Nada | art. 14.2 ET |
| Disciplinario | Con 3 faltas sin justificar | Nada | art. 54.2.a y 55.7 ET |
| Objetivo | Si la semana pasada fue en pérdidas | 20 días por año (tope 12 mensualidades) y 15 de preaviso pagados | arts. 51.1, 52.c y 53 ET |
| Improcedente | Siempre | 33 días por año (tope 24 mensualidades) | art. 56.1 ET |
| Fin del temporal | Solo, al acabar | 12 días por año | art. 49.1.c ET |

- **La indemnización** es gasto deducible. Al empleado le está exenta (art. 7.e LIRPF) y no cotiza (art. 147.2.c
  LGSS). Se paga entera o no se despide.
- **Lo que se le debía de sueldo** sigue en los atrasos.
- **Un despido improcedente de un bonificado antes de los 3 años** obliga a devolver lo bonificado con el recargo
  (arts. 9 y 13 del RDL 1/2023, a la Seguridad Social) y deja 12 meses sin bonificaciones. El objetivo, el
  disciplinario, la dimisión y la prueba no.
- **A quien está en B** no se le despide: no vuelve.

### En B y la Inspección (`inspection`)

- **En B** cobra en mano su bruto entero: ni cotización ni retención. Lo pagado en B no es gasto deducible (no hay
  nómina).
- **La Inspección de Trabajo** pasa un 4 % de las noches en que hay alguien sin alta. La noche siguiente a que uno
  en B se vaya enfadado (lo cuenta), un 50 %.
- **Si los pilla:**
  - **la multa** del art. 40.1.e LISOS por cada uno: 3.750 €, el grado mínimo de la grave del art. 22.2, que sube
    un 20, 30, 40 o 50 % con 2, 3, 4 o 5 trabajadores o más;
  - **las cuotas** de todo lo que se les pagó en B (empresa y trabajador, art. 18.2 LGSS: se cotiza desde el primer
    día) con el recargo del 20 %;
  - **pasan a fijos** (art. 15.4 ET).
  - Ni la multa ni el recargo son gasto deducible (art. 15.c LIS).
- **No compensa en la media:** lo que se ahorra la empresa con un SMI en B (su cuota, ~1.500 Y$ la noche) es menos que el
  4 % de la multa (15.000 Y$ la noche).

### Cerrar

- **Traspasar:** la plantilla se va con el negocio sin despido (sucesión de empresa, art. 44 ET).
- **Cerrar o liquidar** (también por desahucio o sin local): despido objetivo de todos por el cierre (20 días por
  año, art. 52.c ET). Lo que se les debe y sus indemnizaciones son lo primero que se paga.
- **Lo que no llegue** lo paga el FOGASA (art. 33 ET). Simplificación: el FOGASA no mueve dinero (el empleado no
  existe) y no reclama nada al dueño.

### La sociedad

- La plantilla de una sociedad la paga su caja.
- **En Sociedades** son gasto `personal`, `despidos` y `formacion` (`TAX_ITEMS`). Lo pagado en B, las multas y los
  recargos (`negro`, `multas`) no.
- 📊 **Cuentas** enseña las partidas nuevas.


### La RIC y la ZEC (solo para sociedades)

Los dos incentivos fiscales de Canarias de la Ley 19/1994, en `business_sl.py` («RIC y ZEC»), en el Sociedades
de cada domingo (`settle_week`) y en 🏢 **Sociedad** (el desplegable 🌴 de la RIC y el botón 🏝️ de la ZEC).

**Reserva para Inversiones en Canarias (art. 27):**

- **Dotación:** la sociedad elige llevar cada domingo un 0, un 50 o un 90 % de la base de la semana (`ric_share`;
  el 90 % es el tope del art. 27.2). Esa parte no paga Sociedades.
- **Reserva indisponible:**
  - sale del beneficio de la semana, antes que la reserva legal, y no se puede repartir;
  - sin beneficio contable que la cubra, no hay dotación;
  - lo que vence y se integra no se vuelve a dotar.
- **Invertirla en 3 años** (3 semanas: `RIC_NIGHTS`) en activos nuevos en Canarias: las mejoras del local y la obra
  de un traslado (`ric_invest`, de la dotación más vieja a la más nueva).
- **Lo que no se invierte a tiempo** se integra en la base del domingo en que vence (art. 27.16). Simplificación:
  sin los intereses de demora.
- **Lo invertido se mantiene 5 años** (5 semanas, `RIC_KEEP_NIGHTS`). Después la reserva vuelve a lo disponible.
- **Al liquidar la sociedad**, lo que quede (sin invertir o sin haber mantenido lo invertido) se integra en el
  último Sociedades.
- **Autónomos, no:** la RIC del IRPF (una deducción en la cuota, art. 27.15) no está, porque la Renta del juego no
  lleva deducciones por actividad.

**Zona Especial Canaria (arts. 29 a 44):**

- **Tipo:** Sociedades al **4 %** (art. 43) hasta su base máxima: 1,8 millones de euros, más 0,5 millones por cada
  empleo por encima del mínimo (art. 44.6), pasados a la semana con la vara. Lo que pase, al tipo de siempre.
- **Actividad:** solo las del anexo. De los sectores del juego, el **taller** (división 45 de la NACE); el comercio
  minorista y la hostelería no están.
- **Empleo:** 5 empleos con alta en Gran Canaria y Tenerife (3 en el resto de islas, art. 31.2.e).
  - La inversión de 100.000 € (art. 31.2.d), a 100 Y$ el euro, no la alcanza nadie. Se puede dispensar creando
    más empleos que el mínimo, así que el juego pide **6**, y eso pide un local grande.
- **Plazo de inscripción:** hasta el 31/12/2026, el fin del reglamento europeo de ayudas (art. 29). Los ya inscritos
  siguen.
- **Cada domingo** tiene que mantener el empleo mínimo:
  - una semana sin él tributa al tipo de siempre;
  - con dos seguidas, sale del registro (`ZEC_MISSES`).
- **Simplificaciones:**
  - no hay tasa de inscripción;
  - la base máxima no mira el resto de requisitos;
  - el «de nueva creación» lo cumple la sociedad (se constituye nueva, aunque el negocio venga del autónomo).
- **Rara vez compensa:** con el simulador, un taller con seis empleados pierde dinero. Es el régimen de las grandes
  empresas, como en la vida real.

| Norma | Qué dice | Uso |
|---|---|---|
| Art. 27.2, 27.4, 27.8 y 27.16 de la Ley 19/1994 | RIC: hasta el 90 % del beneficio no distribuido; materializar en 3 años; mantener 5; si no, se integra con intereses | La RIC |
| Arts. 29, 31.2.d-e, 43 y 44.6 y anexo de la Ley 19/1994 | ZEC: inscripción hasta el fin del RGEC; 100.000 € y 5 empleos (o más empleos sin la inversión); 4 %; 1,8 M€ + 0,5 M€ por empleo de más; división 45 sí, 47 y 55/56 no | La ZEC |
### Equilibrio

`python docs/negocio_sim.py --plantilla`: el barrio del medio de cada sector con el cupo, días 15 a 28, media de 6
semillas. Lo que deja al día el negocio llevado por empleados indefinidos a jornada completa, con las tres
habilidades a 30, 50 o 70, frente al dueño solo (jugador bueno, 4 turnos):

| Sector | Dueño, 4 turnos | 1 de mañana (30 / 50 / 70) | Mañana y tarde (30 / 50 / 70) | Dueño + 1 de tarde (50) | 2 y 2 (50) |
|---|---|---|---|---|---|
| Cafetería | 16.800 | 600 / 5.500 / 10.200 | 7.400 / 12.200 / 14.500 | 18.200 | −1.100 |
| Tienda | 14.000 | −1.800 / 200 / 1.400 | 5.200 / 5.400 / 5.200 | 12.300 | −8.200 |
| Taller | 5.900 | −2.000 / −800 / 2.000 | −2.500 / −100 / 4.700 | 6.700 | −12.200 |
| Heladería | 12.800 | −1.200 / 1.700 / 5.300 | 1.800 / 3.500 / 4.900 | 9.900 | −11.700 |
| Ferretería | 17.100 | 5.100 / 6.500 / 7.700 | 13.300 / 13.200 / 12.900 | 19.000 | 1.000 |

- **Lo pasivo rinde menos que lo activo:**
  - una plantilla buena cubriendo mañana y tarde deja entre la mitad y lo mismo que el dueño jugando 4 turnos, sin
    gastar franjas;
  - con alguien que sabe poco, en la cafetería y la ferretería se gana algo y en el resto se pierde.
- **Lo que más pesa es el sector y el turno:**
  - la cafetería y la ferretería tienen margen por turno;
  - la tienda casi no (lo que sabe el empleado le cambia poco);
  - el taller y la heladería piden su hora (el taller, de mañana; la heladería, de tarde).
- **Contratar las horas en las que abres tú** se come tu cupo: en la heladería y la tienda, deja menos que abrir solo.
- **Cuatro a jornada completa pierden dinero en todos:** el cupo del barrio no da para tantos turnos.

### Pantallas y medido (22/10/2026)

| `screen` | Para qué |
|---|---|
| `plantilla` | Hasta 6 tarjetas: la cara (por su semilla, con el delantal del negocio), el contrato, el turno, el sueldo, el humor, las tres habilidades, los rasgos y los avisos; las nóminas de esta noche y lo que se debe |
| `candidatos` | Los tres de hoy con su perfil, la bonificación y lo que piden, y la oferta al elegido (bruto, lo que le llega y lo que cuesta; en B, el sello «sin alta») |

- **La portada** enseña a la plantilla de pie en la acera, con sus turnos y ventas de hoy.
- **El panel:** 👥 **Plantilla** junto a 🏠 Inicio. Dentro, el desplegable de la persona, 💶 la subida, 🎓 el curso, 📄
  hacerle fijo y 🚪 despedir (con lo que cuesta cada forma). En 👥 **Candidatos**, el candidato, el contrato y la
  jornada con el turno, y ✍️ Contratar.
- **Pintar antes del clic:** desde la portada se pinta la plantilla; desde la plantilla, los candidatos.
- **Dibujo** (`docs/medir_dibujos.py`, 4 hilos de este PC, ms con 1 proceso abierto / en caliente): plantilla
  121-128 / 120, candidatos 111-115 / 112.
- **Node contra Chrome 155** (las 62 muestras): la peor, 2,47/255; las nuevas, de 1,06 a 2,18.

### Normas de la fase 4 comprobadas (22/10/2026)

En el BOE consolidado:

| Norma | Qué dice | Uso |
|---|---|---|
| RD 126/2026, arts. 1 y 3 | SMI de 2026: 40,70 €/día, 1.221 €/mes, 17.094 € al año | El sueldo mínimo |
| Art. 14 ET | Prueba: 2 meses (3 en empresas de menos de 25), sin indemnización | Prueba de 2 noches |
| Art. 15.2 y 15.5 ET | Circunstancias de la producción: 6 meses (1 año por convenio sectorial) | El temporal |
| Arts. 49.1.c, 52, 53, 54, 55.7 y 56.1 ET | 12, 20 (tope 12 mensualidades, preaviso de 15 días pagable) y 33 días (tope 24) | Despidos |
| Art. 50 ET (LO 1/2025) | Tres mensualidades sin cobrar en un año: extinción con la indemnización del improcedente | Se va sin cobrar |
| Arts. 32 y 33 ET | Superprivilegio de los últimos 30 días de salario; el FOGASA paga salarios e indemnizaciones en insolvencia | Orden de cobro y cierre |
| Art. 44 ET | La sucesión de empresa mantiene los contratos | Traspasar |
| Orden PJC/297/2026, arts. 4, 16 y 33 | Tipos de 2026: 23,60/4,70, desempleo 5,50/1,55 (temporal 6,70/1,60), FOGASA 0,20, FP 0,60/0,10, MEI 0,75/0,15 | La nómina |
| DA 61.ª LGSS (RDL 3/2026) | Tarifa de accidentes con la CNAE-2025: 56 → 1,50 %, 47 → 1,65 %, 9531 → 4,45 % | La cuota de la empresa |
| Art. 30.1 LGSS (Ley 6/2017) | Recargos del 10 % y del 20 % (20 y 35 % sin presentar) | Lo que no se paga |
| RDL 1/2023, arts. 8, 9, 11.4, 13 y 21 | Parado de larga duración: 110 €/mes (128 con 45 años) 3 años; mantener 3 años; reintegro con recargo; 12 meses de exclusión | La bonificación |
| LISOS, arts. 22.2 y 40.1.e | Sin alta: grave, 3.750-7.500 € en grado mínimo por trabajador, +20/30/40/50 % con varios | La Inspección |
| Art. 18.2 LGSS | Se cotiza desde el inicio de la actividad | Las cuotas del B |
| Art. 7.e LIRPF | Indemnización obligatoria exenta hasta 180.000 € | El despido |
| DA 92.ª.Cinco Ley 31/2022 (prorrogada) | Crédito de FUNDAE de 420 € para empresas de 1 a 5 trabajadores | El curso |

Sin comprobar o simplificado:

- la «presunción de alta» del B no está escrita así en la ley (la obligación de cotizar nace con la actividad, art.
  18.2 LGSS);
- el modelo 111 trimestral y sus recargos de la LGT (del 1 al 20 %): las retenciones llevan el de la cotización;
- las vacaciones (art. 38 ET), las pagas extra (van prorrateadas en el sueldo) y el preaviso de la dimisión;
- el encadenamiento de temporales (art. 15.5 ET): un temporal se acaba y no se renueva;
- el FOGASA no reclama al dueño lo que pagó (en la vida real se subroga);
- el reintegro de la bonificación cuando se va por el art. 50 ET.

### Decidido por Claude (cambiable)

- Los sueldos a la escala de la `pala` y no a la vara del IRPF.
- Las 3 habilidades y los 16 rasgos.
- El tope de empleados por tamaño de local.
- Los turnos de mañana y tarde, y las horas de la media jornada.
- Que trabajen solos cada 10 minutos, con el mismo minijuego.
- El humor y sus umbrales.
- El curso de 9.000 Y$ y +15.
- La Inspección al 4 % por noche.
- Que se vaya con 2 noches sin cobrar.
- El recargo del 20 % a todo lo que no se paga.


## Fase 5 al detalle: las cadenas (9.11.0)

Pedida el 24/10/2026 con «implementa la siguiente fase de los negocios». Reglas puras en `business_chain.py`
(la nave, importar, reponer y el precio de venta) y en `business_staff.py` (el gerente); casos de uso en
`negocio.py`; la nave se guarda en `business_nave`; las pantallas `cadena`, `nave` y `venta` en
`business_view.py`; y en el panel, 🗺️ **Cadena**, 🏭 **Nave** y 💼 **Vender la sociedad** (`cogs/business.py`).
Normas comprobadas en el BOE consolidado el 24/10/2026 (tabla al final).

### Varios locales (`MAX_LOCALES`)

- **Hasta 4 locales por persona**, cada uno un `Business` con su sector, su barrio, su tamaño, su stock, su
  plantilla, sus reseñas y sus cuentas. Cada uno se busca por su número (`biz`); sin él, el más antiguo.
- **Lo que es de la persona** no se multiplica:
  - las franjas (los turnos de todos los locales gastan las mismas, `franjas_today`) y la batería;
  - la cuota de autónomo, una por persona (la tarifa plana es de la primera alta, no de cada local);
  - la pluriactividad, una vez por semana.
- **Dos del mismo sector en el mismo barrio, no:** se repartirían el cupo.
- **➕ Otro local** (en 🗺️ Cadena) abre el montaje con el rótulo del local de ahora: una cadena suele llevar la
  misma marca.
- **Cerrar un local** de autónomo deja los demás como estaban.

### La sociedad lleva toda la cadena

Una persona lleva todos sus locales como autónomo o todos en su sociedad, nunca mezclados. Así lo que va de
la nave a un local no es una venta entre dos personas.

- **Constituirla** convierte todos los locales a la vez (`company_id`: el `id` del local con el que se
  constituye, que da su caja aunque ese local cierre).
  - Ninguno puede estar en un local propio (operación vinculada, art. 18 LIS), y la nave tampoco.
  - El curro no puede ser incompatible con ninguno de los sectores.
  - Lo de esta semana antes de constituirla (de todos los locales y de la nave) va al `offset`.
- **Abrir otro local** siendo sociedad lo abre la sociedad: lo paga su caja y tributa con ella. Si está en la
  ZEC, solo talleres (el anexo).
- **El estado de la sociedad** (`company`) es uno, y cada local guarda una copia. `save_in` copia el de uno
  a los demás de la misma sociedad.
- **Cada noche** se cierran los locales de cada dueño del más antiguo al más nuevo (`_close_owner_nights`).
  El último de la sociedad que cierra esa noche:
  - paga **una gestoría** (no una por local);
  - el domingo, **un solo Impuesto sobre Sociedades** con lo de todos (los demás ya han apuntado su noche), lo
    de la nave y lo de los locales que cerraron esa semana (`company["carry"]`);
  - mira la causa de disolución con el patrimonio de todos.
- **Microempresa:** la cifra de negocios es la de todos los locales. Es la sociedad la que factura, y el art.
  29.1 LIS remite al art. 101.3, que suma las entidades de una misma persona.
- **El patrimonio neto** y su valor en el Patrimonio (`companies_in`) suman todos los locales y la nave, con la
  caja una vez.
- **La ZEC** cuenta los empleos de todos los locales, pide que todos sean talleres y aplica el mínimo de empleos
  de la isla que más pide.
- **Cerrar un local de una sociedad que tiene más** no la liquida:
  - lo cobrado (stock, mejoras o traspaso) y la fianza entran en su caja;
  - la deuda con el casero y los finiquitos salen de ella;
  - a su plantilla se la despide como en un cierre (despido objetivo: cerrar un centro con 6 personas o menos no
    llega a los umbrales del colectivo del art. 51.1 ET);
  - sus préstamos pasan a otro local de la sociedad (`move_business_loans_in`): la deudora es la sociedad;
  - lo de su semana entra en el Sociedades del domingo (`carry`).
- **Cerrar el último** sí la liquida, como en la fase 3, y con ella la nave.

### La nave (`Nave`, `open_nave`)

- **Alquilada** en cualquier barrio, o en una **nave entera tuya de `pisos`** (no siendo sociedad).
- **Tres tamaños:**

| Tamaño | Metros | Lotes que caben | Suministros (noche) | Alquiler en un barrio obrero (Telde) |
|---|---|---|---|---|
| Pequeña | 100 m² | 50 | 300 Y$ | 6.300 Y$ + IGIC |
| Mediana | 200 m² | 110 | 500 Y$ | 12.600 Y$ + IGIC |
| Grande | 400 m² | 240 | 800 Y$ | 25.200 Y$ + IGIC |

- **El alquiler** sale de los metros, el barrio y el `rent_factor` de una nave industrial de `pisos`, a la misma
  escala que los locales. Va con su IGIC y la retención del 19 % del casero.
- **Abrirla:**
  - la tasa de la declaración responsable (5.000 Y$). Un almacén sin mercancía peligrosa es actividad inocua y
    la Ley 7/2011 de Canarias no le pide comunicación previa (art. 2.3.c), pero las ordenanzas suelen pedir la
    declaración, que tiene su tasa (art. 20.4.i TRLRHL);
  - la fianza de dos mensualidades (art. 36.1 LAU).
- **Reparte a los locales de su isla**, y solo a esos.
- **Cada noche,** antes que los locales:
  - llegan los contenedores;
  - se paga lo de la nave;
  - con 3 noches sin pagar (o si la nave propia deja de ser tuya), se cierra: lo de dentro se liquida a la
    mitad y lo que venía en camino se pierde.
- **Dejarla:** el mayorista recompra lo de dentro a la mitad (venta de la actividad) y vuelve la fianza. Con
  contenedores en camino, no se puede.
- **Las cuentas de la nave** (sus compras y su alquiler) van en la propia nave y, de una sociedad, a su
  Sociedades. Sus avisos salen en el panel con los del local.

### Importar (`import_quote`, `import_container`)

- **Un contenedor** son 20 lotes de un producto, directo del fabricante de la Península:
  - el 70 % de lo que cobra el mayorista;
  - el flete (4.000 Y$);
  - el **AIEM** de lo que está en su anexo, sobre el valor en aduana (mercancía y flete);
  - el **IGIC de la importación**, al tipo del producto, sobre el valor en aduana más el AIEM;
  - el agente de aduanas (1.000 Y$ más IGIC).
- **Llega en dos noches:** la mañana del día siguiente a la segunda.
- **Solo lo que no caduca:** un contenedor dura semanas y en la nave se estropearía. Se quedan fuera el pan, la
  leche, la fruta, los zumos, los bocadillos, los dulces, los helados y los batidos.
- **Cabe lo que cabe:** lo que hay y lo que viene no pueden pasar de los lotes de la nave.
- **Solo lo que vende algún local de la isla.**
- **Impuestos:**
  - entrar desde la Península es una importación para el IGIC (art. 8.1 de la Ley 20/1991: la Península es
    territorio tercero), al tipo de la entrega del mismo bien (art. 32.4 del DL 1/2025);
  - el pequeño empresario no repercute IGIC en sus ventas, pero paga el de sus importaciones (art. 91.b) y no lo
    deduce: es coste;
  - el AIEM (arts. 92 y 94 y anexo 2.º), al tipo de cada producto: café tostado 15 %; refrescos 5 %; detergentes
    5 %; pinturas al agua 10 %. Los recambios, la tornillería, las bombillas, las herramientas y las conservas de
    pescado no están en el anexo, y el aceite de motor salió en 2026 (Ley 9/2025);
  - interpretaciones del proyecto: el cortado y el barraquito, como el café; el granizado, como un refresco; la
    pintura, al agua; la fontanería y las cerraduras, fuera del anexo;
  - todo es gasto de la actividad el día que se paga (compras), como lo del mayorista, y `hacienda` cuenta el
    AIEM aparte.
- **🏭 Traer de la nave** (en el almacén de cada local): 1, 5 o todos los lotes de ese producto, sin pagar (ya
  se pagaron). Simplificación: el reparto en la isla no cuesta.

### El gerente (`business_staff.MANAGER`, `_manage`)

- **Se contrata entre los candidatos** con el contrato «Gerente»:
  - uno por local, indefinido y a jornada completa, con alta (compra en nombre del negocio: en B, no);
  - pide lo de un dependiente que sepa lo mismo, un 20 % más (`manager_ask`);
  - no ocupa sitio en el mostrador (`STAFF_CAP` cuenta a los demás) y no atiende franjas;
  - cobra su nómina como los demás: humor, faltas (decididas la noche, antes de la nómina) y despidos.
- **Cada noche, después de la del local:**
  - **repone** hasta día y medio de lo que se vendió ese día (`Business.sold_days`), y al menos un lote de cada
    producto. Trae primero de la nave de su isla (gratis) y compra lo que falte al mayorista con lo que haya en
    la caja, de lo que más se vende a lo que menos;
  - **cubre las bajas:** a quien acaba su temporal, dimite o se va sin cobrar le sustituye el mejor candidato
    del día siguiente, indefinido, con lo que pide y en el mismo turno. Los despidos del dueño no son bajas.
- **La noche que falta,** ni repone ni cubre.

### Vender la sociedad (`sale_offer`, `sell_company`)

- **Desde 🏢 Sociedad.** Hacen falta 3 semanas cerradas en Sociedades (los «tres ejercicios») y no deber nada a
  Hacienda, a los caseros ni a la plantilla.
- **El precio** es el mayor entre el patrimonio neto y la media de los resultados de esas tres semanas por
  cinco (capitalizar al 20 %, art. 37.1.b LIRPF). El comprador del juego paga justo eso. Además paga el
  préstamo del socio por su valor.
- **Se va todo:** la caja, los locales, la nave y la plantilla, que sigue con sus contratos (no cambia el
  empleador: vender participaciones no es la sucesión del art. 44 ET). Los locales se apuntan como cerrados con
  el motivo `venta`.
- **Los préstamos del banco** los cancela el comprador. Simplificación: en la vida real el socio seguiría
  avalando salvo que el banco le libere (arts. 1205 y 1847 CC).
- **Impuestos:**
  - la venta de participaciones está exenta de IVA e ITP (art. 338 de la Ley 6/2023);
  - para el socio es una ganancia patrimonial de la base del ahorro (arts. 33 y 46.b LIRPF): el precio menos lo
    que metió, capital y aportaciones (que integran su valor de adquisición, art. 35.1 LIRPF y consulta V1583-24
    de la DGT);
  - paga el 19 % en el momento, como los dividendos. Simplificación: la escala real del ahorro va del 19 al 30 %;
  - una pérdida no se compensa.
- **El dinero:** lo que paga el comprador se crea, y la caja de la sociedad sale del juego con él
  (`BusinessMove.sale`).

### El banco

Cada local de una cadena pide sus préstamos (2 como mucho cada uno). El mostrador 🏪 Negocio de `banco` se
abre con el local de la portada, y con varios locales lleva un botón para pasar al siguiente.

### Equilibrio

`python docs/negocio_sim.py --cadena` (barrio del medio de cada sector, con el cupo, días 15 a 28 y 6 semillas).
Lo que gasta un local al día en el mayorista y lo que se ahorraría importando lo que no caduca:

| Sector | Gasto en el mayorista (dueño 4 turnos / plantilla mañana y tarde) | Ahorro al importar | Locales para pagar una nave pequeña en Telde (7.062 Y$ la noche) |
|---|---|---|---|
| Cafetería | 15.400 / 20.400 | 360 / 470 | 15-20 |
| Tienda | 47.700 / 56.900 | 7.700 / 9.200 | 1 |
| Taller | 14.700 / 21.600 | 3.300 / 4.800 | 2-3 |
| Heladería | 7.500 / 8.800 | 30 | nunca |
| Ferretería | 33.700 / 44.100 | 8.200 / 10.800 | 1 |

- **La nave es del comercio y del taller:**
  - la tienda y la ferretería la pagan con un local y sacan dinero con dos;
  - el taller, con dos o tres;
  - la cafetería y la heladería casi no tienen qué importar: su café paga un 15 % de AIEM y lo demás caduca.
    Como en la vida real, el café y el helado se tuestan y se hacen en las islas.
- **Un contenedor** ahorra de unos 4.000 Y$ (café) a 76.000 (bebidas) frente al mayorista.
- **Un gerente** con todo a 50 cuesta unos 9.300 Y$ la noche, bruto y cuota de la empresa. Es lo que cuesta no
  estar: un local llevado por la plantilla deja de 0 a 14.000 Y$ al día (fase 4), así que el gerente solo
  compensa en los que más venden, o cuando no vas a reponer tú.
- **Vender:** con 100.000, 250.000 o 500.000 Y$ de beneficio a la semana, el comprador paga 500.000, 1.250.000 o
  2.500.000 Y$. Es poco más de un mes de beneficios: vender es para quien se va, no un atajo.

### Pantallas

| `screen` | Para qué |
|---|---|
| `cadena` | Hasta 4 tarjetas de local (rótulo, sector, barrio e isla, estrellas, caja de hoy, turnos, plantilla, gerente y aviso) y la de la nave |
| `nave` | Sin nave, los tres tamaños para alquilarla; con ella, lo que hay dentro, lo lleno que está, lo que viene y el desglose del contenedor del producto elegido |
| `venta` | Los resultados de las tres semanas, su media por cinco, el patrimonio, el precio, la ganancia, su IRPF y lo que te llevas |

- **El inicio** dice cuántos locales más tienes.
- **La portada** lleva la chapa de la cadena y el gerente.
- **La plantilla** marca al gerente, y la oferta de los candidatos, el sello de gerente.

### Logros (67, 4 secretos)

- **Locales:** 2, 3 y 4 a la vez; 2, 3 y 4 sectores a la vez; las dos islas; 2 y 4 locales con el mismo nombre;
  1, 3 y 10 locales abiertos teniendo ya otro.
- **La sociedad:** cerrar un local sin liquidarla; constituirla con 2 y con 4 locales.
- **La nave:** abrirla (con su historia) y 3 naves; una propia; la mediana y la grande; llenarla; dejarla; y
  perderla (secreto).
- **Importar:** 1 (con su historia), 10, 50 y 250 contenedores; lo pagado, el AIEM y lo ahorrado por escalones;
  contenedores sin AIEM; los que llegan; los lotes traídos a mano.
- **Gerentes:** contratar 1 y 10; 2 y 4 a la vez; las noches que repone, las que trae de la nave y las bajas
  que cubre; las que falta (secretos).
- **Vender:** 1 (con su historia) y 3; el precio, los locales, la ganancia y el IRPF por escalones; y vender con
  pérdida (secreto).

### Normas de la fase 5 comprobadas (24/10/2026)

En el BOE consolidado:

| Norma | Qué dice | Uso |
|---|---|---|
| Art. 37.1.b LIRPF | Valor de las participaciones que no cotizan: no menos que el mayor entre el patrimonio neto del último balance y la media de los resultados de tres ejercicios capitalizada al 20 % (los dividendos y las reservas cuentan como beneficio) | El precio de venta |
| Art. 35.1 LIRPF; consulta V1583-24 de la DGT | El valor de adquisición; las aportaciones a fondos propios lo integran | La ganancia de la venta |
| Arts. 46.b, 66 y 76 LIRPF | Ganancias de transmisiones en la base del ahorro; escala del 19, 21, 23, 27 y 30 % | Simplificación: el 19 % |
| Art. 338 Ley 6/2023 | La transmisión de valores está exenta de IVA e ITP (salvo sociedades inmobiliarias) | Sin ITP en la venta |
| Arts. 29.1, 101.3 y DT 44.ª LIS | Microempresa en 2026: 19 % hasta 50.000 € y 21 % el resto; la cifra de negocios cuenta las entidades de una misma persona | La sociedad de la cadena |
| Art. 8.1 Ley 20/1991 | La Península es territorio tercero: la entrada de bienes es importación | El IGIC de importar |
| DL 1/2025 de Canarias, arts. 32.4, 89, 90.1 y 91.b | Importación al tipo de la entrega; el pequeño empresario paga el IGIC de sus importaciones | El IGIC de importar |
| DL 1/2025 de Canarias, arts. 92 y 94 y anexo 2.º | El AIEM, al mismo tipo en la importación y en la entrega, por producto | Los tipos del AIEM |
| Ley 9/2025, DF 9.ª.18 | Saca del AIEM los productos del petróleo desde 2026 | El aceite de motor, sin AIEM |
| Ley 7/2011 de Canarias, art. 2.3.c; art. 20.4.i TRLRHL | La actividad inocua no pide intervención previa; la tasa municipal vale también para la declaración responsable | La tasa de la nave |
| Arts. 51.1, 52.c y 53.1.b ET | Colectivo con 10 trabajadores en empresas de menos de 100 o el cierre total con más de 5; si no, objetivo con 20 días por año | Cerrar un local de la cadena |
| Art. 44 ET | La subrogación es por cambio de titular | Vender participaciones no la activa |
| Arts. 1205, 1847 y 1156 CC | Cambiar de deudor pide el consentimiento del acreedor; vender participaciones no extingue el aval | Simplificación: el comprador cancela los préstamos |

Sin comprobar:

- la Decisión (UE) 2020/1792 del Consejo, que autoriza el AIEM hasta 2027 (EUR-Lex no cargó);
- una sentencia que diga que vender participaciones no es sucesión de empresa (el razonamiento sí);
- las ordenanzas de cada ayuntamiento sobre la declaración responsable de un almacén.

### Decidido por Claude (cambiable)

- Hasta 4 locales; todos de autónomo o todos de la sociedad.
- Una gestoría y un Sociedades por sociedad, en la noche de su último local.
- La nave: tres tamaños, solo para su isla; contenedores de 20 lotes al 70 % del mayorista, con 4.000 Y$ de
  flete, 1.000 de aduanas y dos noches de viaje; solo lo que no caduca.
- El gerente: uno por local, un 20 % más que un dependiente, repone día y medio y cubre las bajas con el mejor
  candidato.
- El comprador paga justo el valor del art. 37.1.b y cancela los préstamos de la sociedad.

### Simplificaciones

- Si se cierra un local en el que se invirtió la RIC, no se mira que lo invertido se mantuviera 5 años.
- Las importaciones vienen de la Península: sin el arancel de la UE de lo que llega de fuera.
- El reparto de la nave a los locales no cuesta.
- La pérdida de vender la sociedad por debajo de lo que metiste no se compensa.
