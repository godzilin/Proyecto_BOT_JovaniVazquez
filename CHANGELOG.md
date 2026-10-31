# Historial de versiones

Cada cambio que entra en `main` sube la versión del bot (`src/bot/__init__.py`)
y añade aquí su entrada, la más nueva arriba. Cómo se decide el número está en
la sección «Versiones» de `Biblia.txt`. Las versiones anteriores a la 5.22.0
se asignaron a posteriori, revisando cada PR y commit de `main` con el mismo
criterio.

## 9.x

### 9.35.0 · 2026-10-31

- Jovani: cambia de foto, banner, bio y estado según lo que pasa en el servidor
- Jovani: 12 looks de serie que salen solos: de diario, pijama, noche de casino, lunes de Hacienda, recién actualizado, beernight, matadragones, Carnaval, Día de Canarias, Halloween, Navidad y aniversario
- Jovani: el banner lleva dibujados la versión y tres datos curiosos del servidor, y el estado los va turnando
- Jovani: `jovani`, ficha pública con la versión, el look, cuánto lleva encendido y las cifras de la semana y de siempre
- Admin: `aspecto` fija un look, crea looks propios y cambia su avatar (también GIF animado), banner, bio y estado
- Logros: 24 nuevos en 🎤 Jovani

### 9.34.0 · 2026-10-29

- Pico: el pico rebota y va excavando su hueco a saltos, como en los juegos de este tipo, en vez de caer a plomo; ningún bloque normal cae de un golpe y se ven las grietas
- Pico: bloques y pico un 50 % más grandes (6 columnas de 48 px) y un mundo más corto: el Nether en la −80, el End en la −160 y el dragón en la −220
- Pico: bloque misterioso, que multiplica por ×2, ×3, ×5 o ×10 y no lo dice hasta romperlo; los multiplicadores se apilan a la vista («×2 · ×3 = ×6») y cualquier premio sale con lluvia de monedas
- Pico: los GIF ya no se paran entre una parte y la siguiente ni dan tirones de cámara
- Pico: 🏆 Récords y el ranking solo cuentan las tiradas del mundo nuevo (las de la 9.33 llegaban a capas que ya no existen)
- Logros: 8 nuevos en ⛏️ Pico (bloque misterioso, ×10 del misterioso, multiplicadores en una tirada, premios menores que el precio); los de capas se ajustan al mundo nuevo y las capas −300, −404 y −666, que ya no existen, pasan a 300 golpes y a cobrar 404 y 666 Y$
- Interno: el calibrador del pico ya no se para antes de tiempo con los picos baratos
- Administración: `dinero` también quita yapdollars con `quitar` (si no le llega, deja el saldo a cero)
- Administración: `repicar` va solo con punto (`.repicar`), sin comando de barra

### 9.33.1 · 2026-10-28

- Hotfix: arreglo menor de la 9.33.0

### 9.33.0 · 2026-10-28

- Pico: `picar`, un pico que cae rompiendo bloques hasta el dragón
- Pico: seis picos con precio fijo (de 10 a 5.000 Y$) que caen por un mundo de bloques con físicas: minerales que suman premio, bloques que lo multiplican, TNT en cadena, cofres y mesas de crafteo que devuelven vida, barrancos, el Nether, el End y el dragón, que paga el tope. Todos devuelven el 99,5 % exacto. La partida se ve entera en GIF, con rótulos entre dimensiones, y deja una tarjeta con el premio y el récord. 📋 Premios y 🏆 Récords con el ranking del servidor
- Pico: cada tirada se guarda con su semilla; `repicar` (administración) la vuelve a jugar para auditarla
- Logros: 134 nuevos en ⛏️ Pico; el desplegable de 🎰 Casino se reparte en dos porque ya no cabía
- Interno: `GifStream` monta GIF largos por tramos sin tener todos los fotogramas en memoria

### 9.32.0 · 2026-10-26

- Lista: las tareas se editan con el menú ✏️
- Lista: el menú ✏️, debajo del de tachar, abre un formulario con el nombre de la tarea ya escrito y su prioridad elegida; al guardar, la lista se actualiza en su sitio y dice qué cambió. Pueden editar quien la apuntó y los administradores, y la tarea conserva su antigüedad
- Lista: el menú, las flechas y el formulario siguen funcionando tras reiniciar el bot, también un formulario que ya estaba abierto (el rescate de clics deja los formularios reclamados con `claim_custom_ids` a su listener)
- Logros: 4 nuevos en 📝 Lista (editar 1, 10 y 50 tareas, y el secreto *Ya si eso, mañana*)

### 9.31.4 · 2026-10-26

- ONGs: los médicos e ingenieros del Gran Hotel Emergencia, en la tumbona
- ONGs: textos más cortos: los médicos e ingenieros que nos prometieron están parados en el hotel sin hacer nada; el regalo del expediente pasa a ser la bata de médico en la tumbona

### 9.31.3 · 2026-10-26

- ONGs: en el Gran Hotel Emergencia esperan médicos e ingenieros sin homologar
- ONGs: dentro del resort hay una cardióloga, dos ingenieros y un anestesista esperando desde 2023 a que les homologuen el título, mientras faltan médicos en todas las islas; nuevo regalo, el expediente de homologación sin abrir

### 9.31.2 · 2026-10-26

- ONGs: la de la acogida pasa a ser el «Gran Hotel Emergencia ★★★★★»
- ONGs: la broma es el resort de lujo que el Gobierno paga a precio de temporada alta: el minibar a 9 € el agua, la tasa turística que se cobra a sí mismo, el «módulo habitacional con vistas» y el consejero compareciendo desde la tumbona; regalos y logros nuevos a juego

### 9.31.1 · 2026-10-26

- ONGs: la Fundación Acogida pasa a ser «Eso Es Cosa de Madrid»
- ONGs: textos y regalos nuevos para la ONG de la acogida en Canarias: el pendrive del consejero de vacaciones, el «pulse 2 y espere cuatro años», el contrato en una servilleta y el croissant de la cumbre de crisis

### 9.31.0 · 2026-10-26

- ONGs: la Fundación Acogida Cinco Estrellas
- ONGs: 🏨 Fundación Acogida Cinco Estrellas, sobre la gestión de la acogida en Canarias: Madrid y Canarias pasándose la pelota, contratos de emergencia sin concurso, el hotel pagado a precio de suite y las ruedas de prensa que no dicen nada
- ONGs: diez regalos exclusivos para ella y 4 logros nuevos (sus tres escalones y donar a las 11 ONGs)

### 9.30.1 · 2026-10-26

- ONGs: Techo Para Todos pasa a ser la Fundación Ningún Inversor Sin Casa
- ONGs: la ONG del fondo buitre cambia de nombre y de textos (trasteros como «lofts», balcones como habitaciones, el psicotécnico para alquilar); lo donado y los logros se conservan

### 9.30.0 · 2026-10-26

- ONGs: la Fundación Familia Primero, del cuñado de Perro Sanxe
- ONGs: la Fundación Ejemplaridad Borbónica pasa a ser 🐶 Fundación Familia Primero: la preside el cuñado de Perro Sanxe, que lee el BOE la víspera, pide las ayudas para sus empresas y se las adjudica él mismo. Lo donado y los niveles de mecenas se conservan
- ONGs: diez regalos exclusivos nuevos para ella (el enchufe de la Diputación, el BOE de mañana, el contrato troceado…) y sus logros cambian de nombre; los dos secretos piden ahora 14.999 o 5.300 Y$ exactos
- ONGs: Techo Para Todos ya no lleva «(patrocina un fondo buitre)» en el nombre

### 9.29.0 · 2026-10-26

- Logros: 28 nuevos de las ONGs
- Logros: sorteos jugados y perdidos, varios ganados el mismo viernes, ganar sin rivales o con un boleto contra un Patrono, ser el mayor donante de una ONG o del muro y mirar la ficha de las 10 ONGs
- Logros: donar en fechas señaladas (viernes 13, Halloween, Nochevieja, Reyes, el Pino, los Inocentes) y cifras secretas por ONG

### 9.28.0 · 2026-10-26

- ONGs: el sorteo pide donar 1.202 Y$ y los exclusivos son sorpresa
- ONGs: para entrar en el sorteo de una ONG hay que donarle en la semana al menos 1.202 Y$ (lo que desgrava al 80 %); antes bastaba con 1 Y$
- ONGs: la ficha de cada ONG ya no enseña sus regalos exclusivos: cuál te toca es sorpresa
- Logros: rarezas de los sorteos y los regalos de las ONGs recalculadas con una simulación de los sorteos, y 3 nuevos (50 y 150 sorteos ganados y 200 regalos)

### 9.27.0 · 2026-10-26

- ONGs: dos ONGs más picantes, 100 regalos exclusivos y 49 logros nuevos
- ONGs: 👑 Fundación Ejemplaridad Borbónica (su donativo acaba en Abu Dabi) y 🦅 Fundación Techo Para Todos (patrocina un fondo buitre)
- Tienda: 60 regalos exclusivos nuevos, diez por ONG (100 en total); siguen sin venderse
- Logros: 49 nuevos de las ONGs: Oro y Patrono de cada ONG, mecenas de varias ONGs, colecciones de exclusivos, más sorteos y regalos, el mayor donativo, fechas y cifras secretas

### 9.26.0 · 2026-10-26

- ONGs: `donar` con panel, ocho ONGs, mecenas y sorteo de los viernes
- ONGs: cuatro nuevas: 🎮 Gamers Sin Fronteras, 🤳 Protectora de Influencers Abandonados, 🍌 Plataforma Salvemos el Plátano Canario y 🤖 Fundación Amigos del Bot Jovani
- ONGs: `donar` sin cantidad abre un panel con tus niveles, el muro de donantes y la ficha de cada ONG (sus mayores donantes, tu progreso y sus regalos), con botones para donar 500, 5.000 u otra cantidad
- ONGs: lo donado a cada ONG te hace mecenas suyo (🥉 Bronce 5.000 Y$, 🥈 Plata 25.000, 🥇 Oro 100.000, 🏛️ Patrono 500.000); cada nivel trae un regalo y tu mejor nivel sale en `perfil`
- ONGs: cada viernes a las 20:00 cada ONG sortea un regalo entre quienes le donaron esa semana, con más boletos cuanto más alto es tu nivel
- Tienda: 40 regalos exclusivos de las ONGs (cinco por ONG) que no se venden; salen pocas veces en el sorteo y siempre al llegar a Oro y a Patrono. Si no toca uno, la ONG regala algo de la tienda de hasta 500 Y$
- Logros: 15 nuevos de las ONGs (las ocho ONGs, los niveles de mecenas, los sorteos y los regalos)

### 9.25.0 · 2026-10-25

- Lotería: todo dibujado, botes que crecen cada día y rasca animado
- Lotería: el panel abre con el cartel de la administración (los cuatro botes en grande, lo que suben al día y los próximos sorteos de la Nacional); al comprar sale el décimo o el resguardo con las bolas, y 🎟️ Mis boletos enseña el último boleto sorteado con los aciertos marcados
- Lotería: el rasca sale rascándose en un GIF, con las casillas que deciden al final, y el resultado llega cuando acaba; el siguiente rasca ya está pintado al pulsar. Las casillas sin premio salen de la propia tabla, así que a veces se ven dos del premio máximo
- Lotería: los sorteos se anuncian con las bolas saliendo del bombo (en la Nacional, las cifras de los premios) y, al acabar, el resultado con los premiados
- Lotería: el bote de Primitiva, Bonoloto, Gordo y Euromillones lo garantiza el Estado y sube cada día sin acertante (Primitiva desde 5.000 Y$ y +400 al día, Euromillones desde 15.000 y +1.000, hasta 90 días); antes anunciaban de 0 a 800 Y$
- Lotería: las categorías por debajo del bote pagan premios fijos que crecen con la dificultad (acertar el bote de la Primitiva pagaba de mediana 6 Y$); máximo 10 apuestas por persona y sorteo en los juegos de bote
- Logros: 20 nuevos de la lotería (el «casi» del rasca, botes grandes, diez apuestas, racha de días, comprobar el boleto, fechas y cuatro números con guasa en secreto)

### 9.24.0 · 2026-10-25

- Renta: la declaración en una carta dibujada, con desglose y consejo de donar
- Renta: al abrir el sobre sale la carta (de lo que ganaste a tu base, la cuota frente a lo retenido, el sello con el resultado, el tipo medio y la tabla por conceptos) y cuatro pestañas: 📄 Resumen, 🧩 Desglose, 📜 Paso a paso y 🤝 Consejo
- Renta: 🧩 Desglose reparte la cuota entre cada fuente (trabajo, alquileres, casino…) y dice cuánto faltó o sobró por retener en cada una
- Renta: 🤝 Consejo calcula cuánto habría rendido donar esa semana (rehecho con la declaración de verdad) y lleva el botón 🤝 Donar con la cantidad que más rinde ya escrita
- Renta: pagar o presentar pone su sello en la carta, que queda de justificante; arreglado que, tras presentarla, `renta` siguiera diciendo «preséntala»
- Logros: 7 nuevos de la Renta, por mirar el desglose y el consejo y por donar desde el consejo

### 9.23.0 · 2026-10-25

- Pisos: eliges cuánto dura cada contrato y puedes recuperar el piso aunque el inquilino pague
- Pisos: en 🧰 Gestionar, la duración del próximo contrato, de 1 a 5 años (12 a 60 días); firmar corto trae menos candidatos y más impagos, pero al acabar lo recuperas gratis
- Pisos: botón 🔑 Recuperar el piso en la ficha de un piso alquilado, con tres caminos: no renovar (gratis; en zona tensionada el inquilino puede pedir un año de prórroga), pedirlo para vivir (art. 9.3 LAU: se va en 2 días, te mudas y tienes que vivir ahí 12) y comprarle las llaves
- Pisos: comprar las llaves cuesta medio mes de renta por cada año que le queda (más si está a gusto o si los alquileres han subido); se le ofrece por las buenas, lo justo o algo generoso, con la probabilidad de que acepte en el botón; si dice que no, te dice su precio, y uno de riesgo alto puede atrincherarse
- Pisos: fama de casero: cada piso recuperado así en 30 días encarece el siguiente un 20 %, nadie se va por las buenas y los candidatos tardan más
- Pisos: a un miembro de alquiler se le ofrecen las llaves en el canal y regatea; lo que cobra paga el 19 % de IRPF (ganancia patrimonial)
- Logros: 46 nuevos en 🏠 Pisos (comprar llaves, la fama, el regateo, atrincherarse, la prórroga, la necesidad, las cinco duraciones…)

### 9.22.0 · 2026-10-25

- Deportes: `deportes` estrena panel y apuestas a los goles
- Deportes: panel nuevo con el color de cada competición, los próximos partidos con su botón y, en cada partido, la probabilidad de cada resultado en barras y los goles que se esperan de cada equipo
- Deportes: tres mercados nuevos además del 1X2: más/menos goles (1,5, 2,5 y 3,5), ambos marcan y resultado exacto (del 0-0 al 3-3 y «otro resultado»), cada uno en su pestaña
- Deportes: una apuesta por mercado y partido; el tope de 50.000 Y$ cuenta para todo el partido sumando mercados
- Deportes: al apostar llega el boleto dibujado, con los escudos de los dos equipos, y al acabar el partido sale en el canal el marcador final dibujado con cómo quedó cada mercado
- Deportes: las cuotas de goles salen de ajustar un modelo de goles al 1X2 y al más/menos de las casas; el exacto deja un 15 % a la casa y el resto, el 6 % de siempre
- Logros: 20 nuevos en ⚽ Deportes (cada mercado, clavar un exacto, quedarse en el palo por un gol, los cuatro mercados de un mismo partido…)

### 9.21.0 · 2026-10-25

- Fortunas: `fortunas` estrena panel, con podio, páginas y tu puesto
- Fortunas: arriba, el patrimonio de todo el servidor, de qué es (pisos, efectivo, sociedades, tienda) y lo que se debe, con el Gini en barra
- Fortunas: los tres primeros salen con su avatar y su desglose; del cuarto en adelante, una línea por persona, de 10 en 10 con ◀ ▶ (antes solo salían 15)
- Fortunas: «📍 Tú» dice tu puesto y lo que te falta para el de arriba, y el botón 📍 Yo salta a tu página. Si pulsa otra persona, le sale su copia en privado
- Fortunas: el desglose cuenta ya la sociedad 🏢, que sumaba en el total pero no se veía
- Logros: ocho nuevos de `fortunas` en 🏛️ Economía y Hacienda (pasar páginas, llegar al final, buscarte con 📍 Yo, quedarte cuarto…)

### 9.20.0 · 2026-10-25

- Financiar: cualquier compra a plazos con el banco y el IPC del crédito
- Financiar: 💳 Financiar en la caja, la ficha y el carrito de la tienda, en las obras y divisiones de `pisos`, en la formación, el vuelo y el café de `pala` y en entrenar el gallo. Firmas y compras de una vez; si la compra falla, se deshace
- Financiar: plan normal (el tipo personal más 2 puntos, con seguro y carencia de 7 días), 0 % TIN de la semana en dos categorías (con comisión y seguro obligatorio: la TAE no es 0) y Paga en 3 para lo de 100 a 999 Y$ (sin intereses, con recargo si no pagas)
- Financiar: no se financia lo numerado ni los títulos, ni el juego, impuestos, pujas o compras entre miembros
- Financiar: lo financiado sin devolver sube los precios de la tienda y las obras hasta un 20 % (IPC del crédito), y la caja lo dice
- Financiar: las cuotas salen en `saldo` y en `banco`, donde se amortizan o se desiste
- Saldo: las rentas de esta noche cuentan las habitaciones de los pisos por habitaciones (y sus atrasos e imprevistos), y la cartera de `pisos` ya no enseña la renta de un inquilino que no paga: las dos dicen lo mismo
- Logros: quince nuevos de financiar compras en 🏦 Banco

### 9.19.0 · 2026-10-25

- Pala: 4 franjas de noche para trasnochar y el café vale 8 veces más
- Pala: acabadas las 8 franjas del día se pueden trasnochar 4 más (horas extra, B, guardias, estudio o el negocio), y ninguna después. De noche la Inspección pilla el B un 30, 40, 50 y 60 % de las veces (de día, un 10 %) y lo devuelves doble, y cada turno de la `pala` trasnochando cuesta 5 de familia. El panel y la oferta del B dicen el riesgo de ese turno
- Pala: el café de la máquina vale unas 8 veces más: el cortado, 110 Y$, y el barraquito, 200 Y$ (antes, 14 y 25), con IGIC aparte
- Pala: el inicio pinta las 12 franjas, con las 4 de noche sobre fondo oscuro
- Logros: catorce nuevos de trasnochar, del B de noche y de los cafés, y «¿Qué es dormir?» (12 turnos en un día) vuelve a poderse sacar

### 9.18.0 · 2026-10-24

- Admin: `dinero`, para que Yeyo y Dani den yapdollars a un miembro o a sí mismos (devolver lo perdido por un fallo o dar premios)

### 9.17.3 · 2026-10-24

- Yapdollars: ningún panel enseña ya euros. La lotería (décimos, rascas y sus premios), `/renta`, el capital y el contrato en B de los negocios, las fichas de `pisos`, Hong Kong, el casino y el Bizum dan sus cifras en Y$, con el valor que aplica el juego
- Yapdollars: los textos de ambiente (eventos de `pala`, logros, barrios, tienda, multas de broma) pasan sus euros a Y$ a 1 € = 100 Y$, y el minijuego de pedidos del rider paga en Y$
- Logros: los textos que explican la ley (Bizum, seguro de impago, vivienda vacía, Bono Alquiler Joven, Grandes Fortunas, tarifa plana, Ley Beckham, tramos del ahorro) dan las cifras del juego en Y$

### 9.17.2 · 2026-10-24

- Hotfix: arreglo menor de la 9.17.1

### 9.17.1 · 2026-10-24

- Hotfix: arreglo menor de la 9.17.0

### 9.17.0 · 2026-10-24

- Hacienda: `hacienda` es un panel con pestañas (Resumen, Impuestos, Contribuyentes, Caseros y Empresarios): el saldo del Estado en grande, barras con lo que aporta cada impuesto y el podio de quien más paga
- Hacienda: nueva pestaña 🏪 Empresarios con lo que pagan los negocios de `pala` y los empresarios que más pagan (IGIC, Seguridad Social e IRPF de su plantilla, su propio IRPF, Sociedades, AIEM, tasas y multas)
- Hacienda: la factura de `hacienda @miembro` se ve en un solo bloque y enseña también lo que paga por sus negocios
- Logros: cuatro nuevos de `hacienda` (mirar 🏪 Empresarios, las cinco pestañas y, secretos, encabezar a los caseros o a los empresarios)

### 9.16.1 · 2026-10-24

- Hotfix: arreglo menor de la 9.16.0

### 9.16.0 · 2026-10-24

- Pisos: sobreáticos y áticos con vistas en la mitad de los bloques de los barrios con vistas: más grandes, con terraza y bastante más caros (54 nuevos)
- Pisos: los pisos grandes, los áticos, los lofts, los locales y los bajos amplios se pueden dividir en dos (izquierda y derecha), y en el edificio por dentro cada hueco ocupa lo que mide
- Pisos: los edificios por dentro ya no son todos iguales: cada barrio tiene su estilo y cada vivienda, local, plaza y trastero se ve distinto según lo que pasa en él

### 9.15.2 · 2026-10-24

- Pisos: en el plano de calles los edificios y sus botones van numerados en orden (1, 2, 3… calle a calle y de izquierda a derecha), no por el número de su tarjeta

### 9.15.1 · 2026-10-24

- Pisos: elegir un barrio ya abre su plano de calles; daba error porque dos botones del plano y del edificio repetían el identificador del desplegable de barrios y Discord rechazaba la pantalla
- Pisos: en el edificio por dentro, ⭐ Destacados en lugar de un segundo botón que llevaba también a las calles

### 9.15.0 · 2026-10-24

- Pisos: al elegir un barrio se ve directamente su plano de calles, con el índice y la noticia del barrio; los nueve de siempre, en ⭐ Destacados
- Pisos: los barrios del centro y obreros tienen más edificios que los del campo, y entre los bloques hay casas terreras, casonas y chalés (405 inmuebles más)

### 9.14.0 · 2026-10-24

- Pisos: 🗺️ Calles en cada barrio: tres calles reales con tres edificios cada una, y cada edificio se ve por dentro, partido por la mitad, con lo que hay en cada planta, qué se hace y de quién es
- Pisos: 866 inmuebles nuevos alrededor de los de siempre (pisos, locales, plazas de garaje, trasteros, amarres, bungalows y naves), para empezar con poco dinero y acabar juntando edificios

### 9.13.0 · 2026-10-24

- Pisos: al dividir un edificio por plantas, el bajo es un local comercial (se puede montar ahí tu negocio) y sale un trastero por vivienda; los edificios que ya se dividieron siguen como estaban
- Pisos: la pantalla de plantas enseña hasta diez tarjetas y los edificios dicen sus plantas en el título

### 9.12.0 · 2026-10-24

- Pisos: en 🧰 Gestionar → 🏢 Dividir, los edificios, las casonas, los chalés y los dúplex se dividen por plantas: cada planta es un inmueble con su inquilino, su hipoteca y su precio, y se vende por separado (juntas piden un 25 % más; obra con IGIC, ICIO, licencia y AJD de la escritura)
- Pisos: los pisos de tres dormitorios, los áticos, los dúplex, las terreras, los chalés y las casonas se pueden alquilar por habitaciones, cada una con su inquilino (10 inquilinos nuevos que solo buscan habitación); compensa en el centro, los barrios históricos y la playa, y se vuelven a juntar cuando están vacías
- Pisos: botón verde 👥 Candidatos en el mapa, el barrio y Mis pisos cuando alguien espera a que elijas inquilino, que lleva de un piso o habitación al siguiente
- Hacienda: el AJD de las escrituras de división horizontal sale con el ITP de los pisos

### 9.11.0 · 2026-10-24

- Negocios: hasta 4 locales por persona, una cadena con sus franjas, su batería y su cuota de autónomo de siempre; 🗺️ Cadena en la portada para ir de uno a otro y montar otro con el mismo rótulo
- Negocios: si eres sociedad, la sociedad lleva todos los locales: una caja, una gestoría por noche y un solo Impuesto sobre Sociedades con lo de todos; cerrar uno de ellos no la liquida
- Negocios: 🏭 una nave alquilada o tuya de `pisos` para importar contenedores de la Península, más baratos que el mayorista, con su flete, el AIEM y el IGIC de importación; reparte a tus locales de su isla
- Negocios: 🧑‍💼 gerentes que cada noche reponen el almacén (de la nave primero) y cubren las bajas de la plantilla
- Negocios: 💼 vender la sociedad con toda la cadena por el valor del art. 37.1.b LIRPF; la ganancia paga el 19 %
- Banco: cada local de una cadena pide sus préstamos, y el mostrador del negocio pasa de uno a otro
- Hacienda: el AIEM de las importaciones sale aparte

### 9.10.2 · 2026-10-24

- Tienda: las ediciones limitadas dicen cuántas quedan en la lista y en el desplegable, sin tener que abrir la ficha

### 9.10.1 · 2026-10-24

- Logros: los de 🎸 Robuso ya no dicen «Solo Robuso:», así no se sabe cuáles son solo suyos

### 9.10.0 · 2026-10-23

- Patrimonio: vuelve `patrimonio` (también `.patrimonio @miembro`) y cada piso sale con la parte que tienes según tus participaciones
- Saldo: `saldo @miembro` enseña todo lo pendiente de ese miembro, igual que si lo pidiera él
- Pala: en los minijuegos de memoria (como los plenos de Bruselas), el reloj se para hasta 5 segundos mientras memorizas cada secuencia

### 9.9.0 · 2026-10-23

- Reinicios: antes de reiniciarse, el bot espera a que acabe lo que hay a medias (turnos de `pala` y del negocio, partidas y paneles en uso), como mucho 15 minutos; también en la actualización de las 5:00
- Reinicios: mientras se prepara la versión nueva todo sigue funcionando, y quien usa un comando recibe una vez el aviso de que en unos minutos habrá reinicio
- Reinicios: el aviso al pulsar un botón del minijuego de `pala` o del negocio ya no le quita la respuesta al juego

### 9.8.1 · 2026-10-23

- Tienda: la 🎒 Mochila vuelve a abrirse a quien tiene a la vez un título y una skin (daba «Algo ha fallado con ese botón»)
- Negocios: un clic en el minijuego mientras el panel sube la imagen del cliente siguiente se acepta al momento, sin esperar a que acabe la subida

### 9.8.0 · 2026-10-22

- Negocios: 👥 Plantilla, contrata empleados entre los tres candidatos que llegan cada día, cada uno con su perfil, dos rasgos y tres habilidades (acierto, rapidez y trato)
- Negocios: contrato indefinido o temporal (6 meses), los dos con período de prueba, o en B; jornada completa o media, de mañana o de tarde; de 2 a 6 según el tamaño del local
- Negocios: la plantilla atiende sola las franjas de su turno con el mismo minijuego que tú, y sus ventas, reseñas y estrellas cuentan igual; aprenden trabajando y se forman con cursos (con el crédito de FUNDAE)
- Negocios: cada noche, su nómina con las cotizaciones de 2026 y la cuota de la empresa, que por fin paga alguien; el salario mínimo de 2026 y la bonificación del parado de larga duración
- Negocios: si no llega para pagarles se les debe y es lo primero que se paga; con dos noches sin cobrar se van con la indemnización del despido improcedente, y los seguros sociales que no se pagan llevan un 20 % de recargo
- Negocios: humor, faltas y dimisiones; súbeles el sueldo o hazlos fijos; despido en prueba, disciplinario, objetivo o improcedente, cada uno con su indemnización
- Negocios: quien está en B no cotiza, pero si pasa la Inspección de Trabajo hay multa de la LISOS y las cuotas atrasadas con recargo
- Negocios: al cerrar, la plantilla se despide con 20 días por año; al traspasar, se va con el negocio
- Negocios: las sociedades pueden dotar la Reserva para Inversiones en Canarias (RIC) y, un taller con seis empleados, entrar en la Zona Especial Canaria (Sociedades al 4 %)
- Logros: 63 nuevos de la plantilla, la RIC y la ZEC en 🏪 Negocio

### 9.7.0 · 2026-10-22

- Negocios: pide préstamos a nombre de tu negocio en `banco` (🏪 Negocio) o desde la portada del negocio en `pala` (🏦 Banco)
- Negocios: de autónomo lo firmas tú (entra en tu monedero y cuenta en tu 35 %); de sociedad lo firma ella (entra en su caja, cabe en lo que deja ella más lo tuyo y lo avalas)
- Negocios: el tipo es el de una pyme (Euríbor + 4 % y tu perfil), hasta 2 por negocio aparte de los personales; sin desistimiento ni seguro, que son del crédito al consumo
- Negocios: las cuotas las paga la noche del negocio, después del local y de Hacienda; los intereses y la comisión son gasto (IRPF o Sociedades)
- Negocios: si no paga, fichero de morosos y vencimiento como un préstamo personal; de una sociedad te lo reclama a ti y ella pasa a debértelo
- Negocios: al hacerlo sociedad o al cerrar, lo que quede sigue a tu nombre como préstamo personal (al liquidar la sociedad, su caja paga antes lo que pueda)
- Patrimonio: lo que debe tu sociedad al banco resta de su valor, no del tuyo
- Logros: 14 nuevos de los préstamos al negocio en 🏪 Negocio

### 9.6.0 · 2026-10-21

- Negocios: 🏢 Sociedad, convierte tu negocio en una sociedad limitada (capital de 1 €, 3.000 € o 10.000 €; notaría y registro, sin ITP)
- Negocios: la sociedad tiene caja propia: cobra las ventas sin IRPF y paga el local, las compras, las mejoras y una gestoría cada noche
- Negocios: cada domingo, el Impuesto sobre Sociedades de la semana (19 % y 21 % como microempresa), con su reserva legal y la compensación de pérdidas
- Negocios: cobra de tu sociedad un sueldo de administrador (con su retención, cuenta como nómina) o dividendos (19 %); apórtale o préstale dinero
- Negocios: si se hunde, solo pierdes lo que hay dentro (salvo los 3.000 € del capital mínimo, lo que debe a Hacienda y las pérdidas sin remediar); al cerrar se liquida y te llevas la cuota
- Negocios: algunos curros no dejan administrar una sociedad (altos cargos, Alta Administración, sanidad pública, el unicornio y la hostelería con una cafetería o heladería)
- Negocios: quien cotiza por la `pala` y por su negocio recupera cada domingo lo que cotice de más (pluriactividad)
- Patrimonio: tu sociedad cuenta en `perfil`, `fortunas` y el Impuesto sobre el Patrimonio
- Hacienda: el Impuesto sobre Sociedades sale aparte
- Logros: 35 nuevos de la sociedad en 🏪 Negocio

### 9.5.0 · 2026-10-21

- Negocios: dos sectores nuevos, 🍦 heladería (comandas) y 🔩 ferretería (la caja)
- Negocios: 🛠️ Mejoras, dos o tres por sector (la terraza, la cafetera de dos grupos, el elevador…), que suben los clientes, la paciencia o la propina, o bajan la merma; se amortizan cada noche y la terraza paga su tasa
- Negocios: 📣 campañas de publicidad (buzoneo, redes y radio) que suben los clientes y tu parte del cupo del barrio unos días
- Negocios: ⭐ reseñas de los clientes al acabar cada turno; con 4,5 de media y 10 reseñas, el negocio sale recomendado (un 10 % más de clientes)
- Negocios: 🚚 Traslado a otro barrio o a un local mediano o grande, o a un local o nave entera tuya de `pisos` (sin alquiler ni fianza; mientras esté tu negocio, no se alquila ni se vende)
- Negocios: el precio de cada producto se puede poner aparte en el almacén
- Negocios: al cerrar, 🤝 traspasarlo (te pagan el stock, las mejoras y la clientela, que tributa al 19 %) o liquidarlo (el stock al 50 % y las mejoras al 40 %); antes el stock se perdía
- Negocios: el acondicionamiento del local ya no es gasto entero de la semana: se amortiza al 10 % como instalación
- Negocios: el IGIC de lo que se compra al mayorista sigue al Decreto Legislativo 1/2025 (el café y la fruta van al tipo cero; zumo, dulce, conservas y limpieza, al 3 %)
- Pisos: un local con tu negocio dentro sale como «Tu negocio» y no se puede vender hasta trasladarlo o cerrarlo
- Logros: 44 nuevos en 🏪 Negocio (reseñas, mejoras, publicidad, traslados, traspasos y los sectores nuevos); «Cafetería, tienda y taller: el trío calavera» pasa a llamarse «Tres tristes trastiendas»

### 9.4.0 · 2026-10-21

- Negocios: el IMV y el banco cuentan lo que deja tu negocio (ventas menos su IRPF y sus gastos, nunca en negativo): quien vive de su negocio ya no es «sin nómina» para los préstamos, y el IMV baja como con una nómina
- Negocios: sin curro, los turnos del negocio también cansan; el inicio de `pala` enseña tu batería, y al fichar tu primer curro llegas con la que tengas
- Negocios: el primer cliente del turno ya está pintado al pulsar 🔑 Abrir
- Negocios: la mascota tiene frases propias al cerrar un turno, contenta con una S o una A y de bajón con una D; la paloma también se posa al salir del negocio
- Logros: 52 nuevos en 🏪 Negocio (escalones intermedios, domingos, el mayorista, las noches, los cierres, los clientes rápidos y los que se van)

### 9.3.0 · 2026-10-21

- Banco: 🤝 avalista, otro miembro te avala un préstamo o una rehipoteca (suma su 35 % y baja el tipo medio punto)
- Banco: si no pagas, el banco reclama al avalista y tú le debes lo que pague, que se cobra cada noche y se embarga de tus nóminas
- Saldo: lo que avalas, lo que debes a tu avalista y lo que te debe tu avalado
- Logros: 11 de avales en 🏦 Banco

### 9.2.0 · 2026-10-21

- Banco: 💸 Anticipos, el banco te adelanta 3, 6 o 12 meses de la renta de un piso alquilado y se la cobra cada noche
- Pisos: 💸 Adelantar rentas en 🧰 Gestionar abre el banco con el inmueble elegido; con un anticipo vivo no se puede vender
- Saldo: la cuota del anticipo sale como «Anticipo de rentas de…»
- Logros: 12 de anticipos de rentas en 🏦 Banco

### 9.1.0 · 2026-10-21

- Banco: 🔐 Garantías, rehipotecar tus pisos: hasta el 80 % de lo que valen hoy (60 % si no es vivienda) menos lo que ya debes de ellos
- Pisos: 🏦 Rehipotecar en 🧰 Gestionar abre el banco con el inmueble elegido
- Pisos: las hipotecas de un mismo inmueble se ejecutan juntas, con una sola subasta (antes podía quedar una viva sin inmueble)
- Perfil: el patrimonio y `fortunas` restan las deudas con el banco
- Patrimonio: las hipotecas de la vivienda habitual restan en la parte que no está exenta
- Logros: 11 de rehipotecas en 🏦 Banco

### 9.0.0 · 2026-10-21

- Vender: `vender` sin nada abre un panel con tu mochila: a un miembro, al Rastro, al Monte de Piedad o empeñado
- Rompe: el Monte de Piedad deja `banco` y se muda a `vender` (`/banco mostrador:Monte` ya no existe; `.banco monte` abre los préstamos con un aviso)
- Logros: 2 de ofertas a miembros y «Vendo Opel Corsa» por usar los cuatro destinos de `vender`

## 8.x

### 8.15.1 · 2026-10-21

- Negocios: el embargo de las deudas con el banco alcanza también a las ventas del negocio

### 8.15.0 · 2026-10-20

- Banco: `banco`, préstamos personales con el 35 % junto a las hipotecas, tipo según tu perfil, TAE y seguro
- Banco: Monte de Piedad, vende al 85 % de lo que pagaste o empeña al 55 % y desempeña en 3 días
- Banco: las cuentas de la tienda, el banco y el casino
- Banco: fichero de morosos (ASNEF) y embargo de nómina por tramos para las deudas con el banco
- Logros: 16 del Monte en 🧺 Rastro y 18 de préstamos y cuentas en 🏦 Banco

### 8.14.0 · 2026-10-20

- Negocios: monta tu cafetería, tienda o taller dentro de `pala`
- Pala: un inicio con tus 8 franjas del día, reducción de jornada y excedencia (como mucho 4 horas extra al día)

### 8.13.0 · 2026-10-20

- Deportes: `deportes`, apuestas con cuotas fijas a LaLiga y la Champions

### 8.12.0 · 2026-10-20

- Tienda: escaparate en lista compacta (15 por página) con buscador, filtros y orden
- Tienda: ficha de cada artículo con cantidad, y carrito que se paga todo junto o nada
- Tienda: lista de deseos pública (también en `perfil`) con avisos por privado
- Tienda: la caja avisa antes de pagar si ya compraste el máximo por persona
- Logros: 35 del carrito, la ficha y la lista de deseos en 🛍️ Tienda

### 8.11.0 · 2026-10-20

- Saldo: `saldo` enseña lo que tienes pendiente de pagar, de cobrar y retenido, con su fecha
- Saldo: el Patrimonio del lunes sale con la misma cuenta que `perfil`
- Logros: 32 de mirar el saldo en 🏦 Banco

### 8.10.0 · 2026-10-20

- Reinicio: mientras el bot espera a reiniciarse no se abren partidas ni paneles nuevos, y quien tiene uno abierto recibe un aviso para terminarlo

### 8.9.0 · 2026-10-20

- Admin: `crear`, para que Yeyo y Dani devuelvan un objeto perdido
- Mascotas: ya no se comen lo de edición limitada, como la patata con forma de España

### 8.8.0 · 2026-10-20

- Allá tú: escalera de premios bajo el gordo en las tres tablas y llamada del pésame al abrirlo
- Logros: 11 de Allá tú (el pésame y ganar sin el gordo) y rarezas recalibradas

### 8.7.0 · 2026-10-19

- Renta: la que sale a pagar se paga al abrirla, entera o fraccionada (60 % y 40 % el jueves)
- Renta: quien debe a Hacienda paga el 30 % de lo que cobra, y botón para liquidar la deuda
- Logros: 6 del pago fraccionado, la deuda liquidada y el embargo

### 8.6.1 · 2026-10-19

- Admin: `bloquear` solo para Yeyo y Dani, como `reinicio`

### 8.6.0 · 2026-10-19

- Admin: `bloquear` apaga un comando (y los botones de sus mesas) mientras se arregla un fallo
- Minas: retirarse en la primera casilla (siempre buena) ya no cuenta como jugada ni da logros
- Logros: «Con uno me vale» y sus escalones cuentan los cobros tras el primer diamante con riesgo

### 8.5.0 · 2026-10-18

- Pisos: junta de copropietarios en los inmuebles con varios dueños; quien no vota cuenta a favor
- Pisos: la junta vota renta, seguro, quién elige y quién administra, reformas, licencia y venta
- Pisos: fondo de reserva que paga derramas e ITE, y derramas votadas para llenarlo
- Pisos: acción de división: cualquier dueño saca el inmueble entero a subasta
- Logros: 12 de la junta, el fondo y la división

### 8.4.0 · 2026-10-18

- Pisos: reseñas del vacacional; la nota mueve la ocupación y da Superanfitrión (+10 %)
- Pisos: cada estancia paga la plataforma y la limpieza, con su IGIC
- Pisos: licencia de 5 años (Ley 6/2025): nada nuevo en zona tensionada ni en obra nueva, y en un bloque vota la comunidad
- Tienda: pasillo 🛋️ Muebles del vacacional, diez muebles que suben la nota de las reseñas
- Logros: 13 del vacacional

### 8.3.0 · 2026-10-18

- Pisos: reformas con IGIC, ICIO y licencia de obra que suben la renta y el valor
- Pisos: la reforma integral puede ser rehabilitación y dar la reducción del 60 %
- Pisos: averías, humedades, cerraduras y el vecino los decides tú (o la gestora a los dos días)
- Pisos: el humor del inquilino, que cambia lo que se queda
- Logros: 20 de reformas, imprevistos y humor

### 8.2.0 · 2026-10-18

- Pisos: vender participaciones a otro miembro, con el ITP sobre el valor de referencia
- Pisos: subastas de un día entre miembros, con depósito y la última hora que alarga
- Pisos: las ejecuciones hipotecarias van antes a subasta judicial; sin puja que valga, el banco al 70 %
- Logros: 24 de compraventa y subastas

### 8.1.0 · 2026-10-18

- Pisos: 59 inquilinos con personalidad, riesgo y ficha, y elegir tú entre los candidatos
- Pisos: seguro de impago y seguro del hogar con el impuesto de primas
- Pisos: noticias de barrio los lunes que mueven la demanda, los impagos y las averías
- Pisos: el desahucio paga abogado y procurador; deducciones canarias por el seguro y por alquilar un piso vacío
- Logros: 43 de inquilinos, seguro y noticias

### 8.0.0 · 2026-10-17

- Renta: una declaración cada lunes con toda la renta, que sale a pagar o a devolver
- Renta: `renta` con sobre cerrado, resumen y detalle paso a paso con los artículos
- IRPF: una sola vara para toda la renta y un tipo estable que no baja a mitad de semana
- Casino: retención del 19 % de lo ganado en el día si pasa de 300 €; el lunes ajusta
- Tienda: los seguros pagan el impuesto de primas (8 %) en vez de ir sin impuesto
- Pisos: la hipoteca desgrava, tope de intereses con arrastre, reducción del 50 o el 90 %
- Pisos: los vacíos imputan renta y la notaría y la agencia pagan IGIC
- Patrimonio: el límite con el IRPF de la semana rebaja hasta el 80 % de la ley
- Pala: sueldos nuevos por curva, ramas más largas y P10 más alto
- Pala: una pensión que crece con cada rama, jubilación activa y veteranía
- Pala: cambiar de oficio dice cuánto cuesta; experiencia con menos días y tareas
- Logros: Renta semanal, coleccionista de oficios y ahorro que vuelve a poder conseguirse

## 7.x

### 7.30.1 · 2026-10-18

- Logros: el aviso enseña todos los que desbloqueas a la vez, no solo los ocho primeros

### 7.30.0 · 2026-10-18

- Logros: una categoría secreta con 132 logros

### 7.29.1 · 2026-10-18

- Interno: una línea del catálogo de logros demasiado larga

### 7.29.0 · 2026-10-18

- Logros de chat y voz: 384 nuevos. Enlaces y adjuntos, cifras y horas con guasa, temas con guasa (pizza con piña, la tortilla, ChatGPT, la 33…), ping-pong y respuestas rápidas, reacciones con emoji propio, racha de días en llamada, con quién hablas, abrir y cerrar llamadas, jugar en llamada, escribir desde la llamada y 84 de 🎭 Personajes

### 7.28.0 · 2026-10-18

- Logros: 1.604 nuevos (5.865 en total). Escalones intermedios en casi todos los contadores (chat, voz, música, memes, casino, juegos, trabajo, tienda, pisos, loterías…) y nueve secretos más en Allá tú, el Rastro y la Torre

### 7.27.0 · 2026-10-18

- Logros: 115 nuevos. Partidas en el casino y juegos distintos, fechas en cada juego (madrugada, viernes 13, Día de Canarias, Nochevieja) y en el casino entero, escalones más altos en chat, voz, música, memes, lista y juegos, y secretos con cifras con guasa. Seis logros que dependen de otros suben de rareza

### 7.26.1 · 2026-10-18

- Logros: los de Coleccionista por número de logros y por puntos bajan de rareza; con más de 4.000 logros en el catálogo, 400 ya no es mítico

### 7.26.0 · 2026-10-18

- Logros: 57 de Coleccionista que faltaban (hasta 4.000 logros, más escalones por rareza, secretos, puntos, premios y categorías, y uno nuevo por logros comunes)

### 7.25.31 · 2026-10-18

- Interno: las pruebas vigilan las reglas de tono (frases normales, lo boricua y la muleta «como»)

### 7.25.30 · 2026-10-18

- Interno: el informe de la auditoría de tono, al día, con las cifras de antes y después

### 7.25.29 · 2026-10-18

- Logros: 51 nombres que repetían el chiste de otra categoría, cambiados, y las historias sin «mi amor»

### 7.25.28 · 2026-10-18

- Interno: repaso de errores y avisos (casi todos ya iban en claro) y la auditoría deja de confundir «administrador» con «ministro»

### 7.25.27 · 2026-10-18

- Comunidad: bienvenidas, despedidas, cumpleaños y subidas de nivel con menos política y sin lo boricua; los errores, en claro

### 7.25.26 · 2026-10-18

- Tienda, Rastro y mascotas: frases nuevas, menos política y sin lo boricua genérico

### 7.25.25 · 2026-10-18

- Hacienda: las líneas de impuestos de cada cobro dicen la cifra con el 🐶, sin la frase de Perro Sanxe

### 7.25.24 · 2026-10-18

- Lotería, Porras y Botes: fuera los últimos «¡Ay, bendito!» y «mi amor» del casino

### 7.25.23 · 2026-10-18

- Botes y Caballos: frases nuevas, más normales y con Jovani

### 7.25.22 · 2026-10-18

- Dados y Pachinko: frases nuevas, más normales y con Jovani

### 7.25.21 · 2026-10-18

- Póker, Timba y Bacará: frases nuevas, más normales y con Jovani

### 7.25.20 · 2026-10-18

- 4 en raya y Backgammon: Jovani habla como Jovani y deja de copiar los chistes de un juego a otro

### 7.25.19 · 2026-10-18

- Gallos: frases nuevas para peleas, entrenos y funerales, con mucha menos política

### 7.25.18 · 2026-10-18

- Allá tú: frases nuevas; Perro Sanxe sigue de presentador, con menos política alrededor

### 7.25.17 · 2026-10-18

- Guerra, Cara o cruz y Autobús: frases nuevas, más normales y con Jovani

### 7.25.16 · 2026-10-18

- Pollo, Minas y Crash: frases nuevas, más normales y con Jovani

### 7.25.15 · 2026-10-18

- Torre: frases nuevas, más normales y con Jovani; menos Juego de Tronos y política

### 7.25.14 · 2026-10-18

- Interno: la auditoría de frases mide temas, frases normales y lo boricua por partes

### 7.25.13 · 2026-10-18

- Logros: fuera el «¡Wepa!» y el «¡Ay, bendito!», y más frases de Jovani en los nombres

### 7.25.12 · 2026-10-18

- Ruleta, blackjack y tragaperras: fuera el «¡Wepa!» y el «mi amor»; solo frases del cantante Jovani

### 7.25.11 · 2026-10-18

- Ruleta, blackjack y tragaperras: frases nuevas, más normales, más Jovani y de temas variados

### 7.25.10 · 2026-10-18

- Logros: 24 nombres pasan a humor de adultos donde el logro lo pedía

### 7.25.9 · 2026-10-18

- Logros: fuera las coletillas entre paréntesis y los «X, versión Y» en los nombres

### 7.25.8 · 2026-10-18

- Logros: fuera los nombres «X, como [referencia]»: 152 pasan a juegos de palabras

### 7.25.7 · 2026-10-18

- Logros: segunda pasada a todos los nombres: menos repetidos, más variados y ninguno soso

### 7.25.6 · 2026-10-18

- Interno: la política vuelve a ser una fuente de bromas como las demás, al mismo nivel

### 7.25.5 · 2026-10-18

- Logros: descripciones neutras que dicen qué hay que hacer, sin broma detrás
- Logros: nombres con bromas de cine, series, deporte y cultura friki en vez de tanta política

### 7.25.4 · 2026-10-18

- Logros: el aviso pone la retención de Hacienda en la misma línea que el premio

### 7.25.3 · 2026-10-18

- Interno: la auditoría de tono sin prefijos obligatorios en commits ni changelog

### 7.25.2 · 2026-10-18

- Interno: la nueva norma de tono se puede revertir entera o por partes

### 7.25.1 · 2026-10-18

- Interno: nueva norma de tono: menos bromas, más espaciadas y de temas variados, y menos política

### 7.25.0 · 2026-10-18

- Admin: las listas de `admin` incluyen a todos los miembros, en páginas con ◀ y ▶
- Admin: el panel `admin` usa textos serios, sin bromas

### 7.24.0 · 2026-10-18

- Admin: `admin`, panel de moderación con la actividad de cada miembro, sanciones, historial y notas

### 7.23.0 · 2026-10-17

- Blackjack: contar cartas da para ganar si te lo curras: el jefe de sala deja subir de 1 a 8
- Blackjack: el zapato es de cada jugador y sigue al abrir otra mesa (una mesa por jugador)
- Blackjack: la carta de corte va a una baraja del final: se reparten 5 de las 6
- Blackjack: logros por contar cartas: la ficha a tope y, secretos, los desvíos de la cuenta

### 7.22.0 · 2026-10-17

- Pisos: nueve inmuebles por barrio (180), cada barrio con su carácter y 10 tipos nuevos
- Pisos: el índice del barrio sigue a lo que compran los miembros y los nómadas buscan playa
- Patrimonio: el límite con el IRPF rebaja como mucho la mitad

### 7.21.0 · 2026-10-16

- Blackjack: mesa más grande y legible, y cada jugada acaba en su imagen fija

### 7.20.0 · 2026-10-16

- Botes: el Volcán, la Mina y el Olimpo con suspense, rescates y recuento del bonus
- Tragaperras: carteles, luces y marcador comunes a todas las máquinas (interno)

### 7.19.0 · 2026-10-16

- Tragaperras: máquina más bonita, tirada con suspense y doble o nada con carta

### 7.18.0 · 2026-10-16

- Blackjack: mesa animada con tapetes, zapato para contar y rachas

### 7.17.0 · 2026-10-16

- Allá tú: `allatu`, 22 cajas, un gordo sorpresa y la banca al teléfono

### 7.16.0 · 2026-10-16

- Pisos: obra nueva de la rama Ladrillo, aval de los padres, fianzas y frase del BCE
- Patrimonio: cuentan los objetos de la tienda, límite con el IRPF y Grandes Fortunas

### 7.15.0 · 2026-10-16

- `.met`: atajo de texto de `reinicio` (sin versión `/`)

### 7.14.0 · 2026-10-16

- Informe de uso: llamadas a Discord por ruta, tareas programadas, tiempo de arranque, memoria de Chromium y Node, disco leído y escrito, tareas vivas, primeros pasos de los miembros nuevos, reacciones a los mensajes del bot, y por día los impuestos, el Gini y el saldo del Estado
- El informe del canal llega con un resumen de lo que hay que vigilar del día anterior

### 7.13.0 · 2026-10-16

- Informe de uso: tiempo de cada consulta a la base de datos por repositorio, esperas en candados, procesos de Node y dibujos con plan B, peso de las imágenes, interacciones perdidas (10062), partidas y paneles abiertos, cerrados o abandonados, opciones de los menús y páginas de la ayuda, comandos mal escritos o rechazados, errores por tipo y función, música, minutos sin datos (bot caído), logros por rareza, ventas de la tienda, mascotas y economía por día

### 7.12.0 · 2026-10-16

- Informe de uso: mide la CPU del contenedor, lo que ocupa la base de datos y el disco libre, la espera del clic a la imagen, las vistas que caducan a medias, cada arranque con su versión, los cortes con Discord, sus respuestas 429 y los procesos matados por memoria
- `docs/analisis_metricas.py` junta varios informes en una visión global y compara versiones del bot; `docs/descargar_informe.py` baja todos los del canal

### 7.11.0 · 2026-10-16

- `informe`: con `canal`, el informe de uso se publica cada día en ese canal privado; con `quitar`, deja de publicarse

### 7.10.1 · 2026-10-16

- Torre del dragón: al acabar cada jugada ya no se repite un trozo del GIF anterior con fotogramas mezclados (el mensaje pasa a embed, como los demás juegos)

### 7.10.0 · 2026-10-16

- Torre del dragón: subida entre pisos más larga por una escalera de caracol, el dragón sale del hueco sin saltos y al acabar la cámara se aleja hasta la torre entera

### 7.9.2 · 2026-10-16

- Torre del dragón: al subir de piso, la cámara entra por la puerta del huevo en vez de enseñar dos pisos a la vez

### 7.9.1 · 2026-10-16

- Torre del dragón: al subir de piso ya no se mezclan los dos pisos, con un forjado de piedra entre ellos

### 7.9.0 · 2026-10-15

- Torre del dragón: dibujo rehecho con sombreado, luces y un dragón nuevo

### 7.8.0 · 2026-10-15

- Torre del dragón: `torre` animada, con un tema por nivel y todo pintado antes del clic

### 7.7.1 · 2026-10-15

- Logros de colección: las cajas y las skins ya no cuentan como coleccionables

### 7.7.0 · 2026-10-15

- Pisos: rediseño ilustrado, con las islas de verdad y mucha más guasa

### 7.6.0 · 2026-10-15

- Guerra: mesa animada con tapete propio, pintada antes del clic

### 7.5.3 · 2026-10-15

- Arreglos: impuestos de pisos en bases antiguas y cobros que se quedaban colgados

### 7.5.2 · 2026-10-15

- Tragaperras: rótulos y fondos de la Mina y el Olimpo a la altura del Volcán

### 7.5.1 · 2026-10-15

- Tragaperras: la Mina y el Olimpo, con el mismo arte que el Volcán

### 7.5.0 · 2026-10-15

- Tienda: cajas por niveles, de temporada y ganadas jugando, con colecciones

### 7.4.0 · 2026-10-15

- Dados: mesa compartida del canal, con tirador y tiradas para todos

### 7.3.0 · 2026-10-15

- Tragaperras: la Mina y el Olimpo, dos máquinas nuevas en `tragas`

### 7.2.0 · 2026-10-15

- Tienda: tapete del 20-N, la tele de 1975 con la caricatura de Franco

### 7.1.1 · 2026-10-15

- Tragaperras: el bote crece el doble de rápido, con el 6 % de cada tirada

### 7.1.0 · 2026-10-15

- Gallos: stats, entrenamiento y muertes en el reñidero

### 7.0.0 · 2026-10-15

- Tragaperras: `tragas` junta todas las máquinas, la clásica y el Volcán
- Rompe: el comando `volcan` desaparece; el Volcán se abre con `tragas volcan` (o la opción máquina de `/tragas`)

## 6.x

### 6.17.0 · 2026-10-15

- Guerra: `guerra`, carta contra carta y a la guerra si empatas

### 6.16.0 · 2026-10-15

- ONGs: once logros nuevos, más frases y la mascota comenta el donativo

### 6.15.1 · 2026-10-15

- Babel: `babel` ya no se corta a mitad cuando Google pide un respiro

### 6.15.0 · 2026-10-15

- Rastro: `trueque`, cambia un objeto por otro con otro miembro

### 6.14.2 · 2026-10-15

- Tragaperras y todo el bot: ya no van más lentos cuanto más se juega

### 6.14.1 · 2026-10-15

- Interno: auditoría de las loterías con el código real

### 6.14.0 · 2026-10-15

- Logros: todo lo del trabajo, en una sola entrada 🪏 Trabajo del menú

### 6.13.0 · 2026-10-15

- Mascotas: atajos a adoptar y a la comida desde el panel

### 6.12.0 · 2026-10-15

- Blackjack: el zapato dura entre manos y se pueden contar cartas

### 6.11.0 · 2026-10-15

- Torre del dragón: `torre`, sube pisos esquivando dragones

### 6.10.0 · 2026-10-15

- Tienda: cada artículo dice si ya lo tienes y cuántos

### 6.9.0 · 2026-10-15

- Aniversario: el 7 de octubre, fiesta del servidor con XP doble

### 6.8.0 · 2026-10-15

- Caballos: el bote del Gran Premio crece con el 10 % de lo que se pierde en cada carrera, hasta 50.000 Y$

### 6.7.5 · 2026-10-15

- Interno: la prueba de caducidad de la música ya no falla al azar

### 6.7.4 · 2026-10-15

- Jovani: frases repasadas, sin bromas copiadas entre juegos y con más Bibi y groypers

### 6.7.3 · 2026-10-15

- Tienda: el tapete de los archivos Epstein pasa a 3.000.000 Y$, con tres copias numeradas
- Tienda: los cambios de precio llegan también a las tiendas que ya tenían el artículo (el de Israel ya cuesta 271.000 Y$)

### 6.7.2 · 2026-10-15

- Pala: los marrones salen el doble, uno de cada tres turnos

### 6.7.1 · 2026-10-15

- Mascotas: el panel ya no deja de responder tras pedir un respiro

### 6.7.0 · 2026-10-15

- Pisos: hipoteca, VPO por sorteo, alquilar a miembros con Bono Alquiler Joven, vacacional, fondo buitre y SOCIMI

### 6.6.0 · 2026-10-15

- Pisos: `pisos`, el mercado inmobiliario de Canarias, y el Patrimonio cuenta el ladrillo

### 6.5.0 · 2026-10-14

- Tienda: nueve tapetes nuevos (PSOE, Falcon, NO-DO, Modelo 100, Prove Me Wrong, archivos Epstein, Corea del Norte, diamantes y el Gordo) y el de Israel baja a 271.000 Y$

### 6.4.1 · 2026-10-14

- Timba: al empezar la mano ya no se quedan los botones de entre manos y nadie se retira sin poder jugar

### 6.4.0 · 2026-10-14
- Rastro: `vender` y `rastro`, segunda mano entre miembros con subastas e ITP

### 6.3.1 · 2026-10-14

- Botones: se acabó «Esta interacción ha fallado» al pulsar rápido o en botones viejos, y contestan antes tras un rato sin uso
- Pollo: dos clics seguidos en 🔁 Jugar ya no cobran dos partidas

### 6.3.0 · 2026-10-14

- Backgammon: `backgammon`, contra Jovani o en duelo con cubo de doblar

### 6.2.5 · 2026-10-14

- Interno: receta para que los juegos nuevos pinten igual de rápido

### 6.2.4 · 2026-10-14

- Juegos: GIF más rápidos con los 4 pintores a la vez y Python 3.13

### 6.2.3 · 2026-10-14

- Interno: los pintores de Node ya no pasan de 4 aunque se pidan varios a la vez

### 6.2.2 · 2026-10-14

- Juegos: los GIF se dibujan y se montan bastante más rápido (los gallos y el bacará, en menos de la mitad de tiempo) y el primero tras un rato ya no arranca en frío
- Interno: el informe cuenta lo que tarda cada imagen o GIF en subir a Discord

### 6.2.1 · 2026-10-14

- Interno: las pruebas pasan también en Windows (textos en UTF-8, la hora del meme del tuit y las piezas del pachinko)

### 6.2.0 · 2026-10-14

- 4 en raya: `4enraya`, contra Jovani con retos del día o en duelo con apuesta y grada

### 6.1.2 · 2026-10-14

- Timba: la mesa de cada botón de quien habla (pasar o igualar, retirarse, cada subida y todo) se pinta mientras corre su turno, y la primera de cada mano durante la espera entre manos

### 6.1.1 · 2026-10-14

- Timba: mientras corre el turno se pinta ya la mesa de quien se duerme, y sale al momento al acabarse el tiempo

### 6.1.0 · 2026-10-14

- Timba: botón ✅ Listo entre manos (si lo pulsan todos, empieza ya) y dos minutos de espera si no
- Timba: el turno se agota cuando marca el reloj de la mesa, no unos segundos después

### 6.0.1 · 2026-10-14

- Interno: reglas para que Claude navegue el código con el grafo de graphify

### 6.0.0 · 2026-10-14

- Póker: mesa del canal para seis, 💎 Bote progresivo y el club (rango, retos y racha)
- Póker: `poker` con dos modos, contra la banca y la timba entre miembros (Texas Hold'em sin límite)
- Interno: póker, comprobación rápida de la ventaja de la casa
- Rompe: `poker` pide el modo (o lo pregunta con botones) y ya no abre una mesa propia: contra la banca es la mesa del canal y se juega sentándose con 🪑; el ante máximo depende del rango del club (10.000 Y$ al empezar).

## 5.x

### 5.25.0 · 2026-10-14

- Póker: `poker`, Casino Hold'em contra la banca

### 5.24.0 · 2026-10-13

- Pala: los ocho oficios llegan a 10 puestos con dos ramas cada uno

### 5.23.0 · 2026-10-13

- Ayuda: `ayuda` va por menús, con lo que hace cada comando

### 5.22.1 · 2026-10-13

- Novedades: se anuncian los commits directos a main que se quedaron sin aviso desde el #99

### 5.22.0 · 2026-10-13

- Versiones: el bot tiene número de versión y el aviso de novedades lo dice

### 5.21.1 · 2026-10-13

- Novedades: el changelog cuenta todo lo que entra en main, no solo los PR (5322531)

### 5.21.0 · 2026-10-13

- Tienda: tapete propio con tu imagen y `pieles` para manejar las skins (7fb2285)

### 5.20.1 · 2026-10-13

- Pachinko: la Ráfaga ×5 se anima con las 50 bolas a la vez (c2db2a8)

### 5.20.0 · 2026-10-13

- Casino: botón 🎨 del tapete en todas las mesas, también en la ruleta (#99)

### 5.19.0 · 2026-10-13

- Casino: tapetes de oro y de bandera, uno por mesa y se compran en la mesa (#98)

### 5.18.2 · 2026-10-13

- Pala: comprar y dar regalos a la familia sin salir del panel (#97)

### 5.18.1 · 2026-10-13

- Pala: los regalos para la familia se compran desde el desplegable (#96)

### 5.18.0 · 2026-10-13

- Pala: la sanidad llega a 10 puestos con ramas pública y privada (#95)

### 5.17.0 · 2026-10-12

- Pala: la obra llega a 10 puestos y la política estrena bromas (#94)

### 5.16.0 · 2026-10-12

- Jovani con más frases, coletillas y logros con sátira en todo el bot (#93)

### 5.15.0 · 2026-10-12

- Gallos: `gallos`, peleas contra la casa y duelos con apuestas (#92)

### 5.14.1 · 2026-10-12

- Novedades: salen todos los cambios, enteros y sin recortar (#91)

### 5.14.0 · 2026-10-12

- Pala: la familia frena el grind, con regalos y favores (#90)

### 5.13.1 · 2026-10-12

- Música: el reproductor ya no se queda colgado (#89)

### 5.13.0 · 2026-10-12

- Lista: páginas de 25, `/lista` arreglado y prioridad con botones (#88)

### 5.12.0 · 2026-10-12

- Pala: carrera larga con ramas y jubilación; la política llega a 10 (#87)

### 5.11.1 · 2026-10-12

- Interno: la prueba de la porra completa ya no falla a ratos (#86)

### 5.11.0 · 2026-10-12

- Pala: tres oficios nuevos (reparto, funcionariado e influencer) (#85)

### 5.10.0 · 2026-10-12

- Renta: el IRPF se reinicia cada lunes y devuelve lo cobrado de más (#84)

### 5.9.0 · 2026-10-12

- Skins: cambia el tapete de la ruleta, y pronto más mesas (#83)

### 5.8.0 · 2026-10-12

- Tienda: títulos para lucir junto al nombre y caprichos caros (#82)

### 5.7.0 · 2026-10-11

- Bacará: `bacara`, Punto y Banca con zapato compartido y apretón (#81)

### 5.6.1 · 2026-10-11

- Pala: el minijuego ya no cuenta como fallo y fichar contesta antes (#80)

### 5.6.0 · 2026-10-11

- IMV por día natural y `pala` dice cuándo vuelves a fichar (#79)

### 5.5.0 · 2026-10-11

- Blackjack: seguro también con blackjack (dinero par) y tres logros (#78)

### 5.4.0 · 2026-10-11

- Interno: el bot cuenta cuánto se usa cada cosa y `informe` lo descarga (#77)

### 5.3.0 · 2026-10-11

- Pollo: nueva mesa al estilo del casino y el paso sale al momento (#76)

### 5.2.4 · 2026-10-10

- Autobús y caballos: más rápidos y sin navegador (#75)

### 5.2.3 · 2026-10-10

- Dados y ruleta: más rápidos y sin navegador (#74)

### 5.2.2 · 2026-10-10

- Interno: receta para acelerar los juegos como la moneda (#73)

### 5.2.1 · 2026-10-10

- Cara o cruz: la moneda sale nada más pulsar (#72)

### 5.2.0 · 2026-10-10

- Novedades: el aviso se puede publicar en un hilo con `cambios` (#71)

### 5.1.0 · 2026-10-10

- Novedades: aviso detallado como en GitHub y comando `cambios` (#70)

### 5.0.2 · 2026-10-10

- Cara o cruz: el botón responde al momento y no chiva el resultado (#69)

### 5.0.1 · 2026-10-10

- Cara o cruz: la moneda se dibuja sin navegador (#68)

### 5.0.0 · 2026-10-10

- Autobús: el comando pasa a ser guagua (#67)
- Rompe: `autobus` pasa a llamarse `guagua`.


## 4.x

### 4.6.0 · 2026-10-10

- Blackjack: seguro y sin tope (#66)

### 4.5.0 · 2026-10-10

- Autobús: `autobus`, el Ride the Bus con las probabilidades a la vista (#65)

### 4.4.2 · 2026-10-10

- Optimizar la Rule (#64)

### 4.4.1 · 2026-10-10

- Arreglo Rule (#63)

### 4.4.0 · 2026-10-10

- Ruleta: rayos de ×500, rueda nueva y números calientes y fríos (#62)

### 4.3.0 · 2026-10-10

- Dados: `dados`, craps con Pase, No pase y Odds sin comisión (#61)

### 4.2.0 · 2026-10-10

- Cara o cruz: `moneda`, doble o nada con monedas que caen de canto (#60)

### 4.1.1 · 2026-10-10

- Tragaperras: los giros gratis dentro de giros gratis se suman (#59)

### 4.1.0 · 2026-10-10

- Caballos: parrilla de 2 minutos y botón ✅ Listo para salir antes (#58)

### 4.0.0 · 2026-10-09

- Perfil: un solo menú, lotería a medida del servidor y blackjack con empate (#57)
- Rompe: `nivel`, `patrimonio`, `logros` y `mochila` desaparecen; ahora es `perfil`.


## 3.x

### 3.19.1 · 2026-10-09

- Interno: los agentes de IA solo usan Haiku y Sonnet, de uno en uno (#56)

### 3.19.0 · 2026-10-09

- Tragaperras: re-giro, doble o nada, giro del día y bote con tope (#55)

### 3.18.0 · 2026-10-09

- Pachinko: autoplay y la siguiente tanda lista al pulsar (#54)

### 3.17.0 · 2026-10-09

- Pachinko: las bolas chocan entre sí; Tragaperras: autoplay animado (#53)

### 3.16.0 · 2026-10-09

- Pachinko: máquina con neón, bolas metálicas y bolsillos con relieve (#52)

### 3.15.0 · 2026-10-09

- Pachinko: las bolas caen con física real (#51)

### 3.14.2 · 2026-10-09

- Casino: las animaciones de los juegos se preparan mucho más rápido (#50)

### 3.14.1 · 2026-10-08

- Botones: responden al momento y ya no fallan (#49)

### 3.14.0 · 2026-10-08

- Beernight: `beernight`, mandamientos, eventos, chivatazos e histórico (#48)

### 3.13.0 · 2026-10-08

- Novedades: el bot publica en Discord los PR de cada despliegue (#47)

### 3.12.0 · 2026-10-08

- Caballos (#46)

### 3.11.0 · 2026-10-07

- Claude/adoring tesla ybmnco (#45)

### 3.10.0 · 2026-10-07

- Porras: apuestas entre miembros sobre las jugadas de otro (#44)

### 3.9.0 · 2026-10-07

- Pala: clics que no se pierden, minijuegos más claros y herramientas (#43)

### 3.8.0 · 2026-10-06

- Patrimonio hacienda (#42)

### 3.7.0 · 2026-10-06

- Estadísticas del casino: comando apuestas con siete páginas (#41)

### 3.6.0 · 2026-10-06

- Comando reinicio: actualizar y reiniciar el bot desde Discord (#40)

### 3.5.0 · 2026-10-06

- Tienda: surtido de serie con 155 artículos, pasillos y objetos que se usan (#39)

### 3.4.1 · 2026-10-06

- Actualizar sin git (#38)

### 3.4.0 · 2026-10-06

- Logros compactos (#37)

### 3.3.0 · 2026-10-06

- Autoactualización del bot en el NAS (actualizar.sh) (#36)

### 3.2.0 · 2026-10-06

- Logros risas (#35)

### 3.1.0 · 2026-10-06

- Pollo (Chicken Road) con GIF de tensión y autocobro; casino unificado (#34)

### 3.0.0 · 2026-10-06

- Limpieza: fuera la hora feliz, lotería en positivo y /hongkong (#33)
- Rompe: se quita la hora feliz (XP ×2) y sus logros.


## 2.x

### 2.5.0 · 2026-10-06

- Intereses: cuenta remunerada diaria por tramos, con liquidación (#32)

### 2.4.1 · 2026-10-06

- pala: sueldos calibrados frente al IMV (escala de nóminas a 100 Y$/€) (#31)

### 2.4.0 · 2026-10-06

- Pala (#30)

### 2.3.0 · 2026-10-06

- Trabajo: `pala`, tres oficios con minijuegos, nóminas y ascensos (#29)

### 2.2.0 · 2026-10-05

- Botes: el Volcán, tragaperras de monedas, recogida y bonus (`volcan`) (#28)

### 2.1.0 · 2026-10-05

- Lista: tareas compartidas del servidor ordenadas por prioridad (#27)

### 2.0.0 · 2026-10-05

- Nombres de comandos en español y norma en la Biblia (#26)
- Rompe: 27 comandos cambian de nombre al español (`play`→`poner`, `help`→`ayuda`…).


## 1.x

### 1.19.0 · 2026-10-05

- Bizum entre miembros, exento de Donaciones, con sus logros (#25)

### 1.18.1 · 2026-10-05

- Purge: borra los últimos mensajes, no los de hace casi 14 días (#24)

### 1.18.0 · 2026-10-05

- Loterías del Estado: Nacional, Primitiva, Bonoloto, Gordo y Euromillones (#23)

### 1.17.2 · 2026-10-05

- Minas: de 1 a 12 minas, sin tope y el menú enseña el premio gordo (#22)

### 1.17.1 · 2026-10-05

- Logros cogs puente (#21)

### 1.17.0 · 2026-10-05

- Tienda: escaparate con botones, caja con IGIC, mochila y trastienda (#20)

### 1.16.0 · 2026-10-05

- Pachinko: cuatro tableros a elegir, del más tranquilo al más loco (#19)

### 1.15.0 · 2026-10-04

- Pachinko: tablero de clavos, reach, atari y rush con botones (#18)

### 1.14.0 · 2026-10-04

- Comando niveles para importar el historial y encender los niveles (#17)

### 1.13.1 · 2026-10-04

- Crash minas tension (#16)

### 1.13.0 · 2026-10-04

- Crash y Minas: cohete compartido por canal y tablero de 5×5 (#15)

### 1.12.0 · 2026-10-04

- Tragaperras (#14)

### 1.11.0 · 2026-10-04

- Bienvenidas (#13)

### 1.10.0 · 2026-10-04

- Logros (#12)

### 1.9.0 · 2026-10-04

- Cumpleaños: cumple, cumples, anuncio con regalo y felicitaciones pagadas (#11)

### 1.8.0 · 2026-10-04

- Niveles con voz, IMV, IRPF a la española, Hacienda y Campaña de la Renta (#10)

### 1.7.0 · 2026-10-03

- Blackjack con botones (/blackjack, .blackjack, .bj) (#9)

### 1.6.0 · 2026-10-03

- Ruleta: varias apuestas en la misma tirada (#8)

### 1.5.0 · 2026-10-03

- Comando babel: frases, apodos y canales por 99 idiomas (#7)

### 1.4.0 · 2026-10-03

- Ayuda unificada en un embed y comandos de administración (#6)

### 1.3.0 · 2026-10-03

- Economía de yapdollars y ruleta americana con botones (#5)

### 1.2.1 · 2026-10-03

- Listar los efectos de imagen en .help (#4)

### 1.2.0 · 2026-10-03

- Efectos de imagen de Dank Memer como comandos de texto (#3)

### 1.1.0 · 2026-10-03

- Sonidos de entrada al canal de voz y voz ensordecida (#2)

### 1.0.0 · 2026-10-03

- Unificar el prefijo de texto en "." y alinear tests y docs (#1)
- Primera versión estable: el prefijo `.` queda fijo.


## 0.x: desarrollo inicial

### 0.7.0 · 2026-10-01

- Cambios en los prefijos del bot (eb5010c)

### 0.6.0 · 2026-10-01

- Despliegue con Docker en el NAS (db11421)

### 0.5.0 · 2026-10-01

- Un solo nombre por comando, `magik` y ayuda más simple (5b2b4eb)

### 0.4.0 · 2026-10-01

- Todos los comandos con `/` también como texto, y ayuda (ec66e98)

### 0.3.0 · 2026-09-29

- Bienvenida y despedida, reproductor de música y comandos con prefijo º (3f1e9aa)

### 0.2.0 · 2026-09-28

- Estadísticas y niveles (f577287)

### 0.1.0 · 2026-09-28

- Estructura inicial del bot y comando `ping` (2343cd6)
