"""Catálogo del trabajo (`pala`): oficios, puestos, contenido de minijuegos y eventos.

Solo datos. Añadir un oficio es añadir un `Job` a `JOBS` con sus puestos (el
código admite los que haya), su contenido de minijuego (si usa uno nuevo) y, si
se quiere, sus eventos. Para la carrera larga, un oficio lleva un tronco de
puestos y sus ramas (`Branch`): todas con los mismos puestos, la primera sin
formación y cada una con su clase (`BranchKind`); los puestos pueden dar una ventaja
(`Perk`) y los eventos pueden ser de una rama (`Event.branches`). Los `key`
(oficios, ramas, formaciones, tareas, eventos) no se cambian nunca: son lo que
se guarda en la base de datos. Receta: «Añadir un oficio a `pala`» de `CLAUDE.md`.

Sueldos (base del turno): cada oficio fija sus tres primeros puestos (el 1, entre
1.650 y 1.800 Y$, un poco más que un IMV entero de 1.500) y el resto sale de su curva
(`work.PayCurve`): el puesto 5 entre 9.600 y 12.000 y lo más alto de una rama, entre
28.100 y 35.200 en las seguras y entre 41.500 y 51.800 en las arriesgadas. Con la vara
del IRPF (`taxes.IRPF_YAPDOLLARS_PER_EURO`, la semana es el año), la jornada completa
(4 turnos) de un celador equivale a unos 10.000 € al año y la de un comisario europeo,
a unos 300.000 €, con la retención que toca a cada uno. Las formaciones cuestan de 2 a 4
turnos del puesto al que dan acceso (`PayCurve.course`). El análisis que fija estas
cifras está en `docs/economia-trabajo.md`.

La política es una sátira del PSOE, el partido del Gobierno, y de sus
tópicos (sobres, enchufes, comisiones, puertas giratorias, el Falcon, la
máquina del fango). La obra se ríe del ladrillo (recalificaciones, la
burbuja, los pisos turísticos) y de la obra pública (modificados, aeropuertos
sin aviones, el AVE a ninguna parte). La sanidad, de la precariedad con bata
(guardias, contratos de un día, camas en el pasillo) y, en sus ramas, de la
gestión pública (listas de espera, mascarillas a precio de oro, hospitales
inaugurados sin personal) y del negocio privado (letra pequeña, conciertos con
la Comunidad, fondos de inversión). La hostelería, de los contratos de cuatro horas
que son diez, el turismo de pulsera y la patronal (hotel) y de la alta cocina de
espumas, estrellas y Dubái. El reparto se ríe de las apps de reparto
y de los falsos autónomos; el funcionariado, de la burocracia (con las
preguntas del temario de verdad), de la alta administración (el BOE a las 23:59,
el Consejo de Estado) y de la puerta giratoria hacia el asesoramiento fiscal; el
influencer, de la fama a golpes, de la tele pública (tertulias, audiencias, consejos
por cuotas) y del exilio fiscal en Andorra (energéticas, veladas, cripto). La
oficina, de la tecnología con corbata (viernes de despliegue, hackatones
«voluntarios»), de las consultoras que facturan horas al Ministerio (licitaciones
a medida, la app pública que se cae) y de los unicornios (pitch, pivotar, quemar
la caja, comprar una red social). Los
personajes del juego son genéricos; las figuras públicas (Feijóo, Puigdemont,
Trump, Erdoğan…) salen como en el resto del bot, con las fuentes de ironía de
la Biblia. No se nombra a ninguna persona que no sea figura pública ni a una
marca.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.services.work import (
    BRANCH_DAYS,
    FLAT_RATE_FEE,
    Branch,
    BranchKind,
    Job,
    Mechanic,
    PayCurve,
    Perk,
    Position,
    Task,
    Training,
)

# Sueldos: cada oficio fija sus tres primeros puestos y el resto sale de su curva
# (`PayCurve`, con la clase de cada rama); las formaciones, de 2 a 4 bases del puesto.
_SAFE = BranchKind.SAFE
_RISKY = BranchKind.RISKY

# -- Tareas comunes --------------------------------------------------------------------
#
# Contadores de `Contract.progress` (se ponen a cero al cambiar de puesto):
# `good` turnos de 90 o más, `extras` turnos extra, `events` eventos
# resueltos, y los del minijuego: `clean` (turnos de cavar sin romper nada),
# `caught` (aciertos de detectar), `perfect_rounds` (rondas de memoria sin
# fallo), `smooth` (respuestas buenas en un diálogo), `sorted` (aciertos de
# clasificar), `best` (aciertos de calcular) y `temario` (preguntas del
# temario acertadas estudiando la oposición).


def _good(goal: int) -> Task:
    return Task("good", f"Haz {goal} turnos de 90 o más", "good", goal)


def _extras(goal: int) -> Task:
    return Task("extras", f"Echa {goal} turno{'s' if goal > 1 else ''} extra", "extras", goal)


def _guards(goal: int) -> Task:
    return Task("guards", f"Haz {goal} guardias", "guards", goal)


def _study(goal: int) -> Task:
    return Task("temario", f"Acierta {goal} preguntas del temario", "temario", goal)


def _events(goal: int) -> Task:
    return Task("events", f"Sal de {goal} marrones (eventos)", "events", goal)


# -- Oficios ---------------------------------------------------------------------------

_OBRA = PayCurve(4_500)

OBRA = Job(
    key="obra",
    name="Obra",
    emoji="🦺",
    blurb="De la pala al palco o al IBEX. Estable, duro y con jubilados mirando.",
    rgb=(240, 160, 30),
    positions=(
        Position(
            1,
            "Peón",
            1_800,
            Mechanic.DIG,
            30,
            (_good(3), Task("clean", "Haz 2 turnos sin romper nada", "clean", 2)),
            days=2,
            content="cavar:3",
        ),
        Position(
            2,
            "Oficial de segunda",
            2_800,
            Mechanic.DIG,
            30,
            (_good(4), _extras(1)),
            days=3,
            content="cavar:3",
            training=Training("prl20", "el curso de PRL de 20 horas", 6_000),
            perk=Perk("Sabes leer el plano: +5 % de tiempo en el minijuego.", extra_time=0.05),
        ),
        Position(
            3,
            "Oficial de primera",
            _OBRA.p3,
            Mechanic.DIG,
            40,
            (_good(5), Task("clean", "Haz 4 turnos sin romper nada", "clean", 4)),
            days=4,
            content="cavar:4",
            perk=Perk("Bocadillo de las diez: cada turno cansa un 10 % menos.", battery=0.10),
        ),
        Position(
            4,
            "Encargado",
            _OBRA.pay(4),
            Mechanic.SPOT,
            60,
            (Task("caught", "Pilla a 25 escaqueados", "caught", 25), _good(5), _events(2)),
            days=5,
            content="vagos",
            training=Training(
                "recurso", "el curso de recurso preventivo de 60 horas", _OBRA.course(4, 2)
            ),
            perk=Perk("Ojo de encargado: un fallo gratis por turno.", retry=True),
        ),
        Position(
            5,
            "Constructor",
            _OBRA.pay(5),
            Mechanic.SPOT,
            60,
            (
                Task("caught", "Encuentra 25 partidas con sobrecoste", "caught", 25),
                _good(5),
                _events(3),
            ),
            days=4,
            content="sobrecostes",
            self_employed=True,
            training=Training(
                "rea", "el alta en el Registro de Empresas Acreditadas", _OBRA.course(5, 4)
            ),
            perk=Perk(
                "Gestoría de confianza: una respuesta mala tachada en los diálogos.", fifty=True
            ),
        ),
    ),
    branches=(
        Branch(
            "ladrillo",
            "Ladrillo",
            "🏖️",
            "Promotora en la costa: pisos sobre plano, recalificaciones y la burbuja "
            "que siempre vuelve. Autónomo. Cobra más, se jubila peor. Construye casas, "
            "bloques y torres en los solares de `pisos` (🏗️ Solares, en las calles) y cada "
            "lunes levanta una promoción de obra nueva y se lleva el 5 % de lo que se venda.",
            (
                Position(
                    6,
                    "Promotor inmobiliario",
                    _OBRA.pay(6, _RISKY),
                    Mechanic.MEMORY,
                    60,
                    (
                        Task(
                            "perfect",
                            "Enseña 15 pisos piloto tal como los pidió el cliente",
                            "perfect_rounds",
                            15,
                        ),
                        _good(5),
                        _events(2),
                    ),
                    days=BRANCH_DAYS[6],
                    content="pisos:4",
                    self_employed=True,
                    perk=Perk(
                        "Oficina de ventas en primera línea: +5 % de tiempo.", extra_time=0.05
                    ),
                ),
                Position(
                    7,
                    "Urbanizador de la costa",
                    _OBRA.pay(7, _RISKY),
                    Mechanic.SORT,
                    60,
                    (
                        Task("sorted", "Manda 30 terrenos a su sitio", "sorted", 30),
                        _good(6),
                        _events(3),
                    ),
                    days=BRANCH_DAYS[7],
                    content="terrenos",
                    self_employed=True,
                    training=Training(
                        "costas",
                        "el informe favorable de Costas (con mariscada)",
                        _OBRA.course(7, 3, _RISKY),
                    ),
                    perk=Perk(
                        "Todoterreno de promotor: cada turno cansa un 5 % menos.", battery=0.05
                    ),
                ),
                Position(
                    8,
                    "Rey del ladrillo",
                    _OBRA.pay(8, _RISKY),
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Refinancia 25 deudas sin asustar al banco", "smooth", 25),
                        _good(6),
                        _events(3),
                    ),
                    days=BRANCH_DAYS[8],
                    content="banco",
                    self_employed=True,
                    perk=Perk("Asesor fiscal en Andorra: un fallo gratis por turno.", retry=True),
                ),
                Position(
                    9,
                    "Magnate inmobiliario",
                    _OBRA.pay(9, _RISKY),
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Cierra 30 convenios con el alcalde", "smooth", 30),
                        _good(6),
                        _events(4),
                    ),
                    days=BRANCH_DAYS[9],
                    content="alcalde",
                    self_employed=True,
                    training=Training(
                        "convenio",
                        "un convenio urbanístico con el ayuntamiento",
                        _OBRA.course(9, 3, _RISKY),
                    ),
                    perk=Perk(
                        "El alcalde en favoritos: cada turno cansa un 5 % menos.", battery=0.05
                    ),
                ),
                Position(
                    10,
                    "Presidente de un club de fútbol",
                    _OBRA.pay(10, _RISKY),
                    Mechanic.DIALOGUE,
                    60,
                    (),
                    days=0,
                    content="palco",
                    self_employed=True,
                    perk=Perk("Palco VIP con catering: +10 % de tiempo.", extra_time=0.10),
                ),
            ),
            pension=2_500,
            kind=BranchKind.RISKY,
            retired_as="promotor jubilado con el chalet embargado",
        ),
        Branch(
            "publica",
            "Obra pública",
            "🚄",
            "Gran constructora: túneles, licitaciones, modificados y ese AVE que para en "
            "mitad del campo. En nómina. Cobra menos, se jubila mejor.",
            (
                Position(
                    6,
                    "Jefe de obra de una gran constructora",
                    _OBRA.pay(6, _SAFE),
                    Mechanic.DIG,
                    60,
                    (
                        _good(5),
                        Task("clean", "Haz 6 turnos de tuneladora sin romper nada", "clean", 6),
                        _events(2),
                    ),
                    days=BRANCH_DAYS[6],
                    content="cavar:5",
                    perk=Perk(
                        "Caseta de obra con aire acondicionado: cada turno cansa un 5 % menos.",
                        battery=0.05,
                    ),
                ),
                Position(
                    7,
                    "Director de licitaciones",
                    _OBRA.pay(7, _SAFE),
                    Mechanic.SORT,
                    60,
                    (
                        Task("sorted", "Manda 30 concursos a su sitio", "sorted", 30),
                        _good(6),
                        _events(3),
                    ),
                    days=BRANCH_DAYS[7],
                    content="licitaciones",
                    training=Training(
                        "clasificacion",
                        "la clasificación de contratista del Estado",
                        _OBRA.course(7, 3, _SAFE),
                    ),
                    perk=Perk(
                        "Los pliegos, de memoria: +5 % de tiempo en el minijuego.", extra_time=0.05
                    ),
                ),
                Position(
                    8,
                    "Director de concesiones",
                    _OBRA.pay(8, _SAFE),
                    Mechanic.MEMORY,
                    60,
                    (
                        Task(
                            "perfect",
                            "Renueva 15 tandas de concesiones sin olvidar ninguna",
                            "perfect_rounds",
                            15,
                        ),
                        _good(6),
                        _events(3),
                    ),
                    days=BRANCH_DAYS[8],
                    content="concesiones:4",
                    perk=Perk("Jefe de gabinete: un fallo gratis por turno.", retry=True),
                ),
                Position(
                    9,
                    "Consejero delegado",
                    _OBRA.pay(9, _SAFE),
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Saca 30 modificados en el ministerio", "smooth", 30),
                        _good(6),
                        _events(4),
                    ),
                    days=BRANCH_DAYS[9],
                    content="fomento",
                    training=Training(
                        "ute",
                        "la UTE con las otras cinco grandes",
                        _OBRA.course(9, 3, _SAFE),
                    ),
                    perk=Perk("Chófer de la empresa: cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    10,
                    "Presidente de una constructora del IBEX",
                    _OBRA.pay(10, _SAFE),
                    Mechanic.DIALOGUE,
                    60,
                    (),
                    days=0,
                    content="internacional",
                    perk=Perk("Avión privado del consejo: +10 % de tiempo.", extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="expresidente de constructora con medalla al mérito civil",
        ),
    ),
)

_HOSTELERIA = PayCurve(4_000)

HOSTELERIA = Job(
    key="hosteleria",
    name="Hostelería",
    emoji="🍽️",
    blurb="Del friegaplatos al hotel o a la estrella. Propinas en negro y reseñas de una estrella.",
    rgb=(46, 204, 113),
    positions=(
        Position(
            1, "Friegaplatos", 1_700, Mechanic.MEMORY, 30,
            (_good(3), Task("perfect", "Saca 6 tandas perfectas", "perfect_rounds", 6)),
            days=2, content="platos:3",
        ),
        Position(
            2, "Camarero", 2_500, Mechanic.MEMORY, 30, (_good(4), _extras(1)),
            days=3, content="comandas:3",
            training=Training("manipulador", "el carnet de manipulador de alimentos", 5_000),
            perk=Perk("Bandeja de tres pisos (y un pulso de cirujano): +5 % de tiempo en el "
                      "minijuego.", extra_time=0.05),
        ),
        Position(
            3, "Jefe de rango", _HOSTELERIA.p3, Mechanic.MEMORY, 40,
            (Task("perfect", "Saca 15 comandas perfectas", "perfect_rounds", 15), _good(5)),
            days=4, content="comandas:4",
            perk=Perk("Zapatillas antideslizantes (de las que usan en el hospital): cada turno "
                      "cansa un 10 % menos.", battery=0.10),
        ),
        Position(
            4, "Jefe de cocina", _HOSTELERIA.pay(4), Mechanic.MEMORY, 60,
            (Task("perfect", "Saca 20 comandas perfectas", "perfect_rounds", 20), _good(6),
             _events(2)),
            days=5, content="cocina:5",
            training=Training("alergenos", "el curso de alérgenos y APPCC",
                              _HOSTELERIA.course(4, 2)),
            perk=Perk("El pinche que te avisa antes de que se queme: un fallo gratis por turno.",
                      retry=True),
        ),
        Position(
            5, "Dueño del chiringuito", _HOSTELERIA.pay(5), Mechanic.DIALOGUE, 60,
            (Task("smooth", "Sobrevive a 25 clientes en el chiringuito", "smooth", 25),
             _good(6), _events(3)),
            days=4, content="chiringuito", self_employed=True,
            training=Training("apertura", "la licencia de apertura del chiringuito",
                              _HOSTELERIA.course(5, 4)),
            perk=Perk("Tu cuñado en la barra, que te sopla: una respuesta mala tachada en los "
                      "diálogos.", fifty=True),
        ),
    ),
    branches=(
        Branch(
            "hotel",
            "Hotelera",
            "🏨",
            "Benidorm, pulseras del todo incluido, balconing y tasa turística: de director de "
            "hotel a presidente de la patronal. En nómina. Cobra menos, se jubila mejor. "
            "«Canarias tiene un límite», tu cuenta de resultados no.",
            (
                Position(
                    6, "Director de hotel en primera línea", _HOSTELERIA.pay(6, _SAFE),
                    Mechanic.MEMORY, 60,
                    (Task("perfect", "Reparte las pulseras de 15 huéspedes sin liarla",
                          "perfect_rounds", 15), _good(5), _events(2)),
                    days=BRANCH_DAYS[6], content="pulseras:4",
                    perk=Perk("Carrito de golf para ir de la recepción al buffet: cada turno "
                              "cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    7, "Director regional de una cadena hotelera", _HOSTELERIA.pay(7, _SAFE),
                    Mechanic.SORT, 60,
                    (Task("sorted", "Manda 30 reseñas a su departamento", "sorted", 30),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[7], content="quejas",
                    training=Training(
                        "master_hotelero",
                        "el máster en Dirección Hotelera (en inglés, en Suiza y con tu "
                        "madre pagando)", _HOSTELERIA.course(7, 2.5, _SAFE),
                    ),
                    perk=Perk("Tarjeta de socio de la cadena, con sala VIP en el aeropuerto: "
                              "+5 % de tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    8, "Director de resorts en el Caribe", _HOSTELERIA.pay(8, _SAFE),
                    Mechanic.SPOT, 60,
                    (Task("caught", "Pilla a 25 colados en el buffet", "caught", 25),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="colados",
                    perk=Perk("Un jefe de recepción que te cubre las espaldas (y los check-in): "
                              "un fallo gratis por turno.", retry=True),
                ),
                Position(
                    9, "Consejero delegado de una cadena hotelera", _HOSTELERIA.pay(9, _SAFE),
                    Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Sobrevive a 30 juntas con los inversores", "smooth", 30),
                     _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="cadena_hotelera",
                    training=Training(
                        "familia_fundadora",
                        "la confianza de la familia fundadora (que te cuesta una cena de "
                        "Nochebuena con el patriarca y un anillo de compromiso)",
                        _HOSTELERIA.course(9, 2.5, _SAFE),
                    ),
                    perk=Perk("Helicóptero para llegar a la junta sin pisar la autovía: cada "
                              "turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    10, "Presidente de la patronal turística", _HOSTELERIA.pay(10, _SAFE),
                    Mechanic.DIALOGUE, 60, (),
                    days=0, content="patronal",
                    perk=Perk("El Falcon del presidente, «en comisión de servicio»: +10 % de "
                              "tiempo en el minijuego.", extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="expresidente de la patronal con suite vitalicia en primera línea",
        ),
        Branch(
            "estrella",
            "Alta cocina",
            "⭐",
            "Espumas, deconstrucciones y un menú degustación de 30.000 Y$: del restaurante propio "
            "al gurú con franquicias en Dubái. Autónomo. Cobra más, se jubila peor.",
            (
                Position(
                    6, "Chef con restaurante propio", _HOSTELERIA.pay(6, _RISKY),
                    Mechanic.MEMORY, 60,
                    (Task("perfect", "Emplata 15 platos tal como mandaba la carta",
                          "perfect_rounds", 15), _good(5), _events(2)),
                    days=BRANCH_DAYS[6], content="emplatado:4", self_employed=True,
                    perk=Perk("Pinzas de emplatar de titanio (y un pulso que no tiembla): +5 % "
                              "de tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    7, "Chef con estrella", _HOSTELERIA.pay(7, _RISKY), Mechanic.SPOT, 60,
                    (Task("caught", "Pilla a 25 inspectores de la Guía", "caught", 25),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[7], content="inspector", self_employed=True,
                    training=Training(
                        "stage_tres_estrellas",
                        "un stage gratis de seis meses en un tres estrellas (fregando "
                        "cacerolas, pero con el apellido del chef en el CV)",
                        _HOSTELERIA.course(7, 3, _RISKY),
                    ),
                    perk=Perk("Un sumiller que te susurra por el pinganillo: un fallo gratis "
                              "por turno.", retry=True),
                ),
                Position(
                    8, "Chef de la tele", _HOSTELERIA.pay(8, _RISKY), Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Aguanta 25 galas del talent de cocina", "smooth", 25),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="plato_tv", self_employed=True,
                    perk=Perk("Maquilladora de plató (te quita las ojeras de las grabaciones): "
                              "cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    9, "Dueño de un grupo de restaurantes", _HOSTELERIA.pay(9, _RISKY),
                    Mechanic.SORT, 60,
                    (Task("sorted", "Manda a 30 cocineros a su partida", "sorted", 30),
                     _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="brigada", self_employed=True,
                    training=Training(
                        "socio_inversor",
                        "la entrada de un fondo y de un socio con dinero de procedencia "
                        "«digamos, dudosa»", _HOSTELERIA.course(9, 2.5, _RISKY),
                    ),
                    perk=Perk("Un maître con cara de póquer que gestiona las crisis: +5 % de "
                              "tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    10, "Gurú gastronómico con franquicias en Dubái", _HOSTELERIA.pay(10, _RISKY),
                    Mechanic.DIALOGUE,
                    60, (),
                    days=0, content="dubai", self_employed=True,
                    perk=Perk("Jet privado con cocina de inducción: +10 % de tiempo en el "
                              "minijuego.", extra_time=0.10),
                ),
            ),
            pension=2_500,
            kind=BranchKind.RISKY,
            retired_as="gurú jubilado que cobra en pan de oro (y la pensión no da ni para el pan)",
        ),
    ),
)  # fmt: skip

_POLITICA = PayCurve(5_000)

POLITICA = Job(
    key="politica",
    name="Política (PSOE)",
    emoji="🌹",
    blurb="Del pegacarteles a la Moncloa o a Bruselas. Sobres incluidos.",
    rgb=(225, 6, 19),
    positions=(
        Position(
            1,
            "Pegacarteles",
            1_700,
            Mechanic.MEMORY,
            30,
            (_good(3), Task("perfect", "Haz 6 rutas perfectas", "perfect_rounds", 6)),
            days=2,
            content="carteles:3",
        ),
        Position(
            2,
            "Concejal",
            2_700,
            Mechanic.MEMORY,
            30,
            (
                Task("perfect", "Vota 10 veces lo que diga el partido", "perfect_rounds", 10),
                _good(4),
            ),
            days=3,
            content="votos:3",
            training=Training("listas", "un puesto de salida en las listas", 8_000),
            perk=Perk("Te sabes el argumentario: +5 % de tiempo en el minijuego.", extra_time=0.05),
        ),
        Position(
            3,
            "Diputado autonómico",
            _POLITICA.p3,
            Mechanic.DIALOGUE,
            40,
            (Task("smooth", "Esquiva 15 preguntas en rueda de prensa", "smooth", 15), _good(5)),
            days=4,
            content="prensa",
            perk=Perk("Coche oficial: cada turno cansa un 10 % menos.", battery=0.10),
        ),
        Position(
            4,
            "Diputado en el Congreso",
            _POLITICA.pay(4),
            Mechanic.DIALOGUE,
            60,
            (
                Task("smooth", "Sal vivo de 20 preguntas en comisión", "smooth", 20),
                _good(5),
                _events(3),
            ),
            days=5,
            content="comision",
            # Clave heredada de cuando el puesto 4 era ministro: quien ya la
            # compró no paga dos veces.
            training=Training(
                "cartera", "el escaño en el Congreso (cena en Ferraz)", _POLITICA.course(4, 3)
            ),
            perk=Perk("Asesores de confianza: un fallo gratis por turno.", retry=True),
        ),
        Position(
            5,
            "Portavoz del grupo parlamentario",
            _POLITICA.pay(5),
            Mechanic.DIALOGUE,
            60,
            (
                Task("smooth", "Contesta 25 preguntas sin contestar nada", "smooth", 25),
                _good(5),
                _events(3),
            ),
            days=4,
            content="portavoz",
            training=Training(
                "portavoz", "el móvil de portavoz (nunca se apaga)", _POLITICA.course(5, 3)
            ),
            perk=Perk(
                "Argumentario de Ferraz memorizado: una respuesta mala tachada en los diálogos.",
                fifty=True,
            ),
        ),
    ),
    branches=(
        Branch(
            "moncloa",
            "Moncloa",
            "🏛️",
            "Consejos de ministros, socios que chantajean y, al final, la puerta "
            "giratoria. Cobra menos, se jubila mejor.",
            (
                Position(
                    6,
                    "Secretario de Estado",
                    _POLITICA.pay(6, _SAFE),
                    Mechanic.SORT,
                    60,
                    (
                        Task("sorted", "Despacha 30 papeles al cajón correcto", "sorted", 30),
                        _good(5),
                        _events(2),
                    ),
                    days=BRANCH_DAYS[6],
                    content="decretos",
                    perk=Perk("Chófer oficial: cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    7,
                    "Ministro",
                    _POLITICA.pay(7, _SAFE),
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Sal airoso de 25 marrones del ministerio", "smooth", 25),
                        _good(6),
                        _events(3),
                    ),
                    days=BRANCH_DAYS[7],
                    content="ministerio",
                    training=Training(
                        "ministerio",
                        "la cartera ministerial (otra cena en Ferraz)",
                        _POLITICA.course(7, 3, _SAFE),
                    ),
                    perk=Perk(
                        "Gabinete de prensa: +5 % de tiempo en el minijuego.", extra_time=0.05
                    ),
                ),
                Position(
                    8,
                    "Vicepresidente del Gobierno",
                    _POLITICA.pay(8, _SAFE),
                    Mechanic.MEMORY,
                    60,
                    (
                        Task(
                            "perfect",
                            "Recuerda 15 veces lo que le prometiste a cada socio",
                            "perfect_rounds",
                            15,
                        ),
                        _good(6),
                        _events(3),
                    ),
                    days=BRANCH_DAYS[8],
                    content="socios:4",
                    perk=Perk("Falcon a demanda: cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    9,
                    "Presidente del Gobierno",
                    _POLITICA.pay(9, _SAFE),
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task(
                            "smooth",
                            "Sobrevive a 30 preguntas en la sesión de control",
                            "smooth",
                            30,
                        ),
                        _good(6),
                        _events(4),
                    ),
                    days=BRANCH_DAYS[9],
                    content="control",
                    training=Training(
                        "investidura",
                        "una investidura (pactos incluidos)",
                        _POLITICA.course(9, 3, _SAFE),
                    ),
                    perk=Perk("Manual de resistencia: otro fallo gratis por turno.", retry=True),
                ),
                Position(
                    10,
                    "Consejero de una eléctrica",
                    _POLITICA.pay(10, _SAFE),
                    Mechanic.DIALOGUE,
                    60,
                    (),
                    days=0,
                    content="consejo",
                    # Clave heredada del antiguo puesto 5: quien ya la compró la conserva.
                    training=Training(
                        "giratoria",
                        "el paso por la puerta giratoria",
                        _POLITICA.course(10, 3.5, _SAFE),
                    ),
                    perk=Perk("El sillón ya está caliente: +10 % de tiempo.", extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="expresidente con sillón vitalicio en la eléctrica",
        ),
        Branch(
            "bruselas",
            "Bruselas",
            "🇪🇺",
            "Eurocámara: dietas, lobbies, trílogos y Estrasburgo una vez al mes. Lejos de "
            "la UCO, cerca de la OLAF. Cobra más, se jubila peor.",
            (
                Position(
                    6,
                    "Eurodiputado",
                    _POLITICA.pay(6, _RISKY),
                    Mechanic.MEMORY,
                    60,
                    (
                        Task("perfect", "Vota bien 15 tandas del pleno", "perfect_rounds", 15),
                        _good(5),
                        _events(2),
                    ),
                    days=BRANCH_DAYS[6],
                    content="plenos:4",
                    perk=Perk(
                        "Dietas de Estrasburgo: cada turno cansa un 5 % menos.", battery=0.05
                    ),
                ),
                Position(
                    7,
                    "Ponente de un reglamento",
                    _POLITICA.pay(7, _RISKY),
                    Mechanic.SORT,
                    60,
                    (
                        Task("sorted", "Manda 30 enmiendas a su sitio", "sorted", 30),
                        _good(6),
                        _events(3),
                    ),
                    days=BRANCH_DAYS[7],
                    content="enmiendas",
                    training=Training(
                        "ponencia",
                        "la ponencia de un reglamento (te la cede tu grupo)",
                        _POLITICA.course(7, 2.5, _RISKY),
                    ),
                    perk=Perk(
                        "Intérpretes en cabina: +5 % de tiempo en el minijuego.", extra_time=0.05
                    ),
                ),
                Position(
                    8,
                    "Jefe de delegación",
                    _POLITICA.pay(8, _RISKY),
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Torea a 25 lobistas sin firmar nada", "smooth", 25),
                        _good(6),
                        _events(3),
                    ),
                    days=BRANCH_DAYS[8],
                    content="lobbies",
                    perk=Perk("Asistente acreditado: un fallo gratis por turno.", retry=True),
                ),
                Position(
                    9,
                    "Vicepresidente del Parlamento Europeo",
                    _POLITICA.pay(9, _RISKY),
                    Mechanic.DIALOGUE,
                    60,
                    (
                        Task("smooth", "Cierra 30 puntos de un trílogo", "smooth", 30),
                        _good(6),
                        _events(4),
                    ),
                    days=BRANCH_DAYS[9],
                    content="trilogo",
                    training=Training(
                        "vicepresidencia",
                        "el apoyo de los grandes grupos para la vicepresidencia",
                        _POLITICA.course(9, 3, _RISKY),
                    ),
                    perk=Perk(
                        "Coche con chófer en Bruselas: cada turno cansa un 5 % menos.",
                        battery=0.05,
                    ),
                ),
                Position(
                    10,
                    "Comisario europeo",
                    _POLITICA.pay(10, _RISKY),
                    Mechanic.DIALOGUE,
                    60,
                    (),
                    days=0,
                    content="comisario",
                    perk=Perk("Gabinete de veinte personas: +10 % de tiempo.", extra_time=0.10),
                ),
            ),
            pension=3_000,
            kind=BranchKind.RISKY,
            retired_as="eurócrata jubilado con pensión comunitaria",
        ),
    ),
)

_SANIDAD = PayCurve(4_800)

SANIDAD = Job(
    key="sanidad",
    name="Sanidad",
    emoji="🏥",
    blurb="Del celador a la Consejería o a la aseguradora. Aquí se rinde haciendo guardias.",
    rgb=(26, 188, 156),
    guards_from=2,
    ordinary_cap=5,
    guards_required=((4, 2),),
    positions=(
        Position(
            1, "Celador", 1_750, Mechanic.MEMORY, 30,
            (_good(3), Task("perfect", "Haz 6 traslados perfectos", "perfect_rounds", 6)),
            days=2, content="camilla:3",
        ),
        Position(
            2, "TCAE", 2_800, Mechanic.MEMORY, 30,
            (_good(3), _guards(2)),
            days=3, content="ronda:3",
            training=Training("tcae", "el título de TCAE (FP de grado medio)", 6_000),
            perk=Perk("Zuecos de goma: +5 % de tiempo en el minijuego.", extra_time=0.05),
        ),
        Position(
            3, "Enfermero", _SANIDAD.p3, Mechanic.SPOT, 40,
            (Task("caught", "Acierta 20 triajes", "caught", 20), _guards(4)),
            days=4, content="triaje",
            training=Training("enfermeria", "el grado en Enfermería", 19_000),
            perk=Perk(
                "Café de la máquina del control: cada turno cansa un 10 % menos.", battery=0.10
            ),
        ),
        Position(
            4, "Médico residente (MIR)", _SANIDAD.pay(4), Mechanic.DIALOGUE, 60,
            (Task("smooth", "Acierta 25 preguntas del MIR", "smooth", 25), _guards(6),
             _events(2)),
            days=5, content="mir",
            training=Training("mir", "la academia del MIR (y aprobar el examen)",
                              _SANIDAD.course(4, 4)),
            perk=Perk("El Harrison, subrayado entero: un fallo gratis por turno.", retry=True),
        ),
        Position(
            5, "Médico adjunto", _SANIDAD.pay(5), Mechanic.DIALOGUE, 60,
            (Task("smooth", "Pasa 25 consultas sin una sola reclamación", "smooth", 25),
             _guards(6), _events(3)),
            days=4, content="consulta",
            training=Training("ope", "la plaza de adjunto (oposición, OPE)", _SANIDAD.course(5, 4)),
            perk=Perk(
                "El residente te sopla: una respuesta mala tachada en los diálogos.", fifty=True
            ),
        ),
    ),
    branches=(
        Branch(
            "publica",
            "Sanidad pública",
            "🏛️",
            "Del jefe de sección a la Consejería: listas de espera, camas en el pasillo y "
            "hospitales inaugurados sin personal. En nómina. Cobra menos, se jubila mejor.",
            (
                Position(
                    6, "Jefe de sección", _SANIDAD.pay(6, _SAFE), Mechanic.MEMORY, 60,
                    (Task("perfect", "Cuadra 15 partes de quirófano sin tachones",
                          "perfect_rounds", 15), _guards(6), _events(2)),
                    days=BRANCH_DAYS[6], content="quirofano:4",
                    perk=Perk(
                        "Despacho con ventana (al patio de luces): cada turno cansa un 5 % "
                        "menos.", battery=0.05,
                    ),
                ),
                Position(
                    7, "Jefe de servicio", _SANIDAD.pay(7, _SAFE), Mechanic.SORT, 60,
                    (Task("sorted", "Manda 30 pacientes a su sitio", "sorted", 30),
                     _guards(6), _events(3)),
                    days=BRANCH_DAYS[7], content="derivaciones",
                    training=Training(
                        "jefatura",
                        "la jefatura de servicio por concurso de méritos (y un café con el "
                        "gerente)", _SANIDAD.course(7, 2.5, _SAFE),
                    ),
                    perk=Perk(
                        "Secretaria que te guarda la agenda: +5 % de tiempo en el minijuego.",
                        extra_time=0.05,
                    ),
                ),
                Position(
                    8, "Director médico", _SANIDAD.pay(8, _SAFE), Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Apaga 25 incendios en la junta técnico-asistencial",
                          "smooth", 25), _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="direccion",
                    perk=Perk("Un jefe de guardia que no te llama: un fallo gratis por turno.",
                              retry=True),
                ),
                Position(
                    9, "Gerente del hospital", _SANIDAD.pay(9, _SAFE), Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Sobrevive a 30 reuniones con la Consejería", "smooth", 30),
                     _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="gerencia",
                    training=Training(
                        "gerencia",
                        "el nombramiento de gerente (libre designación, del partido que toque)",
                        _SANIDAD.course(9, 3, _SAFE),
                    ),
                    perk=Perk("Plaza de garaje en el hospital: cada turno cansa un 5 % menos.",
                              battery=0.05),
                ),
                Position(
                    10, "Consejero de Sanidad", _SANIDAD.pay(10, _SAFE), Mechanic.DIALOGUE, 60, (),
                    days=0, content="consejeria",
                    perk=Perk("Coche oficial con sirena (de adorno): +10 % de tiempo.",
                              extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="exconsejero de Sanidad con sillón en una aseguradora",
        ),
        Branch(
            "privada",
            "Privada",
            "💉",
            "Mutuas, retoques y conciertos con la Comunidad: la salud como modelo de negocio. "
            "Autónomo. Cobra más, se jubila peor.",
            (
                Position(
                    6, "Médico de mutua", _SANIDAD.pay(6, _RISKY), Mechanic.SPOT, 60,
                    (Task("caught", "Encuentra 25 cláusulas de letra pequeña", "caught", 25),
                     _good(5), _events(2)),
                    days=BRANCH_DAYS[6], content="letra_pequena", self_employed=True,
                    perk=Perk(
                        "Consultas de diez minutos (el doble que en la pública): cada turno "
                        "cansa un 5 % menos.", battery=0.05,
                    ),
                ),
                Position(
                    7, "Cirujano estético", _SANIDAD.pay(7, _RISKY), Mechanic.MEMORY, 60,
                    (Task("perfect", "Haz 15 retoques tal como los pidió el cliente",
                          "perfect_rounds", 15), _good(6), _events(3)),
                    days=BRANCH_DAYS[7], content="retoques:4", self_employed=True,
                    training=Training(
                        "estetica",
                        "el máster en Medicina Estética (un fin de semana en Marbella)",
                        _SANIDAD.course(7, 2.5, _RISKY),
                    ),
                    perk=Perk("Luz de quirófano favorecedora: +5 % de tiempo en el minijuego.",
                              extra_time=0.05),
                ),
                Position(
                    8, "Director de una clínica privada", _SANIDAD.pay(8, _RISKY),
                    Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Atiende a 25 clientes VIP sin perder ninguno", "smooth",
                          25), _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="clinica", self_employed=True,
                    perk=Perk("Recepcionista que lo filtra todo: un fallo gratis por turno.",
                              retry=True),
                ),
                Position(
                    9, "Consejero delegado de un grupo hospitalario", _SANIDAD.pay(9, _RISKY),
                    Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Cierra 30 conciertos con la Comunidad", "smooth", 30),
                     _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="conciertos", self_employed=True,
                    training=Training(
                        "fondo_salud",
                        "la entrada de un fondo de inversión en el accionariado",
                        _SANIDAD.course(9, 2.5, _RISKY),
                    ),
                    perk=Perk("Helipuerto en la azotea: cada turno cansa un 5 % menos.",
                              battery=0.05),
                ),
                Position(
                    10, "Dueño de una aseguradora de salud", _SANIDAD.pay(10, _RISKY),
                    Mechanic.DIALOGUE, 60, (),
                    days=0, content="aseguradora", self_employed=True,
                    perk=Perk("Póliza de oro para ti (sin carencias): +10 % de tiempo.",
                              extra_time=0.10),
                ),
            ),
            pension=2_500,
            kind=BranchKind.RISKY,
            retired_as="dueño jubilado de aseguradora, con su propia póliza denegada",
        ),
    ),
)  # fmt: skip

_OFICINA = PayCurve(5_000)

OFICINA = Job(
    key="oficina",
    name="Oficina (tecnología)",
    emoji="💻",
    blurb="Del café del becario a CTO con stock options. Y arriba, la consultora del Ministerio "
    "o el unicornio con red social propia. Teletrabajo y Hong Kong.",
    rgb=(52, 152, 219),
    remote=True,
    abroad_from=3,
    options_level=5,
    options_branch="unicornio",
    positions=(
        Position(
            1, "Becario", 1_700, Mechanic.MEMORY, 30,
            (_good(3), Task("perfect", "Acierta 6 rondas de cafés", "perfect_rounds", 6)),
            days=2, content="cafes:3",
        ),
        Position(
            2, "Programador junior", 2_800, Mechanic.SPOT, 30,
            (Task("caught", "Encuentra 15 bugs", "caught", 15), _good(4)),
            days=3, content="bugs",
            training=Training("bootcamp", "el bootcamp de programación", 10_000),
            perk=Perk("Los auriculares con cancelación de ruido, que te aíslan hasta del "
                      "«tienes un momento»: +5 % de tiempo en el minijuego.", extra_time=0.05),
        ),
        Position(
            3, "Programador senior", _OFICINA.p3, Mechanic.SPOT, 40,
            (Task("caught", "Para 20 cambios que rompían producción", "caught", 20), _good(5)),
            days=4, content="revisiones",
            perk=Perk("El teclado mecánico y la silla buena (la de 60.000 Y$ que te pagas tú): "
                      "cada turno cansa un 10 % menos.", battery=0.10),
        ),
        Position(
            4, "Tech lead", _OFICINA.pay(4), Mechanic.DIALOGUE, 60,
            (Task("smooth", "Acorta 20 reuniones", "smooth", 20), _good(5), _events(2)),
            days=5, content="reuniones",
            training=Training("scrum", "la certificación de Scrum Master", _OFICINA.course(4, 2)),
            perk=Perk("El `git revert`, tu botón de «aquí no ha pasado nada»: un fallo gratis "
                      "por turno.", retry=True),
        ),
        Position(
            5, "CTO de startup", _OFICINA.pay(5), Mechanic.DIALOGUE, 60,
            (Task("smooth", "Convence a 25 inversores", "smooth", 25), _good(6), _events(3)),
            days=4, content="inversores",
            training=Training("mba", "el MBA de una escuela de negocios", _OFICINA.course(5, 4)),
            perk=Perk("Un ingeniero que te sopla por el chat qué decir (y qué no) a los "
                      "inversores: una respuesta mala tachada en los diálogos.", fifty=True),
        ),
    ),
    branches=(
        Branch(
            "consultora",
            "Consultora",
            "🏢",
            "Horas facturadas al Ministerio, un PowerPoint de 80 diapositivas, la app pública "
            "que no funciona y Cl@ve a las 23:59. En nómina de la firma: cobra menos, se jubila "
            "mejor.",
            (
                Position(
                    6, "Jefe de proyecto", _OFICINA.pay(6, _SAFE), Mechanic.SORT, 60,
                    (Task("sorted", "Imputa 30 horas al cliente que toca", "sorted", 30),
                     _good(5), _events(2)),
                    days=BRANCH_DAYS[6], content="horas",
                    perk=Perk("Un Excel de imputación de horas con una pestaña oculta que solo "
                              "abres tú: cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    7, "Gerente de cuentas públicas", _OFICINA.pay(7, _SAFE), Mechanic.MEMORY, 60,
                    (Task("perfect", "Cierra 15 licitaciones sin que salte la alarma",
                          "perfect_rounds", 15), _good(6), _events(3)),
                    days=BRANCH_DAYS[7], content="licitacion:4",
                    training=Training(
                        "pmp",
                        "la certificación de gestión de proyectos (y un PowerPoint de 80 "
                        "diapositivas, que ya es más de lo que cabe en la certificación)",
                        _OFICINA.course(7, 2.5, _SAFE),
                    ),
                    perk=Perk("Una plantilla de pliego que ya trae el nombre de tu empresa "
                              "escrito: +5 % de tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    8, "Director de transformación digital", _OFICINA.pay(8, _SAFE),
                    Mechanic.SPOT, 60,
                    (Task("caught", "Encuentra 25 fallos que tumban la app pública", "caught", 25),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="app_publica",
                    perk=Perk("Un «comité de seguimiento» que se reúne cuando algo falla y "
                              "decide que no ha fallado: un fallo gratis por turno.",
                              retry=True),
                ),
                Position(
                    9, "Socio", _OFICINA.pay(9, _SAFE), Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Sal airoso de 25 comités de socios", "smooth", 25),
                     _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="comite",
                    training=Training(
                        "cuota_socio",
                        "la cuota de entrada como socio (se paga en cómodos plazos… de por "
                        "vida)", _OFICINA.course(9, 2.5, _SAFE),
                    ),
                    perk=Perk("Un despacho con una mesa de caoba y un cuadro que no entiendes: "
                              "cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    10, "Socio director", _OFICINA.pay(10, _SAFE), Mechanic.DIALOGUE, 60, (),
                    days=0, content="socio_director",
                    perk=Perk("Un coche con chófer y una agenda con el móvil del ministro: "
                              "+10 % de tiempo en el minijuego.", extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="socio jubilado que factura sus memorias por horas",
        ),
        Branch(
            "unicornio",
            "Unicornio",
            "🦄",
            "Pitch a inversores, pivotar con cada moda, quemar la caja con un futbolín de oro "
            "y, al final, comprar una red social. Autónomo, con opciones en papel: cobra más, "
            "se jubila peor.",
            (
                Position(
                    6, "Fundador de una startup", _OFICINA.pay(6, _RISKY), Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Vende humo a 30 inversores", "smooth", 30),
                     _good(5), _events(2)),
                    days=BRANCH_DAYS[6], content="pitch", self_employed=True,
                    perk=Perk("Una camiseta con el logo y un sofá-puf en la oficina: cada turno "
                              "cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    7, "CEO tras la serie A", _OFICINA.pay(7, _RISKY), Mechanic.SORT, 60,
                    (Task("sorted", "Pivota 30 ideas a la moda del momento", "sorted", 30),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[7], content="pivotar", self_employed=True,
                    training=Training(
                        "serie_a",
                        "la ronda serie A (con un fondo que no ha leído el plan de negocio)",
                        _OFICINA.course(7, 2.5, _RISKY),
                    ),
                    perk=Perk("Un «asesor de estrategia» que dice «pivota» cada vez que dudas: "
                              "+5 % de tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    8, "CEO de un unicornio", _OFICINA.pay(8, _RISKY), Mechanic.SPOT, 60,
                    (Task("caught", "Encuentra 25 gastos que queman la caja", "caught", 25),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="runway", self_employed=True,
                    perk=Perk("Un director financiero que dice «es una inversión» por ti: un "
                              "fallo gratis por turno.", retry=True),
                ),
                Position(
                    9, "Business angel", _OFICINA.pay(9, _RISKY), Mechanic.MEMORY, 60,
                    (Task("perfect", "Repasa 15 historiales de rondas sin equivocarte",
                          "perfect_rounds", 15), _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="rondas:4", self_employed=True,
                    training=Training(
                        "family_office",
                        "el family office (con un gestor que te dice «sí, claro» a todo)",
                        _OFICINA.course(9, 2, _RISKY),
                    ),
                    perk=Perk("Una lista de startups ya enterradas para contárselas a los "
                              "jóvenes: cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    10, "Tecnomillonario con red social propia", _OFICINA.pay(10, _RISKY),
                    Mechanic.DIALOGUE, 60,
                    (), days=0, content="red_social", self_employed=True,
                    perk=Perk("Un megáfono que llega a todo el mundo, tengas razón o no: +10 % "
                              "de tiempo en el minijuego.", extra_time=0.10),
                ),
            ),
            pension=2_500,
            kind=BranchKind.RISKY,
            retired_as="tecnomillonario jubilado que da charlas sobre el fracaso",
        ),
    ),
)  # fmt: skip

_REPARTO = PayCurve(4_500)

REPARTO = Job(
    key="reparto",
    name="Reparto (rider)",
    emoji="🛵",
    blurb="Pedidos, estrellas en la app y lluvia que paga más. De la bici a fundar la app.",
    rgb=(0, 204, 188),
    weather=True,
    rating=True,
    positions=(
        Position(
            1, "Rider en bici", 1_650, Mechanic.CALC, 30,
            (_good(3), Task("best", "Elige bien 15 pedidos", "best", 15)),
            days=2, content="pedidos:3", self_employed=True, fee=FLAT_RATE_FEE,
        ),
        Position(
            2, "Rider en moto", 2_700, Mechanic.CALC, 30,
            (Task("best", "Elige bien 25 pedidos", "best", 25), _good(4)),
            days=3, content="pedidos:4", self_employed=True, fee=FLAT_RATE_FEE,
            training=Training("carnet_a2", "el carnet de moto (A2) y una moto de segunda mano",
                              8_000),
            perk=Perk("Casco con bluetooth (para oír al cliente gritar «¿dónde estás?» en "
                      "estéreo): +5 % de tiempo en el minijuego.", extra_time=0.05),
        ),
        Position(
            3, "Rider con contrato", _REPARTO.p3, Mechanic.MEMORY, 40,
            (Task("perfect", "Haz 15 rutas perfectas", "perfect_rounds", 15), _good(5)),
            days=4, content="entregas:4",
            training=Training("ley_rider", "el abogado laboralista que te gana la laboralidad "
                              "(Ley 12/2021, la «Ley Rider»)", 15_000),
            perk=Perk("Chubasquero de verdad (no el del bazar): cada turno cansa un 10 % "
                      "menos.", battery=0.10),
        ),
        Position(
            4, "Jefe de flota", _REPARTO.pay(4), Mechanic.SPOT, 60,
            (Task("caught", "Pilla a 25 riders comiéndose el pedido", "caught", 25), _good(5),
             _events(2)),
            days=5, content="flota",
            training=Training("flotas", "el curso de gestión de flotas", _REPARTO.course(4, 2.5)),
            perk=Perk("El grupo de WhatsApp de la flota (donde nadie escribe pero todos lo "
                      "leen): un fallo gratis por turno.", retry=True),
        ),
        Position(
            5, "Fundador de app de reparto", _REPARTO.pay(5), Mechanic.DIALOGUE, 60,
            (Task("smooth", "Sobrevive a 25 preguntas como fundador de la app", "smooth", 25),
             _good(6), _events(3)),
            days=4, content="fundador", self_employed=True,
            training=Training("semilla", "la ronda semilla (familia, amigos y algún incauto)",
                              _REPARTO.course(5, 4)),
            perk=Perk("Un abogado laboralista en nómina (del otro lado del pasillo): una "
                      "respuesta mala tachada en los diálogos.", fifty=True),
        ),
    ),
    branches=(
        Branch(
            "correos",
            "Correos",
            "📮",
            "El aviso de «ausente» sin llamar al timbre, el paquete en tránsito desde 2019, "
            "el voto por correo y una presidencia que siempre es para alguien de confianza "
            "de Moncloa. En nómina (empresa pública). Cobra menos, se jubila mejor.",
            (
                Position(
                    6, "Jefe de unidad de reparto", _REPARTO.pay(6, _SAFE), Mechanic.SORT, 60,
                    (Task("sorted", "Manda 30 envíos a su sitio", "sorted", 30),
                     _good(5), _events(2)),
                    days=BRANCH_DAYS[6], content="paquetes",
                    perk=Perk("Furgoneta de Correos con aire acondicionado (el de 1994, pero "
                              "aire): cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    7, "Director de zona", _REPARTO.pay(7, _SAFE), Mechanic.MEMORY, 60,
                    (Task("perfect", "Haz 15 rutas rurales perfectas", "perfect_rounds", 15),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[7], content="ruta_rural:4",
                    training=Training(
                        "oposicion_correos",
                        "la oposición de Correos (decenas de miles de aspirantes para unas "
                        "pocas plazas, y tú con el temario de 2008)", _REPARTO.course(7, 2.5,
                                                                                      _SAFE),
                    ),
                    perk=Perk("Un sello de «Urgente» que abre cualquier puerta del almacén: "
                              "+5 % de tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    8, "Director de operaciones", _REPARTO.pay(8, _SAFE), Mechanic.SPOT, 60,
                    (Task("caught", "Encuentra 25 paquetes perdidos", "caught", 25),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="perdido",
                    perk=Perk("Un cartero veterano que sabe en qué estante de Zaragoza duerme "
                              "cada carta: un fallo gratis por turno.", retry=True),
                ),
                Position(
                    9, "Director general de Correos", _REPARTO.pay(9, _SAFE), Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Sobrevive a 30 consejos de administración", "smooth", 30),
                     _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="direccion_correos",
                    training=Training(
                        "llamada_moncloa",
                        "una llamada de Moncloa (el currículum es lo de menos, pero el "
                        "carné, lo de más)", _REPARTO.course(9, 2.5, _SAFE),
                    ),
                    perk=Perk("Despacho con vistas a Moncloa (y un café que no paga nadie): "
                              "cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    10, "Presidente de Correos", _REPARTO.pay(10, _SAFE), Mechanic.DIALOGUE, 60, (),
                    days=0, content="presidencia_correos",
                    perk=Perk("Un sello conmemorativo con tu cara, que abre cualquier "
                              "puerta: +10 % de tiempo en el minijuego.", extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="expresidente de Correos con sello conmemorativo propio (y torcido)",
        ),
        Branch(
            "logistica",
            "Imperio logístico",
            "🚀",
            "Tiendas fantasma, falsos autónomos, drones y un cohete propio: del dueño de "
            "dark stores al multimillonario con cohete. Autónomo. Cobra más, se jubila peor.",
            (
                Position(
                    6, "Dueño de dark stores", _REPARTO.pay(6, _RISKY), Mechanic.MEMORY, 60,
                    (Task("perfect", "Prepara 15 pedidos de 10 minutos en orden",
                          "perfect_rounds", 15), _good(5), _events(2)),
                    days=BRANCH_DAYS[6], content="dark_store:4", self_employed=True,
                    perk=Perk("Persiana con mando a distancia (la tienda abre y cierra en un "
                              "segundo): +5 % de tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    7, "Lobista contra la Ley Rider", _REPARTO.pay(7, _RISKY),
                    Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Sobrevive a 25 reuniones en el Ministerio de Trabajo",
                          "smooth", 25), _good(6), _events(3)),
                    days=BRANCH_DAYS[7], content="ley_rider", self_employed=True,
                    training=Training(
                        "bufete_colaboradores",
                        "el bufete que convierte riders en «colaboradores» (con una sola "
                        "firma y sin que se enteren)", _REPARTO.course(7, 3, _RISKY),
                    ),
                    perk=Perk("Un asesor que te susurra «eso no lo digas» antes de cada "
                              "frase: un fallo gratis por turno.", retry=True),
                ),
                Position(
                    8, "Rey de la última milla", _REPARTO.pay(8, _RISKY), Mechanic.SORT, 60,
                    (Task("sorted", "Manda 30 pedidos a su reparto", "sorted", 30),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="ultima_milla", self_employed=True,
                    perk=Perk("Una flota de drones que reparten mientras duermes (los que no "
                              "se caen): cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    9, "Magnate de la logística", _REPARTO.pay(9, _RISKY), Mechanic.SPOT, 60,
                    (Task("caught", "Descubre a 25 inspectoras de Trabajo infiltradas",
                          "caught", 25), _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="inspector_trabajo", self_employed=True,
                    training=Training(
                        "salida_bolsa",
                        "la salida a bolsa (con campana, banquero y nota de prensa)",
                        _REPARTO.course(9, 2.5, _RISKY),
                    ),
                    perk=Perk("La campana de la bolsa, que suena sola cuando entras: +5 % de "
                              "tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    10, "Multimillonario con cohete propio", _REPARTO.pay(10, _RISKY),
                    Mechanic.DIALOGUE, 60, (),
                    days=0, content="cohete", self_employed=True,
                    perk=Perk("Un cohete privado con asiento para ti y otro para tu ego: "
                              "+10 % de tiempo en el minijuego.", extra_time=0.10),
                ),
            ),
            pension=2_500,
            kind=BranchKind.RISKY,
            retired_as="multimillonario jubilado que cobra en cohetes (y la pensión no da "
                       "ni para la gasolina)",
        ),
    ),
)  # fmt: skip

_FUNCIONARIADO = PayCurve(4_400)

FUNCIONARIADO = Job(
    key="funcionariado",
    name="Funcionariado",
    emoji="🗂️",
    blurb="Oposita, saca la plaza y que no te mueva nadie. Trienios y desayuno sagrado. "
    "Y arriba, el BOE a las 23:59 o la puerta giratoria.",
    rgb=(149, 165, 166),
    study="temario",
    tenure_from=2,
    seniority=True,
    positions=(
        Position(
            1, "Interino", 1_700, Mechanic.SORT, 30,
            (_good(3), _study(20)),
            days=2, content="ventanilla",
        ),
        Position(
            2, "Auxiliar administrativo (C2)", 2_700, Mechanic.SORT, 30,
            (_study(40), Task("sorted", "Tramita 40 papeles al sitio correcto", "sorted", 40)),
            days=3, content="registro",
            training=Training("ope_c2", "las tasas y la academia de la oposición de auxiliar "
                              "(C2)", 6_000),
            perk=Perk("El sello de caucho propio, que nadie te toca y nunca se queda sin tinta: "
                      "+5 % de tiempo en el minijuego.", extra_time=0.05),
        ),
        Position(
            3, "Administrativo (C1)", _FUNCIONARIADO.p3, Mechanic.MEMORY, 40,
            (_study(60), Task("perfect", "Haz 15 circuitos de firmas perfectos",
                              "perfect_rounds", 15), _good(4)),
            days=4, content="sellos:4",
            training=Training("ope_c1", "la promoción interna a administrativo (C1)", 15_000),
            perk=Perk("La silla ergonómica que pediste en 2009 (acaba de llegar): cada turno "
                      "cansa un 10 % menos.", battery=0.10),
        ),
        Position(
            4, "Técnico de gestión (A2)", _FUNCIONARIADO.pay(4), Mechanic.DIALOGUE, 60,
            (_study(80), Task("smooth", "Atiende bien a 25 ciudadanos", "smooth", 25),
             _events(2)),
            days=5, content="ciudadano",
            training=Training("ope_a2", "la oposición de técnico de gestión (A2)",
                              _FUNCIONARIADO.course(4, 4)),
            perk=Perk("El compañero que se sabe el procedimiento de memoria y te lo susurra: un "
                      "fallo gratis por turno.", retry=True),
        ),
        Position(
            5, "Inspector de Hacienda (A1)", _FUNCIONARIADO.pay(5), Mechanic.SPOT, 60,
            (Task("caught", "Destapa 25 declaraciones con trampa", "caught", 25), _good(6),
             _events(3)),
            days=4, content="inspeccion",
            training=Training("ope_a1", "la oposición a Inspector de Hacienda (cinco años de "
                              "academia)", _FUNCIONARIADO.course(5, 4)),
            perk=Perk("La base de datos que lo cruza todo (y no te deja ni fingir que no te "
                      "has dado cuenta): una respuesta mala tachada en los diálogos.",
                      fifty=True),
        ),
    ),
    branches=(
        Branch(
            "alta",
            "Alta Administración",
            "🏛️",
            "El BOE a las 23:59 de un viernes de agosto, decretos ómnibus, libre designación y "
            "el Consejo de Estado como cementerio de elefantes. Sigues siendo funcionario: "
            "nómina, plaza fija y trienios. Cobra menos, se jubila mejor.",
            (
                Position(
                    6, "Subdirector general", _FUNCIONARIADO.pay(6, _SAFE), Mechanic.SORT, 60,
                    (Task("sorted", "Manda 30 disposiciones a su sección del BOE", "sorted", 30),
                     _good(5), _events(2)),
                    days=BRANCH_DAYS[6], content="boe",
                    perk=Perk("Plaza de garaje en el ministerio (con tu nombre pintado): cada "
                              "turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    7, "Director general", _FUNCIONARIADO.pay(7, _SAFE), Mechanic.MEMORY, 60,
                    (Task("perfect", "Tramita 15 expedientes sin saltarte ningún trámite",
                          "perfect_rounds", 15), _good(6), _events(3)),
                    days=BRANCH_DAYS[7], content="expediente:4",
                    training=Training(
                        "libre_designacion",
                        "el nombramiento por libre designación (y el carné del partido que "
                        "toque este año)", _FUNCIONARIADO.course(7, 2.5, _SAFE),
                    ),
                    perk=Perk("Un jefe de gabinete que sabe en qué cajón está cada firma: +5 % "
                              "de tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    8, "Secretario general técnico", _FUNCIONARIADO.pay(8, _SAFE),
                    Mechanic.SPOT, 60,
                    (Task("caught", "Pilla 25 disposiciones adicionales coladas", "caught", 25),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="colada",
                    perk=Perk("El letrado que se lee hasta la última página (el único): un "
                              "fallo gratis por turno.", retry=True),
                ),
                Position(
                    9, "Subsecretario", _FUNCIONARIADO.pay(9, _SAFE), Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Sobrevive a 30 lunes en la Comisión de Subsecretarios",
                          "smooth", 30), _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="subsecretarios",
                    training=Training(
                        "cuerpo_elite",
                        "pertenecer a un cuerpo de élite y comer, cada martes, con el ministro",
                        _FUNCIONARIADO.course(9, 2.5, _SAFE),
                    ),
                    perk=Perk("Chófer que conoce todos los atajos de la Castellana: cada turno "
                              "cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    10, "Consejero de Estado", _FUNCIONARIADO.pay(10, _SAFE), Mechanic.DIALOGUE, 60,
                    (),
                    days=0, content="consejo_estado",
                    perk=Perk("Despacho con vistas y sin plazo para dictaminar: +10 % de tiempo "
                              "en el minijuego.", extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="consejero de Estado emérito con despacho, chófer y un dictamen sin leer",
        ),
        Branch(
            "giratoria",
            "Puerta giratoria",
            "💼",
            "El inspector que ahora enseña a no pagar: asesor de fortunas, socio de una Big "
            "Four, ingeniero fiscal y Andorra. Excedencia voluntaria y autónomo: sin plaza "
            "fija ni trienios. Cobra más, se jubila peor.",
            (
                Position(
                    6, "Asesor fiscal de grandes fortunas", _FUNCIONARIADO.pay(6, _RISKY),
                    Mechanic.SORT, 60,
                    (Task("sorted", "Manda 30 bienes a su escondite legal", "sorted", 30),
                     _good(5), _events(2)),
                    days=BRANCH_DAYS[6], content="patrimonio", self_employed=True,
                    perk=Perk("Una agenda con el móvil del gerente de cada banco andorrano: un "
                              "fallo gratis por turno.", retry=True),
                ),
                Position(
                    7, "Socio de una Big Four", _FUNCIONARIADO.pay(7, _RISKY),
                    Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Sobrevive a 30 reuniones con clientes a los que también "
                          "auditas", "smooth", 30), _good(6), _events(3)),
                    days=BRANCH_DAYS[7], content="big_four", self_employed=True,
                    training=Training(
                        "excedencia_voluntaria",
                        "la excedencia voluntaria y un despacho con vistas a la Castellana (el "
                        "alquiler lo paga el cliente)", _FUNCIONARIADO.course(7, 2.5, _RISKY),
                    ),
                    perk=Perk("Un becario que prepara las reuniones a las cuatro de la "
                              "madrugada: cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    8, "Ingeniero fiscal de multinacionales", _FUNCIONARIADO.pay(8, _RISKY),
                    Mechanic.MEMORY, 60,
                    (Task("perfect", "Traza 15 rutas del dinero sin perder un yapdollar por el "
                          "camino", "perfect_rounds", 15), _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="sandwich:4", self_employed=True,
                    perk=Perk("Un mapa del mundo con los paraísos fiscales marcados en "
                              "fosforito: +5 % de tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    9, "Consejero de una SICAV", _FUNCIONARIADO.pay(9, _RISKY), Mechanic.SPOT, 60,
                    (Task("caught", "Encuentra a 25 inversores de verdad entre los mariachis",
                          "caught", 25), _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="mariachis", self_employed=True,
                    training=Training(
                        "capital_sicav",
                        "el capital mínimo de la SICAV y cien mariachis que firmen donde les "
                        "digas", _FUNCIONARIADO.course(9, 2.5, _RISKY),
                    ),
                    perk=Perk("Un gestor que se acuerda de los nombres de los cien socios (los "
                              "falsos también): un fallo gratis por turno.", retry=True),
                ),
                Position(
                    10, "Asesor de fortunas en Andorra", _FUNCIONARIADO.pay(10, _RISKY),
                    Mechanic.DIALOGUE, 60, (),
                    days=0, content="andorra", self_employed=True,
                    perk=Perk("Un piso en Andorra con luz, agua y el padrón al día (y vacío): "
                              "+10 % de tiempo en el minijuego.", extra_time=0.10),
                ),
            ),
            pension=2_500,
            kind=BranchKind.RISKY,
            retired_as="exinspector jubilado en Andorra que ya no sabe dónde tributa",
        ),
    ),
)  # fmt: skip

_INFLUENCER = PayCurve(4_800)

INFLUENCER = Job(
    key="influencer",
    name="Influencer",
    emoji="🤳",
    blurb="Unos días no te ve ni tu madre y otros te haces viral. Funas incluidas. Y arriba, "
    "la tele pública o el directo desde Andorra.",
    rgb=(233, 30, 99),
    viral=True,
    positions=(
        Position(
            1, "Tiktoker de barrio", 1_700, Mechanic.MEMORY, 30,
            (_good(3), Task("perfect", "Clava 6 coreografías", "perfect_rounds", 6)),
            days=2, content="baile:3",
        ),
        Position(
            2, "Microinfluencer", 2_800, Mechanic.CALC, 30,
            (Task("best", "Acierta 20 métricas", "best", 20), _good(4)),
            days=3, content="metricas",
            training=Training("aro_luz", "el aro de luz y el curso de marca personal", 6_000),
            perk=Perk("El aro de luz que te quita las ojeras (y media cara): +5 % de tiempo "
                      "en el minijuego.", extra_time=0.05),
        ),
        Position(
            3, "Streamer", _INFLUENCER.p3, Mechanic.DIALOGUE, 40,
            (Task("smooth", "Sobrevive a 25 momentos del directo", "smooth", 25), _good(5)),
            days=4, content="directo", self_employed=True,
            training=Training("setup", "el setup de streamer (silla gamer incluida)", 15_000),
            perk=Perk("La silla gamer con lumbar, que te salva la espalda aunque no el "
                      "directo: cada turno cansa un 10 % menos.", battery=0.10),
        ),
        Position(
            4, "Influencer con agencia", _INFLUENCER.pay(4), Mechanic.SPOT, 60,
            (Task("caught", "Rechaza 20 colaboraciones turbias", "caught", 20), _good(5),
             _events(2)),
            days=5, content="colaboraciones", self_employed=True,
            training=Training("agencia", "el contrato con una agencia de representación (se "
                              "queda el 20 %)", _INFLUENCER.course(4, 3)),
            perk=Perk("Un community manager que borra lo que no debes haber dicho: un fallo "
                      "gratis por turno.", retry=True),
        ),
        Position(
            5, "Celebrity de reality", _INFLUENCER.pay(5), Mechanic.DIALOGUE, 60,
            (Task("smooth", "Sal airoso de 25 preguntas en plató", "smooth", 25), _good(6),
             _events(3)),
            days=4, content="plato", self_employed=True,
            training=Training("reality", "el casting de un reality en una isla desierta",
                              _INFLUENCER.course(5, 4)),
            perk=Perk("El guionista del reality, que te sopla qué decir (y a quién): una "
                      "respuesta mala tachada en los diálogos.", fifty=True),
        ),
    ),
    branches=(
        Branch(
            "tele",
            "Tele",
            "📺",
            "Tertulias de todo sin saber de nada, la guerra de las audiencias del access, "
            "consejeros por cuota de partido y Eurovisión. En nómina de la cadena: cobra "
            "menos, se jubila mejor.",
            (
                Position(
                    6, "Tertuliano", _INFLUENCER.pay(6, _SAFE), Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Opina con aplomo de 30 temas que no dominas", "smooth", 30),
                     _good(5), _events(2)),
                    days=BRANCH_DAYS[6], content="tertulia",
                    perk=Perk("Un cartel con tu nombre y la palabra «analista» (nadie sabe de "
                              "qué): cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    7, "Presentador de magacín", _INFLUENCER.pay(7, _SAFE), Mechanic.MEMORY, 60,
                    (Task("perfect", "Cumple la escaleta 15 veces sin que se caiga el programa",
                          "perfect_rounds", 15), _good(6), _events(3)),
                    days=BRANCH_DAYS[7], content="escaleta:4",
                    training=Training(
                        "exclusividad_cadena",
                        "el contrato de exclusividad con la cadena (no puedes ni felicitar la "
                        "Navidad en otra)", _INFLUENCER.course(7, 2.5, _SAFE),
                    ),
                    perk=Perk("Un apuntador con la escaleta en cartulinas fosforitas: +5 % de "
                              "tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    8, "Presentador de prime time", _INFLUENCER.pay(8, _SAFE), Mechanic.SORT, 60,
                    (Task("sorted", "Manda 30 invitados a su bloque del programa", "sorted", 30),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="invitados",
                    perk=Perk("Un pinganillo por el que el director te dice «alarga, alarga»: "
                              "un fallo gratis por turno.", retry=True),
                ),
                Position(
                    9, "Director de cadena", _INFLUENCER.pay(9, _SAFE), Mechanic.SPOT, 60,
                    (Task("caught", "Encuentra a 25 programas que hunden la audiencia",
                          "caught", 25), _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="share",
                    training=Training(
                        "voto_consejo",
                        "el voto del consejo de administración (repartido por cuotas, como "
                        "las sillas)", _INFLUENCER.course(9, 2.5, _SAFE),
                    ),
                    perk=Perk("Un despacho con vistas a la parrilla y un café que sabe a "
                              "share: cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    10, "Presidente de RTVE", _INFLUENCER.pay(10, _SAFE), Mechanic.DIALOGUE, 60, (),
                    days=0, content="rtve",
                    perk=Perk("Un sillón de presidente que gira solo hacia el partido que "
                              "toque: +10 % de tiempo en el minijuego.", extra_time=0.10),
                ),
            ),
            pension=5_000,
            retired_as="expresidente de RTVE con tertulia vitalicia y despacho en Prado del Rey",
        ),
        Branch(
            "andorra",
            "Andorra",
            "🇦🇩",
            "Exiliado fiscal con el directo encendido: subathon desde el Principado, bebida "
            "energética, veladas de boxeo, cripto y, al final, un partido. Autónomo: cobra más, "
            "se jubila peor.",
            (
                Position(
                    6, "Streamer exiliado en Andorra", _INFLUENCER.pay(6, _RISKY),
                    Mechanic.MEMORY, 60,
                    (Task("perfect", "Atiende 15 subathons sin perderte una alerta",
                          "perfect_rounds", 15), _good(5), _events(2)),
                    days=BRANCH_DAYS[6], content="subathon:4", self_employed=True,
                    perk=Perk("Un monitor extra para el chat (el otro es para Hacienda): cada "
                              "turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    7, "Dueño de una bebida energética", _INFLUENCER.pay(7, _RISKY),
                    Mechanic.SORT, 60,
                    (Task("sorted", "Manda 30 ideas de negocio a su cajón", "sorted", 30),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[7], content="marca", self_employed=True,
                    training=Training(
                        "marca_propia",
                        "la marca propia (con su registro en la OEPM y una lata que sabe a "
                        "jarabe)", _INFLUENCER.course(7, 2.5, _RISKY),
                    ),
                    perk=Perk("Una nevera llena de tu propio producto (no la abre ni tu "
                              "madre): +5 % de tiempo en el minijuego.", extra_time=0.05),
                ),
                Position(
                    8, "Organizador de veladas de boxeo", _INFLUENCER.pay(8, _RISKY),
                    Mechanic.DIALOGUE, 60,
                    (Task("smooth", "Vende 30 combates entre streamers", "smooth", 30),
                     _good(6), _events(3)),
                    days=BRANCH_DAYS[8], content="velada", self_employed=True,
                    perk=Perk("Un promotor que te cubre el ojo morado antes del directo: un "
                              "fallo gratis por turno.", retry=True),
                ),
                Position(
                    9, "Gurú cripto", _INFLUENCER.pay(9, _RISKY), Mechanic.SPOT, 60,
                    (Task("caught", "Detecta a 25 proyectos que son un timo", "caught", 25),
                     _good(6), _events(4)),
                    days=BRANCH_DAYS[9], content="rugpull", self_employed=True,
                    training=Training(
                        "whitepaper_ia",
                        "el whitepaper (escrito por una IA en una tarde, con tres gráficos que "
                        "suben)", _INFLUENCER.course(9, 2.5, _RISKY),
                    ),
                    perk=Perk("Un grupo de Telegram con 40.000 «inversores» que dicen «to the "
                              "moon»: cada turno cansa un 5 % menos.", battery=0.05),
                ),
                Position(
                    10, "Fundador de un partido antisistema", _INFLUENCER.pay(10, _RISKY),
                    Mechanic.DIALOGUE, 60, (),
                    days=0, content="partido", self_employed=True,
                    perk=Perk("Un escaño en Estrasburgo con inmunidad y todo: +10 % de tiempo "
                              "en el minijuego.", extra_time=0.10),
                ),
            ),
            pension=2_500,
            kind=BranchKind.RISKY,
            retired_as="streamer jubilado en Andorra que vuelve a España para operarse",
        ),
    ),
)  # fmt: skip

JOBS: tuple[Job, ...] = (
    OBRA,
    HOSTELERIA,
    POLITICA,
    SANIDAD,
    OFICINA,
    REPARTO,
    FUNCIONARIADO,
    INFLUENCER,
)
JOB_BY_KEY: dict[str, Job] = {job.key: job for job in JOBS}

# -- Contenido: cavar --------------------------------------------------------------------
#
# Cada turno trae su plano: unas marcas del suelo son seguras, otras esconden
# algo que romper y otras son roca (no rompes nada, pero no avanzas). El plano
# cambia en cada turno, así que hay que leerlo cada vez.

DIG_MARKS: tuple[str, ...] = ("🟫", "🌱", "💧", "⚡", "🪨", "🟨", "🧱", "🍂")
DIG_DANGERS: tuple[str, ...] = (
    "la tubería del agua",
    "el cable de la luz",
    "la fibra de todo el barrio",
    "la tubería del gas",
    "el alcantarillado",
)
DIG_HINTS: tuple[str, ...] = (
    "👴 Un jubilado señala {mark}: «Ahí, chacho, ahí, que yo lo sé».",
    "👷 El encargado grita desde la caseta: «¡Dale en {mark}!».",
    "🐕 Un perro está escarbando justo en {mark}.",
    "🧓 Otro jubilado discrepa: «Yo cavaría en {mark}».",
)

# -- Contenido: detectar -----------------------------------------------------------------

WORKER_NAMES: tuple[str, ...] = (
    "Yeray",
    "Acoidán",
    "Cristo",
    "Juanma",
    "Airam",
    "Kevin",
    "Tino",
    "Echedey",
    "Dailos",
    "Nauzet",
    "Ayoze",
    "Pepe",
    "Jonathan",
    "Óliver",
    "Guayarmina",
    "Iballa",
)
WORKING: tuple[str, ...] = (
    "⛏️ cavando la zanja del fondo",
    "🧱 subiendo bloques al segundo",
    "📏 midiendo la pared con la cinta",
    "🚚 descargando el camión de arena",
    "🪣 haciendo la mezcla",
    "🔨 montando el encofrado",
    "💧 bebiendo agua (hay 34 °C)",
    "🦺 ajustándose el arnés antes de subir",
    "🪚 cortando tablones",
    "🧹 recogiendo escombro",
    "🚜 moviendo la retro",
    "📋 firmando el albarán del cemento",
    "🔩 apretando la ferralla",
    "🧰 cambiando el disco de la radial",
)
SLACKING: tuple[str, ...] = (
    "📱 lleva 40 minutos «buscando una llave del 13»",
    "🥖 en el bocadillo de las 10 (son las 12:30)",
    "🚬 cuarto cigarro «para pensar la estructura»",
    "📞 hablando con su primo de un coche que vende",
    "😴 «vigilando» la hormigonera con los ojos cerrados",
    "👀 mirando cómo trabaja el de al lado",
    "🪑 sentado en un palé «por la espalda»",
    "🔩 ha ido a la ferretería a por tornillos… hace dos horas",
    "📺 viendo el partido en el móvil detrás de la caseta",
    "🍺 ha bajado al bar «a por hielo»",
    "🪞 peinándose en el retrovisor de la furgoneta",
)
#: Partidas de un presupuesto de obra pública: (concepto, precio normal en Y$).
BUDGET_ITEMS: tuple[tuple[str, int], ...] = (
    ("Saco de cemento", 70),
    ("Palé de bloques", 900),
    ("Hora de retroexcavadora", 450),
    ("Ferralla (100 kg)", 1_100),
    ("Ventana de aluminio", 2_400),
    ("Puerta de entrada", 3_500),
    ("Camión de arena", 1_600),
    ("Andamio (una semana)", 800),
    ("Grifo del baño", 400),
    ("Cuba de escombros", 1_500),
    ("Azulejo (m²)", 250),
    ("Farola", 3_000),
    ("Banco del parque", 1_200),
    ("Placa conmemorativa", 600),
)

# -- Contenido: memoria ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemoryPack:
    """Contenido de un minijuego de memoria.

    Attributes:
        intro: Qué tienes que memorizar (se enseña con la secuencia).
        ask: Qué hacer después (con la secuencia oculta).
        items: `(emoji, nombre)` posibles.
    """

    intro: str
    ask: str
    items: tuple[tuple[str, str], ...]


DISHES: tuple[tuple[str, str], ...] = (
    ("🍺", "Caña"),
    ("🍷", "Vino"),
    ("☕", "Barraquito"),
    ("🥔", "Papas arrugadas"),
    ("🐟", "Vieja sancochada"),
    ("🧀", "Queso asado"),
    ("🥗", "Ensalada"),
    ("🍤", "Gambas"),
    ("🦑", "Calamares"),
    ("🍮", "Quesillo"),
)

MEMORY_PACKS: dict[str, MemoryPack] = {
    "platos": MemoryPack(
        "🧽 Llega una montaña de platos. Lávalos en este orden o el jefe se enfada:",
        "Lávalos en el mismo orden.",
        (
            ("🍽️", "Plato llano"),
            ("🥣", "Bol"),
            ("🍴", "Cubiertos"),
            ("🥛", "Vaso"),
            ("🍳", "Sartén"),
            ("🫖", "Tetera"),
            ("🍲", "Olla"),
        ),
    ),
    "comandas": MemoryPack("🧾 Mesa {n} pide:", "Sírvelo en el mismo orden.", DISHES),
    "cocina": MemoryPack(
        "👨‍🍳 Comanda para la mesa {n}. Emplata en este orden:", "¡Emplata!", DISHES
    ),
    "carteles": MemoryPack(
        "🗺️ Noche de pegada de carteles. El coordinador de campaña te dicta la ruta "
        "(y que no te pille la policía local):",
        "Pega el cartel del candidato en cada sitio, en el mismo orden de la ruta.",
        (
            ("💡", "Farola"),
            ("🚏", "Marquesina de la guagua"),
            ("🗳️", "Colegio electoral"),
            ("🏛️", "Ayuntamiento"),
            ("🌹", "Casa del Pueblo"),
            ("🧱", "Muro del solar"),
            ("🏪", "Persiana del bazar"),
            ("📮", "Buzón de Correos"),
            ("🗿", "Pedestal sin estatua (Ley de Memoria)"),
            ("🚌", "Guagua del IMSERSO"),
        ),
    ),
    "votos": MemoryPack(
        "🗳️ Pleno. El portavoz te hace señas desde su escaño. Vota en este orden:",
        "Vota lo que te ha dicho. Disciplina de partido.",
        (
            ("👍", "Sí"),
            ("👎", "No"),
            ("✋", "Abstención"),
            ("🚪", "Salir del pleno"),
            ("📱", "Voto telemático"),
        ),
    ),
}

# -- Contenido: diálogos -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Dialogue:
    """Una situación con una respuesta buena y dos o tres malas (se barajan al jugar)."""

    situation: str
    good: str
    bad: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DialoguePack:
    """Contenido de un minijuego de diálogo.

    Attributes:
        rule: La regla de oro que se enseña al empezar.
        items: Situaciones.
    """

    rule: str
    items: tuple[Dialogue, ...]


DIALOGUE_PACKS: dict[str, DialoguePack] = {
    "prensa": DialoguePack(
        "🎤 Rueda de prensa. Regla de oro: **no contestes nunca a lo que te preguntan.**",
        (
            Dialogue(
                "¿Conocía las adjudicaciones a la empresa de su cuñado?",
                "Le voy a hablar de lo que importa a la gente: el paro está en mínimos.",
                ("Sí, pero mi cuñado es un gran profesional.", "Me enteré por el BOE, como todos."),
            ),
            Dialogue(
                "¿Va a dimitir?",
                "Lo que este país necesita es estabilidad, y eso es lo que damos.",
                ("Sí, mañana a primera hora.", "Si sale algo más en la prensa, sí."),
            ),
            Dialogue(
                "¿Por qué se reunió de madrugada con un empresario investigado?",
                "Fue un encuentro casual, de esos que pasan en las gasolineras.",
                ("Para hablar del contrato del puerto.", "Porque me invitó a cenar."),
            ),
            Dialogue(
                "¿Cuánto ha costado el viaje oficial en el avión del Gobierno?",
                "Lo relevante es la agenda internacional que hemos cerrado.",
                ("Unos 4.000.000 Y$.", "Fui al concierto, pero era una agenda de trabajo."),
            ),
            Dialogue(
                "¿Va a pactar con quien prometió no pactar nunca?",
                "Hemos cambiado de opinión porque ha cambiado la realidad.",
                ("Sí, porque necesitamos los votos.", "No, nunca. Bueno, ya veremos."),
            ),
            Dialogue(
                "¿Ha leído usted la sentencia?",
                "Respeto profundamente a la justicia y no voy a valorar resoluciones.",
                ("No, ni pienso.", "Sí, y los jueces se han equivocado."),
            ),
            Dialogue(
                "¿Por qué ha subido un 30 % el presupuesto de la obra?",
                "Estamos ante la mayor inversión de la historia de esta isla.",
                ("Hubo comisiones.", "No sé sumar, soy de letras."),
            ),
            Dialogue(
                "¿Qué opina del informe de la UCO?",
                "Es un bulo de la fachosfera y de los medios de siempre.",
                ("Que me pilla de lleno.", "Que es muy completo y muy bien redactado."),
            ),
            Dialogue(
                "¿Su asesor cobraba sin ir a trabajar?",
                "Es un servidor público con una trayectoria impecable.",
                ("Venía los jueves.", "Iba al despacho a cargar el móvil."),
            ),
            Dialogue(
                "¿Cuántos asesores tiene su consejería?",
                "Los necesarios para servir a la ciudadanía.",
                ("Ciento doce.", "No lo sé, nunca los he visto a todos juntos."),
            ),
            Dialogue(
                "¿Por qué no se han presentado los presupuestos?",
                "Seguimos gobernando con unos presupuestos que funcionan.",
                ("Porque no tenemos apoyos.", "Se nos olvidó."),
            ),
            Dialogue(
                "¿Cobró usted dietas por un viaje en el que no estuvo?",
                "Toda mi actividad está publicada en el portal de transparencia.",
                ("Sí, pero poquito.", "Estuve en espíritu."),
            ),
            Dialogue(
                "¿Le ha llamado Puigdemont desde Waterloo?",
                "Hablamos con todas las fuerzas democráticas por la convivencia.",
                ("Sí, desde el maletero de un coche.", "Cada noche, para darme las buenas noches."),
            ),
            Dialogue(
                "Ayuso dice que su ley es comunismo. ¿Qué responde?",
                "Nosotros, a lo nuestro: gobernar para la mayoría social.",
                ("Que tiene razón: la copiamos de Cuba.", "Que a mí también me gusta la fruta."),
            ),
            Dialogue(
                "¿Es verdad que tenía Pegasus en el móvil?",
                "La seguridad del Estado no se comenta en una rueda de prensa.",
                (
                    "Sí, y me escuchan cantar en la ducha.",
                    "Lo tenía, pero me he pasado a un Nokia.",
                ),
            ),
            Dialogue(
                "Feijóo dice que usted miente más que habla.",
                "La oposición vive instalada en el ruido y en la máquina del fango.",
                ("Es que hablo poco.", "Un vaso es un vaso, y una mentira es una mentira."),
            ),
            Dialogue(
                "¿Qué opina de los aranceles de Trump al aceite?",
                "Defenderemos a nuestros sectores productivos de la mano de Europa.",
                ("Que nos hunde el olivar.", "Que le mando papas arrugadas a ver si se ablanda."),
            ),
            Dialogue(
                "¿En qué se han gastado los fondos europeos?",
                "Es la mayor transformación de nuestra economía en décadas.",
                ("¿Qué? ¿Qué? ¡Toma mangó!", "En un PERTE de cosas variadas."),
            ),
        ),
    ),
    "comision": DialoguePack(
        "🏛️ Comisión de investigación. Regla de oro: **no te consta, no lo recuerdas y "
        "la culpa es del Gobierno anterior.**",
        (
            Dialogue(
                "¿Firmó usted el contrato de las mascarillas?",
                "No me consta. Eso se gestionaba a otro nivel.",
                ("Sí, aquí está mi firma.", "Lo firmé sin leer, como todo."),
            ),
            Dialogue(
                "¿Cómo explica los 40.000.000 Y$ en efectivo?",
                "No lo recuerdo, señoría. Han pasado muchos meses.",
                ("Ahorros del Bizum.", "Me tocó la lotería tres veces."),
            ),
            Dialogue(
                "¿Por qué el déficit se ha disparado bajo su mandato?",
                "Heredamos una situación desastrosa del Gobierno anterior.",
                ("Gastamos más de lo que teníamos.", "No sé qué es el déficit."),
            ),
            Dialogue(
                "¿Conoce a este señor de la foto?",
                "Me hago muchas fotos al día, señoría. No puedo conocer a todo el mundo.",
                ("Sí, es mi socio.", "Es mi chófer, mi testaferro y mi amigo."),
            ),
            Dialogue(
                "¿Dónde estaba la noche del 14?",
                "En un acto del partido, rodeado de compañeros que lo pueden confirmar.",
                ("En un bar con el empresario.", "En Andorra, de compras."),
            ),
            Dialogue(
                "¿Ha borrado los mensajes de su móvil?",
                "Mi teléfono se actualizó solo y se perdió todo, una pena.",
                ("Sí, todos, esa misma noche.", "Lo tiré al mar en Puerto Rico."),
            ),
            Dialogue(
                "¿Quién decidió adjudicar sin concurso?",
                "Fue una decisión técnica, colegiada y ajustada a derecho.",
                ("Yo, por teléfono.", "Lo echamos a piedra, papel o tijera."),
            ),
            Dialogue(
                "¿Por qué su exmujer trabaja en una empresa pública?",
                "Accedió por sus méritos, como cualquier otra ciudadana.",
                ("Para que no me pidiera pensión.", "Porque se lo prometí en el divorcio."),
            ),
            Dialogue(
                "¿Va a entregar los documentos que se le pidieron?",
                "Colaboraremos con esta comisión en todo lo que permita la ley.",
                ("No, se mojaron.", "Sí, los que no he quemado."),
            ),
            Dialogue(
                "¿Le mandó usted mensajes de ánimo al tesorero?",
                "No me consta haber enviado mensajes de esa naturaleza.",
                ("Sí: «Sé fuerte».", "Le mandé un sticker de un gatito con casco."),
            ),
            Dialogue(
                "¿Qué hacía de madrugada en el aeropuerto con una vicepresidenta venezolana?",
                "Fue un encuentro fortuito en una zona de tránsito, señoría.",
                ("Que no pisara suelo español, técnicamente.", "Traía unas maletas; no pregunté."),
            ),
            Dialogue(
                "¿Sabe usted lo que es una caja B?",
                "Conozco la contabilidad del partido, que está auditada y es ejemplar.",
                ("La que va debajo de la caja A.", "La llevaba en el maletero, como todo."),
            ),
            Dialogue(
                "¿Qué recuerda de aquella cena con el comisionista?",
                "Señoría, recuerdo que se habló de fútbol. Nada más.",
                ("Que pidió café… ¡Quiero café!", "Que pagó él, en chistorras."),
            ),
        ),
    ),
    "consejo": DialoguePack(
        "💼 Consejo de administración de la eléctrica. Regla de oro: **asiente, no "
        "preguntes y cobra.**",
        (
            Dialogue(
                "El presidente propone subir la tarifa un 15 % en invierno.",
                "Me parece una decisión valiente y necesaria.",
                ("¿Y la gente que no pueda pagar?", "Propongo bajarla, que es Navidad."),
            ),
            Dialogue(
                "Hay que pedirle al Gobierno una ayuda de 200 millones.",
                "Yo me encargo: tengo el móvil de medio Consejo de Ministros.",
                ("¿No dimos beneficios récord?", "Mejor no, que queda feo."),
            ),
            Dialogue(
                "Toca votar los bonus de la dirección.",
                "A favor. Y propongo que el mío lleve decimales.",
                ("En contra, eso es mucho.", "Me abstengo, que tengo conciencia."),
            ),
            Dialogue(
                "¿Alguien tiene alguna pregunta sobre las cuentas?",
                "Ninguna. Están clarísimas.",
                ("Yo: ¿por qué hay una filial en las Caimán?", "¿Qué es un balance?"),
            ),
            Dialogue(
                "Un periodista pregunta por qué cobra usted 30.000.000 Y$ por ir una vez al mes.",
                "Por mi dilatada experiencia en el sector, adquirida en el ministerio.",
                ("Porque fui ministro, básicamente.", "Por no hacer preguntas."),
            ),
            Dialogue(
                "Se propone cortar la luz a los morosos en ola de frío.",
                "Hay que ser responsables con los accionistas.",
                ("Me parece una salvajada.", "¿Y si les mandamos una manta?"),
            ),
            Dialogue(
                "Hay que elegir dónde se celebra la junta: en Madrid o en Maldivas.",
                "En Maldivas, por la sostenibilidad del equipo.",
                ("En Madrid, que es más barato.", "Por Zoom, que ahorramos."),
            ),
            Dialogue(
                "El presidente se duerme a mitad de su propio discurso.",
                "Aplaudes fuerte para que se despierte con dignidad.",
                ("Le haces una foto para el grupo.", "Te vas a casa, que ya has cobrado."),
            ),
            Dialogue(
                "Un consejero pregunta si las placas solares nos quitan el negocio.",
                "Diversificamos: cobramos el sol al precio del gas.",
                ("Pues sí, el sol no se puede facturar.", "Propongo bajar la luz por solidaridad."),
            ),
            Dialogue(
                "Trump amenaza con aranceles al gas europeo.",
                "Una gran oportunidad para revisar las tarifas al alza.",
                ("Habrá que bajar márgenes.", "Le llamamos y le pedimos perdón en inglés."),
            ),
            Dialogue(
                "Llega al consejo un exministro, recién salido del Gobierno.",
                "Bienvenido: aquí tu experiencia vale oro, y tu agenda, más.",
                (
                    "¿Y tú qué sabes de electricidad?",
                    "Pedimos antes un informe de incompatibilidades.",
                ),
            ),
        ),
    ),
    "chiringuito": DialoguePack(
        "🏖️ Tu chiringuito. Regla de oro: **el cliente siempre tiene razón… hasta que paga.**",
        (
            Dialogue(
                "Un guiri pide paella a las seis de la tarde.",
                "«Of course, my friend!». Y le cobras la paella a precio de langosta.",
                ("«Aquí no se come paella a esa hora».", "Le das la del mediodía recalentada."),
            ),
            Dialogue(
                "Llega la inspectora de Sanidad sin avisar.",
                "La invitas a un barraquito y le enseñas el registro de temperaturas al día.",
                ("Escondes los calamares en el baño.", "Le dices que vuelva el lunes."),
            ),
            Dialogue(
                "Una mesa de doce se quiere ir sin pagar.",
                "Te pones en la puerta con el datáfono y una sonrisa.",
                ("Les tiras un plato.", "Llamas a tu primo el de la moto."),
            ),
            Dialogue(
                "Un cliente pide la hoja de reclamaciones.",
                "Se la das con un chupito de ron miel «para que escriba más tranquilo».",
                ("Le dices que se ha acabado.", "Te la comes delante de él."),
            ),
            Dialogue(
                "El camarero nuevo quiere cobrar las horas extra.",
                "Se las pagas y lo apuntas todo: Inspección de Trabajo ronda la playa.",
                ("«Aquí somos una familia».", "Le pagas en croquetas."),
            ),
            Dialogue(
                "Un influencer pide comer gratis a cambio de una historia en Instagram.",
                "Le ofreces un 10 % si te etiqueta. La caja no se paga con likes.",
                ("Le das el menú entero gratis.", "Lo echas a gritos."),
            ),
            Dialogue(
                "Se acaba el queso asado un sábado a las dos.",
                "Recomiendas el especial del día con mucho arte.",
                ("Dices que es tendencia que no haya.", "Asas queso de sándwich."),
            ),
            Dialogue(
                "Viene un concejal a pedirte «una ayuda» para la licencia de la terraza.",
                "Le dices que lo tramitas todo por la sede electrónica, gracias.",
                ("Le das un sobre.", "Le das un sobre más grande."),
            ),
        ),
    ),
}

# -- Contenido de sanidad y oficina ------------------------------------------------------

MEMORY_PACKS.update(
    {
        "camilla": MemoryPack(
            "🛏️ Te dictan el traslado del paciente. Ruta:",
            "Lleva la camilla por la misma ruta, en orden.",
            (("🚑", "Urgencias"), ("🩻", "Rayos"), ("🧪", "Laboratorio"), ("🛏️", "Planta 3"),
             ("🔪", "Quirófano"), ("🛗", "Ascensor de servicio"), ("☕", "Cafetería")),
        ),
        "ronda": MemoryPack(
            "📋 Ronda de la planta. La supervisora te dice el orden:",
            "Haz la ronda en orden.",
            (("🍪", "Merienda"), ("🌡️", "Temperatura"), ("💊", "Pastillero"),
             ("🛏️", "Cambio de sábanas"), ("🚽", "Cuña"), ("🩸", "Glucemia"),
             ("📺", "Mando de la tele")),
        ),
        "cafes": MemoryPack(
            "☕ El equipo te pide los cafés. Apunta (de memoria, claro):",
            "Reparte los cafés en el mismo orden.",
            (("☕", "Solo"), ("🥛", "Con leche"), ("🍵", "Té verde"), ("🧋", "Té con perlas"),
             ("🥃", "Barraquito"), ("🧊", "Con hielo"),
             ("🌱", "Avena templada, sin lactosa, descafeinado")),
        ),
    }
)  # fmt: skip

#: Triaje: pacientes que tienen que pasar YA y pacientes que pueden esperar (mucho).
TRIAGE_URGENT: tuple[str, ...] = (
    "Dolor en el pecho que se le va al brazo izquierdo; está sudando",
    "No puede respirar y tiene los labios morados",
    "Se ha caído de un andamio y no mueve las piernas",
    "Habla raro y tiene media cara caída desde hace 20 minutos",
    "Sangra mucho por un corte en el muslo",
    "Fiebre de 40 y manchas que no se borran al apretarlas",
    "Se le hincha la cara después de comer gambas y le cuesta tragar",
    "Está convulsionando en la sala de espera",
)
TRIAGE_TRIVIAL: tuple[str, ...] = (
    "Catarro de hace tres semanas; ha venido a las 4 de la mañana",
    "Le duele un dedo desde 2019",
    "Viene a por la receta de siempre",
    "Quiere un justificante para el trabajo",
    "Ha leído en Google que lo suyo es muy grave",
    "Una picadura de mosquito «muy fea»",
    "Que le miren un lunar que tiene desde niño",
    "Resaca de las fiestas del pueblo",
    "Le pica la garganta desde esta tarde",
    "Quiere un análisis «de todo», por si acaso",
    "Acompaña a su madre, pero aprovecha para preguntar lo suyo",
    "Se ha cortado con un folio",
)

#: Código con un bug en una línea: (líneas, índice de la línea con el bug).
CODE_BUGS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("def media(numeros):", "    if not numeros:", "        return 0",
      "    total = sum(numeros)", "    return total / len(numeros) + 1"), 4),
    (("precio = 100", "igic = 0.07", "total = precio * igic", "print(total)",
      "guardar_factura(total)"), 2),
    (("usuarios = cargar_usuarios()", "for u in usuarios:", "    if u.activo = True:",
      "        avisar(u)", "print('hecho')"), 2),
    (("PASSWORD = 'admin123'", "def entrar(clave):", "    return clave == PASSWORD",
      "if entrar(input()):", "    abrir_panel()"), 0),
    (("nombres = ['Ana', 'Yeray']", "print(nombres[0])", "print(nombres[1])",
      "print(nombres[2])", "print('fin')"), 3),
    (("total = 0", "for pedido in pedidos:", "    total = pedido.importe",
      "print(total)", "enviar_informe(total)"), 2),
    (("import time", "def pausa_corta():", "    time.sleep(3600)  # un segundo",
      "pausa_corta()", "seguir()"), 2),
    (("def es_admin(usuario):", "    if usuario.rol == 'admin' or True:",
      "        return True", "    return False", "borrar_todo() if es_admin(yo) else None"), 1),
    (("edad = input('¿Edad? ')", "if edad >= 18:", "    entrar()", "else:",
      "    print('Vuelve en unos años')"), 0),
    (("dias = 7", "horas_por_dia = 24", "horas = dias + horas_por_dia",
      "print(f'{horas} horas')", "programar_backup(horas)"), 2),
    (("for i in range(1, len(lista)):", "    procesar(lista[i])", "print('procesados')",
      "guardar(lista)", "avisar_al_jefe()"), 0),
)  # fmt: skip

#: Revisiones de código: lo que rompe producción y lo que se puede aprobar.
PR_DANGER: tuple[str, ...] = (
    "Quitar el WHERE del UPDATE de precios",
    "Subir el .env con las contraseñas al repositorio",
    "Actualizar las 300 dependencias a la vez",
    "Desactivar los tests «que fallaban a veces»",
    "Poner el IVA al 21 % (estamos en Canarias)",
    "Borrar la carpeta /backups para ganar espacio",
    "Desplegar en producción sin revisión, que es viernes",
    "Renombrar la tabla de usuarios «porque sí»",
    "Poner a 0 el timeout de la base de datos",
    "Guardar las contraseñas en texto plano «para depurar»",
)
PR_SAFE: tuple[str, ...] = (
    "Corregir una errata en el README",
    "Añadir pruebas al cálculo del IGIC",
    "Mejorar el mensaje de error del login",
    "Subir la versión de una librería, con su changelog leído",
    "Documentar la API de pedidos",
    "Quitar código muerto que nadie usa",
    "Añadir un índice a la tabla de pedidos",
    "Traducir los textos del panel al español",
    "Arreglar el formato de las fechas a dd/mm",
)

DIALOGUE_PACKS.update(
    {
        "mir": DialoguePack(
            "📚 Pregunta tipo MIR. Regla de oro: **la respuesta prudente suele ser la buena.**",
            (
                Dialogue(
                    "Paciente de 25 años, a las 4 de la mañana en Urgencias por un dolor de "
                    "garganta de hace tres días, sin fiebre. ¿Actitud?",
                    "Explorarle, darle un analgésico y que lo siga su médico de cabecera.",
                    ("TAC de cuerpo entero.", "Ingreso en la UCI, por si acaso.",
                     "Antibiótico «por si es una bacteria»."),
                ),
                Dialogue(
                    "¿Cuál es la causa más frecuente de que un residente no haya dormido?",
                    "La guardia.",
                    ("El café.", "Las series.", "Los vecinos."),
                ),
                Dialogue(
                    "Un paciente exige antibiótico para un catarro. ¿Qué haces?",
                    "Le explicas que los antibióticos no sirven contra los virus.",
                    ("Se lo das para que se vaya.", "Le das dos, por si uno falla.",
                     "Le recetas mojo picón."),
                ),
                Dialogue(
                    "Llega un paciente con el diagnóstico impreso de internet. ¿Primer paso?",
                    "Escucharle, explorarle y luego comentar lo que ha leído.",
                    ("Tirar el papel a la papelera.", "Darle la razón en todo.",
                     "Buscarlo tú también, delante de él."),
                ),
                Dialogue(
                    "Paciente de 70 años que se cae en casa, no recuerda el golpe y toma "
                    "anticoagulantes. ¿Actitud?",
                    "Valorar un TAC craneal: con anticoagulantes, un golpe en la cabeza no se "
                    "ignora.",
                    ("Paracetamol y a casa.", "Que vuelva si le duele.", "Una tirita."),
                ),
                Dialogue(
                    "Te piden una baja de tres meses por «estrés postvacacional». ¿Qué haces?",
                    "Valoras si hay un motivo médico; si no lo hay, no la firmas.",
                    ("La firmas sin mirar.", "Firmas seis meses, ya puestos.",
                     "Te la coges tú."),
                ),
                Dialogue(
                    "Un adjunto te dice «esto lo has hecho fatal» delante del paciente. Esto es…",
                    "Un mal ejemplo de docencia.",
                    ("Docencia de calidad.", "Tradición.", "Motivación."),
                ),
                Dialogue(
                    "Ante una parada cardiorrespiratoria, ¿qué es lo primero?",
                    "Pedir ayuda y empezar las compresiones torácicas.",
                    ("Buscar su historia clínica.", "Hacerle una foto para el grupo.",
                     "Esperar al adjunto."),
                ),
                Dialogue(
                    "Paciente diabético, sudoroso y con una glucemia de 40. ¿Qué haces?",
                    "Darle azúcar o glucosa de inmediato.",
                    ("Ponerle insulina.", "Que siga en ayunas.",
                     "Esperar al análisis de mañana."),
                ),
                Dialogue(
                    "¿Cuánto dura una guardia de residente, según la leyenda del hospital?",
                    "24 horas, y luego te vas (en teoría).",
                    ("8 horas.", "Las que hagan falta.", "Hasta que te desmayes."),
                ),
            ),
        ),
        "consulta": DialoguePack(
            "🩺 Consulta de cinco minutos. Regla de oro: **claro, amable y breve (fuera "
            "esperan 40).**",
            (
                Dialogue(
                    "«Doctor, he leído en internet que lo mío es gravísimo».",
                    "Vamos a ver qué tiene usted, no lo que dice internet.",
                    ("Internet sabe más que yo.", "Pues sí, gravísimo."),
                ),
                Dialogue(
                    "Un familiar exige un TAC «porque él paga sus impuestos».",
                    "Le explicas qué pruebas hacen falta y por qué.",
                    ("Le haces tres TAC.", "Le dices que Perro Sanxe no paga TACs."),
                ),
                Dialogue(
                    "La gerencia quiere que veas 60 pacientes al día.",
                    "Pides refuerzos por escrito.",
                    ("Cinco minutos por paciente, y si no, tres.", "Atiendes a dos a la vez."),
                ),
                Dialogue(
                    "Un visitador médico te deja bolis y te ofrece un congreso.",
                    "Le das las gracias y te quedas con la evidencia científica.",
                    ("Recetas su fármaco a todo el mundo.", "Le pides el crucero."),
                ),
                Dialogue(
                    "«No me tomé las pastillas porque ya me encontraba bien».",
                    "Le explicas por qué hay que terminar el tratamiento.",
                    ("¡Bien hecho!", "Le riñes y le echas."),
                ),
                Dialogue(
                    "Un paciente te trae un queso de regalo.",
                    "Lo aceptas con una sonrisa: un queso no es un soborno.",
                    ("Le pides también un jamón.", "Llamas a la UCO."),
                ),
                Dialogue(
                    "Llevas 40 pacientes y entra uno «solo para una preguntita».",
                    "La pregunta, y si hay más, otra cita.",
                    ("Le cuentas tu vida.", "Le cobras la pregunta."),
                ),
                Dialogue(
                    "El jefe de servicio te llama un domingo.",
                    "Contestas si estás de guardia; si no, el lunes.",
                    ("Vas al hospital en pijama.", "Cambias de número."),
                ),
            ),
        ),
        "reuniones": DialoguePack(
            "📅 Reunión. Regla de oro: **gana la frase que acaba antes la reunión.**",
            (
                Dialogue(
                    "El de producto quiere «darle una vuelta» a lo que aprobasteis ayer.",
                    "Lo hablamos offline y te mando un resumen.",
                    ("Démosle esa vuelta ahora, que tenemos dos horas.",
                     "¿Y si hacemos un taller de ideación?"),
                ),
                Dialogue(
                    "Alguien comparte pantalla y no se ve nada.",
                    "Mándalo por el chat y seguimos.",
                    ("Espera, que reinicio el ordenador.", "Probemos con otra plataforma."),
                ),
                Dialogue(
                    "Faltan las dos personas que tienen que decidir.",
                    "La movemos a cuando estén y aprovechamos el rato.",
                    ("Les esperamos 20 minutos.", "Decidimos sin ellos y ya se enterarán."),
                ),
                Dialogue(
                    "Te piden una estimación «aproximada».",
                    "Mañana te digo algo cerrado, cuando lo haya mirado.",
                    ("Dos semanas (serán seis).", "Lo que tarde, tarde."),
                ),
                Dialogue(
                    "A las dos menos cinco, alguien dice «solo una cosita rápida».",
                    "Escríbela en el canal y la vemos después de comer.",
                    ("Claro, dime.", "Pedimos comida y seguimos."),
                ),
                Dialogue(
                    "Empieza la guerra de tabuladores contra espacios.",
                    "Lo decide el formateador y seguimos.",
                    ("Votación a mano alzada.", "Os dejo pelear, avisadme."),
                ),
                Dialogue(
                    "Te convocan a una reunión para preparar otra reunión.",
                    "Os paso un documento y hacemos corta la reunión de verdad.",
                    ("Acepto y propongo una tercera.", "Voy, con la cámara apagada y dormido."),
                ),
                Dialogue(
                    "El jefe pregunta quién se encarga.",
                    "Lo asigno ahora mismo y lo apunto en el ticket.",
                    ("Todos y ninguno.", "El becario."),
                ),
            ),
        ),
        "inversores": DialoguePack(
            "🦄 Ronda de inversión. Regla de oro: **cuanto más humo, más dinero.**",
            (
                Dialogue(
                    "«¿Cuál es vuestro modelo de negocio?»",
                    "Somos la plataforma de IA generativa para la economía circular.",
                    ("Vender cosas y ganar más de lo que gastamos.", "Todavía no tenemos."),
                ),
                Dialogue(
                    "«¿Cuántos usuarios tenéis?»",
                    "Crecemos de forma exponencial, con una comunidad muy comprometida.",
                    ("Catorce, y nueve son mi familia.", "Activos, ninguno."),
                ),
                Dialogue(
                    "«¿Por qué perdéis tanto dinero?»",
                    "Estamos invirtiendo en crecimiento antes de monetizar.",
                    ("Porque gastamos más de lo que entra.", "Por el futbolín de la oficina."),
                ),
                Dialogue(
                    "«¿Cuál es vuestra ventaja competitiva?»",
                    "Un algoritmo propio y un equipo de primer nivel.",
                    ("Que somos majos.", "Que copiamos muy rápido."),
                ),
                Dialogue(
                    "«¿Cuándo seréis rentables?»",
                    "Tenemos un camino claro a la rentabilidad en 18 meses.",
                    ("Nunca.", "Cuando nos compre alguien."),
                ),
                Dialogue(
                    "«¿Qué haréis con el dinero?»",
                    "Escalar el equipo y entrar en nuevos mercados.",
                    ("Una oficina con tobogán.", "Pagar las deudas."),
                ),
                Dialogue(
                    "«¿Y la competencia?»",
                    "No tenemos competencia directa: hemos creado una categoría nueva.",
                    ("Hay doscientas empresas iguales.", "Google lo hace gratis."),
                ),
                Dialogue(
                    "«¿Habéis pensado en blockchain?»",
                    "Está en nuestra hoja de ruta para la fase dos.",
                    ("¿Eso sigue existiendo?", "No, gracias."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: clasificar ---------------------------------------------------------------
#
# Cada ronda trae un papel o una persona y hay que mandarla al sitio correcto.
# Los sitios (botones) son los mismos toda la partida.


@dataclass(frozen=True, slots=True)
class SortPack:
    """Contenido de un minijuego de clasificar.

    Attributes:
        rule: Lo que se enseña arriba toda la partida.
        ask: Encabezado de cada ronda (`{n}` y `{total}`).
        bins: Sitios donde clasificar (los botones), de 2 a 5.
        items: `(texto, índice del sitio correcto)`.
    """

    rule: str
    ask: str
    bins: tuple[str, ...]
    items: tuple[tuple[str, int], ...]


SORT_PACKS: dict[str, SortPack] = {
    "ventanilla": SortPack(
        "🪟 Eres el interino de la ventanilla única. Manda a cada vecino a su negociado.",
        "🙋 Vecino {n}/{total}:",
        ("🏠 Padrón", "💶 Recaudación", "🏗️ Urbanismo", "🤝 Servicios sociales"),
        (
            ("Necesita un certificado de empadronamiento para la beca", 0),
            ("Se ha mudado al municipio y quiere darse de alta", 0),
            ("Ha cambiado de piso dentro del mismo pueblo", 0),
            ("Quiere pagar el IBI, que le venció ayer", 1),
            ("Le ha llegado el recibo de la basura dos veces", 1),
            ("Pide fraccionar el impuesto de circulación", 1),
            ("Quiere licencia para cerrar la azotea", 2),
            ("Denuncia que el vecino ha hecho una piscina sin permiso", 2),
            ("Pide licencia de obra menor para cambiar el baño", 2),
            ("Solicita ayuda a domicilio para su madre", 3),
            ("Pregunta por la ayuda de emergencia social", 3),
            ("Quiere pedir la valoración de la dependencia", 3),
        ),
    ),
    "registro": SortPack(
        "📮 Eres auxiliar en el registro. Cada papel va a su sitio (y el spam, a la papelera).",
        "📄 Papel {n}/{total}:",
        ("📨 Entrada", "📤 Salida", "🗄️ Archivo", "🗑️ Papelera"),
        (
            ("Una instancia que acaba de traer un vecino", 0),
            ("Un recurso de alzada presentado hoy", 0),
            ("La factura de un proveedor del ayuntamiento", 0),
            ("La notificación de una multa que manda el ayuntamiento", 1),
            ("Un requerimiento de documentación para un vecino", 1),
            ("La resolución de una subvención que hay que notificar", 1),
            ("Un expediente cerrado de 1994", 2),
            ("Las actas del pleno del año pasado", 2),
            ("Un expediente ya resuelto y notificado", 2),
            ("Un correo de un príncipe extranjero para el alcalde", 3),
            ("Publicidad de una academia de oposiciones", 3),
            ("Una cadena de WhatsApp impresa «para que la vea el concejal»", 3),
        ),
    ),
    "decretos": SortPack(
        "📜 Eres secretario de Estado. Cada papel de la mesa va a su sitio: decreto, "
        "moneda de cambio, filtración o cajón.",
        "🗂️ Papel {n}/{total}:",
        (
            "📜 Real Decreto-ley",
            "🤝 Moneda de cambio con socios",
            "📰 Filtrar a la prensa",
            "🗄️ Al cajón",
        ),
        (
            ("Una medida urgente que hay que aprobar el martes sin pasar por comisión", 0),
            ("Una prórroga de ayudas que caduca el día 31", 0),
            ("Un cambio fiscal que no puede esperar al trámite parlamentario", 0),
            ("Un traspaso de competencias que pide un partido de la investidura", 1),
            ("Una partida para las infraestructuras de un socio", 1),
            ("Una comisión bilateral que un grupo exige para apoyar los presupuestos", 1),
            ("Un indulto que sirve para asegurar siete votos", 1),
            ("El borrador de una subida de impuestos, para ver cómo reacciona la gente", 2),
            ("La lista de nombres de la próxima remodelación del Gobierno", 2),
            ("Un informe incómodo sobre el ministro de al lado", 2),
            ("Un dato favorable del paro que conviene que salga hoy", 2),
            ("Una propuesta de reforma de hace diez legislaturas que nadie ha vuelto a abrir", 3),
            ("El informe que dice que el plan estrella no funciona", 3),
            ("Una iniciativa popular con medio millón de firmas", 3),
            ("Una promesa electoral de 2019 que ya nadie recuerda", 3),
            ("Una queja del Tribunal de Cuentas que «ya se verá»", 3),
            ("Una ley de transparencia que nunca llegó a la mesa", 3),
            ("Un informe de la UCO que salpica a tu jefe de gabinete", 3),
            ("La respuesta a los aranceles de Trump, antes de que abran las bolsas", 0),
            ("La amnistía que piden siete votos desde Waterloo", 1),
            ("La fecha de una exhumación, justo antes de las elecciones", 2),
        ),
    ),
    "enmiendas": SortPack(
        "🇪🇺 Eres ponente de un reglamento. Cada enmienda va a su sitio: aceptar, "
        "transaccional, rechazar o inadmisible.",
        "✏️ Enmienda {n}/{total}:",
        ("✅ Aceptar", "🔀 Transaccional", "❌ Rechazar", "🗑️ Inadmisible"),
        (
            ("Corrige una errata de numeración en el artículo 12", 0),
            ("Añade un «a más tardar» que todos los grupos piden", 0),
            ("Precisa una definición que ya nadie discute", 0),
            ("Aclara que la fecha del artículo 3 se refiere a días hábiles", 0),
            ("Pide fusionar dos propuestas de redacción casi idénticas", 1),
            ("Quiere cambiar «obligatorio» por «recomendado», pero con matices", 1),
            ("Propone una cifra intermedia entre las dos enmiendas enfrentadas", 1),
            ("Combina la idea de dos grupos en un único texto de compromiso", 1),
            ("Elimina todo el reglamento y propone no hacer nada", 2),
            ("Exige multiplicar por diez las sanciones", 2),
            ("Pide que el reglamento no se aplique a ningún país", 2),
            ("Duplica los costes de aplicación sin explicar de dónde sale el dinero", 2),
            ("Introduce un tema que no tiene nada que ver con el reglamento", 3),
            ("Modifica un artículo de otro reglamento ya aprobado", 3),
            ("Contradice un artículo del Tratado de Funcionamiento de la UE", 3),
            ("Llega fuera de plazo, entregada en mano a las tres de la mañana", 3),
            ("Propone legislar sobre una competencia que es solo de los Estados miembros", 3),
            ("Obliga a aceptar las cookies dos veces, por si acaso", 2),
            ("Rebautiza el reglamento de pesca como «Ley Eurovisión»", 3),
            ("Une la propuesta del tapón pegado y la de los bricks en un solo artículo", 1),
        ),
    ),
}

# -- Contenido: calcular -----------------------------------------------------------------
#
# Cada ronda trae varias ofertas con números y hay que quedarse con la mejor
# según una cuenta (euros por kilómetro, me gusta por visita). La mejor saca
# siempre un margen claro a la segunda (`work_games.CALC_MARGIN`).

#: Restaurantes de los pedidos del reparto.
RESTAURANTS: tuple[str, ...] = (
    "🍔 Burger del polígono",
    "🍕 Pizzería Vesubio",
    "🥙 Kebab de la esquina",
    "🍣 Sushi de Mesa y López",
    "🥡 Chino del barrio",
    "🍗 Pollos asados Chacho",
    "🥗 Poke «healthy»",
    "🌮 Tacos de Triana",
    "🧁 Dulcería de Vegueta",
    "🍝 Italiano de Las Canteras",
)
#: Temas de los vídeos del influencer.
VIDEO_TOPICS: tuple[str, ...] = (
    "📹 Probando el gofio por primera vez",
    "📹 Mi rutina de mañana (a las 13:00)",
    "📹 Reacciono a los precios de Maspalomas",
    "📹 Un día en mi vida de influencer",
    "📹 Le pongo nota a los barraquitos",
    "📹 Sigo la receta de mi abuela",
    "📹 Abro el paquete que me mandó una marca",
    "📹 Bailo el trend con mi madre",
    "📹 Cuento mi ruptura (con música triste)",
    "📹 Pruebo la comida del aeropuerto",
)

# -- Contenido: memoria, detectar y diálogos de los oficios nuevos ---------------------------

MEMORY_PACKS.update(
    {
        "entregas": MemoryPack(
            "📱 La app te encadena pedidos. Ruta {n}:",
            "Entrega en el mismo orden (el cliente valora con estrellas).",
            (("🍕", "Pizzería"), ("🏢", "Oficina del 7.º"), ("🏠", "Chalé con perro"),
             ("🏫", "Residencia de estudiantes"), ("🏥", "Hospital (turno de noche)"),
             ("⛱️", "Chiringuito de Las Canteras"), ("🅿️", "Parking del centro comercial")),
        ),
        "sellos": MemoryPack(
            "📋 Expediente {n}. El jefe de negociado te dicta el circuito de firmas:",
            "Pasa el expediente por las mismas mesas, en orden.",
            (("📥", "Registro de entrada"), ("🖋️", "Firma"), ("🔖", "Sello"),
             ("📑", "Compulsa"), ("🗂️", "Archivo"), ("📎", "Grapa"), ("☕", "Desayuno")),
        ),
        "baile": MemoryPack(
            "🎵 Trend nuevo. La coreografía va así (toma {n}):",
            "Repite la coreografía en orden. Grabando…",
            (("👈", "Izquierda"), ("👉", "Derecha"), ("🙌", "Manos arriba"), ("💃", "Giro"),
             ("🦵", "Patada"), ("🫶", "Corazón"), ("🤸", "Voltereta")),
        ),
        "socios": MemoryPack(
            "🤝 Cena con los socios de investidura. Esto es lo que les prometiste, por "
            "orden (ronda {n}):",
            "Recuerda en el mismo orden lo que prometiste a cada socio.",
            (
                ("🚆", "Un tren"),
                ("💶", "Transferencias"),
                ("🏛️", "Una comisión"),
                ("📜", "Un indulto"),
                ("🌉", "Un puente"),
                ("🏥", "Un hospital"),
                ("🗣️", "Un traspaso de competencias"),
                ("🚧", "Una autovía"),
                ("🎫", "Una cartera"),
                ("🧳", "Un viaje a Waterloo"),
                ("🕊️", "Una amnistía"),
                ("🚂", "El Cercanías transferido"),
            ),
        ),
        "plenos": MemoryPack(
            "🇪🇺 Pleno en Estrasburgo. Esta es la secuencia de votaciones de hoy "
            "(tanda {n}):",
            "Vota en el mismo orden que la lista de votación.",
            (
                ("👍", "A favor"),
                ("👎", "En contra"),
                ("✋", "Abstención"),
                ("✏️", "Enmienda"),
                ("✂️", "Votación por partes"),
                ("🔒", "Votación secreta"),
                ("🙋", "Votación nominal"),
            ),
        ),
    }
)  # fmt: skip

#: Jefe de flota: lo que hacen los riders currando y lo que hace el que se come el pedido.
RIDER_WORKING: tuple[str, ...] = (
    "🛵 entregando en un 4.º sin ascensor",
    "📍 esperando en la puerta del restaurante",
    "🌧️ pedaleando bajo la lluvia",
    "🧾 confirmando la entrega en la app",
    "🔋 cambiando la batería de la bici",
    "🗺️ buscando el portal B (no existe)",
    "📦 recogiendo tres pedidos a la vez",
    "🧊 metiendo los helados en la mochila térmica",
    "🚦 parado en el semáforo, como manda la ley",
    "📞 llamando al cliente, que no abre",
)
RIDER_SLACKING: tuple[str, ...] = (
    "🍟 se está comiendo las papas del pedido",
    "🥤 se ha bebido el refresco «porque se iba a derramar»",
    "🍕 ha abierto la pizza «para ver si estaba bien»",
    "📵 tiene la app en «ocupado» y está en la playa",
    "😴 lleva una hora «en un atasco» en su sofá",
    "🍦 se ha comido el helado «porque se derretía»",
    "🔄 acepta y cancela pedidos hasta que le sale uno bueno",
)

#: Inspector de Hacienda: declaraciones con fraude y declaraciones normales.
TAX_FRAUD: tuple[str, ...] = (
    "Declara 900.000 Y$ al año y tiene tres deportivos a su nombre",
    "Dice vivir en Andorra, pero sube stories desde Maspalomas cada día",
    "Restaurante que solo cobra en efectivo y declara pérdidas desde 2009",
    "Desgrava como gasto de empresa un yate «para reuniones»",
    "Sociedad con sede en las Islas Caimán y oficina en Telde",
    "Factura 4.000.000 Y$ de «asesoramiento verbal» a la empresa del cuñado",
    "Tiene a sus cinco gatos dados de alta como empleados",
)
TAX_CLEAN: tuple[str, ...] = (
    "Asalariado con una nómina y la deducción por alquiler antiguo",
    "Pensionista con su pensión y un plan de pensiones pequeño",
    "Autónoma que declara todo lo que factura, con sus facturas",
    "Familia numerosa con la deducción que le toca",
    "Estudiante con una beca y un verano de camarero",
    "Funcionario con su nómina y nada más",
    "Jubilado que vendió el piso del pueblo y lo declara",
    "Rider que declara sus ingresos de autónomo al céntimo",
    "Profesora que dona a una ONG y lo desgrava",
)

#: Influencer con agencia: colaboraciones turbias y colaboraciones normales.
COLLAB_SHADY: tuple[str, ...] = (
    "Una cripto que «se va a multiplicar por cien» seguro",
    "Unas pastillas que adelgazan 10 kilos en una semana",
    "Un curso para hacerte millonario vendiendo cursos",
    "Una casa de apuestas que te paga por cada seguidor que pierda",
    "Un «préstamo rápido» al 3.000 % TAE",
    "Una clínica de retoques sin título sanitario",
)
COLLAB_FINE: tuple[str, ...] = (
    "Una marca de gofio de la isla",
    "Una tienda de ropa del barrio",
    "Un festival de música en el sur",
    "Unas zapatillas de correr",
    "Una app para aprender idiomas",
    "Un restaurante de Vegueta",
    "Una marca de protector solar",
    "Un hotel rural en la cumbre",
    "Una protectora de animales",
)

DIALOGUE_PACKS.update(
    {
        "temario": DialoguePack(
            "📚 Test de la oposición. Regla de oro: **la respuesta es la de la ley**, no la "
            "de la vida.",
            (
                Dialogue("¿Cuántos artículos tiene la Constitución de 1978?", "169",
                         ("155", "200")),
                Dialogue("¿Qué artículo de la Constitución recoge el derecho a una vivienda "
                         "digna?", "El 47", ("El 14", "El 155")),
                Dialogue("¿Qué artículo de la Constitución recoge la igualdad ante la ley?",
                         "El 14", ("El 1", "El 47")),
                Dialogue("¿Qué artículo de la Constitución permite al Gobierno obligar a una "
                         "comunidad autónoma a cumplir la ley?", "El 155", ("El 47", "El 2")),
                Dialogue("¿Qué día se aprobó la Constitución en referéndum?",
                         "El 6 de diciembre de 1978",
                         ("El 29 de diciembre de 1978", "El 20 de noviembre de 1975")),
                Dialogue("¿Cuántos diputados tiene el Congreso?", "350", ("300", "400")),
                Dialogue("¿Cuántos senadores elige cada provincia peninsular?", "Cuatro",
                         ("Dos", "Seis")),
                Dialogue("¿Quién sanciona y promulga las leyes?", "El Rey",
                         ("El presidente del Gobierno", "La presidencia del Congreso")),
                Dialogue("Ley 39/2015: si la norma no fija plazo para resolver, ¿cuál es?",
                         "Tres meses", ("Diez días", "Un año")),
                Dialogue("Ley 39/2015: en un procedimiento a solicitud del interesado, ¿qué "
                         "es, por regla general, el silencio administrativo?", "Estimatorio",
                         ("Desestimatorio", "Lo que diga el jefe de negociado")),
                Dialogue("¿Qué plazo hay para el recurso de alzada contra un acto expreso?",
                         "Un mes", ("Tres meses", "Quince días")),
                Dialogue("Ley 39/2015: ¿qué días no son hábiles?",
                         "Sábados, domingos y festivos",
                         ("Solo domingos y festivos", "Los viernes por la tarde")),
                Dialogue("¿Qué título se pide para el subgrupo C2?",
                         "Graduado en Educación Secundaria Obligatoria",
                         ("Grado universitario", "Bachillerato")),
                Dialogue("¿Qué norma regula el empleo público?",
                         "El Estatuto Básico del Empleado Público",
                         ("El Estatuto de los Trabajadores", "El Reglamento del Congreso")),
                Dialogue("Si a tu solicitud le falta algo, ¿cuánto plazo te dan para "
                         "subsanarla?", "Diez días", ("Un mes", "Veinticuatro horas")),
            ),
        ),
        "ciudadano": DialoguePack(
            "🪟 Atención al ciudadano. Regla de oro: **sigue el procedimiento**, aunque se caiga "
            "el mundo.",
            (
                Dialogue(
                    "Un señor quiere que le atiendas sin cita «porque solo es una pregunta».",
                    "Le doy cita para el primer hueco y le resuelvo la duda rápida.",
                    ("Le atiendo y que esperen los de la cola.", "«Vuelva usted mañana»."),
                ),
                Dialogue(
                    "Le falta la fotocopia del DNI para el trámite.",
                    "La Administración no puede pedirle lo que ya tiene: lo consulto yo.",
                    ("Que vuelva con la fotocopia.",
                     "Que vaya a la papelería de enfrente, que es de mi cuñado."),
                ),
                Dialogue(
                    "Te traen un recurso sin firmar.",
                    "Le pido que lo firme o que lo subsane en diez días.",
                    ("Lo tiro a la papelera.", "Lo firmo yo, que tengo buena letra."),
                ),
                Dialogue(
                    "Te pide que le adelantes si le van a dar la subvención.",
                    "Se resolverá en plazo y se le notificará.",
                    ("Sí, que el concejal es su primo.", "No, se la dan a los de siempre."),
                ),
                Dialogue(
                    "Un vecino grita que tu sueldo lo paga él con sus impuestos.",
                    "Le agradezco su contribución y le ofrezco la hoja de reclamaciones.",
                    ("Entonces súbamelo.", "Le cierro la ventanilla en la cara."),
                ),
                Dialogue(
                    "Te piden un certificado que se saca en la sede electrónica.",
                    "Le enseño a sacarlo con su certificado digital.",
                    ("Que pida cita para dentro de un mes.", "Le digo que eso no existe."),
                ),
                Dialogue(
                    "Alguien llega justo cuando sales a desayunar.",
                    "Le atiendo y luego desayuno.",
                    ("Cuelgo «Vuelvo en 5 minutos» y vuelvo en 45.",
                     "Le digo que se ha caído el sistema."),
                ),
                Dialogue(
                    "Presenta la solicitud un minuto antes de cerrar el registro.",
                    "La registro con su hora y le doy el resguardo.",
                    ("Le digo que ya está cerrado.", "La dejo en la mesa para mañana."),
                ),
            ),
        ),
        "fundador": DialoguePack(
            "🚀 Fundas una app de reparto. Regla de oro: **los riders no son trabajadores, son "
            "«colaboradores»**.",
            (
                Dialogue(
                    "La Inspección de Trabajo pregunta por qué tus riders no tienen contrato.",
                    "Son emprendedores independientes que eligen su horario.",
                    ("Porque contratar es caro.", "¿Qué riders?"),
                ),
                Dialogue(
                    "Un rider pregunta por qué ha bajado la tarifa por kilómetro.",
                    "Hemos optimizado el algoritmo para darte más oportunidades.",
                    ("Porque podemos.", "Porque el inversor quiere beneficios."),
                ),
                Dialogue(
                    "Un periodista pregunta cuánto gana un rider a la hora.",
                    "Nuestros colaboradores pueden llegar a ganar hasta 2.000 Y$ a la hora.",
                    ("Unos 400 Y$, si no llueve.", "Ni idea, nunca lo he probado."),
                ),
                Dialogue(
                    "Los inversores preguntan cuándo daréis beneficios.",
                    "Ahora toca crecer: primero, conquistar el mercado.",
                    ("Nunca.", "Cuando dejemos de pagar a los riders."),
                ),
                Dialogue(
                    "Un juez dice que tus riders son falsos autónomos.",
                    "Respetamos la sentencia y la recurriremos hasta el Supremo.",
                    ("Vale, mañana los contrato a todos.", "¿Falsos qué?"),
                ),
                Dialogue(
                    "Te preguntan por la sostenibilidad.",
                    "Repartimos en bici: somos la empresa más verde del mercado.",
                    ("Repartimos un rollito en una furgoneta diésel.",
                     "La sostenibilidad no da dinero."),
                ),
                Dialogue(
                    "Un cliente se queja de que la comida llegó fría.",
                    "Le pedimos disculpas y le damos un cupón de 300 Y$.",
                    ("Que se la caliente.", "Le bajamos la valoración al rider y listo."),
                ),
                Dialogue(
                    "El Gobierno aprueba la Ley Rider.",
                    "Cumpliremos la ley con un modelo innovador de subcontratas.",
                    ("Cerramos y nos vamos del país.", "Contratamos a todos mañana."),
                ),
            ),
        ),
        "directo": DialoguePack(
            "🎮 Directo. Regla de oro: **el chat siempre entretenido** (y la marca, contenta).",
            (
                Dialogue(
                    "Un hater escribe en el chat «eres malísimo».",
                    "«Gracias por el engagement, rey». Y sigues.",
                    ("Le contestas durante 40 minutos.", "Cierras el directo llorando."),
                ),
                Dialogue(
                    "Te donan 5.000 Y$ con un mensaje que no se puede leer en voz alta.",
                    "Das las gracias sin leerlo y avisas al moderador.",
                    ("Lo lees entero.", "Devuelves los 5.000 Y$."),
                ),
                Dialogue(
                    "Se ve en pantalla una factura con tu dirección.",
                    "Cambias de escena al momento.",
                    ("Haces zoom para que se vea mejor.", "Dices que no es tuya."),
                ),
                Dialogue(
                    "Te llega la publi de una casa de apuestas.",
                    "La rechazas: la ley casi no deja anunciarlas con caras conocidas.",
                    ("La metes sin decir que es publi.", "Apuestas el sueldo en directo."),
                ),
                Dialogue(
                    "Llevas 8 horas de directo y el chat pide «una más».",
                    "Anuncias la última y quedas para mañana.",
                    ("Sigues hasta las 6 de la mañana.", "Te duermes en directo."),
                ),
                Dialogue(
                    "Otro streamer te manda 3.000 personas de golpe.",
                    "Les das la bienvenida y otro día le devuelves el favor.",
                    ("Sigues sin decir nada.", "Les pides que se suscriban o se vayan."),
                ),
                Dialogue(
                    "El chat pregunta por qué no vives en Andorra «como todos».",
                    "«Yo pago aquí, que la sanidad la uso».",
                    ("«Me voy la semana que viene».", "«Es por el clima»."),
                ),
                Dialogue(
                    "Se cae internet en la partida decisiva.",
                    "Avisas por redes y vuelves con el vídeo resumen.",
                    ("Culpas a tu madre en directo.", "Subes 40 stories llorando."),
                ),
            ),
        ),
        "plato": DialoguePack(
            "📺 Plató de televisión. Regla de oro: **ni confirmo ni desmiento**: el misterio paga "
            "la hipoteca.",
            (
                Dialogue(
                    "¿Es verdad que estás con tu compañero del reality?",
                    "Ni confirmo ni desmiento: lo contaré en exclusiva.",
                    ("Sí, desde el martes.", "No, es mi primo."),
                ),
                Dialogue(
                    "Otra cadena te ofrece 2.000.000 Y$ por una entrevista.",
                    "Lo hablo con mi agencia, a ver si suben.",
                    ("Acepto sin leer el contrato.", "La hago gratis, por amor al arte."),
                ),
                Dialogue(
                    "Un paparazzi te pilla saliendo del gimnasio sin maquillar.",
                    "Sonríes y saludas: mañana, portada amable.",
                    ("Le tiras el batido.", "Te escondes detrás de un contenedor."),
                ),
                Dialogue(
                    "Te preguntan en directo por tu residencia en Andorra.",
                    "Eso lo lleva mi gestor; aquí he venido a hablar de mi libro.",
                    ("«Vivo en Maspalomas, pero no se lo digan a nadie».",
                     "Enseñas el piso de Andorra vacío en un story."),
                ),
                Dialogue(
                    "Tu ex sale en el programa de enfrente contando cosas.",
                    "No entras al trapo: tu silencio cotiza más.",
                    ("Llamas en directo al otro programa.", "Subes 40 stories llorando."),
                ),
                Dialogue(
                    "Te preguntan por tu libro, que no has escrito tú.",
                    "Es un proyecto muy personal en el que he volcado mi alma.",
                    ("Lo escribió un escritor fantasma.", "No sé de qué va, no lo he leído."),
                ),
                Dialogue(
                    "La marca de tu perfume quiere que digas que lo creaste tú.",
                    "Es mi esencia hecha fragancia.",
                    ("No sé ni a qué huele.", "Me pagan por decirlo."),
                ),
            ),
        ),
        "portavoz": DialoguePack(
            "📣 Portavoz en el Congreso. Regla de oro: **contesta sin contestar nada.**",
            (
                Dialogue(
                    "Señoría, ¿va a explicar por fin las cuentas del partido?",
                    "Lo que importa es que este Gobierno cumple y la oposición solo grita.",
                    ("Sí, pero tengo que buscar el Excel.", "Esas cuentas las lleva el tesorero."),
                ),
                Dialogue(
                    "¿Piensa su grupo apoyar la ley que ayer criticaba?",
                    "Evolucionamos con los tiempos; la rigidez es cosa de otros.",
                    ("No, qué va, ya está bien.", "Si me dan algo a cambio, quizá."),
                ),
                Dialogue(
                    "¿Por qué no comparece el presidente para dar explicaciones?",
                    "El presidente comparece cuando toca, y la agenda la marca España.",
                    ("Porque le da miedo.", "Está de viaje, como siempre."),
                ),
                Dialogue(
                    "¿Qué le parece el último escándalo de su partido?",
                    "Mi grupo trabaja por la gente mientras otros viven de la bronca.",
                    ("Un escándalo, sí.", "Yo no sabía nada, lo leí en el periódico."),
                ),
                Dialogue(
                    "¿Se compromete a bajar los impuestos?",
                    "Nos comprometemos con la justicia fiscal y con quien más lo necesita.",
                    ("No, los vamos a subir.", "Sí, a ver si cuela."),
                ),
                Dialogue(
                    "¿Admite que el Gobierno ha maquillado las cifras de empleo?",
                    "Las cifras son las mejores de nuestra historia reciente.",
                    ("Maquillar es una palabra fuerte, pero sí.", "Eso lo dirá la oposición."),
                ),
                Dialogue(
                    "¿Va a pedir perdón a las víctimas del desastre?",
                    "Nuestra prioridad es la reconstrucción y mirar hacia adelante.",
                    ("Sí, pero tarde.", "No, ya pidieron perdón los de antes."),
                ),
                Dialogue(
                    "¿Votará su grupo a favor de la enmienda de los socios?",
                    "Estamos dialogando con todos los grupos desde la responsabilidad.",
                    ("Sí, nos tienen cogidos.", "No sé qué enmienda es."),
                ),
                Dialogue(
                    "¿Quién ha filtrado la conversación privada del ministro?",
                    "No vamos a entrar en cuestiones que están en manos de los tribunales.",
                    ("Fue un asesor, seguro.", "Yo, para ver qué pasaba."),
                ),
                Dialogue(
                    "¿Cuántos cargos de confianza ha colocado su partido este año?",
                    "Los que exige una administración moderna y comprometida.",
                    ("Unos trescientos, aproximadamente.", "Todos los primos que había libres."),
                ),
                Dialogue(
                    "¿Por qué ha cambiado de postura sobre el referéndum?",
                    "Porque la realidad es dinámica y la política, también.",
                    ("Por los votos.", "No he cambiado: la que cambió fue mi memoria."),
                ),
                Dialogue(
                    "¿Han cambiado la amnistía por la investidura?",
                    "Hemos apostado por la convivencia y por pasar página.",
                    (
                        "Sí, y ahora me toca explicarlo.",
                        "La amnistía se pagó en Waterloo, en efectivo.",
                    ),
                ),
                Dialogue(
                    "Vox ha presentado una moción montado a caballo. ¿Qué opina?",
                    "Con todo respeto, la derecha y la ultraderecha cada día se parecen más.",
                    (
                        "Si viene a caballo, por lo menos la leo.",
                        "Que se traigan también la Reconquista.",
                    ),
                ),
                Dialogue(
                    "¿Qué le parece lo último que ha dicho Yolanda Díaz?",
                    "En el Gobierno de coalición hay diversidad y una sola dirección.",
                    (
                        "No lo he entendido, pero sonreía mucho.",
                        "Que monte otro partido, que ya toca.",
                    ),
                ),
                Dialogue(
                    "¿Qué opina de Milei y su motosierra?",
                    "Respetamos a todos los gobiernos elegidos democráticamente.",
                    ("Que me la preste para los ministerios.", "Que se corte antes el pelo."),
                ),
            ),
        ),
        "ministerio": DialoguePack(
            "🗂️ Despacho del ministro. Regla de oro: **todo es una oportunidad, nada es "
            "culpa tuya y las filtraciones vienen de fuera.**",
            (
                Dialogue(
                    "Se ha filtrado el borrador de la ley a un periódico.",
                    "Pedir una investigación interna... que no acabe nunca.",
                    ("Admitir que lo filtraste tú.", "Echarle la culpa al becario por escrito."),
                ),
                Dialogue(
                    "El presupuesto de la obra se ha disparado un 60 %.",
                    "Hablar de «ajuste técnico» y de «mayor inversión en seguridad».",
                    ("Decir que nadie sabe sumar.", "Cancelar la obra y no decir nada."),
                ),
                Dialogue(
                    "Un socio de coalición amenaza con romper si no tiene su cartera.",
                    "Un café largo y una promesa vaga para después de las elecciones.",
                    ("Dárselo todo a la primera.", "Mandarlo a freír espárragos por Telegram."),
                ),
                Dialogue(
                    "La prensa pregunta por el sobrecoste del coche oficial.",
                    "Remitir al portal de transparencia, que nadie ha abierto nunca.",
                    ("Reconocer que fue un capricho.", "Decir que se compró barato."),
                ),
                Dialogue(
                    "Estalla una crisis con un país vecino por un comentario tuyo.",
                    "Emitir un comunicado de «máximo respeto y relaciones históricas».",
                    ("Repetir el comentario más alto.", "Desaparecer una semana."),
                ),
                Dialogue(
                    "Un alto cargo del ministerio ha cobrado dos sueldos.",
                    "Abrir un expediente informativo y pasar al siguiente tema.",
                    ("Aplaudir su iniciativa.", "Pedirle que devuelva el dinero por Bizum."),
                ),
                Dialogue(
                    "Los sindicatos convocan huelga del sector.",
                    "Convocar una mesa de diálogo con fecha para dentro de seis meses.",
                    ("Subir el sueldo de golpe.", "Reírte en directo."),
                ),
                Dialogue(
                    "Te piden cuentas por un contrato a dedo.",
                    "Aclarar que se ajustó al procedimiento de emergencia.",
                    ("Decir que era un amigo.", "Asegurar que fue un error de la fotocopiadora."),
                ),
                Dialogue(
                    "Un informe interno dice que el plan estrella no funciona.",
                    "Encargar otro informe que diga lo contrario.",
                    ("Publicarlo entero.", "Quemar el único ejemplar delante de testigos."),
                ),
                Dialogue(
                    "Toca presentar un recorte y todos los grupos se te echan encima.",
                    "Llamarlo «optimización del gasto» y repetirlo cinco veces.",
                    ("Pedir perdón a todo el mundo.", "Culpar a la herencia de Felipe II."),
                ),
                Dialogue(
                    "Toca inaugurar un pantano y no hay pantano.",
                    "Inauguras la primera piedra; el agua ya vendrá.",
                    ("Cancelas el acto.", "Inauguras un charco y lo graba el NO-DO."),
                ),
                Dialogue(
                    "Bruselas pide justificar los fondos Next Generation.",
                    "Mandas un Excel de 4.000 filas con la palabra «resiliencia».",
                    ("Reconoces que se fueron en asesores.", "Mandas una postal de Canarias."),
                ),
                Dialogue(
                    "Tu jefe de gabinete sale en un audio con un comisionista.",
                    "Lo cesas «a petición propia» y agradeces los servicios prestados.",
                    ("Lo ascienes a secretario de Estado.", "Pides que te pasen el audio entero."),
                ),
            ),
        ),
        "control": DialoguePack(
            "🏛️ Sesión de control, miércoles. Regla de oro: **nunca contestes a la "
            "pregunta; devuelve la pelota con un «¿y usted más?»**",
            (
                Dialogue(
                    "Presidente, ¿cuándo convocará elecciones?",
                    "Cuando toque, y mientras tanto usted gobernó con corrupción.",
                    ("Pronto, si me deja.", "El mes que viene, creo."),
                ),
                Dialogue(
                    "¿Sabía usted lo de las comisiones de su exsecretario?",
                    "Y usted, ¿sabía lo de su tesorero, señoría?",
                    ("Algo me sonaba.", "Me enteré el mismo día que usted."),
                ),
                Dialogue(
                    "¿Por qué sube el precio de la luz?",
                    "Gobernaron ocho años y dejaron la factura más cara de Europa.",
                    ("Por el mercado marginalista, que lo explico.", "No tengo ni idea."),
                ),
                Dialogue(
                    "¿Ha pagado usted ese viaje de su bolsillo?",
                    "Hablemos de los viajes que pagaron ustedes en su día.",
                    ("Pagó el partido.", "Sí, todo en metálico."),
                ),
                Dialogue(
                    "¿Va a rectificar la política de vivienda?",
                    "Su partido vendió pisos a fondos buitre; nosotros construimos.",
                    ("Sí, rectifico hoy mismo.", "La vivienda no es mi competencia."),
                ),
                Dialogue(
                    "¿Respeta usted las sentencias del Supremo?",
                    "Respeto las sentencias, y ustedes, ¿respetan las urnas?",
                    ("Depende de la sentencia.", "No las leo, pero me las resumen."),
                ),
                Dialogue(
                    "Su ministro ha mentido al Congreso.",
                    "Más mintió su líder prometiendo bajar impuestos.",
                    ("Es verdad, y me da vergüenza.", "Lo he sabido siempre."),
                ),
                Dialogue(
                    "¿Por qué no hay presupuestos?",
                    "Porque ustedes bloquean todo, como siempre.",
                    ("Porque no los he hecho.", "Los hice, pero se me perdieron."),
                ),
                Dialogue(
                    "¿Qué piensa de la última encuesta?",
                    "Las encuestas las ganan ustedes; las elecciones, nosotros.",
                    ("Pinta muy mal, la verdad.", "La encuesta la hice yo, ya sabe."),
                ),
                Dialogue(
                    "¿Cuántos asesores hay en la Moncloa?",
                    "Los mismos que cuando gobernaban ustedes, pero más baratos.",
                    ("Unos mil trescientos.", "No me los sé, los contrato por grupos."),
                ),
                Dialogue(
                    "¿Se arrepiente de haber pactado con quien dijo que no pactaría?",
                    "Y usted, ¿se arrepiente de haber hecho lo mismo en tres autonomías?",
                    ("Un poco, sí.", "Qué va, me lo recomendaron."),
                ),
                Dialogue(
                    "Presidente, ¿es cierto que va a indultar a sus socios?",
                    "Aquí lo que importa es la concordia, señoría, y sus tribunales ya veremos.",
                    ("Claro, ya está firmado.", "Solo a mis amigos."),
                ),
                Dialogue(
                    "¿Por qué no condena usted a Maduro?",
                    "Y ustedes, ¿por qué no condenaron los sobres de su tesorero?",
                    ("Porque nunca enseña las actas.", "Lo condenaré cuando publique las actas."),
                ),
                Dialogue(
                    "¿Va a escribir otra carta a la ciudadanía?",
                    "Escribiré cuando la máquina del fango deje de funcionar, señoría.",
                    (
                        "Sí, y me iré cinco días a reflexionar.",
                        "Ya la tengo escrita para el jueves.",
                    ),
                ),
                Dialogue(
                    "¿Le parece normal un Gobierno con veintidós ministerios?",
                    "Más asesores tenían ustedes en Génova.",
                    ("Son pocos: quiero treinta.", "Uno por socio, más o menos."),
                ),
                Dialogue(
                    "Feijóo le pregunta cuándo va a dimitir.",
                    "Usted ganó las elecciones y no gobierna: dimita usted de la oposición.",
                    ("Mañana a primera hora.", "Le dejo el sillón un rato, si quiere."),
                ),
            ),
        ),
        "lobbies": DialoguePack(
            "🤝 Pasillos del Parlamento Europeo. Regla de oro: **escucha, sonríe y no firmes "
            "nada.**",
            (
                Dialogue(
                    "Un lobista de una gran tecnológica te invita a un desayuno «informal».",
                    "Vas, escuchas, te llevas la tarjeta y no prometes nada.",
                    ("Aceptas todo lo que te dice.", "Le firmas un papel «de buena voluntad»."),
                ),
                Dialogue(
                    "Una petrolera pide que el reglamento «reconozca la transición gradual».",
                    "«Muy interesante, lo estudiaremos con el máximo rigor».",
                    ("Le copias su texto tal cual.", "Le dices que se vaya al diablo en directo."),
                ),
                Dialogue(
                    "La agroindustria quiere una exención para los pesticidas.",
                    "Sonríes, tomas notas y lo remites a la comisión competente.",
                    ("Le das la exención ahora mismo.", "Le haces un discurso sobre las abejas."),
                ),
                Dialogue(
                    "Un lobista te regala un maletín de «material informativo».",
                    "Aceptas el folleto, rechazas el maletín.",
                    ("Aceptas el maletín entero.", "Lo abres delante de las cámaras."),
                ),
                Dialogue(
                    "Una asociación farmacéutica te pide una reunión urgente.",
                    "Que la pidan por el registro de transparencia, con cita el mes que viene.",
                    ("Quedas a cenar esta misma noche.", "Le das tu número personal."),
                ),
                Dialogue(
                    "Una empresa de datos te ofrece un «estudio independiente» ya escrito.",
                    "Lo recibes, lo guardas y lo dejas junto a los demás estudios.",
                    ("Lo presentas como propio.", "Firmas el prólogo."),
                ),
                Dialogue(
                    "Un lobby te propone un viaje a una conferencia en una isla.",
                    "Lo declinas con cortesía y lo apuntas en el registro.",
                    ("Aceptas e invitas a un amigo.", "Dices que sí, pero que pagas tú... luego."),
                ),
                Dialogue(
                    "Te piden que enmiendes un párrafo para «clarificar» el texto.",
                    "«Lo valoraremos en el momento procesal oportuno».",
                    ("Lo cambias en el acto.", "Le preguntas cuánto vale la clarificación."),
                ),
                Dialogue(
                    "Un lobista te asegura que su cliente genera cien mil empleos.",
                    "«Fascinante cifra». Sonríes y pides que la envíen por escrito.",
                    ("Le preguntas dónde están.", "Le ofreces un puesto a un primo."),
                ),
                Dialogue(
                    "Una alianza de empresas energéticas te invita a su cóctel anual.",
                    "Pasas un rato, saludas y te vas antes del brindis.",
                    ("Brindas con ellos por el reglamento.", "Te quedas a dormir en el hotel."),
                ),
                Dialogue(
                    "Un enviado de Musk pide que el reglamento proteja «la libertad de "
                    "expresión» de su dueño.",
                    "Lo escuchas y le pides que lo mande por el registro de transparencia.",
                    (
                        "Le pones un check azul al reglamento.",
                        "Le prometes una exención para Marte.",
                    ),
                ),
                Dialogue(
                    "Un lobby de Pekín te regala un móvil de última generación.",
                    "Lo devuelves con una nota amable; tu móvil ya te espía bastante.",
                    ("Lo usas para el trabajo.", "Te haces un selfi con él y lo subes a TikTok."),
                ),
            ),
        ),
        "trilogo": DialoguePack(
            "🇪🇺 Trílogo con el Consejo y la Comisión. Regla de oro: **cierra un acuerdo tan "
            "ambiguo que todos puedan venderlo como victoria.**",
            (
                Dialogue(
                    "El Consejo quiere 2030 y el Parlamento, 2025, para el objetivo.",
                    "Propones «ambición progresiva con hitos intermedios flexibles».",
                    ("Aceptas 2030 sin más.", "Exiges 2025 o te levantas de la mesa."),
                ),
                Dialogue(
                    "La Comisión pide mantener su propuesta original intacta.",
                    "Se mantiene el espíritu y se «adapta» el articulado.",
                    ("Aceptas el texto original.", "Lo reescribes entero en la servilleta."),
                ),
                Dialogue(
                    "Un Estado miembro exige una excepción para su país.",
                    "Una cláusula de «circunstancias nacionales específicas».",
                    ("Le das la excepción completa.", "Le dices que no, bajo ningún concepto."),
                ),
                Dialogue(
                    "Son las tres de la mañana y nadie cede en el artículo 7.",
                    "Lo dejas en «cuando proceda y de conformidad con la legislación».",
                    ("Te duermes en la silla.", "Sacas el artículo y lo borras."),
                ),
                Dialogue(
                    "Hay que decidir quién fija la fecha de aplicación.",
                    "«La fecha será determinada por los Estados miembros en coordinación».",
                    ("Pones una fecha y la defiendes.", "Lo pospones a 2040."),
                ),
                Dialogue(
                    "El Parlamento quiere sanciones; el Consejo, recomendaciones.",
                    "Sanciones «proporcionadas, disuasorias y, en su caso, recomendadas».",
                    ("Sanciones duras o nada.", "Solo recomendaciones, y gracias."),
                ),
                Dialogue(
                    "La prensa espera un titular claro de la reunión.",
                    "Sales con tus socios y dices que ha sido «un paso decisivo».",
                    ("Dices que no hay acuerdo.", "Explicas el acuerdo con detalle."),
                ),
                Dialogue(
                    "Quedan cinco minutos antes de que cierre la presidencia semestral.",
                    "Aprobáis un texto con tres interpretaciones posibles.",
                    ("Pedís otra reunión para después.", "Lo votáis sin leerlo."),
                ),
                Dialogue(
                    "Un jurista advierte de que el texto no se entiende.",
                    "«Se aclarará en los actos delegados». Y todos felices.",
                    ("Le das la razón y repites la ronda.", "Despides al jurista."),
                ),
                Dialogue(
                    "Hay que repartir la financiación entre las partes.",
                    "Cifras entre corchetes y un «sin perjuicio del marco financiero».",
                    ("Fijas cantidades exactas.", "Dices que cada uno pague lo suyo."),
                ),
                Dialogue(
                    "La presidencia húngara amenaza con vetar el texto.",
                    "Una «declaración de la presidencia» anexa que no obliga a nadie.",
                    ("Aceptas el veto.", "Llamas a Orbán a las tres de la mañana."),
                ),
                Dialogue(
                    "Macron quiere dar un discurso de veinte minutos antes de firmar.",
                    "Le das la palabra y adelantas el café para todos.",
                    ("Le cortas el micrófono.", "Le pides que lo resuma en un tuit."),
                ),
                Dialogue(
                    "Los fabricantes piden una prórroga para el tapón pegado a la botella.",
                    "Prórroga «técnica», con cláusula de revisión en 2030.",
                    (
                        "Despegar los tapones de toda Europa.",
                        "Que cada uno lleve el tapón en la mano.",
                    ),
                ),
            ),
        ),
        "comisario": DialoguePack(
            "🏢 Despacho en el Berlaymont. Regla de oro: **la respuesta buena es la más "
            "comunitaria, la más burocrática y la que no compromete a nadie.**",
            (
                Dialogue(
                    "Una gran tecnológica recurre la multa de miles de millones.",
                    "«La Comisión ha actuado con arreglo a los Tratados y confía en el control "
                    "jurisdiccional».",
                    ("Le subes la multa por insolente.", "Le dices que no pasa nada."),
                ),
                Dialogue(
                    "Un Estado miembro se queja de que el reglamento le cuesta mucho.",
                    "Remites a la «evaluación de impacto» y a una consulta pública.",
                    ("Le das dinero ahora mismo.", "Le dices que se aguante."),
                ),
                Dialogue(
                    "Un eurodiputado te pregunta por escrito cuántos funcionarios hay.",
                    "«La Comisión remite a su respuesta a la pregunta E-004321/2024».",
                    ("Contestas con la cifra exacta.", "No contestas nunca."),
                ),
                Dialogue(
                    "Una audiencia en el Parlamento te interroga sobre tu programa.",
                    "«Trabajaremos con los colegisladores en un espíritu constructivo».",
                    ("Explicas tu plan en detalle.", "Les cuentas tus vacaciones."),
                ),
                Dialogue(
                    "La prensa pregunta si habrá nuevo reglamento sobre el tema.",
                    "«Se estudiará en el marco del programa de trabajo de la Comisión».",
                    ("Anuncias el reglamento en directo.", "Dices que no habrá nada."),
                ),
                Dialogue(
                    "Un país te pide flexibilidad con el déficit.",
                    "«Se aplicarán las cláusulas de salvaguardia conforme al marco fiscal».",
                    ("Le dices que sí a todo.", "Le abres un procedimiento por si acaso."),
                ),
                Dialogue(
                    "Dos direcciones generales discuten por quién redacta el informe.",
                    "Creas un grupo de trabajo interservicios.",
                    ("Lo redactas tú esa noche.", "Las mandas a la cantina a resolverlo."),
                ),
                Dialogue(
                    "Un Estado miembro incumple una directiva desde hace diez años.",
                    "Una carta de emplazamiento, con plazo de dos meses ampliable.",
                    ("Le impones una multa inmediata.", "Mirar a otro lado, que ya se acordará."),
                ),
                Dialogue(
                    "La gente en redes pregunta qué hace Bruselas por ellos.",
                    "Un comunicado con la palabra «ciudadanos» cinco veces.",
                    ("Les respondes con memes.", "Les mandas el informe de 800 páginas."),
                ),
                Dialogue(
                    "Una asociación pide transparencia sobre una reunión a puerta cerrada.",
                    "«El documento solicitado queda fuera del ámbito del Reglamento 1049/2001».",
                    ("Les envías las actas completas.", "Les dices que se las inventen."),
                ),
                Dialogue(
                    "Trump anuncia aranceles del 50 % a todo lo europeo.",
                    "«La Unión responderá de manera firme, proporcionada y unida».",
                    (
                        "Le mandas una tarta con un 50 % menos.",
                        "Te rindes y compras gas a precio Trump.",
                    ),
                ),
                Dialogue(
                    "Erdoğan te invita a su palacio de mil habitaciones a hablar de la adhesión.",
                    "«El diálogo con Turquía sigue abierto en el marco de las negociaciones».",
                    ("Vas y te pierdes en el ala oeste.", "Le dices que entra en la UE el lunes."),
                ),
                Dialogue(
                    "Boris Johnson quiere volver a la UE, pero sin pagar.",
                    "«La puerta está abierta conforme al artículo 49 del Tratado».",
                    (
                        "Le dices que sí y que traiga el té.",
                        "Le pides el cheque británico de vuelta.",
                    ),
                ),
                Dialogue(
                    "Un país pide que las cookies no haya que aceptarlas.",
                    "«La Comisión evaluará la experiencia del usuario al revisar la directiva».",
                    ("Aceptas todas las cookies.", "Prohíbes las galletas."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: las ramas de la obra (Ladrillo y Obra pública) --------------------------

MEMORY_PACKS.update(
    {
        "pisos": MemoryPack(
            "🏖️ Venta sobre plano. El cliente {n} quiere su piso con esto, por orden:",
            "Enséñale el piso piloto en el mismo orden (y no menciones la depuradora).",
            (
                ("🏊", "Piscina comunitaria"),
                ("🅿️", "Plaza de garaje"),
                ("🌊", "Vistas al mar (de lado)"),
                ("⛳", "Campo de golf"),
                ("🧱", "Trastero"),
                ("🛗", "Ascensor"),
                ("🌴", "Palmera"),
                ("☀️", "Solárium"),
                ("🛋️", "Cocina americana"),
            ),
        ),
        "concesiones": MemoryPack(
            "📋 Consejo de concesiones. Renueva en este orden (tanda {n}):",
            "Renueva las concesiones en el mismo orden, antes de que caduquen.",
            (
                ("🛣️", "Autopista radial (rescatada)"),
                ("✈️", "Aeropuerto sin aviones"),
                ("🚄", "Estación del AVE en mitad del campo"),
                ("🚇", "Línea de metro"),
                ("💧", "Desaladora"),
                ("🅿️", "Parking municipal"),
                ("🏟️", "Estadio"),
                ("🏥", "Hospital de gestión privada"),
                ("⚓", "Puerto deportivo"),
            ),
        ),
    }
)  # fmt: skip

SORT_PACKS.update(
    {
        "terrenos": SortPack(
            "🗺️ Eres urbanizador. Cada terreno va a su sitio: construir ya, recalificar "
            "(con mariscada), dejarlo protegido o devolverlo, que es de Costas.",
            "🌄 Terreno {n}/{total}:",
            ("🏗️ Construir ya", "🦐 Recalificar", "🦎 Protegido", "🌊 Es de Costas"),
            (
                ("Un solar con licencia en el centro del pueblo", 0),
                ("Una parcela urbanizable al lado del polígono", 0),
                ("El hueco de una gasolinera cerrada", 0),
                ("Un pinar justo detrás de tu última urbanización", 1),
                ("Una finca de plataneras que «ya no da»", 1),
                ("El campo de fútbol del pueblo", 1),
                ("Un barranco «seco desde hace años»", 1),
                ("El hábitat del lagarto gigante de El Hierro", 2),
                ("Las dunas de Maspalomas", 2),
                ("Un yacimiento con grabados guanches", 2),
                ("La primera línea de una playa virgen", 3),
                ("Un hotel a medio hacer en plena arena desde 2008", 3),
                ("Un paseo marítimo «que total, nadie usa»", 3),
            ),
        ),
        "licitaciones": SortPack(
            "📑 Eres director de licitaciones. Cada concurso va a su sitio: nos "
            "presentamos, en UTE con la competencia, baja temeraria (y luego "
            "modificado) o pasamos.",
            "📂 Concurso {n}/{total}:",
            (
                "✅ Nos presentamos",
                "🤝 En UTE con la competencia",
                "📉 Baja y luego modificado",
                "🚪 Pasamos",
            ),
            (
                ("Un tramo de autovía normal, con presupuesto razonable", 0),
                ("La reforma de un colegio, con plazos claros", 0),
                ("Un hospital con el proyecto ya redactado", 0),
                ("Un AVE de 4.000 millones que ninguna empresa puede hacer sola", 1),
                ("Un túnel bajo el mar que asusta hasta al banco", 1),
                ("Ocho estadios para un Mundial con un plazo imposible", 1),
                ("Un tramo con un presupuesto que no da ni para el asfalto", 2),
                ("Una obra que el ministro quiere inaugurar antes de las elecciones", 2),
                ("Un puerto que «seguro que se amplía después»", 2),
                ("Un concurso a medida de otra empresa (su logo sale en el pliego)", 3),
                ("Una obra en un país del Sahel que va por su tercer golpe de Estado", 3),
                ("Un pliego que exige haber construido antes un muro de 3.000 km", 3),
            ),
        ),
    }
)  # fmt: skip

DIALOGUE_PACKS.update(
    {
        "banco": DialoguePack(
            "🏦 Reunión con el banco para refinanciar. Regla de oro: **nunca digas que no "
            "puedes pagar: el mercado está «en un momento de oportunidad».**",
            (
                Dialogue(
                    "¿Cómo piensa devolver los 40 millones?",
                    "Con la venta de la fase dos, que tiene una demanda espectacular.",
                    ("No pienso.", "Con un décimo de Navidad que llevo en la cartera."),
                ),
                Dialogue(
                    "Los pisos de la fase uno siguen vacíos.",
                    "Están en fase de comercialización estratégica.",
                    ("Sí, solo vive un gato.", "Es que están en mitad del desierto."),
                ),
                Dialogue(
                    "El tasador dice que valen la mitad.",
                    "Buscamos otro tasador con más visión de futuro.",
                    ("Tiene razón el hombre.", "La mitad de la mitad, si le soy sincero."),
                ),
                Dialogue(
                    "¿Qué avales tiene?",
                    "Veinte años en el sector y la confianza del ayuntamiento.",
                    ("La bici de mi hijo.", "Una foto con Florentino, firmada."),
                ),
                Dialogue(
                    "En la tele dicen que estalla la burbuja.",
                    "El ladrillo nunca baja: como mucho, se toma un respiro.",
                    ("Ya ha estallado; por eso vengo.", "¿Qué burbuja? ¿La del jacuzzi?"),
                ),
                Dialogue(
                    "Necesitamos ver las cuentas de la sociedad.",
                    "Se las mando el lunes, auditadas y en tres idiomas.",
                    ("Están en una servilleta.", "¿De cuál de las cuarenta sociedades?"),
                ),
                Dialogue(
                    "El banco se quiere quedar los pisos como pago.",
                    "Dación en pago no: refinanciamos a treinta años y todos contentos.",
                    ("Vale, quedáoslos.", "Os doy también el chalet, que me ahoga."),
                ),
                Dialogue(
                    "¿Por qué hay un yate a nombre de la empresa?",
                    "Es la oficina de ventas flotante para el mercado internacional.",
                    ("Para escaparme.", "Es de mi cuñado, que vive en Andorra."),
                ),
                Dialogue(
                    "¿Le suena la palabra «concurso de acreedores»?",
                    "Como concepto académico, sí.",
                    (
                        "La estoy rellenando ahora mismo.",
                        "Estoy destruido, pero por fuera… por dentro estoy nuevo.",
                    ),
                ),
            ),
        ),
        "alcalde": DialoguePack(
            "🏛️ Comisión de urbanismo con el alcalde. Regla de oro: **todo se arregla con un "
            "convenio urbanístico y una foto con casco.**",
            (
                Dialogue(
                    "El alcalde dice que el pinar es suelo protegido.",
                    "Proponemos un convenio: el pinar se urbaniza y el pueblo gana un "
                    "polideportivo.",
                    ("Lo talamos y ya se verá.", "Le invito a una mariscada y lo hablamos."),
                ),
                Dialogue(
                    "La oposición pide un informe medioambiental.",
                    "Encantados: lo encargamos nosotros, que conocemos a un técnico estupendo.",
                    ("¿Medioambiental? ¿Eso qué es?", "Que lo haga mi primo, que es fontanero."),
                ),
                Dialogue(
                    "Los vecinos protestan por el rascacielos en primera línea.",
                    "Es un hito arquitectónico que pondrá el pueblo en el mapa.",
                    ("Que se muden.", "Le quito una planta y se queda en veintinueve."),
                ),
                Dialogue(
                    "El alcalde pregunta qué gana el pueblo con todo esto.",
                    "Una rotonda con una escultura de dos millones.",
                    ("Atascos.", "El pueblo, nada; usted, ya hablaremos."),
                ),
                Dialogue(
                    "El secretario municipal no quiere firmar.",
                    "Pedimos un informe jurídico externo que diga que sí.",
                    (
                        "Lo cambiamos por otro secretario.",
                        "Firmo yo por él, que tengo buena letra.",
                    ),
                ),
                Dialogue(
                    "Te piden viviendas protegidas en la urbanización.",
                    "Las hacemos, en la parcela que da justo a la autopista.",
                    ("Ni una.", "Todas protegidas."),
                ),
                Dialogue(
                    "La prensa local habla de «un nuevo Marbella».",
                    "Marbella es un modelo de éxito turístico, no hay más que verla.",
                    (
                        "Ojalá: allí sí se recalificaba bien.",
                        "Sin comentarios, que me escucha el juez.",
                    ),
                ),
                Dialogue(
                    "Bruselas avisa de que la Ley de Costas prohíbe construir en la arena.",
                    "Construimos a 101 metros y llamamos «paseo marítimo» a la terraza.",
                    ("Pues construimos en el mar.", "Que vengan a echarnos de la playa."),
                ),
            ),
        ),
        "palco": DialoguePack(
            "⚽ Palco del estadio. Regla de oro: **en el palco no se habla de fútbol; se habla "
            "de obra pública y recalificaciones.**",
            (
                Dialogue(
                    "El alcalde te pregunta qué tal va el equipo.",
                    "Muy bien, igual que la recalificación de la ciudad deportiva.",
                    ("Fatal, bajamos.", "Ni idea: no he visto un partido en mi vida."),
                ),
                Dialogue(
                    "Un ministro quiere saber si apoyarás el Mundial en España.",
                    "Si hay estadio nuevo, mis máquinas están listas.",
                    ("No, que sale carísimo.", "Solo si juega Messi."),
                ),
                Dialogue(
                    "La afición pide fichajes.",
                    "Primero el estadio nuevo: los fichajes vienen con los palcos VIP.",
                    ("Fichamos al portero de mi cuñado.", "Que los pague la afición."),
                ),
                Dialogue(
                    "Un jeque quiere comprar el club.",
                    "Hablamos, pero el suelo de la ciudad deportiva se queda en casa.",
                    ("Vendido, con todos dentro.", "Solo si me regala un Mundial."),
                ),
                Dialogue(
                    "Un periodista pregunta quién construye el estadio nuevo.",
                    "La mejor empresa del país, adjudicada con todas las garantías.",
                    ("La mía, casualmente.", "Lo firmé en una servilleta en el descanso."),
                ),
                Dialogue(
                    "Te sientas al lado del presidente del Gobierno.",
                    "Le hablas del tiempo y, en el descanso, del AVE a tu pueblo.",
                    ("Le pides que dimita.", "Le cantas el himno del rival."),
                ),
                Dialogue(
                    "El árbitro pita un penalti en contra.",
                    "Pides calma y un comunicado oficial contra el sistema.",
                    ("Bajas al campo a por él.", "Le mandas un Bizum al árbitro."),
                ),
                Dialogue(
                    "El equipo baja a segunda.",
                    "Es un año de transición hacia un proyecto ganador (y un estadio nuevo).",
                    ("Lo pago con dinero del ayuntamiento.", "Me compro otro club."),
                ),
            ),
        ),
        "fomento": DialoguePack(
            "🚄 Despacho del Ministerio de Transportes. Regla de oro: **pide el modificado y "
            "ofrece la inauguración.**",
            (
                Dialogue(
                    "El ministro pregunta por qué el AVE va con tres años de retraso.",
                    "Por las mejoras de seguridad que exige la nueva normativa europea.",
                    ("Por la siesta.", "Porque se nos olvidó el túnel."),
                ),
                Dialogue(
                    "El presupuesto del túnel se ha duplicado.",
                    "Es un modificado técnico por la geología, imprevisible como la política.",
                    ("Es que cavamos dos veces.", "Pusimos mármol en el túnel."),
                ),
                Dialogue(
                    "Te piden que el tren pare en el pueblo del ministro.",
                    "Una estación nueva, con aparcamiento, en mitad del campo.",
                    ("Imposible.", "Que vaya andando, que es sano."),
                ),
                Dialogue(
                    "La UCO investiga al asesor del ministerio que os adjudicaba obras.",
                    "Colaboraremos con la justicia; nuestras adjudicaciones son impecables.",
                    ("Nos cobraba en chistorras.", "Ya le dije que no usara WhatsApp."),
                ),
                Dialogue(
                    "Los trenes nuevos no caben por los túneles.",
                    "Se adaptarán los túneles con un nuevo contrato.",
                    ("Los trenes los hicieron mal.", "Que pasen de lado."),
                ),
                Dialogue(
                    "Hay que poner la primera piedra antes de las elecciones.",
                    "La ponemos el viernes; el resto de piedras ya irán llegando.",
                    ("No hay ni proyecto.", "La ponemos de cartón piedra."),
                ),
                Dialogue(
                    "El Tribunal de Cuentas pregunta por el aeropuerto sin aviones.",
                    "Es una infraestructura estratégica a la espera de demanda.",
                    ("Tiene estatua, ¿no le vale?", "Lo usan los conejos para correr."),
                ),
                Dialogue(
                    "El ministerio quiere licitar sin la ampliación que pedisteis.",
                    "Sin ampliación no hay oferta: el mercado está muy tensionado.",
                    ("Vale, la hacemos gratis.", "La hacemos y la cobramos en B."),
                ),
            ),
        ),
        "internacional": DialoguePack(
            "🌍 Contratos por el mundo. Regla de oro: **firma todo, cobra por adelantado y que "
            "el sobrecoste lo pague el país.**",
            (
                Dialogue(
                    "Trump quiere un muro en la frontera y dice que lo paga México.",
                    "Encantados: nuestra tarifa, en dólares y por adelantado.",
                    ("Que lo pague México, pues.", "Le hacemos una valla de cañas."),
                ),
                Dialogue(
                    "Erdoğan quiere ampliar su palacio de mil habitaciones.",
                    "Mil cien, con helipuerto, y la factura en yapdollars.",
                    ("¿No le bastan mil?", "Le recomendamos un piso en Vallecas."),
                ),
                Dialogue(
                    "Arabia quiere un tren de alta velocidad en mitad del desierto.",
                    "Firmamos, y el sobrecoste de la arena lo pactamos después.",
                    (
                        "La arena estropea las vías; mejor no.",
                        "Mejor un camello de alta velocidad.",
                    ),
                ),
                Dialogue(
                    "Panamá dice que la ampliación del canal cuesta más de lo pactado.",
                    "Abrimos un arbitraje internacional que dure diez años.",
                    ("Pagamos la diferencia.", "Lo dejamos a medias y nos vamos."),
                ),
                Dialogue(
                    "Kim Jong-un quiere un hotel de 105 plantas para su cumpleaños.",
                    "Lo acabamos para la fecha y cobramos antes del aplauso obligatorio.",
                    ("Ya tiene uno sin terminar desde 1987.", "Le regalamos un misil de cartón."),
                ),
                Dialogue(
                    "Milei cancela toda la obra pública con una motosierra.",
                    "Le ofrecemos una concesión privada con peaje: ¡libertad, carajo!",
                    ("Le pedimos que no corte.", "Le mandamos una motosierra más grande."),
                ),
                Dialogue(
                    "Qatar necesita ocho estadios con aire acondicionado para un Mundial.",
                    "Con aire, con césped y con calendario de pagos.",
                    ("Que jueguen en invierno.", "Mejor un estadio en Las Palmas."),
                ),
                Dialogue(
                    "Xi te invita a entrar en la Ruta de la Seda.",
                    "Un memorando de entendimiento con las cláusulas en chino y en inglés.",
                    ("Sí, pero que me den TikTok.", "Me lo pienso, que me baja el crédito social."),
                ),
                Dialogue(
                    "Maduro quiere un metro nuevo en Caracas y paga en bolívares.",
                    "Por adelantado, en yapdollars y depositado en Suiza.",
                    ("Aceptamos bolívares.", "Primero, que enseñe las actas."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: las ramas de la sanidad (Pública y Privada) ------------------------------

MEMORY_PACKS.update(
    {
        "quirofano": MemoryPack(
            "🗓️ Parte de quirófano del lunes {n}. El orden de las intervenciones:",
            "Programa las operaciones en el mismo orden (y que no se cuele el sobrino del "
            "gerente).",
            (
                ("🦴", "Prótesis de cadera"),
                ("🫀", "Bypass coronario"),
                ("🧠", "Hernia discal"),
                ("👁️", "Cataratas"),
                ("🦵", "Menisco del concejal"),
                ("🩻", "Apendicitis"),
                ("🫁", "Biopsia de pulmón"),
                ("🧽", "Contar las gasas"),
                ("🧤", "Cambio de guantes"),
            ),
        ),
        "retoques": MemoryPack(
            "💋 El cliente {n} lo quiere todo, en este orden (y en efectivo):",
            "Haz los retoques en el mismo orden, que luego sale sin filtro en las fotos.",
            (
                ("💉", "Bótox"),
                ("👄", "Relleno de labios"),
                ("👃", "Rinoplastia"),
                ("🧑‍🦲", "Injerto capilar (mejor que el de Turquía)"),
                ("🦷", "Carillas de blanco nuclear"),
                ("🍑", "Aumento de glúteos"),
                ("👀", "Párpados"),
                ("🪒", "Láser"),
                ("😐", "Lifting (sin expresión)"),
            ),
        ),
    }
)  # fmt: skip

SORT_PACKS.update(
    {
        "derivaciones": SortPack(
            "🗂️ Eres jefe de servicio. Cada paciente va a su sitio: quirófano ya, lista de "
            "espera, concierto con la privada o de vuelta a su médico de cabecera.",
            "🧑‍⚕️ Paciente {n}/{total}:",
            ("🔪 Quirófano ya", "⏳ Lista de espera", "🤝 A la privada", "🏠 A su médico"),
            (
                ("Apendicitis con fiebre y la tripa como una tabla", 0),
                ("Fractura de cadera de una señora de 85 años", 0),
                ("Hernia estrangulada desde esta mañana", 0),
                ("Embarazo ectópico con dolor fuerte", 0),
                ("Juanete que le molesta «desde la boda de su hija»", 1),
                ("Cataratas que aún le dejan ver el telediario", 1),
                ("Rodilla de pádel del concejal (sin prisa, aunque él diga que sí)", 1),
                ("Varices de toda la vida, sin dolor", 1),
                ("Prótesis de rodilla con dos años de espera (y elecciones en mayo)", 2),
                ("Operación de hombro que la Consejería quiere sacar de la lista ya", 2),
                ("Doscientas cataratas para bajar la lista antes del telediario", 2),
                ("Resonancia que en la pública tarda nueve meses", 2),
                ("Catarro derivado a Cirugía «por si acaso»", 3),
                ("Viene a por la receta de las pastillas de la tensión", 3),
                ("Pide un justificante para el gimnasio", 3),
                ("Dolor de espalda de la mudanza, sin nada más", 3),
            ),
        ),
    }
)  # fmt: skip

#: Pólizas de la mutua: la cláusula que deja tirado al asegurado y las que son normales.
POLICY_TRAPS: tuple[str, ...] = (
    "Excluye cualquier enfermedad que empiece por vocal",
    "No cubre accidentes en días laborables ni en festivos",
    "Carencia de 18 meses para las urgencias",
    "Las prótesis solo se cubren si son de segunda mano",
    "No cubre lesiones jugando al pádel (el 90 % de los asegurados)",
    "La ambulancia se cubre si la empuja el asegurado",
    "Excluye las enfermedades preexistentes, incluida la de haber nacido",
    "Copago del 100 % en todo lo que no sea una tirita",
    "Los partos solo se cubren los martes y si son de gemelos",
    "Habitación individual, compartida con otros tres pacientes",
)
POLICY_FINE: tuple[str, ...] = (
    "Consultas de medicina general sin copago",
    "Urgencias 24 horas en los centros concertados",
    "Análisis de sangre con volante del especialista",
    "Hospitalización en habitación individual",
    "Pediatría para los hijos del titular",
    "Revisión ginecológica una vez al año",
    "Diez sesiones de fisioterapia al año",
    "Limpieza dental anual incluida",
    "Segunda opinión médica por videollamada",
)

DIALOGUE_PACKS.update(
    {
        "direccion": DialoguePack(
            "🏥 Junta técnico-asistencial. Regla de oro: **nunca digas «no hay dinero»; di "
            "«estamos priorizando».**",
            (
                Dialogue(
                    "Los cirujanos amenazan con dimitir en bloque por las peonadas.",
                    "Te sientas con ellos y pagas las tardes como se pactó.",
                    ("Que dimitan, ya vendrán los de la privada.",
                     "Les pones un reloj de fichar dentro del quirófano."),
                ),
                Dialogue(
                    "Urgencias tiene 40 pacientes en el pasillo.",
                    "Abres la planta que lleva cerrada «por obras» desde 2019.",
                    ("Mueves las camillas al parking, que hay más sitio.",
                     "Comunicado de prensa: «situación puntual»."),
                ),
                Dialogue(
                    "El jefe de Traumatología quiere un robot quirúrgico de 2 millones.",
                    "Pides un estudio de coste y de uso antes de firmar.",
                    ("Lo compras: queda muy bien en la foto.", "Le regalas un destornillador."),
                ),
                Dialogue(
                    "La supervisora te avisa: no hay sustitutos para agosto.",
                    "Abres la bolsa de empleo y haces contratos de verano completos.",
                    ("Contratos de un día, renovables cada mañana.",
                     "Cerráis el hospital en agosto, como los bares."),
                ),
                Dialogue(
                    "Un periodista pregunta por la lista de espera.",
                    "Das la cifra real y el plan para bajarla.",
                    ("«No es una lista: es una cola de esperanza».",
                     "Le dices que la culpa es de Perro Sanxe."),
                ),
                Dialogue(
                    "El sindicato pide descanso después de las guardias de 24 horas.",
                    "Lo garantizas por escrito: lo dice la ley.",
                    ("Les regalas un cojín cervical.",
                     "Que duerman en la cama de guardias, de dos en dos."),
                ),
                Dialogue(
                    "Un presentador famoso quiere saltarse la lista de espera.",
                    "Entra por la lista, como todo el mundo.",
                    ("Le abres un quirófano el domingo.", "Le pides un selfie a cambio."),
                ),
                Dialogue(
                    "Se cae el sistema informático a las nueve de la mañana.",
                    "Activas el plan de contingencia: papel y bolígrafo.",
                    ("Reinicias el servidor a patadas.", "Mandas a todo el mundo a casa."),
                ),
                Dialogue(
                    "Una consultora te ofrece «optimizar» el hospital por 3 millones.",
                    "Pides que expliquen qué van a hacer antes de pagar un yapdollar.",
                    ("Firmas: el PowerPoint tenía unas gráficas preciosas.",
                     "Le encargas también la cafetería."),
                ),
                Dialogue(
                    "La Consejería te pide recortar un 10 % «sin tocar la calidad».",
                    "Les mandas por escrito qué se recorta y qué se pierde con ello.",
                    ("Recortas el papel higiénico.",
                     "Apagas la luz del pasillo, que total ya hay pacientes."),
                ),
            ),
        ),
        "gerencia": DialoguePack(
            "📊 Reunión con la Consejería. Regla de oro: **todo es «un reto» y nada es «un "
            "problema».**",
            (
                Dialogue(
                    "El consejero quiere inaugurar el ala nueva antes de las elecciones. No "
                    "hay personal.",
                    "Propones abrirla por fases, a medida que haya plantilla.",
                    ("Cinta, tijeras y la luz apagada al día siguiente.",
                     "Contratas figurantes con bata para la foto."),
                ),
                Dialogue(
                    "Te piden explicar por qué el hospital tiene déficit.",
                    "Enseñas qué está infradotado y cuánto cuesta arreglarlo.",
                    ("La culpa es de los pacientes, por ponerse malos.",
                     "Escondes las facturas en un cajón, como en 2008."),
                ),
                Dialogue(
                    "Hay que comprar mascarillas urgentes y te llama un intermediario.",
                    "Compras por el procedimiento de emergencia, con todo por escrito.",
                    ("Compras al amigo de un asesor de un ministro, el del Rolls.",
                     "Pagas por adelantado a una empresa de colchones."),
                ),
                Dialogue(
                    "El hospital sale el último en un ranking.",
                    "Miras qué mide el ranking y mejoras lo que sí importa.",
                    ("Compras el ranking.", "Dices que el ranking lo ha hecho el Kremlin."),
                ),
                Dialogue(
                    "Los médicos se van a Francia y a Portugal porque pagan el doble.",
                    "Pides a la Consejería un plan de retención: sueldos y plazas fijas.",
                    ("Les cantas «¡Azul, azul, como el mar azul!» para que vuelvan.",
                     "Fichas médicos por TikTok."),
                ),
                Dialogue(
                    "La cafetería sube el café a 250 Y$.",
                    "Renegocias la concesión: el personal no puede pagar eso.",
                    ("«¡Quiero café!» y te lo pagas con las dietas.", "Prohíbes el café."),
                ),
                Dialogue(
                    "Un fondo de inversión quiere gestionar el hospital «con eficiencia "
                    "privada».",
                    "Le pides los números de los otros hospitales que gestiona.",
                    ("Le das las llaves y te vas de vacaciones.",
                     "Le vendes también el tanatorio."),
                ),
                Dialogue(
                    "Hay goteras en el quirófano 3.",
                    "Lo cierras y pides la obra urgente.",
                    ("Pones un cubo y a operar.", "Dices que es humedad ambiental, muy sana."),
                ),
                Dialogue(
                    "En el Parlamento autonómico te preguntan por las camas en el pasillo.",
                    "Reconoces el problema y das plazos.",
                    ("«Es el vecino el que elige la cama», como diría Rajoy.",
                     "Las camas en el pasillo dan ambiente."),
                ),
                Dialogue(
                    "El consejero te pide que la memoria anual quede «bonita».",
                    "La haces exacta y bien explicada.",
                    ("Gráficas con el eje empezando en 99.",
                     "La presentas en un NO-DO con música de fanfarria."),
                ),
            ),
        ),
        "consejeria": DialoguePack(
            "🏛️ Pleno del Parlamento autonómico. Regla de oro: **la culpa siempre es del "
            "Gobierno central (o del anterior).**",
            (
                Dialogue(
                    "La oposición te pregunta por la lista de espera.",
                    "Es herencia del Gobierno anterior y, aun así, la estamos bajando.",
                    ("Es culpa mía: dimito.", "¿Qué lista?"),
                ),
                Dialogue(
                    "Llega una pandemia nueva.",
                    "Un comité de expertos de verdad y datos públicos cada día.",
                    ("Un comité de expertos que no existe, como en 2020.",
                     "Recomiendas gárgaras de gofio."),
                ),
                Dialogue(
                    "Te piden explicar el contrato de mascarillas a precio de oro.",
                    "Publicas el expediente entero en el portal de transparencia.",
                    ("«Fue una urgencia y no me acuerdo».",
                     "Dices que las trajo Puigdemont en el maletero."),
                ),
                Dialogue(
                    "Toca repartir los fondos Next Generation.",
                    "Presentas proyectos concretos: centros de salud y equipos.",
                    ("Pides el dinero para una rotonda con estatua.",
                     "Te lo gastas en consultoras."),
                ),
                Dialogue(
                    "Ayuso dice que en Madrid se opera más y mejor.",
                    "Comparas los datos públicos, comunidad por comunidad.",
                    ("Le contestas con una frase sobre la fruta.",
                     "Le pides que te preste quirófanos."),
                ),
                Dialogue(
                    "Los médicos de familia convocan huelga: ven 60 pacientes al día.",
                    "Negocias cupos máximos y más plazas de residente.",
                    ("Les recomiendas consultas de un minuto.", "Contratas a Doctor Google."),
                ),
                Dialogue(
                    "Un periodista te pregunta si te has vacunado.",
                    "Sí, y enseñas el certificado.",
                    ("Como Bolsonaro: «yo no me vacuno».",
                     "Le pinchas a él para demostrarlo."),
                ),
                Dialogue(
                    "Trump dice que la sanidad de EE. UU. es «the best, believe me».",
                    "Explicas lo que cuesta allí una ambulancia.",
                    ("Le das la razón y copias su modelo.",
                     "Le mandas la factura de tu última guardia."),
                ),
                Dialogue(
                    "Te preguntan si vas a fichar por una aseguradora al dejar el cargo.",
                    "Respetarás el periodo de incompatibilidad, como manda la ley.",
                    ("Ya tengo el despacho elegido.",
                     "¿Puerta giratoria? Es una puerta automática."),
                ),
                Dialogue(
                    "Hay que nombrar al nuevo gerente del hospital.",
                    "Concurso de méritos y una comparecencia pública.",
                    ("Nombras al cuñado del alcalde.",
                     "A dedo, pero con un dedo muy profesional."),
                ),
            ),
        ),
        "clinica": DialoguePack(
            "💳 Recepción de la clínica. Regla de oro: **el paciente es «cliente», y el cliente "
            "siempre trae tarjeta.**",
            (
                Dialogue(
                    "Un futbolista quiere entrar por detrás para que no le vean.",
                    "Garaje privado, ascensor directo y confidencialidad.",
                    ("Le haces una foto para las redes.",
                     "Le cobras aparte la puerta de atrás."),
                ),
                Dialogue(
                    "Un cliente pregunta por qué la resonancia cuesta 40.000 Y$.",
                    "Le das el presupuesto detallado y le explicas qué incluye.",
                    ("Porque la hacemos con más cariño.",
                     "Porque la máquina es alemana y tiene sentimientos."),
                ),
                Dialogue(
                    "Una influencer quiere un chequeo «gratis a cambio de visibilidad».",
                    "Le ofreces la tarifa normal o un acuerdo por escrito.",
                    ("Se lo haces todo gratis por diez stories.",
                     "Le cobras por cada seguidor."),
                ),
                Dialogue(
                    "Un cliente VIP se queja de que el café de la sala de espera es de "
                    "máquina.",
                    "Barista y cruasán: esto es la privada.",
                    ("«¿Con pulpa? ¡Sin pulpa!» y le das un zumo de bote.",
                     "Que vaya a la pública, que allí ni café."),
                ),
                Dialogue(
                    "Un paciente de la mutua pide una prueba que su póliza no cubre.",
                    "Le explicas qué cubre y le das presupuesto del resto.",
                    ("Se la haces y que pague la mutua sin enterarse.",
                     "Le dices que lo suyo no existe."),
                ),
                Dialogue(
                    "Un médico quiere trabajar por la mañana en la pública y por la tarde aquí.",
                    "Si tiene la compatibilidad en regla, adelante.",
                    ("Que deje la pública, que aquí no hay listas.",
                     "Que se traiga a sus pacientes de la pública."),
                ),
                Dialogue(
                    "Un turista de EE. UU. no se cree lo barata que es la consulta.",
                    "Le atiendes con la tarifa de siempre.",
                    ("Le cobras la tarifa de Ohio.",
                     "Le dices que en España todo es gratis menos la luz."),
                ),
                Dialogue(
                    "Reseña de una estrella: «tardaron 7 minutos en atenderme».",
                    "Contestas con amabilidad y revisas qué pasó.",
                    ("Contestas: «en la pública tardan 7 meses».",
                     "Compras cien reseñas de cinco estrellas."),
                ),
                Dialogue(
                    "Un jeque quiere la planta entera para él y su séquito.",
                    "Le reservas la suite y mantienes las camas para el resto.",
                    ("Echas a todos los pacientes.", "Le alquilas la UCI como trastero."),
                ),
                Dialogue(
                    "Llega un cliente con un diagnóstico que le ha hecho un chatbot.",
                    "Le exploras y luego comparas con lo que dice la máquina.",
                    ("Le das la razón al chatbot.", "Le cobras la consulta al chatbot."),
                ),
            ),
        ),
        "conciertos": DialoguePack(
            "🤝 Despacho de la Consejería. Regla de oro: **la pública no da abasto y tú tienes "
            "quirófanos libres (qué casualidad).**",
            (
                Dialogue(
                    "La Comunidad quiere derivarte 5.000 cataratas.",
                    "Precio cerrado por proceso y resultados auditados.",
                    ("Las operas a toda prisa, que se paga por ojo.",
                     "Operas el mismo ojo dos veces."),
                ),
                Dialogue(
                    "Te ofrecen gestionar un hospital nuevo durante 30 años.",
                    "Contrato con indicadores de calidad y penalizaciones claras.",
                    ("Firmas con una cláusula: si pierdes dinero, paga la Comunidad.",
                     "Le pones tu nombre al hospital."),
                ),
                Dialogue(
                    "Un fondo de Luxemburgo quiere el 40 % del grupo.",
                    "Pides garantías de que no recortará plantilla.",
                    ("Vendes y fusionas Pediatría con la cafetería.", "Le vendes el 140 %."),
                ),
                Dialogue(
                    "La oposición dice que «estáis privatizando la sanidad».",
                    "Publicas cuánto cobráis por proceso y cuánto costaría en la pública.",
                    ("Les invitas a una revisión gratis en la suite.",
                     "Dices que es colaboración público-privada, como los sobres."),
                ),
                Dialogue(
                    "Hay que renovar el concierto de diálisis.",
                    "Mantienes precio y calidad: esos pacientes no pueden esperar.",
                    ("Subes el precio un 30 %: ¿adónde van a ir?",
                     "Ofreces diálisis a domicilio por Temu."),
                ),
                Dialogue(
                    "Un consejero deja el cargo y quiere trabajar con vosotros.",
                    "Esperas a que acabe su periodo de incompatibilidad.",
                    ("Le das un despacho al día siguiente.",
                     "Le pagas «asesorías» desde antes de dejarlo."),
                ),
                Dialogue(
                    "Los médicos piden más sueldo.",
                    "Revisas las tablas salariales: si se van, no hay hospital.",
                    ("Les ofreces una gorra con el logo.",
                     "Contratas médicos por horas, como los riders."),
                ),
                Dialogue(
                    "Un auditor pregunta por las facturas a la Comunidad.",
                    "Le das acceso a todo.",
                    ("Le invitas a un congreso en Cancún.", "Le facturas también la auditoría."),
                ),
                Dialogue(
                    "Milei te propone llevar tu modelo a Argentina.",
                    "Estudias el mercado con calma.",
                    ("Llevas una motosierra a la reunión.", "Le facturas en dólar blue."),
                ),
                Dialogue(
                    "Te piden publicar las listas de espera de la privada.",
                    "Las publicas, igual que la pública.",
                    ("En la privada no hay listas: hay «agendas premium».",
                     "«Lista de espera» te suena a comunismo."),
                ),
            ),
        ),
        "aseguradora": DialoguePack(
            "📈 Junta de accionistas. Regla de oro: **lo que se cobra es «prima»; lo que se "
            "paga es «siniestralidad».**",
            (
                Dialogue(
                    "Los accionistas preguntan por qué el beneficio ha subido un 40 %.",
                    "Más asegurados por las listas de la pública y una buena gestión.",
                    ("Porque denegamos el 40 % de los partes.",
                     "Porque cobramos también a los difuntos."),
                ),
                Dialogue(
                    "Un asegurado de 80 años quiere renovar.",
                    "Le renuevas: lleva treinta años pagando.",
                    ("Le subes la prima un 300 % «por la edad».",
                     "Le ofreces un seguro de decesos en su lugar."),
                ),
                Dialogue(
                    "El regulador revisa tus cláusulas.",
                    "Simplificas la póliza y la publicas en lenguaje claro.",
                    ("Las imprimes en cuerpo 3 para que no se lean.",
                     "Las escribes en latín, como las recetas de antes."),
                ),
                Dialogue(
                    "Trump te propone entrar en el mercado de EE. UU.",
                    "Estudias la regulación y los riesgos antes de entrar.",
                    ("Entras cobrando 40.000 dólares por una ambulancia.",
                     "Le regalas un seguro dental a cambio de quitar los aranceles."),
                ),
                Dialogue(
                    "Hacienda revisa cómo vendes la exención de los seguros de empresa.",
                    "Cumples la norma y se la explicas bien a tus clientes.",
                    ("Le ofreces a Perro Sanxe una póliza gratis.",
                     "Te llevas la sede a Andorra."),
                ),
                Dialogue(
                    "Una periodista pregunta por los partes denegados.",
                    "Das los datos y el procedimiento de reclamación.",
                    ("«Solo denegamos lo que habría que pagar».",
                     "«No me consta, no me consta, no me consta»."),
                ),
                Dialogue(
                    "La competencia lanza pólizas a 999 Y$.",
                    "Compites con servicio y con buen cuadro médico.",
                    ("Lanzas una a 998 Y$ que no cubre nada.",
                     "Copias su anuncio con un perro más bonito."),
                ),
                Dialogue(
                    "Los médicos del cuadro no quieren seguir cobrando 1.200 Y$ por consulta.",
                    "Subes las tarifas: sin médicos no hay seguro.",
                    ("Les pagas en vales de gasolina.",
                     "Les dices que «antes se trabajaba por vocación»."),
                ),
                Dialogue(
                    "Llega una ola de gripe y se disparan las consultas.",
                    "Refuerzas el cuadro médico y la teleconsulta.",
                    ("Subes las primas a mitad de año.", "Cierras por vacaciones hasta marzo."),
                ),
                Dialogue(
                    "Erdoğan te invita a abrir clínicas de injerto capilar en Estambul.",
                    "Le das las gracias y sigues centrado en tu mercado.",
                    ("Aceptas y pones su cara en el logo.",
                     "Te haces un injerto en directo en la reunión."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: las ramas de la hostelería (Hotelera y Alta cocina) ----------------------------

MEMORY_PACKS.update(
    {
        "pulseras": MemoryPack(
            "🛬 El vuelo de Mánchester ha aterrizado. El huésped {n} lo pide todo, en este orden:",
            "Cúmplele las peticiones en el mismo orden, que luego te las reclama en inglés.",
            (
                ("🏖️", "Hamaca en primera fila"),
                ("🧺", "Toalla en la tumbona a las 7 de la mañana"),
                ("🟢", "Pulsera del todo incluido"),
                ("🌊", "Cambio a una habitación con vistas al mar"),
                ("🍺", "Cerveza a las diez de la mañana"),
                ("🥞", "Desayuno hasta las dos de la tarde"),
                ("🧊", "Aire acondicionado a 16 °C"),
                ("🫖", "Té con leche (hirviendo)"),
                ("🍊", "Zumo de naranja: ¿con pulpa? ¡Sin pulpa!"),
                ("🍳", "Huevos fritos en el buffet"),
            ),
        ),
        "emplatado": MemoryPack(
            "🍽️ Plato {n} del menú degustación. Se emplata con pinzas, en este orden:",
            "Emplata con las pinzas en el mismo orden, que si se mueve algo se hunde la estrella.",
            (
                ("🥔", "Aire de papa arrugada"),
                ("🧪", "Esferificación de mojo"),
                ("🌱", "Tierra de almogrote"),
                ("☁️", "Espuma de gofio"),
                ("🧊", "Gel de barraquito"),
                ("🌸", "Flor comestible (sin sabor)"),
                ("🪙", "Hoja de pan de oro"),
                ("💧", "Una gota de aceite en el centro"),
                ("🫧", "Humo de nogal bajo una campana"),
            ),
        ),
    }
)  # fmt: skip

SORT_PACKS.update(
    {
        "quejas": SortPack(
            "📝 Eres director regional. Las reseñas llegan a tu mesa: cada una a su "
            "departamento, y a las absurdas, un «lamentamos su experiencia».",
            "⭐ Reseña {n}/{total}:",
            ("🧹 Limpieza", "🍳 Buffet", "🛎️ Recepción", "🙄 «Lamentamos su experiencia»"),
            (
                ("Encontré un pelo en la ducha. No era mío. Ni de nadie que conozca", 0),
                ("La toalla olía a humedad del siglo pasado", 0),
                ("Las sábanas tenían una mancha con forma de Galicia", 0),
                ("La habitación 214 no se limpió en cuatro días", 0),
                ("Los huevos revueltos llevaban un par de días en el buffet", 1),
                ("Se acabó el jamón a las 8:05 de la mañana", 1),
                ("La máquina de zumo hacía un ruido de moto", 1),
                ("La fuente de fruta estaba en la otra punta del comedor", 1),
                ("Esperé 40 minutos para el check-in con las maletas en la mano", 2),
                ("Me dieron una habitación con vistas a un muro y yo pedí el mar", 2),
                ("Me cobraron dos veces la tasa turística", 2),
                ("Me cambiaron de habitación a las dos de la madrugada sin avisar", 2),
                ("Llovió el día que fui a la playa. Exijo la devolución", 3),
                ("Nadie me avisó de que en la playa hay arena", 3),
                ("El mar estaba demasiado mojado", 3),
                ("Mi marido roncaba y no me dieron otro marido", 3),
            ),
        ),
        "brigada": SortPack(
            "🔪 Eres el dueño del grupo. Manda a cada uno de tu brigada a su partida (y que "
            "nadie se entere de quién cobra qué).",
            "🧑‍🍳 Cocinero {n}/{total}:",
            ("🔥 Cuarto caliente", "🥗 Cuarto frío", "🍰 Pastelería", "🧽 Office"),
            (
                ("Hace una salsa que no se corta ni a cuchilladas", 0),
                ("Controla el punto de la carne a ojo, con la plancha a 300 grados", 0),
                ("Se pasa la vida entre fogones y le encanta el humo", 0),
                ("Es el único que no se achicharra con la sartén en llamas", 0),
                ("Hace ensaladas con pinzas y un pulso de cirujano", 1),
                ("Se le dan de maravilla los ceviches y los carpaccios", 1),
                ("Corta verdura en brunoise sin mirar", 1),
                ("Monta los entrantes fríos y le tiene pánico al fuego", 1),
                ("Pesa el azúcar al gramo y no se fía de nadie", 2),
                ("Se levanta a las cuatro de la mañana a amasar", 2),
                ("Hace merengues que se aguantan solos", 2),
                ("Sueña con una tarta con forma de Falcon", 2),
                ("Es el stagier danés que vino a aprender gratis", 3),
                ("Se presentó para chef y le han dado un estropajo", 3),
                ("El becario que dijo que sabía freír un huevo", 3),
                ("El sobrino del socio, que no sabe ni encender una vitro", 3),
            ),
        ),
    }
)  # fmt: skip

#: Colados del buffet del resort: el que no lleva pulsera del todo incluido.
GATECRASHERS: tuple[str, ...] = (
    "Lleva una pulsera dibujada con rotulador verde",
    "Dice «soy de la 304», pero en tu hotel no hay habitación 304",
    "Luce la pulsera del todo incluido… de la Feria de Abril de 2016",
    "Repite por cuarta vez el plato de gambas y no tiene pulsera",
    "Su pulsera es de papel de aluminio, pegada con celo",
    "Entra por la cocina con una bandeja y una camisa de camarero (que no lo es)",
    "Dice que es primo del director, y es lo único que dice",
    "Cena en el buffet con la pulsera de la piscina de otro hotel",
    "Se llena los bolsillos de bocadillos con la servilleta al cuello",
)
GUESTS_LEGIT: tuple[str, ...] = (
    "Pulsera verde del todo incluido, bañador y un plato de huevos",
    "Familia de cuatro con pulseras a juego y los niños con zumo",
    "Pareja de jubilados con pulsera y su propio salero",
    "Despedida de soltero con pulsera y resaca",
    "Huésped con pulsera y la tarjeta de la habitación en la mano",
    "Un señor con pulsera, chancletas y dos platos (con pulsera, no pasa nada)",
    "Mujer con pulsera y un periódico de hace tres días",
    "Matrimonio de Mánchester con pulsera y cerveza a las nueve de la mañana",
    "Niño con pulsera y un plato de patatas fritas, y nada más",
    "Huésped con pulsera dorada de suite y tostada con aguacate",
    "Recién casados con pulsera y cara de no haber dormido",
    "Abuela con pulsera que guarda una naranja en el bolso",
)

#: Los inspectores de la Guía tienen sus manías; los demás comensales, las suyas.
INSPECTOR_TELLS: tuple[str, ...] = (
    "Come solo, apunta en una libreta y pide el menú largo",
    "Hace fotos de cada plato con una cámara de verdad, no con el móvil",
    "Pregunta de dónde viene la sal y habla en voz baja",
    "Pide agua del grifo, mira el mantel a contraluz y cuenta los cubiertos",
    "Reserva a nombre de «Sr. Fernández» y paga siempre con tarjeta de empresa",
    "Mastica cada bocado como si le dictaran sentencia",
    "Va al baño tres veces y vuelve con la libreta más gorda",
    "Se pasa la servilleta por la copa y la mira a contraluz",
    "Pide una copa de vino y no la bebe: la huele y la apunta",
)
DINERS_NORMAL: tuple[str, ...] = (
    "Pareja que se hace un selfie con la tarta",
    "Grupo de ocho celebrando una comunión a gritos",
    "Influencer que graba la carta con el móvil mientras pide",
    "Un cuñado que dice que él lo haría mejor",
    "Pareja de aniversario que se mira y no habla",
    "Familia de turistas que pide ketchup para el solomillo",
    "Un abuelo que pregunta si hay papas arrugadas",
    "Ejecutivo que habla por teléfono a gritos",
    "Dos amigos que se pelean por pagar la cuenta",
    "Un niño que pide macarrones en un restaurante de tres tenedores",
    "Señora que pide la salsa aparte, el pan aparte y el plato aparte",
    "Pareja que pide «el vino más caro» y lo mezcla con cola",
)

DIALOGUE_PACKS.update(
    {
        "cadena_hotelera": DialoguePack(
            "🏢 Junta con los inversores. Regla de oro: **lo que cuesta dinero es «inversión» "
            "y lo que se ahorra, «eficiencia».**",
            (
                Dialogue(
                    "Un inversor pregunta qué piensas hacer con los pisos turísticos de la "
                    "competencia.",
                    "Compramos cien y los llamamos «apartahotel boutique». Es inversión.",
                    ("Pedir al Ayuntamiento que los cierre todos, menos los míos.",
                     "Mandarles una paella con chorizo de bienvenida."),
                ),
                Dialogue(
                    "Costas dice que el hotel de la playa está en dominio público.",
                    "Inversión en seguridad jurídica: un bufete y veinte años de recursos.",
                    ("Mover la playa dos metros, que sale más barato.",
                     "Reconocer que se construyó en la arena en 1971."),
                ),
                Dialogue(
                    "Las kellys piden dejar de limpiar veinte habitaciones por turno.",
                    "Eficiencia: lo externalizamos y que su jefe sea una empresa que no "
                    "contesta al teléfono.",
                    ("Subirles el sueldo, que se lo han ganado.",
                     "Proponer que los huéspedes se hagan la cama, como en el IKEA."),
                ),
                Dialogue(
                    "Los accionistas quieren más margen este trimestre.",
                    "Eficiencia: la toalla de la piscina pasa a ser «servicio premium».",
                    ("Recordarles que el año pasado batimos récord.",
                     "Proponer cerrar el buffet los lunes, que los lunes no come nadie."),
                ),
                Dialogue(
                    "El Gobierno canario anuncia una ecotasa.",
                    "La cobramos con una línea de «contribución sostenible» y un 8 % de "
                    "gestión. Eficiencia verde.",
                    ("Anunciar que el turista se irá a Marruecos mañana mismo.",
                     "Pagarla nosotros, por la imagen."),
                ),
                Dialogue(
                    "Una agencia online te pide el 22 % de comisión por reserva.",
                    "Inversión en canal propio: «reserva en nuestra web y te regalamos un "
                    "chupito de ron miel».",
                    ("Subir un 22 % el precio y que pague el guiri.",
                     "Hacer una web que solo funciona los martes."),
                ),
                Dialogue(
                    "Sale en la prensa que el hotel tiene 300 reseñas falsas de cinco "
                    "estrellas.",
                    "«Una inversión en reputación digital que la competencia no entiende».",
                    ("Comprar 3.000 más para diluirlas.",
                     "Culpar a los bots de Putin."),
                ),
                Dialogue(
                    "Un fondo quiere el 30 % de la cadena.",
                    "Se lo vendemos y lo llamamos «inversión estratégica»: el que se va de "
                    "vacaciones a Suiza eres tú.",
                    ("Venderle el 130 %, por si cuela.",
                     "Firmarlo en una servilleta del bar del hotel."),
                ),
                Dialogue(
                    "Hay que cambiar los colchones de 1998 y el presupuesto no da.",
                    "Les damos la vuelta: «plan de renovación integral, fase uno».",
                    ("Comprarlos nuevos, que hay quejas.",
                     "Poner camas de campaña y un cartel de «glamping»."),
                ),
                Dialogue(
                    "Un periodista pregunta cuánto cobran los camareros de temporada.",
                    "«Lo justo para un sector con un enorme potencial de eficiencia».",
                    ("Dar la cifra de verdad.",
                     "«Es una vocación» y colgar el teléfono."),
                ),
            ),
        ),
        "patronal": DialoguePack(
            "🏛️ Mesa con el Gobierno. Regla de oro: **siempre hay crisis… salvo en el "
            "balance.**",
            (
                Dialogue(
                    "El Gobierno propone subir la tasa turística.",
                    "«El sector atraviesa un momento muy delicado» (récord de visitantes "
                    "por cuarto año seguido).",
                    ("Decir que vale, que total la paga el turista.",
                     "Proponer que se cobre también a los peninsulares."),
                ),
                Dialogue(
                    "El ministro quiere la foto del acuerdo antes de las elecciones.",
                    "Foto sí, firma después: «estamos muy cerca, pero el sector sufre».",
                    ("Firmar lo que sea con tal de salir en el telediario.",
                     "Hacerte un selfie con él y subirlo con «#Turismo»."),
                ),
                Dialogue(
                    "Los sindicatos piden subir el convenio un 12 %.",
                    "«Con la crisis del sector, un 2 % y una mesa de seguimiento».",
                    ("Aceptar el 12 %, que el año fue bueno.",
                     "Ofrecerles una cesta de Navidad con turrón de Alicante."),
                ),
                Dialogue(
                    "Una tertulia te pregunta por qué no encontráis camareros.",
                    "«Es un problema de formación y de cultura del esfuerzo, no de "
                    "salarios».",
                    ("«Porque pagamos 90.000 Y$ por diez horas».",
                     "«Porque los jóvenes prefieren ser influencers»."),
                ),
                Dialogue(
                    "Hay manifestaciones contra el turismo masivo en tres islas.",
                    "«Compartimos la preocupación» y pides más fondos para promoción "
                    "turística en el extranjero.",
                    ("Decir que quien protesta no sabe de dónde sale su sueldo.",
                     "Colgar un cartel de «Welcome» que dice «Go home» por detrás."),
                ),
                Dialogue(
                    "El Gobierno plantea limitar los pisos turísticos.",
                    "«Bienvenida sea la regulación… para los pisos. Los hoteles somos "
                    "otra cosa».",
                    ("Decir que es el fin del turismo, de la paella y de España.",
                     "Comprar diez edificios esa misma tarde y convertirlos."),
                ),
                Dialogue(
                    "Te piden explicar por qué la habitación ha subido un 40 %.",
                    "«La energía, la inflación y la crisis» (la crisis siempre va al final).",
                    ("«Porque la gente lo paga».",
                     "Cobrar la vista al mar en yapdollars por mirada."),
                ),
                Dialogue(
                    "Llegan los fondos Next Generation para modernizar hoteles.",
                    "«El sector está en crisis: los necesitamos todos, y para ayer».",
                    ("Renunciar a ellos por responsabilidad.",
                     "Pedirlos para la piscina climatizada de tu chalet."),
                ),
                Dialogue(
                    "Te preguntan si el presidente usará el Falcon para venir al congreso "
                    "del sector.",
                    "«Que venga como quiera, pero que traiga la subvención en la bodega».",
                    ("«El Falcon es contaminante y una vergüenza».",
                     "Pedir un Falcon para la patronal."),
                ),
                Dialogue(
                    "Un informe dice que la hostelería gana mucho y paga poco.",
                    "«Ese informe es de antes de la crisis» (de la que sea).",
                    ("Reconocerlo y proponer una subida.",
                     "Demostrar con un gráfico al revés que pagáis muchísimo."),
                ),
            ),
        ),
        "plato_tv": DialoguePack(
            "📺 Gran final del talent de cocina. Regla de oro: **humilla con gracia: el "
            "público quiere sangre, pero en cámara lenta.**",
            (
                Dialogue(
                    "Un concursante te presenta un pollo crudo por dentro.",
                    "«Este pollo aún tiene esperanzas de llegar a gallina». Pausa. Primer "
                    "plano.",
                    ("Explicarle con calma qué temperatura necesita.",
                     "Tirarlo al suelo, que eso lo hizo otro y ya no hace gracia."),
                ),
                Dialogue(
                    "Una concursante llora porque se le ha cortado la mayonesa.",
                    "«Llora, que con la sal de las lágrimas a lo mejor la salvamos».",
                    ("Abrazarla y decirle que no pasa nada.",
                     "Pedir la siguiente sin mirarla."),
                ),
                Dialogue(
                    "El productor te pide más mala leche: la audiencia baja.",
                    "Pruebas el plato, dejas el tenedor muy despacio y miras a cámara "
                    "cinco segundos sin decir nada.",
                    ("Gritar a todo el mundo, cámaras incluidas.",
                     "Decirle que tú eres cocinero, no actor."),
                ),
                Dialogue(
                    "Un concursante presenta «la receta de su abuela» con lágrimas en los "
                    "ojos.",
                    "«Tu abuela se merecía un nieto que la hubiera escuchado mejor».",
                    ("«Está buenísimo, enhorabuena a tu abuela».",
                     "Preguntarle el nombre de la abuela para el titular."),
                ),
                Dialogue(
                    "La marca patrocinadora quiere que nombres su aceite en cada plato.",
                    "«Aceite de… la marca que paga este programa». Y guiñas a cámara.",
                    ("Negarte en directo: «aquí no se hace publicidad».",
                     "Echarle aceite hasta al flan."),
                ),
                Dialogue(
                    "Un concursante ha deconstruido una tortilla de patatas.",
                    "«La has deconstruido tanto que ya no la encuentra ni Puigdemont "
                    "desde Waterloo».",
                    ("Felicitarle por la técnica.",
                     "Pedirle que te la construya otra vez, por favor."),
                ),
                Dialogue(
                    "Te corrige un plato en directo un chef invitado.",
                    "«Gracias, maestro. Cuando tengas programa propio lo comentamos».",
                    ("Darle las gracias y aprender algo.",
                     "Tirarle una espátula."),
                ),
                Dialogue(
                    "El público vota salvar al concursante que peor cocina.",
                    "«El pueblo ha hablado… y ha hablado con la boca llena de algo crudo».",
                    ("Anular el voto «por irregularidades», como Maduro con las actas.",
                     "Decir que la audiencia no sabe de cocina."),
                ),
                Dialogue(
                    "Un niño del especial infantil te sirve un sándwich de crema de cacao.",
                    "«Es lo mejor que he probado esta temporada». Y es verdad.",
                    ("Destrozarlo como a los adultos.",
                     "Decirle que eso no es cocina."),
                ),
            ),
        ),
        "dubai": DialoguePack(
            "🏜️ Tu restaurante de Dubái. Regla de oro: **si no lleva pan de oro, todavía no "
            "está terminado.**",
            (
                Dialogue(
                    "Un jeque quiere algo «que no tenga nadie».",
                    "Un huevo frito con pan de oro, servido por un camarero vestido de "
                    "huevo frito.",
                    ("Un huevo frito normal, que está riquísimo.",
                     "Un lingote con una gota de vinagre."),
                ),
                Dialogue(
                    "Un emir pide papas arrugadas como las de tu abuela.",
                    "Papas arrugadas con mojo de azafrán y lámina de oro, a 40.000 Y$ "
                    "la papa.",
                    ("Unas papas arrugadas de verdad, con sal de Janubio.",
                     "Decirle que eso no se pide en Dubái."),
                ),
                Dialogue(
                    "Un millonario quiere comprar tu plato estrella como NFT.",
                    "Se lo vendes en NFT y, de regalo, el plato de verdad con oro por "
                    "encima para la foto.",
                    ("Explicarle que un plato se come.",
                     "Vendérselo partido en dos tokens, uno por mitad."),
                ),
                Dialogue(
                    "Elon Musk tuitea que abrirá una cadena de espumas en Marte.",
                    "Le contestas que la tuya ya lleva pan de oro y el cohete se paga "
                    "aparte.",
                    ("Desearle suerte y seguir cocinando.",
                     "Pedirle trabajo en Marte."),
                ),
                Dialogue(
                    "Un influencer pide el menú de 150.000 Y$ «para hacer un unboxing».",
                    "Se lo cobras… y le regalas una hoja de oro para que la abra en "
                    "cámara.",
                    ("Dárselo gratis a cambio de una story.",
                     "Cobrarle también la luz del ring light."),
                ),
                Dialogue(
                    "Un socio quiere franquiciar tu marca en un centro comercial.",
                    "Sí, pero con mostrador de mármol, camareros de esmoquin y oro en las "
                    "servilletas.",
                    ("Aceptarlo tal cual: bandejas de plástico y cola.",
                     "Negarte: «mi cocina no se vende»."),
                ),
                Dialogue(
                    "Un crítico escribe que tu espuma «sabe a jabón».",
                    "Le mandas una nueva «espuma de jabón con oro» y la pones en la carta "
                    "a 9.000 Y$.",
                    ("Reconocer el error y cambiar la receta.",
                     "Pagarle para que la cambie."),
                ),
                Dialogue(
                    "Te invitan a cocinar en la cumbre del clima de Dubái.",
                    "Menú sostenible: verduras de kilómetro cero… con pan de oro de "
                    "kilómetro seis mil.",
                    ("Un menú vegano sencillo, sin florituras.",
                     "Hamburguesas de avión, para que se sientan en casa."),
                ),
                Dialogue(
                    "Hacienda española pregunta dónde tributa tu franquicia.",
                    "«En Dubái, donde está mi pan de oro y mi domicilio fiscal (y mi "
                    "jeque)».",
                    ("Tributar en España, que es tu país.",
                     "Decir que vives en un avión y que no tienes domicilio."),
                ),
                Dialogue(
                    "El jeque prueba el plato y no dice nada.",
                    "Le añades otra lámina de oro delante de él. Sonríe.",
                    ("Preguntarle si le gusta.",
                     "Retirarlo y pedir perdón en tres idiomas."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: las ramas del reparto (Correos e Imperio logístico) -----------------------

MEMORY_PACKS.update(
    {
        "ruta_rural": MemoryPack(
            "🏔️ Ruta rural {n}. Hoy el reparto pasa por estos sitios, en este orden:",
            "Repite la ruta en el mismo orden, que aquí no hay GPS ni cobertura (ni asfalto).",
            (
                ("🏘️", "La aldea de tres vecinos y un gallo"),
                ("🗼", "El faro, con un farero que lleva años sin salir"),
                ("⛴️", "El barco a La Graciosa (si hay mar)"),
                ("🐕", "La casa del perro que no muerde (muerde)"),
                ("🍻", "El bar que hace de oficina de Correos"),
                ("🐐", "El camino de cabras sin asfaltar"),
                ("⛪", "La ermita con buzón y sin cura"),
                ("🌋", "El pueblo de la lava, con un único buzón"),
                ("🚜", "El tractor que corta la carretera"),
                ("🏞️", "El caserío al que solo se llega a pie"),
            ),
        ),
        "dark_store": MemoryPack(
            "📱 Pedido de 10 minutos número {n}. En la dark store hay que recoger, en este orden:",
            "Recógelo en el mismo orden, que el cliente tiene prisa y tu tienda, la persiana "
            "bajada.",
            (
                ("🥛", "Leche del pasillo 3, el del fondo"),
                ("🍫", "Chocolate «de emergencia»"),
                ("🧻", "Papel higiénico, por si acaso"),
                ("🥑", "Aguacate «listo para hoy» (nunca lo está)"),
                ("🍺", "Pack de cervezas a las tres de la madrugada"),
                ("🧊", "Hielo, que se derrite en diez minutos"),
                ("🍕", "Pizza congelada de la nevera del fondo"),
                ("🕯️", "Vela aromática, por romanticismo"),
                ("🔋", "Pilas AA del cajón de debajo"),
                ("🍦", "El último helado de pistacho"),
            ),
        ),
    }
)  # fmt: skip

SORT_PACKS.update(
    {
        "paquetes": SortPack(
            "📦 Eres jefe de unidad de reparto. Manda cada envío a su sitio (a la velocidad "
            "del Estado).",
            "📮 Envío {n}/{total}:",
            ("📬 Buzón", "🏢 «Ausente»: a la oficina", "🔁 Devolver al remitente",
             "🗳️ Voto por correo: urgente"),
            (
                ("Una carta de la comunidad con la derrama del ascensor", 0),
                ("Una postal de Benidorm con el sello despegándose", 0),
                ("Un folleto del súper con el 3×2 en lentejas", 0),
                ("Una carta del banco que empieza por «Estimado cliente»", 0),
                ("Un paquete de 18 kilos con el cliente de vacaciones en Tailandia", 1),
                ("Una certificada de Hacienda para un vecino que nunca abre la puerta", 1),
                ("Una lavadora para un cuarto sin ascensor y con el timbre roto", 1),
                ("Un burofax cuyo destinatario te ve venir desde la ventana", 1),
                ("Una carta a «Calle Sin Nombre, s/n, Pueblo»", 2),
                ("Un sobre sin sello, sin remitente y que huele a queso", 2),
                ("Una felicitación navideña para un difunto, desde 1998", 2),
                ("Un paquete cuyo destinatario pone «Dirección desconocida» y «fecha: ayer»", 2),
                ("Un sobre de voto por correo, con 48 horas para las elecciones", 3),
                ("Otro voto por correo, y la furgoneta sin gasolina", 3),
                ("El voto de un residente en el extranjero, que lleva tres semanas en Zaragoza", 3),
                ("Un sobre de la Junta Electoral con un voto dentro y un nervio fuera", 3),
            ),
        ),
        "ultima_milla": SortPack(
            "🚀 Eres el rey de la última milla. Manda cada pedido a su reparto (y a la nómina "
            "más baja posible).",
            "📲 Pedido {n}/{total}:",
            ("🚐 Furgoneta subcontratada", "🚲 Rider «colaborador»", "🛸 Dron",
             "📦 «Entregado» (sin entregar)"),
            (
                ("Una lavadora para un 5.º con ascensor, de un cliente sin prisa", 0),
                ("Cuarenta cajas de agua para una despedida de soltero", 0),
                ("Un sofá de tres plazas que no cabe por el portal", 0),
                ("Una estantería en kit con 214 tornillos, uno de ellos suelto", 0),
                ("Una hamburguesa y dos refrescos a 2,5 km, con un perro por el camino", 1),
                ("Un sushi para dos, con el cliente en pijama y con hambre", 1),
                ("Un café para la oficina, en plena reunión de las diez", 1),
                ("Un helado de cucurucho en agosto, con el cliente mirando el reloj", 1),
                ("Un paquete de 300 gramos a una azotea sin viento ni pájaros", 2),
                ("Un botiquín a una playa vacía y con buena visibilidad", 2),
                ("Unas llaves olvidadas para el vecino de enfrente (seis metros)", 2),
                ("Un pendrive para una torre de oficinas, sin gaviotas a la vista", 2),
                ("Un pedido con el cliente en el trabajo y el timbre sin cable", 3),
                ("Un paquete que lleva tres días «en reparto» en el cajón de la furgoneta", 3),
                ("Un pedido cuyo cliente ya puso la reclamación (pero antes lo marcamos)", 3),
                ("Un pedido a un barrio donde no entran ni la furgoneta ni el dron", 3),
            ),
        ),
    }
)  # fmt: skip

#: El paquete perdido de Correos: no se distingue por una palabra, sino por su historia.
LOST_PARCELS: tuple[str, ...] = (
    "Lleva en tránsito desde 2019 y tiene más sellos que un pasaporte diplomático",
    "Ha pasado por Zaragoza nueve veces y siempre figura «en reparto»",
    "En la etiqueta pone «Frágil», «Urgente» y «Déjalo donde quieras»",
    "Su remitente, ya jubilado, preguntó por él en 2021 y le dieron un número de caso",
    "Seguimiento: «Entregado en Jaén». El destinatario vive en Gran Canaria",
    "Figura en tres almacenes a la vez, según el sistema",
    "Lleva un sello de 1987 con la peseta tachada a boli",
    "Lo han reclamado un cliente, dos abogados y un notario",
    "Estado: «en tránsito». Ubicación: «desconocida». Destino: «el más allá»",
    "El cartero jura que lo entregó, el vecino jura que no, y el buzón calla",
)
PARCELS_TRACKED: tuple[str, ...] = (
    "Pedido de 600 g, entregado ayer con firma del cliente",
    "Carta ordinaria con sello nuevo y dirección clara",
    "Paquete de 2 kilos escaneado esta mañana en el centro logístico",
    "Sobre acolchado con zapatillas, en reparto con su ruta asignada",
    "Libro de segunda mano con etiqueta nítida y código de barras legible",
    "Caja de 5 kilos camino de una oficina, con aviso de recogida",
    "Envío internacional con la aduana despachada esta mañana",
    "Certificada con acuse de recibo, firmada por el portero",
    "Paquete de pilas que sale de la nave y llega a la hora",
    "Sobre con un DNI nuevo, entregado en mano",
    "Caja con un microondas, en el camión de las 14:00",
    "Devolución de unos zapatos, ya de vuelta en el almacén de origen",
    "Envío urgente de un día, con el cartero llamando al timbre",
)

#: Las inspectoras de Trabajo infiltradas entre tus riders y los riders de verdad.
LABOUR_INSPECTORS: tuple[str, ...] = (
    "Pregunta por el convenio, por la cotización y por el nombre de tu asesor",
    "Lleva casco, chaleco y un sello oficial escondido en la mochila",
    "Apunta en una libreta cuántas horas lleva de turno y quién se las paga",
    "Hace fotos de la mochila, del casco y de la app sin pedir ningún pedido",
    "No tiene prisa ni quiere propina, pero sí saber quién le paga la mutua",
    "Pide el registro horario a un rider que «es colaborador»",
    "Se sabe la Ley Rider de memoria y la recita entre pedido y pedido",
    "Reparte con un bocadillo de la cantina de la Administración",
    "Su pedido es de 400 Y$, pero guarda las llaves de un coche oficial",
    "Le pregunta a tu abogado «y esto, ¿cómo lo cotizáis?» con una sonrisa que da miedo",
)
RIDERS_NORMAL: tuple[str, ...] = (
    "Chaval de 19 años con una bici eléctrica comprada a plazos",
    "Padre de familia que lleva ocho horas repartiendo sin comer",
    "Estudiante de Informática con la mochila térmica del primo",
    "Rider que habla con su madre por el manos libres",
    "Recién llegado, perdido y con el GPS al revés",
    "Moto en doble fila, intermitente roto y cara de prisa",
    "Rider que escucha reguetón a todo volumen dentro del casco",
    "Móvil agrietado y el 3 % de batería",
    "Rider con dos apps abiertas y cara de pocos amigos",
    "Se ha comido una patata del pedido y lo niega con la boca llena",
    "Discute con otro rider por el último sitio del semáforo",
    "Cuenta que hoy lleva 3.800 Y$ en toda la tarde",
    "Pregunta si hay propina «en esta app o en otro universo»",
)

DIALOGUE_PACKS.update(
    {
        "direccion_correos": DialoguePack(
            "🏢 Consejo de Administración de Correos. Regla de oro: **si pierde dinero, es "
            "«servicio público»; si lo gana, «gestión eficiente».**",
            (
                Dialogue(
                    "El Gobierno te pregunta por las pérdidas de 250 millones del año.",
                    "«Es el precio del servicio público universal: cada carta a la aldea es "
                    "un acto de soberanía nacional.»",
                    ("Reconocer que se ha gestionado fatal y pedir perdón.",
                     "Pedir un rescate a Moncloa, otro a Bruselas y otro al Papa."),
                ),
                Dialogue(
                    "Hay que cerrar 300 oficinas rurales que no son rentables.",
                    "Las «reconvertimos» en puntos de servicio: una máquina de sellos en el "
                    "bar del pueblo. Gestión eficiente.",
                    ("Mantenerlas todas abiertas aunque no entre nadie, por humanidad.",
                     "Mandar un helicóptero que lea las cartas en voz alta a los vecinos."),
                ),
                Dialogue(
                    "El Banco de Correos tiene que dar beneficios.",
                    "Lanzas «Correos Banco»: mismas colas, mismo sello, y ahora con "
                    "comisión de mantenimiento por tener sello.",
                    ("Cerrar el banco porque no lo usa nadie.",
                     "Guardar el dinero en un buzón con candado."),
                ),
                Dialogue(
                    "La SEPI te pide un plan de viabilidad para mañana.",
                    "Entregas 400 páginas con un diagrama de flujo, una flecha a "
                    "«digitalización» y otra a «contexto».",
                    ("Entregar una hoja que diga «vender más sellos».",
                     "Decirle que la SEPI se ocupe de la SEPI."),
                ),
                Dialogue(
                    "Las grandes plataformas reparten con su propia flota y te quitan el negocio.",
                    "Les ofreces una «alianza estratégica»: ellos reparten y tú pones el "
                    "logo.",
                    ("Denunciarlas por competencia desleal con un sello de 200 Y$.",
                     "Prohibir los paquetes de menos de dos kilos."),
                ),
                Dialogue(
                    "Los sindicatos piden más plantilla porque los carteros reparten 300 "
                    "cartas por cabeza.",
                    "Anuncias un plan de empleo de 3.000 plazas «a convocar en 2031».",
                    ("Contratar 3.000 carteros mañana por la mañana.",
                     "Pasar el reparto a los jubilados del hogar del pensionista."),
                ),
                Dialogue(
                    "Un periodista pregunta por qué su carta de 2019 sigue en tránsito.",
                    "«Está en tránsito estratégico: las cartas bien curadas mejoran con "
                    "los años.»",
                    ("Admitir que está en un contenedor de Zaragoza con otras 40.000.",
                     "Pedirle que la mande otra vez, certificada y con otro sello."),
                ),
                Dialogue(
                    "El PP pregunta en el Congreso por los coches oficiales de la directiva.",
                    "Son «vehículos de última milla de la alta dirección»: eficiencia "
                    "logística.",
                    ("Decir que son de un sobrino.",
                     "Aparcar uno en una rotonda y llamarlo instalación artística."),
                ),
                Dialogue(
                    "Hay que justificar la inversión en digitalización.",
                    "Anuncias un piloto de «dron rural» en una isla. Una. Con un dron "
                    "prestado y presentación en Fitur.",
                    ("Comprar 10.000 drones sin presupuesto.",
                     "Contratar un cartero con alas de cartón."),
                ),
                Dialogue(
                    "Tienes que viajar a Bruselas a presentar el plan y no hay presupuesto.",
                    "Viaje institucional «en misión logística»: en business, con un "
                    "paquete en la maleta como mercancía.",
                    ("Ir en autobús, en la fila del fondo.",
                     "Pedir prestado el Falcon y pedir perdón en el aire."),
                ),
            ),
        ),
        "presidencia_correos": DialoguePack(
            "🏛️ Presidencia de Correos. Regla de oro: **lo tuyo es la lealtad, no la "
            "contabilidad: contesta lo que le conviene a quien te puso.**",
            (
                Dialogue(
                    "La comisión del Congreso te pregunta por las pérdidas de la empresa.",
                    "Culpas a «la herencia recibida» de un presidente anterior que "
                    "nombró tu propio partido.",
                    ("Dar los números reales con pantallazos.",
                     "Llorar en el estrado hasta que te pasen un pañuelo."),
                ),
                Dialogue(
                    "La oposición dice que el voto por correo es «una amenaza»; el Gobierno, "
                    "que «un modelo de seguridad».",
                    "«Garantizamos el voto por correo al cien por cien, salvo el de quien "
                    "lo mandó tarde, que será culpa suya.»",
                    ("Admitir que se han perdido 3.000 sobres en Zaragoza.",
                     "Proponer votar por palomas mensajeras."),
                ),
                Dialogue(
                    "Un periodista pregunta por el sello conmemorativo con tu cara.",
                    "«Es un homenaje a la historia de Correos; si te sale torcido, vale "
                    "más.»",
                    ("Decir que no sabías nada y exigir que dimita el de marketing.",
                     "Pegártelo en la frente."),
                ),
                Dialogue(
                    "Un digital publica que tu cuñada cobra de Correos sin pisar la oficina.",
                    "«Es la máquina del fango: se acusa de enchufe a quien llega por mérito "
                    "(y por afinidad).»",
                    ("Admitirlo, y que además cobra el doble que tú.",
                     "Negar que tengas cuñada."),
                ),
                Dialogue(
                    "Moncloa te pide resistir ante la presión de la oposición.",
                    "Citas el «Manual de resistencia»: no dimitir, no explicar, no mirar "
                    "atrás. Y no miras atrás, que Correos va marcha atrás.",
                    ("Dimitir en rueda de prensa, con la voz temblorosa.",
                     "Pedir el manual por correo certificado, con acuse de recibo."),
                ),
                Dialogue(
                    "Los carteros convocan huelga por los sueldos.",
                    "«Respetamos el derecho de huelga; los servicios mínimos serán del "
                    "120 %.»",
                    ("Subirles el sueldo un 20 % esta misma tarde.",
                     "Mandar a tu cuñado a repartir con un sombrero de cartero."),
                ),
                Dialogue(
                    "La oposición pregunta por el contrato de las furgonetas a la empresa del "
                    "cuñado de un ministro.",
                    "«Fue un concurso abierto; la empresa del cuñado era de las pocas que "
                    "tenían furgonetas azules.»",
                    ("Admitir que lo adjudicaste a dedo desde un ático.",
                     "Pedir que te enseñen la furgoneta."),
                ),
                Dialogue(
                    "Aparecen 4.000 cartas sin entregar en una nave abandonada de Zaragoza.",
                    "«Es un archivo histórico de primer nivel»: lo inauguras con una "
                    "placa y un vino de honor.",
                    ("Entregarlas todas hoy, a tres cartas por hora.",
                     "Quemar la nave y el acta."),
                ),
                Dialogue(
                    "La comisión critica las dietas del consejo.",
                    "«Son gastos de representación en misión logística», y a los "
                    "comisionados los invitas a cenar.",
                    ("Devolver el dinero y pedir que te lo cobren con recargo.",
                     "Decir que es tu sobre de supervivencia."),
                ),
                Dialogue(
                    "Te preguntan si renunciarías al sueldo mientras la empresa pierda dinero.",
                    "«Mi sueldo es el 0,01 % del presupuesto; el problema es la "
                    "competencia, la digitalización y el contexto.»",
                    ("Decir que sí, y también al coche oficial.",
                     "Prometerlo a cambio de un sello con una dedicatoria."),
                ),
            ),
        ),
        "ley_rider": DialoguePack(
            "🏛️ Reunión en el Ministerio de Trabajo. Regla de oro: **tus riders no son "
            "trabajadores: son «colaboradores» y «emprendedores de sí mismos».**",
            (
                Dialogue(
                    "La vicepresidenta de Trabajo te pide que contrates a tus 12.000 riders.",
                    "«Con todo respeto, ministra: nuestros riders son emprendedores, y los "
                    "emprendedores no tienen jefe, tienen partners.» Y le regalas una "
                    "mochila con su nombre.",
                    ("«Vale, los contrato a todos.»",
                     "«Es que la ley no habla de mochilas.»"),
                ),
                Dialogue(
                    "En una tertulia, alguien te acusa de precarizar el empleo.",
                    "«Precarizar es no ofrecer trabajo; nosotros ofrecemos flexibilidad, "
                    "incluida la de elegir a qué hora llueve.»",
                    ("Admitir que sí, que da asco, y llorar en directo.",
                     "Cortar la conexión y decir que se oye mal."),
                ),
                Dialogue(
                    "Tu algoritmo desconecta a un rider por rechazar tres pedidos y el "
                    "sindicato va a juicio.",
                    "«No ha sido desconectado: ha elegido descansar de forma permanente.»",
                    ("Readmitirlo ya y pedirle perdón por escrito.",
                     "Decir que el algoritmo tiene vida propia y que tú no mandas."),
                ),
                Dialogue(
                    "Un juez dicta que tus riders son falsos autónomos.",
                    "Recurres y, mientras tanto, lo llamas «sentencia de transición hacia "
                    "el modelo de colaboración de nueva generación».",
                    ("Acatar la sentencia el mismo día, con una nota de prensa.",
                     "Cerrar la empresa y mudarte a Dubái (otro más)."),
                ),
                Dialogue(
                    "La Inspección de Trabajo presenta 4.000 actas por falsos autónomos.",
                    "Pagas las multas de golpe, que cuestan menos que un año de cuotas, y "
                    "las llamas «inversión en diálogo social».",
                    ("Contratar a los 4.000 y convertirte en una empresa seria.",
                     "Romper las actas en un acto público, con fotógrafo."),
                ),
                Dialogue(
                    "Bruselas aprueba una directiva que presume la laboralidad de los "
                    "riders.",
                    "«Es una directiva de mínimos; España la transpondrá con calma, y "
                    "mientras tanto pasamos a un modelo de colaboradores asociados.»",
                    ("Cumplirla antes de que acabe el plazo.",
                     "Dar las gracias a Bruselas en cinco idiomas."),
                ),
                Dialogue(
                    "Tu rival contrata a sus riders con contrato y presume de ello en la tele.",
                    "«Es una operación de marketing que no sobrevivirá a su primer "
                    "trimestre», y le mandas una cesta de bienvenida a la quiebra.",
                    ("Copiarle y contratar a todos con contrato.",
                     "Comprarle la empresa con un cheque sin fondos."),
                ),
                Dialogue(
                    "Un rider te pregunta por qué se le paga por pedido y no por hora.",
                    "«Porque es mucho más motivador cobrar por lo que haces y no por lo que "
                    "tardas.»",
                    ("«Porque nos sale más barato.»",
                     "Darle un abrazo y una bonificación de 200 Y$."),
                ),
                Dialogue(
                    "Un periodista pregunta cuántos riders figuran en nómina.",
                    "«Hoy somos cero en nómina y 12.000 en colaboración: el mayor equipo de "
                    "España sin sueldo fijo.»",
                    ("«Todos.»",
                     "«Eso es secreto de Estado.»"),
                ),
                Dialogue(
                    "Montas una campaña «Defiende tu flexibilidad» con riders de verdad.",
                    "Les pagas el pedido de la rueda de prensa y les das un chaleco con el "
                    "eslogan «Yo elijo».",
                    ("Dejarles hablar libremente.",
                     "Reunirlos en un sótano sin cobertura."),
                ),
            ),
        ),
        "cohete": DialoguePack(
            "🚀 Entrevista en tu hangar. Regla de oro: **el cohete no es un capricho: es «el "
            "futuro de la humanidad» (y se paga con dinero público).**",
            (
                Dialogue(
                    "Un periodista pregunta por qué cobras 40.000.000 Y$ por un viaje de once "
                    "minutos.",
                    "«No es turismo: es la democratización del espacio para quien ya tiene "
                    "todo lo demás.»",
                    ("«Porque me da la gana.»",
                     "«Porque la gasolina cuesta lo que cuesta.»"),
                ),
                Dialogue(
                    "Trump impone aranceles del 25 % a las piezas de tu cohete.",
                    "Felicitas a Trump por su valiente defensa del producto nacional y "
                    "fabricas la tuerca en Texas, en el garaje de un amigo.",
                    ("Criticarle en directo y que te cancele el lanzamiento.",
                     "Pagar el arancel y callarte, con la cabeza gacha."),
                ),
                Dialogue(
                    "Musk tuitea que tu cohete «es un juguete».",
                    "Contestas con cuatro palabras y un emoji de cohete: los dos subís en "
                    "bolsa.",
                    ("Responder con un hilo de cuarenta tuits llorando.",
                     "Retarle a una carrera a la Luna, a pie."),
                ),
                Dialogue(
                    "Los sindicatos piden convenio para los técnicos del cohete.",
                    "«Convenio no: cultura de la excelencia, con mesa de pimpón y barritas "
                    "de proteína gratis.»",
                    ("Firmarlo y subirles el sueldo.",
                     "Mandarlos en el primer cohete, sin billete de vuelta."),
                ),
                Dialogue(
                    "Hacienda pregunta dónde tributa tu patrimonio.",
                    "«En una fundación en Luxemburgo, una sociedad en Delaware y una "
                    "gasolinera en la órbita baja, donde aún no hay Hacienda.»",
                    ("Pagar todo en España con una sonrisa.",
                     "Alegar que los impuestos los paga el cohete."),
                ),
                Dialogue(
                    "Un cliente se queja: su paquete ha llegado antes a la Luna que a "
                    "Lanzarote.",
                    "«Es un servicio premium: a la Luna, dos días; a Lanzarote hay que "
                    "esperar a que el ferry se alinee con Mercurio.»",
                    ("Pedir disculpas y mandarle otro paquete.",
                     "Mandar un cohete a Lanzarote a por el paquete."),
                ),
                Dialogue(
                    "Los ecologistas dicen que tu cohete contamina como 10.000 coches.",
                    "«Es la inversión más verde: vamos a salvar a la humanidad desde "
                    "fuera de la Tierra.»",
                    ("Reconocer que contamina y cerrarlo.",
                     "Plantar un árbol dentro del cohete."),
                ),
                Dialogue(
                    "El Gobierno te ofrece 200 millones de subvención por «innovación "
                    "aeroespacial».",
                    "La aceptas y la anuncias como «inversión privada con acompañamiento "
                    "público».",
                    ("Rechazarla por principios.",
                     "Devolverla con un lazo y una nota de agradecimiento al contribuyente."),
                ),
                Dialogue(
                    "Tus antiguos riders se manifiestan bajo el hangar.",
                    "Les ofreces un viaje al espacio, a 40.000.000 Y$, «con un descuento de "
                    "empleado del 2 %».",
                    ("Bajar a hablar con ellos con una pancarta.",
                     "Echarles el chorro de combustible del cohete."),
                ),
                Dialogue(
                    "El lanzamiento se ha aplazado por sexta vez.",
                    "«Estamos haciendo ajustes finos: 2030 será el año del cohete.»",
                    ("Decir que no funciona y pedir perdón.",
                     "Cambiar la fecha al 30 de febrero."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: las ramas del funcionariado (Alta Administración y Puerta giratoria) -----------

MEMORY_PACKS.update(
    {
        "expediente": MemoryPack(
            "📁 El expediente {n} tiene que pasar por todos los despachos, uno por uno:",
            "Recorre el circuito en el mismo orden. Si te saltas un trámite, el expediente "
            "vuelve a empezar (y ya tiene dos años).",
            (
                ("📥", "Registro"),
                ("🧮", "Intervención"),
                ("⚖️", "Abogacía del Estado"),
                ("🏛️", "Consejo de Estado"),
                ("💶", "Hacienda"),
                ("✍️", "La firma del ministro"),
                ("📰", "El BOE"),
                ("🗄️", "Secretaría General Técnica"),
                ("🚬", "El pasillo, esperando el visto bueno"),
            ),
        ),
        "sandwich": MemoryPack(
            "🧾 La multinacional factura {n} mil millones en España. Hay que sacar el dinero "
            "por esta ruta:",
            "Pasa el dinero por los mismos países en el mismo orden, que cada escala le quita "
            "un impuesto.",
            (
                ("🇮🇪", "Irlanda"),
                ("🇳🇱", "Países Bajos"),
                ("🇧🇲", "Bermudas"),
                ("🇱🇺", "Luxemburgo"),
                ("🇺🇸", "Delaware"),
                ("🇨🇾", "Chipre"),
                ("🇲🇹", "Malta"),
                ("🇵🇦", "Panamá"),
                ("🇬🇮", "Gibraltar"),
            ),
        ),
    }
)  # fmt: skip

SORT_PACKS.update(
    {
        "boe": SortPack(
            "📰 Eres subdirector general: que el BOE salga bien (o que salga) depende de ti. "
            "Cada disposición, a su sección.",
            "📜 Disposición {n}/{total}:",
            (
                "📜 I. Disposiciones generales",
                "👤 II. Autoridades y personal",
                "📄 III. Otras disposiciones",
                "📢 V. Anuncios",
            ),
            (
                ("Un real decreto-ley de medidas urgentes con seis temas que no se conocen", 0),
                ("La ley de presupuestos: 1.200 páginas y una enmienda que nadie ha visto", 0),
                ("Un real decreto que regula cómo se regulan los reales decretos", 0),
                ("La ley que declara «de interés general» un domingo de cada mes", 0),
                ("El nombramiento de tu cuñado como asesor, con rango de director general", 1),
                ("El cese, «a petición propia», de un director general que no lo había pedido", 1),
                ("La convocatoria de oposiciones a 12 plazas de ordenanza y 30.000 aspirantes", 1),
                ("El nombramiento de un subsecretario, con foto de la toma de posesión", 1),
                ("Una subvención de 3 millones a una fundación del partido «de interés»", 2),
                ("Un convenio con una universidad para estudiar el propio ministerio", 2),
                ("Una resolución que da publicidad al acuerdo de otra resolución anterior", 2),
                ("La medalla al mérito civil para un exministro que acaba de dimitir", 2),
                ("La licitación de 4 millones para reformar el despacho del director general", 3),
                ("El edicto que notifica una multa de tráfico a alguien al que nadie encuentra", 3),
                ("El anuncio de subasta de un edificio que el Estado no sabía que tenía", 3),
                ("La convocatoria de la junta de una sociedad estatal, a las 8:00 del 24 de "
                 "diciembre", 3),
            ),
        ),
        "patrimonio": SortPack(
            "💼 Eres asesor fiscal de grandes fortunas: cada bien de la fortuna, a su "
            "escondite legal (que ilegal sería de pobres).",
            "💰 Bien {n}/{total}:",
            (
                "🏦 SICAV",
                "🇦🇩 Andorra",
                "🎨 Arte (exento de Patrimonio)",
                "🏛️ Fundación",
            ),
            (
                ("Una cartera de bolsa de 40 millones que solo tributa al 1 %", 0),
                ("Unas acciones que cobran dividendos y no quieren que se note", 0),
                ("Un fondo de inversión con cien socios, de los cuales conoces a dos", 0),
                ("Una cartera tan grande que necesita un gestor con corbata y sin escrúpulos", 0),
                ("La residencia fiscal del cliente, que vive en un hotel de Madrid", 1),
                ("Una cuenta con un millón en efectivo y un recibo de la luz de un piso vacío", 1),
                ("El futbolista que quiere cobrar el 10 % en vez del 47 %", 1),
                ("Un empadronamiento de una casa en la que solo hay una nevera", 1),
                ("Un cuadro de un pintor que nadie conocía hace una semana", 2),
                ("Una escultura que parece un montón de chatarra y vale 2 millones", 2),
                ("Una colección de vinos que nadie piensa beber", 2),
                ("Un Picasso colgado de la pared del baño (por si viene Hacienda)", 2),
                ("La herencia de un abuelo que no quiere pagar Sucesiones", 3),
                ("Un palacete que quiere ser «fundación cultural» y casa familiar", 3),
                ("El yate familiar, convertido en «aula flotante de ciencias del mar»", 3),
                ("El coto de caza que ahora es «centro de estudios cinegéticos»", 3),
            ),
        ),
    }
)  # fmt: skip

#: Disposiciones coladas en una ley que no tiene nada que ver (el colado de `colada`).
COLADAS: tuple[str, ...] = (
    "Ley de pesca de bajura, disposición adicional séptima: se indulta a un exalcalde "
    "condenado por prevaricación",
    "Norma de etiquetado de yogures, disposición final tercera: los aforados seguirán siendo "
    "aforados, y los que no, también",
    "Decreto de ayudas al olivar, art. 14: el Gobierno podrá subir las dietas de los altos "
    "cargos «por circunstancias»",
    "Ley del deporte escolar, disposición adicional cuarta: la sociedad estatal de suelo "
    "cederá un solar al partido que gobierne",
    "Reglamento de ascensores, art. 3: el modificado de una obra pública podrá ser del 300 %",
    "Ley de protección de las abejas, disposición transitoria segunda: la puerta giratoria "
    "no será incompatibilidad durante el verano",
    "Decreto de turismo rural, disposición final primera: el Estado asume la deuda de una "
    "autopista de peaje",
    "Ley de bienestar animal, disposición adicional décima: los ministros tendrán tres años "
    "más para presentar la declaración de bienes",
    "Norma de envases reutilizables, art. 22: la fundación del presidente queda exenta de "
    "todo, con efectos retroactivos",
    "Ley de eficiencia energética de edificios, disposición adicional quinta: las cenas de "
    "partido tributan al 4 % de IVA",
    "Orden del calendario de caza, disposición derogatoria única: queda derogado el artículo "
    "de la ley anticorrupción que molestaba",
)
#: Disposiciones de verdad: cada una encaja con la ley en la que está.
LEYES_LIMPIAS: tuple[str, ...] = (
    "Ley de pesca de bajura, art. 5: los barcos de menos de doce metros faenarán con "
    "licencia de la cofradía",
    "Ley de pesca de bajura, disposición transitoria primera: las licencias vigentes lo "
    "seguirán siendo hasta su vencimiento",
    "Norma de etiquetado de yogures, art. 8: la fecha de caducidad se imprimirá en la tapa, "
    "en letra legible",
    "Norma de etiquetado de yogures, disposición adicional segunda: el Gobierno publicará una "
    "guía en seis meses",
    "Decreto de ayudas al olivar, art. 3: cada hectárea tendrá derecho a una ayuda de 9.000 Y$",
    "Decreto de ayudas al olivar, disposición final primera: entrará en vigor al día siguiente "
    "de su publicación",
    "Ley del deporte escolar, art. 9: todos los colegios ofrecerán dos horas de educación física",
    "Ley del deporte escolar, disposición final segunda: las comunidades podrán desarrollar "
    "esta ley",
    "Reglamento de ascensores, art. 11: se revisarán cada dos años por una empresa autorizada",
    "Reglamento de ascensores, disposición adicional primera: los ascensores sin certificado "
    "se precintarán",
    "Ley de protección de las abejas, art. 6: se prohíben los pesticidas en la época de floración",
    "Decreto de turismo rural, art. 2: una casa rural podrá alojar hasta quince personas",
    "Ley de bienestar animal, art. 19: los criadores llevarán un registro de cada camada",
    "Norma de envases reutilizables, disposición adicional tercera: los bares ofrecerán agua "
    "del grifo",
    "Ley de eficiencia energética de edificios, art. 7: las calderas se revisarán cada cuatro años",
    "Orden del calendario de caza, art. 1: la veda comenzará el primer domingo de febrero",
    "Ley de transparencia, art. 4: los altos cargos publicarán su agenda de reuniones",
    "Ley de incompatibilidades, disposición adicional primera: la puerta giratoria requerirá "
    "informe previo",
    "Ley de indultos, art. 2: el indulto se concederá por real decreto, previa deliberación "
    "del Consejo de Ministros",
)

#: Los inversores de verdad de la SICAV (los demás socios son mariachis).
SICAV_REAL: tuple[str, ...] = (
    "Pregunta cada semana por la rentabilidad y se lee el informe del gestor",
    "Ha puesto 8 millones de su bolsillo y los ha visto salir de su cuenta",
    "Discute la comisión de gestión con el gerente, y la gana",
    "Cobra los dividendos y sabe cuánto son, al céntimo",
    "Sabe cómo se llama la sociedad en la que invierte, y cómo se llama su gestor",
    "Viene a la junta con la carpeta subrayada y una calculadora",
    "Compara la cartera con el índice de bolsa y no le gusta lo que ve",
    "Pide que las cuentas le lleguen a casa, y su casa existe",
    "Quiere vender si baja un 5 % y lo dice en voz alta",
    "Firma con su propia letra, después de leerse el contrato entero",
)
#: Los mariachis: testaferros que hacen falta para llegar a los cien socios.
SICAV_MARIACHIS: tuple[str, ...] = (
    "Jubilada de Albacete que cree que ha firmado para el viaje del Imserso",
    "El chófer del dueño, que firma donde le dicen sin preguntar",
    "Estudiante que ha cobrado 5.000 Y$ por «hacer un favor a un amigo»",
    "Un señor cuyo único patrimonio conocido es una Vespa",
    "La cuñada del gestor, que está en cuarenta sociedades y ninguna le suena",
    "El portero del edificio del despacho, con el DNI en la mano",
    "Un jardinero con 12 millones a su nombre y una bicicleta prestada",
    "El yerno del socio fundador, que acaba de heredar un cargo",
    "Un primo del pueblo que cree que SICAV es una marca de cerveza",
    "Una señora que ha firmado un papel en el bar de la esquina, con un chupito de por medio",
    "El becario del despacho, que figura como accionista y como camarero de la fiesta",
    "Un empleado de la lavandería que vota en la junta por videollamada desde el turno",
    "Un jubilado al que han prometido una cesta de Navidad por firmar",
)

DIALOGUE_PACKS.update(
    {
        "subsecretarios": DialoguePack(
            "🗓️ Comisión General de Secretarios de Estado y Subsecretarios, lunes a las 9:00. "
            "Regla de oro: **todo se pacta el lunes para que el martes parezca que nadie ha "
            "mandado nada.**",
            (
                Dialogue(
                    "Un ministerio trae al Consejo de Ministros un decreto del que Hacienda no "
                    "sabe nada.",
                    "Se aprueba «sin perjuicio de lo que opine Hacienda», que lo leerá en el "
                    "BOE y tendrá tiempo de quejarse en privado.",
                    ("Pedir que lo hable con Hacienda antes y que vuelva dentro de seis meses.",
                     "Votarlo en contra, por si acaso."),
                ),
                Dialogue(
                    "Un subsecretario rival propone que todas las competencias pasen a su "
                    "ministerio.",
                    "Aplaudes su «visión de Estado» y propones una comisión de estudio que no "
                    "se reúna jamás.",
                    ("Decirle que no, delante de todos.",
                     "Proponer que se pasen también las tuyas, a ver si cuela."),
                ),
                Dialogue(
                    "El orden del día se filtra a la prensa antes de empezar.",
                    "Culpas a «la dinámica comunicativa» y filtras tú el acta de la semana que "
                    "viene, para equilibrar.",
                    ("Abrir una investigación interna con nombres y apellidos.",
                     "Desmentirlo con un comunicado de cuatro páginas."),
                ),
                Dialogue(
                    "Hay que elegir qué se lleva al Consejo de Ministros del martes: lo bueno o "
                    "lo polémico.",
                    "Lo polémico, escondido en un decreto con otros doce temas que nadie quiere "
                    "leer.",
                    ("Solo lo bueno, que es lo honesto.",
                     "Echarlo a cara o cruz con una moneda."),
                ),
                Dialogue(
                    "El secretario de Estado de Presupuestos pregunta quién paga todo esto.",
                    "«Se financiará con cargo a los créditos existentes»: ya lo pagará el "
                    "siguiente Gobierno.",
                    ("Decirle la cifra de verdad.",
                     "«A mí no me mire, soy de la Comisión»."),
                ),
                Dialogue(
                    "Un asesor te avisa de un error en el borrador que el ministro ya ha "
                    "firmado.",
                    "Lo conviertes en «errata menor subsanable» y lo corriges por real decreto "
                    "el mes que viene.",
                    ("Parar el Consejo de Ministros para corregirlo.",
                     "Tirar el borrador a la papelera y fingir que nunca existió."),
                ),
                Dialogue(
                    "El ministro pregunta qué ha decidido la Comisión sobre su ley favorita.",
                    "«Se ha tomado en consideración con enorme interés»: nadie ha dicho nada y "
                    "nadie la ha tocado.",
                    ("«Que es una ley mala, ministro».",
                     "Leerle el acta, que dura cuatro horas."),
                ),
                Dialogue(
                    "Moncloa llama: hay un decreto que tiene que salir esta semana sí o sí.",
                    "Se aprueba por vía de urgencia, sin leerlo, con un acta que diga que "
                    "se leyó.",
                    ("Pedir los informes preceptivos, que tardan tres meses.",
                     "Plantarte con una pancarta de «Esto es ilegal»."),
                ),
                Dialogue(
                    "Un periodista pregunta por qué las reuniones son los lunes y a puerta "
                    "cerrada.",
                    "«Porque los lunes son el día de las ideas». Y la puerta, por la corriente.",
                    ("«Porque aquí se decide todo antes de que se entere el Consejo de "
                     "Ministros».",
                     "Invitarle a la próxima, con tortilla incluida."),
                ),
                Dialogue(
                    "Dos subsecretarios empatan por una competencia.",
                    "La competencia se parte en dos: la mitad que no importa para él y la que "
                    "importa para ti.",
                    ("Echarlo a suertes.",
                     "Pedir al ministro que decida, que para eso cobra."),
                ),
                Dialogue(
                    "A las 9:30 piden café y no queda ni uno en la sala.",
                    "Mandas al jefe de gabinete: «Hazme café… ¡Quiero café!». Y que lo traiga "
                    "en vaso de papel con el escudo.",
                    ("Ir tú a por él, con humildad.",
                     "Suspender la Comisión hasta que llegue el catering."),
                ),
            ),
        ),
        "consejo_estado": DialoguePack(
            "🏛️ Pleno del Consejo de Estado, en la calle Mayor. Regla de oro: **dictamina sin "
            "molestar a nadie: el dictamen no es vinculante, y por eso nadie lo lee.**",
            (
                Dialogue(
                    "El Gobierno pide un dictamen sobre un decreto que ya ha aprobado.",
                    "Dictaminas favorablemente «con las observaciones que se estimen "
                    "oportunas» (ninguna).",
                    ("Decir que llega tarde y que es ilegal.",
                     "Dictaminar en contra, con tres votos particulares y un poema."),
                ),
                Dialogue(
                    "Un expresidente del Gobierno, consejero nato, llega tarde y pregunta de "
                    "qué va el asunto.",
                    "Le resumes el expediente en dos frases que no comprometan a nadie y le "
                    "das la razón al final.",
                    ("Contarle los 400 folios.",
                     "Decirle que se siente y se calle."),
                ),
                Dialogue(
                    "Hay que dictaminar sobre la reforma de un estatuto autonómico.",
                    "«Procede, en los términos expuestos, siempre que no se modifique nada de "
                    "lo que se modifica».",
                    ("Decir que es inconstitucional y salir a gritarlo a la calle.",
                     "Pedir un año más para estudiarlo en un balneario."),
                ),
                Dialogue(
                    "La prensa pregunta si el dictamen vincula al Gobierno.",
                    "«Es una consulta de enorme valor jurídico» (traducción: haced lo que "
                    "queráis).",
                    ("«Sí, y por eso lo ignoran siempre».",
                     "«Es un secreto de Estado»."),
                ),
                Dialogue(
                    "El presidente del Consejo te pide un voto particular que no dé problemas "
                    "al Gobierno.",
                    "Redactas un voto particular de 30 páginas sobre una coma. Todos felices.",
                    ("Votar según tu conciencia, aunque dé problemas.",
                     "No votar y quedarte en la cafetería."),
                ),
                Dialogue(
                    "Un consejero propone dictaminar contra el Gobierno por una vez en la "
                    "vida.",
                    "Se aplaude su valentía y se aprueba aplazarlo «para mayor profundidad de "
                    "estudio».",
                    ("Apoyarle y votar en contra del Gobierno.",
                     "Reírte en su cara en pleno."),
                ),
                Dialogue(
                    "Llega a consulta un tratado internacional que nadie ha leído.",
                    "Dictaminas que «no contradice la Constitución», sin precisar de cuál.",
                    ("Leerlo entero.",
                     "Pedir a un becario que lo lea y que dictamine él."),
                ),
                Dialogue(
                    "El Consejo cumple años y te piden un discurso de homenaje a la propia "
                    "institución.",
                    "Hablas veinte minutos de la «imprescindible función consultiva» sin que "
                    "se oiga ni una vez la palabra «inútil».",
                    ("Reconocer que casi nadie les hace caso.",
                     "Pedir una subida de sueldo en mitad del discurso."),
                ),
                Dialogue(
                    "Un periodista pregunta cuánto cuesta el Consejo de Estado al año.",
                    "«Es una inversión en seguridad jurídica», sin cifras y con una sonrisa.",
                    ("Decirle la cifra, con los coches oficiales incluidos.",
                     "«Pregunte en Hacienda»."),
                ),
                Dialogue(
                    "El Gobierno ignora tu dictamen y hace justo lo contrario.",
                    "Te alegras: «Prueba de que nuestra independencia es total». Y archivas la "
                    "copia con cariño.",
                    ("Dimitir del Consejo, indignado.",
                     "Mandar una carta de protesta de 40 folios."),
                ),
            ),
        ),
        "big_four": DialoguePack(
            "🏢 Tu despacho con vistas a la Castellana. Regla de oro: **todo es legal hasta "
            "que un juez diga lo contrario (y eso tarda diez años).**",
            (
                Dialogue(
                    "Un cliente quiere pagar menos Impuesto de Sociedades.",
                    "Le montas una estructura «de eficiencia fiscal» con diez filiales: legal "
                    "hasta que un juez diga otra cosa.",
                    ("Decirle que pague lo que toca, que para eso hay ley.",
                     "Quemar el balance y fingir pérdidas en una hoguera."),
                ),
                Dialogue(
                    "Auditas a una empresa a la que también asesoras. Te preguntan por el "
                    "conflicto de intereses.",
                    "«Tenemos murallas chinas entre departamentos»: son de papel, pero muy "
                    "altas.",
                    ("Renunciar a una de las dos y perder la mitad de los honorarios.",
                     "Reconocer que es evidente."),
                ),
                Dialogue(
                    "Una cotizada tiene un agujero de 400 millones.",
                    "Lo cuentas como «partida extraordinaria de carácter no recurrente» y "
                    "firmas la auditoría limpia.",
                    ("Avisar al supervisor.",
                     "Llamar al periódico y darles el balance."),
                ),
                Dialogue(
                    "Hacienda pide los papeles de trabajo de un cliente.",
                    "Los entregas completos… en 40.000 folios impresos a doble cara, sin "
                    "índice.",
                    ("Negarte y esconderte en el baño.",
                     "Dárselos en un pen drive con la contraseña «1234»."),
                ),
                Dialogue(
                    "Un cliente exige que su nombre no aparezca en el informe.",
                    "Le llamas «el Cliente» durante 300 páginas y la factura va a una sociedad "
                    "de Panamá.",
                    ("Poner su nombre en la portada, con letras grandes.",
                     "Quitarle del informe y también de la factura."),
                ),
                Dialogue(
                    "Un exministro quiere entrar como socio senior.",
                    "Le abres la puerta giratoria de la sede con alfombra roja y plaza de "
                    "garaje.",
                    ("Preguntar por el periodo de incompatibilidad.",
                     "Decirle que ya hay demasiados exministros y que espere a que salga uno."),
                ),
                Dialogue(
                    "Un fondo pide una valoración altísima de su empresa antes de venderla.",
                    "Pones un múltiplo «razonable» y una nota al pie de 200 palabras que nadie "
                    "leerá.",
                    ("Valorarla por lo que vale, que no es lo que quiere.",
                     "Valorarla en un millón y pedir el 1 % de comisión."),
                ),
                Dialogue(
                    "El socio director pide un 20 % más de ingresos este año.",
                    "Vendes a cada cliente «una revisión» de lo que ya hiciste, con nombre en "
                    "inglés.",
                    ("Bajar precios por humildad.",
                     "Despedir a los becarios, que trabajan gratis."),
                ),
                Dialogue(
                    "Un periodista pregunta por una quiebra que auditasteis.",
                    "«Cumplimos escrupulosamente las normas vigentes en el momento de la "
                    "firma». Vale para todas.",
                    ("Reconocer que visteis el problema y no lo dijisteis.",
                     "Negar que esa empresa haya existido nunca."),
                ),
                Dialogue(
                    "Llega la campaña de la Renta y todos tus clientes quieren pagar menos.",
                    "A cada uno su plan: seguros, SICAV, fundaciones y un «convenio con "
                    "Andorra» de regalo.",
                    ("Decirles que paguen lo que sale, que es un deber ciudadano.",
                     "Recomendarles esconder el dinero bajo el colchón."),
                ),
            ),
        ),
        "andorra": DialoguePack(
            "🏔️ Tu despacho en Escaldes. Regla de oro: **tu cliente vive en Andorra: los "
            "papeles lo dicen, y los vecinos de Chamberí no se enteran.**",
            (
                Dialogue(
                    "Un youtuber con cinco millones de seguidores quiere tributar en Andorra "
                    "sin dejar Madrid.",
                    "Alquilas un piso a su nombre, con padrón, factura de la luz y un cepillo "
                    "de dientes de atrezo.",
                    ("Decirle que, si vive en Madrid, tributa en Madrid.",
                     "Aconsejarle que se mude de verdad, con la perra y todo."),
                ),
                Dialogue(
                    "Hacienda pregunta por qué la casa del padrón está vacía.",
                    "«Es un cliente muy minimalista»: y adjuntas una factura del agua de hace "
                    "dos años.",
                    ("Confesar que no ha pisado el piso en tres años.",
                     "Cambiar el domicilio al de tu cuñada en Soria."),
                ),
                Dialogue(
                    "Un cliente quiere explicar 20 millones en Andorra sin que Hacienda "
                    "pregunte.",
                    "«Es la herencia del avi Florenci»: toda familia con ahorros en el "
                    "extranjero tiene un abuelo con mucha visión.",
                    ("Decir que salen de un negocio de cerveza artesanal.",
                     "Decir que no sabe de dónde han salido."),
                ),
                Dialogue(
                    "El banco andorrano te pide justificar el origen de los fondos.",
                    "Entregas una carta con membrete bonito: «fondos de origen lícito», con "
                    "firma ilegible.",
                    ("Contar la verdad entera al departamento de cumplimiento.",
                     "Cambiar de banco cada semana."),
                ),
                Dialogue(
                    "Llega una carta de la Agencia Tributaria: «Acredite su residencia».",
                    "La contestas con un certificado de residencia, catorce recibos de la "
                    "compañía telefónica del Principado y una foto en la nieve (de 2019).",
                    ("Ignorarla: si no la abres, no existe.",
                     "Contestar que vive en la Luna y que no cotiza."),
                ),
                Dialogue(
                    "Un futbolista de primera quiere pagar un 10 % en vez de un 47 %.",
                    "Domicilias sus derechos de imagen en una sociedad del Principado, «por "
                    "razones estrictamente deportivas».",
                    ("Recomendarle que declare todo en España.",
                     "Venderle una parcela en la Luna."),
                ),
                Dialogue(
                    "Un cliente pregunta cuántos días puede pasar en España sin ser residente.",
                    "183, repartidos con mucho tino y con fotos en la nieve para demostrar "
                    "dónde estaba cada uno.",
                    ("Todos los que quiera, que la ley es de buen rollo.",
                     "Ninguno: que se compre una cabaña y viva en el bosque."),
                ),
                Dialogue(
                    "Un tertuliano te acusa en directo de ayudar a defraudar.",
                    "«Solo aconsejo conforme a la legislación vigente en el Principado». Y "
                    "mandas la factura por la entrevista.",
                    ("Pedir disculpas en directo.",
                     "Esconderte detrás del atril."),
                ),
                Dialogue(
                    "Un influencer que dice vivir en Andorra sale en directo con la Gran Vía "
                    "de fondo.",
                    "Le recomiendas un fondo desenfocado, una nota «grabado en 2019» y un "
                    "croma de montaña.",
                    ("Pedirle que borre el directo y ya está.",
                     "Cambiar de asesor, a ver si cuela."),
                ),
                Dialogue(
                    "Un cliente aparece con un millón en efectivo en una bolsa de deporte.",
                    "Se la guardas en una caja de seguridad hasta que sea «fruto de la venta "
                    "de unos cuadros».",
                    ("Aceptar la bolsa y darle un recibo en una servilleta.",
                     "Llamar a la policía, que es lo suyo."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: las ramas del influencer (Tele y Andorra) -------------------------------------

MEMORY_PACKS.update(
    {
        "escaleta": MemoryPack(
            "📺 Magacín de hoy (edición {n}). El director te dicta la escaleta a gritos por el "
            "pinganillo:",
            "Cumple la escaleta en el mismo orden. Si te saltas un bloque, el programa se "
            "alarga hasta la medianoche (y no hay presupuesto para eso).",
            (
                ("🍳", "La receta de la abuela"),
                ("🔮", "El horóscopo"),
                ("⛈️", "La conexión con el reportero bajo la lluvia"),
                ("🚨", "Sucesos"),
                ("📞", "El concurso de llamadas"),
                ("🛋️", "La entrevista al famoso"),
                ("🧴", "El publirreportaje (que parece una noticia)"),
                ("💌", "El consultorio sentimental"),
                ("🐶", "El perrito viral"),
                ("🚗", "El tráfico y el tiempo"),
            ),
        ),
        "subathon": MemoryPack(
            "🔴 Subathon de 24 horas, hora {n}. Las alertas saltan en este orden y tú sonríes a "
            "todas:",
            "Reacciona a cada alerta en el mismo orden (y que no se note que llevas veinte "
            "horas despierto).",
            (
                ("💸", "La donación de 500 Y$"),
                ("💎", "Los bits"),
                ("🎁", "La suscripción regalada"),
                ("🚀", "El raid"),
                ("🤖", "El TTS que lee lo que no debe"),
                ("🏆", "Un seguidor nuevo"),
                ("🔥", "La donación troll"),
                ("👑", "El suscriptor de 36 meses"),
                ("📢", "El aviso del moderador"),
            ),
        ),
    }
)  # fmt: skip

SORT_PACKS.update(
    {
        "invitados": SortPack(
            "🎬 Eres presentador de prime time: cada invitado a su bloque, que el programa "
            "dura cuatro horas y la gente tiene que llegar al final.",
            "🎟️ Invitado {n}/{total}:",
            (
                "🛋️ Sofá",
                "🎤 Actuación",
                "🧨 Polémica",
                "📞 Conexión en directo",
            ),
            (
                ("Una actriz que promociona una serie que nadie ha visto, con anécdota de "
                 "rodaje ensayada", 0),
                ("Un escritor con su libro en la mano (lo ha escrito su nieto)", 0),
                ("Una chef que trae una tarta y bromea con que «no se ha quemado»", 0),
                ("El exfutbolista que cuenta por vigésima vez cómo fue aquel gol del 98", 0),
                ("Un cantante que viene a hacer playback de su nuevo sencillo", 1),
                ("Un mago que saca un conejo (el mismo desde 2008)", 1),
                ("Un coro de niños que canta villancicos en abril", 1),
                ("Un humorista con seis minutos sobre su suegra", 1),
                ("La ex del concursante, con un sobre cerrado y un abogado", 2),
                ("Un concejal que defiende lo indefendible y se acalora al minuto", 2),
                ("El primo del famoso, que «lo sabe todo y va a contarlo»", 2),
                ("Dos tertulianos que ya se han insultado en tres programas distintos", 2),
                ("Un reportero bajo la lluvia, delante de una puerta cerrada", 3),
                ("Una corresponsal en un pueblo donde «no pasa nada, pero pasa mucho»", 3),
                ("Un enviado especial a un aeropuerto con 400 vuelos retrasados", 3),
                ("Un reportero a las puertas de un juzgado, esperando a que salga alguien", 3),
            ),
        ),
        "marca": SortPack(
            "🥤 Eres dueño de una bebida energética: cada idea, a su cajón (y que ninguna "
            "acabe en el cajón de «Demanda»).",
            "💡 Idea {n}/{total}:",
            (
                "🥤 Sabor nuevo",
                "👕 Merch",
                "🤝 Colaboración",
                "⚖️ Demanda",
            ),
            (
                ("Edición limitada «Fresa tóxica»", 0),
                ("Un sabor «Lunes por la mañana»: ácido y con 300 mg de cafeína", 0),
                ("Una mezcla de sandía, wasabi y «ganas de vivir»", 0),
                ("La versión sin azúcar con un nombre en inglés que nadie sabe pronunciar", 0),
                ("Una sudadera con el logo de la lata, a 8.000 Y$ y una sola talla", 1),
                ("Una gorra con la lata bordada que no se puede lavar", 1),
                ("Una lata de coleccionista que no se puede abrir", 1),
                ("Unos calcetines con el logo (porque los fans los piden)", 1),
                ("Un youtuber que cobra 5.000.000 Y$ por beberse una lata en directo", 2),
                ("Un futbolista que sale en el anuncio sosteniendo la lata al revés", 2),
                ("Un cantante con su «edición firmada» (la ha firmado su manager)", 2),
                ("Un festival que pone la lata en cada barra, junto a la cerveza", 2),
                ("Una madre que denuncia que la lata ponía «energía» y su hijo no durmió en "
                 "tres días", 3),
                ("Una marca de latas que se parece sospechosamente a la tuya", 3),
                ("Consumo, que pide quitar «sin efectos secundarios» del anuncio", 3),
                ("Un proveedor que dice que le debes tres contenedores de jarabe", 3),
            ),
        ),
    }
)  # fmt: skip

#: Director de cadena: programas que hunden la audiencia (el distinto de la parrilla).
SHARE_SINKS: tuple[str, ...] = (
    "Documental de tres horas sobre el pegamento, el sábado en prime time · 2,1 %",
    "Debate de nueve invitados sobre el IVA de la carne, a la hora de la cena · 3,4 %",
    "Reposición del especial de Navidad, emitida en agosto · 2,8 %",
    "Concurso de preguntas… en latín · 1,9 %",
    "El telediario de ayer, repetido «por si alguien se lo perdió» · 3,0 %",
    "Una película muda de 1927 con una hora de créditos, a las 22:00 · 2,6 %",
    "Manualidades con papel de periódico, a las diez menos veinte de la noche · 3,2 %",
    "Un debate electoral emitido a la vez que el partido de la selección",
    "Programa de cocina sin cocina: solo se habla de recetas y se comenta el olor · 2,2 %",
    "Un reality con los guionistas de la cadena encerrados en una casa · 3,1 %",
    "Telenovela de 400 capítulos que empieza por el 212 · 3,5 %",
    "La gala de aniversario de la cadena, con discursos de los directivos, de 22:00 a 03:00",
)
#: Programas que funcionan (o que flojean con motivo).
SHARE_HITS: tuple[str, ...] = (
    "Final de un talent show con la familia del concursante en primera fila · 17,8 %",
    "Partido de la selección, con prórroga · 33,0 %",
    "Las Campanadas, con un vestido que dará que hablar · 24,5 %",
    "El informativo de las nueve, con la lluvia de fondo · 14,1 %",
    "Cocina para parejas que se pelean por la sal · 12,6 %",
    "Serie diaria sobre un pueblo en el que todos son primos · 15,3 %",
    "Un concurso en el que se acierta una pregunta por programa · 13,9 %",
    "Programa de reformas donde todo acaba bien en cuarenta minutos · 11,7 %",
    "Gala de Eurovisión, con sus votos y sus vetos · 22,4 %",
    "Programa de testimonios: la vecina cuenta lo que vio por la mirilla · 12,8 %",
    "Un talent show de cantantes que acaba en lágrimas y abrazos · 16,4 %",
    "El tiempo, que tiene su audiencia fiel · 10,2 %",
    "Documental de naturaleza con narrador de voz grave · 9,8 %",
    "Cámara oculta a famosos en un aeropuerto · 11,4 %",
    "Programa de madrugada, a las 03:00 · 6,5 % (el mejor de su franja)",
)

#: Gurú cripto: proyectos que van a ser un rug pull.
RUG_TIMOS: tuple[str, ...] = (
    "Equipo anónimo, el 95 % de los tokens en la cartera del dueño y una web de plantilla gratuita",
    "Promete un 4.000 % de rentabilidad diaria «garantizada» y un sorteo de un deportivo",
    "Un whitepaper de tres páginas, dos erratas y el logo de un perro con gafas",
    "La liquidez está bloqueada siete minutos «por seguridad»",
    "El fundador sale en las fotos con una careta, un reloj de lujo y un coche alquilado",
    "Lo audita la cuñada del fundador, con firma en un Word",
    "El token solo se puede comprar; la opción de vender se activará «próximamente»",
    "Lo anuncian diez influencers que no saben lo que es y usan la misma frase",
    "Para sacar tu dinero hay que invitar a cinco amigos y pagar una «tasa de desbloqueo»",
    "Su hoja de ruta: 1) lanzar el token, 2) luna, 3) fundar una isla",
    "En su Telegram borran cualquier pregunta que contenga la palabra «auditoría»",
    "Un token con nombre de ministro, promesas de ministro y sede en una isla sin extradición",
)
#: Proyectos serios (o aburridos, que en cripto viene a ser lo mismo).
RUG_SERIOS: tuple[str, ...] = (
    "Equipo con nombres y apellidos, y una oficina que existe y tiene la luz pagada",
    "Una cripto con diez años, miles de nodos y una comunidad que discute sobre su propia comisión",
    "Tres auditorías públicas y un informe que dice «hay cuatro riesgos medios»",
    "Ofrece un 3 % anual con la letra pequeña a la vista: «puedes perder el capital»",
    "Los desarrolladores publican cada semana el código, incluidos los errores",
    "Plataforma regulada, con licencia de la autoridad financiera y una web aburridísima",
    "El fundador cobra un sueldo normal y solo vende acciones con aviso previo",
    "Un token respaldado por reservas con informe mensual de un tercero",
    "Tiene un foro donde los usuarios critican cada decisión y el equipo contesta",
    "Una red que tarda un año más en lanzarse porque «aún no está lista»",
    "Una comunidad pequeña que crece despacio y una hoja de ruta con fechas cumplidas",
    "Código abierto, revisado por universidades, sin influencers ni sorteos",
    "Cripto de una cooperativa agrícola que se compra para pagar la cosecha, no para ir a la luna",
)

DIALOGUE_PACKS.update(
    {
        "tertulia": DialoguePack(
            "🎙️ Tertulia de las ocho, de las nueve y de las diez. Regla de oro: **opina de todo, "
            "sabe de nada y no rectifiques nunca.**",
            (
                Dialogue(
                    "El moderador te pregunta por la geopolítica del Sahel. No has abierto un "
                    "mapa en tu vida.",
                    "«Es un asunto complejísimo y, como siempre digo, hay que mirar a quién "
                    "beneficia. Y en el Sahel, créame, beneficia siempre a los mismos.»",
                    ("«Perdone, no sé exactamente dónde está el Sahel.»",
                     "«Prefiero no opinar de lo que no he estudiado.»"),
                ),
                Dialogue(
                    "La doctora de la mesa cita un estudio que te contradice.",
                    "«Los estudios están muy bien, pero yo tengo algo que no tiene ningún "
                    "estudio: experiencia de la vida.»",
                    ("«Tiene usted toda la razón; me retiro de la tertulia.»",
                     "Saco el móvil y busco el estudio en directo.",
                     "Le subo el volumen del micro, por si acaso."),
                ),
                Dialogue(
                    "Te enseñan un dato que demuestra que te equivocaste hace cinco minutos.",
                    "«Eso depende de cómo se mire el dato, y yo lo miro desde un ángulo que no "
                    "ha mirado nadie.»",
                    ("«Me equivoqué. Rectifico y pido perdón a la audiencia.»",
                     "Pido un receso y me escondo en el baño hasta la publicidad."),
                ),
                Dialogue(
                    "Te presentan como «experto en economía». Eres licenciado en Filología "
                    "Hispánica.",
                    "«Los economistas llevan veinte años equivocándose; hace falta una mirada "
                    "humanista, y esa soy yo.»",
                    ("«Soy filólogo, para qué voy a mentir.»",
                     "Cambio de tema y recito un soneto de Quevedo."),
                ),
                Dialogue(
                    "Otro tertuliano te interrumpe y habla a la vez que tú.",
                    "«Sofía, cállate: déjame terminar», y subes el tono sin haber empezado.",
                    ("Cedo la palabra con elegancia y espero mi turno.",
                     "Me callo y pongo cara de póker durante diez minutos.",
                     "Le ofrezco una infusión."),
                ),
                Dialogue(
                    "Sacan un tuit tuyo de hace dos años que dice justo lo contrario.",
                    "«Eso es una cita sacada de contexto. Además era ironía. Y además entonces "
                    "era otro momento.»",
                    ("«Sí, era yo, y estaba equivocado.»",
                     "Borro el tuit en directo, delante de todos."),
                ),
                Dialogue(
                    "El presentador pregunta: «¿Hay que subir o bajar los impuestos?».",
                    "«Si los baja mi partido, es responsabilidad fiscal; si los baja el otro, "
                    "es una irresponsabilidad. Y a la inversa.»",
                    ("«Hay que subirlos a los ricos y bajarlos a los pobres, con un estudio de "
                     "trescientas páginas.»",
                     "«Eso lo dirá Hacienda, que para eso cobra.»"),
                ),
                Dialogue(
                    "Llevas treinta segundos sin argumentos y quedan veinte minutos de tertulia.",
                    "Repites «lo que está pasando en este país» con una pausa dramática y una "
                    "mirada a cámara.",
                    ("Pides que pongan publicidad.",
                     "Confiesas que has venido por el catering.",
                     "Te quedas mirando el techo del plató."),
                ),
                Dialogue(
                    "Te preguntan por una noticia que ha salido hace una hora y no has leído.",
                    "«Ya lo avisé hace meses; lo que pasa es que nadie me hace caso.»",
                    ("«Acabo de enterarme, ¿alguien me lo resume?»",
                     "Finjo que se corta la conexión y me desconecto."),
                ),
                Dialogue(
                    "Una oyente llama para decir que te equivocas en todo.",
                    "«Agradezco la llamada: tenemos oyentes muy apasionados, y eso es "
                    "pluralidad.» Y cambias de tema.",
                    ("«Quizá tenga razón; ¿qué es lo que he dicho mal?»",
                     "Cuelgo y apago el micro para siempre."),
                ),
            ),
        ),
        "rtve": DialoguePack(
            "📡 Presidencia de RTVE, consejo por cuotas de partido. Regla de oro: **todos los "
            "partidos quieren su silla, y tú las repartes de forma que todos acaben igual de "
            "molestos.**",
            (
                Dialogue(
                    "El Gobierno quiere que el telediario abra con sus logros; la oposición, con "
                    "los escándalos.",
                    "Abres con el tiempo (una calima gordísima) y metes ambos temas después, "
                    "con la misma duración, cronometrada.",
                    ("Abres con el Gobierno, por agradecimiento.",
                     "Abres con la oposición, por quedar bien.",
                     "Abres con tu dimisión."),
                ),
                Dialogue(
                    "Hay que nombrar al director de informativos.",
                    "A alguien «de perfil técnico e independiente», con carné de partido hasta "
                    "hace nueve años.",
                    ("Convocas un concurso público con un tribunal de periodistas.",
                     "Lo echas a suertes con un dado."),
                ),
                Dialogue(
                    "Los presentadores de los dos programas del access se pelean por la "
                    "audiencia y coinciden el viernes.",
                    "Declaras que «la competencia es sana», programas a los dos el mismo día y "
                    "llamas a la prensa para anunciar la «guerra de audiencias».",
                    ("Los juntas en un programa conjunto para que se perdonen.",
                     "Cancelas los dos para evitar problemas.",
                     "Los dejas arreglarse entre ellos en el parking."),
                ),
                Dialogue(
                    "Hay un boicot a Eurovisión por la participación de un país en guerra. Te "
                    "llaman todos los partidos.",
                    "Emites un comunicado equilibrado: «RTVE lamenta la situación y estudiará "
                    "su participación» (y la estudia hasta que acabe el festival).",
                    ("Retiras a España y devuelves el dinero.",
                     "Mandas a la cantante con un cartel político.",
                     "Mandas una cabra con pandereta."),
                ),
                Dialogue(
                    "Todo el mundo habla del vestido de las Campanadas y nadie de las cuentas.",
                    "Dejas que el vestido sea el tema: si hablan del vestido, no hablan del "
                    "presupuesto.",
                    ("Pides que vayan de chándal para evitar polémicas.",
                     "Cancelas las Campanadas, por si acaso."),
                ),
                Dialogue(
                    "La comisión de control del Congreso te cita para explicar el gasto.",
                    "Llevas un informe de seiscientas páginas con anexos que concluye que no "
                    "hay nada que explicar.",
                    ("Llevas la verdad, con las cifras.",
                     "No vas, ni mandas a nadie."),
                ),
                Dialogue(
                    "Los sindicatos piden estabilizar a seiscientos interinos.",
                    "Prometes estabilizarlos en tres fases, dos de las cuales dependen de la "
                    "siguiente legislatura.",
                    ("Los estabilizas a todos mañana.",
                     "Dices que la plantilla ya está muy estable (de pie, con el abrigo "
                     "puesto)."),
                ),
                Dialogue(
                    "Te recortan cien millones y la plantilla no da más.",
                    "Anuncias una «transformación digital sin precedentes» y contratas a una "
                    "consultora para explicar el recorte.",
                    ("Cierras un canal en directo.",
                     "Pones publicidad en el telediario, entre la sequía y los incendios.",
                     "Pides un préstamo a tu cuñado."),
                ),
                Dialogue(
                    "Un programa del partido que te colocó tiene un share ridículo.",
                    "Lo trasladas a la madrugada y lo declaras «servicio público de calidad».",
                    ("Lo cancelas, que es lo que pide el share.",
                     "Obligas a los empleados a verlo."),
                ),
                Dialogue(
                    "Un periodista pregunta si hay presiones políticas en RTVE.",
                    "«Aquí hay pluralidad: cada partido presiona a su manera, y por eso "
                    "estamos equilibrados.»",
                    ("«Sí, todos los días.»",
                     "«No, ninguna», con la voz temblando."),
                ),
            ),
        ),
        "velada": DialoguePack(
            "🥊 Velada de boxeo entre streamers. Regla de oro: **el combate es lo de menos: lo "
            "que vende es el cara a cara.**",
            (
                Dialogue(
                    "Uno de los dos peleadores admite que no sabe boxear.",
                    "«Mejor: así el cara a cara tiene más rabia. Que lo diga mañana en el "
                    "directo, con voz de víctima.»",
                    ("Cancelas la pelea con una disculpa a los fans.",
                     "Le pagas tres años de clases de boxeo."),
                ),
                Dialogue(
                    "Hay que llenar un recinto de doce mil personas.",
                    "Organizas un cara a cara con empujón en la rueda de prensa y dejas que el "
                    "vídeo haga el resto.",
                    ("Bajas las entradas a 300 Y$.",
                     "Haces un cartel con la técnica de los golpes.",
                     "Esperas a que la gente se anime sola."),
                ),
                Dialogue(
                    "Un boxeador se baja del combate el día antes.",
                    "Lo vendes como «el rival ha huido» y fichas a un primo youtuber para "
                    "sustituirlo.",
                    ("Cancelas el evento y devuelves cincuenta mil entradas.",
                     "Pides al otro que pelee contra un saco."),
                ),
                Dialogue(
                    "En el pesaje, uno se pasa cuatro kilos.",
                    "Lo conviertes en espectáculo: «ha pesado lo que ha pesado», y que lo "
                    "discutan en directo.",
                    ("Aplazas el combate hasta que adelgace.",
                     "Le haces pesar la camiseta aparte."),
                ),
                Dialogue(
                    "Un patrocinador exige que el evento sea «deportivo y de valores».",
                    "Dices que es un «evento de valores» y metes en el segundo bloque un cara "
                    "a cara con insultos personales.",
                    ("Cambias el cartel por una exhibición de yoga.",
                     "Pides a los boxeadores que se den un abrazo."),
                ),
                Dialogue(
                    "Un periodista pregunta si es seguro que dos aficionados peleen a puñetazos.",
                    "«Cada uno firma un papel, hay un médico al lado y dos ambulancias… para "
                    "la foto.»",
                    ("«No es asunto mío: yo solo vendo entradas.»",
                     "«La seguridad ya la pondrá Dios.»"),
                ),
                Dialogue(
                    "Hay que decidir el orden del cartel.",
                    "El cara a cara va antes del combate y se repite después con los mismos "
                    "insultos, por rentabilidad.",
                    ("El combate primero, y luego un debate técnico sobre la pelea.",
                     "Un cara a cara a escondidas, sin cámaras."),
                ),
                Dialogue(
                    "Un streamer se niega a hacer el cara a cara.",
                    "Le pagas más por hacerlo y le escribes el guion de los insultos.",
                    ("Respetas su decisión: quiere centrarse en el combate.",
                     "Haces el cara a cara con un muñeco que se le parece."),
                ),
                Dialogue(
                    "El combate dura 40 segundos.",
                    "Cobras igual y anuncias «la revancha» para dentro de tres meses, con otro "
                    "cara a cara.",
                    ("Devuelves la mitad del dinero.",
                     "Pides que se repita gratis ahora mismo, sin público."),
                ),
                Dialogue(
                    "La plataforma de pago te pide la audiencia real.",
                    "Declaras «dos millones de espectadores simultáneos» (la mitad, con la "
                    "ventana abierta).",
                    ("Das la cifra real con decimales.",
                     "Dices que se ha perdido el contador."),
                ),
            ),
        ),
        "partido": DialoguePack(
            "🏛️ Fundador de un partido antisistema, con escaño en Estrasburgo. Regla de oro: "
            "**contra el sistema en el directo, con el sueldo del sistema en el banco.**",
            (
                Dialogue(
                    "Un periodista te pregunta cuánto cobras como eurodiputado.",
                    "«Lo cobro para que no lo cobre otro peor. Y lo reinvierto en el pueblo: "
                    "en mis directos.»",
                    ("«Es una cifra muy alta y no la merezco.»",
                     "«Lo dono todo y vivo de cartas de amor.»"),
                ),
                Dialogue(
                    "Te recuerdan que criticas a la casta… y vas en coche oficial con chófer.",
                    "«El chófer es un trabajador del pueblo y el coche es eléctrico: es el "
                    "compromiso del partido.»",
                    ("«Tienes razón: me bajo del coche y voy en bici.»",
                     "«El coche es de un amigo.»"),
                ),
                Dialogue(
                    "Un bulo que difundiste resulta ser falso.",
                    "«Yo solo pregunté: ¿y si fuera verdad? El debate ya está abierto.»",
                    ("Pido perdón y lo borro.",
                     "Digo que lo escribió mi gato."),
                ),
                Dialogue(
                    "Te piden una propuesta concreta.",
                    "«Acabar con la fiesta»: tiene lema, logo, camiseta y ninguna cifra.",
                    ("Presentas un programa de 400 páginas con costes.",
                     "Dices que la propuesta es no tener propuestas, pero con rigor."),
                ),
                Dialogue(
                    "Das un mitin por directo y solo hay 14 personas en la sala.",
                    "Dices «somos 80.000 en directo» y pones de fondo una multitud generada.",
                    ("Das la cifra real.",
                     "Cancelas el mitin y vuelves a casa en silencio."),
                ),
                Dialogue(
                    "Justicia pide levantarte la inmunidad.",
                    "Lo conviertes en «persecución del sistema» y vendes la camiseta «Yo "
                    "también soy perseguido».",
                    ("Renuncias a la inmunidad en el acto.",
                     "Te escondes en el maletero de un coche camino de Waterloo."),
                ),
                Dialogue(
                    "Otro partido antisistema, que te odia, te propone pactar.",
                    "Pactas «solo para echar al sistema» y los traicionas en la segunda "
                    "votación.",
                    ("Les dices que ellos son más sistema que tú y se rompe el pacto.",
                     "Les propones unirse en un solo partido con el tuyo al mando."),
                ),
                Dialogue(
                    "Un votante te pregunta por qué cobras dietas.",
                    "«Son dietas de resistencia: el pueblo se alimenta con gasolina y yo, con "
                    "menú del día.»",
                    ("«No sé, vienen en la nómina.»",
                     "«Para pagar el chiringuito donde estoy ahora.»"),
                ),
                Dialogue(
                    "Un rival te llama populista en televisión.",
                    "Le contestas con un meme y treinta segundos de directo en los que el "
                    "insultado eres tú.",
                    ("Le propones un debate con datos.",
                     "Te ríes con mucho gusto y le das la razón."),
                ),
                Dialogue(
                    "Un tertuliano te pide que condenes la violencia en tus mítines.",
                    "Condenas «toda violencia, venga de donde venga, sobre todo si viene de "
                    "donde me conviene».",
                    ("Pides perdón a todos los asistentes y suspendes el mitin.",
                     "Dices que nunca ha habido violencia, aunque se vea en el vídeo."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Contenido: las ramas de la oficina (Consultora y Unicornio) -------------------------------

MEMORY_PACKS.update(
    {
        "licitacion": MemoryPack(
            "🏛️ Licitación {n} del Ministerio. El expediente sigue los pasos de siempre, que "
            "son los de todos los años:",
            "Repite los pasos de la licitación en el mismo orden (un paso fuera de sitio y el "
            "interventor se pone a mirar).",
            (
                ("📜", "El pliego hecho a medida"),
                ("🪑", "La mesa de contratación"),
                ("📉", "La baja temeraria"),
                ("🔧", "El modificado"),
                ("⚖️", "El recurso del perdedor"),
                ("📬", "La publicación en la plataforma (el viernes a las 23:59)"),
                ("🤝", "La adjudicación al único licitador"),
                ("🧾", "La factura de «imprevistos»"),
                ("⏳", "La ampliación del plazo"),
                ("✂️", "La inauguración con cinta y fotógrafo"),
            ),
        ),
        "rondas": MemoryPack(
            "👼 Historial de la startup {n}. Tú pusiste el primer cheque; estas son sus rondas, "
            "en orden, con sus desgracias:",
            "Repite las rondas en el mismo orden (y no te acuerdes de lo que invertiste).",
            (
                ("🌱", "Presemilla (tu cuñado y un fondo de amigos)"),
                ("🌿", "Ronda semilla"),
                ("🚀", "Serie A, con foto de equipo en el sofá"),
                ("🩹", "Ronda puente «para llegar a la B»"),
                ("📉", "Down round: vale la mitad que hace un año"),
                ("🏦", "Serie B, con un fondo soberano que no abre el correo"),
                ("✂️", "Despidos «para ser más eficientes»"),
                ("🔔", "Salida a bolsa… del vestíbulo del edificio"),
                ("💥", "Exit (para los fundadores; los demás se quedan con el sofá)"),
            ),
        ),
    }
)  # fmt: skip

SORT_PACKS.update(
    {
        "horas": SortPack(
            "⏱️ Eres jefe de proyecto: cada hora se imputa al cliente que toque (o a quien "
            "mejor pague, que viene a ser lo mismo).",
            "🕐 Hora {n}/{total}:",
            (
                "🏛️ Ministerio",
                "🏦 Banco",
                "🍽️ Comida de trabajo",
                "📚 Formación",
            ),
            (
                ("Redactar el informe sobre el informe del subdirector general", 0),
                ("Llamada para decidir si el botón de la app pública va en azul o en «azul "
                 "institucional»", 0),
                ("Diapositiva 63 de 80: «Arquitectura de la solución» (un cuadrado con flechas)",
                 0),
                ("Comité de seguimiento de un proyecto que lleva en seguimiento cuatro años", 0),
                ("Auditoría de riesgos para una sucursal que cerraron en marzo", 1),
                ("Presentación de resultados con tres gráficos de barras y un semáforo", 1),
                ("Informe de cumplimiento normativo (de lo que no cumplen)", 1),
                ("Reunión con el director de zona sobre las comisiones de la cuenta «gratis»",
                 1),
                ("Menú del día con el cliente: tres platos, café y copa «de cortesía»", 2),
                ("Cena con el director general en un restaurante con estrella", 2),
                ("Café con el jefe de compras «para ir cerrando» (cerráis a las seis)", 2),
                ("Comida de seguimiento que se alarga hasta la hora de la merienda", 2),
                ("Curso de metodologías ágiles de 40 horas, en cascada", 3),
                ("Webinar sobre liderazgo disruptivo con un coach que no ha liderado nada", 3),
                ("Taller de gestión del cambio con post-its de colores", 3),
                ("Certificación interna en «cultura de la excelencia»", 3),
            ),
        ),
        "pivotar": SortPack(
            "🔄 Eres CEO tras la serie A: el inversor quiere que tu idea siga la moda del "
            "momento. Cada idea, a su moda.",
            "💡 Idea {n}/{total}:",
            (
                "🤖 IA",
                "🪙 Cripto",
                "🥽 Metaverso",
                "🚕 El Uber de…",
            ),
            (
                ("Un buscador de recetas… «con IA» (que sigue buscando recetas)", 0),
                ("Una app para fichar el turno que «aprende de tu cara de sueño»", 0),
                ("Un chat que contesta a tu suegra por ti", 0),
                ("Un horóscopo escrito por IA, idéntico al de siempre pero más caro", 0),
                ("Un token para pagar el café, con una comisión mayor que el café", 1),
                ("Una DAO de vecinos para decidir el color del portal", 1),
                ("La tostada de aguacate, descentralizada y con su propia cadena de bloques", 1),
                ("Un NFT de la factura de la luz (que sube de valor, ya hay que decirlo)", 1),
                ("Un gimnasio virtual con avatares sin piernas", 2),
                ("La junta de la comunidad de vecinos, en 3D y con gafas", 2),
                ("Una oficina virtual donde se hace lo mismo que antes, pero con casco", 2),
                ("Un concesionario donde pruebas el coche sin salir de casa… ni verlo", 2),
                ("El Uber de los fontaneros (llega, mira la tubería y se va, pero con puntuación)",
                 3),
                ("El Uber de las abuelas que cuidan niños, con tarifa dinámica", 3),
                ("El Uber de las plazas de garaje, que ya tiene un pleito con tu comunidad", 3),
                ("El Uber de las escaleras: sube y baja cosas, y te lo cobra por planta", 3),
            ),
        ),
    }
)  # fmt: skip

#: Director de transformación digital: fallos que tumban la app pública (el distinto).
APP_FALLOS: tuple[str, ...] = (
    "El certificado de seguridad caducó ayer y el navegador enseña un candado rojo enorme",
    "Cl@ve pide el PIN por SMS y el SMS no llega nunca (ni a las 23:59 del último día)",
    "El formulario solo funciona con un navegador de 2003 y con los plugins de entonces",
    "La cita previa da hora para el 31 de febrero",
    "Al pulsar «Enviar» sale «Error desconocido», sin más, y se vacía el formulario",
    "La página exige adjuntar un PDF de menos de 1 KB, y un DNI no cabe ni comprimido",
    "El servidor está en el sótano de una consejería y la llave la tiene alguien de baja",
    "Todos los usuarios ven el expediente de otro ciudadano (siempre el de la misma señora)",
    "El captcha pide identificar semáforos en una foto en la que no hay ninguno",
    "El sistema se cae cada día a las 23:59, que es cuando termina el plazo",
    "La base de datos guarda la ñ como «Ã±» y a España la llama «EspaÃ±a»",
    "El botón «Pagar» solo se activa cuando ya has pagado",
)
#: Cosas normales de una app pública (que funcionan o que molestan sin tumbar nada).
APP_BIEN: tuple[str, ...] = (
    "La página carga en dos segundos y pide aceptar las cookies, como todas",
    "Cl@ve manda el PIN por SMS y llega en cinco segundos",
    "El formulario guarda el borrador cada minuto y te deja seguir mañana",
    "La cita previa da hora para la semana que viene y manda un recordatorio",
    "El certificado de seguridad se renovó con un mes de margen",
    "Al pulsar «Enviar» sale un justificante con número de registro y fecha",
    "El mantenimiento del domingo a las 4:00 se avisó con una semana de antelación",
    "El buscador de trámites encuentra el trámite a la primera (un milagro menor)",
    "Hay una versión en lenguaje claro con las mismas instrucciones, pero sin latines",
    "Se pueden subir PDF de hasta 10 MB sin que la página proteste",
    "La app del móvil abre en la misma pantalla que la web, aunque con otro menú",
    "El chat de ayuda lo contesta una persona real, en horario de oficina",
    "El pago con tarjeta pasa a la primera y envía el recibo por correo",
    "La web pide el DNI en un campo con ayuda, para que no te confundas con la letra",
)

#: CEO de un unicornio: gastos que queman la caja (el distinto).
RUNWAY_QUEMA: tuple[str, ...] = (
    "Un futbolín de oro macizo en la sala de descanso (ya nadie sabe jugar)",
    "Una oficina en un ático de 400 m² para doce personas",
    "La fiesta de la empresa en Ibiza, con avión privado, por el «engagement»",
    "Un chef privado que cocina cada día para un equipo de dos personas",
    "Una consultora de naming que cobra 8.000.000 Y$ por quitarle una letra al logo",
    "Un anuncio en la final de la liga americana para una app con 300 usuarios",
    "Una sala de reuniones con cascada interior y sonido de selva",
    "Un cartel de neón gigante en la fachada que gasta lo mismo que un bloque de pisos",
    "Un reloj de lujo de regalo de Navidad para cada cliente (y para cada cuñado)",
    "Un retiro de «alineamiento» del equipo directivo en una isla del Índico",
    "Una flota de patinetes dorados para ir de la sala 1 a la sala 2",
    "Un helicóptero para el trayecto de doce minutos hasta el aeropuerto",
)
#: Gastos razonables de una empresa que quiere durar.
RUNWAY_RAZONABLE: tuple[str, ...] = (
    "Los servidores en la nube, que pagan lo que gastan los usuarios",
    "El sueldo de los ingenieros que escriben el producto",
    "Un abogado que revisa el contrato con los clientes antes de firmarlo",
    "La gestoría que hace las nóminas y los impuestos a tiempo",
    "Un coworking con una mesa para cada uno y wifi que va",
    "El seguro de responsabilidad civil que nadie quiere pero todos necesitan",
    "El café de la oficina, que es barato y sube la moral",
    "Las licencias del programa que usa todo el equipo",
    "Una auditoría de seguridad antes de lanzar la nueva versión",
    "Un portátil nuevo para el ingeniero cuyo ordenador se apaga solo",
    "Publicidad en buscadores con 50.000 Y$ al mes y medición de resultados",
    "Una persona de atención al cliente, porque los clientes escriben",
    "El dominio de la web y los correos corporativos",
    "Una hora de formación en seguridad para todo el equipo, una vez al año",
)

DIALOGUE_PACKS.update(
    {
        "comite": DialoguePack(
            "🤝 Comité de socios de la consultora. Regla de oro: **se factura la hora, no el "
            "resultado.**",
            (
                Dialogue(
                    "El bonus de fin de año: los beneficios suben un 12 % y los sueldos de los "
                    "juniors, un 0 %.",
                    "Lo repartís por horas facturadas, que casualmente son las vuestras; a los "
                    "juniors, una cesta con un jamón de plástico.",
                    ("Propones repartirlo con los juniors que han hecho el trabajo, que además "
                     "llevan semanas sin dormir bien y ya hablan de sindicarse.",
                     "Lo donas a una ONG y lo cuentas en tu red profesional."),
                ),
                Dialogue(
                    "Propuesta para el Ministerio: piden una app que funcione. Tu equipo calcula "
                    "dos meses; el presupuesto dice veinte.",
                    "Presentas veinte meses: ocho de «análisis», seis de «transformación» y seis "
                    "de «sostenibilidad». Que funcione ya se verá.",
                    ("Les dices que se hace en dos meses y les cobras dos meses.",
                     "Rechazas el contrato por vergüenza torera."),
                ),
                Dialogue(
                    "Un becario lleva 80 horas esta semana y pide, tímidamente, que se las "
                    "paguen.",
                    "Le das las gracias por su «compromiso» y un certificado de horas… que "
                    "facturas al cliente a 14.000 Y$ la hora.",
                    ("Le pagas las 80 horas de tu bolsillo, al precio de socio.",
                     "Le mandas a casa una semana, con gastos pagados, y le pides perdón en "
                     "persona ante todo el comité y delante de su madre."),
                ),
                Dialogue(
                    "El cliente se queja: el informe ha llegado con tres semanas de retraso.",
                    "Le facturas las tres semanas de retraso como «horas de revisión adicional "
                    "de calidad».",
                    ("Reconoces el retraso y le devuelves la factura.",
                     "Dices que el informe lo escribió una IA y que reclame a la IA."),
                ),
                Dialogue(
                    "Otro socio te pide que firmes un informe de 400 páginas que no has leído.",
                    "Firmas sin leer, a cambio de que él firme el tuyo: así os leéis "
                    "mutuamente la fe.",
                    ("Lees las 400 páginas y señalas tres errores de bulto, delante de todos y "
                     "con rotulador rojo.",
                     "Dices que no firmas nada sin pasarlo por el comité de ética de la casa… "
                     "que no existe."),
                ),
                Dialogue(
                    "La competencia ofrece el mismo servicio por la mitad de precio.",
                    "Subes tu tarifa un 30 % y lo llamas «posicionamiento premium»: los clientes "
                    "de verdad quieren que cueste.",
                    ("Bajas el precio a la mitad y lo recuperas «en volumen».",
                     "Pides a la competencia que te haga un descuento a ti.",
                     "Subcontratas a la competencia y te quedas con el margen, sin decírselo."),
                ),
                Dialogue(
                    "Un junior propone una solución sencilla que lo arreglaría todo en una "
                    "tarde.",
                    "Le das las gracias, lo conviertes en un proyecto de tres fases y lo cobras "
                    "como «metodología propia».",
                    ("Aplicas la solución del junior y cobras una tarde.",
                     "Lo ascienden a socio y te jubilas tú."),
                ),
                Dialogue(
                    "El cliente pregunta cuánto durará el proyecto.",
                    "«Depende del alcance», y sonríes: el alcance, por suerte, siempre se "
                    "amplía.",
                    ("«Seis meses, y entregamos en seis meses.»",
                     "Le dices la verdad: dos semanas y un programador con un café, y se lo "
                     "dices tú mismo en la reunión de apertura."),
                ),
                Dialogue(
                    "La cena de socios en un restaurante con estrella: la cuenta son "
                    "240.000 Y$.",
                    "La imputas al proyecto del Ministerio como «reunión de trabajo con "
                    "stakeholders».",
                    ("Cada uno paga lo suyo, con calculadora.",
                     "La pagas tú con tu tarjeta y te quedas sin postre."),
                ),
                Dialogue(
                    "El comité de ética (cinco minutos, un café) pregunta si hay algún conflicto "
                    "de intereses.",
                    "«Ninguno que no hayamos gestionado debidamente», y pasáis al siguiente "
                    "punto: el catering.",
                    ("Confiesas que tu cuñado es subdirector del Ministerio y que le habéis "
                     "dado el contrato de la app, con un descuento.",
                     "Contestas «todos» y señalas el techo."),
                ),
            ),
        ),
        "socio_director": DialoguePack(
            "🏛️ Despacho del socio director, con vistas al Ministerio. Regla de oro: **que el "
            "Gobierno de turno siga siendo cliente, gane quien gane.**",
            (
                Dialogue(
                    "Cambia el Gobierno y el nuevo ministro revisa los contratos del anterior.",
                    "Le invitas a comer y le explicas que tu equipo lleva «años preparando "
                    "justo su programa».",
                    ("Defiendes el contrato del anterior ante el ministro y le acusas de "
                     "partidista, con una carpeta de recortes de periódico.",
                     "Rescindes el contrato tú mismo, por si acaso."),
                ),
                Dialogue(
                    "Un exministro, recién salido del cargo, quiere entrar de «asesor» en la "
                    "firma, con despacho propio. Hace tres meses firmaba contratos contigo.",
                    "Lo fichas de «consejero independiente», sin funciones, con sueldo y con "
                    "tarjeta de visita en relieve.",
                    ("Le dices que la ley de incompatibilidades pide dos años de espera y que "
                     "le avisarás cuando pasen, con un calendario y todo.",
                     "Le ofreces un puesto de becario para que aprenda cómo es el mundo real."),
                ),
                Dialogue(
                    "El Tribunal de Cuentas pide aclarar un informe de 600 páginas que la firma "
                    "entregó al Gobierno.",
                    "Entregas un segundo informe, de 900 páginas, que explica el primero. "
                    "Cuando acabas de leerlo, nadie recuerda la pregunta.",
                    ("Admites que hay una errata en la página 312 y la corriges.",
                     "Dices que se lo comió el perro."),
                ),
                Dialogue(
                    "Fondos Next Generation: hay 140 millones para «digitalizar» un pueblo de "
                    "800 habitantes.",
                    "Presentas el proyecto «aldea inteligente»: sensores en los cuatro bancos "
                    "del pueblo y una app (la bajarán nueve).",
                    ("Propones arreglar el camino y el centro de salud, que es lo que pide el "
                     "pueblo desde hace treinta años, y lo presentas con firmas de los vecinos.",
                     "Devuelves el dinero a Bruselas con una carta de disculpa."),
                ),
                Dialogue(
                    "Un periodista pregunta si es cierto que la firma redacta las leyes que "
                    "luego evalúa.",
                    "«Asesoramos técnicamente; la decisión política corresponde siempre a los "
                    "representantes.» Justo lo que decías antes de redactarlas.",
                    ("Respondes que sí y sacas la lista de las leyes, con su precio.",
                     "Te echas a llorar delante de la cámara."),
                ),
                Dialogue(
                    "La oposición pide una comisión de investigación sobre tu contrato con el "
                    "Gobierno.",
                    "Contratas como «asesor de comunicación» al portavoz de la oposición, y la "
                    "comisión se disuelve sola, por falta de ganas.",
                    ("Te presentas voluntario a declarar, con toda la documentación.",
                     "Te vas de vacaciones a Waterloo y no dejas ni dirección."),
                ),
                Dialogue(
                    "El Ministerio quiere rebajar el precio del contrato un 10 %.",
                    "Aceptas el 10 % y amplías el alcance un 25 %: «una mejora del servicio».",
                    ("Aceptas la rebaja sin condiciones, con una sonrisa y una disculpa por el "
                     "precio anterior.",
                     "Rechazas la rebaja y dices que, en realidad, el Ministerio te debe "
                     "dinero, y que lo has soñado."),
                ),
                Dialogue(
                    "Un socio joven propone hacer el informe anual «de verdad, con datos».",
                    "Le felicitas por la iniciativa y le encargas el «análisis de viabilidad "
                    "del análisis de datos». Lo cerráis en 2031.",
                    ("Le dejas hacerlo con datos reales y descubres lo que dicen: que el "
                     "Ministerio no necesita tu firma.",
                     "Le despides por insolencia."),
                ),
                Dialogue(
                    "El Consejo de Ministros te llama a las tres de la madrugada: hay que "
                    "entregar un informe «ya».",
                    "Contestas que sí, que está casi, y lo escribes con una IA mientras te "
                    "sirven el café.",
                    ("Pides que lo dejen para la mañana, que a estas horas no se trabaja bien, "
                     "y que te llamen con cita previa.",
                     "No coges el teléfono y lo apagas para siempre."),
                ),
                Dialogue(
                    "Se acerca tu jubilación y te piden que elijas heredero para la "
                    "presidencia.",
                    "Eliges al más fiel: el que firma lo que le pones delante y se acuerda de "
                    "tu cumpleaños.",
                    ("Eliges al más brillante, que además te cae mal.",
                     "Disuelves la firma y repartes el dinero entre los empleados.",
                     "Lo echas a suertes con una urna y un notario."),
                ),
            ),
        ),
        "pitch": DialoguePack(
            "🚀 Ronda con inversores. Regla de oro: **no vendes un producto, vendes una visión "
            "(y un PowerPoint con una curva hacia arriba).**",
            (
                Dialogue(
                    "«¿Cuántos usuarios tenéis hoy?» Tenéis once, y cuatro son de tu familia.",
                    "«Tenemos una comunidad *fundadora* y un crecimiento del 400 % mes a mes.» "
                    "(De dos a once.)",
                    ("«Once, y cuatro son de mi familia.»",
                     "«Los que no tenemos aún los tendremos en cuanto lancemos, y los que hemos "
                     "tenido los hemos perdido, ya verá.»"),
                ),
                Dialogue(
                    "Preguntan por la competencia. Hay diecisiete empresas iguales que la tuya.",
                    "«No tenemos competencia: estamos creando una categoría.»",
                    ("Les enseñas la tabla con las diecisiete empresas y explicas por qué la "
                     "tuya es peor, pero más barata.",
                     "«Nuestro mayor competidor es la siesta.»"),
                ),
                Dialogue(
                    "Preguntan por los ingresos: cero yapdollars.",
                    "«Estamos en fase de pre-monetización, optimizando para el crecimiento.»",
                    ("«Cero. Pero el año que viene también.»",
                     "Les dices que el dinero es una construcción social."),
                ),
                Dialogue(
                    "El inversor duda de que el equipo sepa ejecutar.",
                    "Sacas un «advisory board» con cinco nombres ilustres. Ninguno sabe que "
                    "está en él.",
                    ("Admites que el equipo son dos personas y una impresora.",
                     "Dices que, si no confía en vosotros, no hay trato, y te levantas "
                     "indignado dejando la carpeta en la mesa."),
                ),
                Dialogue(
                    "Preguntan por el tamaño del mercado.",
                    "«Si capturamos solo el 1 % de un mercado de 50.000 millones…», y el "
                    "inversor asiente, que suena a 500.",
                    ("Les das un estudio real, con una cifra pequeña que sí podrías cumplir.",
                     "«Del tamaño de una piscina, pero muy bien aprovechada.»"),
                ),
                Dialogue(
                    "Preguntan qué valoración pides.",
                    "Pides 20 millones y dejas caer que «hay otros dos fondos interesados» (los "
                    "dos son tu primo y el primo de tu primo).",
                    ("Pides lo que vale de verdad: 20.000.000 Y$.",
                     "Dices que es información confidencial."),
                ),
                Dialogue(
                    "El proyector se apaga en mitad del pitch.",
                    "«Para quien no vea la diapositiva: es una curva que sube.» Y sigues sin "
                    "parpadear.",
                    ("Te bloqueas cinco minutos, pides que te saquen de la sala y preguntas si "
                     "hay un médico.",
                     "Les enseñas el portátil boca abajo."),
                ),
                Dialogue(
                    "Preguntan cuándo serás rentable.",
                    "«Estamos poniendo las bases; la rentabilidad llega con la escala.» El "
                    "«cuándo» no se dice nunca.",
                    ("«En dieciocho meses, si nada falla… y siempre falla algo.»",
                     "«Cuando lleguemos a la luna.»"),
                ),
                Dialogue(
                    "Te piden que expliques la empresa en una frase.",
                    "«Somos la plataforma de X, pero con IA y para la generación Z.»",
                    ("Les explicas el algoritmo con fórmulas en la pizarra, durante veinte "
                     "minutos y de espaldas.",
                     "«Una app.»"),
                ),
                Dialogue(
                    "Un inversor ofrece 2 millones a cambio del 80 % de la empresa.",
                    "Contraofertas el 10 % por 2 millones y dices «o me voy a otro fondo» (no "
                    "hay otro fondo), con la sonrisa de quien tiene tres.",
                    ("Aceptas encantado el 80 % y le ofreces también tu silla.",
                     "Le preguntas amablemente si puede ser el 79 %."),
                ),
            ),
        ),
        "red_social": DialoguePack(
            "📢 La plaza pública es tuya. Regla de oro: **lo que tú dices es libertad de "
            "expresión; lo que dicen los demás, ya se verá.**",
            (
                Dialogue(
                    "Has comprado la red social por 44.000 millones sin leer las cuentas. El "
                    "consejo quiere saber por qué.",
                    "«Por la libertad de expresión», con un tuit de un pájaro y una carcajada. "
                    "Las cuentas, ya las leeremos.",
                    ("Enseñas un análisis financiero de cincuenta páginas que justifica la "
                     "compra, con anexos y un gráfico de tarta.",
                     "Confiesas que fue sin querer."),
                ),
                Dialogue(
                    "Vendes la marca de verificación a 800 Y$ al mes.",
                    "La cobras: ahora cualquier bulo con tarjeta de crédito tiene credibilidad, "
                    "¡y ingresos!",
                    ("La regalas a los periodistas y a los médicos que demuestren quiénes son, y "
                     "a los demás les das las gracias por esperar.",
                     "La cobras a 800 Y$ y devuelves el dinero a quien no la use."),
                ),
                Dialogue(
                    "Trump vuelve a la red tras años de veto. ¿Qué haces?",
                    "Lo readmites tras una encuesta (la gana por goleada, votan 600 bots): «la "
                    "voluntad del pueblo».",
                    ("Lo dejas vetado para siempre, por pura coherencia, y publicas una carta "
                     "abierta que lo explica en doce partes.",
                     "Lo readmites, pero solo con tres palabras por tuit."),
                ),
                Dialogue(
                    "Musk, tu rival (con su propia red), te reta a un combate en una jaula.",
                    "Aceptas y fijas la fecha «para cuando los dos estemos libres» (nunca), y "
                    "vendes las entradas igual.",
                    ("Aceptas y entrenas seis meses, con dieta y todo, para pelear de verdad.",
                     "Le denuncias por competencia desleal en un juzgado de Delaware."),
                ),
                Dialogue(
                    "Un periodista escribe que eres «una copia de Musk con peor peluquero».",
                    "Lo pones como tendencia número uno en tu red y lo invitas a un debate… "
                    "moderado por ti.",
                    ("Lo niegas indignado y le enseñas tus diferencias con Musk en una "
                     "diapositiva de nueve apartados.",
                     "Le das la razón y subes un meme sobre ti mismo."),
                ),
                Dialogue(
                    "Un empleado critica tu gestión en la propia red.",
                    "Lo despides por «violar los términos de uso» y defiendes tu decisión como "
                    "libertad de expresión (la tuya).",
                    ("Le das las gracias por el feedback, abres un canal interno de "
                     "sugerencias y le invitas a un café con Recursos Humanos para hablarlo.",
                     "Le subes el sueldo, que tiene razón."),
                ),
                Dialogue(
                    "La red se llena de bulos y los anunciantes se van.",
                    "Declaras que moderar es censura y que los anunciantes son «enemigos del "
                    "pueblo»: pierdes ingresos, pero ganas un millón de seguidores que no "
                    "compran nada.",
                    ("Contratas moderadores y publicas las normas con total transparencia.",
                     "Cierras la red un mes «por reformas»."),
                ),
                Dialogue(
                    "Una ley europea te exige borrar los contenidos ilegales en 24 horas.",
                    "Respondes con un hilo de cuarenta tuits contra Bruselas y sus cookies. El "
                    "contenido sigue, la multa llega en tres años y se recurre.",
                    ("Cumples en 24 horas y encima pides perdón.",
                     "Cierras en Europa «por principios» y te mudas a una isla."),
                ),
                Dialogue(
                    "Tu tuit de las 4 de la madrugada acaba de hundir la bolsa de una empresa.",
                    "Lo dejas y subes otro: «era ironía». El mercado cree lo que más le "
                    "conviene.",
                    ("Lo borras y pides perdón al mercado con una rueda de prensa.",
                     "Lo borras y dices que fue tu gato."),
                ),
                Dialogue(
                    "Los anunciantes piden garantías: ¿qué les prometes?",
                    "A ellos, un entorno seguro para las marcas; a tus usuarios, libertad "
                    "total. Las dos cosas a la vez, en dos tuits distintos.",
                    ("Les dices que se vayan, que con tus seguidores te sobra, y que a las "
                     "marcas ya les contestarás.",
                     "Les prometes cualquier cosa y luego te olvidas."),
                ),
                Dialogue(
                    "Un senador de Estados Unidos te cita a declarar sobre la moderación.",
                    "Vas con gafas oscuras, contestas «no recuerdo» doce veces y al salir "
                    "publicas un hilo: «lo que no te cuentan de la comisión».",
                    ("No vas y mandas a tu abogado con un bigote postizo.",
                     "Vas y cuentas la verdad con detalle, incluido lo del algoritmo."),
                ),
            ),
        ),
    }
)  # fmt: skip

# -- Eventos ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    """Consecuencias de elegir una opción de un evento.

    Attributes:
        text: Lo que se cuenta al elegirla.
        pay: Parte de la base del puesto que se cobra en nómina (con impuestos).
        black: Parte de la base que se cobra en negro.
        risk: Probabilidad de que te pillen si hay dinero en negro.
        caught_by: Quién te pilla (`"inspeccion"` o `"uco"`).
        performance: Cambio de la barra de rendimiento.
        family: Cambio de la familia.
        battery: Cambio de la batería.
        stat: Estadística de logros que suma 1 al elegirla.
        demote: Si te bajan un puesto (p. ej., dimitir).
        fine: Sin dinero en negro, la multa (parte de la base) si sale `risk`.
        rating: Cambio de la valoración de la app, en décimas de estrella (reparto).
        followers: Cambio de los seguidores, en tanto por uno (influencer).
    """

    text: str
    pay: float = 0.0
    black: float = 0.0
    risk: float = 0.0
    caught_by: str = "inspeccion"
    performance: int = 0
    family: int = 0
    battery: int = 0
    stat: str | None = None
    demote: bool = False
    fine: float = 0.0
    rating: int = 0
    followers: float = 0.0


@dataclass(frozen=True, slots=True)
class Event:
    """Algo que pasa al terminar un turno, con dos opciones.

    Attributes:
        key: Identificador estable.
        text: La situación.
        options: `(etiqueta del botón, consecuencias)`; siempre dos.
        jobs: Oficios donde sale (vacío = todos).
        min_level: Puesto mínimo.
        family_below: Solo sale con la familia por debajo de esto (0 = siempre).
        abroad: `True`, solo trabajando fuera; `False`, solo en casa; `None`, siempre.
        phase: Solo con esa situación fiscal (`work.residence_phase`).
        special: No sale al azar; lo lanza una regla (p. ej., el teletrabajo).
        branches: Ramas donde sale (vacío = en cualquiera y en el tronco).
    """

    key: str
    text: str
    options: tuple[tuple[str, Outcome], tuple[str, Outcome]]
    jobs: tuple[str, ...] = ()
    min_level: int = 1
    family_below: int = 0
    abroad: bool | None = None
    phase: str | None = None
    special: bool = False
    branches: tuple[str, ...] = ()


EVENTS: tuple[Event, ...] = (
    # -- Para todos ------------------------------------------------------------------
    Event(
        "quedarse",
        "🕖 Son las siete y tu jefe asoma la cabeza: «Oye, ¿te puedes quedar un ratito más?».",
        (
            (
                "Me quedo",
                Outcome(
                    "Te quedas. Te lo pagan como extra, pero en casa ya han cenado.",
                    pay=0.5,
                    battery=-15,
                    family=-8,
                    stat="work_stayed",
                ),
            ),
            (
                "Tengo cosas",
                Outcome(
                    "«Tengo cosas que hacer». Tu jefe apunta algo en una libreta.",
                    performance=-10,
                    family=5,
                ),
            ),
        ),
    ),
    Event(
        "cena_empresa",
        "🥂 Cena de empresa en un restaurante de Las Canteras. Va todo el mundo.",
        (
            (
                "Voy",
                Outcome(
                    "Fuiste, bailaste con el jefe y él lo recuerda con cariño.",
                    performance=10,
                    battery=-10,
                    family=-3,
                ),
            ),
            (
                "No voy",
                Outcome(
                    "No fuiste. Al día siguiente todos tienen una anécdota menos tú.",
                    performance=-5,
                    family=5,
                ),
            ),
        ),
    ),
    Event(
        "en_b",
        "💶 Tu jefe, bajito: «Este mes te pago una parte en B, ¿vale? Así no te quitan el IMV».",
        (
            (
                "Vale",
                Outcome(
                    "Te llevas un sobre. Sin IRPF, sin cotizar… y sin cubrirte si te pillan.",
                    black=0.6,
                    risk=0.15,
                    stat="work_black_offer",
                ),
            ),
            (
                "Todo en nómina",
                Outcome(
                    "Le dices que todo en nómina. Te mira como si fueras de Hacienda.",
                    pay=0.3,
                    performance=-5,
                ),
            ),
        ),
    ),
    # -- Familia ----------------------------------------------------------------------
    Event(
        "madre",
        "📱 Tu madre te escribe por Discord: «¿Sigues vivo? Hace días que no te veo el pelo».",
        (
            (
                "La llamo",
                Outcome(
                    "La llamas. Te cuenta tres dramas del barrio y se queda feliz.",
                    family=12,
                    battery=-5,
                ),
            ),
            (
                "Le dejo el visto",
                Outcome("Visto a las 23:47. Ella lo sabe.", family=-6, stat="work_mom_ghosted"),
            ),
        ),
        family_below=40,
    ),
    Event(
        "comunion",
        "⛪ Es la comunión de tu sobrino y coincide con tu turno de mañana.",
        (
            (
                "Voy",
                Outcome(
                    "Fuiste. Comiste tarta y saliste en las fotos.", family=15, performance=-10
                ),
            ),
            (
                "Le mando un Bizum",
                Outcome(
                    "Le mandas un Bizum de 5.000 Y$ con un emoji de cura. Tu hermana no lo "
                    "olvidará.",
                    family=-5,
                    stat="work_communion_bizum",
                ),
            ),
        ),
        family_below=60,
    ),
    Event(
        "pareja",
        "📝 Al llegar a casa encuentras una nota en la nevera: «La cena está en el micro. "
        "Tú y yo tenemos que hablar».",
        (
            (
                "Hablamos",
                Outcome(
                    "Habláis hasta las dos. Mañana vas hecho polvo, pero en paz.",
                    family=20,
                    battery=-20,
                ),
            ),
            (
                "Mañana",
                Outcome(
                    "«Mañana hablamos». Mañana también trabajas.",
                    family=-10,
                    stat="work_note_ignored",
                ),
            ),
        ),
        family_below=30,
    ),
    # -- Obra -------------------------------------------------------------------------
    Event(
        "jubilado",
        "👴 Un jubilado se apoya en la valla: «Eso así no se hace, chacho. En mis tiempos…».",
        (
            (
                "Le explico",
                Outcome(
                    "Le explicas la obra entera. Se va feliz y vuelve mañana con otro.",
                    performance=5,
                    battery=-5,
                    stat="work_retiree",
                ),
            ),
            ("Sigo a lo mío", Outcome("Sigues cavando. Él sigue mirando. Para siempre.")),
        ),
        jobs=("obra",),
    ),
    Event(
        "calima",
        "🌫️ Entra calima fuerte y Protección Civil activa la alerta. El encargado duda.",
        (
            (
                "Paramos",
                Outcome(
                    "Se para la obra y se paga igual: permiso retribuido por fenómenos "
                    "meteorológicos adversos (art. 37.3.g ET).",
                    pay=0.5,
                    family=5,
                    stat="work_calima",
                ),
            ),
            (
                "Seguimos",
                Outcome(
                    "Seguís con mascarilla. Toses polvo del Sáhara hasta el jueves.",
                    performance=10,
                    battery=-15,
                ),
            ),
        ),
        jobs=("obra",),
    ),
    Event(
        "sin_factura",
        "🧾 Un cliente te pide la reforma del baño «sin factura, que sale más barato».",
        (
            (
                "Sin factura",
                Outcome(
                    "Sin factura, sin IGIC y sin garantía. Cobras en un sobre de la panadería.",
                    black=0.8,
                    risk=0.2,
                    stat="work_no_invoice",
                ),
            ),
            (
                "Con factura",
                Outcome(
                    "Factura con IGIC. El cliente te mira como si fueras Hacienda.",
                    pay=0.4,
                    performance=-5,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=5,
    ),
    # -- Hostelería ---------------------------------------------------------------------
    Event(
        "propina",
        "🪙 Una pareja de alemanes te deja una propina de 2.000 Y$ en efectivo.",
        (
            (
                "A la caja común",
                Outcome(
                    "A la caja común. Tus compañeros te quieren un poco más.",
                    performance=5,
                    family=0,
                ),
            ),
            (
                "Al bolsillo",
                Outcome(
                    "Al bolsillo. Dinero en negro, pero en pequeñito.",
                    black=0.4,
                    risk=0.03,
                    stat="work_tip",
                ),
            ),
        ),
        jobs=("hosteleria",),
    ),
    Event(
        "sinpa",
        "🏃 Una mesa de cuatro se levanta sin pagar y sale corriendo hacia el paseo.",
        (
            (
                "Los persigo",
                Outcome(
                    "Los alcanzas en la avenida y pagan avergonzados.",
                    performance=12,
                    battery=-12,
                    stat="work_dine_dash",
                ),
            ),
            (
                "Que se vayan",
                Outcome("Que se vayan. El jefe te lo descuenta de la moral.", performance=-8),
            ),
        ),
        jobs=("hosteleria",),
    ),
    # -- Hostelería: más marrones del tronco ---------------------------------------------
    Event(
        "contrato_4h",
        "📑 Te ofrecen un contrato de cuatro horas. Trabajando, serán diez. «Es lo que hay».",
        (
            (
                "Firmo y curro diez",
                Outcome(
                    "Firmas. Cuatro horas en la nómina y seis en un sobre. Inspección de "
                    "Trabajo, de momento, está de bocadillo.",
                    pay=0.3,
                    black=0.7,
                    risk=0.2,
                    battery=-15,
                    family=-5,
                    stat="work_four_hours",
                ),
            ),
            (
                "Contrato de verdad",
                Outcome(
                    "Exiges el contrato de jornada completa. Te miran como si hubieras "
                    "pedido caviar en el menú del día.",
                    performance=-8,
                    family=5,
                    stat="work_full_contract",
                ),
            ),
        ),
        jobs=("hosteleria",),
    ),
    Event(
        "paella_chorizo",
        "🥘 A las once de la mañana, un guiri pide paella… con chorizo. «Like in London».",
        (
            (
                "Se la hago",
                Outcome(
                    "Le haces la paella con chorizo. Lo publica en redes y media Valencia "
                    "te manda amenazas con buena ortografía.",
                    pay=0.4,
                    performance=-5,
                    stat="work_chorizo_paella",
                ),
            ),
            (
                "Ni hablar",
                Outcome(
                    "Le dices que la paella no se toca. Se va a un sitio con paella "
                    "congelada y cinco estrellas.",
                    performance=5,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
    ),
    Event(
        "terraza_metro",
        "📏 La policía local aparece con un metro y mide la terraza: tienes tres mesas de más.",
        (
            (
                "Mesas de más",
                Outcome(
                    "Dices que «son decorativas» y las dejas. El agente anota algo en la "
                    "libreta y no es tu teléfono.",
                    pay=0.6,
                    risk=0.3,
                    fine=1.0,
                    caught_by="terraza",
                    stat="work_extra_tables",
                ),
            ),
            (
                "Recoger mesas",
                Outcome(
                    "Recoges las tres mesas. El metro se va, pero los clientes se sientan "
                    "en el suelo.",
                    performance=-5,
                    battery=-10,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=3,
    ),
    Event(
        "resena_una",
        "⭐ Un cliente amenaza: «Si no me haces descuento, os pongo una estrella en las reseñas».",
        (
            (
                "Postre invitado",
                Outcome(
                    "Le invitas al postre. La reseña sale de cinco estrellas y habla de "
                    "«un trato familiar» (el de una familia mafiosa).",
                    performance=8,
                    stat="work_review_bribe",
                ),
            ),
            (
                "Que la ponga",
                Outcome(
                    "«Pon lo que quieras». Pone una estrella, una queja de la sal y una foto "
                    "de otro restaurante.",
                    performance=-8,
                    family=3,
                    stat="work_one_star",
                ),
            ),
        ),
        jobs=("hosteleria",),
    ),
    Event(
        "influencer_gratis",
        "🤳 Una influencer quiere comer gratis: «Os doy visibilidad, que tengo doce mil "
        "seguidores (nueve mil son bots)».",
        (
            (
                "Come gratis",
                Outcome(
                    "Le pones el menú entero. Sube una story de dos segundos con el plato "
                    "borroso y tu local sale como «un sitio».",
                    performance=-5,
                    stat="work_free_dinner",
                ),
            ),
            (
                "Pasa por caja",
                Outcome(
                    "Le pasas la cuenta. Te deja de seguir y 40 bots te dan un me gusta de "
                    "consolación.",
                    pay=0.3,
                    performance=5,
                ),
            ),
        ),
        jobs=("hosteleria",),
    ),
    Event(
        "crucero",
        "🚢 Atraca un crucero con 4.000 pasajeros. Traen el desayuno, el almuerzo y la cena "
        "pagados en el barco.",
        (
            (
                "Menú para cruceristas",
                Outcome(
                    "Montas un menú a 499 Y$. Venden 4.000 cafés solos y el que paga la "
                    "luz es tu bolsillo.",
                    pay=0.5,
                    battery=-15,
                    stat="work_cruise_menu",
                ),
            ),
            (
                "Cierro y me voy",
                Outcome(
                    "Cierras el chiringuito y te vas a la playa. Cuatro mil personas pasan "
                    "de largo, con su bolsa de plástico y su botella de agua del barco.",
                    performance=-8,
                    family=8,
                    stat="work_cruise_closed",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=2,
    ),
    Event(
        "ola_calor",
        "🔥 Ola de calor: 48 °C dentro de la cocina. El extractor se ha rendido y los "
        "cocineros también.",
        (
            (
                "Cocino igual",
                Outcome(
                    "Cocinas con una toalla en la cabeza. Sirves 80 menús y pierdes dos "
                    "kilos de agua. «Estoy destruido por fuera, pero por dentro estoy "
                    "nuevo».",
                    pay=0.5,
                    battery=-25,
                    stat="work_heatwave_cook",
                ),
            ),
            (
                "Cierro la cocina",
                Outcome(
                    "Cierras la cocina y abres solo bebidas frías. Todo el mundo está más "
                    "contento menos tu jefe.",
                    performance=-8,
                    family=8,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=4,
    ),
    Event(
        "no_camareros",
        "🪧 Te llaman de una tertulia de radio para que cuentes que «no encuentras "
        "camareros». En la puerta tienes un cartel: «Se busca camarero. 90.000 Y$, diez horas».",
        (
            (
                "Subo el sueldo",
                Outcome(
                    "Subes el sueldo a 140.000 Y$ y aparecen veinte currículums en una tarde. "
                    "Casualidades de la vida.",
                    performance=10,
                    family=5,
                    stat="work_waiters_paid",
                ),
            ),
            (
                "Mantengo el cartel",
                Outcome(
                    "Mantienes los 90.000 Y$ y el cartel. Sigues sin camareros y te quejas "
                    "en la tele de la juventud.",
                    performance=-8,
                    stat="work_no_waiters",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=5,
    ),
    # -- Política: corrupción ---------------------------------------------------------
    Event(
        "sobre",
        "✉️ Un empresario muy simpático te deja un sobre gordito «para la campaña».",
        (
            (
                "Lo cojo",
                Outcome(
                    "Te lo guardas en la gabardina. Chistorras de 500, nada menos.",
                    black=2.0,
                    risk=0.25,
                    caught_by="uco",
                    stat="work_envelope",
                ),
            ),
            (
                "No, gracias",
                Outcome(
                    "Lo rechazas. En el partido empiezan a desconfiar de ti.",
                    performance=-10,
                    stat="work_envelope_refused",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=2,
    ),
    Event(
        "enchufe",
        "🔌 Un dirigente de Ferraz te llama: «Necesito colocar a mi sobrino. ¿Lo ves?».",
        (
            (
                "Lo coloco",
                Outcome(
                    "Lo colocas de asesor. Ferraz no olvida a quien hace favores.",
                    performance=35,
                    stat="work_cronyism",
                ),
            ),
            (
                "No hay plaza",
                Outcome(
                    "Le dices que no hay plaza. Te mandan a pegar carteles a Fuerteventura.",
                    performance=-15,
                    stat="work_cronyism_refused",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=2,
    ),
    Event(
        "mordida",
        "🏗️ La constructora de la nueva rotonda ofrece «un 3 % para la fundación».",
        (
            (
                "Para la fundación",
                Outcome(
                    "La fundación crece. Tú también, un poquito.",
                    black=3.0,
                    risk=0.3,
                    caught_by="uco",
                    stat="work_kickback",
                ),
            ),
            (
                "Concurso público",
                Outcome(
                    "Haces un concurso público de verdad. Nadie entiende nada.",
                    performance=-5,
                    stat="work_clean_tender",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=3,
    ),
    Event(
        "falcon",
        "✈️ Hay un concierto en Benicàssim y el avión oficial está libre.",
        (
            (
                "Agenda oficial",
                Outcome(
                    "Lo apuntas como agenda oficial. Pagan los de siempre.",
                    performance=5,
                    battery=10,
                    family=5,
                    stat="work_falcon",
                ),
            ),
            (
                "Voy en guagua",
                Outcome(
                    "Vas en guagua. Llegas tarde y sin batería.",
                    battery=-15,
                    stat="work_falcon_refused",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=4,
    ),
    Event(
        "fango",
        "📰 Un digital publica que tienes un yate en Marbella. No tienes ni bici.",
        (
            (
                "Máquina del fango",
                Outcome(
                    "Denuncias la máquina del fango en un vídeo de cinco minutos. El bulo se "
                    "hace el doble de famoso, pero los tuyos te aplauden.",
                    performance=10,
                    stat="work_fango",
                ),
            ),
            (
                "Lo desmiento con papeles",
                Outcome(
                    "Publicas tu declaración de bienes. Nadie la lee; todos comparten el bulo.",
                    performance=-5,
                    battery=-5,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=2,
    ),
    Event(
        "pegasus",
        "📱 Tu móvil se calienta solo y pita de madrugada. Los de informática susurran: «Pegasus».",
        (
            (
                "Móvil nuevo",
                Outcome(
                    "Te compras otro móvil y metes el viejo en la nevera. Por si acaso.",
                    performance=5,
                    battery=-5,
                    stat="work_pegasus",
                ),
            ),
            (
                "Que escuchen",
                Outcome(
                    "Que escuchen lo aburrida que es tu agenda: plenos, comidas y la siesta. "
                    "En algún despacho, alguien bosteza.",
                    performance=-5,
                    family=-3,
                    stat="work_pegasus_listened",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=3,
    ),
    # -- Sanidad ------------------------------------------------------------------------
    Event(
        "cancun",
        "🏝️ Un visitador médico te invita a un «congreso» en Cancún. Hotel con pulsera y "
        "una charla de veinte minutos.",
        (
            (
                "Me apunto",
                Outcome(
                    "Vuelves moreno y con dos fotos de la charla. El viaje lo pagó otro.",
                    black=0.8,
                    risk=0.1,
                    caught_by="expediente",
                    battery=20,
                    family=5,
                    stat="work_cancun",
                ),
            ),
            (
                "Ética ante todo",
                Outcome("Lo rechazas. El visitador te mira raro.", stat="work_cancun_refused"),
            ),
        ),
        jobs=("sanidad",),
        min_level=3,
    ),
    Event(
        "agresivo",
        "😤 Un familiar se pone a gritar en la sala de espera porque «lleva tres horas».",
        (
            (
                "Seguridad",
                Outcome(
                    "Llamas a seguridad. Se calma de golpe.", performance=5, stat="work_aggressive"
                ),
            ),
            (
                "Lo calmo yo",
                Outcome(
                    "Le explicas el triaje con paciencia infinita. Funciona.",
                    performance=10,
                    battery=-10,
                    stat="work_aggressive",
                ),
            ),
        ),
        jobs=("sanidad",),
    ),
    Event(
        "lista_espera",
        "📉 Gerencia te pide «optimizar» la lista de espera antes de que salga en el periódico.",
        (
            (
                "La optimizo",
                Outcome(
                    "Mueves a medio paciente a «pendiente de cita». La lista baja, la espera no.",
                    performance=15,
                    stat="work_waitlist",
                ),
            ),
            (
                "Me niego",
                Outcome("Le dices a gerencia que la lista es la que es.", performance=-10),
            ),
        ),
        jobs=("sanidad",),
        min_level=4,
    ),
    Event(
        "huelga",
        "🪧 Los residentes convocan huelga por las guardias de 24 horas.",
        (
            (
                "Me sumo",
                Outcome(
                    "Te sumas. Pierdes el día, pero ganas compañeros.",
                    performance=-10,
                    family=10,
                    stat="work_strike",
                ),
            ),
            (
                "Voy a trabajar",
                Outcome(
                    "Vas a trabajar. Te pagan, pero en el grupo ya no te hablan.",
                    pay=0.5,
                    stat="work_scab",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=2,
    ),
    Event(
        "aplausos",
        "👏 Son las ocho y un vecino aplaude desde el balcón. Nadie sabe si va en serio.",
        (
            (
                "Saludo",
                Outcome("Saludas con la mano. Él sigue aplaudiendo.", family=5, stat="work_clap"),
            ),
            ("Sigo andando", Outcome("Sigues andando. Ya no estamos para eso.")),
        ),
        jobs=("sanidad",),
    ),
    Event(
        "cambio_turno",
        "📆 Te cambian el turno del domingo con un día de antelación.",
        (
            (
                "Vale",
                Outcome(
                    "Aceptas. Tu familia aplaza la paella otra vez.",
                    family=-10,
                    performance=10,
                    stat="work_shift_swap",
                ),
            ),
            ("No puedo", Outcome("Dices que no. La supervisora toma nota.", performance=-5)),
        ),
        jobs=("sanidad",),
    ),
    # -- Sanidad: más marrones del tronco ------------------------------------------------
    Event(
        "contrato_dia",
        "📞 Son las siete de la mañana y te ofrecen un contrato de un día. El undécimo este mes.",
        (
            (
                "Lo cojo",
                Outcome(
                    "Lo coges. Te firman el contrato, el finiquito y el siguiente contrato en "
                    "la misma hoja.",
                    pay=0.5,
                    battery=-10,
                    family=-5,
                    stat="work_one_day_contract",
                ),
            ),
            (
                "Paso",
                Outcome("Pasas. Te bajan en la bolsa de empleo hasta 2031.", performance=-5),
            ),
        ),
        jobs=("sanidad",),
        min_level=2,
    ),
    Event(
        "selfie_quirofano",
        "🤳 Un compañero quiere grabar un baile en el quirófano para las redes.",
        (
            (
                "Bailo",
                Outcome(
                    "Bailáis con las mascarillas puestas. Dos millones de visitas y una "
                    "reunión con la supervisora.",
                    performance=-5,
                    family=5,
                    stat="work_or_dance",
                ),
            ),
            (
                "Ni de broma",
                Outcome(
                    "Le recuerdas que el paciente sigue dormido. Graba él solo.", performance=5
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=3,
    ),
    Event(
        "cama_guardias",
        "🛏️ La cama de la sala de guardias huele a 1987 y cruje como un barco.",
        (
            (
                "Dormir igual",
                Outcome(
                    "Duermes dos horas con la bata de almohada. Hueles a 1987.",
                    battery=15,
                    stat="work_guard_bed",
                ),
            ),
            (
                "Silla del control",
                Outcome(
                    "Cabeceas en la silla del control. Te despierta el timbre de la 214.",
                    battery=-5,
                    performance=5,
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=2,
    ),
    # -- Sanidad: la pública -------------------------------------------------------------
    Event(
        "pasillo",
        "🛏️ Urgencias, colapsada: hay camas en el pasillo hasta la máquina de café.",
        (
            (
                "Abrir la planta cerrada",
                Outcome(
                    "Abres la planta cerrada «por obras» desde 2019. Las obras eran una "
                    "cinta de plástico.",
                    performance=15,
                    battery=-15,
                    stat="work_ward_opened",
                ),
            ),
            (
                "«Situación puntual»",
                Outcome(
                    "Sale tu comunicado: «situación puntual». Es la duodécima puntual del año.",
                    performance=5,
                    stat="work_hallway_beds",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=6,
        branches=("publica",),
    ),
    Event(
        "derivar_privada",
        "📋 La Consejería te pide derivar 500 operaciones a la privada «para bajar la lista».",
        (
            (
                "Derivar",
                Outcome(
                    "Las derivas. La lista baja, el gasto sube y tus quirófanos siguen "
                    "cerrados por la tarde.",
                    pay=0.5,
                    performance=10,
                    stat="work_referred_private",
                ),
            ),
            (
                "Abrir por la tarde",
                Outcome(
                    "Abres tus quirófanos por la tarde. La lista baja y en casa ya no te ven "
                    "el pelo.",
                    performance=15,
                    battery=-15,
                    family=-5,
                    stat="work_afternoon_ops",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=7,
        branches=("publica",),
    ),
    Event(
        "mascarillas",
        "😷 Un «empresario» que ayer vendía colchones te ofrece mascarillas a precio de oro, "
        "con comisión para el que lo presenta.",
        (
            (
                "Comprar",
                Outcome(
                    "Compras. Las mascarillas llegan tarde y el intermediario se compra un "
                    "Rolls. Te toca un sobre.",
                    black=2.0,
                    risk=0.3,
                    caught_by="uco",
                    stat="work_masks",
                ),
            ),
            (
                "Concurso público",
                Outcome(
                    "Concurso público con tres ofertas. Llegan una semana más tarde y a la "
                    "cuarta parte de precio.",
                    performance=-5,
                    stat="work_masks_refused",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=8,
        branches=("publica",),
    ),
    Event(
        "hospital_vacio",
        "✂️ Toca inaugurar el hospital nuevo antes de las elecciones. Faltan el personal, el "
        "material y la mitad de los ascensores.",
        (
            (
                "Cortar la cinta",
                Outcome(
                    "Cinta, tijeras y foto en la entrada. Al día siguiente lo cerráis «para "
                    "ajustes».",
                    pay=0.4,
                    performance=10,
                    stat="work_empty_hospital",
                ),
            ),
            (
                "Cuando haya personal",
                Outcome(
                    "Esperas a tener plantilla. El consejero inaugura otro hospital vacío en "
                    "la isla de al lado.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=9,
        branches=("publica",),
    ),
    Event(
        "giratoria_salud",
        "💼 Una aseguradora te ofrece un sillón en su consejo… para cuando dejes la "
        "Consejería. Que firmes ya, por si acaso.",
        (
            (
                "Firmo el precontrato",
                Outcome(
                    "Firmas con el cargo todavía caliente. Te mandan una cesta de Navidad del "
                    "tamaño de un coche.",
                    black=1.5,
                    risk=0.2,
                    caught_by="incompatibilidades",
                    stat="work_revolving_health",
                ),
            ),
            (
                "Ni hablar",
                Outcome(
                    "Les dices que hay periodo de incompatibilidad. Te miran como si "
                    "hablaras en latín.",
                    performance=-5,
                    stat="work_revolving_refused",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=10,
        branches=("publica",),
    ),
    # -- Sanidad: la privada -------------------------------------------------------------
    Event(
        "poliza_denegada",
        "📄 Un asegurado de 30 años necesita una prueba cara. La póliza tiene una cláusula "
        "en letra de cuerpo 4.",
        (
            (
                "Denegar por la cláusula",
                Outcome(
                    "Se la deniegas por la cláusula 47.3.b. La aseguradora te manda un jamón.",
                    pay=0.6,
                    performance=10,
                    risk=0.2,
                    fine=1.0,
                    caught_by="abusivas",
                    stat="work_claim_denied",
                ),
            ),
            (
                "Cubrirla",
                Outcome(
                    "La autorizas. El paciente se cura y la aseguradora te manda una circular.",
                    performance=-10,
                    family=5,
                    stat="work_claim_paid",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=6,
        branches=("privada",),
    ),
    Event(
        "canje",
        "🤳 Una influencer quiere un retoque gratis «a cambio de visibilidad».",
        (
            (
                "Canje por stories",
                Outcome(
                    "Diez stories con tu clínica de fondo. Te llegan doscientos mensajes "
                    "pidiendo lo mismo gratis.",
                    performance=10,
                    stat="work_canje",
                ),
            ),
            (
                "Con factura",
                Outcome(
                    "Le pasas la tarifa. Te deja de seguir y te pone una estrella en las reseñas.",
                    pay=0.5,
                    performance=-5,
                    stat="work_canje_refused",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=7,
        branches=("privada",),
    ),
    Event(
        "turquia",
        "🧑‍🦲 Medio pueblo vuelve de Estambul con un injerto capilar torcido y te piden "
        "arreglarlo.",
        (
            (
                "Arreglarlos",
                Outcome(
                    "Arreglas doce cabezas a precio de urgencia. Erdoğan, sin saberlo, es tu "
                    "mejor comercial.",
                    pay=0.8,
                    battery=-10,
                    stat="work_turkey_fix",
                ),
            ),
            (
                "Que reclamen allí",
                Outcome(
                    "Les das el teléfono de la clínica de Estambul. Contesta un contestador en "
                    "turco.",
                    family=5,
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=7,
        branches=("privada",),
    ),
    Event(
        "ohio",
        "🇺🇸 Un turista de EE. UU. ve tu factura y se echa a llorar: «En Ohio esto me costaría "
        "40.000 dólares».",
        (
            (
                "Tarifa de Ohio",
                Outcome(
                    "Le cobras «la tarifa internacional». Te da las gracias y 25 % de propina.",
                    pay=0.8,
                    risk=0.2,
                    fine=1.0,
                    caught_by="abusivas",
                    stat="work_ohio_rate",
                ),
            ),
            (
                "Tarifa normal",
                Outcome(
                    "Le cobras lo normal. Te abraza y dice que se muda a Canarias.",
                    family=5,
                    stat="work_fair_rate",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=8,
        branches=("privada",),
    ),
    Event(
        "concierto_sobre",
        "🤝 Un alto cargo de la Comunidad te insinúa que el concierto se renueva antes «con "
        "un detalle».",
        (
            (
                "Un detalle",
                Outcome(
                    "Un detalle en un sobre y el concierto renovado por diez años. La UCO "
                    "también tiene agenda.",
                    black=2.5,
                    risk=0.3,
                    caught_by="uco",
                    stat="work_concert_envelope",
                ),
            ),
            (
                "Por concurso",
                Outcome(
                    "Te presentas al concurso como todo el mundo. Lo ganas por los pelos y "
                    "sin sobre.",
                    performance=-10,
                    stat="work_concert_clean",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=9,
        branches=("privada",),
    ),
    Event(
        "fondo_salud",
        "🦅 Un fondo de inversión quiere comprar tu grupo hospitalario y «optimizar» la plantilla.",
        (
            (
                "Vender al fondo",
                Outcome(
                    "Vendes. Al mes, una enfermera atiende tres plantas y el fondo reparte "
                    "dividendos.",
                    pay=1.0,
                    performance=-5,
                    family=-5,
                    stat="work_health_fund_sale",
                ),
            ),
            (
                "La plantilla primero",
                Outcome(
                    "Le dices que tu plantilla no se toca. El fondo compra al de enfrente.",
                    performance=-5,
                    family=5,
                    stat="work_staff_first",
                ),
            ),
        ),
        jobs=("sanidad",),
        min_level=9,
        branches=("privada",),
    ),
    # -- Hostelería: la rama Hotelera ----------------------------------------------------
    Event(
        "balconing",
        "🩴 Dos huéspedes de la 405 quieren saltar del balcón a la piscina, que está tres "
        "pisos más abajo. Es su primera noche.",
        (
            (
                "Sellar los balcones",
                Outcome(
                    "Mandas soldar las barandillas. Los de la 405 se quejan: «¡Nos habéis "
                    "quitado la mitad de las vistas!».",
                    performance=10,
                    battery=-10,
                    stat="work_balconing_sealed",
                ),
            ),
            (
                "Cartel en cinco idiomas",
                Outcome(
                    "Pones un cartel en cinco idiomas. Lo leen del revés y siguen con el plan.",
                    performance=-5,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=6,
        branches=("hotel",),
    ),
    Event(
        "overbooking",
        "🛏️ Overbooking: has vendido 40 habitaciones de más y llegan tres autobuses a la vez.",
        (
            (
                "Al hotel de enfrente",
                Outcome(
                    "Los mandas al hotel de enfrente, con un cheque de disculpa. Al día "
                    "siguiente es tu competencia la que te manda un cheque.",
                    pay=0.3,
                    performance=8,
                    stat="work_overbook_across",
                ),
            ),
            (
                "Al de dos estrellas",
                Outcome(
                    "Los mandas a un hotel de dos estrellas «de 5 estrellas, pero de la "
                    "carretera». Cuatro reseñas hablan de una cucaracha con nombre propio.",
                    performance=-10,
                    stat="work_overbook_two_star",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=6,
        branches=("hotel",),
    ),
    Event(
        "tasa_turistica",
        "💶 El Gobierno autonómico aprueba la tasa turística: 200 Y$ por noche y persona.",
        (
            (
                "Se la cobro al cliente",
                Outcome(
                    "La repercutes en la factura con un recargo de «gastos de gestión». "
                    "Un cliente te dice que en Barcelona es peor, y no lo es.",
                    pay=0.4,
                    performance=-5,
                    stat="work_tourist_tax_passed",
                ),
            ),
            (
                "La pago yo",
                Outcome(
                    "La pagas tú, a cambio de un titular: «El hotel que no cobra la tasa». "
                    "El contable te manda un correo en mayúsculas.",
                    performance=10,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=7,
        branches=("hotel",),
    ),
    Event(
        "costas",
        "🏖️ La Ley de Costas manda demoler la terraza de tu hotel de primera línea: «está "
        "en dominio público».",
        (
            (
                "Demoler",
                Outcome(
                    "Demueles la terraza. La arena vuelve donde estaba y los clientes de "
                    "pensión completa, también.",
                    performance=-10,
                    battery=-15,
                    stat="work_costas_demolish",
                ),
            ),
            (
                "Recurrir veinte años",
                Outcome(
                    "Recurres. Veinte años de juzgados, tres abogados y una terraza "
                    "inmortal. Costas, mientras tanto, te manda una carta de las suyas.",
                    pay=0.5,
                    risk=0.25,
                    fine=1.2,
                    caught_by="costas",
                    stat="work_costas_appeal",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=8,
        branches=("hotel",),
    ),
    Event(
        "kellys",
        "🧹 Las kellys piden dejar de limpiar veinte habitaciones por turno. «Con dignidad», "
        "dicen.",
        (
            (
                "Externalizar",
                Outcome(
                    "Externalizas el servicio a una empresa multiservicios. Limpian lo "
                    "mismo, cobran menos y nadie sabe quién es su jefe.",
                    pay=0.8,
                    performance=-5,
                    family=-5,
                    stat="work_kellys_outsourced",
                ),
            ),
            (
                "Contratarlas",
                Outcome(
                    "Las contratas con el convenio. Los números se resienten, el hotel "
                    "huele a limpio y en la plantilla hay buen ambiente.",
                    performance=10,
                    family=5,
                    stat="work_kellys_hired",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=8,
        branches=("hotel",),
    ),
    Event(
        "falcon_huesped",
        "✈️ El presidente del Gobierno quiere pasar agosto en tu hotel. Llega en el Falcon "
        "y pide la suite presidencial «de cortesía».",
        (
            (
                "Suite gratis",
                Outcome(
                    "Suite, pensión completa y spa, todo de cortesía. Sales en una foto "
                    "con él (de espaldas, como siempre).",
                    pay=0.5,
                    performance=12,
                    family=-5,
                    stat="work_falcon_guest",
                ),
            ),
            (
                "Se lo cobro",
                Outcome(
                    "Se lo cobras como a cualquiera. La factura la paga «el partido» y "
                    "al mes te piden «una revisión del modelo de colaboración».",
                    performance=-10,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=9,
        branches=("hotel",),
    ),
    Event(
        "lobby_pisos",
        "🏛️ El Ayuntamiento va a limitar los pisos turísticos. Un concejal te insinúa que "
        "«se podría hablar», con un sobre de por medio.",
        (
            (
                "Un sobre para el concejal",
                Outcome(
                    "Un sobre en una bolsa de paella. La norma se retrasa seis meses y "
                    "la UCO te tiene en la agenda.",
                    black=2.5,
                    risk=0.3,
                    caught_by="uco",
                    stat="work_lobby_envelope",
                ),
            ),
            (
                "Alegaciones como todos",
                Outcome(
                    "Presentas alegaciones por la vía legal. Te las leen, te dan las "
                    "gracias y no cambia nada.",
                    performance=-8,
                    stat="work_lobby_clean",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=9,
        branches=("hotel",),
    ),
    Event(
        "canarias_limite",
        "📢 Una manifestación bajo el lema «Canarias tiene un límite» llega a la puerta de "
        "tu hotel con pancartas y cacerolas.",
        (
            (
                "Pancarta de apoyo",
                Outcome(
                    "Cuelgas una pancarta que dice «Canarias tiene un límite (de "
                    "habitaciones: 1.200)». Los manifestantes no se ríen.",
                    performance=-5,
                    family=5,
                    stat="work_limit_banner",
                ),
            ),
            (
                "Llamar a la policía",
                Outcome(
                    "Llamas a la policía. Salen en las noticias frente a tu lobby y tu "
                    "reputación en las reseñas baja un punto.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=10,
        branches=("hotel",),
    ),
    # -- Hostelería: la rama Alta cocina -----------------------------------------------------
    Event(
        "stagier",
        "🧑‍🍳 Llega un stagier de fuera: quiere trabajar seis meses gratis «por la "
        "experiencia». En el currículum pone «chef en ciernes».",
        (
            (
                "Sin contrato, sin sueldo",
                Outcome(
                    "Seis meses sin sueldo ni contrato, pero con muchas ganas. Inspección "
                    "de Trabajo tiene un olfato muy fino.",
                    black=1.0,
                    risk=0.25,
                    caught_by="inspeccion",
                    stat="work_stagier_free",
                ),
            ),
            (
                "Con contrato de prácticas",
                Outcome(
                    "Le haces contrato de prácticas, con sueldo. El stagier cree que es "
                    "una broma y te lleva a casa un tupper de agradecimiento.",
                    performance=-5,
                    family=5,
                    stat="work_stagier_paid",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=6,
        branches=("estrella",),
    ),
    Event(
        "inspector_guia",
        "🔎 Sospechas que la mesa 4 es el inspector de la Guía. Pide el menú largo y lo "
        "apunta todo en una libreta.",
        (
            (
                "Todo el trato de gala",
                Outcome(
                    "Pones un cubierto de más, una trufa de más y una sonrisa de más. "
                    "Al final era un opositor que repasaba el temario de Hacienda entre "
                    "plato y plato.",
                    performance=12,
                    battery=-20,
                    stat="work_guide_pampered",
                ),
            ),
            (
                "Como a cualquiera",
                Outcome(
                    "Le tratas igual que a los demás. Resulta que era el inspector y tu "
                    "estrella tiembla como una gelatina.",
                    performance=-8,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=7,
        branches=("estrella",),
    ),
    Event(
        "alergico",
        "🤧 Un cliente es alérgico al gluten, la lactosa, los frutos secos, el marisco y «a "
        "algunos ruidos». Reserva el menú degustación.",
        (
            (
                "Menú a medida",
                Outcome(
                    "Le cocinas un menú a medida con aire, agua y pimienta. Dice que es "
                    "lo mejor que ha comido y deja de propina un abrazo.",
                    performance=8,
                    battery=-15,
                    stat="work_allergic_menu",
                ),
            ),
            (
                "A otro restaurante",
                Outcome(
                    "Le recomiendas un restaurante «más adecuado a sus necesidades». Es "
                    "una farmacia de guardia.",
                    performance=-5,
                    family=3,
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=7,
        branches=("estrella",),
    ),
    Event(
        "tortilla",
        "🥔 Un periodista te pregunta en directo: «La tortilla de patatas, ¿con cebolla o "
        "sin cebolla?». Media España contiene la respiración.",
        (
            (
                "Con cebolla",
                Outcome(
                    "«Con cebolla.» Tu móvil explota y tu abuela te manda un audio de "
                    "once minutos. Eres tendencia dos horas.",
                    performance=8,
                    family=-5,
                    stat="work_onion_with",
                ),
            ),
            (
                "Sin cebolla",
                Outcome(
                    "«Sin cebolla.» Sale un tertuliano a gritar «¡herejía!» y la "
                    "polémica nacional te da otra estrella en visibilidad.",
                    performance=8,
                    family=-5,
                    stat="work_onion_without",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=8,
        branches=("estrella",),
    ),
    Event(
        "presentador_gritos",
        "📺 Un presentador famoso por gritar «¡esto es un desastre!» llega con cámaras a tu "
        "restaurante: «Vengo a salvarte».",
        (
            (
                "Que grabe",
                Outcome(
                    "Le dejas grabar. Grita tres veces, llora dos y al final la audiencia "
                    "se come tu plato estrella por la tele.",
                    pay=0.8,
                    performance=-5,
                    family=-5,
                    stat="work_tv_crew",
                ),
            ),
            (
                "A la calle",
                Outcome(
                    "Le echas con una bandeja. Sale en las noticias: «El chef que echó "
                    "a un presentador». Te llaman de tres cadenas.",
                    performance=10,
                    stat="work_tv_kicked",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=8,
        branches=("estrella",),
    ),
    Event(
        "jeque_oro",
        "🏜️ Un jeque de visita quiere pan de oro en todo: el entrante, el principal, el "
        "postre y el agua.",
        (
            (
                "Oro en todo",
                Outcome(
                    "Pan de oro hasta en la ensalada. Pagó con una tarjeta negra que "
                    "parecía un lingote.",
                    pay=1.0,
                    performance=-5,
                    stat="work_gold_all",
                ),
            ),
            (
                "Solo en el postre",
                Outcome(
                    "Le pones oro en el postre y le explicas que lo demás es de sabor. "
                    "El jeque te regala un reloj. Falso, pero con mucho brillo.",
                    pay=0.4,
                    performance=8,
                    stat="work_gold_dessert",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=9,
        branches=("estrella",),
    ),
    Event(
        "espumas",
        "💥 La máquina de espumas explota en pleno servicio y cubre la sala de espuma de "
        "gofio hasta las lámparas.",
        (
            (
                "Limpiar y seguir",
                Outcome(
                    "Limpias entre plato y plato. Los clientes dicen que el ambiente "
                    "estaba «muy aireado».",
                    performance=5,
                    battery=-20,
                ),
            ),
            (
                "Venderlo como técnica",
                Outcome(
                    "Lo vendes como «la nueva técnica del baño de espuma». Cobras un "
                    "suplemento y alguien lo pone en una guía.",
                    pay=0.5,
                    performance=5,
                    stat="work_foam_sold",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=9,
        branches=("estrella",),
    ),
    Event(
        "inspeccion_sanidad",
        "🩺 La Inspección de Sanidad llega sin avisar a la cocina del grupo: en la cámara "
        "hay huevos de la semana pasada, y «de otra semana».",
        (
            (
                "Esconder los huevos",
                Outcome(
                    "Metes los huevos en un armario y la inspectora abre el armario "
                    "primero. Lleva años haciendo esto.",
                    pay=0.6,
                    risk=0.3,
                    fine=1.5,
                    caught_by="sanidad_alimentaria",
                    stat="work_health_hidden",
                ),
            ),
            (
                "Cocina abierta",
                Outcome(
                    "Le abres todas las puertas y la invitas a probar la tarta. Sale "
                    "con un informe impecable y un trozo para la oficina.",
                    performance=10,
                    battery=-10,
                    stat="work_health_open",
                ),
            ),
        ),
        jobs=("hosteleria",),
        min_level=10,
        branches=("estrella",),
    ),
    # -- Oficina ------------------------------------------------------------------------
    Event(
        "viernes",
        "🚀 Son las 17:40 de un viernes y hay que desplegar «una cosita».",
        (
            (
                "Despliego",
                Outcome(
                    "Despliegas. Funciona. Esta vez.",
                    performance=10,
                    battery=-15,
                    stat="work_deploy_friday",
                ),
            ),
            ("El lunes", Outcome("Lo dejas para el lunes. El cliente se enfada.", performance=-5)),
        ),
        jobs=("oficina",),
        min_level=2,
    ),
    Event(
        "reunion_correo",
        "📨 Te invitan a una reunión de una hora para algo que cabía en un correo.",
        (
            (
                "Voy",
                Outcome(
                    "Vas. Una hora de tu vida que no vuelve.",
                    battery=-5,
                    stat="work_useless_meeting",
                ),
            ),
            (
                "Contesto por correo",
                Outcome(
                    "Contestas por correo y te cancelan la reunión. Héroe.",
                    performance=-5,
                    stat="work_meeting_killed",
                ),
            ),
        ),
        jobs=("oficina",),
    ),
    Event(
        "irlanda",
        "🇮🇪 Un reclutador de LinkedIn te ofrece irte a Dublín por el doble.",
        (
            (
                "Lo pienso",
                Outcome(
                    "Se lo dices a tu jefe para que te suba el sueldo. No cuela.",
                    performance=-5,
                    stat="work_ireland",
                ),
            ),
            ("Me quedo", Outcome("Te quedas. La lealtad cotiza poco.", performance=5)),
        ),
        jobs=("oficina",),
        min_level=2,
    ),
    Event(
        "linkedin",
        "💼 Te tienta escribir en LinkedIn: «Agradecido y emocionado de anunciar…».",
        (
            (
                "Publico",
                Outcome(
                    "Publicas. Tres reacciones: tu madre, tu jefe y un bot.",
                    family=-2,
                    stat="work_linkedin",
                ),
            ),
            ("Ni de broma", Outcome("Cierras LinkedIn. Tu dignidad lo agradece.")),
        ),
        jobs=("oficina",),
    ),
    Event(
        "paintball",
        "🔫 Team building: paintball un sábado por la mañana.",
        (
            (
                "Voy",
                Outcome(
                    "Le das al jefe en la espalda «sin querer».",
                    battery=-15,
                    performance=5,
                    stat="work_paintball",
                ),
            ),
            (
                "Tengo cosas",
                Outcome("No vas. El lunes todos tienen moratones menos tú.", performance=-5),
            ),
        ),
        jobs=("oficina",),
    ),
    Event(
        "ninja",
        "🥷 Contratan a un «ninja de la IA» que cobra el triple que tú.",
        (
            (
                "Le enseño el código",
                Outcome(
                    "Le enseñas el código. Él lo pega en un chatbot.",
                    performance=5,
                    stat="work_ai_ninja",
                ),
            ),
            (
                "Que se apañe",
                Outcome(
                    "Rompe producción en dos días. Te llaman a ti.", performance=-10, battery=-10
                ),
            ),
        ),
        jobs=("oficina",),
    ),
    Event(
        "desconexion",
        "📱 Son las 23:12. Tu jefe: «Oye, ¿tienes un momentito?».",
        (
            (
                "Contesto",
                Outcome(
                    "Contestas. El momentito dura hasta la una.",
                    performance=10,
                    family=-8,
                    stat="work_always_online",
                ),
            ),
            (
                "Desconexión digital",
                Outcome(
                    "Ni lo abres: tienes derecho a la desconexión digital (art. 88 de la Ley "
                    "Orgánica 3/2018).",
                    performance=-5,
                    family=5,
                    stat="work_disconnect",
                ),
            ),
        ),
        jobs=("oficina",),
        special=True,
    ),
    # -- Hong Kong ----------------------------------------------------------------------
    Event(
        "t8",
        "🌀 El Observatorio de Hong Kong iza la señal de tifón n.º 8. La ciudad se para.",
        (
            (
                "Me quedo en casa",
                Outcome(
                    "Te quedas en casa y cobras igual, como recomienda el Labour Department.",
                    pay=1.0,
                    family=5,
                    stat="work_t8",
                ),
            ),
            (
                "Voy a la oficina",
                Outcome(
                    "Llegas empapado. Eres el único. Tu jefe te ve.",
                    performance=10,
                    battery=-15,
                    stat="work_t8_hero",
                ),
            ),
        ),
        jobs=("oficina",),
        abroad=True,
    ),
    Event(
        "dimsum",
        "🥟 Robuso, que vive en Hong Kong, te invita a dim sum el domingo.",
        (
            (
                "Voy",
                Outcome(
                    "Har gow, siu mai y Robuso poniéndote al día del servidor. Vuelves nuevo.",
                    battery=10,
                    family=5,
                    stat="work_dimsum",
                ),
            ),
            (
                "Tengo que currar",
                Outcome(
                    "Te quedas trabajando. Robuso te manda fotos de los bollos.", performance=5
                ),
            ),
        ),
        jobs=("oficina",),
        abroad=True,
    ),
    Event(
        "lkf",
        "🍸 Afterwork en Lan Kwai Fong con los del equipo.",
        (
            (
                "Salgo",
                Outcome(
                    "Sales. Acabas cantando en un karaoke de Wan Chai.",
                    battery=-20,
                    performance=-5,
                    stat="work_lkf",
                ),
            ),
            (
                "Me retiro pronto",
                Outcome("Te vas a las diez. El lunes no entiendes los chistes.", battery=5),
            ),
        ),
        jobs=("oficina",),
        abroad=True,
    ),
    Event(
        "videollamada",
        "📞 Tu madre te hace una videollamada. En Hong Kong son las tres de la mañana.",
        (
            (
                "Contesto",
                Outcome(
                    "Contestas medio dormido. Ella está feliz de verte la cara.",
                    family=15,
                    battery=-10,
                    stat="work_videocall",
                ),
            ),
            ("Mañana la llamo", Outcome("Mañana la llamas. Ella lo sabe.", family=-5)),
        ),
        jobs=("oficina",),
        abroad=True,
    ),
    Event(
        "carta_hacienda",
        "📨 Te llega una carta de la Agencia Tributaria: «¿De verdad vive usted en Hong Kong? "
        "Su familia y sus intereses siguen en España».",
        (
            (
                "Demuestro que vivo aquí",
                Outcome(
                    "Mandas el alquiler, el certificado de residencia y la factura de la luz. "
                    "Te dejan en paz.",
                    performance=-5,
                    stat="work_proved_residence",
                ),
            ),
            (
                "La ignoro",
                Outcome(
                    "Ignoras la carta. A ver qué pasa.",
                    risk=0.4,
                    fine=3.0,
                    caught_by="hacienda",
                    stat="work_ignored_letter",
                ),
            ),
        ),
        jobs=("oficina",),
        abroad=True,
        phase="no_residente",
    ),
    # -- Reparto ------------------------------------------------------------------------
    Event(
        "sexto_sin_ascensor",
        "🏢 El cliente deja 50 Y$ de propina y te pide que se lo subas a un 6.º sin ascensor.",
        (
            (
                "Subo",
                Outcome(
                    "Subes. Llegas sin aire, pero el cliente te pone cinco estrellas.",
                    battery=-10,
                    rating=2,
                    stat="work_sixth_floor",
                ),
            ),
            (
                "Que baje él",
                Outcome(
                    "«Te espero en el portal». El cliente baja en pijama y te pone una estrella.",
                    rating=-4,
                ),
            ),
        ),
        jobs=("reparto",),
    ),
    Event(
        "diluvio",
        "🌧️ Diluvia en Las Palmas (tres gotas) y la app ofrece 200 Y$ más por pedido. ¿Sales?",
        (
            (
                "Salgo",
                Outcome(
                    "Sales con el chubasquero del bazar. Cobras más y llegas empapado.",
                    pay=0.5,
                    battery=-15,
                    stat="work_storm",
                ),
            ),
            ("Me quedo en casa", Outcome("Te quedas en casa viendo llover.", family=6)),
        ),
        jobs=("reparto",),
    ),
    Event(
        "perro_entrega",
        "🐕 En la entrega te espera un perro sin atar. El dueño grita: «¡No hace nada!».",
        (
            (
                "Corro",
                Outcome(
                    "Corres como nunca. El perro también. Entregas el pedido desde la valla.",
                    battery=-10,
                    stat="work_dog_chase",
                ),
            ),
            (
                "Le doy una papa",
                Outcome(
                    "Le das una papa del pedido. El perro, feliz; el cliente, no tanto.",
                    rating=-2,
                    stat="work_dog_bribe",
                ),
            ),
        ),
        jobs=("reparto",),
    ),
    Event(
        "huelga_riders",
        "✊ Los riders convocan una desconexión masiva contra la bajada de tarifas.",
        (
            (
                "Me desconecto",
                Outcome(
                    "Te desconectas con los compañeros. La app toma nota.",
                    performance=-10,
                    family=5,
                    stat="work_rider_strike",
                ),
            ),
            (
                "Reparto igual",
                Outcome(
                    "Repartes tú solo media ciudad. Pedidos a montones, compañeros mirando mal.",
                    pay=0.4,
                    battery=-10,
                    stat="work_rider_scab",
                ),
            ),
        ),
        jobs=("reparto",),
    ),
    Event(
        "un_hielo",
        "🧊 Un cliente pide a las 4 de la mañana un solo cubito de hielo. Gastos de envío: 300 Y$.",
        (
            (
                "Lo llevo",
                Outcome(
                    "Llevas un hielo en una mochila térmica de 40 litros. Cinco estrellas.",
                    pay=0.1,
                    rating=2,
                    stat="work_ice_cube",
                ),
            ),
            ("Lo rechazo", Outcome("Lo rechazas. La app te lo apunta.", rating=-2)),
        ),
        jobs=("reparto",),
    ),
    # -- Reparto: el tronco (rider, flota y fundador) ----------------------------------
    Event(
        "pedido_mojado",
        "🌧️ El cliente te pone una estrella: «El pedido llegó mojado». Estaba diluviando "
        "(tres gotas, pero con ganas).",
        (
            (
                "Reclamar a la app",
                Outcome(
                    "Reclamas: «llovía». Te contesta un bot que te pide que reinicies el "
                    "móvil y te desea un buen día.",
                    performance=-3,
                    stat="work_wet_claim",
                ),
            ),
            (
                "Pagarle el pedido",
                Outcome(
                    "Le pagas la hamburguesa de tu bolsillo. Cinco estrellas y una mochila "
                    "más ligera, la tuya.",
                    rating=3,
                    performance=3,
                    stat="work_wet_paid",
                ),
            ),
        ),
        jobs=("reparto",),
    ),
    Event(
        "propina_20",
        "💶 Tres pisos sin ascensor, cuarenta minutos de espera y el cliente te da 20 "
        "céntimos de propina. En monedas de uno.",
        (
            (
                "Sonreír y aceptar",
                Outcome(
                    "Cuentas las monedas delante de él. Veinte. No hace falta cambio, "
                    "dices, y la sonrisa te sale un poco torcida.",
                    rating=2,
                    stat="work_tip_cents",
                ),
            ),
            (
                "Devolvérselas",
                Outcome(
                    "«Quédate con ellas, para el próximo pedido.» El cliente te pone una "
                    "estrella por ser «borde».",
                    rating=-4,
                    family=3,
                    stat="work_tip_back",
                ),
            ),
        ),
        jobs=("reparto",),
    ),
    Event(
        "portal_sin_numero",
        "🌙 Las tres de la madrugada. La dirección dice: «Calle del Pino, el portal sin "
        "número, el de la puerta verde». Hay catorce puertas verdes.",
        (
            (
                "Llamar al cliente",
                Outcome(
                    "Contesta dormido: «Es la quinta… no, la sexta…». Entregas a las 4:10 "
                    "y te quedas con ganas de cenar.",
                    battery=-8,
                    rating=2,
                    stat="work_green_door_call",
                ),
            ),
            (
                "Dejarlo en cualquiera",
                Outcome(
                    "Lo dejas en una puerta verde al azar. Mañana alguien tendrá un "
                    "desayuno inesperado y tú, una estrella menos.",
                    rating=-4,
                    stat="work_green_door_any",
                ),
            ),
        ),
        jobs=("reparto",),
    ),
    Event(
        "patinete_sin_bateria",
        "🛴 El patinete eléctrico se queda sin batería a mitad de pedido, a tres kilómetros "
        "del cliente y cuesta arriba. «Y Víctor le dijo a Joel: móntate en mi motora.»",
        (
            (
                "Empujarlo",
                Outcome(
                    "Subes la cuesta empujando y entregas con las rodillas en la boca. El "
                    "cliente ni te mira: solo mira la hamburguesa.",
                    battery=-20,
                    rating=2,
                    stat="work_scooter_push",
                ),
            ),
            (
                "Pedir otro por la app",
                Outcome(
                    "Pagas 400 Y$ de desbloqueo por otro patinete (que tampoco tiene "
                    "batería). Entregas tarde y con tres monedas menos.",
                    performance=-5,
                    rating=-2,
                    stat="work_scooter_swap",
                ),
            ),
        ),
        jobs=("reparto",),
    ),
    Event(
        "algoritmo_rechazo",
        "🤖 El algoritmo te castiga por rechazar un pedido de 80 Y$ a nueve kilómetros: te "
        "baja la prioridad «por falta de compromiso con la comunidad».",
        (
            (
                "Aceptar lo que me echen",
                Outcome(
                    "Aceptas los siguientes diez pedidos, todos a 110 Y$ y con escaleras. "
                    "El algoritmo te manda un emoji de pulgar.",
                    pay=0.2,
                    battery=-10,
                ),
            ),
            (
                "Hacer un hilo en redes",
                Outcome(
                    "Tu hilo se hace viral: 40 riders te dan la razón y la app te manda un "
                    "correo de «tu feedback es muy importante para nosotros».",
                    performance=-5,
                    family=5,
                    stat="work_algo_thread",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=2,
    ),
    Event(
        "paquete_shenzhen",
        "📦 Llevas un paquete de 199 Y$ que viene de Shenzhen. Pesa 40 gramos y el envío te "
        "lo pagan a 210 Y$. Se mueve sospechosamente.",
        (
            (
                "Entregarlo",
                Outcome(
                    "Lo entregas. Dentro hay una funda de móvil que ya llega con la "
                    "esquina rota, de serie.",
                    pay=0.1,
                ),
            ),
            (
                "Abrirlo por curiosidad",
                Outcome(
                    "Dentro hay otro paquete. Dentro de ese, otro. Una muñeca rusa de "
                    "199 Y$ con un aviso de «sin devolución».",
                    performance=-8,
                    rating=-3,
                    stat="work_shenzhen_open",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=2,
    ),
    Event(
        "black_friday",
        "🛍️ Black Friday: 4.000 pedidos de gente que compra por 500 Y$ lo que ayer valía 500 Y$. "
        "La flota no da abasto. «Víctor le dijo a Joel: móntate en mi furgoneta.»",
        (
            (
                "Doblar turno",
                Outcome(
                    "Doblas el turno con la furgoneta de tu cuñado y entregas 200 "
                    "paquetes. Llegas a casa a las cuatro, con la espalda en la mochila.",
                    pay=0.6,
                    battery=-25,
                    family=-8,
                    stat="work_black_friday_double",
                ),
            ),
            (
                "Ponerme malo",
                Outcome(
                    "Pillas una gripe diplomática de 48 horas. Tu jefe lo entiende, "
                    "aunque solo con la cara.",
                    performance=-10,
                    family=5,
                    stat="work_black_friday_sick",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=4,
    ),
    # -- Reparto: la rama Correos ---------------------------------------------------------
    Event(
        "aviso_ausente",
        "📮 Tienes cuarenta avisos por entregar. Es muchísimo más rápido pegar el de "
        "«ausente» sin llamar al timbre.",
        (
            (
                "Pegar el aviso",
                Outcome(
                    "Pegas cuarenta avisos de «ausente» en veinte minutos. Los vecinos ya "
                    "saben que el timbre de tu portal es de adorno.",
                    pay=0.3,
                    performance=5,
                    stat="work_absent_notice",
                ),
            ),
            (
                "Llamar a cada timbre",
                Outcome(
                    "Llamas a cada timbre. Te abren 31, entregas 31 y llegas a la oficina "
                    "a las nueve de la noche. Eres el cartero más amado y más tarde.",
                    battery=-15,
                    performance=-5,
                    stat="work_ring_bell",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=6,
        branches=("correos",),
    ),
    Event(
        "voto_por_correo",
        "🗳️ Faltan tres días para las elecciones y hay 800 sobres de voto por correo en el "
        "almacén y dos furgonetas, una de ellas sin ruedas.",
        (
            (
                "Priorizar los votos",
                Outcome(
                    "Los entregas todos en plazo. Mañana alguien hablará de pucherazo "
                    "igualmente, pero esta vez con tus datos delante.",
                    performance=10,
                    battery=-15,
                    stat="work_postal_vote_ontime",
                ),
            ),
            (
                "Mezclarlos con el buzoneo",
                Outcome(
                    "Repartes los votos con los folletos del súper. El 14 % llega el día "
                    "después. Saldrás en una tertulia, a las tres de la tarde.",
                    pay=0.3,
                    risk=0.35,
                    fine=1.5,
                    caught_by="junta_electoral",
                    stat="work_postal_vote_mixed",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=7,
        branches=("correos",),
    ),
    Event(
        "oficina_rural",
        "🏚️ Desde arriba te piden cerrar la oficina rural de la aldea (tres vecinos y un "
        "burro) porque «no es rentable».",
        (
            (
                "Cerrarla",
                Outcome(
                    "La cierras con un cartel de «Gracias por su comprensión». El burro "
                    "lo entiende mejor que el consejero.",
                    pay=0.3,
                    performance=8,
                    family=-3,
                    stat="work_rural_closed",
                ),
            ),
            (
                "Mantenerla abierta",
                Outcome(
                    "La mantienes: pierdes 40.000 Y$ al mes y ganas el cariño de tres vecinos, "
                    "un burro y la prensa local, que te hace una entrevista.",
                    performance=-8,
                    family=6,
                    stat="work_rural_open",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=8,
        branches=("correos",),
    ),
    Event(
        "certificada_hacienda",
        "📨 Tienes una carta certificada de Hacienda para un vecino moroso. Si no abre, "
        "vence el plazo y le suben la multa.",
        (
            (
                "Entregársela en mano",
                Outcome(
                    "Se la entregas en mano. El vecino te mira como si le hubieras dado "
                    "la sentencia de muerte. Quizá es verdad.",
                    performance=5,
                    family=-3,
                    stat="work_certified_hand",
                ),
            ),
            (
                "Dejar el aviso de ausente",
                Outcome(
                    "Dejas el aviso. Pasa el plazo y la multa se triplica. Te lo "
                    "encuentras en el bar y te invita a una caña, sin saber nada.",
                    pay=0.2,
                    performance=-3,
                    stat="work_certified_notice",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=6,
        branches=("correos",),
    ),
    Event(
        "reyes_8_enero",
        "👑 El paquete de Reyes del niño de la calle del Pino llega el 8 de enero. Los Reyes "
        "ya se han ido.",
        (
            (
                "Culpar a los camellos",
                Outcome(
                    "«Los camellos salieron tarde por la nieve en Canarias.» (No ha "
                    "nevado.) El niño te cree. Su madre, no.",
                    performance=5,
                    stat="work_kings_excuse",
                ),
            ),
            (
                "Escribir una carta de disculpa",
                Outcome(
                    "Escribes una carta firmada «Melchor, Gaspar y Baltasar, S. A.». El "
                    "niño la enmarca. Correos te abre un expediente por falsificar la "
                    "firma.",
                    performance=-5,
                    family=5,
                    stat="work_kings_apology",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=7,
        branches=("correos",),
    ),
    Event(
        "enchufe_correos",
        "🤝 Un compañero de partido, de los de Moncloa, te pide colocar a su cuñada como "
        "jefa de sección. Su currículum cabe en un pósit.",
        (
            (
                "Colocarla",
                Outcome(
                    "La colocas. Ya tiene despacho, coche oficial y un sello para firmar. "
                    "Enhorabuena: tienes un amigo.",
                    black=0.5,
                    risk=0.25,
                    caught_by="uco",
                    stat="work_correos_enchufe",
                ),
            ),
            (
                "Negarte",
                Outcome(
                    "Te niegas. Al día siguiente tu puesto lo ocupa «una persona de la "
                    "máxima confianza», y tú te quedas con la cartera.",
                    performance=-12,
                    family=5,
                    stat="work_correos_refuse",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=9,
        branches=("correos",),
    ),
    Event(
        "comision_congreso",
        "🏛️ Te citan en la comisión de investigación del Congreso por las pérdidas de "
        "Correos: «¿Dónde está el dinero?».",
        (
            (
                "Comparecer sin contestar",
                Outcome(
                    "Respondes a todo con «lo analizaremos con rigor». Duras cuatro horas "
                    "sin decir nada y sales con ovación de los tuyos.",
                    performance=10,
                    stat="work_commission_dodge",
                ),
            ),
            (
                "Decir la verdad",
                Outcome(
                    "Dices que las pérdidas son porque «nadie manda cartas». Te aplauden "
                    "los de la oposición y te desayunan los tuyos.",
                    performance=-15,
                    family=5,
                    stat="work_commission_truth",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=10,
        branches=("correos",),
    ),
    Event(
        "sello_conmemorativo",
        "🖼️ Correos quiere sacar un sello conmemorativo con tu cara por tu «labor al "
        "frente». Tirada: 50.000 sellos.",
        (
            (
                "Aceptar",
                Outcome(
                    "Aceptas. Tu cara se pega en 50.000 sobres, con saliva. Los "
                    "coleccionistas pagan 300 Y$ por los que salen torcidos.",
                    pay=0.4,
                    performance=5,
                    stat="work_stamp_face",
                ),
            ),
            (
                "Rechazarlo con modestia",
                Outcome(
                    "Dices que no. Tres semanas después sale igual, «por error de "
                    "imprenta». Y se agota en una tarde.",
                    family=5,
                    performance=-5,
                    stat="work_stamp_refuse",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=10,
        branches=("correos",),
    ),
    # -- Reparto: la rama Imperio logístico -----------------------------------------------
    Event(
        "huelga_flota",
        "✊ Tus «colaboradores» convocan huelga contra la bajada de tarifas. Tú, el jefe, no "
        "tienes nada contra la huelga. La ley, tampoco, pero tú sí.",
        (
            (
                "Subir dos céntimos",
                Outcome(
                    "Les subes dos céntimos por pedido y lo anuncias como «histórico». "
                    "Sale tu foto en el periódico y el chiste, en las redes.",
                    performance=8,
                    battery=-5,
                    stat="work_logi_strike_cents",
                ),
            ),
            (
                "Contratar esquiroles",
                Outcome(
                    "Traes 200 «colaboradores» de otra ciudad. Ahora la huelga es contra "
                    "ti, contra ellos y contra el mundo.",
                    pay=0.4,
                    performance=-5,
                    stat="work_logi_scabs",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=6,
        branches=("logistica",),
    ),
    Event(
        "inspeccion_falsos_autonomos",
        "🕵️ La Inspección de Trabajo avisa de una visita: tienes 3.000 «colaboradores» que "
        "cumplen horario, usan tu app, tu mochila y tu camiseta.",
        (
            (
                "Mantenerlos autónomos",
                Outcome(
                    "Los mantienes como colaboradores y ahorras un millón en cuotas. "
                    "La inspectora toma notas con una sonrisa muy educada.",
                    black=0.8,
                    risk=0.35,
                    caught_by="inspeccion",
                    stat="work_logi_false_freelance",
                ),
            ),
            (
                "Contratarlos a todos",
                Outcome(
                    "Los contratas. Tu margen cae un 30 %, tu contable llora y la "
                    "inspectora te sonríe por primera vez.",
                    performance=8,
                    family=4,
                    stat="work_logi_hire",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=7,
        branches=("logistica",),
    ),
    Event(
        "dron_piscina",
        "🛸 Tu dron de reparto, con una hamburguesa de 1.800 Y$, cae en la piscina del cliente. "
        "Con el cliente dentro.",
        (
            (
                "Reclamar el dron",
                Outcome(
                    "Reclamas el dron; el cliente reclama la hamburguesa, la piscina y "
                    "un abogado. Los tres aparecen el lunes.",
                    performance=-5,
                    stat="work_drone_claim",
                ),
            ),
            (
                "Regalarle la hamburguesa",
                Outcome(
                    "Se la regalas y te hace una reseña: «Entrega de otro nivel, "
                    "literalmente». Sale en el telediario.",
                    performance=5,
                    stat="work_drone_gift",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=8,
        branches=("logistica",),
    ),
    Event(
        "almacen_40",
        "🌡️ El almacén está a 40 °C y los riders se desmayan entre palés. El aire "
        "acondicionado cuesta 3.000.000 Y$ y tu margen es de 3 Y$ por pedido.",
        (
            (
                "Comprar ventiladores",
                Outcome(
                    "Compras seis ventiladores del bazar y los pones a ras de suelo. Los "
                    "riders te lo agradecen y el almacén sigue a 39,5 °C.",
                    performance=6,
                    battery=-10,
                ),
            ),
            (
                "Vender agua a 300 Y$",
                Outcome(
                    "Vendes la botella de agua a 300 Y$ en la máquina del almacén. Lo llamas "
                    "«hidratación premium» y lo cobras con tarjeta.",
                    pay=0.3,
                    performance=-10,
                    stat="work_hot_warehouse_water",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=7,
        branches=("logistica",),
    ),
    Event(
        "aranceles_trump",
        "🇺🇸 Trump anuncia aranceles del 25 % a todo lo que llegue en barco, incluidas tus "
        "mochilas térmicas.",
        (
            (
                "Repercutirlos al cliente",
                Outcome(
                    "Los repercutes con un «recargo logístico». El cliente se queja de "
                    "Trump y tú te ríes con la cuenta de resultados.",
                    pay=0.4,
                    performance=-3,
                    stat="work_tariff_passed",
                ),
            ),
            (
                "Pagarlos tú",
                Outcome(
                    "Los pagas tú y lo llamas «patriotismo logístico». Nadie te cree y tu "
                    "margen llora en un rincón.",
                    performance=5,
                    battery=-10,
                    stat="work_tariff_paid",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=9,
        branches=("logistica",),
    ),
    Event(
        "cohete_explota",
        "🚀 Tu cohete de pruebas explota en directo ante 40 millones de espectadores. En un "
        "lateral se lee tu logo. «Y Víctor le dijo a Joel: móntate en mi cohete.»",
        (
            (
                "«Éxito de aprendizaje»",
                Outcome(
                    "Lo llamas «desmontaje rápido no planificado». Tus acciones suben un "
                    "3 % y Musk te manda un emoji.",
                    performance=8,
                    stat="work_rocket_spin",
                ),
            ),
            (
                "Pedir perdón en directo",
                Outcome(
                    "Pides perdón llorando. Un meme te mantiene tres días en tendencia y "
                    "tu madre te llama preocupada.",
                    performance=-8,
                    family=5,
                    stat="work_rocket_sorry",
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=10,
        branches=("logistica",),
    ),
    Event(
        "comprar_periodico",
        "📰 Un periódico publica que explotas a tus riders. Su dueño acepta «un acuerdo de "
        "publicidad institucional» a cambio de tratarte con cariño.",
        (
            (
                "Comprar la publicidad",
                Outcome(
                    "Pagas 20.000.000 Y$ de publicidad y a la semana sales como «el "
                    "empresario que reinventó el reparto». Tu madre guarda el recorte.",
                    black=0.5,
                    risk=0.25,
                    caught_by="uco",
                    stat="work_press_bought",
                ),
            ),
            (
                "Dejar que publiquen",
                Outcome(
                    "Publican. Tu reputación cae, pero las ventas suben «porque se habla "
                    "de nosotros».",
                    performance=-8,
                    pay=0.2,
                ),
            ),
        ),
        jobs=("reparto",),
        min_level=8,
        branches=("logistica",),
    ),
    # -- Funcionariado ---------------------------------------------------------------------
    Event(
        "desayuno",
        "🥐 Son las 10:00: la hora sagrada del desayuno. Hay cola en la ventanilla.",
        (
            (
                "Me voy a desayunar",
                Outcome(
                    "Cuelgas «Vuelvo en 5 minutos» y vuelves en 40, con el barraquito dentro.",
                    battery=15,
                    performance=-5,
                    stat="work_breakfast",
                ),
            ),
            (
                "Atiendo la cola",
                Outcome(
                    "Atiendes la cola en ayunas. Nadie te lo agradece.", performance=5, battery=-5
                ),
            ),
        ),
        jobs=("funcionariado",),
    ),
    Event(
        "expediente_perdido",
        "📁 Aparece detrás del archivador un expediente de 2011 sin resolver.",
        (
            (
                "Lo tramito",
                Outcome(
                    "Lo tramitas. El vecino lleva trece años esperando y llora al teléfono.",
                    performance=10,
                    battery=-10,
                    stat="work_lost_file",
                ),
            ),
            (
                "Lo vuelvo a esconder",
                Outcome(
                    "Lo vuelves a dejar donde estaba. Ojos que no ven…",
                    risk=0.15,
                    fine=0.5,
                    caught_by="defensor",
                    stat="work_hidden_file",
                ),
            ),
        ),
        jobs=("funcionariado",),
    ),
    Event(
        "sistema_caido",
        "💻 Se ha caído el sistema informático. Otra vez.",
        (
            (
                "Lo apunto a mano",
                Outcome("Lo apuntas todo a boli, como en 1985.", performance=5, battery=-10),
            ),
            (
                "Cartel de «sin sistema»",
                Outcome(
                    "Cuelgas el cartel y te vas a casa antes. Mañana, el doble de cola.",
                    family=5,
                    stat="work_no_system",
                ),
            ),
        ),
        jobs=("funcionariado",),
    ),
    Event(
        "sobrino_concejal",
        "🤝 Un concejal te pide que «mires con cariño» el expediente de su sobrino.",
        (
            (
                "Lo miro con cariño",
                Outcome(
                    "Lo pones el primero de la pila. El concejal te deja un sobre «para el café».",
                    black=0.5,
                    risk=0.15,
                    caught_by="uco",
                    stat="work_favor",
                ),
            ),
            (
                "Por orden de entrada",
                Outcome(
                    "«Va por orden de entrada, como todos». El concejal ya no te saluda.",
                    performance=-5,
                    stat="work_by_the_book",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=2,
    ),
    Event(
        "moscosos",
        "🏖️ Te quedan días de asuntos propios (los moscosos) sin gastar y acaba el año.",
        (
            (
                "Me los cojo",
                Outcome(
                    "Te los coges todos seguidos. Tu familia ya no se acordaba de ti.",
                    family=15,
                    performance=-5,
                    stat="work_moscosos",
                ),
            ),
            (
                "Los regalo",
                Outcome("Los pierdes. Tu jefe de negociado te mira raro.", performance=5),
            ),
        ),
        jobs=("funcionariado",),
        min_level=2,
    ),
    Event(
        "fotocopia_dni",
        "📄 Un ciudadano trae la fotocopia de su DNI… por una sola cara. El impreso pide las "
        "dos, y él lleva dos horas en la cola.",
        (
            (
                "Se la acepto",
                Outcome(
                    "La aceptas con una sonrisa. En tres semanas te la devolverán requerida "
                    "«por defecto de forma» y la culpa será de otro compañero.",
                    performance=-5,
                    family=3,
                    stat="work_dni_accept",
                ),
            ),
            (
                "Por las dos caras",
                Outcome(
                    "«Por las dos caras, que lo dice la ley». Se va a fotocopiar y vuelve con "
                    "el DNI otra vez por una cara. Y con ganas de hablar con tu jefe.",
                    performance=5,
                    battery=-5,
                    stat="work_dni_two_sides",
                ),
            ),
        ),
        jobs=("funcionariado",),
    ),
    Event(
        "cita_marzo",
        "📅 Un vecino pide cita previa. La primera libre es en marzo. Estamos en octubre y "
        "necesita el certificado para hoy.",
        (
            (
                "Le hago un hueco",
                Outcome(
                    "Le haces un hueco entre dos cafés. La cola se entera y quiere el mismo "
                    "trato. Se arma el cuarto Waterloo del día.",
                    performance=5,
                    battery=-10,
                    stat="work_slot_found",
                ),
            ),
            (
                "«Para marzo»",
                Outcome(
                    "«La cita es para marzo». Se va a la oficina de al lado y le dan cita para "
                    "mañana. Desde entonces vigilas esa oficina con recelo.",
                    family=5,
                    stat="work_march",
                ),
            ),
        ),
        jobs=("funcionariado",),
    ),
    Event(
        "sin_reposicion",
        "👋 Tu compañero de al lado se jubila y no hay reposición: sus expedientes (y su "
        "cafetera) pasan a ser tuyos. Se despide: «Hazme café… ¡Quiero café!».",
        (
            (
                "Me quedo con todo",
                Outcome(
                    "Te quedas con los expedientes, la cafetera y una pila de cartas "
                    "dirigidas a «Don Anselmo». Tu jefe lo llama «sinergias».",
                    performance=10,
                    battery=-15,
                    stat="work_no_replacement_take",
                ),
            ),
            (
                "A la pila de nadie",
                Outcome(
                    "Los dejas en la pila de «pendientes de reposición». Es la más alta de "
                    "la oficina y lleva desde 2009 creciendo.",
                    performance=-5,
                    family=5,
                    stat="work_no_replacement_pile",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=2,
    ),
    Event(
        "curso_igualdad",
        "🎓 Curso obligatorio de igualdad: doce horas de vídeo, y hay que firmar que lo has visto.",
        (
            (
                "Lo hago entero",
                Outcome(
                    "Lo ves entero, con apuntes. Descubres que tu jefe va por el módulo 1 "
                    "desde 2019.",
                    performance=5,
                    battery=-10,
                    stat="work_course_done",
                ),
            ),
            (
                "Vídeo en silencio",
                Outcome(
                    "Lo dejas en silencio en una pestaña mientras haces el crucigrama en "
                    "otra. Apruebas con un 9,8. El crucigrama, con un 10.",
                    battery=5,
                    stat="work_course_mute",
                ),
            ),
        ),
        jobs=("funcionariado",),
    ),
    Event(
        "cesta_navidad",
        "🧺 Un proveedor te deja en la mesa una cesta de Navidad con jamón, cava y una "
        "tarjeta: «Para lo que necesite el expediente». Estamos en octubre.",
        (
            (
                "La acepto",
                Outcome(
                    "Te quedas la cesta (y la tarjeta). El jamón estaba buenísimo; el "
                    "expediente, mejor aún.",
                    black=0.4,
                    risk=0.15,
                    caught_by="uco",
                    stat="work_basket_taken",
                ),
            ),
            (
                "La devuelvo",
                Outcome(
                    "La devuelves con un acta de tres folios. El proveedor ya no te saluda. "
                    "Tu cuñado, tampoco, por el jamón.",
                    performance=-5,
                    stat="work_basket_returned",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=2,
    ),
    Event(
        "famoso_sociedad",
        "🎤 Te cae en la mesa el expediente de un cantante de éxito: factura sus conciertos "
        "por una sociedad instrumental con sede en un buzón y un único empleado, su guitarra.",
        (
            (
                "Inspección a fondo",
                Outcome(
                    "Abres expediente. Su abogado te manda una carta de 90 páginas y dos "
                    "entradas para el palco. Tú, las dos entradas, a Hacienda.",
                    performance=10,
                    battery=-10,
                    family=-3,
                    stat="work_famous_probe",
                ),
            ),
            (
                "«Falta de indicios»",
                Outcome(
                    "Lo archivas «por falta de indicios». Esa tarde recibes una camiseta "
                    "firmada. Dentro de la camiseta, un sobre.",
                    black=0.5,
                    risk=0.15,
                    caught_by="uco",
                    stat="work_famous_ignored",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=5,
        branches=("",),
    ),
    Event(
        "youtuber_padron",
        "🏔️ Un youtuber de cinco millones de seguidores jura en directo que vive en Andorra. "
        "Detrás de él, la Puerta de Alcalá.",
        (
            (
                "Le pido el padrón",
                Outcome(
                    "Le pides el padrón, el recibo de la luz y el cepillo de dientes. Tarda "
                    "tres semanas y sube un vídeo de disculpas con música triste.",
                    performance=10,
                    battery=-10,
                    stat="work_youtuber_padron",
                ),
            ),
            (
                "Un «me gusta»",
                Outcome(
                    "Le das un «me gusta» desde la cuenta oficial. Te llega un código de "
                    "descuento para unas gafas de sol.",
                    performance=-5,
                    family=3,
                    stat="work_youtuber_like",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=5,
        branches=("",),
    ),
    # -- Funcionariado: la Alta Administración ---------------------------------------------
    Event(
        "boe_viernes",
        "📰 Viernes de agosto, 23:58. Hay que publicar en el BOE una disposición que no "
        "gusta a nadie. A las 23:59, ni la lee el Tribunal Supremo.",
        (
            (
                "Publicar a las 23:59",
                Outcome(
                    "Se publica a las 23:59 y 40 segundos. El lunes los telediarios hablan "
                    "de un perro que sabe esquiar.",
                    performance=10,
                    battery=-10,
                    stat="work_boe_friday",
                ),
            ),
            (
                "Esperar al lunes",
                Outcome(
                    "Esperas al lunes, con luz del día y con la prensa. Tu ministro te mira "
                    "como se mira a un topo.",
                    performance=-10,
                    family=5,
                    stat="work_boe_monday",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=6,
        branches=("alta",),
    ),
    Event(
        "omnibus_dg",
        "📚 Hay que aprobar un decreto ómnibus: en el mismo texto, las pensiones, el IVA del "
        "aceite, una condonación de deuda y el nombre de un puente.",
        (
            (
                "Meter de todo",
                Outcome(
                    "Metes de todo, hasta el nombre del puente. Nadie lo lee y todos lo "
                    "votan. Hay una enmienda que no se sabe de quién es.",
                    pay=0.3,
                    performance=10,
                    stat="work_omnibus_all",
                ),
            ),
            (
                "Un decreto, un tema",
                Outcome(
                    "Un decreto, un tema, como en la facultad. El Consejo de Ministros lo "
                    "aprueba pero no vuelve a contar contigo.",
                    performance=-5,
                    stat="work_omnibus_single",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=7,
        branches=("alta",),
    ),
    Event(
        "cunado_asesor",
        "👨‍👩‍👦 El ministro te pide que nombres a su cuñado «asesor del gabinete». Es ingeniero de "
        "caminos… de caminos de cabras.",
        (
            (
                "Lo nombro",
                Outcome(
                    "Lo nombras. Cobra 4.000 al mes por asesorar al gabinete en lo suyo. Tú "
                    "recibes una cesta con tu nombre.",
                    black=0.5,
                    risk=0.15,
                    caught_by="uco",
                    stat="work_brother_in_law",
                ),
            ),
            (
                "Que oposite",
                Outcome(
                    "«Que oposite, como todo el mundo». Se ofende y se presenta a la "
                    "oposición… a ser tu jefe.",
                    performance=-5,
                    stat="work_brother_refused",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=6,
        branches=("alta",),
    ),
    Event(
        "coche_pan",
        "🚗 Son las 8:00, el coche oficial te espera en la puerta con el chófer y tú vas a "
        "por el pan.",
        (
            (
                "En el coche oficial",
                Outcome(
                    "Vas a por el pan en el coche oficial, con el chófer en doble fila. El "
                    "pan estaba buenísimo; el viaje, regular.",
                    performance=5,
                    fine=0.6,
                    risk=0.2,
                    caught_by="tribunal_cuentas",
                    stat="work_car_bread",
                ),
            ),
            (
                "Andando",
                Outcome(
                    "Vas andando, 800 metros, con tu cartera de cuero. Dos directores "
                    "generales te adelantan en coche oficial y te saludan por la ventanilla.",
                    battery=5,
                    performance=-5,
                    stat="work_walk_bread",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=8,
        branches=("alta",),
    ),
    Event(
        "informe_consejo",
        "📄 El Consejo de Estado emite un informe que dice, en 80 folios, justo lo contrario "
        "de lo que quiere el Gobierno. Y alguien lo ha filtrado a un periódico.",
        (
            (
                "Archivarlo",
                Outcome(
                    "Lo archivas en el cajón de «informes no preceptivos» (todos). El "
                    "periodista llama a las diez y a las once ya no.",
                    performance=10,
                    stat="work_council_archive",
                ),
            ),
            (
                "Publicarlo",
                Outcome(
                    "Lo publicas íntegro. El Gobierno te cesa por «pérdida de confianza» "
                    "y, por la tarde, te nombra director de otra cosa.",
                    performance=-10,
                    family=5,
                    stat="work_council_publish",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=9,
        branches=("alta",),
    ),
    Event(
        "jubilacion_dorada",
        "🏛️ El Consejo te ofrece un sillón vitalicio junto a los expresidentes del Gobierno: "
        "sueldo, despacho, coche y un dictamen al año (sin plazo).",
        (
            (
                "Aceptar el sillón",
                Outcome(
                    "Te sientas entre cuatro expresidentes que no se hablan. Os une el cargo "
                    "y el cochazo.",
                    black=0.8,
                    risk=0.2,
                    caught_by="incompatibilidades",
                    stat="work_golden_chair",
                ),
            ),
            (
                "Rechazarlo",
                Outcome(
                    "Dices que prefieres seguir trabajando. Ya se sentará otro (hay lista "
                    "de espera y mucho quórum).",
                    performance=5,
                    stat="work_golden_refused",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=10,
        branches=("alta",),
    ),
    # -- Funcionariado: la Puerta giratoria ------------------------------------------------
    Event(
        "compa_hacienda",
        "☎️ Te llama tu antiguo compañero de Hacienda: tiene abierta una inspección a tu "
        "cliente. «Por los viejos tiempos», dice.",
        (
            (
                "Una comida en el reservado",
                Outcome(
                    "Comida en un reservado con vistas al Retiro. Hablas de todo menos de "
                    "trabajo y el expediente aparece archivado «por prescripción».",
                    black=0.6,
                    risk=0.2,
                    caught_by="uco",
                    stat="work_old_pal_lunch",
                ),
            ),
            (
                "Que haga su trabajo",
                Outcome(
                    "«Haz tu trabajo, que yo ya hice el mío». Tu cliente cambia de despacho "
                    "esa misma semana.",
                    performance=-10,
                    stat="work_old_pal_refused",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=6,
        branches=("giratoria",),
    ),
    Event(
        "amnistia_fiscal",
        "🎁 El Gobierno anuncia una amnistía fiscal: quien regularice paga un 3 % y no "
        "pregunta nadie. Tus clientes con dinero fuera te miran fijamente.",
        (
            (
                "Acogerse",
                Outcome(
                    "Se acogen todos, con un 3 % y un suspiro. Cobras una comisión por "
                    "regularizar lo que tú mismo les habías escondido.",
                    pay=0.5,
                    performance=5,
                    stat="work_amnesty_in",
                ),
            ),
            (
                "No acogerse",
                Outcome(
                    "«Yo, de amnistías, ni una». Tus clientes siguen con el dinero fuera y "
                    "tú, con tu honor. Y con los honorarios sin cobrar.",
                    performance=-5,
                    family=3,
                    stat="work_amnesty_out",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=7,
        branches=("giratoria",),
    ),
    Event(
        "lista_papeles",
        "📂 Se filtra una lista de titulares de cuentas en paraísos fiscales (la de Falciani "
        "de la semana, los papeles de Panamá del mes). Tu mejor cliente sale en la página 4.",
        (
            (
                "Negarlo todo",
                Outcome(
                    "«Es un homónimo»: con la misma sociedad, el mismo yate y la misma cuenta. "
                    "Cuela en la tertulia; en el juzgado, ya veremos.",
                    pay=0.3,
                    fine=0.5,
                    risk=0.25,
                    caught_by="fiscalia",
                    stat="work_papers_deny",
                ),
            ),
            (
                "Regularizar a la carrera",
                Outcome(
                    "Regularizas a toda prisa, con recargo y lágrimas. El cliente te factura "
                    "la humillación.",
                    performance=5,
                    battery=-15,
                    stat="work_papers_regularize",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=8,
        branches=("giratoria",),
    ),
    Event(
        "youtuber_madrid",
        "📹 El youtuber que te contrató para mudarse a Andorra sale en directo… desde su "
        "salón de Madrid, con los coches de la Castellana de fondo.",
        (
            (
                "Un vuelo de atrezo",
                Outcome(
                    "Le montas un vuelo semanal de ida y vuelta a Andorra, con tarjeta de "
                    "embarque y todo. En clase turista, para que sea creíble.",
                    pay=0.4,
                    stat="work_ytb_prop_flight",
                ),
            ),
            (
                "Rescindir el contrato",
                Outcome(
                    "Le rescindes el contrato antes de que Hacienda llame por él. Pierdes un "
                    "cliente, pero ganas una noche de sueño.",
                    performance=-5,
                    family=5,
                    stat="work_ytb_dropped",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=7,
        branches=("giratoria",),
    ),
    Event(
        "carta_padron",
        "📨 Llega una carta de la Agencia Tributaria: preguntan por qué el padrón andorrano "
        "de tu cliente tiene una casa vacía y catorce recibos de Madrid.",
        (
            (
                "Ochenta folios",
                Outcome(
                    "Contestas con ochenta folios, certificados y una foto con nieve. "
                    "Tardarán un año en leerlos y para entonces todos serán felices.",
                    performance=5,
                    battery=-15,
                    stat="work_padron_answer",
                ),
            ),
            (
                "El cajón del «ya contestaré»",
                Outcome(
                    "La metes en el cajón de «ya contestaré». Al cajón, no al buzón: el "
                    "plazo corre igual.",
                    fine=0.8,
                    risk=0.3,
                    caught_by="hacienda",
                    stat="work_padron_ignore",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=9,
        branches=("giratoria",),
    ),
    Event(
        "fundacion_sucesiones",
        "🏰 Un cliente de 82 años no quiere que su herencia pague Sucesiones. «Móntame una "
        "fundación», dice, con el palacete bajo el brazo.",
        (
            (
                "Montar la fundación",
                Outcome(
                    "Creas la Fundación Amigos del Mus y los Caballos, con sede en el "
                    "palacete. Él, presidente vitalicio; su nieto, patrono.",
                    pay=0.5,
                    fine=0.8,
                    risk=0.15,
                    caught_by="fiscalia",
                    stat="work_foundation_made",
                ),
            ),
            (
                "Que pague Sucesiones",
                Outcome(
                    "«Que pague lo que toca.» Se queja, firma, y tres meses después te "
                    "despide… de la herencia.",
                    performance=-10,
                    family=5,
                    stat="work_foundation_refused",
                ),
            ),
        ),
        jobs=("funcionariado",),
        min_level=10,
        branches=("giratoria",),
    ),
    # -- Influencer -----------------------------------------------------------------------
    Event(
        "funa",
        "🔥 Te están funando: alguien ha encontrado un tuit tuyo de 2014.",
        (
            (
                "Vídeo pidiendo perdón",
                Outcome(
                    "Subes un vídeo llorando con sudadera gris. Pierdes seguidores, pero menos.",
                    followers=-0.1,
                    performance=-5,
                    stat="work_apology",
                ),
            ),
            (
                "Doblo la apuesta",
                Outcome(
                    "Dices que es la «cultura de la cancelación». Te funan más fuerte.",
                    followers=-0.3,
                    performance=5,
                    stat="work_cancelled",
                ),
            ),
        ),
        jobs=("influencer",),
    ),
    Event(
        "andorra",
        "🏔️ Tu agencia te propone irte a vivir a Andorra «por el clima».",
        (
            (
                "Me voy",
                Outcome(
                    "Te vas una semana. Hace frío, no hay playa y echas de menos el gofio. "
                    "Vuelves sin decir nada a nadie.",
                    family=-10,
                    battery=-10,
                    stat="work_andorra",
                ),
            ),
            (
                "Me quedo",
                Outcome(
                    "Te quedas en Canarias pagando lo tuyo. Hacienda te manda un corazón.",
                    family=5,
                    stat="work_andorra_no",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=3,
    ),
    Event(
        "publi_encubierta",
        "📦 Una marca te paga por enseñar su crema, pero sin poner que es publicidad.",
        (
            (
                "Pongo #publi",
                Outcome(
                    "Pones #publi. Cobras y duermes tranquilo.",
                    pay=0.3,
                    stat="work_ad_tagged",
                ),
            ),
            (
                "Sin #publi",
                Outcome(
                    "Lo cuelas como si te encantara de verdad.",
                    black=0.5,
                    risk=0.2,
                    caught_by="consumo",
                    stat="work_hidden_ad",
                ),
            ),
        ),
        jobs=("influencer",),
    ),
    Event(
        "regalo_marca",
        "🎁 Te llega a casa una caja enorme de una marca: un móvil nuevo «de regalo».",
        (
            (
                "Lo declaro",
                Outcome(
                    "Lo declaras: un regalo por publicidad es renta en especie y tributa.",
                    pay=0.2,
                    stat="work_gift_declared",
                ),
            ),
            (
                "Calladito",
                Outcome(
                    "Lo estrenas en un story y no le dices nada a nadie (salvo a tus seguidores).",
                    black=0.4,
                    risk=0.15,
                    caught_by="regalos",
                    stat="work_gift_hidden",
                ),
            ),
        ),
        jobs=("influencer",),
    ),
    Event(
        "colaboracion_amiga",
        "🤝 Otro influencer te propone un vídeo juntos para «ayudarnos a crecer».",
        (
            (
                "Hecho",
                Outcome(
                    "Hacéis un baile juntos. Sus seguidores te descubren.",
                    followers=0.1,
                    battery=-5,
                    stat="work_collab",
                ),
            ),
            ("Paso", Outcome("Pasas. Él lo hace con otro y se hace viral.", performance=-5)),
        ),
        jobs=("influencer",),
    ),
    # -- Influencer: el tronco (más marrones) -----------------------------------------------
    Event(
        "sorteo_falso",
        "🎁 Tu cuenta lleva una semana planchada. Un colega te susurra: «Sortea un móvil: para "
        "participar hay que seguirte, compartir y etiquetar a tres amigos». El móvil no existe.",
        (
            (
                "Sorteo (de un mango)",
                Outcome(
                    "Sorteas un mango. «Everybody want mango, American people want mango.» "
                    "Medio millón de etiquetas, cero móviles y un ganador que resulta ser tu "
                    "primo.",
                    followers=0.3,
                    black=0.2,
                    risk=0.2,
                    caught_by="ordenacion_juego",
                    stat="work_giveaway_fake",
                ),
            ),
            (
                "Sorteo de verdad",
                Outcome(
                    "Compras el móvil, llamas a un notario y sorteas. Lo gana una señora de "
                    "Albacete que te manda un bizcocho. Ganas 40 seguidores y un bizcocho.",
                    followers=0.03,
                    battery=-5,
                    family=5,
                    stat="work_giveaway_real",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=2,
    ),
    Event(
        "reto_viral",
        "🛒 Te retan a bajar una cuesta en un carrito del súper, grabado en vertical, «para el "
        "algoritmo». Hay una cuesta, un carrito y un vecino que ha sacado la silla a mirar.",
        (
            (
                "Me tiro",
                Outcome(
                    "Bajas la cuesta gritando «¡Yo soy yo… y así me tienes que soportar!». "
                    "Tres millones de visitas y una escayola con firmas.",
                    followers=0.3,
                    battery=-20,
                    family=-5,
                    stat="work_challenge_done",
                ),
            ),
            (
                "Con un doble",
                Outcome(
                    "Lo hace un doble de riesgo con tu peluca. Queda perfecto y el vídeo "
                    "arrasa, hasta que alguien se fija en que el doble tiene bigote.",
                    followers=0.1,
                    performance=-5,
                    stat="work_challenge_stunt",
                ),
            ),
        ),
        jobs=("influencer",),
    ),
    Event(
        "haul_ropa",
        "📦 Te llegan 40 prendas de una web de ropa a 200 Y$. Hay que hacer el haul: se "
        "destiñen en la caja, huelen a plástico y la talla M le va bien a tu sobrino de seis.",
        (
            (
                "Haul sincero",
                Outcome(
                    "Lo enseñas todo, con la camiseta abriéndose por la mitad en directo. "
                    "Pierdes la publi, pero el vídeo se llama «Esto es un robo» y lo ve medio "
                    "país.",
                    followers=0.15,
                    stat="work_haul_honest",
                ),
            ),
            (
                "Haul con filtros",
                Outcome(
                    "Con tres filtros y la luz justa todo parece de diseñador. Un seguidor "
                    "pregunta por qué la cremallera está en la manga. Tú: «Es tendencia».",
                    pay=0.2,
                    followers=-0.05,
                    stat="work_haul_filters",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=2,
    ),
    Event(
        "marca_en_producto",
        "🧴 Una marca te propone pagarte «en producto»: 300 botes de champú y una factura "
        "de «visibilidad». En tu baño ya no cabe ni el espejo.",
        (
            (
                "Acepto el champú",
                Outcome(
                    "Aceptas. El piso huele a manzana durante un mes y pagas la hipoteca con "
                    "«visibilidad». Tu casero no acepta el champú.",
                    followers=0.05,
                    family=-5,
                    stat="work_paid_in_kind",
                ),
            ),
            (
                "Quiero yapdollars",
                Outcome(
                    "«El champú no paga el alquiler.» La marca se lo piensa y te paga un "
                    "tercio en yapdollars, y de regalo, un bote.",
                    pay=0.3,
                    stat="work_paid_in_cash",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=2,
    ),
    Event(
        "politico_baile",
        "🗳️ Un candidato en campaña te propone un vídeo juntos bailando, «para conectar con "
        "los jóvenes». Sabe tres pasos y los baila con la cara de quien los ha ensayado en "
        "el coche oficial.",
        (
            (
                "Bailo con él",
                Outcome(
                    "Bailáis y cobras en B «para no mezclar». El vídeo tiene dos millones de "
                    "visitas y el candidato, tres votos nuevos (los de tu familia).",
                    followers=0.2,
                    black=0.4,
                    risk=0.15,
                    caught_by="campana",
                    stat="work_politician_dance",
                ),
            ),
            (
                "Paso de política",
                Outcome(
                    "«Yo no hago política.» Tu abuela te lo agradece y el candidato te "
                    "bloquea, por lo que más de un partido ya te tiene fichado.",
                    followers=-0.03,
                    family=5,
                    stat="work_politician_refused",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=3,
    ),
    Event(
        "chiste_viejo",
        "🧨 Alguien ha desenterrado un chiste tuyo de hace diez años. Entonces tenía poca gracia "
        "y hoy tiene 400.000 retuits y un hashtag con tu nombre.",
        (
            (
                "Pido perdón",
                Outcome(
                    "Otra vez la sudadera gris y los ojos rojos de cebolla. Nadie te cree, "
                    "pero te dejan en paz el fin de semana.",
                    followers=-0.1,
                    performance=-5,
                    stat="work_old_joke_apology",
                ),
            ),
            (
                "Lo reivindico",
                Outcome(
                    "«Era humor, y ahora todo es un delito.» Mitad te cancela, mitad te "
                    "idolatra y un partido te ofrece ir en listas.",
                    followers=0.15,
                    performance=-10,
                    stat="work_old_joke_defended",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=4,
    ),
    # -- Influencer: Tele ---------------------------------------------------------------------
    Event(
        "tertulia_virologia",
        "🦠 A las ocho te piden opinar de virología. A las nueve, de fútbol. A las diez, de "
        "la guerra. Eres licenciado en Turismo y el productor te pasa un café con cara de "
        "«tú puedes».",
        (
            (
                "Opino de todo",
                Outcome(
                    "Opinas de la vacuna, del fuera de juego y de Ucrania con la misma voz "
                    "grave. Te llaman «el analista» y tu madre pone la tele más alta.",
                    followers=0.15,
                    performance=5,
                    battery=-10,
                    stat="work_opine_all",
                ),
            ),
            (
                "Digo que no soy experto",
                Outcome(
                    "«No soy experto.» El productor te mira como si hubieras blasfemado y al "
                    "día siguiente la silla es de otro, más joven y más seguro de sí mismo.",
                    followers=-0.05,
                    performance=-15,
                    family=5,
                    stat="work_not_expert",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=6,
        branches=("tele",),
    ),
    Event(
        "autobombo_telediario",
        "📰 Informativos te ofrece cuatro minutos en el telediario de la propia cadena sobre tu "
        "programa. «Es información, no publicidad», dice el director, sin que se le mueva "
        "una ceja.",
        (
            (
                "Que salga",
                Outcome(
                    "Cuatro minutos antes de la sequía. La audiencia sube, el defensor del "
                    "espectador baja a la oficina y tú cobras un plus «por imagen».",
                    followers=0.25,
                    black=0.4,
                    risk=0.15,
                    caught_by="consumo",
                    stat="work_selfpromo",
                ),
            ),
            (
                "Esto es autobombo",
                Outcome(
                    "«Esto es autobombo.» El director anota algo: «Con lo bien que se portaba "
                    "el anterior». Esa semana sales en el telediario, pero como oyente.",
                    followers=-0.05,
                    performance=-10,
                    stat="work_selfpromo_no",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=7,
        branches=("tele",),
    ),
    Event(
        "exclusiva_famoso",
        "📸 Un famoso te vende por 2.000.000 Y$ la exclusiva de su separación. Esa misma tarde, "
        "su ex llama a la cadena con voz de abogado y el tono de quien ya ha redactado la "
        "demanda.",
        (
            (
                "Compro la exclusiva",
                Outcome(
                    "Pagas, emites y el programa bate récord de audiencia. Dos semanas "
                    "después, la ex te demanda por intromisión en el honor. «Yo soy yo… y así "
                    "me tienes que soportar.»",
                    followers=0.3,
                    battery=-10,
                    family=-5,
                    fine=0.6,
                    risk=0.25,
                    caught_by="honor",
                    stat="work_exclusive_bought",
                ),
            ),
            (
                "No la compro",
                Outcome(
                    "La compra la otra cadena y bate récord con la historia. En tu comité "
                    "alguien murmura «esto con el anterior no pasaba». Tú duermes bien.",
                    performance=-15,
                    family=5,
                    stat="work_exclusive_refused",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=8,
        branches=("tele",),
    ),
    Event(
        "consejero_cuota",
        "🪑 Un consejero de la cadena, sentado ahí por cuota de su partido, te pide que saques "
        "en tu programa «a los suyos». «Es una cuestión de pluralidad», dice, y se ajusta la "
        "corbata del color del partido.",
        (
            (
                "Pluralidad",
                Outcome(
                    "Sacas a tres de los suyos en el sofá y a uno del otro partido, a las "
                    "tres de la madrugada. Pluralidad total, y te renuevan el contrato.",
                    pay=0.2,
                    performance=15,
                    stat="work_quota_obeyed",
                ),
            ),
            (
                "Aquí no se cuela nadie",
                Outcome(
                    "«Aquí no se cuela nadie.» Dos semanas después tu programa está a las "
                    "tres de la madrugada, entre la teletienda y la carta de ajuste.",
                    followers=-0.1,
                    performance=-20,
                    stat="work_quota_refused",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=8,
        branches=("tele",),
    ),
    Event(
        "campanadas_vestido",
        "🔔 Te toca dar las Campanadas. Tu estilista te trae un vestido «atrevido» (de esos "
        "que no dejan tragar las uvas) y el director, un traje gris «por si acaso».",
        (
            (
                "El vestido polémico",
                Outcome(
                    "Doce uvas, un escote y diez millones de espectadores. Al día siguiente "
                    "no se habla de otra cosa, y tu abuela te llama para decirte que «ya "
                    "está bien».",
                    followers=0.4,
                    performance=-5,
                    family=-5,
                    stat="work_dress_bold",
                ),
            ),
            (
                "El traje gris",
                Outcome(
                    "Un traje discreto, las uvas en orden y cero polémica. Nadie habla de ti. "
                    "Nadie recuerda quién dio las Campanadas, ni tú.",
                    followers=-0.1,
                    performance=5,
                    stat="work_dress_safe",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=9,
        branches=("tele",),
    ),
    Event(
        "eurovision_boicot",
        "🎤 Eurovisión se acerca y un grupo de países habla de boicot. La cadena tiene que "
        "decidir si va o no, y tú vas a decidirlo sin mirar quién queda penúltimo.",
        (
            (
                "Vamos, con bailarines",
                Outcome(
                    "Vamos. Una balada sobre la paz con bailarines en bañador y una "
                    "bandera gigante. España queda penúltima, como siempre; la audiencia, "
                    "primera.",
                    followers=0.1,
                    performance=10,
                    stat="work_eurovision_go",
                ),
            ),
            (
                "Boicot",
                Outcome(
                    "Anuncias el boicot en el telediario. Tres días de portadas, una carta "
                    "de protesta del festival y un cartel de «cobarde» en la prensa de "
                    "enfrente.",
                    followers=0.2,
                    performance=-10,
                    family=5,
                    stat="work_eurovision_boycott",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=10,
        branches=("tele",),
    ),
    # -- Influencer: Andorra --------------------------------------------------------------------
    Event(
        "carta_residencia",
        "📨 Llega una carta de Hacienda: «Acredite su residencia en Andorra». Dudan de que "
        "vivas allí desde que subiste un story con un cocido madrileño de fondo.",
        (
            (
                "Recibos y fotos con nieve",
                Outcome(
                    "Contestas con el recibo de la luz de un piso vacío y una foto con "
                    "nieve de hace seis años. Hacienda lo mira en silencio, que es lo peor.",
                    followers=0.05,
                    fine=0.8,
                    risk=0.3,
                    caught_by="hacienda",
                    stat="work_residence_papers",
                ),
            ),
            (
                "Regularizo",
                Outcome(
                    "Reconoces que vives en España y regularizas. Duele, pero duermes "
                    "tranquilo (y con la persiana subida).",
                    performance=-5,
                    family=5,
                    battery=-5,
                    stat="work_residence_regularized",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=6,
        branches=("andorra",),
    ),
    Event(
        "directo_gran_via",
        "📹 En pleno directo pasa un autobús al fondo, con el rótulo de «Gran Vía». El chat "
        "ruge: «¿No vivías en Andorra?».",
        (
            (
                "Es un fondo virtual",
                Outcome(
                    "«Es un fondo virtual de una aplicación.» El chat te llama mentiroso "
                    "con emojis y bate récord de espectadores. Hacienda también tiene la "
                    "grabación.",
                    followers=0.2,
                    fine=0.6,
                    risk=0.25,
                    caught_by="hacienda",
                    stat="work_gran_via_fake",
                ),
            ),
            (
                "Vengo de visita",
                Outcome(
                    "«Vengo a ver a mi madre, tres días.» El chat no te cree, tu madre sí, "
                    "y alguien ya ha pillado el recibo de la luz del piso de Andorra.",
                    followers=-0.1,
                    family=5,
                    stat="work_gran_via_visit",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=7,
        branches=("andorra",),
    ),
    Event(
        "energetica_taquicardia",
        "⚡ Un cliente cuenta en redes que tu bebida energética le dio una taquicardia. "
        "Lleva 800.000 visualizaciones y el vídeo se titula «Casi me muero por 199 Y$».",
        (
            (
                "Letra pequeña en grande",
                Outcome(
                    "Pones «no apto para menores, embarazadas, ni para nadie con corazón» "
                    "en letras gigantes. Las ventas suben: ahora es una bebida «peligrosa».",
                    pay=0.2,
                    followers=0.1,
                    stat="work_energy_warning",
                ),
            ),
            (
                "Lo niego todo",
                Outcome(
                    "«Es sabor frambuesa, no taquicardia.» Cuatro días después, Consumo "
                    "abre un expediente por el etiquetado. El cliente ya es influencer.",
                    followers=-0.05,
                    fine=0.8,
                    risk=0.3,
                    caught_by="etiquetado",
                    stat="work_energy_denied",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=7,
        branches=("andorra",),
    ),
    Event(
        "velada_caida",
        "🥊 En tu velada de boxeo, tu streamer estrella se cae solo en el primer asalto, "
        "antes de que nadie le toque. Hay 12.000 personas en el recinto y 400.000 en directo.",
        (
            (
                "Fue un golpe invisible",
                Outcome(
                    "Dices que ha sido «un golpe invisible, de los que solo ve el VAR». El vídeo "
                    "de la caída tiene más visitas que el resto del cartel.",
                    followers=0.25,
                    battery=-10,
                    stat="work_invisible_punch",
                ),
            ),
            (
                "Devuelvo las entradas",
                Outcome(
                    "Devuelves las entradas y organizas una revancha gratis. Pierdes la "
                    "mitad de la caja y ganas aplausos, que no pagan el alquiler del recinto.",
                    performance=10,
                    family=5,
                    stat="work_boxing_refund",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=8,
        branches=("andorra",),
    ),
    Event(
        "rug_pull_propio",
        "🪙 Tu propia moneda lleva un año sin valer nada. Un fondo ofrece comprarla y tú "
        "tienes el 90 % de los tokens. En el chat de Telegram, 40.000 «inversores» "
        "escriben «hold».",
        (
            (
                "Vendo y desaparezco",
                Outcome(
                    "Vendes todo en diez minutos y subes un story en una playa. La CNMV "
                    "no tarda en preguntar. Tus 40.000 inversores, tampoco.",
                    black=1.0,
                    risk=0.3,
                    caught_by="cnmv",
                    followers=-0.5,
                    stat="work_rugpull_own",
                ),
            ),
            (
                "Doy la cara",
                Outcome(
                    "Das la cara en un directo de tres horas y un «hold» heroico. Pierdes "
                    "dinero y ganas respeto, que no se cobra pero luce en el currículum.",
                    followers=-0.05,
                    performance=10,
                    family=-5,
                    stat="work_rugpull_faced",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=9,
        branches=("andorra",),
    ),
    Event(
        "mitin_directo",
        "🎙️ Estás dando un mitin por directo: 6.000 personas en el chat y 14 en la sala (tu "
        "cuñado, su perro y doce becarios). Hay un bulo que dispara los números.",
        (
            (
                "Con el bulo",
                Outcome(
                    "Lanzas el bulo. «¡Se acabó la fiesta!», gritas, y al acabar organizas "
                    "una fiesta con 400 personas. La inmunidad te protege, tu cuñado graba.",
                    followers=0.4,
                    fine=0.7,
                    risk=0.25,
                    caught_by="suplicatorio",
                    stat="work_rally_hoax",
                ),
            ),
            (
                "Sin bulos",
                Outcome(
                    "Hablas de cifras y de leyes. A los diez minutos quedan 7 personas en "
                    "el chat: tu madre, su amiga y cinco bots rusos que se aburren.",
                    followers=-0.1,
                    performance=-10,
                    stat="work_rally_clean",
                ),
            ),
        ),
        jobs=("influencer",),
        min_level=10,
        branches=("andorra",),
    ),
    # -- Política: Moncloa --------------------------------------------------------------
    Event(
        "mocion",
        "🗳️ Registran una moción de censura contra el Gobierno. Esta tarde cuentan los votos.",
        (
            (
                "Aguantar",
                Outcome(
                    "Aguantas con tres cafés, dos llamadas a los socios y una promesa de tren. "
                    "La moción decae y Ferraz te debe una.",
                    pay=0.5,
                    performance=20,
                    battery=-20,
                    stat="work_censure_survived",
                ),
            ),
            (
                "Elecciones por sorpresa",
                Outcome(
                    "Convocas elecciones a las seis de la mañana. Nadie lo vio venir, ni tú.",
                    pay=1.0,
                    performance=10,
                    battery=-25,
                    family=-5,
                    stat="work_snap_election",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("moncloa",),
    ),
    Event(
        "socio",
        "🤝 Un socio de la investidura pide algo a cambio de sus votos. «Es una cuestión "
        "de Estado», dice.",
        (
            (
                "Ceder",
                Outcome(
                    "Cedes. Los votos llegan, la factura la pagará otro Gobierno.",
                    pay=0.5,
                    performance=15,
                    stat="work_partner_deal",
                ),
            ),
            (
                "Plantarse",
                Outcome(
                    "Te plantas. El socio vota con la oposición y en Ferraz te miran fatal.",
                    performance=-20,
                    battery=-10,
                    stat="work_partner_refused",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("moncloa",),
    ),
    Event(
        "cis",
        "📊 Te sugieren «afinar» la encuesta del CIS antes de que salga publicada.",
        (
            (
                "La cocino",
                Outcome(
                    "Cocinas la encuesta a fuego lento. Sale una ventaja de diez puntos y "
                    "ningún sociólogo la entiende.",
                    pay=0.6,
                    performance=20,
                    stat="work_cis",
                ),
            ),
            (
                "Sin tocar",
                Outcome(
                    "La publicas tal cual. Sale fatal y el titular es tuyo.",
                    performance=-10,
                    stat="work_cis_honest",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("moncloa",),
    ),
    Event(
        "omnibus",
        "📦 Hay veinte medidas pendientes y poco tiempo. Alguien susurra: «Un ómnibus y a correr».",
        (
            (
                "Todo al ómnibus",
                Outcome(
                    "Metes pensiones, vivienda, una ley de pesca y un indulto en un solo "
                    "decreto. Nadie lo ha leído entero.",
                    pay=0.7,
                    performance=15,
                    battery=-10,
                    stat="work_omnibus",
                ),
            ),
            (
                "Por separado",
                Outcome(
                    "Tramitas cada ley por su cuenta. Es correcto, lento y nadie te lo agradece.",
                    performance=-5,
                    battery=-15,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("moncloa",),
    ),
    # -- Política: Bruselas -------------------------------------------------------------
    Event(
        "qatargate",
        "💼 Un enviado de un emirato te deja un maletín «para estrechar lazos entre pueblos».",
        (
            (
                "Lo cojo",
                Outcome(
                    "Lo cargas en el coche. Pesa lo que pesan las buenas relaciones "
                    "internacionales.",
                    black=3.0,
                    risk=0.3,
                    caught_by="olaf",
                    stat="work_suitcase",
                ),
            ),
            (
                "Lo rechazo",
                Outcome(
                    "Lo devuelves y lo cuentas en el registro de transparencia. Parece que "
                    "has perdido el norte.",
                    performance=-10,
                    stat="work_suitcase_refused",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("bruselas",),
    ),
    Event(
        "estrasburgo",
        "🚆 Toca el traslado mensual a Estrasburgo: cuatro mil funcionarios, ciento "
        "ochenta cajas de documentos y un tren.",
        (
            (
                "Voy con todos",
                Outcome(
                    "Viajas con la comitiva y las cajas. Cuando llegas, ya es el pleno siguiente.",
                    battery=-25,
                    family=-5,
                    stat="work_strasbourg",
                ),
            ),
            (
                "Me quedo en Bruselas",
                Outcome(
                    "Te quedas en Bruselas y lo sigues por pantalla. En casa respiran.",
                    performance=-10,
                    battery=-10,
                    family=5,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("bruselas",),
    ),
    Event(
        "dietas",
        "✍️ El pleno es largo y las dietas se cobran con solo firmar en el registro de la entrada.",
        (
            (
                "Firmo y me voy",
                Outcome(
                    "Firmas, cobras la dieta y desapareces entre los tulipanes. Nadie "
                    "lo comprueba, casi nadie.",
                    black=0.5,
                    risk=0.25,
                    caught_by="olaf",
                    performance=-5,
                    stat="work_per_diem",
                ),
            ),
            (
                "Me quedo al pleno",
                Outcome(
                    "Te quedas. Aprendes lo que es una votación por partes y no cobras extra.",
                    performance=15,
                    battery=-15,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("bruselas",),
    ),
    Event(
        "cena_lobby",
        "🍽️ Un lobby te invita a una cena de cuatro platos en un restaurante con "
        "estrella. «Solo para conocernos».",
        (
            (
                "Acepto",
                Outcome(
                    "Cenas, brindas y sales sin haber firmado nada. La cuenta la paga "
                    "un sector entero.",
                    performance=10,
                    battery=-10,
                    family=-3,
                    stat="work_lobby_dinner",
                ),
            ),
            (
                "Declino",
                Outcome(
                    "Te vas a casa con un bocadillo. Tu asistente te mira con tristeza.",
                    performance=-5,
                    family=5,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("bruselas",),
    ),
    Event(
        "exhumacion",
        "⚰️ Faltan tres meses para las elecciones y alguien propone otra exhumación «histórica».",
        (
            (
                "Con helicóptero",
                Outcome(
                    "Helicóptero, cámaras y directo como en el NO-DO. Franco sigue muerto y tú "
                    "subes dos puntos en el CIS.",
                    pay=0.5,
                    performance=15,
                    stat="work_exhumation",
                ),
            ),
            (
                "No toca",
                Outcome(
                    "Lo dejas para después de las elecciones. Tu jefe de campaña llora en el baño.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("moncloa",),
    ),
    Event(
        "waterloo",
        "🧳 Para sacar la próxima ley te faltan siete votos, y se deciden en Waterloo.",
        (
            (
                "Voy a Waterloo",
                Outcome(
                    "Viajas a Bélgica, te hacen esperar en la puerta y vuelves con los votos "
                    "y una lista de deberes más larga que el Estatut.",
                    pay=0.5,
                    performance=15,
                    battery=-15,
                    family=-5,
                    stat="work_waterloo",
                ),
            ),
            (
                "Que vengan ellos",
                Outcome(
                    "Esperas sentado. Los votos no llegan y la ley, tampoco.",
                    performance=-15,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=7,
        branches=("moncloa",),
    ),
    Event(
        "carta",
        "✉️ Te sientes acosado por la prensa. Tu equipo propone una carta a la ciudadanía "
        "y cinco días de reflexión.",
        (
            (
                "Escribo la carta",
                Outcome(
                    "Escribes la carta, desapareces cinco días y vuelves más fuerte. El país, "
                    "en vilo; el resultado, el de siempre.",
                    pay=0.5,
                    performance=20,
                    family=10,
                    stat="work_letter",
                ),
            ),
            (
                "Sigo trabajando",
                Outcome(
                    "Sigues al pie del cañón. Ni una portada te dedican.",
                    performance=-5,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=9,
        branches=("moncloa",),
    ),
    Event(
        "aranceles",
        "🇺🇸 Trump anuncia aranceles del 200 % al aceite, al vino y a los quesos europeos.",
        (
            (
                "Respuesta firme",
                Outcome(
                    "Aranceles espejo a las motos y al bourbon. Trump te llama «tremendous» y "
                    "sube otro 100 %.",
                    pay=0.5,
                    performance=10,
                    battery=-10,
                    stat="work_tariffs",
                ),
            ),
            (
                "Llamar al Despacho Oval",
                Outcome(
                    "Llamas, te ponen en espera con el himno de fondo y cuelgas sin hablar con "
                    "nadie.",
                    performance=-5,
                    battery=-5,
                    stat="work_oval_office",
                ),
            ),
        ),
        jobs=("politica",),
        min_level=7,
        branches=("bruselas",),
    ),
    Event(
        "tapon",
        "🍶 Toca votar si el tapón pegado a la botella llega también a los bricks de leche.",
        (
            (
                "Tapón para todos",
                Outcome(
                    "Aprobado. Medio continente se mancha la nariz al beber. Bruselas en estado "
                    "puro.",
                    performance=10,
                    stat="work_cap",
                ),
            ),
            (
                "Libertad de tapón",
                Outcome(
                    "Votas en contra. En tu grupo te llaman euroescéptico y te quitan el turno "
                    "de palabra.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("politica",),
        min_level=6,
        branches=("bruselas",),
    ),
    # -- Obra: Ladrillo -----------------------------------------------------------------
    Event(
        "burbuja",
        "📉 En la tele dicen que estalla la burbuja. Tienes trescientos pisos sin vender.",
        (
            (
                "Vender a un fondo",
                Outcome(
                    "Se los vendes todos a un fondo buitre a mitad de precio. Al mes los "
                    "alquila al doble.",
                    pay=0.6,
                    performance=-5,
                    stat="work_vulture",
                ),
            ),
            (
                "Aguantar",
                Outcome(
                    "«El ladrillo nunca baja». Aguantas con los pisos vacíos y el banco "
                    "llamando a diario.",
                    performance=15,
                    battery=-15,
                    family=-5,
                    stat="work_bubble_held",
                ),
            ),
        ),
        jobs=("obra",),
        min_level=6,
        branches=("ladrillo",),
    ),
    Event(
        "recalificacion",
        "🌲 El concejal de urbanismo te ofrece recalificar un pinar «a cambio de un detalle».",
        (
            (
                "Un detalle",
                Outcome(
                    "Un detalle en un sobre y el pinar ya es urbanizable. Las ardillas, "
                    "desahuciadas.",
                    black=2.5,
                    risk=0.3,
                    caught_by="uco",
                    stat="work_rezoning",
                ),
            ),
            (
                "Por la vía legal",
                Outcome(
                    "Pides la licencia por la vía legal. Te la darán en 2041.",
                    performance=-10,
                    stat="work_rezoning_refused",
                ),
            ),
        ),
        jobs=("obra",),
        min_level=6,
        branches=("ladrillo",),
    ),
    Event(
        "oligarca",
        "🛥️ Un oligarca quiere diez áticos al contado, en billetes de 500 y sin preguntas.",
        (
            (
                "Vendo",
                Outcome(
                    "Cuentas los billetes con una máquina que se calienta. Venta cerrada.",
                    pay=1.0,
                    risk=0.25,
                    fine=1.5,
                    caught_by="sepblac",
                    stat="work_oligarch",
                ),
            ),
            (
                "Transferencia o nada",
                Outcome(
                    "Le pides una transferencia con su origen. Se va a la promoción de al lado.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=7,
        branches=("ladrillo",),
    ),
    Event(
        "turisticos",
        "🧳 Un fondo quiere tu bloque nuevo entero para pisos turísticos.",
        (
            (
                "Todo turístico",
                Outcome(
                    "Cuarenta pisos con caja de llaves en el portal. Los vecinos de toda la "
                    "vida, a la periferia.",
                    pay=0.7,
                    family=-3,
                    stat="work_tourist_flats",
                ),
            ),
            (
                "Para el barrio",
                Outcome(
                    "Lo alquilas a gente del barrio. Ganas menos y te saludan por la calle.",
                    performance=-5,
                    family=5,
                    stat="work_social_rent",
                ),
            ),
        ),
        jobs=("obra",),
        min_level=6,
        branches=("ladrillo",),
    ),
    # -- Obra: Obra pública -------------------------------------------------------------
    Event(
        "modificado",
        "📈 La obra va por la mitad y el presupuesto ya está gastado.",
        (
            (
                "Modificado al canto",
                Outcome(
                    "Pides un modificado «por la geología». Te lo aprueban en una semana: el "
                    "ministerio también quiere inaugurar.",
                    pay=0.7,
                    performance=15,
                    stat="work_change_order",
                ),
            ),
            (
                "Acabar con lo que hay",
                Outcome(
                    "Terminas con lo que queda. La carretera acaba en un descampado.",
                    performance=-10,
                    battery=-15,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=6,
        branches=("publica",),
    ),
    Event(
        "inauguracion",
        "✂️ El ministro quiere inaugurar la autovía antes de las elecciones. Falta la mitad.",
        (
            (
                "Cortar la cinta",
                Outcome(
                    "Cinta, tijeras y foto en el kilómetro 3. El kilómetro 4 sigue siendo tierra.",
                    pay=0.5,
                    performance=15,
                    stat="work_ribbon",
                ),
            ),
            (
                "Cuando esté acabada",
                Outcome(
                    "Le dices que cuando esté acabada. El ministro deja de contestarte.",
                    performance=-10,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=6,
        branches=("publica",),
    ),
    Event(
        "aeropuerto",
        "✈️ Te encargan un aeropuerto en una provincia sin vuelos. Con estatua en la rotonda.",
        (
            (
                "Lo construyo",
                Outcome(
                    "Lo levantas en tiempo récord. La estatua mide veinticuatro metros; los "
                    "aviones, de momento, cero.",
                    pay=0.8,
                    performance=10,
                    stat="work_ghost_airport",
                ),
            ),
            (
                "Mejor un tren",
                Outcome(
                    "Propones un tren. Te miran como si hubieras pedido un concurso limpio.",
                    performance=-5,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=7,
        branches=("publica",),
    ),
    Event(
        "muro",
        "🧱 Trump te llama: quiere un muro en la frontera y jura que lo paga México.",
        (
            (
                "Por adelantado",
                Outcome(
                    "Presupuesto en dólares, cobrado por adelantado. Quién lo pague al final "
                    "ya no es cosa tuya.",
                    pay=1.0,
                    performance=10,
                    battery=-10,
                    stat="work_wall",
                ),
            ),
            (
                "Mejor un puente",
                Outcome(
                    "Le propones un puente. Te cuelga y te pone un arancel.",
                    performance=-5,
                    family=5,
                ),
            ),
        ),
        jobs=("obra",),
        min_level=8,
        branches=("publica",),
    ),
    # -- Oficina: el tronco (más marrones) --------------------------------------------------
    Event(
        "cambio_viernes",
        "🕖 Viernes, 19:00. El cliente escribe: «Es un pequeñísimo cambio, ¿lo tenéis para el "
        "lunes?». El pequeñísimo cambio es rehacer el módulo de pagos.",
        (
            (
                "Lo hago ahora",
                Outcome(
                    "Te quedas hasta las once. El cambio era pequeño: solo afectaba a todo. El "
                    "lunes, el cliente: «Gracias. ¿Y lo del botón azul?».",
                    pay=0.5,
                    battery=-20,
                    family=-10,
                    performance=10,
                    stat="work_small_change_done",
                ),
            ),
            (
                "Lo presupuesto",
                Outcome(
                    "Mandas un presupuesto de tres semanas con «pequeño cambio» entre comillas. "
                    "De repente, al cliente deja de parecerle urgente.",
                    performance=5,
                    stat="work_small_change_quoted",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=2,
    ),
    Event(
        "postit_clave",
        "🟨 Pasas junto al monitor de un compañero y ves la contraseña de producción en un "
        "pósit: «Admin123!!». Al lado, otro pósit: «No tocar».",
        (
            (
                "Se lo digo",
                Outcome(
                    "Se lo cuentas. Cambia la contraseña por «Admin124!!» y te da las gracias "
                    "con un bizcocho.",
                    performance=5,
                    stat="work_postit_told",
                ),
            ),
            (
                "Me la apunto",
                Outcome(
                    "Te la apuntas «por si algún día hace falta» y vendes un acceso de "
                    "«pruebas» a un conocido, en un sobre.",
                    black=0.3,
                    risk=0.2,
                    stat="work_postit_noted",
                ),
            ),
        ),
        jobs=("oficina",),
    ),
    Event(
        "ia_bug",
        "🤖 La IA que tu empresa os obliga a usar escribe el 80 % del código. Producción se cae "
        "y el jefe pregunta quién ha escrito esa función. En el commit pone tu nombre.",
        (
            (
                "Asumo el bug",
                Outcome(
                    "Dices «he sido yo». Mientras, la IA ya propone arreglarlo con otro error "
                    "nuevo, más elegante.",
                    performance=-5,
                    battery=-10,
                    stat="work_ai_blame_owned",
                ),
            ),
            (
                "Fue la IA",
                Outcome(
                    "«Ha sido la IA.» El jefe: «¿Y quién le dio permiso?». Tú. Tú se lo diste "
                    "con el botón de «aceptar todo».",
                    performance=-10,
                    stat="work_ai_blamed",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=3,
    ),
    Event(
        "hackathon",
        "🏆 Hackathon «voluntario» el sábado, de 9:00 a 9:00. Hay pizza, una pegatina y un "
        "premio de 20.000 Y$ para el mejor equipo (el del director, seguro).",
        (
            (
                "Voy",
                Outcome(
                    "Programas 24 horas seguidas y ganas el segundo premio: una pegatina. «Estoy "
                    "destruido, pero por fuera… por dentro estoy nuevo.» Mentira: por dentro "
                    "también.",
                    battery=-25,
                    family=-10,
                    performance=10,
                    stat="work_hackathon_went",
                ),
            ),
            (
                "Me quedo en casa",
                Outcome(
                    "Te quedas en casa. El lunes todos hablan de la pizza; tú, de tu sábado. "
                    "Nadie te pregunta.",
                    family=8,
                    performance=-5,
                    stat="work_hackathon_skipped",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=2,
    ),
    Event(
        "prueba_tecnica",
        "🧪 Te toca corregir la prueba técnica de un candidato: ocho horas, sin cobrar, «para "
        "ver cómo trabaja». El candidato ha entregado un módulo que casualmente necesitabais.",
        (
            (
                "Reutilizo el código",
                Outcome(
                    "Lo metes en producción «como inspiración». El candidato recibe un «gracias "
                    "por su tiempo» y tú, un aplauso en la reunión del lunes.",
                    pay=0.2,
                    performance=10,
                    stat="work_free_test_used",
                ),
            ),
            (
                "Que se pague",
                Outcome(
                    "Propones pagar la prueba. Recursos Humanos te mira como si hablaras en "
                    "arameo. El candidato te escribe: «gracias». Y se acabó.",
                    performance=-10,
                    stat="work_free_test_paid",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=4,
    ),
    Event(
        "alineamiento",
        "🗓️ Reunión de «alineamiento» de dos horas, dieciséis invitados y una agenda con un único "
        "punto: «Alinearnos». Hazme café… ¡quiero café!",
        (
            (
                "Participo",
                Outcome(
                    "Preguntas, tomas notas y propones un siguiente paso. Sales con tres "
                    "acciones asignadas, todas a ti.",
                    battery=-10,
                    performance=10,
                    stat="work_alignment_active",
                ),
            ),
            (
                "Cámara apagada",
                Outcome(
                    "Cámara apagada, micrófono en silencio y, mientras, avanzas trabajo de "
                    "verdad. Al final alguien: «¿Tú qué opinas?». Tú: «Estoy de acuerdo con lo "
                    "anterior».",
                    performance=-5,
                    stat="work_alignment_camera_off",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=3,
    ),
    # -- Oficina: Consultora ----------------------------------------------------------------
    Event(
        "horas_ministerio",
        "🧾 El Ministerio pide el informe de horas. Hay 1.200 horas facturadas y 400 "
        "trabajadas. Tu socio, bajito: «Que cuadre».",
        (
            (
                "Facturo las 1.200",
                Outcome(
                    "Facturas las 1.200: las trabajadas, las de la comida y las de un entierro "
                    "al que fue el cliente. Cobras tu parte en un sobre.",
                    black=0.6,
                    risk=0.2,
                    caught_by="tribunal_cuentas",
                    stat="work_ministry_hours_billed",
                ),
            ),
            (
                "Facturo las 400",
                Outcome(
                    "Facturas las 400 de verdad. A las ocho de la mañana, el socio te llama a su "
                    "despacho: «¿Esto es una broma?».",
                    performance=-10,
                    stat="work_ministry_hours_honest",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=6,
        branches=("consultora",),
    ),
    Event(
        "cita_sepe",
        "📲 La app de citas previas del SEPE sale al aire y se cae el primer día: 400.000 "
        "personas intentan pedir cita y la web dice «Servicio en mantenimiento». Es tu app.",
        (
            (
                "Culpo al servidor",
                Outcome(
                    "«Es un problema de infraestructura del cliente.» El cliente: «Pero si la "
                    "infraestructura la hicisteis vosotros». Tú: «De la versión anterior».",
                    performance=-5,
                    stat="work_sepe_server_blamed",
                ),
            ),
            (
                "Pido más presupuesto",
                Outcome(
                    "Presentas un plan «de mejora»: otros tres millones para arreglar lo que "
                    "acabas de entregar. Te lo aprueban; el cliente no tiene alternativa.",
                    pay=0.3,
                    battery=-10,
                    stat="work_sepe_budget_asked",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=7,
        branches=("consultora",),
    ),
    Event(
        "junior_senior",
        "🧑‍💼 El cliente paga por «consultor senior». Tu equipo disponible: un junior de 22 años "
        "con la corbata de su padre.",
        (
            (
                "Lo vendo como senior",
                Outcome(
                    "En el currículum pone «dos décadas de experiencia» (contando las del "
                    "colegio). El cliente paga tarifa de senior y recibe trabajo de junior.",
                    pay=0.4,
                    performance=5,
                    stat="work_junior_as_senior",
                ),
            ),
            (
                "Lo facturo como junior",
                Outcome(
                    "Facturas lo justo. El socio: «Eres una vergüenza para esta casa». El "
                    "cliente renueva por honradez y tú, sin comisión.",
                    performance=-10,
                    stat="work_junior_honest",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=6,
        branches=("consultora",),
    ),
    Event(
        "pliego_medida",
        "📜 Un cliente público te pide «orientar» el pliego de una licitación: «Solo detalles "
        "técnicos, como pedir 14 años de experiencia en una tecnología que tiene 12».",
        (
            (
                "Redacto el pliego",
                Outcome(
                    "Redactas un pliego con una cláusula que solo cumple tu empresa. Un sobre "
                    "por cabeza y «ni una palabra de esto».",
                    black=0.6,
                    risk=0.2,
                    caught_by="uco",
                    stat="work_tailored_tender",
                ),
            ),
            (
                "Pliego abierto",
                Outcome(
                    "Lo haces abierto a todo el mundo. Gana una empresa de Valladolid con un "
                    "40 % menos de presupuesto y tu socio no te habla en una semana.",
                    performance=-10,
                    stat="work_open_tender",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=8,
        branches=("consultora",),
    ),
    Event(
        "exministro_asesor",
        "🎩 Un exministro, recién salido del cargo, quiere entrar de «asesor externo» en la "
        "firma, con despacho propio y sueldo. Hace tres meses firmaba contratos contigo.",
        (
            (
                "Lo ficho",
                Outcome(
                    "Lo fichas de «asesor estratégico». Su primer consejo: «Ficha a mi cuñado». "
                    "El segundo: «No dejes nada por escrito».",
                    black=0.7,
                    risk=0.2,
                    caught_by="incompatibilidades",
                    stat="work_ex_minister_hired",
                ),
            ),
            (
                "Dos años de espera",
                Outcome(
                    "Le explicas que la ley pide dos años. Se ríe. El lunes ficha por la "
                    "competencia, con un despacho mayor.",
                    performance=-10,
                    stat="work_ex_minister_waited",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=9,
        branches=("consultora",),
    ),
    Event(
        "informe_informe",
        "🇪🇺 El Gobierno te encarga un informe sobre el informe del año pasado, con cargo a los "
        "fondos europeos: «Que sea más largo y con gráficos de colores».",
        (
            (
                "Lo hago",
                Outcome(
                    "Copias el original, le añades un capítulo de «metodología» y tres gráficos "
                    "de tarta. Bruselas paga 60.000.000 Y$ por un copiar y pegar.",
                    black=0.7,
                    risk=0.2,
                    caught_by="olaf",
                    stat="work_report_on_report",
                ),
            ),
            (
                "No hace falta",
                Outcome(
                    "Dices que el original ya servía. El Ministerio le pasa el encargo a otra "
                    "consultora, que cobra el doble por el mismo copiar y pegar.",
                    performance=-15,
                    stat="work_report_on_report_refused",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=10,
        branches=("consultora",),
    ),
    # -- Oficina: Unicornio ---------------------------------------------------------------
    Event(
        "despidos_zoom",
        "📹 Hay que despedir al 30 % del equipo. Tu abogado: «Mejor una videollamada de tres "
        "minutos, que es más eficiente».",
        (
            (
                "Videollamada de tres minutos",
                Outcome(
                    "Abres la llamada, anuncias los despidos en el minuto uno y te despides con "
                    "«mucha suerte en el futuro». A algunos aún les suena la sala de espera.",
                    pay=0.3,
                    performance=5,
                    family=-5,
                    stat="work_layoff_call",
                ),
            ),
            (
                "En persona",
                Outcome(
                    "Lo haces uno a uno, en persona. Acabas agotado, con 300 correos de "
                    "agradecimiento y 300 notas de queja de tu inversor.",
                    battery=-20,
                    family=-5,
                    performance=-5,
                    stat="work_layoff_face",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=6,
        branches=("unicornio",),
    ),
    Event(
        "valoracion_inflada",
        "📈 El fondo propone valorar tu startup en 900 millones. Tienes 14 empleados, un "
        "producto y un cliente (tu cuñado). El asesor: «Tú ve diciendo que sí».",
        (
            (
                "Acepto la valoración",
                Outcome(
                    "Aceptas 900 millones. Tu startup ya «vale» más que un banco y menos que tu "
                    "cuñado, que sigue siendo el cliente. Cobras tu parte en un sobre.",
                    black=0.7,
                    risk=0.2,
                    caught_by="cnmv",
                    stat="work_inflated_valuation",
                ),
            ),
            (
                "Valoración real",
                Outcome(
                    "Pides 12 millones, que es lo que vale. El fondo cuelga: «No entiende cómo "
                    "funciona esto». El del fondo de al lado levanta la mano: «Yo sí».",
                    performance=-10,
                    stat="work_realistic_valuation",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=7,
        branches=("unicornio",),
    ),
    Event(
        "sobrino_cto",
        "👨‍👦 Un inversor pone cinco millones con una condición: que su sobrino, de 19 años y "
        "«mucho potencial», sea tu CTO.",
        (
            (
                "Acepto",
                Outcome(
                    "El sobrino llega con dos portátiles, un monopatín y la idea de «reescribir "
                    "todo en un lenguaje nuevo». Tú lo vas revirtiendo cada noche.",
                    pay=0.3,
                    battery=-10,
                    stat="work_nephew_cto",
                ),
            ),
            (
                "Rechazo el dinero",
                Outcome(
                    "Dices que no. El inversor pone el dinero en tu competidor, y el sobrino, "
                    "de CTO. Rompe todo en tres meses y lo despiden. Tú no sonríes, pero casi.",
                    performance=-10,
                    stat="work_nephew_refused",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=8,
        branches=("unicornio",),
    ),
    Event(
        "fiesta_ibiza",
        "🏝️ Tu equipo quiere hacer la fiesta de la empresa en Ibiza, con DJ, barra libre y "
        "«sesión de team building». El presupuesto: lo que queda de la caja.",
        (
            (
                "Ibiza",
                Outcome(
                    "Ibiza. El DJ es el cuñado del CEO, la barra libre dura hasta las seis y el "
                    "team building acaba con un ingeniero en la piscina. ¡Jota Uve! ¡Pre!",
                    battery=-20,
                    family=-5,
                    performance=10,
                    stat="work_ibiza_party",
                ),
            ),
            (
                "Pizza en la oficina",
                Outcome(
                    "Pizza en la oficina: cuatro porciones por cabeza, un «viva» y a las diez en "
                    "casa. El equipo agradece la sobriedad… en las reseñas de la empresa.",
                    performance=-5,
                    family=5,
                    stat="work_ibiza_skipped",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=8,
        branches=("unicornio",),
    ),
    Event(
        "comprar_red",
        "🐦 A las tres de la madrugada, un tuit tuyo: «¿Y si compro la red social?». Tu "
        "equipo legal te escribe cinco minutos después: «Por favor, no».",
        (
            (
                "La compro",
                Outcome(
                    "Compras la red social por un tuit y, al día siguiente, descubres que hay "
                    "que pagarla de verdad. Despides a media plantilla y la rebautizas con una "
                    "letra.",
                    pay=0.5,
                    battery=-15,
                    family=-5,
                    performance=10,
                    stat="work_buy_network",
                ),
            ),
            (
                "Borro el tuit",
                Outcome(
                    "Borras el tuit. Ya hay 40.000 capturas y un meme: «El hombre que casi "
                    "compra».",
                    family=5,
                    stat="work_delete_tweet",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=9,
        branches=("unicornio",),
    ),
    Event(
        "sede_delaware",
        "🏛️ Tu asesor fiscal: «Muda la sede de la startup a Delaware: es más barato, hay menos "
        "papeleo y los fundadores seguís viviendo en Madrid».",
        (
            (
                "Sede en Delaware",
                Outcome(
                    "Mudas la sede a un buzón de Delaware. El buzón comparte oficina con 4.000 "
                    "empresas más y una planta.",
                    black=0.6,
                    risk=0.2,
                    caught_by="hacienda",
                    stat="work_delaware",
                ),
            ),
            (
                "Me quedo en España",
                Outcome(
                    "Te quedas, pagas aquí y presumes de ser «el único fundador que cotiza». "
                    "Tus inversores te preguntan si estás bien.",
                    performance=-5,
                    stat="work_stay_in_spain",
                ),
            ),
        ),
        jobs=("oficina",),
        min_level=7,
        branches=("unicornio",),
    ),
)

EVENT_BY_KEY: dict[str, Event] = {event.key: event for event in EVENTS}

#: Lo que pasa cuando la Inspección te pilla un B trasnochando (`work.NIGHT_SURCHARGE`).
NIGHT_CAUGHT_TEXT = (
    "🕵️ **Inspección de Trabajo, de noche.** Te pillan trasnochando en negro: devuelves el "
    "turno doble (lo cobrado y una multa igual) y te suspenden el IMV 3 días."
)
#: Qué pasa cuando la UCO o la Inspección te pillan con dinero en negro.
CAUGHT_TEXT: dict[str, str] = {
    "inspeccion": (
        "🕵️ **Inspección de Trabajo.** Te pillan cobrando en negro: devuelves lo cobrado con "
        "un recargo del 20 % y te suspenden el IMV 3 días."
    ),
    "uco": (
        "🚔 **La UCO llama a tu puerta a las seis de la mañana.** Estás imputado: devuelves "
        "lo del sobre con un 20 % de recargo y te quedas sin IMV 3 días."
    ),
    "expediente": (
        "📋 **Expediente disciplinario.** El hospital se entera del «congreso»: devuelves el "
        "viaje con un 20 % de recargo."
    ),
    "hacienda": (
        "📨 **Hacienda te regulariza.** Dice que tu centro de intereses económicos sigue en "
        "España (art. 9.1.b LIRPF) y te cobra la diferencia con recargo."
    ),
    "defensor": (
        "📮 **Queja al Diputado del Común.** El vecino del expediente escondido se ha quejado "
        "y te abren un expediente a ti: multa."
    ),
    "consumo": (
        "📢 **Consumo te expedienta por publicidad encubierta** (art. 26 de la Ley de "
        "Competencia Desleal): devuelves lo de la marca con un 20 % de recargo."
    ),
    "regalos": (
        "📨 **Hacienda cruza datos con la marca.** El regalo era renta en especie: lo "
        "regularizas con un 20 % de recargo."
    ),
    "sepblac": (
        "🏦 **El Sepblac (prevención del blanqueo) pregunta por los áticos.** Los billetes "
        "de 500 dejan rastro: multa por no identificar el origen del dinero."
    ),
    "olaf": (
        "🇪🇺 **La OLAF (Oficina Europea de Lucha contra el Fraude) abre una investigación.** "
        "Tras tres años de papeleo bilingüe, devuelves lo cobrado con un 20 % de recargo."
    ),
    "incompatibilidades": (
        "🏛️ **La Oficina de Conflictos de Intereses te abre expediente.** Lo que firmaste con "
        "el cargo aún caliente (un precontrato, un sillón vitalicio, una cesta con tarjeta) "
        "no era compatible: devuelves lo cobrado con un 20 % de recargo."
    ),
    "tribunal_cuentas": (
        "🧾 **El Tribunal de Cuentas te reclama el reintegro.** Un viaje a por pan que costó "
        "4.000 Y$ de gasolina, o mil doscientas horas facturadas de las que nadie "
        "trabajó cuatrocientas: las cuentas no cuadran, pagas el reintegro y el chófer cobra "
        "horas extra."
    ),
    "fiscalia": (
        "⚖️ **La Fiscalía Anticorrupción te cita como investigado.** El «homónimo» y la "
        "fundación no han convencido a nadie: pagas la sanción y tu cliente se busca otro "
        "asesor."
    ),
    "abusivas": (
        "⚖️ **Un juzgado declara abusiva la cláusula.** El asegurado reclamó con abogado: "
        "la clínica paga lo que cobró de más y una multa por cláusulas abusivas."
    ),
    "terraza": (
        "📏 **La policía local te multa por ocupación de vía pública.** Las tres mesas "
        "«decorativas» eran muy reales: pagas la sanción y ya conoces al agente por su nombre."
    ),
    "costas": (
        "🏖️ **Costas ejecuta la demolición subsidiaria.** Después de veinte años de recursos, "
        "la terraza cae en una tarde y la factura de la excavadora te llega a ti."
    ),
    "sanidad_alimentaria": (
        "🩺 **La Inspección de Sanidad cierra la cámara.** Los huevos tenían más historia que "
        "tu local: multa por no cumplir la cadena de frío y un acta con tu nombre."
    ),
    "ordenacion_juego": (
        "🎲 **La Dirección General de Ordenación del Juego te abre expediente.** Un sorteo sin "
        "premio, sin bases y sin notario es una rifa ilegal con filtro: devuelves lo cobrado "
        "con un 20 % de recargo."
    ),
    "campana": (
        "🗳️ **La Junta Electoral y el Tribunal de Cuentas cruzan los gastos de campaña.** "
        "El baile «espontáneo» con el candidato nunca se declaró: devuelves lo cobrado con un "
        "20 % de recargo."
    ),
    "honor": (
        "⚖️ **Un juzgado condena a la cadena por intromisión en el derecho al honor.** La "
        "exclusiva costó 2.000.000 Y$ y la sentencia, algunos más: pagas la indemnización y "
        "el famoso se compra un cuadro."
    ),
    "etiquetado": (
        "🥤 **Consumo y la AESAN inspeccionan tu bebida energética.** La cafeína no estaba "
        "donde la ley manda que esté y «sabor frambuesa» no es una advertencia: multa por "
        "etiquetado engañoso."
    ),
    "cnmv": (
        "🪙 **La CNMV te abre diligencias.** Un token sin folleto con el 90 % en tu cartera, o "
        "una startup «valorada» en 900 millones sobre un Excel y un cuñado: devuelves lo "
        "cobrado con un 20 % de recargo."
    ),
    "suplicatorio": (
        "🏛️ **El Parlamento Europeo estudia tu suplicatorio.** Tras seis meses de papeleo "
        "bilingüe te levantan la inmunidad solo para la multa: pagas, conservas el escaño y "
        "tu camiseta «Yo también soy perseguido» se agota."
    ),
    "junta_electoral": (
        "🗳️ **La Junta Electoral Central te abre expediente.** Los votos por correo "
        "viajaron con el buzoneo del súper y algunos llegaron después del recuento: multa, "
        "y una mención especial en la tertulia de las tres."
    ),
}
#: Pillados que además dejan sin IMV unos días.
CAUGHT_SUSPENDS_IMV = frozenset({"inspeccion", "uco"})

#: Probabilidad de que te indulten tras pillarte la UCO (no pagas la multa).
PARDON_CHANCE = 0.2

#: Pregunta después de que te pille la UCO.
RESIGN_EVENT = Event(
    "dimision",
    "🎙️ Te esperan los periodistas en la puerta de casa. «¿Va a dimitir?».",
    (
        (
            "Dimito",
            Outcome(
                "Dimites. Bajas un puesto, pero duermes tranquilo.",
                demote=True,
                family=10,
                stat="work_resigned",
            ),
        ),
        (
            "Ni de broma",
            Outcome(
                "«Dimitir no está en mi vocabulario». El sillón es tuyo.",
                performance=-20,
                stat="work_clinging",
            ),
        ),
    ),
    jobs=("politica",),
)


def events_for(
    job: str,
    level: int,
    family: int,
    *,
    abroad: bool = False,
    phase: str = "casa",
    branch: str = "",
) -> list[Event]:
    """Eventos que pueden salir al azar con este oficio, puesto, rama, familia y país."""
    return [
        event
        for event in EVENTS
        if not event.special
        and (not event.jobs or job in event.jobs)
        and (not event.branches or branch in event.branches)
        and level >= event.min_level
        and (not event.family_below or family < event.family_below)
        and (event.abroad is None or event.abroad == abroad)
        and (event.phase is None or event.phase == phase)
    ]


# -- Café ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Coffee:
    """Algo de la máquina de café: cuánto recarga y cuánto cuesta (base, sin IGIC)."""

    key: str
    name: str
    emoji: str
    battery: int
    price: int


#: Unas 8 veces lo que costaban hasta la 9.19.0 (14 y 25 Y$), cuando la batería salía
#: gratis: ahora encadenar cafés cuesta algo, sin que un café valga un turno. El
#: barraquito sale algo mejor por punto de batería (10 Y$ frente a 11).
COFFEES: tuple[Coffee, ...] = (
    Coffee("cortado", "Cortado", "☕", 10, 110),
    Coffee("barraquito", "Barraquito", "🥃", 20, 200),
)
COFFEE_BY_KEY: dict[str, Coffee] = {coffee.key: coffee for coffee in COFFEES}


# -- Familia: regalos y favores ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Gift:
    """Un regalo de la tienda (pasillo 🏠 La familia) que sube la familia en `pala`.

    Se compra en `tienda` como cualquier objeto y se da desde el panel de `pala`
    (se gasta). La clave es la del artículo en `bot.services.shop_catalog`.

    Attributes:
        family: Lo que sube la familia.
        cooldown: Segundos hasta poder dar otro igual (tu madre no quiere
            veinte ramos el mismo día).
    """

    key: str
    emoji: str
    name: str
    family: int
    cooldown: int


HOUR = 3600
DAY = 24 * HOUR

GIFTS: tuple[Gift, ...] = (
    Gift("bombones_mama", "🍫", "Caja de bombones", 6, 8 * HOUR),
    Gift("flores_mama", "💐", "Ramo para tu madre", 10, 12 * HOUR),
    Gift("pizza_familiar", "🍕", "Pizza familiar", 12, 12 * HOUR),
    Gift("tarta_abuela", "🎂", "Tarta de la abuela", 15, DAY),
    Gift("juguete_sobrino", "🧸", "Juguete para el sobrino", 20, 3 * DAY),
    Gift("entradas_concierto", "🎟️", "Entradas para un concierto", 20, 3 * DAY),
    Gift("cena_guachinche", "🍷", "Cena en un guachinche", 25, 4 * DAY),
    Gift("finde_sur", "🏖️", "Finde en el sur", 50, 7 * DAY),
)
GIFT_BY_KEY: dict[str, Gift] = {gift.key: gift for gift in GIFTS}


@dataclass(frozen=True, slots=True)
class Favor:
    """Algo que le pides a la familia desde `pala`: cuesta familia, no dinero.

    No se piden si dejarían la familia por debajo de `work.FAVOR_FLOOR`: quien
    ya la tiene en crisis no tiene a quién pedirle nada.

    Attributes:
        cost: Lo que baja la familia.
        cooldown: Segundos hasta poder pedirlo otra vez.
        text: Qué hace, para el desplegable.
        done: Lo que se cuenta al pedirlo.
    """

    key: str
    emoji: str
    name: str
    cost: int
    cooldown: int
    text: str
    done: str


FAVORS: tuple[Favor, ...] = (
    Favor(
        "tupper",
        "🍲",
        "El tupper de tu madre",
        15,
        20 * HOUR,
        "+30 de batería, sin temblores",
        "🍲 Tu madre te da un tupper de lentejas «para que comas algo caliente». +30 de batería.",
    ),
    Favor(
        "ninos",
        "💪",
        "Lo hago por los niños",
        15,
        12 * HOUR,
        "+15 % de tiempo en el próximo turno",
        "💪 Miras la foto de los niños en el móvil. El próximo turno va con +15 % de tiempo.",
    ),
    Favor(
        "cubre",
        "👵",
        "Te cubre tu madre",
        25,
        3 * DAY,
        "Quita el saliente de guardia",
        "👵 Tu madre llama al hospital: «Mi niño no puede, que tiene fiebre». Se acabó el "
        "saliente.",
    ),
    Favor(
        "cambio",
        "👯",
        "Tu hermana te cambia el turno",
        20,
        7 * DAY,
        "Un turno ordinario más hoy",
        "👯 Tu hermana se queda con los niños y te cambia el turno. Hoy tienes un turno "
        "ordinario más, sin extras ni B.",
    ),
    Favor(
        "excusa",
        "📞",
        "Excusa familiar",
        10,
        DAY,
        "Te libras del marrón que tengas delante",
        "📞 «Perdona, jefe, me llama mi madre». Te libras del marrón.",
    ),
    Favor(
        "bizum",
        "💸",
        "Bizum de mamá",
        20,
        7 * DAY,
        "Te adelanta un sueldo base; se devuelve con media nómina",
        "💸 Tu madre te manda un Bizum con el concepto «para que no pases fatigas».",
    ),
    Favor(
        "aval",
        "👴",
        "Tus padres te avalan",
        40,
        7 * DAY,
        "Avalan tu próxima hipoteca o alquiler de `pisos` (7 días)",
        "👴 Tus padres firman como avalistas «porque eres tú». Ponen el piso del pueblo de "
        "garantía. Durante 7 días el banco cuenta media pensión de tu padre y los caseros "
        "dejan de pedirte la garantía extra.",
    ),
)
FAVOR_BY_KEY: dict[str, Favor] = {favor.key: favor for favor in FAVORS}
#: Lo que sube la batería el tupper.
TUPPER_BATTERY = 30
#: Lo que da de tiempo «Lo hago por los niños», en tanto por uno.
MOTIVATED_TIME = 0.15
#: Lo que dura el aval de tus padres (el favor `aval`) y la marca de que ya se usó: avala
#: una sola operación de `pisos` (una hipoteca o un alquiler).
AVAL_SECONDS = 7 * DAY
AVAL_SPENT = "aval_gastado"
#: Lo que baja la familia si el banco ejecuta una hipoteca que avalaron tus padres.
AVAL_EXECUTION_FAMILY = 60
#: La rama que levanta la obra nueva de `pisos` cada lunes: quien haya fichado en ella en
#: los últimos `BUILDER_DAYS` días.
BUILDER_JOB = "obra"
BUILDER_BRANCH = "ladrillo"
BUILDER_DAYS = 7
