# Bot Jovani Vázquez

Bot de Discord escrito en Python, con arquitectura modular basada en
`discord.py`. Las normas de estructura, límites y documentación del
proyecto están definidas en [Biblia.txt](./Biblia.txt); léelo antes de
añadir funcionalidades nuevas. El alcance funcional previsto se describe
en [FUNCIONALIDADES.md](./FUNCIONALIDADES.md).

## Requisitos

- Python 3.12 o superior.
- Una aplicación y un token de bot creados en el
  [portal de desarrolladores de Discord](https://discord.com/developers/applications).
- `ffmpeg` instalado en el sistema y accesible en el `PATH`, necesario para
  reproducir audio en canales de voz (comandos de música). No se instala
  con `pip`; en Debian/Ubuntu basta con `sudo apt install ffmpeg`.
- Las dependencias de Python `PyNaCl` y `davey` (cifrado de voz) se
  instalan automáticamente con `pip install -e .`; si el bot lanza
  `RuntimeError: davey library needed in order to use voice` al intentar
  reproducir música, reinstala las dependencias (`pip install -e ".[dev]"`).

## Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

> Si tu proyecto vive en un sistema de archivos montado con la opción
> `noexec` (por ejemplo, algunas particiones NTFS montadas manualmente),
> la creación del entorno virtual y la ejecución de dependencias
> compiladas fallarán. En ese caso, crea el entorno virtual fuera de esa
> ruta, por ejemplo en `~/.venvs/bot-jovani-vazquez`.

## Configuración

1. Copia `.env.example` a `.env`.
2. Completa `DISCORD_TOKEN` con el token de tu bot. Nunca lo subas al
   repositorio.
3. Ajusta `LOG_LEVEL` y `COMMAND_PREFIX` si lo necesitas (ambos son
   opcionales). `COMMAND_PREFIX` es el prefijo de los comandos de texto
   (por defecto `.`); con él se invocan los mismos comandos que con `/`.
4. `CASINO_CHANNEL_IDS` (opcional) limita los juegos del casino (ruleta,
   blackjack, tragaperras, pachinko, lotería y porras) a esos canales: IDs
   separados por comas. En nuestro servidor, `#casino` es
   `1384280704539562054`. Vacío = se puede jugar en cualquier canal.

El bot carga automáticamente el archivo `.env` (si existe) al arrancar,
mediante `python-dotenv`. En producción no es necesario un archivo
`.env`: basta con exportar las variables en el entorno de despliegue.

## Ejecución

```bash
python -m bot
```

## Funcionalidad actual

- Cada comando tiene **un único nombre corto, igual con `/` y con `.`**
  (`/poner despacito` = `.poner despacito`), salvo los de imagen, que solo
  existen con `.` para dejar los slash commands al resto del bot. Escribe
  `/ayuda` o `.ayuda` para ver la lista:

  | Categoría | Comandos |
  |---|---|
  | ⚙️ General | `ayuda` · `latencia` |
  | 👤 Perfil | `perfil [miembro]` |
  | 🎵 Música | `cola` · `parar` · `pausar` · `poner <consulta>` · `quitar <posicion>` · `saltar` · `seguir` · `vaciar` · `volumen <1-200>` |
  | 📊 Niveles | `ranking [pagina]` |
  | 🎂 Cumpleaños | `cumple [dd/mm] [miembro]` · `cumples` |
  | 📝 Lista | `lista [tarea] [prioridad alta\|media\|baja]` |
  | 🍻 Beernight | `beernight [sonido] [archivo]` |
  | 🛍️ Tienda | `mascota [miembro]` · `tienda` |
  | 🪏 Trabajo | `pala` |
  | 🎰 Casino | `apuestas [miembro]` · `bizum <miembro> <cantidad> [concepto]` · `donar [ong] [cantidad]` · `fortunas` · `imv` · `hacienda [miembro]` · `renta` · `ruleta [cantidad] [apuesta]` · `blackjack [cantidad]` (atajo `.bj`) · `cohete [cantidad] [auto]` · `minas [cantidad] [minas]` · `pollo [cantidad] [dificultad] [autocobro]` · `moneda [cantidad] [lado]` · `guagua [cantidad]` · `dados [cantidad] [apuesta]` · `pachinko [cantidad]` · `caballo [cantidad] [caballos] [tipo]` · `porra [miembro] [juego] [propuesta] [jugadas] [apuesta]` · `loteria` · `saldo [miembro]` · `tragas [cantidad]` · `volcan [cantidad]` |
  | 🔔 Entradas | `entrada [archivo] [volumen] [borrar]` |
  | 🗼 Diversión | `babel <texto \| @miembros #canales>` · `hongkong` |
  | 🎨 Imagen (solo `.`) | `magik [miembro]` · `memes [efecto]` · 108 efectos (`.memes`) |
  | 🛡️ Admin | `abrir` · `apodo` · `banear` · `bienv` · `borrar` · `callar` · `cambios` · `catalogo` · `cerrar` · `decir` · `echar` · `hablar` · `indultar` · `lento` · `niveles` · `rol` · `tajo` |

  La ayuda cabe en un solo embed: categorías con los nombres en orden
  alfabético, sin descripciones. La categoría Admin solo la ve quien es
  administrador.
- Los **comandos de administración** solo los pueden usar miembros con el
  permiso Administrador (se comprueba en cada uso, no solo ocultándolos del
  menú). Respetan la jerarquía de roles, dejan el motivo y el autor en el
  registro de auditoría y avisan si al bot le falta un permiso. Detalle de
  cada uno en [FUNCIONALIDADES.md](./FUNCIONALIDADES.md#6-quater-administración).

- `magik` deforma una imagen con *seam carving* (reescalado consciente del
  contenido, el mismo efecto que Dank Memer): en vez de estirar o recortar,
  elimina primero los caminos de píxeles menos importantes y vuelve a
  ampliar, así que las formas se "derriten". Usa, por orden: la imagen
  adjunta (también la del mensaje al que respondes), el avatar del miembro
  indicado o el tuyo. Ejemplos: `.magik` con una foto adjunta,
  `.magik @alguien`, o responde a una foto con `.magik`. Solo acepta
  adjuntos de Discord y avatares (nunca enlaces externos), hasta 8 MB, con
  5 segundos de espera entre usos por usuario. Respeta la rotación de las
  fotos de móvil. El bot necesita el permiso **Adjuntar archivos** en el
  canal; si falta, `magik` lo explica en lugar de quedarse colgado.
- Los **108 efectos de imagen de Dank Memer** (`trigger`, `slap`, `wanted`,
  `changemymind`, `brain`, `tweet`, `crab`...) funcionan como comandos de
  texto con su nombre original. `.ayuda` y `.memes` los listan por tipo y
  `.memes <efecto>` explica uno. Reglas comunes:
  - `@alguien` (o responder a su mensaje) usa su avatar; una imagen adjunta
    lo sustituye. En los de dos personas (`slap`, `spank`, `bed`...) tú eres
    la primera y el mencionado la segunda.
  - Los textos múltiples se separan con `|`: `.brain agua | zumo | café | café a las 3`.
    En los efectos de solo texto, `@alguien` se escribe con su nombre.
  - Comparten con `magik` el límite de 8 MB, los 5 s de espera por usuario
    y un máximo de 2 trabajos a la vez en todo el bot.
  - `crab`, `letmein` y `scaryabove` devuelven MP4 (los genera `ffmpeg`,
    ya incluido en la imagen Docker); `trigger`, `dank`, `salty`, `airpods`,
    `america`, `communism` y `kowalski` devuelven GIF.
  - Diferencias con Dank Memer: los emojis del texto no se dibujan (los
    personalizados salen como `:nombre:`), `dream` es una imitación ligera
    sin TensorFlow, y `radialblur` y `warp` están reimplementados con numpy.
    No se incluyen `profile` (la ficha de la economía de Dank Memer) ni
    `yomomma` (solo devuelve un chiste de texto).
  - Las plantillas (~27 MB) están en `src/bot/assets/memes`, con la licencia
    MIT de [imgen](https://github.com/DankMemer/imgen).
- **Economía (yapdollars):** una sola moneda, ficticia y no comprable,
  para todo el bot. Cada miembro empieza con 1.000 Y$ por servidor y
  `imv` (Ingreso Mínimo Vital, antes `daily`, exento de IRPF) paga 500 Y$
  más 100 por cada día seguido (tope 1.500 Y$; se cobra cada 20 h y la racha se
  pierde tras 48 h). Lo cobrado trabajando con `pala` lo reduce (ver Trabajo).
- **Cumpleaños:** `cumple 14/02` guarda el tuyo (solo día y mes; una vez
  puesto, solo un administrador lo cambia) y `cumples` lista los próximos.
  Ese día (hora canaria) el bot lo anuncia en `#chat-general` y el
  cumpleañero recibe 3.000 Y$. Felicitarle, con el botón 🎉 o con un mensaje
  que le mencione o le responda y suene a felicitación, da 500 Y$ a quien
  felicita y 100 Y$ más al cumpleañero, una vez por persona. Si existe un rol
  llamado `🎂 Cumpleañero`, el bot se lo pone durante el día (necesita
  **Gestionar roles**). Los regalos pagan IRPF con retención (ganancia
  patrimonial, art. 33.1 LIRPF), aunque solo muerde a quien ya pasa del mínimo.
- **IRPF y Hacienda:** los premios por subir de nivel y la ganancia neta
  diaria del casino tributan; el IMV está exento, como el real (art. 7.y
  LIRPF). En el casino, las pérdidas del día compensan las ganancias del
  mismo día: si pierdes después de ganar, Hacienda te devuelve lo retenido
  de más.
- **Campaña de la Renta:** cada lunes se cierra la semana anterior y las
  pérdidas de unos días compensan las ganancias de otros. Lo retenido de más
  sale a devolver, y se cobra presentando la declaración: con `renta` o con
  el aviso (solo lo ves tú) que sale la primera vez que juegas en la semana.
  Al presentar, el bot lo anuncia en el canal. Se guardan sin caducidad las 2
  últimas semanas pendientes; si se acumula otra, la más antigua se pierde.
  El bot crea un evento de Discord por campaña (necesita **Gestionar
  eventos**). La retención
  proyecta la renta anual con todo lo cobrado en los últimos 7 días
  (nóminas, premios y casino juntos, la misma semana que la Renta) y le aplica
  la escala estatal y la de Canarias con sus mínimos personales, a 10 Y$
  por euro (detalle y fuentes en `src/bot/services/taxes.py`). Cada cobro
  muestra una línea pequeña con lo que se lleva Perro Sanxe. Todo lo
  retenido entra en la cuenta del Estado (`user_id = 0` en
  `economy_wallets`), y `hacienda` muestra a cualquiera su saldo, lo
  recaudado este año y desde siempre, y quién más ha pagado. Todo movimiento queda en
  un libro (`economy_ledger`) y se aplica de forma atómica: dos clics a la
  vez no pueden gastar dos veces el mismo dinero.
- **Impuesto sobre el Patrimonio:** cada lunes se cobra lo que pase de
  70.000 Y$ con los tipos reales (0,2 % a 3,5 %, art. 30 de la Ley 19/1991),
  tramos escalados al mismo factor que el mínimo. `saldo` avisa de lo que te
  tocaría y el bot anuncia quién ha pagado. La primera semana tras desplegarlo
  no cobra.
- **Cuenta remunerada:** cada día el monedero paga intereses sobre el saldo
  medio del día anterior, por tramos: 2,5 % hasta 4.000 Y$, 1,25 % hasta 20.000
  y 0,4 % hasta 70.000 (500 Y$ brutos al día como mucho). Cobran también los
  que no hacen nada. Retención del 19 % en cada pago (art. 101.4 LIRPF) y, cada
  lunes, liquidación con la escala del ahorro (arts. 66.1 y 76 LIRPF). El aviso
  llega una vez, en la siguiente acción con dinero; `saldo` enseña lo de ayer y
  lo que va saliendo hoy.
- **IGIC:** el impuesto al consumo es el canario, no el IVA. Lo pagan las
  compras de la tienda, al tipo de cada artículo: cero, reducido (3 %),
  general (7 %, el de por defecto), incrementado (9,5 %) o de lujo (15 %),
  como en los arts. 51 a 59 de la Ley 4/2012 de Canarias.
- **Tienda** (`tienda`): El Colmado de Jovani. Escaparate con pestañas por
  lo que hace cada artículo (🎭 roles, ⚡ XP, 🐾 mascotas, 💎 vitrina, 🫳 para
  usar) y un desplegable de pasillos por su tema,
  cada artículo con su botón **Comprar**, rebajas tachadas, existencias que
  quedan y etiquetas de 🆕 nuevo y 🔥 lo más vendido. Los precios van sin IGIC; la caja enseña el
  ticket (precio, rebaja, base, IGIC y total) y pide confirmar. Al pagar
  llega una factura simplificada que solo ve el comprador y un aviso público
  en el canal. Cualquiera puede comprar desde el escaparate de otro: la caja
  se abre solo para quien pulsa.
  - Roles para siempre o alquilados (volver a comprar alarga el alquiler; al
    vencer, el bot los quita). Si no puede dar el rol, devuelve todo, IGIC
    incluido.
  - Potenciadores: multiplican el XP de mensajes y voz (de ×1,1 a ×3) un
    tiempo; si ya tienes uno, el nuevo se pone a la cola.
  - Coleccionables: de capricho; con existencias limitadas, cada unidad sale
    numerada ("nº 3 de 10").
  - Surtido de serie: 213 artículos a la venta (176 objetos, 15
    potenciadores y 22 mascotas) que se meten solos en cada servidor la
    primera vez que se abre la tienda, repartidos en 14 pasillos por tema (La
    Moncloa, Ventanilla, Canarias, Typical Spanish, Fiestas y verbenas,
    Supersticiones, Bazar de todo a 100, Rincón boricua, Importación de Hong
    Kong, Vida de rico, Lo que no debería venderse, Tienda de animales,
    Ferretería del curro y Peña de la porra). Del
    Falcon (50 millones, una unidad) a la piedra (1 Y$), cada uno con el IGIC
    que le tocaría de verdad. Lo que se come lleva 🍽️ (las mascotas se lo
    comen). Lo que un administrador retire no vuelve solo. Las reglas para
    añadir artículos (tipo por lo que hace, pasillo por su tema) están en la
    Biblia.
  - Objetos que se usan (59): desde la mochila, contra alguien o sin más:
    huevos, tomates, burofax, multas de la DGT, el chivatazo a la UCO,
    Pegasus, indultos, bulos, la encuesta del CIS, pimientos de Padrón, la
    bola 8, el d20, el megáfono, el DNI falso (cambia el apodo), la llamada a
    Robuso (contesta según la hora real de Hong Kong) y la caja botín (da un
    coleccionable al azar). El resultado sale en el canal y menciona a quien
    lo recibe. Los de un solo uso se gastan; el resto tiene una espera entre
    usos. Usar no mueve dinero.
  - La mochila (🎒 en el escaparate o en la sección 🎒 Objetos de `perfil`)
    enseña lo que tiene alguien; su dueño puede ponerse y quitarse los roles
    que compró para siempre y usar sus objetos.
- **Mascotas** (`mascota [miembro]`): 28 especies con su personalidad, del
  gato que te tira la ficha de la mesa a Perro Sanxe (una en todo el
  servidor). 22 se adoptan en la tienda (perros, gatos y hurones con «tasa de
  adopción», que la Ley 7/2023 no deja venderlos) y 6 aparecen solas: la
  cucaracha cuando te quedas a cero, el gato callejero al cobrar el IMV, la
  cotorra al subir de nivel, el lagarto gigante de El Hierro el Día de
  Canarias… Puedes tener todas las que quieras; una va contigo.
  - El panel deja acariciarla, jugar con ella y darle de comer lo que tengas
    en la mochila (cada una tiene su comida favorita; la cabra se come
    cualquier cosa, Modelo 100 incluido), ponerle nombre y elegir cuál te
    acompaña. Los demás solo miran.
  - Cuidar sube el vínculo, que nunca baja. Da poco a propósito: hasta +5 %
    de XP con la que llevas, trucos nuevos a nivel 3, 6 y 9, y a veces un
    regalo del colmado. No hay castigo por no cuidarla.
  - La que va contigo sale en los mensajes del bot con una frase suya: en
    las jugadas del casino, la lotería, `pala`, el IMV, la tienda, los
    niveles, los logros y tu cumpleaños. Siempre en los momentos sonados
    (pelotazo, quedarte a cero) y de vez en cuando en los demás.
  - La base de cada venta va a la caja de la tienda (`user_id = -200`) y el
    IGIC al Estado; `hacienda` lo cuenta como recaudado.
- **Trastienda** (`catalogo`, solo administradores): panel con botones para
  poner a la venta roles, potenciadores y coleccionables, y editar cada
  artículo (precio, descripción, duración, multiplicador, existencias, máximo
  por persona, nivel mínimo, rebaja con fecha de fin, tipo de IGIC, ocultarlo
  o retirarlo). Todo se configura desde Discord, sin tocar código. No deja
  vender roles por encima del del bot ni con permisos de moderación o
  administración. **📦 Reponer surtido** vuelve a poner a la venta los
  artículos de serie retirados. El bot necesita **Gestionar roles** (y
  **Gestionar apodos** para el DNI falso).
- **Trabajo** (`pala`): coges la pala y curras. Cinco oficios con 5 puestos cada
  uno (🦺 obra, 🍽️ hostelería, 🌹 política en el PSOE, de pegacarteles a
  consejero de una eléctrica, 🏥 sanidad y 💻 oficina). Cada turno es un minijuego de decidir rápido, de
  30 a 60 s según el puesto (cavar leyendo el plano, pillar al que se escaquea,
  memorizar comandas o votaciones, esquivar preguntas en rueda de prensa), y la
  nota mueve el sueldo entre el 70 % y el 130 %. Cada turno es una nómina de
  verdad: Seguridad Social del trabajador (6,5 %), IRPF con la reducción por
  rendimientos del trabajo y la cotización de la empresa (32 %), que va al
  Estado igualmente. Batería que se gasta y se recarga sola (o con un
  barraquito), 4 turnos ordinarios al día, 2 extras legales a la semana y,
  después, extras en B con riesgo de Inspección. Ascensos al estilo de los Sims
  (barra de rendimiento, días en el puesto, tareas y formación), sin despidos.
  En la Ferretería del curro de la `tienda` hay 16 herramientas (casco,
  libreta de comandas, pinganillo, vademécum…) que, con tenerlas, ayudan en
  el minijuego de su oficio: más tiempo, un fallo gratis por turno, un seguro
  para las tuberías o una respuesta mala tachada.
  Las nóminas van a 100 Y$ por euro (un turno del puesto más bajo, unos 1.700 Y$,
  cunde como un IMV a racha máxima; ver `docs/economia-trabajo.md`).
  El IMV se reduce 1 Y$ por cada 20 Y$ netos de nómina que pasen de 11.506 a la semana
  (incentivo al empleo del RD 789/2022), pero nunca baja del 20 %. Eventos con
  dos opciones (sobres, enchufes, la UCO, la comunión del sobrino). En
  sanidad se rinde con **guardias** (turno doble que paga 1,6 veces y te deja
  saliente 12 h; el residente tiene guardias mínimas). En oficina se puede
  **teletrabajar** (cansa menos, rinde menos y te escriben a las once), el CTO
  cobra parte en **stock options** (exit o quiebra) y desde programador senior
  te puedes **ir a Hong Kong**: sueldo doble, MPF y salaries tax, exención del
  art. 7.p LIRPF mientras sigues siendo residente, sin IMV, y Ley Beckham si
  vuelves tras 5 «años» fuera. Un administrador limita los canales con `tajo`.
- **Bizum** (`bizum`): manda yapdollars a otro miembro al momento. Llega
  entero: exento de Donaciones y sin IRPF (en la vida real, entre amigos se
  pagaría; el bot trata a todo el servidor como familia directa). Mínimo 5 Y$
  (0,50 €). Los máximos de Bizum (10.000 Y$ por operación y 20.000 Y$ al día)
  no se aplican: el bot avisa de que te has pasado y te da el logro
  *A espaldas de Sánchez*. El resultado es público y menciona a quien recibe.
- **Donativos** (`donar`): cuatro ONGs de broma que hacen lo contrario de lo
  que dicen. Donar es gastar (el dinero se queda en la ONG), pero desgrava en
  la renta del lunes: 80 % de los primeros 2.500 Y$ y 40 % del resto, hasta el
  10 % de lo que ganes esa semana y sin pasar del IRPF pagado.
- **Ruleta americana** (0 y 00, la casa gana el 5,26 %): `ruleta` abre una
  mesa con botones que solo puede usar quien la abre. Cada clic en una
  apuesta cobra, gira (GIF de ~4 s) y paga. En cada tirada caen de 1 a 5
  ⚡ rayos sobre números al azar, con multiplicadores de ×50 a ×500: un
  pleno con rayo cobra eso y uno sin rayo, 29 a 1. Botones: rojo/negro, par/impar,
  1-18/19-36, docenas, columnas, 0 y 00; 🎯 **Números** abre un formulario
  para plenos, caballos, transversales, cuadros, seisenas y la línea
  0-00-1-2-3. Con ½, ×2 y 💰 All-in se cambia la ficha; 🔁 Repetir y
  ⏫ Doblar repiten la última tirada. 🔥 **Caliente** y ❄️ **Frío** apuestan
  a pleno al número que más ha salido en el servidor o al que más tarda.
  🧩 **Varias** cambia al modo de varias apuestas: cada botón pone una
  ficha (pulsar dos veces la misma apila fichas), 🎰 **Girar** las juega
  todas en la misma tirada y 🗑️ las quita. Hasta 10 apuestas distintas;
  todo se cobra y se paga en una sola operación y el resultado marca qué
  entró. Atajos de texto: `.ruleta 500`, `.ruleta all rojo`,
  `.ruleta 50 17-20`, `.ruleta rojo`, `.ruleta 100 rojo + 17 + d2` (varias
  con `+`, la cantidad es por apuesta; con `all` se reparte el saldo).
  La rueda la pinta Node con canvas (`assets/ruleta/escena.html`, Skia, sin
  navegador): gira en perspectiva, la bola corre al revés, rebota y salta de
  casilla en casilla a cámara lenta, y el marcador enseña los últimos
  números, los calientes y los fríos. Cada tirada se dibuja al momento
  (~1 s, en dos procesos a la vez; mientras, la mesa dice «🎲 No va
  más…»), salvo la que repite la última apuesta: esa se precarga mientras
  gira la anterior y sale al instante, como en el pachinko. Si no hay Node,
  la pinta Chromium y, si tampoco, la rueda de Pillow de antes.
- **Blackjack** (`/blackjack`, `.blackjack` o `.bj`): reparte al momento con la apuesta indicada
  (`.bj 500`, `.bj all`) y se juega con botones: 🃏 Pedir, ✋ Plantarse,
  ⏫ Doblar y ✂️ Separar. Si la banca enseña un as, antes salen 🛡️ Seguro y
  Sin seguro. Al terminar, 🃏 Repartir juega otra mano en el mismo
  mensaje y ½ / ×2 / 💰 All-in cambian la apuesta. La mesa es una imagen
  (tapete y cartas, ~5-10 KB por paso) y la banca roba carta a carta en
  pantalla. Reglas: 6 barajas rebarajadas en cada mano, la banca se planta
  en 17 (también blando) y mira si tiene blackjack; si lo tiene, pierdes la
  apuesta (empate si tú también lo tienes). Seguro con un as de la banca:
  cuesta media apuesta y paga 2:1 si tiene blackjack. Blackjack paga 3:2,
  doblar con dos cartas (también tras separar), separar una vez; sin
  rendición ni tope de apuesta. Jugando bien y sin seguro, el juego devuelve ≈ 99,7 % de lo
  apostado; el seguro, ≈ 92 % de lo que se mete en él. La apuesta se cobra al repartir (y al doblar, separar o asegurar) y
  el premio se paga al acabar. Si la mesa caduca o el bot se apaga con una
  mano a medias, se planta y se paga.
- **Tragaperras** (`/tragas`, `.tragas [cantidad]`): una máquina de 3 rodillos
  con botones que solo usa quien la abre. Paga la fila del medio; las filas
  de arriba y abajo se ven para que se note cuándo has estado cerca.
  Botones: 🎰 Tirar, 🔁 Ráfaga ×10 (diez tiradas sin animación y con un solo
  resumen), ▶️ Auto (tiradas encadenadas, cada una con su animación; el botón
  pasa a ser ⏹️ Parar y para solo al llegar a 25 tiradas, con un premio gordo,
  sin saldo o al perder 10 veces la apuesta), ⚡ Turbo (sin animación),
  ½ / ×2 / 💰 All-in y 📋 Premios. Cuando tocan salen 🔁 Re-girar el 3º (tras
  un casi-premio, por lo que vale de media el re-giro), 🔴 Rojo / ⚫ Negro (doble
  o nada con lo cobrado, hasta 5 veces) y 🎁 Giro del día (gratis, a 100 Y$ por
  día de racha, hasta 700 Y$). El embed enseña lo que paga cada combinación a
  tu apuesta y los premios cobrados en la sesión; al cerrarse sale el ticket
  con el neto. Premios:
  🍒 al principio devuelve la mitad, 🍒 🍒 ×2, tríos de ×4 a ×200, 🃏 comodín
  y 🃏 🃏 🃏 se lleva el **bote común** del servidor, que crece con el 3 % de
  cada apuesta y vuelve a 5.000 Y$ al vaciarse; además cae solo antes de
  llegar a 50.000 Y$. Tres 🎟️ en cualquier fila dan 5 giros gratis, y cada 5
  tiradas con premio la máquina se calienta y la siguiente paga ×2 (el calor
  se guarda y se enfría un punto cada 10 minutos sin jugar). La barra de bonus
  sube con cada tirada pagada (más con los casi-premios, y a cuentagotas al
  final) y, llena, da 3 giros gratis. Los rodillos
  tienen pesos por casilla, como las máquinas reales: el 7️⃣ roza la línea en
  una de cada cinco tiradas. Devuelve ~99,5 % de lo apostado contando el bote. El GIF
  (~150 KB, ~0,1 s de CPU) se monta en cada tirada con piezas precalculadas:
  los rodillos paran uno a uno y, si los dos primeros prometen algo gordo, el
  tercero frena despacio. Los premios tributan como el resto del casino, el
  bote incluido, y los de más de ×50 y los botes se anuncian en el canal.
- **Botes** (`/volcan`, `.volcan [cantidad]`): una tragaperras de 5×4 al
  estilo de las de casino, ilustrada (cerezas, campana, BAR, siete, máscaras
  tiki, diamante WILD y un volcán de recogedor). Los símbolos pagan por
  **ways** y las monedas (verde, azul y roja, cada una con su forma) llevan su
  premio escrito: el volcán en el rodillo 1 o el 5 las cobra todas. Cada
  moneda que cae llena el **maletín** de su color, que se guarda en la base de
  datos; lleno, dispara un **bonus** de 3 tiradas que vuelven a 3 cada vez que
  cae algo, con tickets de multiplicador, tiradas extra, multiplicadores
  inmediatos, maximizador y misteriosos. Botes MINI (10 monedas), MAJOR (15) y
  GRAND (pantalla llena, ×1.000). Botones: 🎰 Tirar/Girar, 🔁 Auto ×10 o
  ⏩ Auto bonus, ⚡ Turbo, ½ / ×2 / 💰 All-in y 📋 Premios. Devuelve ~94 %, con
  un bonus cada ~58 tiradas. GIF de 300-450 KB por tirada base y ~200 KB por
  tirada del bonus; en turbo, solo el PNG (~120 KB). Tributan como el resto
  del casino. Las reglas admiten más máquinas con otros dibujos (`THEMES`).
- **Crash** (`/cohete`, `.cohete [cantidad] [auto]`): un cohete compartido
  por canal. En el embarque (7-10 s) se entra con 🚀 o con `.cohete 500 2x`
  (500 Y$ y auto-retiro en 2x); ½, ×2, 💰 All-in y 🎯 Auto cambian tu ficha.
  Luego el multiplicador sube, lento al principio y cada vez más rápido
  (2x a los 8 s, 10x a los 19 s, tope en 1.000x) y 💸 Retirar cobra
  apuesta × multiplicador; quien sigue dentro cuando explota, lo pierde. Devuelve el 99 % de media y el 1 % de las
  rondas explota en 1,00x. Al explotar sale una gráfica con la curva y quién
  saltó dónde, y el mismo mensaje abre la ronda siguiente. Una edición por
  segundo durante el vuelo y una imagen por ronda.
- **Minas** (`/minas`, `.minas [cantidad] [minas]`): un tablero de 5×5 propio
  con 1 a 12 minas (2 por defecto). La primera casilla siempre es buena y
  devuelve la apuesta; desde ahí cada 💎 sube el multiplicador, 💰 Cobrar se
  lo lleva y una 💣 lo pierde todo. Más minas, más pago por casilla y sin tope:
  el menú enseña lo que paga limpiar el tablero con cada opción (de ×23 con 1
  mina a ×2.677.114 con 12). El texto cuenta las casillas (💎 7/23), lo
  que sumaría la siguiente y su probabilidad, celebra rachas y avisa al batir
  tu récord. Devuelve el 99 % de media desde la segunda casilla, sin
  imágenes, y los cobros de ×25 o más se anuncian en el canal.
- **Pollo** (`/pollo`, `.pollo [cantidad] [dificultad] [autocobro]`): el
  Chicken Road de los casinos online. El pollo cruza una carretera carril a
  carril; cada carril sube el multiplicador, 💰 Cobrar se lo lleva y si te
  atropellan lo pierdes todo. Cuatro dificultades: Fácil (4 % por carril,
  meta ×2,63), Media (12 %, ×16,48), Difícil (20 %, ×85,86) y Hardcore (40 %,
  ×2.105). Cada paso es un GIF en el que el pollo tiembla mientras se
  encienden unos faros (la espera, más larga cuanto más hay en juego, es la
  tensión) y después un PNG. 🎯 Autocobro cruza solo hasta el multiplicador
  elegido en un único GIF. Al cobrar dice dónde estaba el coche («quedaban 4
  carriles libres»). Se pinta con canvas al estilo de las demás mesas (en
  Node con Skia, sin navegador; Chromium y Pillow quedan de reserva) y el
  siguiente paso se pinta antes de pulsar, así que el GIF sale al momento.
  Devuelve el 99 % de media.
- **Cara o cruz** (`/moneda`, `.moneda [cantidad] [lado]`): doble o nada.
  Eliges 👑 cara (la corona) o ✈️ cruz (el Falcon) con los botones y se
  lanza; si aciertas, lo que hay en juego se dobla y decides si cobras o te
  la juegas otra vez. A las diez seguidas cobra solo la moneda de oro
  (×1.024). Una de cada cien cae **de canto**: se queda de pie, Perro Sanxe
  la sella y pierdes lo que hubiera. Es la ventaja de la casa: cada
  lanzamiento devuelve el 99 %, así que cobrar tras k aciertos devuelve
  0,99^k. Cada lanzamiento es un GIF dibujado con canvas en Node con Skia,
  sin navegador (la moneda bimetálica gira, rebota y se asienta, o se
  tambalea y se queda de pie). Si no hay Node, lo pinta Chromium y, si
  tampoco, Pillow. Al cobrar dice cómo habría caído la siguiente.
- **Autobús** (`/guagua`, `.guagua [cantidad]`): Ride the Bus. Cuatro
  cartas y cuatro preguntas: rojo o negro; mayor, menor o igual; dentro, fuera
  o poste; y el palo. Tras cada acierto cobras o sigues, y quien acierta las
  cuatro puede jugárselo a la vuelta (doble o nada). Cada botón enseña su
  multiplicador y su probabilidad, y 📋 Tabla la tabla de pagos fija: cobres
  donde cobres, devuelve el 99 %. La guagua avanza de parada en parada en un
  GIF dibujado con canvas en Node con Skia, sin navegador (si no hay Node, en
  Chromium y, si tampoco, con Pillow); las cartas se sortean antes de jugar y
  la animación de la mano siguiente se dibuja mientras piensas.
- **Dados** (`/dados`, `.dados [cantidad] [apuesta]`): craps de casino. Eliges
  ✅ Pase o 🚫 No pase y tiras la salida: con Pase, el 7 y el 11 ganan y el 2,
  el 3 y el 12 pierden; con No pase, al revés, y el 12 empata. Cualquier otro
  total es el **punto**: se sigue tirando hasta repetirlo (gana Pase) o sacar
  un 7, el **siete fuera** (gana No pase). Con el punto puesto, ➕ Odds pone
  encima hasta tres veces la apuesta a la probabilidad exacta (2:1 al 4 y al
  10, 3:2 al 5 y al 9, 6:5 al 6 y al 8), sin ventaja para la casa. Pase
  devuelve el 98,6 % y No pase el 98,6 %, con las reglas de cualquier casino.
  La mesa lleva la mano del tirador hasta el siete fuera, y las manos de 5
  puntos o más se anuncian en el canal. Cada tirada es un GIF dibujado con
  canvas en Node con Skia, sin navegador (si no hay Node, en Chromium y, si
  tampoco, con Pillow): los dados rojos vuelan en 3D, chocan contra la pared
  de pirámides, ruedan y se paran; luego se encienden el total y el cartel, el
  disco ON/OFF se mueve a su casilla y Perro Sanxe recoge o paga las fichas.
- **Carreras de caballos** (`/caballo`, `.caballo [cantidad] [caballos] [tipo]`):
  una carrera por canal, solo cuando alguien la pide. Un establo de 16
  caballos con rasgos propios (velocidad, aguante, salida, terreno preferido y
  regularidad) y forma guardada por servidor. Las cuotas salen de simular la
  carrera 80.000 veces con su terreno, su distancia y su parte de lluvia:
  ganador y colocado devuelven el 95 %, gemela el 92 % y trío el 90 %. En la
  parrilla (2 minutos como mucho) se apuesta con 🎟️ Apostar (panel privado) o de
  un toque con 🐶 Lo de Sanxe, 🐑 Con el pueblo o 🎲 Al azar; `.caballo 500 3-5-1`
  va al trío. Si todos los que han apostado pulsan ✅ Listo, salen sin
  esperar. La carrera es un GIF dibujado con canvas en Node con Skia, sin
  navegador (grada, sedas, galope, polvo, lluvia, rótulos de la tele) con
  foto-finish a cámara lenta si llegan pegados; se dibuja mientras se
  apuesta, así que sale en cuanto se cierra la parrilla. Si no hay Node, la
  pinta Chromium y, si tampoco, Pillow; la parrilla y el boleto, que son
  HTML/CSS, siempre los captura Chromium. Cada 8 carreras (y 4 h) sale el
  Gran Premio con un bote para quien acierte el ganador, que crece si nadie
  acierta. Tributa como el resto del
  casino.
- **Pachinko** (`/pachinko`, `.pachinko [cantidad] [mapa]`): una máquina
  japonesa propia con botones. Cada 🎯 Lanzar cobra la apuesta y suelta 10
  bolas que rebotan por las filas de clavos hasta los bolsillos (los de las
  esquinas pagan más; OUT nada y START, en el centro, juega en la pantalla).
  Cuatro tableros con su propio tema, elegibles en un menú o al abrir
  (`.pachinko 500 oni`): 🌸 Sakura (8 filas, riesgo bajo, atari cada ~7
  tandas), 🏮 Clásica (10 filas, medio), 🐉 Dragón (10 filas, alto, esquinas
  de ×50) y 👹 Oni (12 filas, extremo, atari cada ~39 tandas que paga ×30 de
  media). Con 🎲 Al azar cada tanda cae en uno. Todos devuelven entre el
  94,3 y el 95,2 %: el tablero cambia el riesgo, no la ventaja de la casa. Cada bola en START gana una tirada de la
  pantalla (reserva de 4): tres iguales es **ATARI** (+30 bolas), los impares
  encadenan premios en un **RUSH** y el 7 es el **SUPER RUSH**. El reach
  (dos iguales y el centro frenando) es espectáculo y no cambia nada.
  Botones: 🎯 Lanzar, 🔁 Ráfaga ×5, ▶️ Auto, ⚡ Turbo, ½ / ×2 / 💰 All-in y 📋 Premios.
  ▶️ Auto encadena tandas con su animación hasta 25, y para sola con un atari,
  sin saldo o al perder 10 veces la apuesta; mientras corre, el botón es ⏹️ Parar.
  La tanda siguiente se sortea y se dibuja mientras miras la anterior, sin mover
  dinero hasta que pulsas: el clic pasa de ~0,3 s a pocos milisegundos.
  El mueble, los clavos, los bolsillos, la bola y las bombillas se pintan con canvas una sola vez y se guardan como PNG
  (`python docs/pachinko_piezas.py` los regenera); el bot solo los pega, sin abrir ningún navegador.
  Los números salen de fracciones exactas en las pruebas. Las bolas caen con
  física real (gravedad y rebotes en clavos y paredes) y **chocan entre sí**,
  y aun así cada una acaba donde dijo el sorteo: el bolsillo lo decide la
  máquina y la física obedece (se busca una tanda en la que las diez, chocando
  de verdad, acaben cada una en el suyo, ~75 ms de CPU), así que no cambia lo
  que paga. El GIF (190-390 KB, ~0,2 s de CPU) tiene
  bombillas que persiguen, adornos que se mueven (molinillos, flores,
  perlas de dragón o llamas), rótulo de neón y la pantalla jugando
  la reserva mientras siguen cayendo bolas. Tributa como el resto del casino
  y los SUPER RUSH, los rush de 5 o más y los premios de ×20 se anuncian.
- **Porras** (`porra`): apuestas entre miembros sobre las próximas jugadas
  de otro. `/porra @ana minas mina 3 500` le monta a Ana una porra: «¿pisa
  alguna mina en sus próximas 3 partidas de al menos 500 Y$?». Nadie puede
  montársela a sí mismo y la protagonista tiene que aceptar, porque se
  compromete a jugar. Durante 90 s cualquiera menos ella apuesta a una de
  las opciones (una por persona); luego tiene un plazo para jugar y el bot
  cuenta sus jugadas solo. Reparto mutuo, como la Quiniela: sin banca y sin
  cuotas fijas. De cada apuesta, el 10 % va al Estado como Impuesto sobre
  Actividades de Juego y el 2 % a la protagonista por derechos de imagen
  (con retención fija del 24 %); el resto se lo reparten quienes aciertan.
  El bote no pasa de 5 veces lo que ella se juega, para que amañarla no
  salga tan a cuenta. Once propuestas: gana o pierde, si las gana todas,
  cuántas gana, si dobla, si se pega un palo, si saca un ×5, si acaba tieso
  y las propias de minas, pollo y blackjack. Vale en todos los juegos menos
  el crash, y un juego nuevo las tiene sin hacer nada. Sin `miembro`, enseña
  las porras en marcha. En la tienda, los 🔭 Prismáticos de la UCO dejan ver
  quién apuesta qué y la 📓 Libreta de la porra, montar porras de 10 jugadas.
- **Loterías** (`loteria`): un solo comando abre un panel con pestañas para
  la Lotería Nacional (jueves, sábado, Navidad y Niño), La Primitiva,
  Bonoloto, El Gordo de la Primitiva, Euromillones y dos rascas de la ONCE
  (X10 y 7 y Media). Se compra con botones: apuestas al azar (1, 5 o 10),
  décimo o billete, o números elegidos en un formulario. Precios y reparto
  son los reales (normas de SELAE y tablas de la ONCE) a 10 Y$ por euro, y
  las probabilidades de la Nacional y los rascas también. En los juegos de
  bote se ajustan al servidor: si cada persona (sin contar bots) juega una
  apuesta por sorteo, el bote cae más o menos una vez al mes. Con 30
  personas, el bote de la Primitiva es 1 entre 386 (en la vida real, 1 entre
  139.838.160). La cuenta del Estado hace de banca: cobra los boletos sin
  IGIC, paga los premios con el gravamen especial del 20 % por encima de
  400.000 Y$ y garantiza el bote mínimo del Gordo y de Euromillones,
  encogido en la misma proporción y con una cuarta parte de su saldo como
  mucho. Si no
  le llega para un premio, emite deuda pública. Los sorteos se celebran solos
  a su hora y se anuncian en el canal; los rascas se rascan pulsando las
  casillas (spoilers).
- **Perfil** (`perfil [miembro]`): todo lo de un miembro en un mismo menú.
  Un desplegable cambia de sección en el mismo mensaje: 📋 Resumen (un
  renglón de cada cosa), 📊 Nivel (nivel, XP y racha de días escribiendo),
  🏰 Patrimonio (activos y el Patrimonio del lunes), 🪏 Trabajo (contrato y
  vida laboral; para fichar sigue estando `pala`), 🏆 Logros, 🔥 Rachas (la
  vigente y los récords de todas las rachas que cuentan los logros) y 🎒
  Objetos (la mochila; su botón la abre para usar objetos y ponerse roles).
  Sustituye a `nivel`, `patrimonio`, `logros` y `mochila`. Solo lo maneja
  quien lo abre. Logros en 🏆 Coleccionista: *¿Quién soy yo?*, *Selfie
  diario*, *Ego de ministro*, *Fisgón de rellano*, *Informe de la UCO*,
  *Pegasus de barrio*, *Expediente completo* (ver las siete secciones) y dos
  secretos.
- **Logros** (sección 🏆 de `perfil`): 2.213 logros en 41 categorías. El menú
  tiene tres grupos con secciones: 💬 Chat (general, estilo, risas, hacer
  reír, lengua y temas, conversación, horarios y fechas, imágenes y babel),
  🎙️ Voz (llamada, micro y cámara, entradas y salidas, música) y 🎰 Casino
  (una sección por juego); y además social, lista, beernight, niveles, loterías,
  tienda, mascotas, banco, economía, trabajo, oficios, sanidad, oficina, Hong Kong y
  coleccionista. Cinco rarezas según lo que cuesta conseguirlos: ▫️ común,
  🔹 raro, 💠 épico, 🌟 legendario y 👑 mítico (las del casino, calibradas
  con una simulación, ver `docs/auditoria-logros.md`). 380 son secretos y
  se ven como `???` hasta conseguirlos. Las risas se reconocen de muchas
  formas (jaja, jsjs, lol, xd, 😂, 💀, ajsjsjs, kkkk, «me meo»…).
  Cada logro paga yapdollars según su rareza (50, 200, 750,
  2.500 o 10.000 Y$ brutos) con retención de IRPF, y se anuncia en el canal
  donde se consiguió. La sección 🏆 de `perfil` enseña un resumen (total, puntos, últimos
  conseguidos, los más cercanos y el más raro), un menú por categorías con
  el progreso de cada uno y el porcentaje del servidor que lo tiene (las
  largas, en hojas con ◀ y ▶), y un botón 🏆 Ranking por puntos; el botón
  🏆 de la sección los abre en un mensaje aparte.
  - Los mensajes y reacciones se cuentan en memoria y se guardan una vez por
    minuto en una sola escritura por servidor. Del mensaje solo se miran
    propiedades (largo, hora, enlace, mayúsculas…), nunca se guarda el texto.
  - La voz cuenta un minuto cada minuto a quien está en llamada (aunque
    tenga el micro silenciado) con al menos otra persona que no esté
    ensordecida. El canal AFK no cuenta como llamada, pero tiene sus propios
    logros, igual que estar ensordecido.
  - La primera vez que el bot ve a alguien recupera sus mensajes del
    historial importado y su nivel, así que los veteranos cobran de golpe
    lo que ya tenían.
- `babel` es un teléfono escacharrado con traductores: pasa el texto por 99
  idiomas elegidos al azar y lo devuelve al español, para ver qué queda.
  Responde con el antes, el después y la ruta de idiomas. `.babel` sin texto,
  respondiendo a un mensaje, traduce ese mensaje. Máximo 300 caracteres y una
  tirada a la vez en todo el bot (son 100 peticiones seguidas y tardan unos
  segundos). Usa el endpoint público de Google Translate, sin clave ni coste,
  pero sin garantías: si Google limita la IP, la tirada se corta, vuelve al
  español desde donde iba y lo avisa.
  - Si solo le das menciones (`.babel @Ana @Luis #general`, hasta 10), en
    vez de enseñar el resultado **cambia de verdad** el apodo de esos
    miembros y el nombre de esos canales. Conserva emojis y separadores del
    principio del nombre (`🎮・juegos` → `🎮・<traducción>`). Todos los nombres
    viajan juntos en la misma tirada de 100 traducciones.
  - Exige los mismos permisos que Discord para hacerlo a mano: tu apodo,
    **Cambiar apodo**; el de otro, **Gestionar apodos** y un rol por encima
    del suyo; un canal, **Gestionar canales**. El bot necesita **Gestionar
    apodos** y **Gestionar canales** y estar por encima de los roles de
    quienes renombra. Al dueño del servidor no se le puede cambiar el apodo.
- `hongkong` dice qué hora es en Hong Kong, la de Canarias, cuántas horas van
  por delante (8 en invierno, 7 en verano) y qué anda haciendo Robuso a esa
  hora. Tiene sus logros en la categoría 🇭🇰 Hong Kong.
  - Discord solo deja renombrar un canal 2 veces cada 10 minutos; el bot lo
    avisa en vez de quedarse esperando. No hay comando para deshacer: el
    apodo se quita desde Discord y el canal se renombra a mano.
- Si un comando de texto falla (falta un argumento, un error inesperado…) el
  bot responde con un mensaje claro; los errores internos se guardan en el log.
- Los avisos de subida de nivel se publican en el canal donde se ganó el XP
  (el chat del canal de voz si fue hablando) e incluyen el premio cobrado.
- Al entrar alguien, el bot le saluda con una frase de
  `src/bot/assets/bienvenidas.txt` y el GIF del servidor (sin GIF, el vídeo
  `src/bot/assets/bienvenida.mp4`) en el canal de bienvenida, que por
  defecto es `#chat-general`. Si ya había estado, usa las frases de vuelta.
  Los demás pueden pulsar **👋 Dar la bienvenida** durante su primer día:
  200 Y$ para quien saluda y 100 Y$ para el nuevo, sin IRPF (como los
  regalos de cumpleaños), y cuenta para logros. Un administrador cambia el GIF y el canal con
  `bienv`. Al salir, publica una despedida con una frase aleatoria tomada de
  `src/bot/assets/despedidas.txt` en ese mismo canal.
- `ranking` resuelve nombres visibles del servidor incluso para miembros que
  todavía no estén en la caché local del bot, y lo presenta en un embed con
  podio, progreso visual y paginación.
- Música en canales de voz, sencilla y por servidor: `poner` busca o resuelve
  un enlace (vía `yt-dlp`) y lo reproduce, o lo añade a la cola si ya suena
  algo; `parar` además vacía la cola y desconecta al bot. Solo se puede
  controlar la reproducción desde el mismo canal de voz en el que está el
  bot. Las pistas están limitadas a 30 minutos y la cola, a 50 elementos por
  servidor. El bot abandona el canal automáticamente si se queda sin oyentes
  humanos o tras 5 minutos de inactividad. Se conecta ensordecido para que
  Discord no le envíe el audio de los demás.
- Sonidos de entrada: cada miembro sube con `entrada` un audio de hasta 3 s
  que suena cuando entra a un canal de voz, con volumen ajustable (10-200 %).
  El bot entra, lo reproduce y se va; no suena si el bot ya está poniendo
  música. Los clips se guardan en `.data/entradas/` (mismo volumen Docker
  que la base de datos).
- **Beernight** (`beernight`): la noche de llamada del servidor, para jugar a
  lo que sea y beber lo que cada uno tenga. El panel tiene unos cuantos
  mandamientos activos (154 de serie en siete familias, más los que proponga
  la gente) que rotan solos. Quien cae lo confiesa con 🍺; si no, alguien se
  chiva con 🚨 y otra persona lo confirma, y si el chivatazo es falso bebe el
  chivato. Cada pocos minutos salta uno de los 69 eventos: sorbos directos,
  duelos, retos, repartos, decretos con un mandamiento temporal y
  remodelaciones. El anfitrión y los administradores ajustan el ritmo, el
  tope de sorbos por hora y las familias. Cada servidor sube sus audios con
  `beernight sonido:<momento> archivo:<audio>` (hasta 6 s, cinco por
  momento) y el bot los pone en la llamada si no está ya con música. Todo
  queda en un histórico con ranking de siempre. No mueve yapdollars. Los
  audios se guardan en `.data/beernight/`.

La importación histórica de este servidor ya se completó y la activación de
niveles ya se ejecutó. Los comandos temporales de importación/activación y los
comandos de configuración y estado de niveles no están disponibles. Los
recuentos y niveles existentes permanecen guardados. Fuentes de XP:

- Mensajes: 15–25 XP, como máximo una vez cada 60 segundos por miembro.
- Primer mensaje del día (hora canaria): +50 XP.
- Voz: 4–6 XP por minuto sin mute ni ensordecido, fuera del canal AFK y con
  al menos otra persona sin mutear en el canal.
- Reacciones recibidas de otros: +5 XP, con tope de 100 XP al día.
- Racha: cada día seguido escribiendo suma un 2 % al XP de mensajes y voz,
  hasta +20 %.

Subir al nivel N paga 100 × N Y$ brutos, el doble en los múltiplos de 5. Los
niveles que ya tenía cada uno antes de este cambio no se pagan.

La economía usa el mismo archivo SQLite (tablas `economy_*`). Si el bot sale
de un servidor, se borran sus saldos y su libro de movimientos.

Los datos persistentes viven en `.data/message_stats.sqlite3`, localmente en
la máquina de ejecución y excluidos de Git; inclúyelos en las copias de
seguridad del despliegue. La base va en modo WAL (`bot.repositories.sqlite`):
junto al archivo aparecen `message_stats.sqlite3-wal` y
`message_stats.sqlite3-shm`, y las últimas escrituras pueden estar todavía en el
`-wal`. Una copia a mano se hace con el bot parado y copiando los tres archivos
juntos; copiar solo el `.sqlite3` con el bot en marcha puede dejar fuera lo más
reciente.

Tras actualizar el código, reinicia el bot para que sincronice y retire los
comandos antiguos de Discord.

Para recibir eventos de entrada y salida, habilita **Server Members Intent**
en la sección *Bot* del [portal de desarrolladores de Discord](https://discord.com/developers/applications).
Para que funcionen los comandos de texto con prefijo `.` (o el prefijo
configurado), habilita también **Message Content Intent** en esa misma
sección: sin él, el bot no puede leer el contenido de mensajes normales y
esos comandos simplemente no se dispararán (los comandos de aplicación `/`
no lo necesitan). El bot también necesita ver `#chat-general` y tener permiso
para enviar mensajes y adjuntar archivos, además de permisos de **Conectar**
y **Hablar** en los canales de voz donde se vaya a usar la música. Para los
comandos de administración necesita además Gestionar mensajes, Aislar
temporalmente a miembros, Expulsar, Banear, Gestionar canales, Gestionar
apodos y Gestionar roles, y su rol debe estar por encima de los roles que
vaya a moderar. Gestionar mensajes también sirve para que `.lista <tarea>`
borre el mensaje de la orden y deje solo la lista.

Las frases de bienvenida se editan en `src/bot/assets/bienvenidas.txt` con
las mismas reglas; `{usuario}` se cambia por la mención y lo que va tras la
línea `[vuelta]` es para quien vuelve al servidor.

Las frases de despedida se editan directamente en
`src/bot/assets/despedidas.txt`, una por línea (las líneas vacías y las que
empiezan por `#` se ignoran). El archivo se relee en cada despedida, así que
los cambios se aplican sin reiniciar el bot. Si el archivo falta o queda
vacío, se usa una frase de reserva para no dejar la despedida sin texto.

## Despliegue con Docker (NAS)

El bot corre bien en cualquier equipo x86_64 con Docker, por ejemplo un NAS
UGREEN DXP2800 (Intel N100): consume poca CPU y memoria y no necesita abrir
puertos, porque solo hace conexiones salientes a Discord y a las fuentes de
audio. La imagen incluye `ffmpeg` y `libopus`, y un Chromium sin ventana
(Playwright, unos 300 MB más) con el que se dibujan la parrilla y el boleto
de los caballos y, si falla Node, el resto de juegos. Los caballos abren el
suyo: mientras se usa ocupa unos 150-250 MB de memoria y se cierra solo tras
10 minutos sin uso; si no estuviera, el bot dibuja con Pillow.
Cara o cruz, los dados, la ruleta, el autobús y la carrera de los caballos
ya no usan Chromium: los pinta Node con `@napi-rs/canvas` (Skia, el mismo
motor de dibujo), con procesos de unos 90 MB que arrancan en menos de medio
segundo.

1. Lleva el proyecto al NAS (`git clone` por SSH, o copia la carpeta).
2. Crea el archivo de configuración y pon tu token:
   ```bash
   cp .env.example .env
   nano .env            # DISCORD_TOKEN=...
   chmod 600 .env
   ```
3. Construye y arranca en segundo plano:
   ```bash
   docker compose up -d --build     # o `docker-compose` si tu NAS solo trae esa versión
   docker compose logs -f           # ver el log; Ctrl+C solo sale del log
   ```
   Si prefieres la interfaz gráfica, la app Docker del NAS puede crear un
   proyecto a partir de este `docker-compose.yml`.

`restart: unless-stopped` hace que el bot arranque con el NAS y se levante
solo si falla; no se reinicia si lo paras a mano (`docker compose stop`). Con
un token inválido el contenedor se reiniciará en bucle: revisa el log.

**Actualizar el bot a mano:** `./actualizar.sh` (o, a la antigua,
`git pull && docker compose up -d --build`).

### Actualización automática

`actualizar.sh` deja el bot al día con la rama `main` de GitHub sin que nadie
tenga que reconstruir la imagen. Programado a las 5:00, cada noche:

- Si no hay commits nuevos, no hace nada y el bot no se reinicia.
- Si los hay, construye la imagen nueva con el bot viejo aún en marcha y
  después lo reinicia (unos segundos sin bot; las partidas a medias se cierran
  como en cualquier reinicio).
- Si la imagen no compila, el bot viejo sigue funcionando.
- Si el bot nuevo se cae o entra en bucle en los primeros 90 s (un PR roto),
  vuelve solo a la versión anterior y no reintenta ese commit hasta que llegue
  otro.
- Una vez por semana reconstruye aunque no haya cambios, para traer la última
  `yt-dlp`.
- Si el despliegue sale bien y trae PR nuevos, el bot publica sus títulos en
  `#chat-general` (ver «Novedades» más abajo).

No hace falta tener git instalado: si el sistema no lo trae (el UGREEN no lo
trae), el script usa la imagen `alpine/git` de Docker. Si en los archivos
versionados hay cambios hechos a mano, el script no actualiza y lo apunta en
el log.

Para programarlo, por SSH en el NAS:

```bash
cd /ruta/al/bot
chmod +x actualizar.sh
# Primera vez a mano. Si la carpeta se copió en vez de clonarse, el script lo
# dice: entonces, una sola vez, `sudo ./actualizar.sh --convertir`, que la
# enlaza con GitHub sin tocar `.env` ni los datos.
sudo ./actualizar.sh     # debe acabar en "Desplegado ..." o "Sin cambios"
sudo crontab -e          # y añade estas dos líneas:
0 5 * * * /ruta/al/bot/actualizar.sh
* * * * * /ruta/al/bot/actualizar.sh --solicitud
```

La segunda atiende el comando `reinicio` de Discord: cada minuto mira si
alguien lo ha pedido y, si no, sale sin hacer nada ni escribir en el log.

cron usa la hora del NAS. Si `date` no da la de Canarias (los NAS suelen ir en
UTC), ajústala con `sudo timedatectl set-timezone Atlantic/Canary` o asume
el desfase en la hora del crontab.
El registro de cada ejecución queda en `.despliegue/actualizar.log`.

### Reiniciar desde Discord: `reinicio`

`.reinicio` (o `/reinicio`) actualiza el bot a lo último de `main` y lo
reinicia sin entrar al NAS, aunque no haya commits nuevos y aunque el último
hubiera fallado. Solo lo pueden usar Yeyo y Dani (IDs en `DEPLOYERS`, en
`src/bot/cogs/deploy.py`) y no sale en `ayuda`. El bot no se reinicia solo:
deja una nota en `.despliegue/buzon` (carpeta montada en el contenedor) y el
cron de `actualizar.sh --solicitud` hace el trabajo. Al acabar, el bot publica
en el mismo canal si ha ido bien o qué ha fallado. Si en un par de minutos no
pasa nada, falta la línea de `--solicitud` en el crontab.

### Novedades: el changelog en Discord

Cada vez que `actualizar.sh` despliega commits nuevos (de noche o con
`reinicio`) y el bot nuevo pasa la comprobación de arranque, el script apunta
en `.despliegue/buzon/novedades.txt` los PR fusionados desde el despliegue
anterior, uno por línea, con su número y su rama de origen. El bot lo mira
cada 15 s, pide a GitHub la descripción de cada PR, lo publica como
«📜 Novedades del bot» y lo borra. Si el bot nuevo se cae y se vuelve atrás,
no se anuncia nada.

El aviso es como la página de un PR en GitHub, sin botones ni bromas:

- Primero, la lista de cambios: título, enlace al PR y autor.
- En modo detallado (el de serie), una ficha por PR con su descripción,
  autor y fecha. Las descripciones largas se cortan con un enlace a GitHub.
  Por eso la descripción de un PR acaba en Discord tal cual (ver «Títulos y
  descripciones de PR» en `CLAUDE.md`).
- Se enseñan 12 cambios como mucho; el resto queda en «…y N más».

De dónde sale cada cambio:

- Cada `Merge pull request` entre los dos commits aporta el título de su PR.
- Los PR que traen entero el `main` del fork (rama `main`) se saltan si dentro
  hay otros PR, que ya cuentan lo mismo con más detalle.
- Si el título es el que GitHub pone solo con el nombre de la rama
  («Claude/adoring tesla ybmnco»), salen los asuntos de sus commits.
- Los commits subidos directamente a `main`, sin PR, no salen.
- El número de PR no dice de qué repositorio es (los PR del fork llegan
  dentro de `main`). El bot lo busca en el de godzilin y en el fork, y vale el
  que sale de la misma rama. Usa la API pública de GitHub sin token (60
  peticiones por hora); si no responde, el aviso sale solo con los títulos.

Los administradores lo configuran con `cambios` (`/cambios` o `.cambios`):
sin argumentos enseña la configuración; `activar` o `desactivar` lo
encienden o apagan; `detallado` o `resumen` eligen el formato; un `#canal`
o un hilo (también un post de foro) fija dónde se publica, y `defecto` vuelve
a `#chat-general` (o al canal del sistema si no existe). Un hilo archivado
vale: Discord no lo tiene en la caché del bot, así que se pide a la API, y al
publicar se desarchiva solo. En `.cambios` el hilo se nombra con su mención o
su ID; uno privado necesita que el bot esté dentro. Los avisos viejos con el botón 📜 Leído contestan que
el botón ya no hace nada.

**Si la música deja de funcionar** antes de la reconstrucción semanal, casi
siempre es que `yt-dlp` se ha quedado anticuado (YouTube cambia a menudo).
Reconstruye sin caché para traer la última versión:
`docker compose build --no-cache && docker compose up -d`.

**Datos y copia de seguridad:** niveles y estadísticas viven en el volumen
`bot-jovani-vazquez-data` (sobrevive a reconstrucciones y actualizaciones).
Para copiarlo:
```bash
docker run --rm -v bot-jovani-vazquez-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/bot-data.tgz -C /data .
```
La cola de música está en memoria y se pierde al reiniciar el contenedor.

## Pruebas y calidad

```bash
pytest
ruff check .
```

Las pruebas no requieren un token real ni conexión a Discord: se aíslan
mediante dobles de prueba (`unittest.mock`). Corren en paralelo, un proceso
por núcleo (`pytest-xdist`, configurado en `pyproject.toml`); `pytest -n 0`
las corre en uno solo, para depurar.

## Estructura del proyecto

```text
src/bot/
├── __main__.py        # Punto de entrada: python -m bot
├── app.py              # Construcción y ciclo de vida del cliente
├── config.py           # Lectura y validación de configuración
├── logging_config.py   # Configuración centralizada de logging
├── cogs/                # Comandos y eventos agrupados por dominio
│   ├── general.py       # Comandos generales (ping, help)
│   ├── admin.py         # Moderación solo para administradores
│   ├── deploy.py        # reinicio: pide al NAS que actualice y avisa del resultado
│   ├── errors.py        # Mensajes claros ante errores de comandos (/ y .)
│   ├── message_stats.py # Recuento de mensajes y niveles
│   ├── welcome.py       # Bienvenidas (GIF, frases, botón 👋) y despedidas
│   ├── images.py        # Comandos de imagen: magik, memes y los 108 efectos
│   ├── casino.py        # Ruleta con botones, saldo y daily
│   ├── patrimonio.py    # Impuesto sobre el Patrimonio de cada lunes
│   ├── intereses.py     # Intereses diarios del monedero y liquidación del ahorro
│   ├── donations.py     # donar: ONGs de broma y donativos deducibles
│   ├── bizum.py         # bizum: transferencias entre miembros, exentas
│   ├── shop.py          # tienda y mochila: escaparate, caja con IGIC, alquileres
│   ├── shop_admin.py    # Trastienda de `catalogo`: panel y formularios
│   ├── pets.py          # mascota: panel, cuidados, apariciones y cameos en otros cogs
│   ├── work.py          # pala: panel del curro, minijuego con botones, nóminas
│   ├── blackjack.py     # Blackjack con botones (bj)
│   ├── slots.py         # Tragaperras: botones, re-giro, doble o nada, giro del día y bote
│   ├── crash.py         # Crash: cohete compartido por canal, rondas seguidas
│   ├── mines.py         # Minas: tablero de 5×5 con botones (componentes v2)
│   ├── chicken.py       # Pollo: carretera con botones, GIF por paso y autocobro
│   ├── coin.py          # moneda: cara o cruz con botones, doble o nada y canto
│   ├── bus.py           # guagua: Ride the Bus con botones, cobro y precarga de los GIF
│   ├── craps.py         # dados: craps con Pase, No pase, punto, Odds y la mano
│   ├── pachinko.py      # Pachinko con botones, Ráfaga, Auto y turbo
│   ├── horses.py        # caballo: carrera por canal, parrilla, boletos y Gran Premio
│   ├── porras.py        # porra: panel, apuestas, cierre, reparto y recuperación
│   ├── lottery.py       # loteria: panel con pestañas, compras, rascas y sorteos
│   ├── achievements.py  # Logros: seguimiento, premios, avisos y su vista
│   ├── perfil.py        # `perfil`: nivel, patrimonio, trabajo, logros, rachas y objetos
│   └── music.py         # Comandos de música y control por servidor
├── utils/
│   └── responder.py     # Adaptador común: misma lógica para / y .
├── services/            # Lógica de negocio pura, sin discord.py
│   ├── levels.py        # Cálculo de niveles y progreso
│   ├── achievements.py  # Catálogo de logros y qué cuenta cada jugada o mensaje
│   ├── economy.py       # Yapdollars: única puerta al dinero del bot
│   ├── taxes.py         # IRPF, nómina, Patrimonio, IGIC, loterías, donativos y ahorro
│   ├── interest.py      # Cuenta remunerada: saldo medio, tramos, rachas y liquidación
│   ├── work.py          # Reglas de pala: batería, jornada, familia, ascensos
│   ├── work_catalog.py  # Oficios, puestos, contenido de minijuegos y eventos
│   ├── work_games.py    # Minijuegos de pala: cavar, detectar, memoria, diálogo
│   ├── work_tools.py    # Herramientas de curro de la tienda y su efecto en pala
│   ├── pala.py          # Casos de uso de pala: fichar, cobrar, ascender, café
│   ├── lottery.py       # Loterías del Estado: reglas, probabilidades y reparto
│   ├── donations.py     # Catálogo de ONGs y texto de la deducción
│   ├── shop.py          # Reglas de la tienda: precio en caja, rebajas, factura
│   ├── shop_catalog.py  # Surtido de serie de la tienda y sus pasillos
│   ├── shop_uses.py     # Lo que hacen los objetos al usarlos desde la mochila
│   ├── pets.py          # Reglas de las mascotas: vínculo, cuidados, momentos y cameos
│   ├── pets_catalog.py  # Especies de mascota: personalidad, frases y trucos
│   ├── roulette.py      # Reglas de la ruleta americana (apuestas y pagos)
│   ├── blackjack.py     # Reglas del blackjack (zapato, manos, banca, pagos)
│   ├── cards_render.py  # Imagen de la mesa de blackjack
│   ├── roulette_render.py # Rueda de Pillow, de reserva si no hay navegador
│   ├── roulette_scene.py # Ruleta con canvas en Node o Chromium (assets/ruleta/escena.html)
│   ├── slots.py         # Rodillos, premios, giros gratis y máquina caliente
│   ├── autoplay.py      # Autoplay común: bucle, tope, límite de pérdidas y Parar
│   ├── slots_render.py  # GIF de cada tirada con piezas precalculadas
│   ├── crash.py         # Punto de explosión, curva del cohete y ronda
│   ├── crash_render.py  # Gráfica PNG de cada ronda de Crash
│   ├── mines.py         # Multiplicadores exactos y partida de Minas
│   ├── chicken.py       # Pollo: dificultades, multiplicadores y carril del atropello
│   ├── chicken_render.py # Línea de tiempo del Pollo y dibujo de reserva con Pillow
│   ├── chicken_scene.py # Pollo con canvas en Node o Chromium (assets/pollo/escena.html)
│   ├── coin.py          # Cara o cruz: lanzamientos, canto, racha y doble o nada
│   ├── coin_render.py   # Vuelo de la moneda, lo que se ve en cada fotograma y dibujo con Pillow
│   ├── coin_scene.py    # Cara o cruz con canvas en Node o Chromium (assets/moneda/escena.html)
│   ├── bus.py           # Autobús: cartas, probabilidades exactas y pagos al 99 %
│   ├── bus_render.py    # Autobús: lo que se ve en cada fotograma y dibujo con Pillow
│   ├── bus_scene.py     # Autobús con canvas en Node o Chromium (assets/autobus/escena.html)
│   ├── craps.py         # Dados: salida, punto, Odds a la probabilidad exacta y la mano
│   ├── craps_render.py  # Física de la tirada, cámara, dados en 3D y dibujo con Pillow
│   ├── craps_scene.py   # Dados con canvas en Node o Chromium (assets/dados/escena.html)
│   ├── browser_scene.py # Pestaña de Chromium sin ventana con una escena HTML cargada
│   ├── node_scene.py    # La misma escena pintada en Node con Skia, sin navegador
│   ├── pachinko.py      # Tableros, clavos, bolsillos, sorteo, rush y retorno exacto
│   ├── pachinko_render.py # GIF neón de cada tanda, con un tema por tablero
│   ├── pachinko_physics.py # Clavos, paredes y caída con física de las bolas del pachinko
│   ├── pachinko_motion.py # Movimiento de una tanda: las bolas chocan y cada una acaba en su bolsillo
│   ├── horses.py        # Establo, simulación por tramos, cuotas, boletos y Gran Premio
│   ├── horses_render.py # Dibujo con Pillow (plan B) y ritmo de los fotogramas
│   ├── horses_scene.py  # Caballos: carrera en Node, parrilla y boleto en Chromium
│   ├── porras.py        # Propuestas, tope del bote y reparto mutuo de las porras
│   ├── deploy.py        # Buzón con actualizar.sh para el comando reinicio
│   ├── moderation.py    # Duraciones, IDs y jerarquía de roles de los comandos de admin
│   ├── welcome.py       # GIF de bienvenida, frases y reglas del botón 👋
│   ├── image_input.py   # Lectura validada de imágenes de usuario (límites, EXIF)
│   ├── magik.py         # Seam carving con Pillow y numpy (testable sin Discord)
│   ├── memes/           # Efectos de Dank Memer: registro, utilidades y efectos
│   ├── music.py          # Pistas, cola y límites (testable sin red)
│   └── music_source.py   # Extracción de audio con yt-dlp (bloqueante)
└── assets/
    ├── bienvenida.mp4  # Vídeo adjunto al mensaje de bienvenida
    ├── bienvenidas.txt # Frases de bienvenida y de vuelta, editables sin tocar código
    ├── despedidas.txt  # Frases de despedida, editables sin tocar código
    ├── caballos/       # escena.html: canvas y HTML/CSS de las carreras de caballos
    ├── memes/          # Plantillas y fuentes de los efectos (de imgen, MIT)
    └── slots/          # Símbolos de la tragaperras (Noto Emoji, ver LICENSE.txt)
```

En la raíz: `Dockerfile`, `docker-compose.yml`, `.env.example` y
`actualizar.sh` (autoactualización en el NAS) para el despliegue.

Consulta [Biblia.txt](./Biblia.txt) para el detalle completo de la
estructura de referencia, los límites entre capas y las normas de
seguridad, rendimiento y documentación.
