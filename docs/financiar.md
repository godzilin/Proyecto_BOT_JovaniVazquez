# 💳 Financiar compras y el IPC del crédito (9.20.0)

Pedido el 25/10/2026: poder financiar las obras de los pisos como atajo con el banco de los
préstamos y, ya puestos, cualquier compra, como crítica de cómo el crédito barato le quita
valor al dinero. Lo emblemático no se financia. Las cuotas, en `saldo`. Y ofertas para empujar
el gasto.

Código: reglas en `src/bot/services/credit.py`, la oferta en `src/bot/cogs/banco_compras.py`,
la firma en `HousingRepository.finance` y `HousingService.finance`/`unfinance`/`refresh_ipc`.

## Decisiones

| Qué | Decidido | Quién |
|---|---|---|
| Cómo se nota la crítica | IPC del crédito: lo financiado sin devolver sube los precios de la tienda y las obras para todos, con tope | usuario |
| Ofertas | 0 % TIN trampa, Paga en 3 y carencia (el preconcedido por MD, no) | usuario |
| Emblemático | lo numerado (existencias limitadas) y los títulos, la regla del Monte | usuario |
| Entrada de un piso | no se financia (para eso está la hipoteca) | usuario |
| El botón | sale siempre, te llegue o no: es la tentación | propuesta aceptada |
| El dinero | directo a la compra: entra lo prestado y se compra en el acto | propuesta aceptada |
| Tipo del plan normal | el de un préstamo personal con tu perfil + 2 puntos | propuesta aceptada |
| Fuera | el juego, impuestos y tasas, depósitos (pujas) y compras entre miembros | propuesta aceptada |
| Negocios | los de autónomo, con 💳 Financiar como todo (9.51.0; al montar o trasladarse, solo el acondicionamiento); una sociedad paga con su caja y sigue con su préstamo de 🏪 Negocio | usuario |
| Compras mezcladas (9.51.0) | lo que no se financia (lo numerado y los títulos de un carrito, la tasa y la fianza de un local) va al contado en la misma compra (`Purchase.cash`) y no apaga el botón de lo demás | usuario |
| 0 % TIN en el carrito | solo si todo lo financiado es de un pasillo; si no, la categoría es «la tienda», que nunca lo tiene | decisión técnica |
| Disciplina | `tests/unit/test_financiar_cobertura.py` falla si una compra del código no está decidida (financiada o fuera, con su motivo) | usuario |

## Cómo funciona

1. Quien compra crea un `Deal`: `credit.Purchase(label, amount, category, works)` y `buy`, su
   camino de compra de siempre.
2. La oferta (`FinanceView`) enseña los planes que valen para ese importe y esa semana, el
   plazo, el seguro y la carencia, la ficha (cuota, primera cuota, TIN, TAE, «pagas X por
   algo de Y», letra pequeña) y si cabe en el 35 %.
3. ✍️ Firmar y comprar: `HousingService.finance` apunta el préstamo y abona lo prestado (la
   compra más la comisión de apertura, que también se financia, menos la comisión: llega
   justo lo que cuesta). Después `buy` compra. Si `buy` falla, `unfinance` desiste (devuelve
   lo prestado y la comisión vuelve) y no se debe nada.
4. Es un préstamo más de `housing_loans` (`kind` `compra` o `plazos`): lo cobra el cierre de
   `pisos`, cuenta en el 35 %, fichero de morosos con 2 cuotas sin pagar, vence con 3 y pasa a
   deuda con el banco embargable. Sale en `saldo` y en 🏦 Préstamos de `banco`.

Atajo conocido: firmar y comprar son dos transacciones. Si la compra falla y entretanto el
miembro se ha gastado lo prestado (otro panel en el mismo segundo), `unfinance` no puede
devolverlo y la financiación sigue viva: la paga como cualquier otra y queda en el registro.

## Planes (cifras con un préstamo personal al 9,5 %)

| Plan | Importe | Plazos | Tipo | Comisión | Seguro | 10.000 Y$ a 12 cuotas |
|---|---|---|---|---|---|---|
| Normal | 1.000-750.000 | 3, 6, 12, 24 (obras, 48) | personal + 2 | 1 % | opcional (6 % de la cuota) | 886/cuota, TAE 12,1 % |
| Normal con carencia | igual | igual | igual | 1 % | opcional | 7 días sin pagar, total +4 %, TAE 11,0 % |
| 0 % TIN (2 categorías por semana) | 1.000-750.000 | 6, 12 | 0 | 3 % | obligatorio (2 % de la cuota) | 878/cuota, TAE 10,2 % (a 6 cuotas, 19,7 %) |
| Paga en 3 | 100-999 | 3 | 0 | 0 | no | sin coste; 25 % de recargo por cuota que no llega |

La carencia baja la TAE (la comisión se reparte en más tiempo) aunque se pague más: es la
letra pequeña que se enseña. El 0 % es mejor que el normal a 12 cuotas y una trampa a 6.

## IPC del crédito

`ipc % = min(20, 100 × lo que se debe de financiaciones / el dinero de los monederos de los
miembros)`: la teoría cuantitativa del dinero (MV = PQ) a lo bruto. Sube el precio de
catálogo de la tienda (`shop.quote`) y el coste de las obras y divisiones
(`reform_quote`/`split_quote`). Se recalcula solo en momentos fijos: cada 10 minutos con el
cierre de `pisos` y al terminar o deshacer una compra financiada. Si se recalculara al
firmar, la propia financiación subiría el precio de lo que financia y la tienda diría «el
precio ha cambiado».

Vigilar al desplegar: con poco dinero en los monederos, una obra grande financiada lo lleva
al tope. Es la crítica (el crédito de uno encarece a todos), pero si molesta, la palanca es el
100 de la fórmula (la elasticidad) o el tope.

## Economía

El simulador (`docs/economia_sim.py`) no modela obras ni tienda. La financiación compite por
el mismo 35 % que las hipotecas y los préstamos (como la rehipoteca, que dejó el Falcon en el
mismo día), así que no da más palanca a quien ya la usa entera; solo Paga en 3 no mira el 35 %,
y son como mucho 3 compras de menos de 1.000 Y$. Lo prestado se crea y las cuotas desaparecen:
los intereses y las comisiones son un sumidero. El Falcon no se financia y, con IPC, cuesta más.
