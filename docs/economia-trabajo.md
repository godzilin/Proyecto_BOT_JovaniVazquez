# Economía del trabajo (`pala`), versión 8.0.0

Por qué los sueldos, los días de cada puesto, la pensión, la experiencia y la veteranía de
`pala` tienen las cifras que tienen. Todas salen de `docs/economia_sim.py`, que usa las
funciones reales del bot:

- la nómina con el tipo estable;
- la declaración de los lunes, con los pisos y sus arrastres;
- el Patrimonio con el límite conjunto;
- la pensión con la jubilación activa;
- el ritmo de ascensos medido con las partidas reales (`docs/ritmo_pala.py`).

Para volver a sacarlas: `PYTHONIOENCODING=utf-8 python docs/economia_sim.py`.

## Objetivos

- **El Falcon** (53,5 M con su IGIC) en unos 10-12 meses de juego, para quien ficha a
  diario y compra pisos. Solo con el sueldo, más del doble o no llega.
- **Rápido al principio y constante después:** cada ascenso del tronco casi duplica el
  neto; en las ramas, la segura deja un 15-20 % más de neto por ascenso y la arriesgada,
  un 25-30 %.
- **El impuesto se siente como en la vida real:** P1 casi sin IRPF, P5 en torno al 33 % y
  lo más alto de una rama entre el 44 y el 48 % (IRPF y Seguridad Social sobre el bruto
  de una semana a jornada completa).
- **Una carrera que dura lo que el camino al Falcon:** el primer P10 hacia el día 100 y la
  segunda rama hacia el 165.
- **Acumular sale a cuenta:**
  - cambiar de oficio después de acabar uno recupera el neto diario en unos dos meses;
  - quien hace todos los oficios cobra cada vez más, sin abrir bucles de dinero.
- **Ajustable:** un oficio nuevo solo pone su P3 y la clase de sus ramas (receta «Añadir
  un oficio» de `CLAUDE.md`).

## Reglas

| Qué | Regla | Dónde |
|---|---|---|
| P1-P3 | Las fija cada oficio (1.650-1.800, 2.500-2.800, 4.000-5.000 por turno) | `work_catalog.py` |
| P4 y P5 | P3 × 1,6 y P4 × 1,5 | `work.TRUNK_STEPS`, `PayCurve` |
| Ramas | Cada puesto, la base del anterior × 1,24 (segura) o × 1,34 (arriesgada) | `work.BRANCH_GROWTH` |
| Formaciones | De 2 a 4 bases del puesto al que dan acceso | `PayCurve.course` |
| Días | Tronco 2, 3, 4, 5 y 4; ramas 13, 15, 18 y 21 | `work.BRANCH_DAYS` |
| Pensión | `tope × (1 − ∏(1 − peso × calidad))`, con tope en la base máxima de cotización (61.214,40 € al año, 41.928 Y$ al día) y pesos de 0,30 (segura) y 0,20 (arriesgada) | `work.pension_amount` |
| Calidad | Bruto medio diario cotizado en la rama frente al tope (0-1) | `work.contribution_quality` |
| Jubilación activa | Trabajando se cobra el 45 % la primera semana desde la primera jubilación, luego 55, 65, 80 % y entera desde la quinta (art. 214 TRLGSS); sin trabajar, entera | `work.active_share` |
| Experiencia | Días y tareas × 0,4 por rama de otro oficio en el tronco (mínimo 0,2) y × 0,6 en las ramas (mínimo 0,3) | `work.experience_factor` |
| Veteranía | +10 % de sueldo por rama completada en otros oficios, como mucho +50 % | `work.veteran_bonus` |

Los P10 quedan entre 28.100 y 35.200 Y$ por turno en las ramas seguras y entre 41.500 y
51.800 en las arriesgadas (antes, 20.000-24.000 y 30.000-45.000).

## Lo que dice el simulador

Jugador de nota media 75, 4 turnos al día, que asciende en cuanto puede y compra pisos
con hipoteca a renta de referencia (`--solo referencia`, 450 días; cifras de la 8.5.0, con
el seguro del hogar con su impuesto de primas y el IGIC de las reparaciones):

| Oficio | Primer P10 | Segunda rama | Falcon con pisos | Falcon solo con sueldo |
|---|---|---|---|---|
| Obra | día 93 | día 160 | día 363 | no llega |
| Oficina | día 105 | día 172 | día 356 | no llega |
| Hostelería | día 96 | día 163 | día 386 | no llega |

Lo que se va en impuestos, a jornada completa, P1 → P5 → P10 (`--solo escala`):

- obra: 6 % → 32 % → 45 % en la rama arriesgada;
- política, rama de Bruselas: 6 % → 35 % → 48 %.

Cambiar de oficio después de acabar el primero (obra, día 161), solo con sueldo y pensión
(`--solo cambio`):

| A | Neto diario igualado en | Lo perdido recuperado en |
|---|---|---|
| Política, sanidad, oficina, influencer | 58-59 días | 130-139 días |
| Reparto, funcionariado | 62-65 días | 177-215 días |
| Hostelería (paga menos) | 64 días | 344 días |

## Por qué así

- **El plan pedía a la vez una carrera lenta y un Falcon hacia el día 300.** Con el
  primer P10 hacia el día 110 y las subidas mínimas, el Falcon salía hacia el día 400.
  Se eligió la carrera media con lo más alto de cada rama a ~1,4 veces lo de antes:
  Falcon entre los días 359 y 388. Llegar al 300 pedía multiplicar por 1,8 los sueldos de
  arriba. El simulador no cuenta casino, extras, Hong Kong ni premios: quien hace las 2
  extras legales de la semana cobra un 9 % más.
- **La pensión sola no compensa volver a empezar.** Fiel a la ley, su tope son unos
  42.000 Y$ al día, frente a los ~150.000 brutos de un P10. Con solo pensión y la
  experiencia del plan, igualar el neto diario tardaba unos 136 días y recuperar lo
  perdido, 475. Por eso hay veteranía en el sueldo y una experiencia más fuerte.
- **El Patrimonio se queda con la rebaja del 80 % de la ley.** Con el límite conjunto
  calculado en euros del año (IRPF y Patrimonio en la misma escala), al rico que va a por
  el Falcon la rebaja ni se acerca al tope: con el 50 % de antes sale igual. El
  Patrimonio es un sumidero fuerte: unos 20 M en 480 días, porque cada semana (un año)
  cobra un año entero sobre una riqueza que crece con siete «meses» de alquiler.
- **Sin bucles:**
  - la pensión depende de lo cotizado de verdad y de llegar arriba, y tiene tope;
  - la veteranía también tiene tope;
  - no hay jubilación doble de la misma rama;
  - el IRPF progresivo y el Patrimonio frenan;
  - el cambio de oficio cuesta semanas cobrando menos.

## Compatibilidad con quien ya jugaba

- Nadie baja de puesto y las formaciones compradas siguen valiendo.
- Quien ya estaba en un puesto conserva los días de antes en ese puesto
  (`Contract.legacy_days`); los días nuevos valen desde el siguiente ascenso.
- Quien se jubiló antes de la 8.0.0 nunca cobra menos que la suma de sus pensiones de
  entonces (`Branch.pension`, columna `legacy` de `work_retirements`). La calidad de esas
  ramas es entera, porque no hay historial.
- P4 y P5 bajan algo; las ramas suben.

## Negocios (8.14.0)

El negocio propio de `pala` comparte las 8 franjas del día con el curro: la jornada
reserva 4 y lo que queda es para horas extra o para el negocio. Desde la 9.19.0 se pueden
trasnochar 4 más; el B de noche tiene más riesgo y apurarlo no da dinero
(`docs/noche_pala.py`). Sus cifras y su porqué están
en `docs/negocios.md` («Equilibrio») y salen de `docs/negocio_sim.py`.

- **Ingresos:** un buen jugador saca con 4 turnos de su negocio lo que un P2-P3 de obra con 4
  turnos de jornada; con 8, lo que un P5.
- **Coste:** montarlo cuesta 1-2 semanas de un P3.
- **No toca la curva de la `pala`:** el negocio de la fase 1 no llega a los puestos altos.


## Solares (9.56.0)

La rama 🏖️ Ladrillo construye en los 60 solares de `pisos` (`docs/solares.md`). Con
`python docs/economia_sim.py --solares`, quien sigue la referencia de la obra apenas lo nota:
pasa por Ladrillo del día ~40 al 93 y, si compra pisos con hipoteca, nunca junta efectivo para
una obra. El techo es quien se queda en P10 de Ladrillo y encadena obras vendiéndolo todo al
acabar: con el margen del ~16 % y los plazos de la tabla llega al Falcon el día 305, frente al
340 de los pisos con hipoteca. Con el primer diseño (27 % y la mitad de días), el día 171.

## Rotación de minijuegos (11.1.0)

De P5 en adelante el minijuego del puesto alterna, un día cada uno, con el Cuadrante o la
Negociación; los de detectar, además, con la Auditoría, y los diálogos de P9 y P10, con el
Despacho (`Position.alts`). No cambia sueldos, días ni tareas:

- **La nota va a la misma escala** (0-100, con el extra de tiempo) y el jugador de
  `docs/ritmo_pala.py` juega cada minijuego con el acierto que le da la misma nota media.
  En los nuevos pulsa el botón de `best()` con esa probabilidad y, si no, otro al azar. Con
  acierto 0 saca ~25 en el Cuadrante, ~57 en la Negociación (cerrar sin apretar ya deja el
  40 % de cada trato), ~36 en la Auditoría (~12 con 5 malas) y ~37 en el Despacho; con
  acierto 1, 100 en todos.
- **El Despacho no tiene jugada perfecta.** Su nota es cómo queda al final (`desk_value`
  sobre `DESK_PAR`, 26). Quien mira solo cómo queda tras cada decisión acaba sin crisis y en
  27 de media; quien elige al azar la mitad de las veces entra en crisis uno de cada tres
  turnos. Las barras empiezan en 4 de 6: con 3 la crisis era la norma (un 19 % incluso
  jugando bien).
- **Las tareas cuentan en el contador de siempre** del puesto los días de rotación (en los
  puestos de memoria, dos aciertos del juego del día valen una ronda perfecta).
- **El órdago final es a cara o cruz** sobre el extra de tiempo (hasta 10 puntos): la media
  no cambia. Como la nota no pasa de 100, a quien ya iba a 100 le sale algo peor jugárselo.
- **Medido el 09/11/2026:** a nota 75, llenar la barra y cumplir las tareas en los puestos
  de P5 en adelante tarda de media 6,5 días (antes de la rotación, 6,8; el que más cambia,
  3 días); a nota 85, 3,9 (antes, 4,0). Siempre por debajo de los días que pide el puesto
  (13 a 21 en las ramas), que son los que mandan: las ramas siguen en 67 días y la
  referencia de la obra, con el primer P10 el día 93 y el Falcon con pisos el día 363.
- **Lo que no mide el simulador:** quien se sabía los diálogos de memoria sacaba 100 todos
  los días. En la Negociación, un cara de póker obliga a tantear (5 puntos de ese trato) o
  a jugársela, y en el Despacho no se ve lo que viene: los días de rotación ese jugador
  cobra algo menos (del orden de un 1-5 %).
