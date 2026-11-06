# Casa propia: el plan

Documento de diseño del sistema de hogar (`casa`). Amplía la vivienda habitual de `pisos`
(🏠 Vivir aquí, la VPO y vivir de alquiler en el piso de otro miembro), que hasta la 9.53.0
solo daba un ×1,2 fijo a la recarga de la batería de `pala` (`housing.HOME_REGEN`).

Decisiones de la persona que lo pidió (6/11/2026):

- Muy profundo, por fases.
- Los hijos son personajes del bot (NPC) a los que se les pone nombre.
- Vivir con tus padres es **neutro**: ni premio ni castigo. El incentivo es lo que gana quien
  se emancipa.
- Hay fiestas y compañeros de piso entre miembros.
- Hay suministros (luz, agua, internet) desde la primera fase.

## La idea

La casa en la que vives tiene un **Confort** de 0 a 100. El Confort da un **tramo** y el tramo
da los bufos: la batería de `pala` recarga más rápido, la familia se recupera antes y cada
turno la desgasta menos. Vivir en una casa grande y bien puesta es mejor, pero cuesta: los
suministros crecen con los metros y con un certificado energético malo, y la gente que vive
contigo (hijos, compañeros) se reparte el espacio.

Quien vive con sus padres (no tiene vivienda habitual en `pisos`) se queda como siempre:
recarga ×1, familia como siempre, sin suministros.

## Fase 1: el Confort, los bufos y los suministros

### Confort (0-100)

Cuatro pilares. Se calcula con funciones puras (`bot.services.home`) a partir del inmueble
(`Listing` con sus reformas, o la `VpoUnit`), de cuánta gente vive en él y de lo que tenga
puesto:

| Pilar | Puntos | De dónde sale |
|---|---|---|
| Espacio | 40 | 15 por m² por persona (lleno a 30 m² por cabeza) y 25 por el tamaño de la casa (raíz de los m² sobre 20, llena a 300 m²) |
| Calidad | 25 | Certificado energético (A 16 … G 4), +3 por reforma de vivienda acabada (hasta +9), −3 sin ascensor, −2 con humedades |
| Equipamiento | 25 | Los muebles del hogar (fase 2). En la fase 1, 0 para todos |
| Entorno | 10 | El carácter del barrio: de lujo 10, de playa 8, histórico y marinero 7, céntrico y rural 6, obrero 4 |

Sin muebles el máximo es 75: el tramo de arriba pide la fase 2. Es a propósito: cuando
llegue el equipamiento nadie baja de tramo, solo se puede subir.

Cómo sale el catálogo (914 viviendas, sin reformas → con tres reformas y la energética):
estudio 33 → 47, piso de 2 dormitorios 38 → 52, de 3 42 → 58, ático 45 → 62, chalet 56 → 70,
casona y edificio 54-56 → 70, VPO 38 (no se reforma). Con los muebles de la fase 2 (hasta 25)
un chalet reformado llega a 🏰 Casoplón y un piso de 2 reformado, a 🏡 Hogar.

### Tramos y bufos

| Tramo | Confort | Batería | Familia (recuperación) | Desgaste de cada turno |
|---|---|---|---|---|
| 🛏️ Zulo | 0-29 | ×1,20 | ×1,00 | ×1,00 |
| 🏚️ Apañada | 30-44 | ×1,25 | ×1,10 | ×0,95 |
| 🏠 Cómoda | 45-59 | ×1,30 | ×1,20 | ×0,90 |
| 🏡 Hogar | 60-79 | ×1,40 | ×1,35 | ×0,85 |
| 🏰 Casoplón | 80-100 | ×1,50 | ×1,50 | ×0,80 |

- El suelo es el ×1,2 de antes: nadie que ya viva en su casa pierde nada por el Confort.
- La batería y el tramo de la familia se multiplican como hasta ahora (`_recharge`).
- El desgaste rebaja lo que resta un turno (`family_breakdown`), con una línea propia en el
  desglose («casa +2»), así se ve.
- Se calibra con `docs/familia_pala.py --casa`: la batería solo manda en quien ficha mucho,
  porque las franjas (8 al día) topan los turnos.

### Suministros

Cada noche, en el cierre de `pisos`, quien tiene vivienda habitual paga los suministros del
mes (un día del juego es un mes). Cifras a la escala de los pisos (10 Y$ = 1 €):

| Suministro | Importe | Impuesto (al Estado) |
|---|---|---|
| ⚡ Luz | 0,9 €/m² al mes × factor del certificado (A 0,5 … G 1,65) | Impuesto Especial sobre la Electricidad, 5,11269632 % (art. 99 Ley 38/1992). IGIC al tipo cero: suministro a una vivienda de ≤10 kW (Ley 4/2012) |
| 🚰 Agua | 15 € por persona al mes | IGIC al tipo cero (el agua, Ley 4/2012) |
| 📶 Internet | 35 € al mes | IGIC general (7 %, art. 51 Ley 4/2012) |

- Lo que no es impuesto desaparece (las compañías no existen).
- Paga quien vive: el dueño en su casa y su VPO, el inquilino si vive de alquiler en el piso
  de otro miembro.
- No es deducible: es gasto personal, no del inmueble.
- **Corte de suministros:** si el monedero no llega, no se queda debiendo; le cortan la luz.
  Al día siguiente la casa no da bufos (vuelve a lo neutro, como en casa de tus padres) y el
  panel lo dice. La noche siguiente se vuelve a cobrar con normalidad.
- `hacienda` los cuenta con el IGIC de las compras (la factura personal ya no cabía con una
  línea más) y en el bloque 🏠 juntos, «Impuestos de la luz y la fibra de casa» (las clases
  `electricidad` e `igic_suministros` de `economy_housing_tax` van a la clave `suministros`).

Un piso de 2 dormitorios de 55 m² con certificado D, una persona: unos 1.050 Y$ al día. Un
chalet de 120 m² con una E: unos 1.900. Un turno del puesto 1 deja unos 1.600 netos.

### Panel `casa`

`casa [miembro]`: un solo comando, con la sección 🏡 Casa también en `perfil`.

- Dónde vives (o «Con tus padres» y qué hace falta para emanciparse).
- El Confort con sus cuatro pilares, el tramo y lo que da, y cuánto falta para el siguiente.
- Los suministros de anoche y la previsión de esta noche, con el corte si lo hubo.
- Pistas para subir: qué pilar está más lejos de su máximo y qué lo mejora.

### Logros (categoría 🏡 Casa)

Escalera de la emancipación (vivir en una habitación, de alquiler, en una VPO, en tu casa),
cada tramo alcanzado, días en tu casa (7, 30, 100, 365), suministros pagados por escalones,
los impuestos de la luz, el primer corte (secreto), vivir en cada tipo de vivienda, en una A
y en una G (secreto), un Confort de 69 o de 42 (secretos), mirar la casa de otro, abrir el
panel a deshoras.

## Fase 2: muebles y estancias

- **Muebles del hogar**: un pasillo nuevo de la `tienda` (🛋️ Hogar, al menos 8 artículos),
  como los del vacacional (`housing_furniture.py`): con tenerlos suben el Equipamiento. Uno por
  persona; cada uno suma sus puntos hasta 25. Sofá, cama buena, cocina equipada, lavavajillas,
  escritorio, aire acondicionado, consola y tele, cinta de correr, barbacoa…
- **Estancias**: cada tipo de vivienda tiene sus huecos (estudio 1, piso2 2, piso3 3, ático 3
  más terraza, chalet 4 más jardín, casona 5, finca 3 más huerto, edificio 6). Cada hueco se
  asigna a algo desde el panel:
  - 🛏️ Dormitorio: hace falta uno por cada persona que vive contigo (hijos y compañeros).
  - 💻 Despacho: teletrabajar devuelve más familia y estudiar desgasta menos.
  - 🏋️ Gimnasio: −10 % de batería por turno.
  - 🛠️ Taller: un plus para el negocio de `pala`.
  - 🛋️ Cuarto de invitados: los favores de la familia cuestan menos (la abuela viene a
    quedarse).
  - Los exteriores van con el tipo: terraza, jardín, piscina, huerto (el tupper sin gastar
    familia), la cueva fresquita (el aire acondicionado no hace falta).
- Mudarse con muebles: los muebles son tuyos, se mudan contigo.

## Fase 3: la familia en casa (hijos)

- **Pareja**: un NPC con el nombre que pongas. Comparte gastos (paga la mitad de los
  suministros) y cuenta como persona en el Espacio.
- **Hijos**: NPC con nombre (formulario, validado como entrada no confiable), que crecen por
  etapas al ritmo de `pisos` (un día es un mes): bebé (0-2 años, 24 días), niño (hasta 12),
  adolescente (hasta 18) y adulto, que se emancipa (logro) o se queda en casa.
  - Cada hijo necesita dormitorio (sin él, hacinamiento) y cuesta cada día (comida, ropa,
    colegio): un sumidero de dinero con IGIC donde toque.
  - En la Renta: el mínimo por descendientes (art. 58 LIRPF), la deducción por maternidad
    (art. 81 LIRPF) y la canaria por nacimiento (DL 1/2009). Se calcula con `taxes.week_return`.
  - Su ánimo sube con cuidarlos (llevarlos al cole, deberes, jugar, gasta franjas) y con la
    casa; baja si la casa es pequeña. Un hijo contento sube la familia de `pala`.
  - Eventos por etapa (la fiebre, la reunión del cole, la paga del adolescente, el carnet).
- Los hijos no se venden, no se pierden por un embargo y no son de nadie más.

## Fase 4: compañeros de piso y fiestas

- **Compañeros de piso**: dos o más miembros viven en la misma casa. Se apoya en lo que ya
  hay (alquiler por habitaciones y alquiler a un miembro): quien alquila una habitación del
  piso en el que vive su dueño convive con él. Se reparten los suministros y todos tienen el
  Confort de la casa (contando a todos en el Espacio).
- **Fiestas**: 🎉 desde el panel. Sale un anuncio en el canal con 🙋 Me apunto durante una
  hora. Quien organiza paga la comida y la bebida (con su IGIC) y gana familia por invitado;
  los invitados, un poco de batería. Los vecinos se quejan según el barrio (en uno obrero o
  histórico, multa por ruido de la ordenanza: tasa al Estado). Una por persona y semana.
- **Visitas**: `casa @miembro` ya enseña su casa desde la fase 1.

## Fase 5: el plano de tu casa

Una escena de canvas en Node (`assets/casa/escena.html`, receta «Juego nuevo con dibujo» de
CLAUDE.md): el plano visto desde arriba con sus estancias, los muebles, los hijos y la
mascota activa. Se sube solo si cambia (como el retrato de la mascota).

## Lo que no se hace

- Ningún castigo por vivir con tus padres.
- Ninguna deuda por suministros: el impago es el corte de un día, no una deuda más.
- Los bufos no dan dinero directo: dan tiempo (batería) y familia.

## Versiones

Cada fase es una versión MENOR. La fase 1 añade una regla que se nota (los suministros a
quien ya vive en su casa): se avisa en la descripción de la PR.

## Lo que dijo la calibración de la fase 1

`python docs/familia_pala.py --casa` (28 días, puesto 1 de obra, medias por día):

| Estilo | Con sus padres | Zulo | Hogar | Casoplón |
|---|---|---|---|---|
| Casual (2 turnos) | 2,0 turnos | igual | igual | igual |
| Normal (4 turnos) | 4,0 turnos | igual | igual | igual |
| Grinder (extras, B, cafés) | 5,1 turnos, barra +30 | 5,3 (+4 % bruto) | 6,4 (+23 %), barra +52 | 6,8 (+31 %), barra +61 |

Quien juega normal no nota la casa: va siempre en casa feliz y la batería le sobra. Los bufos
solo se notan si se encadenan turnos o la familia anda baja. Un Casoplón da a un grinder unos
2.800 Y$ brutos más al día, lo mismo que pagan de suministros las casas grandes: no es una
fuente de dinero, es tiempo. Queda abierto darle a quien juega normal algo que sí note en la
fase 1 (por ejemplo, más barra por turno en las casas buenas); no se ha hecho porque cambia el
ritmo de ascensos de `docs/economia_sim.py` y pide recalibrar.
