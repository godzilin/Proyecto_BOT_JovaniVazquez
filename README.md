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
- `node` en el `PATH` para la música: `yt-dlp` lo usa para descifrar las URL
  de audio de YouTube (la imagen Docker ya lo trae).
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
   blackjack, tragaperras, pachinko, lotería, porras y apuestas deportivas) a
   esos canales: IDs separados por comas. En nuestro servidor, `#casino` es
   `1384280704539562054`. Vacío = se puede jugar en cualquier canal.
5. `ODDS_API_KEY` (opcional, secreta) es la clave de [The Odds API](https://the-odds-api.com)
   para las apuestas deportivas (`deportes`). El plan gratis (500 créditos al mes)
   basta para LaLiga y la Champions: el bot gasta unos 390 (unos 330 en el 1X2 y
   los resultados y unos 60 en el más/menos de los mercados de goles). Sin ella,
   `deportes` avisa de que no está configurado.

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
  | 👤 Perfil | `pala` · `perfil [miembro]` · `ranking [pagina]` |
  | 🎰 Casino | `4enraya [miembro] [cantidad]` · `allatu [cantidad] [tabla]` · `apuestas [miembro]` · `backgammon [miembro] [cantidad]` · `bacara [cantidad] [lado]` · `blackjack [cantidad]` (atajo `.bj`) · `caballo [cantidad] [caballos] [tipo]` · `cohete [cantidad] [auto]` · `dados [cantidad] [apuesta] [mesa]` · `deportes` · `gallos [cantidad] [miembro]` · `guagua [cantidad]` · `guerra [cantidad]` · `loteria` · `minas [cantidad] [minas]` · `moneda [cantidad] [lado]` · `pachinko [cantidad]` · `picar` · `poker [modo] [cantidad]` · `pollo [cantidad] [dificultad] [autocobro]` · `porra [miembro] [juego] [propuesta] [jugadas] [apuesta]` · `ruleta [cantidad] [apuesta]` · `torre [cantidad] [nivel]` · `tragas [cantidad] [máquina]` |
  | 🏦 Dinero | `banco [mostrador]` · `bizum <miembro> <cantidad> [concepto]` · `donar [ong] [cantidad]` · `fortunas` · `hacienda [miembro]` · `imv` · `patrimonio [miembro]` · `renta` · `saldo [miembro]` |
  | 🛍️ Tienda | `mascota [miembro]` · `pieles [miembro] [imagen]` · `rastro` · `tienda` · `trueque <objeto> <miembro> <a cambio> [dinero]` · `vender <objeto> <precio> [comprador]` |
  | 🎵 Música | `cola` · `parar` · `pausar` · `poner <consulta>` · `quitar <posicion>` · `saltar` · `seguir` · `vaciar` · `volumen <1-200>` |
  | 🍻 Comunidad | `babel <texto \| @miembros #canales>` · `beernight [sonido] [archivo]` · `cumple [dd/mm] [miembro]` · `cumples` · `entrada [archivo] [volumen] [borrar]` · `hongkong` · `jovani` · `lista [tarea] [prioridad alta\|media\|baja]` |
  | 🎨 Imagen (solo `.`) | `magik [miembro]` · `memes [efecto]` · 108 efectos (`.memes`) |
  | ⚙️ General | `ayuda` · `latencia` |
  | 🛡️ Admin | `abrir` · `admin` · `apodo` · `aspecto` · `banear` · `bienv` · `borrar` · `callar` · `cambios` · `catalogo` · `cerrar` · `decir` · `echar` · `hablar` · `indultar` · `lento` · `informe` · `niveles` · `robuso` · `rol` · `tajo` |

  La ayuda es un menú desplegable, como el de `perfil`: una portada con las
  categorías y una página por categoría con cada comando y lo que hace (la
  descripción de su slash command). Los efectos de imagen van en bloque, por
  tipo, en la página de Imagen. La categoría Admin solo la ve quien es
  administrador, y el menú solo lo maneja quien lo abrió.
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
  más 100 por cada día seguido (tope 1.500 Y$; se cobra una vez por día natural,
  que empieza a medianoche en hora canaria, y la racha se pierde si te saltas un día). Lo cobrado trabajando con `pala` lo reduce (ver Trabajo).
- **Cumpleaños:** `cumple 14/02` guarda el tuyo (solo día y mes; una vez
  puesto, solo un administrador lo cambia) y `cumples` lista los próximos.
  Ese día (hora canaria) el bot lo anuncia en `#chat-general` y el
  cumpleañero recibe 3.000 Y$. Felicitarle, con el botón 🎉 o con un mensaje
  que le mencione o le responda y suene a felicitación, da 500 Y$ a quien
  felicita y 100 Y$ más al cumpleañero, una vez por persona. Si existe un rol
  llamado `🎂 Cumpleañero`, el bot se lo pone durante el día (necesita
  **Gestionar roles**). Los regalos pagan IRPF con retención (ganancia
  patrimonial, art. 33.1 LIRPF), aunque solo muerde a quien ya pasa del mínimo.
  El 7 de octubre, aniversario del servidor, el bot lo anuncia y el XP va
  doble todo el día.
- **IRPF y Hacienda:** los premios, las nóminas, los alquileres y la
  ganancia neta diaria del casino tributan; el IMV está exento, como el real
  (art. 7.y LIRPF). Toda la renta va con una sola vara, 250 Y$ por euro: la
  semana es el año fiscal (la base de la semana × 52 / 250 da los euros del
  año) y se aplican la escala estatal y la canaria (Ley 9/2025). Cada cobro
  retiene con el mayor entre el tipo de la semana anterior y el de esta
  (arts. 82 a 87 RIRPF), así que el tipo no baja a mitad de semana. En el
  casino se retiene un 19 % fijo de lo ganado en el día si pasa de unos 1.442
  Y$ (los 300 € del art. 75.3.f RIRPF; si no, nada): si pierdes después
  de ganar, Hacienda te devuelve parte en la misma jugada.
- **Campaña de la Renta:** cada lunes se cierra la declaración de toda la
  renta de la semana anterior: trabajo, pisos (inmueble a inmueble), juego
  (los días con pérdidas compensan a los que ganan), premios y autónomos,
  con las deducciones de donativos, las canarias de vivienda y alquiler y la
  de Hong Kong. Si sale a pagar, al abrirla se paga entera o fraccionada (el
  60 % al momento y el 40 % el jueves, sin recargo); hay hasta el final del
  lunes y, si no se elige, se cobra entera. Lo que no llegue queda como deuda
  con Hacienda: se embarga el 30 % de lo que se cobre hasta saldarla y se
  puede liquidar con el botón de `renta`. Si sale a devolver, primero compensa
  deudas y el resto
  se presenta con el botón de `renta` o con el aviso (solo lo ves tú) que
  sale la primera vez que juegas en la semana, durante 4 semanas (los «4
  años» de la ley). `renta` enseña un sobre cerrado con «📑 Abrir»: dentro,
  la carta dibujada (de lo que ganaste a tu base, la cuota frente a lo
  retenido, el sello con el resultado y la tabla por conceptos) y cuatro
  pestañas: 📄 Resumen, 🧩 Desglose (cada fuente con su parte de la cuota y
  si faltó o sobró por retener), 📜 Paso a paso con los artículos y
  🤝 Consejo (cuánto habría rendido donar esa semana, con el botón 🤝 Donar).
  Pagar o presentar pone su sello en la carta. Al
  presentar, el bot lo anuncia en el canal. El bot crea un evento de Discord
  por campaña (necesita **Gestionar eventos**). Detalle y fuentes en
  `src/bot/services/taxes.py`. Cada cobro
  muestra una línea pequeña con lo que se lleva Perro Sanxe. Todo lo
  retenido entra en la cuenta del Estado (`user_id = 0` en
  `economy_wallets`), y `hacienda` muestra a cualquiera, en un panel con pestañas, su
  saldo, lo recaudado este año y desde siempre, lo que deja cada impuesto y quién más
  paga: cada miembro, los caseros y los empresarios. Todo movimiento queda en
  un libro (`economy_ledger`) y se aplica de forma atómica: dos clics a la
  vez no pueden gastar dos veces el mismo dinero.
- **Impuesto sobre el Patrimonio:** cada lunes se cobra lo que pase de
  7.000.000 Y$ (los 700.000 € reales a 10 Y$ por euro) entre efectivo, pisos y
  objetos de la tienda, con los tipos reales (0,2 % a 3,5 %, art. 30 de la Ley
  19/1991) y el límite conjunto con el IRPF de la semana (art. 31: los dos
  juntos no pasan del 60 % de la base, pagando al menos el 20 % de la cuota).
  Quien pasa de 37.000.000 Y$ declara además las Grandes Fortunas, que en
  Canarias salen a cero. `saldo` avisa de lo que te tocaría y el bot anuncia quién ha pagado. La primera semana tras
  desplegarlo no cobra.
- **Pisos (`pisos`):** un panel que es solo imágenes con el mercado inmobiliario
  de 20 barrios de Gran Canaria y Tenerife, cada uno con su carácter (180
  inmuebles de 19 tipos, del trastero a la cueva o el amarre, uno de cada en el
  servidor, a precio real × 10). Se compran participaciones (del 1 % al inmueble
  entero) con su ITP del 6,5 %, los inquilinos ficticios pagan cada noche un mes
  de alquiler (un día del bot es un mes) con su IRPF, IBI y comunidad, y se vende
  al mercado con su plusvalía. Comprar sube el precio del barrio. También hay
  hipoteca al Euríbor (con ejecución si no pagas, el aval de tus padres desde
  `pala` y la frase del BCE cada lunes), VPO por sorteo con aval del Estado, vivir
  en tu piso, alquilárselo a otro miembro con fianza, tasa de esfuerzo y Bono
  Alquiler Joven, licencia vacacional con IGIC, la Ley de Vivienda, el fondo
  buitre, la SOCIMI y la obra nueva que levanta cada lunes la rama Ladrillo de
  `pala` (una promoción por barrio, con su comisión de promotor).
- **Cuenta remunerada:** cada día el monedero paga intereses sobre el saldo
  medio del día anterior, por tramos: 2,5 % hasta 4.000 Y$, 1,25 % hasta 20.000
  y 0,4 % hasta 70.000 (500 Y$ brutos al día como mucho). Cobran también los
  que no hacen nada. Retención del 19 % en cada pago (art. 101.4 LIRPF) y, cada
  lunes, liquidación con la escala del ahorro (arts. 66.1 y 76 LIRPF), que
  con el tope diario siempre se queda en el 19 % y sale a cero. El aviso
  llega una vez, en la siguiente acción con dinero; `saldo` enseña lo de ayer y
  lo que va saliendo hoy.
- **Saldo (`saldo`):** además del saldo, lo que tienes pendiente con su fecha:
  ⏳ por pagar (deuda con Hacienda, la Renta y su segundo plazo, el Patrimonio del
  lunes, hipoteca, alquiler y recibos de esta noche, derramas, cuota de autónomo,
  lo que le debes a tu madre), 📥 por cobrar (IMV y pensión de hoy, devoluciones
  de la Renta, rentas de esta noche, boletos por sortear) y 🔒 retenido (pujas y
  fianzas). La Renta sin abrir sale sin cifra. El saldo de otro se ve entero, igual
  que si lo pidiera él.
- **IGIC:** el impuesto al consumo es el canario, no el IVA. Lo pagan las
  compras de la tienda, al tipo de cada artículo: cero, reducido (3 %),
  general (7 %, el de por defecto), incrementado (9,5 %) o de lujo (15 %),
  como en los arts. 51 a 59 de la Ley 4/2012 de Canarias.
- **Tienda** (`tienda [buscar]`): El Colmado de Jovani. Escaparate en lista
  compacta (15 artículos por página, una línea cada uno con su precio y
  etiquetas: ✅ lo tienes, 🛒 en el carrito, ⭐ en tu lista, 📦 quedan, 🔒 nivel,
  🔥 lo más vendido, 🆕 nuevo), con desplegables de sección (🎭 roles, ⚡ XP,
  🐾 mascotas, 💎 vitrina, 🫳 para usar, 🎨 skins, ⭐ lista de deseos), pasillo
  (con cuántos tienes de cada uno) y orden, filtros (ofertas, novedades, lo que
  te llega, lo que tienes o te falta) y buscador. Elegir algo abre su **ficha**
  privada: cantidad, ticket con su IGIC, **Pagar**, al carrito y ⭐. Elegir
  varios los echa al **carrito**, que se paga todo junto o nada, con el
  impuesto desglosado por tipo en la factura simplificada. Al pagar sale un
  aviso público en el canal. Cualquiera puede comprar desde el escaparate de
  otro: la ficha y el carrito se abren solo para quien pulsa.
  - **Lista de deseos** pública (hasta 25 cosas, también en `perfil`): avisa
    por mensaje privado de rebajas, temporadas, reposiciones, puestos del
    Rastro y de cuándo ya te llega el dinero. Se silencia con 🔕.
  - Roles para siempre o alquilados (volver a comprar alarga el alquiler; al
    vencer, el bot los quita). Si no puede dar el rol, devuelve todo, IGIC
    incluido.
  - Potenciadores: multiplican el XP de mensajes y voz (de ×1,1 a ×3) un
    tiempo; si ya tienes uno, el nuevo se pone a la cola.
  - Coleccionables: de capricho; con existencias limitadas, cada unidad sale
    numerada ("nº 3 de 10").
  - Títulos (15): coleccionables que se lucen junto a tu nombre en `perfil`,
    `fortunas` y `ranking`, de la ESO aprobada (2.000 Y$) a la Grandeza de
    España (20 millones, una sola). Comprar uno te lo pone; en la mochila se
    elige cuál llevas o ninguno. No dan dinero ni XP: son para presumir.
  - Las compras de 100.000 Y$ o más se anuncian con un pregón en el canal.
  - Skins (pestaña 🎨 Skins): cambian el aspecto de una mesa del casino sin
    tocar cómo se juega. Los tapetes valen para todas las mesas con fieltro
    (ruleta, autobús, bacará, dados, moneda, pollo, guerra, blackjack, póker y
    backgammon) y cada mesa lleva el suyo:
    granate, de oro, rojigualda, de Israel, del Teide, monoestrellado y el
    🖼️ Tapete propio (100.000 Y$, nivel 10), que lleva la imagen que subas tú. Los
    gallos tienen las suyas. Se compran (también desde el 🎨 de la mesa), se
    ganan con logros o se venden solo en su temporada; se cambian en la
    mochila, con el botón 🎨 de la mesa o en `pieles`. Las
    reglas para añadir skins y abrir más juegos están en la Biblia.
- **Pieles** (`pieles [miembro] [imagen]`): un panel privado con todas tus
  skins, las de ahora y las que vengan: qué lleva cada mesa, un menú para
  cambiarla y, para el 🖼️ Tapete propio, la imagen que lleva con 🖼️ Subir
  imagen (un formulario con archivo) y 🗑️ Quitar imagen. Con `imagen` (o
  `.pieles` con la imagen adjunta) se sube sin abrir el panel. PNG, JPG, WebP
  o GIF de hasta 10 MB; el bot la reduce a 960 px, le quita los metadatos (el
  GPS de la foto, por ejemplo) y la guarda en JPEG. Se ve en la mesa desde la
  siguiente jugada, sin revisión previa; los administradores pueden abrir las
  pieles de otro con `miembro` para cambiarle o quitarle la imagen.
  - Surtido de serie: 266 artículos a la venta (229 objetos, 15
    potenciadores y 22 mascotas) que se meten solos en cada servidor la
    primera vez que se abre la tienda, repartidos en 15 pasillos por tema (La
    Moncloa, Ventanilla, Canarias, Typical Spanish, Fiestas y verbenas,
    Supersticiones, Bazar de todo a 100, Rincón boricua, Importación de Hong
    Kong, Vida de rico, Lo que no debería venderse, Tienda de animales,
    Ferretería del curro, Peña de la porra y Detalles para la familia). Del
    Falcon (50 millones, una unidad) a la piedra (1 Y$), cada uno con el IGIC
    que le tocaría de verdad. Lo que se come lleva 🍽️ (las mascotas se lo
    comen). Lo que un administrador retire no vuelve solo. Las reglas para
    añadir artículos (tipo por lo que hace, pasillo por su tema) están en la
    Biblia.
  - Cajas (7): 📦 Caja de cartón (1.000 Y$), 🎁 Caja de regalo (5.000), 💼 Maletín del
    ministro (25.000) y 👑 Cofre del Rey emérito (100.000, nivel 10) se abren desde la
    mochila con una animación corta y dan piezas de colección: 82 coleccionables en 11
    colecciones (ocho normales de 8 piezas, como los Cromos de la Transición, los
    Bobbleheads de líderes mundiales o las Pegatinas de Hacienda, y tres de temporada
    de 6) con rareza Común, Rara, Épica, Legendaria y Mítica, que solo salen de cajas y
    se pueden vender y cambiar en el Rastro. El maletín y el cofre pueden dar, rarísimo,
    un tapete del casino. Cada caja devuelve de media un 95 % de lo que cuesta a valor de
    catálogo. 🎄 Navidad (dic. y ene.), 🎃 Halloween y 🎂 aniversario (oct.) cuestan 5.000
    Y$, solo se venden en su temporada y sacan antes su colección. Una 📦 gratis cada 100
    jugadas del casino y una 🎁 cada 5 niveles. Comprar paga IGIC; abrir no mueve dinero.
    La caja botín de siempre ya no se vende, pero se sigue abriendo.
  - Objetos que se usan (60): desde la mochila, contra alguien o sin más:
    huevos, tomates, burofax, multas de la DGT, el chivatazo a la UCO,
    Pegasus, indultos, bulos, la encuesta del CIS, pimientos de Padrón, la
    bola 8, el d20, el megáfono, el DNI falso (cambia el apodo), la llamada a
    Robuso (contesta según la hora real de Hong Kong) y las cajas (arriba). El resultado sale en el canal y menciona a quien
    lo recibe. Los de un solo uso se gastan; el resto tiene una espera entre
    usos. Usar no mueve dinero.
  - La mochila (🎒 en el escaparate o en la sección 🎒 Objetos de `perfil`)
    enseña lo que tiene alguien; su dueño puede ponerse y quitarse los roles
    que compró para siempre, elegir qué título luce, cambiar sus skins y usar sus
    objetos.
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
- **Trabajo** (`pala`): coges la pala y curras. Ocho oficios (🦺 obra, 🍽️
  hostelería, 🌹 política en el PSOE, 🏥 sanidad, 💻 oficina, 🛵 reparto, 🗂️
  funcionariado e 🤳 influencer) con la carrera larga: los ocho tienen 5 puestos de
  tronco y dos ramas de 5 más (10 en total), también la oficina. La política va de pegacarteles a portavoz y sigue por 🏛️ Moncloa (hasta
  consejero de una eléctrica) o 🇪🇺 Bruselas (hasta comisario europeo); la obra
  va de peón a constructor y sigue por 🏖️ Ladrillo (hasta presidente de un club
  de fútbol) o 🚄 Obra pública (hasta presidente de una constructora del IBEX); la
  sanidad va de celador a médico adjunto y sigue por 🏛️ Sanidad pública (hasta
  consejero de Sanidad) o 💉 Privada (hasta dueño de una aseguradora de salud); la
  hostelería va de friegaplatos a dueño del chiringuito y sigue por 🏨 Hotelera
  (hasta presidente de la patronal turística) o ⭐ Alta cocina (hasta gurú
  gastronómico con franquicias en Dubái); el reparto va de rider en bici a
  fundador de la app y sigue por 📮 Correos (hasta presidente de Correos, en
  nómina) o 🚀 Imperio logístico (hasta multimillonario con cohete propio,
  autónomo; la valoración de la app solo cuenta en el tronco);
  el funcionariado va de interino a
  inspector de Hacienda y sigue por 🏛️ Alta Administración (hasta consejero de
  Estado, en nómina, con plaza fija y trienios) o 💼 Puerta giratoria (hasta
  asesor de fortunas en Andorra, autónomo, sin plaza fija ni trienios); y el
  influencer va de tiktoker de barrio a celebrity de reality y sigue por 📺 Tele
  (hasta presidente de RTVE, en nómina de la cadena) o 🇦🇩 Andorra (hasta fundador
  de un partido antisistema, autónomo); y la oficina va de becario a CTO de
  startup y sigue por 🏢 Consultora (hasta socio director, en nómina de la firma:
  horas facturadas al Ministerio, licitaciones y la app pública que no funciona) o
  🦄 Unicornio (hasta tecnomillonario con red social propia, autónomo y con el 30 %
  del sueldo en stock options).
  Cada turno es un minijuego de decidir rápido, de
  30 a 60 s según el puesto (cavar leyendo el plano, pillar al que se escaquea,
  memorizar comandas o votaciones, esquivar preguntas en rueda de prensa,
  mandar cada papel a su negociado, elegir el pedido que más paga por
  kilómetro), y la
  nota mueve el sueldo entre el 70 % y el 130 %. Cada turno es una nómina de
  verdad: Seguridad Social del trabajador (6,5 %), IRPF con la reducción por
  rendimientos del trabajo y la cotización de la empresa (32 %), que va al
  Estado igualmente. Batería que se gasta y se recarga sola (o con un
  barraquito), 4 turnos ordinarios al día, 2 extras legales a la semana y,
  después, extras en B con riesgo de Inspección, que sube si trasnochas. Ascensos al estilo de los Sims
  (barra de rendimiento, días en el puesto, tareas y formación), sin despidos.
  En la Ferretería del curro de la `tienda` hay 25 herramientas (casco,
  libreta de comandas, pinganillo, vademécum…) que, con tenerlas, ayudan en
  el minijuego de su oficio: más tiempo, un fallo gratis por turno, un seguro
  para las tuberías o una respuesta mala tachada.
  La familia ❤️ es el freno al que encadena turnos: cada turno cuesta, se
  recupera sola por horas y, según su tramo (de 🏡 en casa feliz a 💔 en
  crisis), la batería recarga más rápido o más lento y la barra sube más o
  menos. Se sube con los regalos del pasillo 🏠 Detalles para la familia, que
  el desplegable ❤️ del panel da si los tienes o compra y da en el acto si no, y se gasta pidiendo favores: el tupper de tu
  madre, que te cubra el saliente, la excusa para librarte de un marrón, un
  Bizum de mamá que se devuelve con la mitad de cada turno o que tus padres te
  avalen la hipoteca o el alquiler de `pisos`.
  Un turno del puesto más bajo, unos 1.700 Y$, cunde algo más que un IMV a
  racha máxima. Cada oficio fija sus tres primeros sueldos y el resto sale de
  una curva común: cada puesto de rama cobra un 24 % más que el anterior en la
  rama segura y un 34 % en la arriesgada, y pide de 13 a 21 días fichando
  (análisis de los sueldos en `docs/economia-trabajo.md`; simulador en
  `docs/economia_sim.py`).
  El IMV se reduce 1 Y$ por cada 20 Y$ netos de nómina que pasen de 11.506 a la semana
  (incentivo al empleo del RD 789/2022), pero nunca baja del 20 %. Eventos con
  dos opciones (sobres, enchufes, la UCO, la comunión del sobrino). En
  sanidad se rinde con **guardias** (turno doble que paga 1,6 veces y te deja
  saliente 12 h; el residente tiene guardias mínimas), también en sus ramas. En oficina se puede
  **teletrabajar** (cansa menos, rinde menos y te escriben a las once), el CTO
  y toda la rama Unicornio cobran parte en **stock options** (exit o quiebra: las
  que ya tienes siguen vivas aunque cambies de puesto) y desde programador senior
  te puedes **ir a Hong Kong**: sueldo doble, MPF y salaries tax, exención del
  art. 7.p LIRPF mientras sigues siendo residente, sin IMV, y Ley Beckham si
  vuelves tras 5 «años» fuera. En el **reparto** cada hora hace un tiempo
  (con lluvia los pedidos pagan ×1,4, pero puedes caerte), la valoración de la
  app (de 3,5 a 5 estrellas) multiplica el sueldo y por debajo te desconecta 6
  h, y los dos primeros puestos son falsos autónomos con tarifa plana. En el
  **funcionariado** hay que **estudiar la oposición** (turnos sin sueldo que
  suman temario, con preguntas de la Constitución y la Ley 39/2015 de
  verdad; solo en el tronco, del interino al técnico), desde auxiliar tienes
  plaza fija (no te bajan nunca y la nota mueve poco el sueldo) y cada 3 «años»
  un trienio (+5 %, hasta 8); en la Puerta giratoria pides la excedencia
  voluntaria y no tienes ni plaza fija ni trienios. El **influencer**
  cobra a golpes: casi siempre se queda corto y a veces se hace viral (hasta
  ×15), suma seguidores y sufre funas, y eso en todo el oficio, también en la
  tele (tertulias, audiencias, consejos por cuotas) y en Andorra (bebidas
  energéticas, veladas de boxeo, cripto y un partido). **Carrera larga**, como en los Sims: al
  acabar el tronco se elige rama; cada puesto da una ventaja 🏅 para siempre
  (más tiempo, un fallo gratis, menos cansancio…); en lo más alto de una rama
  te puedes **jubilar** y seguir por la otra, con una sola **pensión** diaria
  que crece con cada rama completada en cualquier oficio, hasta la base máxima
  de cotización, y se cobra con un botón (IRPF sin cotizar, art. 17.2.a LIRPF;
  mientras sigues trabajando, solo una parte las primeras semanas: jubilación
  activa, art. 214 TRLGSS). Cada rama completada en otro oficio da un 10 % más
  de sueldo (hasta un 50 %) y menos días y tareas para ascender; y cada día hay un
  🌠 **deseo del día** (sacar un 90, fichar de madrugada…) que paga ×1,25 y
  sube la barra, con racha. Los ascensos de las ramas, las jubilaciones, los
  exits y los vídeos virales se anuncian en el canal. Un administrador limita
  los canales con `tajo`.
- **Negocios** (dentro de `pala`, 8.14.0): `pala` abre un inicio con la imagen de
  tu día en 8 **franjas**, el tiempo que comparten el curro y tu negocio: la jornada
  reserva las suyas aunque no fiches y cada hora extra se la quitas al negocio.
  Acabadas, se pueden trasnochar 4 más, con más riesgo de Inspección para el B. Hay
  reducción de jornada y excedencia voluntaria (sin dinero). Desde 🏪 **Negocio** se
  monta una ☕ cafetería, 🛒 tienda o 🔧 taller en un barrio de `pisos`, con nombre,
  colores y emblema propios. Eres autónomo y respondes con lo tuyo: el local, los
  suministros y la cuota corren aunque no abras. Cada turno gasta una franja y es un
  minijuego activo, con clientes que se cansan de esperar y habituales que piden «lo
  de siempre». El panel entero son escenas de Node. Desde la 9.11.0, una cadena de
  hasta 4 locales (una sola sociedad para todos), una nave para importar de la
  Península con su AIEM, gerentes que reponen y vender la sociedad. Diseño y fases
  en `docs/negocios.md`; equilibrio en `docs/negocio_sim.py`.
- **Bizum** (`bizum`): manda yapdollars a otro miembro al momento. Llega
  entero: exento de Donaciones y sin IRPF (en la vida real, entre amigos se
  pagaría; el bot trata a todo el servidor como familia directa). Mínimo 5 Y$
  (0,50 €). Los máximos de Bizum (10.000 Y$ por operación y 20.000 Y$ al día)
  no se aplican: el bot avisa de que te has pasado y te da el logro
  *A espaldas de Sánchez*. El resultado es público y menciona a quien recibe.
- **Rastro** (`vender`, `trueque`, `rastro`): segunda mano entre miembros.
  `vender` sin nada abre tu panel 🧺 Vender: eliges algo de tu mochila, ves su
  valor de referencia y lo que da el Monte de Piedad, y lo mandas a un miembro,
  al Rastro, al Monte o lo empeñas (tus papeletas se desempeñan desde ahí). Con
  `vender <objeto> <precio> <comprador>` le haces una oferta (o se lo regalas
  con precio 0); sin comprador, lo pones en un puesto con precio de salida y,
  si lo quieren varios, se subasta: cada puja sube al menos un 5 % y da una
  hora más. `rastro` enseña los puestos con su botón 🔨 Pujar. Solo se venden
  coleccionables y tapetes, y quien compra tiene que cumplir el nivel mínimo y
  el límite por persona del artículo. Quien compra paga el 5,5 % de ITP sobre
  el mayor entre el precio y el valor de la tienda (vender a 1 Y$ no lo baja;
  la parte regalada está exenta, como el Bizum) y quien vende con ganancia
  paga el 19 % de la plusvalía. Con `trueque <tu objeto> <miembro> <su objeto>
  [dinero]` le propones cambiar un objeto por otro, con dinero encima si
  quieres: cada uno paga el ITP de lo que recibe y la plusvalía de lo que da.
- **Banco** (`banco`): un panel con cinco mostradores. **Negocio**: préstamos a
  nombre de tu negocio de `pala` (también desde su portada), al tipo de una pyme; de
  autónomo los firmas tú y de sociedad los firma ella con tu aval, y los paga la noche
  del negocio. **Garantías**: rehipotecar
  tus pisos (hasta el 80 % de lo que valen hoy si son vivienda, el 60 % si no, menos
  lo que ya debes de ellos; sus intereses no desgravan). **Anticipos**: el banco te
  adelanta 3, 6 o 12 meses de la renta de un piso alquilado y se la cobra cada noche
  (si el inquilino no paga, pagas tú; no se vende con el anticipo vivo). **Avalista**:
  otro miembro puede avalarte un préstamo o una rehipoteca; si no pagas, el banco le
  reclama a él y tú le debes lo que pague. **Préstamos** personales para
  lo que quieras (también la entrada de un piso): todas tus cuotas juntas,
  hipotecas incluidas, no pasan del 35 % de lo que ingresas; el tipo es el Euríbor
  más un diferencial que depende de tu perfil (préstamos devueltos, inmuebles,
  impagos…) y la oferta enseña TIN, TAE, comisión y total, con el seguro de
  protección de pagos opcional. Se puede desistir hasta la primera cuota y
  amortizar antes con la compensación legal (1 % o 0,5 %). Quien no paga entra en
  el fichero de morosos (ni préstamos ni hipotecas) y la deuda se embarga de la
  nómina por tramos del salario mínimo. **Cuentas**: lo que ha cobrado la tienda,
  lo que ha prestado y cobrado el banco y lo que ha ganado el casino.
- **Monte de Piedad** (en el panel de `vender`): compra lo que compraste en la
  tienda al 85 % de lo que pagaste o te presta el 55 % por empeñarlo (lo
  recuperas devolviendo un 5 % más en 3 días); paga con la caja de la tienda y no
  acepta lo regalado ni lo numerado. Hasta la 9.0.0 era un mostrador de `banco`.
- **Donativos** (`donar`): once ONGs de broma que hacen lo contrario de lo
  que dicen, en un panel con la ficha de cada una y el muro de donantes. Donar
  es gastar (el dinero se queda en la ONG), pero desgrava en la renta del lunes:
  80 % de los primeros 250 € al año (unos 1.202 Y$ a la semana) y 40 % del
  resto, hasta el 10 % de tu base de esa semana. Lo donado a cada ONG te hace
  mecenas suyo (🥉 Bronce a 🏛️ Patrono): cada nivel trae un regalo y más boletos
  en el sorteo que hace cada ONG los viernes a las 20:00 entre quienes le
  donaron esa semana. El premio suele ser algo barato de la tienda y, a veces,
  uno de los regalos exclusivos de la ONG, que no se venden.
- **Ruleta americana** (0 y 00, la casa gana el 5,26 %): `ruleta` abre una
  mesa con botones que solo puede usar quien la abre. Cada clic en una
  apuesta cobra, gira (GIF de ~4 s) y paga. En cada tirada caen de 1 a 5
  ⚡ rayos sobre números al azar, con multiplicadores de ×50 a ×500: un
  pleno con rayo cobra eso y uno sin rayo, 29 a 1. Botones: rojo/negro, par/impar,
  1-18/19-36, docenas, 0 y 00; 🎯 **Números** abre un formulario
  para plenos, caballos, transversales, cuadros, seisenas, la línea
  0-00-1-2-3 y las columnas (`c1`, `c2`, `c3`); 🎨 cambia el tapete. Con ½, ×2 y 💰 All-in se cambia la ficha; 🔁 Repetir y
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
  (~1 s, repartida entre los procesos de Node libres; mientras, la mesa dice «🎲 No va
  más…»), salvo la que repite la última apuesta: esa se precarga mientras
  gira la anterior y sale al instante, como en el pachinko. Si no hay Node,
  la pinta Chromium y, si tampoco, la rueda de Pillow de antes.
- **Blackjack** (`/blackjack`, `.blackjack` o `.bj`): reparte al momento con la apuesta indicada
  (`.bj 500`, `.bj all`) y se juega con botones: 🃏 Pedir, ✋ Plantarse,
  ⏫ Doblar y ✂️ Separar. Si la banca enseña un as, antes salen 🛡️ Seguro y
  Sin seguro. Al terminar, 🃏 Repartir juega otra mano en el mismo
  mensaje y ½ / ×2 / 💰 All-in cambian la apuesta, y 🎨 elige el tapete. La mesa
  se pinta con canvas en Node (`assets/blackjack/escena.html`): cada jugada es un
  GIF con las cartas que salen del zapato y se voltean, la banca que destapa y
  roba con suspense, las fichas que se van a la banca o llegan de ella, el cartel
  con el importe que sube y la racha con su llama. Quien cuenta cartas ve lo de un
  casino: la bandeja de descartes crece (con una muesca por baraja, sin números)
  y el zapato mengua con la carta de corte dentro hasta que sale. Mientras miras
  una jugada, la mesa ya ha pintado la de cada botón, así que al pulsar sale al
  momento. Reglas: 6 barajas que duran entre manos hasta la carta de corte,
  a una baraja del final (se pueden contar cartas, y el jefe de sala baraja si
  subes la ficha más de ×8 con la cuenta alta), la banca se planta
  en 17 (también blando) y mira si tiene blackjack; si lo tiene, pierdes la
  apuesta (empate si tú también lo tienes). Seguro con un as de la banca,
  también si tienes blackjack: cuesta media apuesta y paga 2:1 si ella lo
  tiene; con tu blackjack es el «dinero par»: cobras una apuesta pase lo
  que pase. Blackjack paga 3:2,
  doblar con dos cartas (también tras separar), separar una vez; sin
  rendición ni tope de apuesta. Jugando bien y sin seguro, el juego devuelve ≈ 99,8 % de lo
  apostado y un jugador normal, ≈ 97,3 %; el seguro, ≈ 92 % de lo que se mete en él
  (`docs/rtp_blackjack.py`). La apuesta se cobra al repartir (y al doblar, separar o asegurar) y
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
  o nada con lo cobrado, hasta 5 veces, con una carta que se gira) y 🎁 Giro
  del día (gratis, a 100 Y$ por
  día de racha, hasta 700 Y$). El embed enseña lo que paga cada combinación a
  tu apuesta y los premios cobrados en la sesión; al cerrarse sale el ticket
  con el neto. Premios:
  🍒 al principio devuelve la mitad, 🍒 🍒 ×2, tríos de ×4 a ×200, 🃏 comodín
  y 🃏 🃏 🃏 se lleva el **bote común** del servidor, que crece con el 6 % de
  cada apuesta y vuelve a 5.000 Y$ al vaciarse; además cae solo antes de
  llegar a 50.000 Y$. Tres 🎟️ en cualquier fila dan 5 giros gratis, y cada 5
  tiradas con premio la máquina se calienta y la siguiente paga ×2 (el calor
  se guarda y se enfría un punto cada 10 minutos sin jugar). La barra de bonus
  sube con cada tirada pagada (más con los casi-premios, y a cuentagotas al
  final) y, llena, da 3 giros gratis. Los rodillos
  tienen pesos por casilla, como las máquinas reales: el 7️⃣ roza la línea en
  una de cada cinco tiradas. Devuelve ~99,5 % de lo apostado contando el bote. El GIF
  se pinta con canvas en Node, antes de pulsar, y su ritmo depende de lo que hay
  en juego: corto sin nada, y si los dos primeros prometen algo gordo, el tercero
  frena despacio hasta que se ven pasar los símbolos por la línea; el marcador
  cuenta por tramos y el cartel sube de nivel. Los premios tributan como el resto del casino, el
  bote incluido, y los de más de ×50 y los botes se anuncian en el canal.
- **Botes** (`/tragas máquina:Volcán`, `.tragas volcan [cantidad]`; antes `volcan`): una tragaperras de 5×4 al
  estilo de las de casino, ilustrada, con tres máquinas que comparten reglas y
  números y cambian los dibujos y las bromas: 🌋 el **Volcán** (`volcan`:
  cerezas, campana, BAR, siete, máscaras tiki, diamante WILD y un volcán de
  recogedor), 🪨 la **Mina** (`mina`: pico, lámpara de minero, carretilla,
  dinamita, pepita y lingote de oro, diamante en bruto y una vagoneta de oro;
  bonus Cobre, Plata, Oro y ¡FILÓN!) y ⚡ el **Olimpo** (`olimpo`: ánfora,
  lira, casco, laurel, tridente, águila, rayo de Zeus y el propio Zeus de
  recogedor; bonus Bronce, Plata, Oro y ¡IRA DE ZEUS!). Cada una tiene su
  escenario: lava, galería de mina con raíles y antorchas, templo de mármol
  sobre las nubes. Se abren con `tragas mina` y `tragas olimpo`. Los símbolos pagan por
  **ways** y las monedas (verde, azul y roja, cada una con su forma) llevan su
  premio escrito: el volcán en el rodillo 1 o el 5 las cobra todas. Cada
  moneda que cae llena el **maletín** de su color, que se guarda en la base de
  datos; lleno, dispara un **bonus** de 3 tiradas que vuelven a 3 cada vez que
  cae algo, con tickets de multiplicador, tiradas extra, multiplicadores
  inmediatos, maximizador y misteriosos. Botes MINI (10 monedas), MAJOR (15) y
  GRAND (pantalla llena, ×1.000). Botones: 🎰 Tirar/Girar, 🔁 Auto ×10 o
  ⏩ Auto bonus, ⚡ Turbo, ½ / ×2 / 💰 All-in y 📋 Premios. Devuelve ~94 %, con
  un bonus cada ~58 tiradas. La tirada tiene suspense, como la clásica: el
  quinto rodillo se hace esperar si puede traer el recogedor que falta (el
  **rescate**), doblar la recogida o completar el cinco de fila; cada moneda
  vuela a su maletín, la recogida se cuenta moneda a moneda con lava, oro o
  rayos y el cartel sube de GRAN PREMIO a ÉPICO. En el bonus las casillas paran
  de una en una, la última tirada y la moneda que falta para un bote se hacen
  esperar, y al final se cuenta todo moneda a moneda. Cada botón se pinta antes
  del clic. GIF de ~750 KB sin nada a ~2,4 MB en un ÉPICO; en turbo, solo el
  PNG. Tributan como el resto del casino. Una máquina nueva sale sola en `tragas` al añadirla a `THEMES`; sus símbolos se
  dibujan con `docs/dibujar_botes.py`.
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
- **Torre del dragón** (`/torre`, `.torre [cantidad] [nivel]`): una torre de
  9 pisos propia, animada. En cada piso hay varias puertas y unas
  esconden un dragón 🐉; las demás, un huevo 🥚. Eliges una por piso, de abajo
  arriba: con huevo subes y sube el multiplicador, con dragón lo pierdes todo.
  💰 Cobrar se lleva apuesta × multiplicador y coronar el piso 9 cobra solo.
  Cinco niveles en un menú: 🥚 Fácil (4 puertas, 1 dragón; la de por defecto,
  corona ×13,18), 🐣 Normal (3 y 1, ×38,06), 🔥 Difícil (2 y 1, ×506,88),
  🐲 Experto (3 y 2, ×19.486) y 👑 Maestro (4 y 3, ×259.522, 1 entre 262.144).
  Cada jugada es un GIF corto (canvas con Node): la puerta elegida tiembla
  más cuanto más alto estás, y luego sale el huevo y subes al piso de arriba
  o el dragón te chamusca; 🎲 tiene su ruleta, 💰 enseña en fantasma lo que
  había detrás de las puertas siguientes, coronar sube a la cima con corona y
  lluvia de monedas, y al acabar se ve la torre entera con los dragones
  encendiéndose. Cada nivel tiene su tema (día, atardecer, noche, ruinas,
  lava) y cada puerta su aspecto (madera, hierro, runas, bronce). Todo está
  pintado antes del clic. Devuelve el 99 % de media al cobrar en cualquier piso y los
  cobros de ×25 o más se anuncian en el canal.
- **Guerra** (`/guerra`, `.guerra [cantidad]`): el Casino War. Una carta para
  ti y otra para la banca en una mesa propia dibujada con canvas (Node, como
  el bacará): tapete con skins (botón 🎨), cartas grandes que salen del zapato
  y se voltean (primero la tuya, luego la de la banca), la ficha y la lateral
  y un panel con las cuotas y el resultado. Cada jugada es un GIF de ~2 s
  (repartir, ir a la guerra con las 3 cartas quemadas, rendirse) que acaba en
  el PNG de la mesa, y se pinta antes del clic: las cartas de la próxima mano
  se sortean al acabar la anterior y la mesa tiene ya hecho el GIF de cada
  botón. Gana la más alta, con el as por encima del rey y sin contar los
  palos, y la tuya paga 1:1. Con empate eliges entre 🏳️ Rendirse (recuperas la
  mitad de la apuesta, redondeando hacia abajo) y ⚔️ A la guerra (otra apuesta
  igual, la banca quema 3 cartas y sale una carta más para cada uno): si la
  tuya iguala o supera a la de la banca, cobras 1:1 la apuesta de la guerra y
  se te devuelve la original; si no, caen las dos, y si la guerra vuelve a
  empatar, premio extra igual a la apuesta original (la regla de Las Vegas).
  El botón 🤝 Empate 10:1 suma una apuesta lateral igual a la ficha que paga
  10:1 si las dos primeras cartas empatan. Zapato de 6 barajas barajado en cada
  mano y sin tope de apuesta. Devuelve el 97,67 % de la ficha yendo siempre a la
  guerra, el 96,30 % rindiéndose siempre y el 81,35 % de la lateral
  (`docs/rtp_guerra.py`). La ficha y la lateral se cobran al repartir y la
  segunda apuesta al ir a la guerra; el premio se paga al acabar. Si la mesa
  caduca o el bot se apaga con un empate sin decidir, la mano se rinde.
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
- **Allá tú** (`/allatu`, `.allatu [cantidad] [tabla]`): 22 cajas, un gordo
  sorpresa que sortea una ruleta al empezar (casi siempre ×10; con suerte, hasta
  ×250, ×500 o ×1.000 según la tabla) y Perro Sanxe al teléfono. Eliges tu caja,
  abres las demás por rondas y tras cada ronda la banca te ofrece dinero: ✅
  Trato, ❌ No trato o, una vez, 🤝 Regatear (sube o cuelga). Debajo del gordo
  hay una escalera de cajas rojas (hasta ×3, ×4 o ×5), así que perderlo no
  acaba la partida, y si lo abres a mitad de ronda la banca llama en el acto
  con el pésame. Al final, te quedas tu caja o la cambias. Una caja vale de media el 99 % de lo apostado y
  la oferta nunca lo supera, así que aceptar pronto sale caro. Todo se pinta
  con canvas en Node antes del clic: la animación de cada caja, la del trato y
  la del regateo.
- **Póker** (`/poker [modo] [cantidad]`, `.poker banca 500`, `.poker timba 50`):
  un solo comando con dos modos; sin modo, el bot pregunta con dos botones.
  ♠️ **Contra la banca**: Casino Hold'em, el Texas Hold'em
  contra la banca, en una mesa por canal para hasta 6 jugadores. Te sientas con
  tu ante, ves tus dos cartas y el flop y eliges ✅ Ir (el doble del ante) o
  🏳️ Retirarte. La banca juega con pareja de cuatros o mejor; si no juega, el
  ante cobra según la tabla (escalera real 100 a 1, escalera de color 20,
  póker 10, full 3, color 2 y el resto 1 a 1) y la de ir se devuelve. La casa
  se queda un 2,16 % del ante con la mejor estrategia (`docs/rtp_poker.py` lo
  comprueba en segundos). 💎 Bote: apuesta aparte de 20 Y$ con un bote
  progresivo del servidor que se lleva la escalera real. 🏆 Club: rango por
  experiencia (abre las mesas de ante alto y da tapetes), tres retos al día
  con premio y racha de días que lo sube. GIF del reparto y del final pintados
  con canvas en Node (si no hay Node, en Chromium y, si tampoco, con Pillow).
- **Timba** (`/poker timba [ciega]`, `.poker timba`): Texas Hold'em sin límite entre
  miembros, de dos a seis en una mesa por canal; las ciegas las pone quien la
  abre (por defecto 10/20). Cada uno ve sus cartas en privado con 🂠 Mis cartas y
  habla por turnos de 30 s con botones (pasar, igualar, subir al mínimo, medio
  bote, bote, todo o una cantidad, y retirarse). Botes secundarios, ficha suelta
  para el primero tras el botón y comisión del 5 % (tope de 3 ciegas) de cada
  bote que llega al flop, que va al Estado como IAJ. En cada mano te juegas tu
  saldo hasta 100 ciegas; lo que no gastas vuelve.
- **Dados** (`/dados`, `.dados [cantidad] [apuesta] [mesa]`): craps de casino. Eliges
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
  **Mesa compartida:** `/dados mesa:Compartida` o `.dados mesa [cantidad] [apuesta]`
  abre (o baja al final del canal) la mesa del canal, con varios jugadores y las
  mismas tiradas para todos. Cualquiera apuesta a ✅ Pase o 🚫 No pase con su
  ficha (🪙 Ficha, ½ y ×2 la cambian; 🔁 Repetir pone lo de la ronda anterior y te
  marca listo). Con la primera apuesta empieza una cuenta atrás de 20 s; si todos
  los que han apostado pulsan 🃏 Listo, se tira ya. Tira el **tirador**, el primero
  que apostó en la mesa, con 🎲 Tirar (solo él) o, si no lo hace a tiempo, el bot
  tira solo; con un siete fuera pasa al siguiente que apuesta, por orden de
  llegada. Con el punto puesto cada uno con apuesta puede poner ➕ Odds (hasta ×3)
  entre tiradas; quien llega tarde espera a la siguiente salida. Cada tirada se
  resuelve para todos a la vez y el mensaje enseña quién gana o pierde cuánto. Los
  dados de la próxima tirada se sortean al abrir cada ronda y su GIF se pinta
  mientras se apuesta, así que sale nada más tirar (el dibujo no lleva fichas ni
  importes). Lleva el tapete de quien abre la mesa. Cada jugador cobra y paga lo
  suyo por la economía (juego, sin comisiones nuevas); si la mesa se cierra con el
  punto puesto, el bot tira solo hasta decidir y paga. Sin apuestas en 5 minutos,
  la mesa se cierra.
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
  Gran Premio con un bote para quien acierte el ganador, que crece con el
  10 % de lo que se pierde en cada carrera y si nadie acierta. Tributa como el resto del
  casino.
- **Bacará** (`/bacara`, `.bacara [cantidad] [lado]`): Punto y Banca con las
  reglas de casino y un zapato de 8 barajas compartido por todo el canal. Se
  apuesta a 🔵 Jugador (1:1), 🟢 Empate (8:1) o 🔴 Banca (0,95:1, mínimo 20 Y$),
  con 🔁 Repetir para jugar de un clic. Cada mano es un GIF dibujado con canvas
  en Node: las cartas vuelan del zapato, se descubren y las terceras se
  aprietan a tirones; abajo, la carretera grande con las rachas del zapato.
  La mano siguiente se pinta mientras se ve la anterior, así que sale al
  instante. Ventaja de la casa: 1,06 % en Banca, 1,24 % en Jugador y 14,36 % en
  Empate (`docs/rtp_bacara.py`). Tributa como el resto del casino.
- **Pico** (`/picar`, `.picar`): un pozo propio con un botón por pico (🪵 Madera
  10 Y$, 🪨 Piedra 50, ⛓️ Hierro 200, 🥇 Oro 200, 💎 Diamante 1.000 y 🖤 Netherite
  5.000). El pico cae por un mundo de bloques con físicas: rebota en cada golpe,
  gira y va excavando su hueco a saltos; ningún bloque normal cae de un golpe. Cada
  golpe le quita vida; cada mineral suma premio, y los bloques de oro (×2), diamante
  (×3), netherite (×5), la baliza (×10) y el misterioso (×2 a ×10, no lo dice hasta
  romperlo) multiplican todo lo acumulado. Los cofres devuelven vida y las mesas de
  crafteo la devuelven entera; la TNT revienta lo de alrededor, en cadena. Si baja lo
  bastante pasa al Nether (capa −80) y al End (−160); en el fondo, en la −220, espera
  el dragón, que paga el tope del pico (de ×50 con madera a ×5.000 con netherite).
  Todos devuelven el 99,5 %: cambia el riesgo. La partida se ve entera en uno o varios GIF seguidos (uno por
  dimensión, con un rótulo entre ellas) y al final queda un PNG con el premio, la
  capa a la que llegó y el récord. 📋 Premios y 🏆 Récords (los tuyos y el ranking
  del servidor). Las texturas son de Pixel Perfection CE (CC BY-SA 4.0,
  `assets/picar/LICENCIA.md`). Cómo se calcula todo, en `docs/plan-picar.md` y
  `docs/picar-calibracion.md`.
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
  🔁 Ráfaga ×5 juega cinco tandas y, sin turbo, suelta sus 50 bolas en una sola
  animación (en turbo, solo la imagen final).
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
- **Gallos** (`gallos [cantidad] [miembro]`): peleas de gallos contra once
  rivales satíricos de la casa o, retando a un miembro, en duelo con grada
  de apuestas. Cada gallo tiene tres stats de 0 a 100 (💪 fuerza, 🪶
  velocidad y ❤️ aguante) y su *poder* es la media; el aspecto (skins de la
  tienda) no pelea, los stats sí. Con poder 0 se juega como siempre; entrenar
  sube la probabilidad de ganar (`p = sigmoid(logit(p0) + 3·Δpoder/100)`,
  entre el 3 y el 97 %) y la **cuota se recalcula con cada pelea** (`95 % / p`),
  así que lo esperado contra la casa sigue siendo el 95 %: entrenar cambia el
  riesgo, no el margen. En un duelo la ventaja del más entrenado es real
  (es dinero entre jugadores). El botón 🏋️ **Entrenar** abre el gimnasio, con un
  botón por stat: cuesta `200·(1 + nivel/20)²` Y$ redondeado a la decena (200
  a 0, 1.800 a 40, 7.200 a 100) y sube +3 por debajo de 40, +2 hasta 70 y +1
  después. Una hora de descanso entre entrenamientos y 6 al día (día
  canario). **Entrenar paga IGIC general (7 %)** por una prestación de
  servicios hecha en Canarias (art. 4 de la Ley 20/1991; tipo general del
  art. 51 de la Ley 4/2012): la base va a la caja de la tienda, el IGIC al
  Estado, y es un sumidero de dinero. Quien **pierde por KO** (contra la casa o
  en un duelo) muere con un 10 % de probabilidad, que baja con el aguante
  hasta el 5 %: su ficha pasa al cementerio (la ficha enseña los tres últimos
  caídos), el miembro empieza con un gallo nuevo a cero y la skin no se
  pierde. Morir no mueve dinero. 140 logros en 🐓 Gallos (39 de stats,
  entrenamiento y muertes). Reglas en `bot.services.gallos`.
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
  y las propias de minas, la torre, la guerra, pollo, Allá tú y blackjack. Vale en todos los juegos menos
  el crash, y un juego nuevo las tiene sin hacer nada. Sin `miembro`, enseña
  las porras en marcha. En la tienda, los 🔭 Prismáticos de la UCO dejan ver
  quién apuesta qué y la 📓 Libreta de la porra, montar porras de 10 jugadas.
- **Loterías** (`loteria`): un solo comando abre un panel con pestañas para
  la Lotería Nacional (jueves, sábado, Navidad y Niño), La Primitiva,
  Bonoloto, El Gordo de la Primitiva, Euromillones y dos rascas de la ONCE
  (X10 y 7 y Media). Se compra con botones: apuestas al azar (1, 5 o 10),
  décimo o billete, o números elegidos en un formulario. Todo va dibujado: el
  cartel de la administración con los botes al abrir, el décimo o el resguardo
  con las bolas al comprar, el último boleto sorteado con los aciertos marcados
  en 🎟️ Mis boletos, el rasca rascándose en un GIF (pintado antes del clic, así
  que sale al instante) y el sorteo en el canal con las bolas saliendo del
  bombo. Precios, bolas y categorías son los reales (normas de SELAE y tablas
  de la ONCE) a 10 Y$ por euro, y las probabilidades de la Nacional y los rascas
  también. En los juegos de bote se ajustan al servidor (si cada persona, sin
  contar bots, juega una apuesta por sorteo, el bote cae más o menos una vez al
  mes; con 30 personas, el de la Primitiva es 1 entre 386 frente a 1 entre
  139.838.160) y las categorías por debajo del bote pagan premios fijos que
  crecen con la dificultad. El bote junta su parte de las ventas con un mínimo
  que garantiza el Estado y que sube cada día sin acertante, hasta 90 días
  (Primitiva desde 5.000 Y$ y +400 al día, Euromillones desde 15.000 y +1.000).
  Máximo 10 apuestas por persona y sorteo en estos juegos. La cuenta del Estado
  hace de banca: cobra los boletos sin IGIC, paga los premios con el gravamen
  especial del 20 % por encima de 400.000 Y$ y, si no le llega para un premio,
  emite deuda pública. Los sorteos se celebran solos a su hora. Auditoría y
  cifras en `docs/auditoria-loteria.md`.
- **Perfil** (`perfil [miembro]`): todo lo de un miembro en un mismo menú.
  Un desplegable cambia de sección en el mismo mensaje: 📋 Resumen (un
  renglón de cada cosa), 📊 Nivel (nivel, XP y racha de días escribiendo),
  🏰 Patrimonio (activos y el Patrimonio del lunes), 🪏 Trabajo (contrato y
  vida laboral; para fichar sigue estando `pala`), 🏆 Logros, 🔥 Rachas (la
  vigente y los récords de todas las rachas que cuentan los logros) y 🎒
  Objetos (la mochila; su botón la abre para usar objetos y ponerse roles).
  Sustituye a `nivel`, `logros` y `mochila`; 🏰 Patrimonio también sale
  sola con `patrimonio [miembro]`. Solo lo maneja
  quien lo abre. Logros en 🏆 Coleccionista: *¿Quién soy yo?*, *Selfie
  diario*, *Ego de ministro*, *Fisgón de rellano*, *Informe de la UCO*,
  *Pegasus de barrio*, *Expediente completo* (ver las once secciones) y dos
  secretos.
- **Logros** (sección 🏆 de `perfil`): 3.931 logros en 56 categorías. El menú
  tiene tres grupos con secciones: 💬 Chat (general, estilo, risas, hacer
  reír, lengua y temas, conversación, horarios y fechas, imágenes y babel),
  🎙️ Voz (llamada, micro y cámara, entradas y salidas, música) y 🎰 Casino
  (una sección por juego); y además social, lista, beernight, niveles, loterías,
  tienda, mascotas, banco, economía, trabajo, oficios, hostelería, sanidad, oficina, reparto,
  funcionariado, influencer, Hong Kong y coleccionista. Cinco rarezas según lo que cuesta conseguirlos: ▫️ común,
  🔹 raro, 💠 épico, 🌟 legendario y 👑 mítico (las del casino, calibradas
  con una simulación, ver `docs/auditoria-logros.md`). 692 son secretos y
  se ven como `???` hasta conseguirlos. Las risas se reconocen de muchas
  formas (jaja, jsjs, lol, xd, 😂, 💀, ajsjsjs, kkkk, «me meo»…).
  Cada logro paga yapdollars según su rareza (50, 200, 750,
  2.500 o 10.000 Y$ brutos) con retención de IRPF, y se anuncia en el canal
  donde se consiguió. La sección 🏆 de `perfil` enseña un resumen (total, puntos, últimos
  conseguidos, los más cercanos y el más raro), un menú por categorías con
  el progreso de cada uno y el porcentaje del servidor que lo tiene (las
  largas, en hojas con ◀ y ▶), y un botón 🏆 Ranking por puntos; el botón
  🏆 de la sección los abre en un mensaje aparte. Hay además una categoría
  secreta, 🎸 Robuso, que no sale en el menú hasta conseguir uno de sus logros
  y va aparte del total y de Coleccionista: unos solo los consigue Robuso y
  otros quien trata con él. Lo de Hong Kong solo cuenta mientras está allí, y
  eso lo apunta un administrador con `robuso hongkong` o `robuso madrid`.
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
no lo necesitan). Para la «última vez conectado» del panel `admin`, habilita
**Presence Intent**: sin él el bot arranca igual (lo avisa en el log) y el
panel no sabe cuándo se conectó cada miembro. El bot también necesita ver `#chat-general` y tener permiso
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
Cara o cruz, los dados, la ruleta, el autobús, el bacará y la carrera de los
caballos ya no usan Chromium: los pinta Node con `@napi-rs/canvas` (Skia, el mismo
motor de dibujo). Los procesos de Node son de todos los juegos: uno siempre
abierto desde que arranca el bot, otro de reserva cuando se ocupan todos y
hasta 4 (los hilos del NAS), que se cierran tras 10 minutos sin uso. Cada uno
ocupa ~80 MB más ~50 MB por escena cargada (3 como mucho).

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
- Si los hay, construye la imagen nueva con el bot viejo aún en marcha (el bot
  avisa a quien use un comando de que se va a reiniciar), espera a que acabe lo
  que haya a medias (como mucho 15 minutos, `ESPERA_PARTIDAS`) y lo reinicia
  (unos segundos sin bot). Ver «Esperar a las partidas» más abajo.
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

`.reinicio` (o `.met`, o `/reinicio`) actualiza el bot a lo último de `main` y lo
reinicia sin entrar al NAS, aunque no haya commits nuevos y aunque el último
hubiera fallado. Solo lo pueden usar Yeyo y Dani (IDs en `DEPLOYERS`, en
`src/bot/cogs/deploy.py`) y no sale en `ayuda`. El bot no se reinicia solo:
deja una nota en `.despliegue/buzon` (carpeta montada en el contenedor) y el
cron de `actualizar.sh --solicitud` hace el trabajo. Al acabar, el bot publica
en el mismo canal si ha ido bien o qué ha fallado. Si en un par de minutos no
pasa nada, falta la línea de `--solicitud` en el crontab.

#### Esperar a las partidas

Un reinicio (por `reinicio` o el de las 5:00) no corta lo que hay a medias:

1. Mientras el NAS construye la imagen, todo funciona y el bot avisa una vez a cada
   uno que use un comando de que en unos minutos se reinicia (`aviso.json`).
2. Con la imagen lista, el script deja `preparar.json` y espera. El bot entra en modo
   reinicio: no corre ningún comando salvo `reinicio` y `bloquear`, y los paneles
   abiertos siguen funcionando con un aviso para terminarlos. Espera a los turnos en
   marcha (`pala`, el negocio, `banco`, `pisos`, el Rastro) y a cualquier panel, mesa
   o partida que alguien haya tocado en los últimos 2 minutos. Cuando no queda nada,
   contesta `listo.json` y el script reinicia. Si en 15 minutos (`ESPERA_PARTIDAS`,
   en segundos) no ha acabado, reinicia igual.
3. Al apagarse (también a mano en el NAS) tampoco atiende botones mientras los juegos
   cierran sus partidas.

El script solo espera si el bot dejó `sabe_esperar` en el buzón al arrancar y el
contenedor está en marcha: un bot de antes de la 9.9.0 no contestaría nunca. Si el
script contesta sin reiniciar (no pudo compilar) o las notas caducan, el bot vuelve a
abrir. Lo que sigue a medias al reiniciar (lo que llevaba más de 2 minutos parado o lo
que no acabó en 15) se cierra como hasta ahora, cada juego a su manera (los turnos de
`pala` y del negocio se cobran).

`bloquear <comando>` (o `/bloquear`) es su freno de emergencia, con los mismos dos IDs y
tampoco sale en `ayuda`: mientras se arregla un fallo o un exploit, nadie puede usar ese
comando (con `/` ni con `.`) ni los botones de su archivo, también los de las mesas ya
abiertas. Repetirlo lo desbloquea; sin comando, enseña los bloqueados. Sigue bloqueado
tras reiniciar (tabla `blocked_commands`).

`crear <miembro> <objeto>` (o `/crear`), con los mismos dos IDs y fuera de `ayuda`, mete un
objeto de la tienda en la mochila de alguien para devolverle lo que perdió por un fallo:
no cobra, no gasta existencias y no da número de serie. Solo objetos (ni roles, ni
potenciadores, ni mascotas).

### Novedades: el changelog en Discord

Cada vez que `actualizar.sh` despliega commits nuevos (de noche o con
`reinicio`) y el bot nuevo pasa la comprobación de arranque, el script apunta
en `.despliegue/buzon/novedades.txt` todo lo que ha entrado en `main` desde
el despliegue anterior, sea de quien sea: los PR fusionados (con su número y
su rama de origen) y los commits subidos sin PR (con su hash y su autor). El bot lo mira
cada 15 s, pide a GitHub la descripción de cada PR, lo publica como
«📜 Novedades del bot» y lo borra. Si el bot nuevo se cae y se vuelve atrás,
no se anuncia nada.

El aviso es como la página de un PR en GitHub, sin botones ni bromas:

- El título lleva la versión que se acaba de desplegar («📜 Novedades del
  bot · v5.22.0»). El historial de versiones está en `CHANGELOG.md` y cómo
  se sube el número, en la sección «Versiones» de `Biblia.txt`.
- Primero, la lista de cambios: título, enlace al PR y autor.
- En modo detallado (el de serie), una ficha por PR con su descripción,
  autor y fecha. La descripción sale entera: si no cabe en un embed, sigue
  en el siguiente.
  Por eso la descripción de un PR acaba en Discord tal cual (ver «Títulos y
  descripciones de PR» en `CLAUDE.md`).
- Salen todos los PR nuevos, sin tope. Lo que no cabe en un mensaje de
  Discord sigue en otro.

De dónde sale cada cambio:

- Cada `Merge pull request` hecho en el `main` de godzilin entre los dos
  commits aporta el título de su PR. Los PR del fork que llegan dentro de
  ellos no salen: el aviso cuenta los PR de godzilin, que son los que componen
  la imagen.
- Si el título es el que GitHub pone solo con el nombre de la rama
  («Claude/adoring tesla ybmnco»), salen los asuntos de sus commits.
- Los commits subidos directamente a `main`, sin PR, salen cada uno con su
  asunto, el enlace al commit y su autor.
- Un merge hecho a mano en `main` (`Merge branch 'x'`) aporta los asuntos de
  los commits que trae y que no estaban ya desplegados.
- El bot busca cada PR en el repositorio de godzilin y comprueba que sale de
  la misma rama. Usa la API pública de GitHub sin token (60
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

### Aspecto de Jovani: `aspecto`

Jovani cambia su perfil de cada servidor según lo que pase (detalle en
FUNCIONALIDADES, sección 6 octies). No hace falta configurar nada ni dar permisos:
al desplegar empieza con los banners dibujados y la bio, y el avatar sigue siendo el
global hasta que un administrador suba uno con `aspecto`. Las imágenes se guardan en
`.data/aspecto/`, en el mismo volumen que la base de datos.

Discord no publica cuántas veces se puede cambiar un avatar, así que el bot lo cambia
poco (30 minutos como mínimo entre looks automáticos) y, si Discord pide esperar, para
hasta que pase la espera. Si rechaza una pieza, las demás se aplican. Lo uno y lo otro
sale en «Último aviso» del panel de `aspecto`. Para dejarlo como estaba: `aspecto
accion:Apagar`, que quita el perfil del servidor y vuelve al global.

### Telemetría interna: `informe`

El bot cuenta cuánto se usa cada cosa para poder decidir mejoras con datos: comandos
y botones (con lo que tardan en contestar, partido en llegada al bot, trabajo del bot
y viaje de la respuesta, y si se quedaron sin respuesta), mensajes
por canal, reacciones, minutos de voz, altas y bajas, avisos del log, lo que tarda
cada dibujo y cada subida de archivos a Discord, dónde se para el bucle de eventos
cuando se queda bloqueado más de 250 ms, lo que espera cada miembro del clic a la
imagen, las vistas que caducan a medias, cada arranque con su versión, los cortes con
Discord, sus respuestas 429 por ruta, los procesos que el contenedor mata por memoria
lo que tarda cada sentencia de cada repositorio en la base de datos (y sus «database
is locked»), lo que se espera en cada candado, los procesos de Node que se abren y la
espera hasta tener uno, los dibujos que salen con el plan B, lo que pesa cada imagen,
las interacciones que Discord ya daba por perdidas (10062), cada vista abierta,
cerrada o abandonada con lo que duró, la opción elegida en cada desplegable (las
páginas de la ayuda, entre ellas), los comandos mal escritos o que contestan con un
error, los errores con traza por tipo y función, la música (búsquedas, pistas
escuchadas o saltadas, fallos y cola), lo que tarda cada llamada a Discord por ruta,
cada vuelta de cada tarea programada, lo que tarda en arrancar, los mensajes del bot
y las reacciones que reciben, y, cada minuto, la memoria y la CPU del contenedor, el
retraso del bucle, los procesos de Chromium y Node y su memoria, lo leído y escrito en
disco, las tareas vivas del bucle, lo que ocupa la base de datos y el disco libre. Al sacar el informe lee también, sin escribir nada,
cuántos miembros tienen cada logro (frente a su rareza), las ventas de la tienda por
artículo, las mascotas por especie, el dinero que entra y sale cada día (con lo que
recauda el Estado por impuesto, el Gini y su saldo) y los primeros pasos de los
miembros nuevos. Nunca guarda
el texto de un mensaje.

Todo se suma en memoria y se escribe en SQLite una vez por minuto (tablas
`telemetry_*` del mismo archivo). Las horas sueltas se pliegan en días a los 90 días
y los contadores se borran a los 400; quién hizo algo cada día, a los 90; las
muestras del sistema, a los 14.

Un administrador saca el informe con `/informe [días]` (1–90, 30 por defecto): un
JSON que solo ve él, con cantidades y seudónimos (u1, u2…), sin IDs de miembros. Se
guarda en `docs/metricas/` del fork y se resume con:

```bash
python docs/analisis_metricas.py docs/metricas/informe-AAAA-MM-DD.json
python docs/analisis_metricas.py docs/metricas/   # visión global de todos
```

Con una carpeta (o varios JSON) junta todos los informes: lo que va por día (uso
diario, sistema, arranques) cubre toda la historia y «Versiones y reinicios» compara
cada versión del bot con las anteriores. Las muestras del sistema solo se guardan 14
días en el bot, así que la historia de memoria y CPU sale de ir guardando informes.

Además, al arrancar y una vez al día el bot deja ese mismo informe (30 días) en la
carpeta `informes/`, junto a `docker-compose.yml`, y guarda los 14 últimos. La crea
`actualizar.sh` con permisos para el contenedor. Para que un agente lo lea sin
pedirlo, comparte esa carpeta por SMB desde el panel del NAS (solo lectura basta).
Sin la carpeta montada, el bot no guarda nada.

Junto a cada informe del canal va un mensaje corto con la versión del bot y lo que
hay que vigilar del día anterior (clics sin respuesta, interacciones perdidas, bot
caído, CPU, memoria o disco al límite, base de datos bloqueada, reinicios…), o «nada
que vigilar».

Para no depender del NAS, un administrador elige un canal con `/informe canal:#canal`
(o `.informe #canal`): el bot sube ahí el informe cada día y, en ese momento, el de
hoy (un reinicio el mismo día no lo repite). Hazlo privado: que solo lo vean el bot y
quien vaya a leerlo, porque lleva los nombres de los canales y las cifras de uso.
`/informe quitar:True` (o `.informe quitar`) deja de publicarlo.

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
│   ├── telemetry.py     # Telemetría interna: cuenta usos, latencias y memoria
│   ├── message_stats.py # Recuento de mensajes y niveles
│   ├── welcome.py       # Bienvenidas (GIF, frases, botón 👋) y despedidas
│   ├── images.py        # Comandos de imagen: magik, memes y los 108 efectos
│   ├── casino.py        # Ruleta con botones, saldo y daily
│   ├── patrimonio.py    # Impuesto sobre el Patrimonio de cada lunes
│   ├── intereses.py     # Intereses diarios del monedero y liquidación del ahorro
│   ├── donations.py     # donar: panel de las ONGs, mecenas y sorteos
│   ├── bizum.py         # bizum: transferencias entre miembros, exentas
│   ├── shop.py          # tienda y mochila: compras y carrito con IGIC, deseos, alquileres
│   ├── rastro.py        # vender (panel y atajo), trueque, rastro y el Monte de Piedad
│   ├── banco.py         # banco: préstamos y cuentas del servidor
│   ├── pieles.py        # pieles: panel de skins e imágenes del tapete propio
│   ├── shop_admin.py    # Trastienda de `catalogo`: panel y formularios
│   ├── shop_front.py    # escaparate compacto, ficha, carrito y lista de deseos
│   ├── pets.py          # mascota: panel, cuidados, apariciones y cameos en otros cogs
│   ├── work.py          # pala: panel del curro, minijuego con botones, nóminas
│   ├── business.py      # Negocios de pala: panel, minijuegos, noches y puentes
│   ├── blackjack.py     # Blackjack con botones (bj)
│   ├── slots.py         # Tragaperras: botones, re-giro, doble o nada, giro del día y bote
│   ├── crash.py         # Crash: cohete compartido por canal, rondas seguidas
│   ├── mines.py         # Minas: tablero de 5×5 con botones (componentes v2)
│   ├── tower.py         # Torre del dragón: pisos con puertas, huevos y dragones, animada
│   ├── war.py           # Guerra: carta contra carta, rendirse o ir a la guerra, lateral de empate
│   ├── chicken.py       # Pollo: carretera con botones, GIF por paso y autocobro
│   ├── coin.py          # moneda: cara o cruz con botones, doble o nada y canto
│   ├── bus.py           # guagua: Ride the Bus con botones, cobro y precarga de los GIF
│   ├── allatu.py        # allatu: 22 cajas, el gordo y la banca de Perro Sanxe
│   ├── poker.py         # poker: mesa por canal, ir o retirarse, 💎 Bote y club
│   ├── timba.py         # poker modo timba: Texas Hold'em entre miembros, turnos y botes
│   ├── craps.py         # dados: craps con Pase, No pase, punto, Odds y la mano
│   ├── craps_mesa.py    # dados mesa: la mesa compartida del canal, con tirador
│   ├── pachinko.py      # Pachinko con botones, Ráfaga, Auto y turbo
│   ├── picar.py         # El pozo: un pico que cae rompiendo bloques
│   ├── horses.py        # caballo: carrera por canal, parrilla, boletos y Gran Premio
│   ├── bacara.py        # bacara: mesa por canal, zapato compartido, fichas y reparto
│   ├── gallos.py        # gallos: reñidero contra la casa, retos, grada y la pelea
│   ├── backgammon.py    # backgammon: mesa contra Jovani, duelos con cubo y grada, retos
│   ├── enraya.py        # 4enraya: mesa contra Jovani, duelos con grada y retos del día
│   ├── porras.py        # porra: panel, apuestas, cierre, reparto y recuperación
│   ├── lottery.py       # loteria: panel con pestañas, compras, rascas y sorteos
│   ├── achievements.py  # Logros: seguimiento, premios, avisos y su vista
│   ├── perfil.py        # `perfil`: nivel, patrimonio, trabajo, logros, rachas, objetos, gallo, 4 en raya y backgammon
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
│   ├── agenda.py        # Las 8 franjas del día (y 4 de noche) de la pala y el negocio
│   ├── business.py      # Reglas de los negocios: local, demanda, reputación
│   ├── business_catalog.py # Sectores, productos, rótulos y habituales
│   ├── business_games.py # Minijuegos del negocio: comandas, caja, diagnóstico
│   ├── business_scene.py # Escena de Node del inicio y del negocio
│   ├── business_view.py # Lo que enseña cada pantalla del negocio
│   ├── negocio.py       # Casos de uso del negocio y su dinero
│   ├── work_catalog.py  # Oficios, puestos, contenido de minijuegos y eventos
│   ├── work_games.py    # Minijuegos de pala: cavar, detectar, memoria, diálogo
│   ├── work_tools.py    # Herramientas de curro de la tienda y su efecto en pala
│   ├── pala.py          # Casos de uso de pala: fichar, cobrar, ascender, café
│   ├── lottery.py       # Loterías del Estado: reglas, probabilidades y reparto
│   ├── lottery_render.py # Lo que se ve en cada imagen de la lotería
│   ├── lottery_scene.py # Pinta la lotería (rasca, décimo, boleto, sorteo y cartel)
│   ├── donations.py     # ONGs, niveles de mecenas, sorteo y regalos
│   ├── shop.py          # Reglas de la tienda: precio en caja, rebajas, factura
│   ├── shop_catalog.py  # Surtido de serie de la tienda y sus pasillos
│   ├── shop_uses.py     # Lo que hacen los objetos al usarlos desde la mochila
│   ├── rastro.py        # Reglas del Rastro: qué se vende, ITP, plusvalía y pujas
│   ├── monte.py         # Reglas del Monte de Piedad: qué acepta, venta y empeño
│   ├── loans.py         # Préstamos: tipo por perfil, TAE, seguro, ASNEF y embargo
│   ├── skins.py         # Skins de las mesas del casino: juegos que las admiten y catálogo
│   ├── skin_images.py   # Imágenes propias de las skins: comprobar y limpiar lo que se sube
│   ├── pets.py          # Reglas de las mascotas: vínculo, cuidados, momentos y cameos
│   ├── pets_catalog.py  # Especies de mascota: personalidad, frases y trucos
│   ├── roulette.py      # Reglas de la ruleta americana (apuestas y pagos)
│   ├── blackjack.py     # Reglas del blackjack (zapato, manos, banca, pagos)
│   ├── war.py           # Reglas de la Guerra: comparar cartas, rendición, guerra y lateral
│   ├── cards_render.py  # Mesa de cartas con Pillow: plan C del blackjack y la Guerra
│   ├── blackjack_scene.py # Blackjack con canvas en Node o Chromium (assets/blackjack/escena.html)
│   ├── war_scene.py     # Guerra con canvas en Node o Chromium (assets/guerra/escena.html)
│   ├── roulette_render.py # Rueda de Pillow, de reserva si no hay navegador
│   ├── roulette_scene.py # Ruleta con canvas en Node o Chromium (assets/ruleta/escena.html)
│   ├── slots.py         # Rodillos, premios, giros gratis y máquina caliente
│   ├── autoplay.py      # Autoplay común: bucle, tope, límite de pérdidas y Parar
│   ├── slots_render.py  # Tragaperras con Pillow, de reserva si no hay Node ni navegador
│   ├── slots_scene.py   # Tragaperras con canvas en Node o Chromium: ritmo y doble o nada
│   ├── crash.py         # Punto de explosión, curva del cohete y ronda
│   ├── crash_render.py  # Gráfica PNG de cada ronda de Crash
│   ├── mines.py         # Multiplicadores exactos y partida de Minas
│   ├── tower.py         # Dificultades, multiplicadores exactos y partida de la Torre del dragón
│   ├── chicken.py       # Pollo: dificultades, multiplicadores y carril del atropello
│   ├── chicken_render.py # Línea de tiempo del Pollo y dibujo de reserva con Pillow
│   ├── chicken_scene.py # Pollo con canvas en Node o Chromium (assets/pollo/escena.html)
│   ├── coin.py          # Cara o cruz: lanzamientos, canto, racha y doble o nada
│   ├── coin_render.py   # Vuelo de la moneda, lo que se ve en cada fotograma y dibujo con Pillow
│   ├── coin_scene.py    # Cara o cruz con canvas en Node o Chromium (assets/moneda/escena.html)
│   ├── bus.py           # Autobús: cartas, probabilidades exactas y pagos al 99 %
│   ├── bus_render.py    # Autobús: lo que se ve en cada fotograma y dibujo con Pillow
│   ├── bus_scene.py     # Autobús con canvas en Node o Chromium (assets/autobus/escena.html)
│   ├── poker.py         # Póker: valor de las manos, la banca que juega y la tabla del ante
│   ├── poker_render.py  # Póker: lo que se ve en cada fotograma y dibujo con Pillow
│   ├── poker_scene.py   # Póker con canvas en Node o Chromium (assets/poker/escena.html)
│   ├── poker_club.py    # Póker: rango, retos del día y racha del club
│   ├── timba.py         # Timba: ciegas, apuestas sin límite, botes secundarios y comisión
│   ├── craps.py         # Dados: salida, punto, Odds a la probabilidad exacta y la mano
│   ├── craps_render.py  # Física de la tirada, cámara, dados en 3D y dibujo con Pillow
│   ├── craps_scene.py   # Dados con canvas en Node o Chromium (assets/dados/escena.html)
│   ├── browser_scene.py # Pestaña de Chromium sin ventana con una escena HTML cargada
│   ├── node_scene.py    # La misma escena pintada en Node con Skia, sin navegador
│   ├── pachinko.py      # Tableros, clavos, bolsillos, sorteo, rush y retorno exacto
│   ├── pachinko_render.py # GIF neón de cada tanda, con un tema por tablero
│   ├── pachinko_physics.py # Clavos, paredes y caída con física de las bolas del pachinko
│   ├── picar.py         # Precios, valores de los minerales, premios y calibración del pozo
│   ├── picar_physics.py # Motor del pico: rebotes, golpes, TNT, cofres y el dragón
│   ├── picar_scene.py   # GIF del pozo por partes, con transiciones entre dimensiones
│   ├── picar_world.py   # Mundo del pozo: bloques, capas, cuevas y barrancos
│   ├── pachinko_motion.py # Movimiento de una tanda: las bolas chocan y cada una acaba en su bolsillo
│   ├── horses.py        # Establo, simulación por tramos, cuotas, boletos y Gran Premio
│   ├── horses_render.py # Dibujo con Pillow (plan B) y ritmo de los fotogramas
│   ├── horses_scene.py  # Caballos: carrera en Node, parrilla y boleto en Chromium
│   ├── bacara.py        # Bacará: zapato, tercera carta, pagos y carretera grande
│   ├── bacara_render.py # Bacará: lo que se ve en cada fotograma y dibujo con Pillow
│   ├── bacara_scene.py  # Bacará con canvas en Node o Chromium (assets/bacara/escena.html)
│   ├── gallos.py        # Gallos: rivales, cuotas, la pelea, la grada y la ficha del gallo
│   ├── gallos_render.py # Gallos: lo que se ve en cada fotograma y dibujo con Pillow
│   ├── gallos_scene.py  # Gallos con canvas en Node o Chromium (assets/gallos/escena.html)
│   ├── backgammon.py    # backgammon: reglas, Jovani (heurística a 1 o 2 jugadas), retos, cubo
│   ├── backgammon_render.py # backgammon: lo que se ve en cada fotograma y dibujo con Pillow
│   ├── backgammon_scene.py  # backgammon con canvas en Node o Chromium (assets/backgammon)
│   ├── enraya.py        # 4 en raya: tablero, Jovani (negamax), retos del día y grada
│   ├── enraya_render.py # 4 en raya: lo que se ve en cada fotograma y dibujo con Pillow
│   ├── enraya_scene.py  # 4 en raya con canvas en Node o Chromium (assets/enraya/escena.html)
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
    ├── bacara/         # escena.html: canvas de la mesa de bacará
    ├── poker/          # escena.html: canvas de la mesa de póker
    ├── gallos/         # escena.html: canvas del reñidero y los gallos
    ├── backgammon/     # escena.html: canvas del tablero del backgammon (con tapete)
    ├── enraya/         # escena.html: canvas del tablero del 4 en raya
    ├── memes/          # Plantillas y fuentes de los efectos (de imgen, MIT)
    └── slots/          # escena.html de la tragaperras y sus símbolos (Noto Emoji, ver LICENSE.txt)
```

En la raíz: `Dockerfile`, `docker-compose.yml`, `.env.example` y
`actualizar.sh` (autoactualización en el NAS) para el despliegue.

Consulta [Biblia.txt](./Biblia.txt) para el detalle completo de la
estructura de referencia, los límites entre capas y las normas de
seguridad, rendimiento y documentación.
