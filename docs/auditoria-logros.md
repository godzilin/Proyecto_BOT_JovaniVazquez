# Auditoría de logros: rarezas y cantidad

Octubre de 2026. Cifras calculadas con `docs/auditoria_logros.py` (15 jugadores simulados por juego, hasta 400 días cada uno). Se vuelve a ejecutar al tocar las probabilidades de un juego o al añadir logros de suerte.

## Escala

La rareza dice cuánto le cuesta a un miembro activo que hace esa cosa:

| Rareza | Esfuerzo | Premio | Puntos |
|---|---|---|---|
| ▫️ Común | 3 días o menos | 50 Y$ | 10 |
| 🔹 Raro | hasta 2 semanas | 200 Y$ | 25 |
| 💠 Épico | hasta 2 meses | 750 Y$ | 50 |
| 🌟 Legendario | hasta 8 meses | 2.500 Y$ | 100 |
| 👑 Mítico | más de 8 meses, o suerte de 1 entre decenas de miles | 10.000 Y$ | 250 |

Ritmos supuestos de un jugador habitual de cada juego, al día: ruleta 40 tiradas, blackjack 40 manos, tragaperras 200 (con Auto y turbo), Botes 150, Crash 30 rondas, Minas 40, Pollo 60, cara o cruz 60 partidas, autobús 60 partidas, dados 50 partidas, pachinko 60 tandas y caballos 30 boletos. Los contadores que no son del casino (mensajes, voz, reacciones, IMV…) usan los ritmos de `RITMO` del script.

## Método

1. **Casino, simulado.** Cada jugador juega con el código de verdad (`SlotMachine`, `spin_base` y `BonusGame`, `PachinkoMachine`, `MinesGame`, `ChickenGame`, `CoinGame`, `BusGame`, `CrapsGame`, `crash_point`, `Wheel`, `BlackjackGame`, `run_race`) y la misma función de estadísticas que usa el cog. La estrategia imita a un jugador normal: apuestas a color, a número y a docenas en la ruleta; estrategia básica simplificada en el blackjack; minas, dificultades y objetivos de cobro variados en Minas, Pollo y Crash; en cara o cruz, cobrar entre 1 y 7 aciertos (y a veces ir a por el oro) pidiendo siempre el mismo lado, alternando o al azar; en el autobús, bajarse en la parada 1 a 4 o jugarse la vuelta (25/25/20/20/10 %) eligiendo casi siempre la opción más probable (75 %), a veces una al azar (20 %) y a veces la más larga (5 %); en los dados, Pase casi siempre (85 %) y Odds variadas (ninguna, una ficha o al tope), con la mano del tirador de una partida a otra. Se toma la mediana del día en que salta cada logro.
2. **Contadores, por ritmo.** Meta entre ritmo diario. Es orientativo: sirve para ver saltos de dos rarezas, no para afinar.
3. **Fechas y decisiones, a mano.** Los logros de un día del año (Halloween, Reyes…) se quedan en Común: basta con estar ese día. Lo que depende de una decisión del jugador (apostar 1 Y$, plantarse con 11, cobrar a 1,10x) se puede forzar a propósito y va en Común o Raro.

## Lo que salió y se cambió

Estructura: ninguna estadística tenía un escalón más alto con menos rareza. Sí había nombres repetidos: 14 en el catálogo de antes, como «Por los pelos» (tres veces) o «Ballena» y «Bote» (dos), y otros tantos que se colaron en la tanda de chat y voz. Se han renombrado 35 logros (los `id` no cambian). Ahora una prueba impide repetir nombres y otra, que la rareza baje al subir la meta.

Rarezas cambiadas: 130.

- **Pachinko estaba inflado.** El SUPER RUSH era Legendario y sale en unos 2 días (ahora Raro). Encadenar 15 era Mítico y sale en unos 2 meses (ahora Épico). Atari, rush y sus escalones, una rareza menos.
- **Tragaperras, al revés.** 💎💎💎 (1 de cada 847 tiradas) y 7️⃣7️⃣7️⃣ (1 de cada 1.309) salen en 3 a 6 días: de Épico y Legendario pasan a Raro. El premio gordo (1 de cada 14.400) tarda unos 84 días: de Épico a Legendario. Cinco premios gordos, a Mítico.
- **Botes.** MINI, MAJOR, bonus rojo y gran bonus salían en 1 a 5 días con rareza de semanas: bajan uno. Los multiplicadores inmediatos y los misteriosos tardan 18 y 24 días: suben de Común a Épico.
- **Pollo.** Llegar a la meta en Fácil y en Media (1 de cada 2,7 y 1 de cada 17 intentos), Por los pelos y la colección de matrículas bajaban de sobra. ×50, ×1.000, 50 cobros en Hardcore y 10 carriles en Hardcore suben.
- **Autobús** (nuevo, simulado con 20 jugadores). Completar las cuatro manos es Común (sale en menos de un día) y la vuelta ganada, Rara. Igual y poste piden elegir una opción de 1 o 2 entre 13: el primero es Raro y 50 de cada uno, Mítico. Los escalones altos de manos, aciertos y palos suben a Legendario o Mítico porque el ritmo de 60 partidas al día no da para más. Cobrar en ×20 es Común; en ×500, Mítico. Se dejan en Común o Raro, aunque la simulación diga otra cosa, los que el jugador fuerza a propósito: cobrar tras el color (`bus_gallina`), llevar la contraria (`bus_contra_*`), apostar 1 Y$, cobrar 666 o 69 Y$ y las fechas (madrugada, hora punta, Día de Canarias, viernes 13), que el script no simula.
- **Cara o cruz** (nuevo, calibrado con la simulación desde el principio). La racha de 9 y cobrar en ×256 son Míticos: con un 49,5 % por lanzamiento, ×256 sale una de cada ~330 partidas en que se intenta, y casi nadie lo intenta. El primer canto es Común (1 de cada 100 lanzamientos, ~115 lanzamientos al día); 150 cantos, Legendario. Los logros de fecha y el de cobrar 666 Y$ no se simulan: son Comunes por la norma de fechas y por ser una decisión.
- **Dados** (nuevo, calibrado con la simulación desde el principio, con 41 jugadores: con 9, la mediana de los logros de pura suerte bailaba de 0,3 a 34 días). La mano caliente sigue la cuenta exacta: un punto se hace el 40,6 % de las veces, así que k puntos en una mano salen en 1 de cada 0,406^-k manos (unas 20 manos al día). Por eso la escalera es 2 y 4 (Comunes), 6 (Raro, ~8 días), 8 (Épico) y 10 (Mítico, ~257 días). Los seis puntos distintos en una mano son Legendarios. Los de fecha, ganar 10.000 y 100.000 Y$ de golpe y cobrar 777 Y$ no se simulan: fecha, apuesta y decisión.
- **Minas.** ×100 tarda unos 110 días y ×1.000 más de un año: suben a Legendario y Mítico. Limpiar el tablero con una mina (1 de cada 24 partidas con 1 mina, pero casi nadie juega con una) sube a Épico.
- **Crash.** Una ronda que llega a 100x pasa en 1 de cada 101: baja a Raro. 1.000 retiradas suben a Legendario.
- **Ruleta y blackjack.** Acertar un pleno, que salga el mismo número dos veces, separar y ganar las dos o acabar con 5 cartas bajan a Común. 12 tiradas seguidas ganadas (14.600 tiradas a color de media) sube a Mítico.
- **Ruleta con rayos** (nuevo, calibrado con la simulación desde el principio; el jugador simulado pone uno de cada diez plenos con 🔥 o ❄️). Acertar un pleno con rayo tarda unos 21 días (Épico); cinco, unos 171 (Legendario); veinte y el rayo verde (pleno con rayo al 0 o al 00) pasan del horizonte (Míticos). Cobrar ×200 o más tarda unos 91 días y ×500 unos 142 (Legendarios). Tener rayo en tu número y que no salga es diario: el primero Común, 25 Épico y 100 Legendario. Las tormentas de 4 y 5 rayos salen en días (1 de cada 20 y 1 de cada 100 tiradas): Comunes. El casi (la bola a una o dos casillas de tu pleno) es Común el primero, Raro a los 25, Legendario a los 250 y Mítico a los 1.000. Acertar el caliente o el frío tarda de 2 a 4 semanas (Épicos); apostar 50 veces a uno u otro es una decisión y se queda en Raro aunque el simulado tarde mes y medio.
- **Casino general.** Probar los ocho juegos costaba Legendario y se hace en diez minutos: baja a Común o Raro. Las rachas de victorias se fuerzan cobrando a 1,01x en el Crash (98 % de acierto): 5 seguidas a Común, 10 a Raro.
- **Cosas que dependen del calendario.** Treinta días seguidos (hablar, el IMV, los intereses, la pala) es Épico; cien, Legendario; un año, Mítico. Antes había rachas de un año en Legendario y de 30 días en Común.
- **Loterías.** 4 aciertos en la Primitiva o la Bonoloto (1 de cada 1.032 apuestas) pasa de Raro a Épico; 5 aciertos (1 de cada 55.491), de Legendario a Mítico.

## Carreras de caballos

Se añadieron después, con 72 logros (11 secretos). La simulación (`_jugar_caballos`) prepara 60 parrillas con sus cuotas, una de cada diez de Gran Premio, y en cada carrera elige un tipo de boleto como un jugador normal (ganador 55 %, colocado 15 %, gemela 18 %, trío 12 %) y caballos tirando hacia los favoritos. 52 de los 72 caen en su banda; 9 a una de distancia. Lo que se calibró con ella:

- **Suben:** cobrar a 20x (unos 4 días) y a 100x (unos 40), ganar con un tapado de 10x (5 días; diez veces, unos 70), perder 10 por una nariz (63), remontar desde el último (40; diez veces, más de un año), ganar tras tropezar (unos 200 días), 50 colocados y 25 aciertos con el pronóstico de Perro Sanxe (unos 20 días).
- **Bajan:** que tu caballo se desboque (1 de cada 250 boletos, día y medio) y el secreto de Puerta Giratoria, que pide 5x en vez de 10x (a 10x tardaba nueve meses).
- **Se dejan por encima de lo que dice la simulación:** el bote del Gran Premio (Raro, y cinco botes Épico) y la colección de distancias (Raro), porque la simulación corre un Gran Premio de cada diez carreras y en un servidor de verdad sale como mucho uno cada 4 horas y hacen falta ocho carreras normales antes. Igual con «Tribuna llena» y «Derbi de Epsom» (6 y 10 boletos en una carrera), que dependen de cuánta gente juegue en el servidor.
- **Fuera de la simulación:** los de madrugada, apostar 1.500 Y$ justos, ganar 100.000 Y$ de golpe, la retención de IRPF (el jugador simulado no paga impuestos), el caballo cansado (la simulación no guarda historial) y los que se derivan de los contadores por caballo (todo el establo, hincha, socio y peña), que se cuentan en `with_derived`.

## Pachinko: la física

Las bolas del pachinko caen con física real (`bot.services.pachinko_physics`) y de ahí salen 25 logros: rebotes en los clavos, caídas lentas y rápidas y bolas que bajan sin tocar casi nada. Se alimentan en `pachinko_stats` con el movimiento real de la tanda (`bot.services.pachinko_motion`, con las bolas chocando entre sí), no con lo que paga la máquina. Cuatro son secretos.

La rareza salió primero de la biblioteca de caídas guardadas (`trayectorias.json`) y de la probabilidad real de cada bolsillo (`pocket_probability`): cada bola cae en un bolsillo, y la probabilidad de una propiedad de la caída es la suma, sobre bolsillos, de la probabilidad del bolsillo por la parte de sus caídas que la cumple. Al añadir los choques se repitió con 42.000 tandas simuladas con `motion_for` (10.500 por tablero, semillas fijas): los choques casi no mueven estas distribuciones (115,5 rebotes por tanda contra 115,8 sin choques; bolas lentas, 15,6 % contra 14,2 %), así que **ninguna rareza ni meta de estos 25 logros cambia**. Con el jugador de la simulación (60 tandas al día, tableros 40 % Clásica, 30 % Sakura, 15 % Dragón y 15 % Oni) y 10 bolas por tanda salen unos 115 rebotes por tanda (6.930 al día), 0,156 bolas lentas por bola, 0,047 rápidas y 0,026 que bajan casi sin tocar clavos:

| Logro | Cuenta | Días (mediana) | Rareza |
|---|---|---|---|
| 1.000 y 10.000 rebotes | meta / 6.930 al día | 0,15 y 1,4 | Común |
| 50.000 / 250.000 / 1.000.000 / 5.000.000 rebotes | idem | 7 / 36 / 144 / 720 | Raro / Épico / Legendario / Mítico |
| Una bola con 15, 18, 20 y 21 rebotes | 69 %, 12 %, 2,8 % y 1,0 % de las tandas lo traen | 0,01 / 0,1 / 0,4 / 1,1 | Común (con choques, una de cada ~9.000 tandas llega a 22 y 23: «casi lo máximo») |
| Una tanda con 150 rebotes | 2,4 % de las tandas | 0,5 | Común |
| Una tanda con 160, 165 y 170 rebotes | 0,19 %, 0,03 % y 0,003 % | 6 / 40 / 360 | Raro / Épico / Mítico |
| 1, 100, 1.000 y 10.000 bolas lentas (casi 2 s) | 94 al día | 0,01 / 1,1 / 11 / 107 | Común / Común / Raro / Legendario |
| 1, 100, 1.000 y 5.000 bolas rápidas (1,1 s o menos) | 28 al día | 0,04 / 3,5 / 35 / 176 | Común / Raro / Épico / Legendario |
| 1, 25 y 250 bolas con 5 rebotes o menos | 16 al día | 0,06 / 1,6 / 16 | Común / Común / Épico |

Las cifras de las tandas salen de convolucionar la distribución de rebotes de una bola 10 veces por tablero (con la de las 42.000 tandas, bolsillo a bolsillo) y las de una bola, de las tandas simuladas; la simulación de `docs/auditoria_logros.py` (con `PachinkoMachine(rng.randrange, rng.randrange)` y `motion_for`) las confirma y no marca ninguna discrepancia. Si se regenera la biblioteca (`docs/pachinko_trayectorias.py`) o se toca la física de los choques, cambian estas distribuciones: hay que volver a calcular las tablas y las metas de `pachinko_bounce_volley_max`, que son las más sensibles. La única frase que cambió es la de «Récord del Congreso» (21 rebotes): ya no es «lo máximo que da un tablero».

## Pachinko: los choques

Las bolas chocan entre sí (restitución 0,8) y, aun así, cada una acaba en el bolsillo que sorteó la máquina: `motion_for` busca, bola a bola, una salida con la que todas las que están en el aire entran donde deben. Con el método de arriba (42.000 tandas, mismo jugador) salen **26 logros nuevos** en 🌸 Pachinko, cuatro de ellos secretos. Lo que mide la simulación:

- 0,62 choques por tanda (37 al día). El 61 % de las tandas no tiene ninguno; el 39 % tiene al menos uno, el 15 % dos o más, el 5,6 % tres o más, el 2,0 % cuatro, el 0,78 % cinco, el 0,30 % seis, el 0,05 % ocho y el 0,007 % diez. El máximo visto en 42.000 tandas es 11.
- Una bola con 2 o más choques en su caída: 11,5 % de las tandas; 3 o más, 3,6 %; 5 o más, 0,29 %; 7 o más, 0,032 %. Bolas distintas que chocan en una tanda: 5 o más, 1,1 %; 7 o más, 0,048 %; 8, 0,006 %.
- Una bola que choca y acaba en una esquina: se midió forzando una bola a cada esquina en 1.000 tandas por tablero. Choca el 1,6 % de las que van a las esquinas de Sakura y el 0,5-0,7 % en los otros tres (casi todas las esquinas se llegan sin tocar a nadie), lo que da 0,00044 por tanda: unos 26 días.
- Una bola que sale con retraso (ninguna de las 8 salidas probadas sirvió): el 1,0 % de las bolas, 6 al día, y en 42.000 tandas nunca más de 6 fotogramas de retraso.

| Logro | Cuenta | Días (mediana) | Rareza |
|---|---|---|---|
| 1, 100, 500, 2.000, 5.000 y 20.000 choques en total | meta / 37 al día | 0,03 / 2,7 / 13 / 54 / 134 / 538 | Común / Común / Raro / Épico / Legendario / Mítico (secreto) |
| Una tanda con 3, 5, 6, 8 y 10 choques | 5,6 %, 0,78 %, 0,30 %, 0,050 % y 0,007 % de las tandas | 0,2 / 1,5 / 3,9 / 23 / 172 | Común / Común / Raro / Épico / Legendario (el de 10, secreto) |
| Una bola con 2, 3, 5 y 7 choques | 11,5 %, 3,6 %, 0,29 % y 0,032 % de las tandas | 0,09 / 0,3 / 4,0 / 36 | Común / Común / Raro / Épico |
| 5, 7 y 8 bolas distintas chocando en una tanda | 1,1 %, 0,048 % y 0,006 % | 1,0 / 24 / 203 | Común / Épico / Legendario (el de 8, secreto) |
| 1 y 5 bolas que chocan y acaban en una esquina | 0,026 al día | 26 / 190 | Épico (secreto) / Legendario |
| 1, 100 y 1.000 tandas sin ningún choque | 37 al día | 0,03 / 2,7 / 27 | Común / Común / Épico |
| 1, 100 y 1.000 bolas que esperan turno para salir | 6 al día | 0,17 / 17 / 166 | Común / Épico / Legendario |

Las rarezas de los de suerte (tandas con muchos choques, bolas con muchos choques, esquinas) salen de las probabilidades de arriba con `0,69 / (−ln(1 − p) × 60)` días, igual que las del apartado anterior. `docs/auditoria_logros.py --juego pachinko --jugadores 4 --dias 20` confirma los de menos de 20 días (el pachinko cuesta ~0,1 s por tanda con choques, así que simular 400 días de 60 tandas llevaría horas) y el resto se deja a las probabilidades. Las colas más raras (diez choques en una tanda, 8 bolas distintas) salen de 2 a 3 casos en 42.000 tandas: su rareza es aproximada, y el techo de 10 y 8 es lo más alto que se ha visto, no un máximo demostrado.

## Tragaperras: el revamp

El revamp trae re-giro del tercer rodillo, doble o nada, bote misterioso, giro diario, barra de bonus, GRAN/MEGA/ÉPICO, calor que se enfría y ticket de sesión, con 88 logros nuevos. `_jugar_slots` simula ya la máquina nueva: rodillo virtual, re-giro de la mitad de los casi-premios (y en cadena mientras siga rozando), doble o nada con la mitad de los premios (y otra vez la mitad de las veces que acierta) y bote que cae solo entre la semilla y `POT_CAP`, alimentado por cinco jugadores. Con esa simulación se han puesto las rarezas de todos los logros de suerte de la tragaperras, nuevos y antiguos, y ninguno queda fuera de su banda.

Lo que más cambia:

- **El bote cae mucho más:** con el tope de 50.000 Y$ y cinco jugadores, el primer bote llega en unos 9 días. `jackpot_1` pasa de Legendario a Raro, `jackpot_5` de Mítico a Épico y los de bote misterioso y sequía bajan dos escalones. «Bote gordo» pide ahora 40.000 Y$ en vez de 50.000, porque el bote ya no pasa del tope.
- **El 7️⃣ 7️⃣ 7️⃣ es más raro:** el rodillo virtual hace que el 7️⃣ roce la línea a menudo y entre poco. El primer trío tarda unos 110 días (Raro → Legendario), y lo mismo «Frutería completa», que lo necesita.
- **Re-giros:** fallar tres, cuatro o cinco seguidos pasa en el primer o segundo día, porque el 81 % de los re-giros fallan. Son Comunes; siete seguidos, Épico.
- **Barra de bonus:** se llena cada ~80 tiradas pagadas y es regular (el 98 % de las barras tardan entre 60 y 103), así que los de «tardar mucho» o «llenarla rápido» piden 100, 110 y 60 tiradas, no cifras redondas que no salen nunca.
- **Giros gratis encadenados:** desde que los 🎟️ también cuentan dentro de los giros gratis, `free_again` (sacar giros gratis en un giro gratis) sale a los ~7 días en la simulación: Raro. Pasa en ~4 % de las tandas de giros gratis y sube el retorno de la máquina unas 3 décimas.
- **Celebraciones:** el GRAN PREMIO (×5) sale una vez cada ~17 tiradas, así que sus logros bajan un escalón.

Los casi-premios pasan del 0,8 % al 21 % de las tiradas: con 25 y 100 salían en horas. Las metas de `nearmiss_25`, `nearmiss_100` y `nearmiss_500` suben a 1.000, 5.000 y 25.000 (los `id` y las rarezas se quedan), y la de `antic_100` a 1.000.

No se simulan, y se revisan a mano, los que dependen de lo que hace el miembro y no de la suerte: el giro diario (uno al día, rachas de días seguidos), el calor que se enfría (irse más de diez minutos) y el ticket (cerrar la máquina). La tabla de la simulación los marca como «>400 d» porque el jugador simulado nunca hace esas cosas.

## Excepciones que la simulación marca y se dejan

Tras los cambios, 320 de los 370 logros simulados caen en su banda y 27 están a una de distancia, casi siempre en el límite de tres días. Los 23 restantes no son fallos de rareza:

- **Dependen de la hora:** los de madrugada (`slots_night`, `botes_night`, `pachi_night`). La simulación juega a las 18:00.
- **Dependen de la apuesta, no de la suerte:** ganar 10.000 o 100.000 Y$ de golpe (`crash_fuel`, `mines_rich`, `pollo_rich`, `moneda_rich`, `dados_rich`, `pachi_rich`, `botes_win_10k`). Con 100 Y$ por jugada son imposibles; con apuestas grandes, no.
- **Dependen de una decisión:** cobrar tras un diamante o a 1,10x, plantarse con 11, ser gallina en el Pollo, cubrir toda la ruleta, jugar con las 12 cantidades de minas, el pleno repetido al número de siempre. El jugador simulado no lo hace; uno de verdad lo hace cuando quiere.
- **Dependen de la mesa o de la sesión:** 10 apuestas a la vez en la ruleta, 500 tiradas sin cerrar la tragaperras, la racha de 8 en la ruleta (con apuestas solo a color sale en unos 19 días, Épico).

## Cantidad

| | Antes | Después |
|---|---|---|
| Logros | 710 | 1.212 |
| Secretos | 127 | 213 |
| Míticos | 17 | 65 |

Primera tanda (chat y voz): 262 logros. Segunda tanda (el resto de funcionalidades): 240. Ninguna categoría baja de 12 logros. Ruleta, blackjack, tragaperras, Botes, Pollo, cara o cruz, dados, pachinko, loterías, tienda, banco, trabajo, oficios y Hong Kong pasan de 40; Crash, Minas, Sanidad y Oficina rondan los 35. Las que no caben en un embed se parten en hojas con ◀ y ▶ en `logros`, así que el tamaño de una categoría ya no pone techo.
