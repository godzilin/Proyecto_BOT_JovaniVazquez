# El Ultramarino: clientes sin simular en el servidor (diseño)

Diseño, sin código, de los clientes del Ultramarino (un negocio jugable dentro de un interior, al estilo de
«Supermarket Together»: los miembros reponen estanterías y cobran, y llegan clientes que compran). Sigue la
Biblia: el servidor no simula, valida y reparte en ticks, binario donde hay ritmo y con topes. Parte de la
base de los interiores (fase 4 de `docs/mundo-celdas.md`).

## La idea: el servidor decide qué pasa y cuándo; la página lo anima

Un cliente no es un cuerpo que el servidor mueve, sino un **guion** que el servidor decide al crearlo y que
la página reproduce:

- Cada tienda abierta tiene un **tick de tienda** de 1 s (no el de 10 Hz de la sala). En cada tick, el
  servidor mira si toca que llegue un cliente (un ritmo según la hora, la fama y la gente que hay dentro) y,
  si toca, lo crea con una **semilla** y una **lista de la compra**: qué estanterías visita (sus números de
  usable), cuánto coge de cada una y su paciencia. Eso son unos pocos bytes.
- Del guion salen los **momentos** de cada cliente, calculados una vez al crearlo: llega a la estantería 3
  en el segundo 12, a la 7 en el 21, a la caja en el 34, se va si no le cobran en 20 s. El camino (puerta →
  estanterías → caja → puerta) lo calcula igual la página, con la misma semilla y la rejilla de paso del
  interior (sus objetos con colisión), así que se ve andar sin que el servidor mande ni una pose.
- En cada tick, el servidor solo mira **qué momentos caen en ese segundo**: si en la estantería hay
  existencias, las resta; si no, el cliente se enfada (fama) y salta a la siguiente. Al llegar a la caja
  espera a que un miembro le cobre (usar la caja) o, sin nadie, se va. El cobro es dinero: pasa por
  `EconomyService` con el IGIC de la venta y el IRPF o Sociedades de `pala` (el negocio es de `negocios`), y
  la Renta como siempre.

La página no decide nada: anima lo que dice el guion y, si un momento del servidor dice otra cosa (la
estantería estaba vacía), el cliente cambia de camino al recibirlo.

## Estado en tablas

- **Existencias por estantería**: la tabla de los interiores (`universo_interior_estado`) se queda para lo
  binario; las estanterías van en una hermana, `universo_existencias (guild_id, lugar, n, producto,
  cantidad, updated_at)`, por lugar (la tienda de su dueño, `ultramarino@<dueño>`) y número de usable.
- **La tienda**: fama, caja del día y precios, en las tablas de `negocios` (es un negocio de `pala`).
- **Los clientes no se guardan**: viven en memoria mientras la tienda está abierta (con tope, por ejemplo 12
  a la vez por tienda) y se pierden al cerrar o reiniciar, como un cliente que se va.
- Las existencias se escriben por lotes (cada 30 s y al cerrar), no en cada compra: lo que se pierde en un
  reinicio es medio minuto de ventas, que tampoco se cobraron.

## Coste para 50 jugadores

Supuesto: 50 jugadores, 10 tiendas abiertas a la vez, 12 clientes como mucho en cada una.

- **CPU:** cada tick de tienda recorre sus clientes y mira si alguno tiene un momento en ese segundo:
  ~120 comprobaciones por segundo entre todas, decenas de microsegundos. Crear un cliente (sortear la lista
  y calcular sus momentos) es menos de 1 ms y pasa unas pocas veces por minuto. Muy por debajo del 3-5 % de
  un hilo que ya gasta la sala con 50 jugadores.
- **Bytes:** por cliente nuevo, un JSON de ~80 bytes a los que están en esa tienda (su capa). Por cada
  momento que cambia algo, un aviso binario por tick de tienda con lo cambiado (`u8 n, u16 cantidad` por
  estantería, `u8 cliente, u8 suceso` por cliente): unos 10-50 bytes por segundo y tienda en hora punta.
  Con 50 jugadores repartidos en 10 tiendas, menos de 0,1 KB/s por jugador, frente a los ~4 KB/s de las
  poses de la sala.
- **Base de datos:** un lote de existencias cada 30 s por tienda abierta y una escritura por cobro (el
  dinero no se agrupa).
- **Página:** cada cliente es un robot más (el mismo modelo y material que los jugadores, `Remotos`), con
  el mismo tope de dibujados; 12 clientes son ~12 llamadas de dibujo con sombras.

## Lo que se reutiliza de la base de los interiores

- El interior y su capa: la tienda es un interior con dueño (`<id>@<dueño>`, como un piso) y los clientes
  solo se mandan a los de esa capa (`Salas.announce(..., layer=)`).
- Los usables por número: una estantería y la caja son usables (`uso` nuevos, `estanteria` y `caja`), con
  la misma validación de cercanía, permiso (el dueño y sus empleados) y ritmo; reponer es usar una
  estantería con algo en la mochila.
- `/api/interior` con acciones nuevas (`reponer`, `cobrar`) y el aviso `{t: "uso"}` para lo raro.
- El montaje de la página (`interiores/montaje.ts`): las estanterías se pintan como usables con su estado
  (vacía, a medias, llena) en lugar de binario.
- Lo que falta y no está en la base: el tick de tienda (una tarea por tienda abierta, que se cierra sin
  nadie dentro, como una sala vacía), la rejilla de paso del interior y el reloj común para que todos vean
  al cliente en el mismo sitio (el servidor manda la hora de creación y la página resta su retraso).
