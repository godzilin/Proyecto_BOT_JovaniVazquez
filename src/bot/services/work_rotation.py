"""Contenido de los minijuegos de rotación de `pala`: Cuadrante y Negociación.

Solo datos. De P5 en adelante cada puesto alterna su minijuego de siempre con uno de
estos, un día cada uno (`work.Position.alts`, `Position.on`). Las reglas están en
`bot.services.work_games` (`PlanGame`, `DealGame`); aquí va lo que cambia de un puesto a
otro: quién se sienta enfrente, qué se encaja y dónde.

Los textos informan y van en normal (sección «Personalidad» de `Biblia.txt`): la gracia,
si la hay, está en la situación del puesto, no en cada línea. Sin personas reales que no
sean figuras públicas y sin marcas. Las claves de los packs son las del contenido de
siempre del puesto (`Position.content`, sin lo que va tras `:`), así se ve de un vistazo
cuál acompaña a cuál.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PlanPack:
    """Contenido de un Cuadrante.

    Attributes:
        scene: Lo que se enseña arriba toda la partida: quién eres y qué repartes.
        lanes: Los 4 carriles (quirófanos, días, muelles), cortos: son botones.
        pieces: Lo que se encaja, al menos 12 nombres distintos y cortos.
        unit: En qué se mide el sitio («h», «palés»).
        skip: Botón de dejar una pieza fuera («🗑️ Derivar»).
    """

    scene: str
    lanes: tuple[str, str, str, str]
    pieces: tuple[str, ...]
    unit: str
    skip: str


@dataclass(frozen=True, slots=True)
class DealPack:
    """Contenido de una Negociación.

    Attributes:
        scene: Lo que se enseña arriba toda la partida: quién eres y qué negocias hoy.
        who: Con quién te sientas, con artículo («la directora de la sucursal»); 4 o más.
        what: Lo que se negocia en cada trato («el crédito de la promoción»); 4 o más.
    """

    scene: str
    who: tuple[str, ...]
    what: tuple[str, ...]


#: Lo que hace el de enfrente según lo que le queda de aguante. Descripciones normales:
#: leerlas es el juego.
DEAL_TELLS: dict[str, tuple[str, ...]] = {
    "calm": (
        "Se recuesta en la silla y te deja hablar.",
        "Sonríe y pide otro café.",
        "Asiente despacio, sin prisa.",
        "Hojea los papeles con calma.",
        "Te mira relajado y juguetea con el boli.",
        "Se ríe de tu propuesta, pero con ganas de seguir.",
    ),
    "tense": (
        "Mira el reloj por segunda vez.",
        "Tamborilea con los dedos en la mesa.",
        "Se afloja el cuello de la camisa.",
        "Resopla y relee la última cifra.",
        "Deja de sonreír y cruza los brazos.",
        "Contesta con monosílabos.",
    ),
    "limit": (
        "Empieza a recoger sus papeles.",
        "Tapa el boli y lo guarda en la chaqueta.",
        "Mira hacia la puerta.",
        "Se levanta a medias de la silla.",
        "Dice «es mi última palabra» y cierra la carpeta.",
        "Saca el móvil y busca otro número.",
    ),
    "poker": (
        "No mueve ni una ceja.",
        "Te mira igual que al principio.",
        "Toma nota sin decir nada.",
        "Ni sonríe ni se queja.",
        "Sigue con la misma cara.",
        "Guarda silencio y espera.",
    ),
}

PLAN_PACKS: dict[str, PlanPack] = {
    # Obra
    "pisos": PlanPack(
        "🏗️ Eres promotor: reparte las promociones entre los solares de la costa sin pasarte "
        "de plantas por solar.",
        ("Solar playa", "Solar golf", "Solar puerto", "Solar sierra"),
        (
            "🏠 Chalet adosado",
            "🏡 Villa con vistas",
            "🏘️ Urbanización cerrada",
            "🏢 Torre de apartamentos",
            "🏨 Aparthotel",
            "🏬 Centro comercial",
            "⛳ Club de golf",
            "🛥️ Puerto deportivo",
            "🏖️ Paseo marítimo",
            "🧖 Spa y balneario",
            "🅿️ Parking subterráneo",
            "🏫 Colegio concertado",
            "🏪 Locales en planta baja",
            "🌴 Bungalós con palmeras",
        ),
        "plantas",
        "🗑️ Vender a un fondo",
    ),
    "terrenos": PlanPack(
        "🗺️ Eres urbanizador: reparte los terrenos de la costa entre los usos del plan sin "
        "pasarte de hectáreas por zona.",
        ("Residencial", "Hotelero", "Comercial", "Zona verde"),
        (
            "🏝️ Dunas frente al mar",
            "🌾 Antiguo cultivo",
            "🌲 Pinar protegido",
            "🪨 Acantilado",
            "🚜 Finca de tomates",
            "🏚️ Casas de pescadores",
            "🧂 Salinas abandonadas",
            "🌵 Secano sin dueño",
            "⛰️ Ladera con vistas",
            "🏭 Fábrica cerrada",
            "🪻 Humedal",
            "🐐 Cañada de cabras",
            "🛣️ Antiguo camino real",
            "🌋 Malpaís volcánico",
        ),
        "ha",
        "🌊 Devolver a Costas",
    ),
    "cavar": PlanPack(
        "🚧 Eres jefe de obra: reparte las tareas de excavación entre las cuadrillas sin "
        "pasarte de días de trabajo.",
        ("Cuadrilla A", "Cuadrilla B", "Cuadrilla C", "Turno noche"),
        (
            "⛏️ Zanja de saneamiento",
            "🕳️ Pozo de cimentación",
            "🚜 Desmonte de ladera",
            "🧱 Muro de contención",
            "💧 Drenaje del solar",
            "🪨 Roca imprevista",
            "🏺 Hallazgo arqueológico",
            "🔌 Canalización eléctrica",
            "🚰 Red de agua",
            "🛣️ Explanada de acceso",
            "🅿️ Sótano de garaje",
            "🏗️ Zapatas del edificio",
            "🪠 Desvío de tuberías",
            "🐀 Colector atascado",
        ),
        "días",
        "🚫 Pedir un modificado",
    ),
    "licitaciones": PlanPack(
        "📑 Eres director de licitaciones: reparte los concursos entre tus equipos sin pasarte "
        "de semanas de trabajo.",
        ("Equipo Norte", "Equipo Sur", "Equipo UTE", "Externos"),
        (
            "🚇 Línea de metro",
            "🌉 Puente sobre el río",
            "🛣️ Autovía comarcal",
            "🏥 Hospital nuevo",
            "🏫 Colegio público",
            "🚰 Depuradora",
            "🏟️ Estadio municipal",
            "✈️ Aeropuerto sin aviones",
            "🚄 Tramo de alta velocidad",
            "🌳 Parque lineal",
            "🏛️ Reforma del ayuntamiento",
            "💡 Alumbrado LED",
            "🚌 Estación de autobuses",
            "🏊 Piscina cubierta",
        ),
        "sem",
        "✋ No presentarnos",
    ),
    "concesiones": PlanPack(
        "🛣️ Eres director de concesiones: reparte las que renuevas entre las sociedades del "
        "grupo sin pasarte de años de contrato.",
        ("Holding", "Filial Norte", "Filial Sur", "Fondo"),
        (
            "🛣️ Peaje de circunvalación",
            "🚰 Gestión del agua",
            "🅿️ Parquímetros",
            "🚌 Autobús urbano",
            "🗑️ Recogida de basuras",
            "🏊 Polideportivo municipal",
            "⚓ Muelle deportivo",
            "🚇 Cercanías",
            "💡 Alumbrado público",
            "☕ Cafetería del hospital",
            "🌳 Mantenimiento de parques",
            "🚲 Bicis compartidas",
            "🏟️ Explotación del estadio",
            "🚦 Radares y multas",
        ),
        "años",
        "⏳ Dejar que caduque",
    ),
    # Hostelería
    "pulseras": PlanPack(
        "🏨 Eres director de hotel: reparte los grupos de huéspedes entre las plantas sin "
        "pasarte de habitaciones por planta.",
        ("Planta 1", "Planta 2", "Planta 3", "Ático"),
        (
            "🎓 Viaje de fin de curso",
            "💍 Despedida de soltera",
            "🧳 Pareja de jubilados",
            "🎸 Banda de gira",
            "🚴 Club de ciclistas",
            "⚽ Peña de fútbol",
            "💼 Congreso de farmacia",
            "🌍 Grupo de turoperador",
            "🎂 Boda de cuarenta",
            "🏖️ Familia numerosa",
            "🤳 Influencers con canje",
            "🧓 Viaje del Imserso",
            "🐶 Pareja con perro",
            "🎉 Cumpleaños de los 40",
        ),
        "hab",
        "🙅 Overbooking: reubicar",
    ),
    "quejas": PlanPack(
        "📝 Eres director regional: reparte las reseñas entre los departamentos sin pasarte "
        "de horas de gestión.",
        ("Recepción", "Cocina", "Limpieza", "Dirección"),
        (
            "⭐ Reseña de una estrella",
            "🛏️ Colchón demasiado duro",
            "🍳 Buffet sin huevos",
            "🚿 Ducha sin agua caliente",
            "📶 Wi-Fi lento",
            "🔇 Ruido de obras",
            "🏊 Piscina llena",
            "🧴 Sin gel en la ducha",
            "🛗 Ascensor averiado",
            "🪳 Visita inesperada",
            "☀️ Tumbonas reservadas",
            "🍹 Cóctel aguado",
            "🚪 Llave que no abre",
            "🔔 Despertador no pedido",
        ),
        "h",
        "🙏 Lamentamos su experiencia",
    ),
    "emplatado": PlanPack(
        "🍽️ Eres chef con restaurante propio: reparte los platos del menú degustación entre "
        "las partidas sin pasarte de minutos.",
        ("Pase", "Fríos", "Calientes", "Postres"),
        (
            "🥚 Huevo a baja temperatura",
            "🫧 Espuma de patata",
            "🍤 Gamba en dos texturas",
            "🥩 Solomillo al carbón",
            "🐟 Lubina esferificada",
            "🌿 Hoja de borraja",
            "🧀 Queso con membrillo",
            "🍮 Flan deconstruido",
            "🍫 Coulant líquido",
            "🍋 Sorbete de limón",
            "🥖 Pan de cristal",
            "🍅 Gazpacho en copa",
            "🦑 Calamar en tinta",
            "🍄 Setas del bosque",
        ),
        "min",
        "🔥 Retirar el plato",
    ),
    "brigada": PlanPack(
        "🔪 Eres dueño de un grupo de restaurantes: reparte a la brigada entre tus locales sin "
        "pasarte de turnos por local.",
        ("Local centro", "Local playa", "Catering", "Food truck"),
        (
            "🔪 Jefe de cocina",
            "🍳 Segundo de cocina",
            "🥗 Cocinero de fríos",
            "🥘 Cocinero de arroces",
            "🧽 Pinche en prácticas",
            "🍷 Sumiller",
            "🛎️ Maître",
            "🍽️ Camarero de sala",
            "🍸 Barman",
            "🧼 Friegaplatos",
            "🥖 Panadero",
            "🍰 Pastelero",
            "🛵 Repartidor del grupo",
            "📋 Jefe de compras",
        ),
        "turnos",
        "🚪 Dar el día libre",
    ),
    # Política (PSOE)
    "decretos": PlanPack(
        "📜 Eres secretario de Estado: reparte los decretos entre las comisiones sin pasarte "
        "de horas de debate.",
        ("Comisión 1", "Comisión 2", "Comisión 3", "Consejo"),
        (
            "📜 Ayudas directas",
            "🌞 Renovables",
            "🏠 Vivienda",
            "🚆 Transporte",
            "🎓 Becas",
            "🛢️ Energía",
            "🌾 Campo",
            "🧾 Reforma fiscal",
            "🏥 Plan sanitario",
            "📡 Agenda digital",
            "🚨 Emergencia",
            "🌊 Ley de costas",
            "🪖 Defensa",
            "📚 Educación",
        ),
        "h",
        "📥 Dejarlo para después",
    ),
    "socios": PlanPack(
        "🤝 Eres vicepresidente: reparte las exigencias de los socios entre los ministerios "
        "sin pasarte de puntos de presupuesto.",
        ("Economía", "Trabajo", "Transición", "Territorial"),
        (
            "🏴 Competencias propias",
            "🗳️ Reforma electoral",
            "🚆 Cercanías transferidas",
            "💰 Financiación singular",
            "🏛️ Presidencia de comisión",
            "🧾 Cambio de impuestos",
            "🌍 Cuota de embajadas",
            "🛤️ Tren a la comarca",
            "🏫 Transferencia educativa",
            "📺 Sitio en la tele pública",
            "🪪 Cargo para su número dos",
            "🌿 Ley verde",
            "🏗️ Obra prometida",
            "🎖️ Condecoración honorífica",
        ),
        "puntos",
        "🙊 Prometer y olvidar",
    ),
    "plenos": PlanPack(
        "🇪🇺 Eres eurodiputado: reparte los asuntos de la semana entre los días de "
        "Estrasburgo sin pasarte de horas.",
        ("Lunes", "Martes", "Miércoles", "Jueves"),
        (
            "🗳️ Votación en bloque",
            "📑 Informe de agricultura",
            "🌍 Resolución de comercio",
            "🐟 Cuotas de pesca",
            "🔋 Baterías y movilidad",
            "🚜 Política agrícola",
            "🛂 Pacto de migración",
            "💬 Debate de actualidad",
            "🎙️ Intervención de un minuto",
            "🧾 Presupuesto anual",
            "🛡️ Defensa común",
            "🔐 Reglamento de datos",
            "🌡️ Paquete climático",
            "📊 Informe de aduanas",
        ),
        "h",
        "🚪 Pasarlo a Bruselas",
    ),
    "enmiendas": PlanPack(
        "✍️ Eres ponente de un reglamento: reparte las enmiendas entre los capítulos del texto "
        "sin pasarte de artículos.",
        ("Capítulo I", "Capítulo II", "Capítulo III", "Anexos"),
        (
            "📏 Definiciones",
            "⏳ Plazos de aplicación",
            "💰 Régimen de sanciones",
            "🧪 Excepciones sectoriales",
            "📬 Obligación de informar",
            "🔍 Controles y auditorías",
            "🤝 Cooperación entre estados",
            "🧮 Umbrales de tamaño",
            "🛠️ Normas técnicas",
            "📜 Cláusula de revisión",
            "🔁 Transposición nacional",
            "🪪 Autoridad competente",
            "🆘 Régimen transitorio",
            "📎 Fichas informativas",
        ),
        "arts",
        "🗑️ Enmienda rechazada",
    ),
    # Sanidad
    "quirofano": PlanPack(
        "🏥 Eres jefe de sección y hoy llevas el parte quirúrgico: reparte las operaciones "
        "entre los quirófanos sin pasarte de horas.",
        ("Q1", "Q2", "Q3", "Q4"),
        (
            "🫀 Bypass",
            "🦴 Prótesis de cadera",
            "👁️ Cataratas",
            "🩹 Hernia",
            "🧠 Hematoma subdural",
            "🫁 Lobectomía",
            "🦵 Menisco",
            "🫃 Vesícula",
            "🧷 Apendicitis",
            "🦷 Cordales",
            "✋ Túnel carpiano",
            "👂 Amígdalas",
            "🩺 Biopsia",
            "🫘 Cálculo renal",
        ),
        "h",
        "🚑 Derivar a otro hospital",
    ),
    "derivaciones": PlanPack(
        "🗂️ Eres jefe de servicio: reparte a los pacientes entre las agendas sin pasarte de "
        "semanas de espera.",
        ("Quirófano", "Consulta", "Concertada", "Primaria"),
        (
            "🦴 Rodilla con artrosis",
            "👁️ Revisión de cataratas",
            "🫀 Cardiópata estable",
            "🦷 Maxilofacial",
            "🧠 Resonancia pendiente",
            "🦶 Juanetes",
            "🩻 Prueba de imagen",
            "🎗️ Seguimiento oncológico",
            "👶 Pediatría",
            "🦻 Audífono",
            "🫁 Estudio del sueño",
            "🩹 Cura crónica",
            "🧴 Dermatología",
            "🩸 Analítica de control",
        ),
        "sem",
        "🏠 Devolver al ambulatorio",
    ),
    "retoques": PlanPack(
        "💋 Eres cirujano estético: reparte las intervenciones entre las salas de la clínica "
        "sin pasarte de horas.",
        ("Sala 1", "Sala 2", "Sala 3", "Sala VIP"),
        (
            "👃 Rinoplastia",
            "💄 Relleno de labios",
            "🍑 Aumento de glúteos",
            "👁️ Blefaroplastia",
            "🧖 Lifting facial",
            "💉 Bótox",
            "🦷 Carillas dentales",
            "🧔 Injerto capilar",
            "🫃 Abdominoplastia",
            "🦵 Liposucción de muslos",
            "👂 Otoplastia",
            "🪞 Retoque del retoque",
            "🌟 Rejuvenecimiento",
            "💪 Implante de pectorales",
        ),
        "h",
        "🙅 Recomendar no operarse",
    ),
    # Oficina (tecnología)
    "horas": PlanPack(
        "⏱️ Eres jefe de proyecto: reparte las tareas entre los consultores sin pasarte de "
        "horas por persona.",
        ("Júnior", "Senior", "Gerente", "Freelance"),
        (
            "📊 Informe semanal",
            "🧪 Pruebas de regresión",
            "📝 Acta de reunión",
            "🔧 Parche urgente",
            "📞 Llamada con el cliente",
            "🧾 Memoria técnica",
            "🖥️ Migración de datos",
            "🎨 Rediseño del logo",
            "📈 Cuadro de mando",
            "🧹 Limpieza de código",
            "📚 Formación interna",
            "🧩 Integración de módulos",
            "🚀 Despliegue de viernes",
            "🔍 Revisión de calidad",
        ),
        "h",
        "🙈 Imputar a otro proyecto",
    ),
    "licitacion": PlanPack(
        "🏛️ Eres gerente de cuentas públicas: reparte las partes del pliego entre los equipos "
        "sin pasarte de días de redacción.",
        ("Equipo A", "Equipo B", "Jurídico", "Comercial"),
        (
            "📄 Pliego a medida",
            "📎 Anexo técnico",
            "💬 Aclaraciones",
            "🧮 Oferta económica",
            "🗂️ Solvencia técnica",
            "🏷️ Criterios de valoración",
            "📅 Cronograma",
            "👥 Equipo propuesto",
            "🔒 Seguridad y datos",
            "🧾 Memoria descriptiva",
            "♿ Accesibilidad",
            "🛠️ Plan de mantenimiento",
            "📊 Plan de calidad",
            "🖋️ Declaración responsable",
        ),
        "días",
        "🚫 No presentar oferta",
    ),
    "pivotar": PlanPack(
        "🔄 Eres CEO tras la serie A: reparte los proyectos de moda entre los equipos sin "
        "pasarte de sprints.",
        ("Producto", "Ingeniería", "Growth", "Datos"),
        (
            "🤖 Asistente con IA",
            "⛓️ Módulo de blockchain",
            "🥽 App de realidad virtual",
            "📱 App móvil nueva",
            "💬 Chat para clientes",
            "🛒 Marketplace",
            "🎮 Gamificación",
            "🌱 Panel de sostenibilidad",
            "🧠 Motor de recomendaciones",
            "🔔 Notificaciones push",
            "🧾 Pagos integrados",
            "🌐 Internacionalización",
            "🧪 Prueba A/B",
            "📦 Plan premium",
        ),
        "sprints",
        "⏸️ Aparcar en el backlog",
    ),
    "rondas": PlanPack(
        "👼 Eres business angel: reparte tu dinero entre las startups de la cartera según la "
        "ronda, sin pasarte de M Y$ por ronda.",
        ("Semilla", "Serie A", "Serie B", "Puente"),
        (
            "🥑 Delivery de aguacates",
            "🐕 Red social de perros",
            "🧴 Cosmética con IA",
            "🛴 Patinetes de alquiler",
            "🍔 Hamburguesas fantasma",
            "🛰️ Satélites pequeños",
            "🏠 Pisos por suscripción",
            "🎧 Podcasts de pago",
            "🧋 Té de burbujas",
            "🪙 Monedero cripto",
            "👟 Zapatillas a medida",
            "📚 Clases en directo",
            "🍷 Vino por suscripción",
            "🔋 Baterías de bolsillo",
        ),
        "M Y$",
        "🙅 Declinar la ronda",
    ),
    # Reparto (rider)
    "paquetes": PlanPack(
        "📦 Eres jefe de unidad de reparto: reparte los envíos entre las rutas sin pasarte de "
        "bultos por ruta.",
        ("Furgoneta 1", "Furgoneta 2", "Moto", "A pie"),
        (
            "📚 Caja de libros",
            "📮 Carta certificada",
            "🪑 Silla desmontada",
            "🖥️ Pantalla grande",
            "🥬 Cesta de verdura",
            "🎁 Regalo de cumpleaños",
            "⚖️ Notificación judicial",
            "🧸 Peluche gigante",
            "🍷 Caja de vino",
            "🚲 Bici en caja",
            "🌵 Cactus con maceta",
            "💊 Pedido de farmacia",
            "🧊 Nevera portátil",
            "🛋️ Cojines del sofá",
        ),
        "bultos",
        "📭 Dejar aviso de ausente",
    ),
    "ruta_rural": PlanPack(
        "🏔️ Eres director de zona: reparte los pueblos entre las rutas rurales sin pasarte de "
        "horas de ruta.",
        ("Ruta norte", "Ruta sur", "Ruta sierra", "Ruta costa"),
        (
            "⛰️ Aldea de montaña",
            "🐑 Pueblo de pastores",
            "🌾 Pueblo del cereal",
            "🛖 Cortijo aislado",
            "⛪ Pueblo de la ermita",
            "🌉 Pueblo del puente",
            "🚜 Granja lejana",
            "🏞️ Casa del embalse",
            "🌋 Barrio del volcán",
            "🍌 Platanera del norte",
            "🐐 Caserío de cabras",
            "🏚️ Casa en ruinas",
            "🌳 Finca del castañar",
            "🧱 Caserío sin nombre",
        ),
        "h",
        "📮 Dejarlo en el bar",
    ),
    "dark_store": PlanPack(
        "📱 Eres dueño de dark stores: reparte los pedidos entre tus almacenes sin pasarte de "
        "cajas por almacén.",
        ("Almacén A", "Almacén B", "Almacén C", "Sótano"),
        (
            "🥛 Pedido de leche",
            "🍫 Antojo de las 3:00",
            "🧻 Papel higiénico",
            "🍺 Cerveza fría",
            "🧊 Bolsa de hielo",
            "🍕 Pizzas congeladas",
            "💐 Ramo de urgencia",
            "🥑 Aguacates maduros",
            "🧴 Gel y champú",
            "🍼 Biberones",
            "🐾 Pienso para gato",
            "🔋 Pilas",
            "🍦 Helado de pistacho",
            "🥚 Docena de huevos",
        ),
        "cajas",
        "🚫 Cerrar la persiana",
    ),
    "ultima_milla": PlanPack(
        "🚀 Eres el rey de la última milla: reparte los pedidos entre las zonas sin pasarte de "
        "pedidos por turno.",
        ("Centro", "Puerto", "Periferia", "Polígono"),
        (
            "🍔 Menú doble",
            "🍣 Sushi para ocho",
            "🥗 Ensalada ligera",
            "🌮 Tacos con extra",
            "🍕 Pizza familiar",
            "🍗 Pollo frito",
            "🥡 Wok de verduras",
            "🍦 Helado derretido",
            "☕ Café para la oficina",
            "🍟 Patatas de madrugada",
            "🥘 Paella del domingo",
            "🧃 Zumo natural",
            "🍩 Docena de donuts",
            "🥪 Bocata de tortilla",
        ),
        "pedidos",
        "🔕 Rechazar pedido",
    ),
    # Funcionariado
    "boe": PlanPack(
        "📰 Eres subdirector general: reparte las disposiciones entre las secciones del BOE "
        "sin pasarte de páginas.",
        ("Sección I", "Sección II", "Sección III", "Sección V"),
        (
            "📜 Ley orgánica",
            "📑 Real decreto-ley",
            "🏛️ Nombramientos",
            "🎖️ Condecoraciones",
            "📣 Convocatoria de plazas",
            "🏗️ Anuncio de licitación",
            "🗞️ Corrección de errores",
            "💶 Subvenciones",
            "🌐 Convenio internacional",
            "🧾 Orden ministerial",
            "🏫 Oposiciones",
            "⚖️ Sentencia",
            "🏥 Resolución sanitaria",
            "🚧 Expropiaciones",
        ),
        "págs",
        "🕛 Publicar mañana",
    ),
    "expediente": PlanPack(
        "📁 Eres director general: reparte los expedientes entre los negociados sin pasarte "
        "de folios.",
        ("Negociado 1", "Negociado 2", "Registro", "Archivo"),
        (
            "📋 Solicitud de licencia",
            "📬 Reclamación de pago",
            "⚖️ Recurso de alzada",
            "🏗️ Expediente de obras",
            "🚪 Cambio de ubicación",
            "🧾 Devolución de tasas",
            "📌 Información pública",
            "🪪 Certificado de residencia",
            "🧯 Informe de bomberos",
            "🌳 Tala de árboles",
            "🏪 Apertura de local",
            "🐕 Registro de animales",
            "💬 Queja ciudadana",
            "🚗 Vado permanente",
        ),
        "folios",
        "🔁 Falta un sello",
    ),
    "patrimonio": PlanPack(
        "💼 Eres asesor fiscal de grandes fortunas: reparte los bienes entre las sociedades "
        "sin pasarte de M Y$ por vehículo.",
        ("Patrimonial", "Fundación", "Fondo", "Holding"),
        (
            "🏝️ Isla privada",
            "🛥️ Yate de 40 metros",
            "🎨 Colección de arte",
            "🏰 Castillo en Segovia",
            "✈️ Jet privado",
            "🍷 Bodega de culto",
            "🐎 Cuadra de caballos",
            "🚗 Coches clásicos",
            "⌚ Relojes de colección",
            "🏙️ Edificio de oficinas",
            "🌴 Finca en Canarias",
            "💎 Joyas heredadas",
            "📈 Cartera de acciones",
            "🏨 Hotel heredado",
        ),
        "M Y$",
        "🏔️ Mandarlo al extranjero",
    ),
    "sandwich": PlanPack(
        "🥪 Eres ingeniero fiscal de multinacionales: reparte los beneficios entre las filiales "
        "sin pasarte de M Y$ por filial.",
        ("Irlanda", "Países Bajos", "Luxemburgo", "Malta"),
        (
            "🏷️ Royalties de marca",
            "💡 Patentes cedidas",
            "🧾 Comisiones internas",
            "📦 Licencias de software",
            "🚚 Costes de gestión",
            "💬 Servicios de consultoría",
            "📈 Intereses de préstamos",
            "🧮 Precios de transferencia",
            "📣 Gastos de marketing",
            "🛠️ Cuota de asistencia",
            "🏢 Alquiler de la sede",
            "📚 Gastos de I+D",
            "🔐 Derechos de datos",
            "👥 Cesión de personal",
        ),
        "M Y$",
        "🧾 Pagar en España",
    ),
    # Influencer
    "escaleta": PlanPack(
        "📺 Eres presentador de magacín: reparte las secciones entre los bloques del programa "
        "sin pasarte de minutos.",
        ("Apertura", "Bloque 1", "Bloque 2", "Cierre"),
        (
            "🍳 Receta del día",
            "🔮 Horóscopo",
            "💬 Cotilleo de famosos",
            "🧠 Test de cultura",
            "📞 Llamada de oyentes",
            "🎤 Actuación en directo",
            "👗 Moda de temporada",
            "💊 Consejos de salud",
            "📰 Resumen de noticias",
            "😂 Cámara oculta",
            "🐶 Mascota invitada",
            "🌦️ El tiempo",
            "🛒 Teletienda",
            "🗳️ Encuesta del público",
        ),
        "min",
        "✂️ Recortar en montaje",
    ),
    "invitados": PlanPack(
        "🎬 Eres presentador de prime time: reparte los invitados entre los bloques de la "
        "noche sin pasarte de minutos.",
        ("Bloque 1", "Bloque 2", "Bloque 3", "Bloque 4"),
        (
            "⚽ Futbolista retirado",
            "🎤 Cantante en promoción",
            "🎬 Actriz de estreno",
            "🏛️ Político de turno",
            "🍳 Cocinero famoso",
            "📚 Escritor con libro",
            "🚴 Deportista olímpico",
            "🤡 Humorista invitado",
            "🧠 Divulgador científico",
            "🎸 Grupo de reunión",
            "🌟 Estrella de la tele",
            "🛥️ Famoso de reality",
            "📺 Concursante viral",
            "🥇 Entrenador estrella",
        ),
        "min",
        "📵 Cortar la conexión",
    ),
    "subathon": PlanPack(
        "🔴 Eres streamer en pleno subathon: reparte las alertas entre las horas de directo "
        "sin pasarte de minutos por hora.",
        ("Hora 1", "Hora 2", "Hora 3", "Hora 4"),
        (
            "🎁 Suscripción regalada",
            "💬 Mensaje donado",
            "🎮 Reto de los espectadores",
            "🎂 Cumpleaños de un fan",
            "🐶 Perro en cámara",
            "🍜 Comer en directo",
            "🔊 Saludo en alto",
            "📞 Llamada a un seguidor",
            "🎲 Ruleta de castigos",
            "🎶 Canción pedida",
            "🥤 Beber algo raro",
            "📸 Foto con el sub",
            "🏆 Meta superada",
            "🧊 Reto del hielo",
        ),
        "min",
        "🔇 Silenciar la alerta",
    ),
    "marca": PlanPack(
        "🥤 Eres dueño de una bebida energética: reparte las campañas entre los canales sin "
        "pasarte de M Y$ de presupuesto.",
        ("Tele", "Redes", "Eventos", "Deporte"),
        (
            "🏎️ Patrocinio de carreras",
            "🪂 Salto extremo",
            "🎸 Festival de verano",
            "📱 Reto en redes",
            "🛹 Skaters patrocinados",
            "🎮 Torneo de videojuegos",
            "📺 Anuncio en prime time",
            "🏄 Competición de surf",
            "🧢 Merchandising",
            "🚚 Camión de muestras",
            "🎤 Cantante embajador",
            "🏀 Equipo de baloncesto",
            "🔥 Edición limitada",
            "🛼 Concurso de acrobacias",
        ),
        "M Y$",
        "🗄️ Guardar la idea",
    ),
}

DEAL_PACKS: dict[str, DealPack] = {
    # Obra
    "sobrecostes": DealPack(
        "🏗️ Eres constructor y toca cerrar el modificado de la obra: cada parte quiere cobrar "
        "más y pagar menos.",
        (
            "el arquitecto director de obra",
            "la aparejadora del ayuntamiento",
            "el jefe de compras del proveedor",
            "la interventora municipal",
            "el subcontratista del hormigón",
        ),
        (
            "el precio del modificado",
            "el plazo de entrega de la obra",
            "la partida de imprevistos",
            "la revisión de precios del acero",
            "la penalización por el retraso",
            "el pago de las certificaciones atrasadas",
        ),
    ),
    "banco": DealPack(
        "🏦 Eres el rey del ladrillo y hoy toca banco: hay que refinanciar la deuda de la "
        "promoción antes de que venza.",
        (
            "la directora de la sucursal",
            "el analista de riesgos",
            "el interventor de la caja",
            "la jefa de banca de empresas",
            "el gestor de activos problemáticos",
        ),
        (
            "la refinanciación de la deuda",
            "el tipo de la hipoteca de los compradores",
            "el aval de la promoción",
            "la carencia del préstamo",
            "la tasación del solar",
        ),
    ),
    "alcalde": DealPack(
        "🏛️ Eres magnate inmobiliario: toca cerrar con el ayuntamiento el convenio de tu "
        "próxima macrourbanización.",
        (
            "el alcalde de la ciudad",
            "la concejala de urbanismo",
            "el secretario del ayuntamiento",
            "el arquitecto municipal",
            "el portavoz de la oposición municipal",
        ),
        (
            "la recalificación de los terrenos",
            "la edificabilidad del convenio",
            "el número de viviendas protegidas",
            "la zona verde de la promoción",
            "el calendario de las licencias",
            "el nombre de la calle nueva",
        ),
    ),
    "palco": DealPack(
        "⚽ Eres presidente de un club de fútbol y en el palco se habla de lo de siempre: "
        "obras, fichajes y suelo público.",
        (
            "el concejal de deportes",
            "el presidente de la liga",
            "el patrocinador principal del club",
            "el agente del delantero estrella",
            "el presidente del club rival",
        ),
        (
            "el traspaso de la estrella",
            "el nombre del estadio",
            "los derechos de televisión",
            "el terreno de la ciudad deportiva",
            "las entradas del palco",
            "el contrato del entrenador",
        ),
    ),
    "fomento": DealPack(
        "🚄 Eres consejero delegado de una constructora y vas al Ministerio de Transportes a "
        "cerrar el próximo modificado.",
        (
            "el ministro de Transportes",
            "la secretaria de Estado de Infraestructuras",
            "el director general de Carreteras",
            "el presidente de la ferroviaria pública",
            "la jefa de gabinete del ministro",
        ),
        (
            "el modificado del tramo",
            "la fecha de la inauguración",
            "el sobrecoste de los túneles",
            "la ampliación del plazo",
            "el pago de las expropiaciones",
            "la estación que nadie pidió",
        ),
    ),
    "internacional": DealPack(
        "🌍 Eres presidente de una constructora del IBEX y firmas obras por todo el mundo: "
        "cobrar por adelantado es lo primero.",
        (
            "el ministro de obras de un país petrolero",
            "el presidente de un banco de desarrollo",
            "el embajador de España en la capital",
            "la ministra de transportes de un país insular",
            "el representante de un fondo soberano",
        ),
        (
            "el precio del megapuerto",
            "el anticipo del contrato",
            "la garantía bancaria",
            "el plazo de entrega del tren",
            "la cláusula de revisión de precios",
            "el seguro de la obra",
        ),
    ),
    # Hostelería
    "chiringuito": DealPack(
        "🍤 Eres dueño de un chiringuito y antes de la temporada toca cerrar tratos con todos "
        "los que viven del paseo marítimo.",
        (
            "el concejal del litoral",
            "la proveedora de pescado",
            "el socorrista jefe",
            "el presidente de la cofradía de pescadores",
            "el dueño del chiringuito de al lado",
        ),
        (
            "el alquiler de las hamacas",
            "la licencia de la terraza",
            "el precio de la ración de calamares",
            "el horario de la música",
            "la cuota de la basura",
            "el turno de las sombrillas",
        ),
    ),
    "colados": DealPack(
        "🌴 Eres director de resorts en el Caribe: cada uno quiere su parte y los colados de "
        "la pulsera, también.",
        (
            "el turoperador europeo",
            "la delegada sindical del resort",
            "el gerente del hotel vecino",
            "el jefe de seguridad del resort",
            "la agente de viajes principal",
        ),
        (
            "el cupo de habitaciones",
            "la pulsera de todo incluido",
            "las tarifas de temporada alta",
            "el precio del cubo de cervezas",
            "la política de invitados externos",
            "el contrato de los animadores",
        ),
    ),
    "cadena_hotelera": DealPack(
        "🏢 Eres consejero delegado de una cadena hotelera: toca convencer a inversores y "
        "propietarios de que todo es «inversión».",
        (
            "el fondo propietario de los hoteles",
            "la presidenta del consejo de administración",
            "el comité de empresa de la cadena",
            "el director financiero del grupo",
            "el socio inversor minoritario",
        ),
        (
            "el plan de reformas",
            "el recorte de plantilla",
            "el dividendo del año",
            "la compra de un hotel nuevo",
            "el precio medio de la habitación",
            "el contrato de gestión",
        ),
    ),
    "patronal": DealPack(
        "🏛️ Eres presidente de la patronal turística: hay mesa con el Gobierno y los "
        "sindicatos, y siempre hay crisis salvo en el balance.",
        (
            "la ministra de Industria y Turismo",
            "el secretario general del sindicato",
            "el presidente de la comunidad autónoma",
            "la directora general de Trabajo",
            "el portavoz de los hoteleros",
        ),
        (
            "la subida salarial del convenio",
            "las ayudas por la crisis",
            "la tasa turística",
            "la reducción de jornada",
            "los fijos discontinuos",
            "la rebaja del IVA de los menús",
        ),
    ),
    "inspector": DealPack(
        "⭐ Eres chef con estrella y llega el inspector de la guía: tienes una noche para "
        "convencerle de que mereces la segunda.",
        (
            "el inspector de la guía",
            "la crítica gastronómica",
            "el director de la guía",
            "el sumiller del restaurante",
            "el proveedor de producto de temporada",
        ),
        (
            "la segunda estrella",
            "el menú de la noche",
            "la mesa del inspector",
            "el precio del menú degustación",
            "el maridaje de la casa",
            "la reseña del periódico",
        ),
    ),
    "plato_tv": DealPack(
        "📺 Eres chef de la tele: antes de grabar la gran final hay que cerrar los tratos con "
        "la productora.",
        (
            "la productora del programa",
            "el director de la cadena",
            "el agente del concursante favorito",
            "el patrocinador del concurso",
            "la presentadora de la gala",
        ),
        (
            "tu caché por temporada",
            "el guion del enfrentamiento",
            "el producto de la marca patrocinadora",
            "el ganador de la final",
            "el contrato de tu libro de recetas",
            "el tiempo en pantalla",
        ),
    ),
    "dubai": DealPack(
        "🏜️ Eres gurú gastronómico con franquicias en Dubái: toca cerrar la próxima apertura "
        "con quien pone el dinero y quien pone el oro.",
        (
            "el socio inversor local",
            "la directora del centro comercial",
            "el proveedor de pan de oro",
            "el director del hotel de lujo",
            "la agencia de influencers",
        ),
        (
            "el alquiler del local en la torre",
            "el porcentaje de la franquicia",
            "el precio del plato con pan de oro",
            "el contrato del chef ejecutivo",
            "la exclusiva de la carta",
            "el calendario de la inauguración",
        ),
    ),
    # Política (PSOE)
    "portavoz": DealPack(
        "📣 Eres portavoz del grupo parlamentario: pasillo, pacto y votación, y a cada uno le "
        "dices lo que quiere oír.",
        (
            "el portavoz del grupo mixto",
            "la presidenta del Congreso",
            "el portavoz de un partido regional",
            "el jefe de gabinete del grupo",
            "la periodista parlamentaria",
        ),
        (
            "el voto de la ley",
            "la enmienda transaccional",
            "el orden del día del pleno",
            "la comparecencia del ministro",
            "el reparto de las preguntas",
            "el tiempo de intervención",
        ),
    ),
    "ministerio": DealPack(
        "🗂️ Eres ministro y hoy hay despacho abierto: todo el mundo viene a pedir algo y a "
        "nadie se le dice que no del todo.",
        (
            "el secretario de Estado",
            "la jefa de prensa del ministerio",
            "el sindicato del sector",
            "el presidente de la patronal del sector",
            "la directora del gabinete",
        ),
        (
            "el presupuesto del ministerio",
            "el nombramiento del nuevo director",
            "la subvención del sector",
            "la comparecencia en comisión",
            "el plan de choque",
            "el comunicado sobre la polémica",
        ),
    ),
    "control": DealPack(
        "🏛️ Eres presidente del Gobierno en la sesión de control: cada pregunta es un trato "
        "sobre cuánto hay que contestar.",
        (
            "el líder de la oposición",
            "la portavoz del grupo socio",
            "el portavoz del partido bisagra",
            "la presidenta de la cámara",
            "el jefe de gabinete de Moncloa",
        ),
        (
            "la pregunta del escándalo de la semana",
            "el tiempo de réplica",
            "el tema del que se habla mañana",
            "la reforma que se vota",
            "la comisión de investigación",
            "el apoyo para los presupuestos",
        ),
    ),
    "consejo": DealPack(
        "💡 Eres consejero de una eléctrica tras dejar el Gobierno: el consejo decide cuánto "
        "cobrarás por conocer a quien conoces.",
        (
            "el presidente de la eléctrica",
            "la consejera independiente",
            "el exministro del consejo",
            "el director de relaciones institucionales",
            "el accionista principal",
        ),
        (
            "tu dieta de consejero",
            "el contrato de asesoría",
            "el precio de la luz en hora punta",
            "los beneficios del trimestre",
            "el nuevo comité de nombramientos",
            "la subida de la tarifa",
        ),
    ),
    "lobbies": DealPack(
        "🤝 Eres jefe de delegación en el Parlamento Europeo: los lobistas te asedian por los "
        "pasillos y tú escuchas, sonríes y no firmas nada.",
        (
            "el lobista de la industria farmacéutica",
            "la representante de las eléctricas",
            "la asesora del grupo político",
            "el delegado de los agricultores",
            "el cabildero tecnológico",
        ),
        (
            "la enmienda de la ley de productos",
            "la reunión en el despacho",
            "el informe de la comisión",
            "el almuerzo en Bruselas",
            "la excepción para el sector",
            "la cláusula del reglamento",
        ),
    ),
    "trilogo": DealPack(
        "🇪🇺 Eres vicepresidente del Parlamento Europeo en un trílogo: hay que cerrar un "
        "acuerdo que todos puedan vender como victoria.",
        (
            "la presidencia rotatoria del Consejo",
            "el comisario ponente",
            "el negociador del Consejo",
            "la ponente en la sombra",
            "el jefe de gabinete de la Comisión",
        ),
        (
            "el texto del reglamento",
            "el calendario de entrada en vigor",
            "las excepciones para los estados",
            "la financiación del programa",
            "la cláusula de revisión",
            "el nombre del acuerdo",
        ),
    ),
    "comisario": DealPack(
        "🏢 Eres comisario europeo en el Berlaymont: cada decisión es un trato con quien "
        "puede bloquearla.",
        (
            "la presidenta de la Comisión",
            "el comisario de competencia",
            "el embajador de un estado miembro",
            "la eurodiputada del grupo mayoritario",
            "el director general de tu departamento",
        ),
        (
            "la propuesta de directiva",
            "el presupuesto del programa",
            "el expediente sancionador",
            "la fecha de publicación",
            "la agenda de la cumbre",
            "la nota de prensa",
        ),
    ),
    # Sanidad
    "consulta": DealPack(
        "🩺 Eres médico adjunto y en la consulta todo el mundo pide algo: pruebas, bajas y "
        "citas que no existen.",
        (
            "el paciente que viene cada semana",
            "la jefa de enfermería",
            "el representante de laboratorio",
            "la gestora de citas",
            "el familiar de un paciente",
        ),
        (
            "la baja laboral",
            "la prueba de imagen urgente",
            "el cambio de tratamiento",
            "el informe para el seguro",
            "la receta de la crónica",
            "la cita con el especialista",
        ),
    ),
    "direccion": DealPack(
        "🏥 Eres director médico: en la junta todos piden recursos y hay que decir que se "
        "«está priorizando».",
        (
            "el jefe de urgencias",
            "la supervisora de enfermería",
            "el representante del comité de empresa",
            "la gerente del área sanitaria",
            "el jefe de cirugía",
        ),
        (
            "las camas del pasillo",
            "las plazas de refuerzo del verano",
            "el equipo de resonancia",
            "la lista de espera de traumatología",
            "los turnos de guardia",
            "el presupuesto de la planta",
        ),
    ),
    "gerencia": DealPack(
        "📊 Eres gerente del hospital: la Consejería quiere resultados sin gastar más y el "
        "hospital, gastar sin resultados.",
        (
            "el viceconsejero de Sanidad",
            "la directora general de asistencia",
            "el jefe del servicio de personal",
            "el representante de los sindicatos",
            "la gestora de compras",
        ),
        (
            "el presupuesto del próximo año",
            "los contratos de los eventuales",
            "la apertura del ala nueva",
            "la contratación de la limpieza",
            "el objetivo de las listas de espera",
            "la inauguración de la consulta",
        ),
    ),
    "consejeria": DealPack(
        "🏛️ Eres consejero de Sanidad en el pleno autonómico: te preguntan por las listas de "
        "espera y toca pactar a quién se le echa la culpa.",
        (
            "la portavoz de la oposición autonómica",
            "el presidente de la comunidad",
            "el consejero de Hacienda",
            "el representante de los médicos",
            "la gerente del hospital comarcal",
        ),
        (
            "el presupuesto sanitario",
            "los médicos que faltan",
            "el hospital que se inaugura",
            "el pacto por la sanidad",
            "la fecha del plan de choque",
            "la auditoría de las listas",
        ),
    ),
    "letra_pequena": DealPack(
        "🩺 Eres médico de mutua: el paciente quiere que se lo cubran todo y la póliza dice "
        "que casi nada.",
        (
            "el paciente de la póliza básica",
            "el gestor de la aseguradora",
            "la directora de la mutua",
            "el abogado del asegurado",
            "la enfermera de admisión",
        ),
        (
            "la autorización de la prueba",
            "la cobertura del tratamiento",
            "el copago de la visita",
            "la lista de especialistas del cuadro",
            "la franquicia de la póliza",
            "el informe de la preexistencia",
        ),
    ),
    "clinica": DealPack(
        "💳 Eres director de una clínica privada: el paciente es «cliente» y el cliente "
        "siempre trae tarjeta, si el presupuesto cuadra.",
        (
            "el cliente que pide presupuesto",
            "el jefe de cirugía de la clínica",
            "la directora comercial",
            "el representante de la aseguradora de salud",
            "el proveedor de prótesis",
        ),
        (
            "el precio de la operación",
            "los pagos a plazos",
            "el paquete de revisiones",
            "el descuento por traer a un amigo",
            "el suplemento de la habitación",
            "la póliza complementaria",
        ),
    ),
    "conciertos": DealPack(
        "🤝 Eres consejero delegado de un grupo hospitalario: la pública no da abasto y tú "
        "tienes quirófanos libres.",
        (
            "la consejera de Sanidad",
            "el director del servicio de salud",
            "el interventor de la comunidad",
            "el presidente del fondo propietario",
            "el sindicato de la sanidad pública",
        ),
        (
            "el precio de cada operación",
            "el número de camas concertadas",
            "la duración del concierto",
            "el pago de las derivaciones",
            "las listas de espera a derivar",
            "el plus de urgencias",
        ),
    ),
    "aseguradora": DealPack(
        "📈 Eres dueño de una aseguradora de salud: lo que cobras es prima, lo que pagas es "
        "siniestralidad, y hay que cuadrar la junta.",
        (
            "los accionistas de la junta",
            "el director de siniestros",
            "la actuaria jefa",
            "el presidente del grupo hospitalario asociado",
            "el regulador del sector",
        ),
        (
            "la subida de las primas",
            "la lista de exclusiones",
            "el precio por acto médico",
            "el límite de cobertura",
            "el recorte de siniestros",
            "el reparto del dividendo",
        ),
    ),
    # Oficina (tecnología)
    "inversores": DealPack(
        "🦄 Eres CTO de una startup y toca ronda: los inversores quieren humo con gráfica y "
        "tú, tiempo para programar.",
        (
            "la inversora de capital riesgo",
            "el business angel de la ronda",
            "el abogado de la ronda",
            "el cofundador comercial",
            "la directora de producto",
        ),
        (
            "la valoración de la empresa",
            "el porcentaje de la ronda",
            "la hoja de ruta del producto",
            "la fecha del lanzamiento",
            "el equipo que se contrata",
            "el uso del dinero de la ronda",
        ),
    ),
    "app_publica": DealPack(
        "📱 Eres director de transformación digital: la app pública se cae el día del estreno "
        "y hay que acordar quién paga qué.",
        (
            "el subsecretario del ministerio",
            "la jefa de proyecto de la consultora",
            "el responsable de ciberseguridad",
            "el proveedor del servidor",
            "la asesora del ministro",
        ),
        (
            "el sobrecoste de la app",
            "la fecha del relanzamiento",
            "la penalización por la caída",
            "el contrato de mantenimiento",
            "el número de servidores",
            "la nota de prensa del arreglo",
        ),
    ),
    "comite": DealPack(
        "🤝 Eres socio de la consultora: en el comité se factura la hora, no el resultado, y "
        "todos quieren su parte del cliente.",
        (
            "el socio director de la oficina",
            "la directora de recursos humanos",
            "el cliente de la administración",
            "el director de ventas",
            "la gerente de operaciones",
        ),
        (
            "el precio por hora del equipo",
            "el reparto de los bonus",
            "el cliente nuevo de la cartera",
            "la subcontratación a la filial",
            "el alcance del proyecto",
            "el informe para el cliente",
        ),
    ),
    "socio_director": DealPack(
        "🏛️ Eres socio director de la consultora: el Gobierno de turno tiene que seguir "
        "siendo cliente, gane quien gane.",
        (
            "la subsecretaria del ministerio cliente",
            "el director del gabinete ministerial",
            "el jefe de contratación pública",
            "el presidente de la patronal tecnológica",
            "la consejera de la región cliente",
        ),
        (
            "el contrato marco",
            "la renovación del servicio",
            "el informe sobre la reforma",
            "la prórroga del contrato",
            "la subida de tarifas",
            "el nombramiento de tu exsocio",
        ),
    ),
    "pitch": DealPack(
        "🚀 Eres fundador de una startup: es el pitch a inversores y vendes una visión con "
        "una curva hacia arriba.",
        (
            "el fondo de capital riesgo",
            "la mentora de la aceleradora",
            "el inversor escéptico",
            "la periodista tecnológica",
            "el cliente piloto",
        ),
        (
            "la valoración previa",
            "el cheque inicial",
            "el puesto en el consejo",
            "la cláusula de salida",
            "el plan de contratación",
            "el nombre de la ronda",
        ),
    ),
    "runway": DealPack(
        "🔥 Eres CEO de un unicornio y la caja se acaba: hay que alargar el runway con quien "
        "tenga la cartera abierta.",
        (
            "el fondo de crecimiento",
            "la directora financiera de la empresa",
            "el banco de inversión",
            "el comité de trabajadores",
            "el socio fundador",
        ),
        (
            "la nueva ronda",
            "el ajuste del equipo",
            "el plazo de la deuda convertible",
            "la valoración rebajada",
            "el cierre de la oficina nueva",
            "los sueldos de los directivos",
        ),
    ),
    "red_social": DealPack(
        "📢 Eres tecnomillonario dueño de una red social: anunciantes, reguladores y usuarios "
        "discuten el derecho a opinar, sobre todo el tuyo.",
        (
            "el comisario de servicios digitales",
            "la directora de moderación",
            "el anunciante principal",
            "el moderador despedido",
            "el abogado de la plataforma",
        ),
        (
            "las reglas de moderación",
            "el algoritmo de la portada",
            "la multa del regulador",
            "el precio de la verificación",
            "la vuelta de una cuenta suspendida",
            "el ritmo de los despidos",
        ),
    ),
    # Reparto (rider)
    "fundador": DealPack(
        "🚀 Eres fundador de una app de reparto: los riders son «colaboradores» y el sindicato "
        "quiere otra palabra.",
        (
            "el portavoz de los riders",
            "la inspectora de trabajo de guardia",
            "el dueño del restaurante asociado",
            "el inversor principal de la app",
            "la responsable de comunicación",
        ),
        (
            "la comisión por pedido",
            "el pago por kilómetro",
            "la cobertura de accidentes",
            "el bonus por lluvia",
            "los horarios de las franjas",
            "el nombre del contrato",
        ),
    ),
    "perdido": DealPack(
        "📮 Eres director de operaciones de Correos: hay paquetes perdidos desde hace un año y "
        "cada oficina tiene su explicación.",
        (
            "la jefa del centro de clasificación",
            "el sindicato postal",
            "el cliente con el paquete extraviado",
            "el director de la zona",
            "el proveedor de furgonetas",
        ),
        (
            "la indemnización por el envío perdido",
            "el plazo de entrega de siete días",
            "el refuerzo de plantilla en Navidad",
            "la compra de furgonetas nuevas",
            "el cierre de la oficina del pueblo",
            "el buzón que nadie abre",
        ),
    ),
    "direccion_correos": DealPack(
        "🏢 Eres director general de Correos en el consejo: si pierde dinero es servicio "
        "público y si gana, gestión eficiente.",
        (
            "la presidenta de la empresa pública",
            "el representante del ministerio en el consejo",
            "el sindicato mayoritario de Correos",
            "el director financiero de Correos",
            "el consejero independiente",
        ),
        (
            "el déficit del año",
            "la subida del precio del sello",
            "el plan de oficinas rurales",
            "la bolsa de empleo temporal",
            "el nuevo servicio de paquetería",
            "la partida de publicidad",
        ),
    ),
    "presidencia_correos": DealPack(
        "🏛️ Eres presidente de Correos: lo tuyo es la lealtad, no la contabilidad, y hoy "
        "tocan nombramientos y pérdidas.",
        (
            "el ministro que te nombró",
            "el secretario de Estado de Hacienda",
            "la presidenta del comité de empresa",
            "el auditor de las cuentas",
            "el director de comunicación",
        ),
        (
            "el rescate de las pérdidas",
            "el nombramiento de un director",
            "la campaña de publicidad",
            "el plan estratégico",
            "la oficina que se cierra",
            "la subvención por el servicio universal",
        ),
    ),
    "ley_rider": DealPack(
        "📱 Eres lobista contra la Ley Rider: en el Ministerio de Trabajo tus riders son "
        "«emprendedores de sí mismos».",
        (
            "la ministra de Trabajo",
            "el director general de Trabajo",
            "el portavoz de los repartidores",
            "la inspectora jefe de provincia",
            "el asesor del ministerio",
        ),
        (
            "la presunción de laboralidad",
            "el periodo transitorio",
            "la multa por rider sin contrato",
            "el algoritmo de asignación",
            "la mesa de diálogo",
            "el borrador de la ley",
        ),
    ),
    "inspector_trabajo": DealPack(
        "🏭 Eres magnate de la logística: llega la inspección de trabajo a los almacenes y "
        "los despidos se llaman «reajustes».",
        (
            "la inspectora de trabajo del almacén",
            "el delegado sindical del almacén",
            "el director de recursos humanos del grupo",
            "el abogado laboralista",
            "el subdelegado del Gobierno",
        ),
        (
            "la sanción por las horas extra",
            "el convenio del almacén",
            "el registro de jornada",
            "la plantilla fija del centro",
            "el pago de las horas atrasadas",
            "el acta de la inspección",
        ),
    ),
    "cohete": DealPack(
        "🚀 Eres multimillonario con cohete propio: la agencia espacial, el gobierno y el "
        "pueblo quieren cosas distintas, pagadas con dinero público.",
        (
            "el director de la agencia espacial",
            "la ministra de Ciencia",
            "la alcaldesa del pueblo de la base",
            "el inversor de la misión",
            "el periodista de tecnología",
        ),
        (
            "la subvención del lanzamiento",
            "la fecha del despegue",
            "el permiso de la base",
            "la tripulación del primer vuelo",
            "el precio del billete",
            "el seguro del cohete",
        ),
    ),
    # Funcionariado
    "inspeccion": DealPack(
        "🧾 Eres inspector de Hacienda: tienes que cerrar el expediente de un contribuyente "
        "que jura estar en la ruina.",
        (
            "el contribuyente inspeccionado",
            "la asesora fiscal del contribuyente",
            "el jefe de la dependencia",
            "el abogado del Estado",
            "la gerente de la empresa auditada",
        ),
        (
            "la regularización de la deuda",
            "los intereses de demora",
            "la sanción por ocultar ingresos",
            "el aplazamiento del pago",
            "la prescripción del ejercicio",
            "los gastos deducibles",
        ),
    ),
    "colada": DealPack(
        "🗂️ Eres secretario general técnico: todos quieren que el informe del ministerio "
        "pase por tu mesa sin que se note.",
        (
            "la directora general del ministerio",
            "el asesor jurídico del departamento",
            "el jefe de gabinete del ministro",
            "la secretaria de Estado del ramo",
            "el subdirector que redacta",
        ),
        (
            "el informe de la norma",
            "la tramitación urgente",
            "el texto de la disposición",
            "la fecha del Consejo de Ministros",
            "la memoria económica",
            "la enmienda que aparece de pronto",
        ),
    ),
    "subsecretarios": DealPack(
        "🗓️ Eres subsecretario en la comisión del lunes a las 9:00: se pacta hoy para que "
        "mañana parezca que nadie ha decidido nada.",
        (
            "el subsecretario de Presidencia",
            "la subsecretaria de Hacienda",
            "el secretario de Estado de Relaciones",
            "el subsecretario de Interior",
            "la directora de gabinete de Moncloa",
        ),
        (
            "el orden del día del Consejo",
            "los nombramientos de la semana",
            "el reparto del presupuesto",
            "el calendario de los decretos",
            "la coordinación con las comunidades",
            "el nombre del plan",
        ),
    ),
    "consejo_estado": DealPack(
        "🏛️ Eres consejero de Estado: el dictamen no es vinculante y por eso nadie lo lee, "
        "pero todos quieren que diga lo suyo.",
        (
            "el presidente del Consejo de Estado",
            "la ponente del dictamen",
            "el secretario general del Consejo",
            "el consejero nato",
            "el ministro que pide el dictamen",
        ),
        (
            "el dictamen sobre la reforma",
            "la observación que no molesta",
            "el plazo para emitirlo",
            "el voto particular",
            "la cita en la sala de plenos",
            "la memoria anual",
        ),
    ),
    "big_four": DealPack(
        "🏢 Eres socio de una Big Four: todo es legal hasta que un juez diga lo contrario, y "
        "eso tarda diez años.",
        (
            "el cliente de la multinacional",
            "el inspector de Hacienda del caso",
            "la directora financiera del grupo cliente",
            "la socia del área jurídica",
            "el auditor júnior",
        ),
        (
            "la estructura fiscal del grupo",
            "los honorarios de la auditoría",
            "el informe de precios de transferencia",
            "la consulta vinculante",
            "el dictamen para el cliente",
            "la planificación del ejercicio",
        ),
    ),
    "mariachis": DealPack(
        "🎺 Eres consejero de una SICAV: cinco accionistas en la sala, dinero de sobra y "
        "alguien que cuenta las sillas.",
        (
            "el gestor de la sociedad",
            "la depositaria del banco",
            "el inspector del regulador",
            "el cuñado accionista",
            "el presidente de la sociedad",
        ),
        (
            "el número de accionistas",
            "la comisión de gestión",
            "la tributación de la sociedad",
            "el reparto de las plusvalías",
            "la sede de la sociedad",
            "la junta de socios",
        ),
    ),
    "andorra": DealPack(
        "🏔️ Eres asesor de fortunas en Andorra: tu cliente vive aquí según los papeles y los "
        "vecinos de Chamberí no se enteran.",
        (
            "el cliente de la gran fortuna",
            "la gestora del banco andorrano",
            "el notario del principado",
            "el agente de residencia",
            "la inspectora de visita",
        ),
        (
            "la residencia fiscal del cliente",
            "los días de estancia",
            "el alquiler del piso de Andorra",
            "la comisión del banco",
            "el empadronamiento",
            "la declaración de bienes",
        ),
    ),
    # Influencer
    "plato": DealPack(
        "📺 Eres celebrity de reality: en el plató cada entrevista es un trato y el misterio "
        "paga la hipoteca.",
        (
            "la presentadora del programa",
            "el productor del reality",
            "el representante de la influencer",
            "el exconcursante rival",
            "la colaboradora del plató",
        ),
        (
            "la exclusiva de tu historia",
            "el caché de la entrevista",
            "el contrato con la revista",
            "la portada del especial",
            "el enfrentamiento con tu ex",
            "la cláusula de confidencialidad",
        ),
    ),
    "tertulia": DealPack(
        "🎙️ Eres tertuliano de las ocho, de las nueve y de las diez: opinas de todo, sabes de "
        "nada y no rectificas nunca.",
        (
            "la presentadora de la tertulia",
            "el director del programa de debate",
            "el tertuliano del bando contrario",
            "la productora de la mesa",
            "el contertulio de siempre",
        ),
        (
            "el tema del día",
            "el tiempo de palabra",
            "el caché por programa",
            "el titular del cierre",
            "la tertulia de la noche",
            "la rectificación que no llega",
        ),
    ),
    "share": DealPack(
        "📊 Eres director de cadena y el share manda: cada programa se defiende o se cancela "
        "según la audiencia de ayer.",
        (
            "el productor del programa estrella",
            "la directora de contenidos",
            "el anunciante principal de la cadena",
            "el presentador del prime time",
            "la jefa de programación",
        ),
        (
            "la franja del estreno",
            "el presupuesto de la temporada",
            "la cancelación del formato",
            "el contrato de la estrella",
            "el cambio de horario",
            "la publicidad en directo",
        ),
    ),
    "rtve": DealPack(
        "📡 Eres presidente de RTVE y el consejo se reparte por cuotas: todos los partidos "
        "quieren su silla.",
        (
            "la consejera de un partido",
            "el consejero de otro partido",
            "la directora de informativos",
            "el comité de informativos",
            "el administrador provisional",
        ),
        (
            "el reparto de las sillas del consejo",
            "la audiencia de los informativos",
            "el presupuesto del año",
            "la tertulia de la tarde",
            "el presentador del festival",
            "el canal que se cierra",
        ),
    ),
    "velada": DealPack(
        "🥊 Eres organizador de veladas de boxeo entre streamers: el combate es lo de menos, "
        "lo que vende es el cara a cara.",
        (
            "el streamer que pelea",
            "la agente del boxeador",
            "la plataforma de pago por evento",
            "el patrocinador de la velada",
            "el promotor del recinto",
        ),
        (
            "el cartel de la velada",
            "la bolsa de cada boxeador",
            "el precio de la entrada",
            "el combate de la revancha",
            "el cara a cara",
            "los derechos de emisión",
        ),
    ),
    "rugpull": DealPack(
        "🪙 Eres gurú cripto y lanzas tu moneda: todo el mundo quiere entrar, y salir ya se verá.",
        (
            "el inversor de la primera hora",
            "la influencer promocionadora",
            "el exchange que lista la moneda",
            "el desarrollador anónimo",
            "el fondo de inversión especulativo",
        ),
        (
            "el lanzamiento de la moneda",
            "la comisión por listarla",
            "el reparto de los tokens",
            "la promoción en redes",
            "el bloqueo de liquidez",
            "el airdrop de la comunidad",
        ),
    ),
    "partido": DealPack(
        "🏛️ Eres fundador de un partido antisistema con escaño: contra el sistema en directo "
        "y con el sueldo del sistema en el banco.",
        (
            "el presidente del grupo europeo",
            "la candidata de tu lista",
            "el eurodiputado disidente",
            "la periodista de investigación",
            "el donante de la campaña",
        ),
        (
            "los puestos de la lista",
            "el discurso de la campaña",
            "el sueldo de tu escaño",
            "la coalición para las europeas",
            "el reparto de los espacios en televisión",
            "el directo de la gala",
        ),
    ),
}
