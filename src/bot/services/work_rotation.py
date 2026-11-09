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


@dataclass(frozen=True, slots=True)
class AuditPack:
    """Contenido de una Auditoría.

    Attributes:
        scene: Lo que se enseña arriba toda la partida: quién eres y qué revisas.
        item: Cómo se llama cada casilla, en plural («partidas», «facturas»).
        bad: Cómo son las malas, en plural y concordando con `item` («infladas»).
        closed: Emoji de una casilla sin abrir.
        hit: Emoji de una mala que te ha saltado a la cara.
    """

    scene: str
    item: str
    bad: str
    closed: str
    hit: str


@dataclass(frozen=True, slots=True)
class DeskPack:
    """Contenido de un Despacho.

    Attributes:
        scene: Lo que se enseña arriba toda la partida: quién eres y qué llevas hoy.
        bars: Las 4 barras del cargo, con emoji y cortas («🤝 Socios»).
        cards: Al menos 10 decisiones: `(situación, opciones)`, con tres opciones
            `(etiqueta del botón, barra que sube, barra que baja)` (índices de `bars`).
            Cuánto sube y baja cada una lo sortea el juego en cada turno.
    """

    scene: str
    bars: tuple[str, str, str, str]
    cards: tuple[tuple[str, tuple[tuple[str, int, int], ...]], ...]


AUDIT_PACKS: dict[str, AuditPack] = {
    "sobrecostes": AuditPack(
        "🧾 Eres constructor y te ha llegado el presupuesto de la subcontrata: hay partidas "
        "infladas escondidas entre las buenas.",
        "partidas",
        "infladas",
        "🧾",
        "💸",
    ),
    "colados": AuditPack(
        "🌴 Eres director de resorts: en el buffet hay gente comiendo sin pulsera. Revisa las "
        "mesas sin montar un número en las que no toca.",
        "mesas",
        "de colados",
        "🍽️",
        "🙈",
    ),
    "inspector": AuditPack(
        "⭐ Eres chef con estrella: esta noche hay inspectores de la guía repartidos por la "
        "sala. Saluda a las mesas normales sin delatarte ante ellos.",
        "mesas",
        "de inspector",
        "🪑",
        "📝",
    ),
    "letra_pequena": AuditPack(
        "🩺 Eres médico de mutua: en la póliza hay cláusulas que lo excluyen casi todo. "
        "Repasa las que valen sin firmar las que no.",
        "cláusulas",
        "trampa",
        "📄",
        "🔍",
    ),
    "app_publica": AuditPack(
        "📱 Eres director de transformación digital: la app pública sale mañana y hay módulos "
        "que la tumban. Prueba los que funcionan sin tocar los otros.",
        "módulos",
        "rotos",
        "🧩",
        "💥",
    ),
    "runway": AuditPack(
        "🔥 Eres CEO de un unicornio: la caja se acaba y hay gastos que la queman. Repasa "
        "los razonables sin aprobar los otros.",
        "gastos",
        "disparatados",
        "💳",
        "🔥",
    ),
    "perdido": AuditPack(
        "📮 Eres director de operaciones de Correos: en el almacén hay sacas que llevan un "
        "año sin moverse. Da salida a las que van bien sin abrir las otras.",
        "sacas",
        "perdidas",
        "📦",
        "🕸️",
    ),
    "inspector_trabajo": AuditPack(
        "🏭 Eres magnate de la logística y viene la Inspección: en los almacenes hay turnos "
        "que no aguantan una revisión. Enseña los que están en regla.",
        "turnos",
        "irregulares",
        "📋",
        "🚨",
    ),
    "inspeccion": AuditPack(
        "🧾 Eres inspector de Hacienda: en la contabilidad de la empresa hay facturas falsas. "
        "Da por buenas las correctas sin sellar las otras.",
        "facturas",
        "falsas",
        "🧾",
        "🕵️",
    ),
    "colada": AuditPack(
        "🗂️ Eres secretario general técnico: en la ley que va mañana al Consejo alguien ha "
        "colado disposiciones. Valida las que tocan sin dar paso a las otras.",
        "disposiciones",
        "coladas",
        "📜",
        "🐍",
    ),
    "mariachis": AuditPack(
        "🎺 Eres consejero de una SICAV: entre los accionistas hay unos cuantos de relleno. "
        "Atiende a los de verdad sin preguntar de más a los otros.",
        "accionistas",
        "de relleno",
        "👤",
        "🎺",
    ),
    "share": AuditPack(
        "📊 Eres director de cadena: en la parrilla hay programas que hunden la audiencia. "
        "Renueva los que funcionan sin firmar los otros.",
        "programas",
        "hundidos",
        "📺",
        "📉",
    ),
    "rugpull": AuditPack(
        "🪙 Eres gurú cripto: entre los proyectos que te ofrecen promocionar hay timos. "
        "Mira los serios sin poner tu cara en los otros.",
        "proyectos",
        "timo",
        "🪙",
        "🧨",
    ),
}

DESK_PACKS: dict[str, DeskPack] = {
    # Obra
    "alcalde": DeskPack(
        "🏗️ Eres magnate inmobiliario y hoy toca despacho: el ayuntamiento, los vecinos y "
        "el banco no caben en la misma obra.",
        ("🏛️ Alcaldía", "💰 Caja", "🏘️ Vecinos", "🏗️ Obra"),
        (
            (
                "El ayuntamiento exige más zona verde en la macrourbanización.",
                (
                    ("Ceder una parcela", 2, 1),
                    ("Plantar cuatro palmeras", 0, 2),
                    ("Recortar el parque", 1, 0),
                ),
            ),
            (
                "El banco pide más garantías antes de soltar el crédito.",
                (
                    ("Hipotecar los solares", 1, 3),
                    ("Adelantar la preventa", 3, 2),
                    ("Pedir un aval público", 3, 0),
                ),
            ),
            (
                "Una asociación vecinal denuncia ruido de obra desde las siete de la mañana.",
                (
                    ("Retrasar el arranque", 2, 3),
                    ("Pagar el aislamiento", 2, 1),
                    ("Hablar con el concejal", 0, 2),
                ),
            ),
            (
                "La licencia de obras lleva tres meses parada en una mesa.",
                (
                    ("Presionar al concejal", 3, 0),
                    ("Esperar sentado", 1, 3),
                    ("Rehacer el proyecto", 0, 1),
                ),
            ),
            (
                "Piden convertir un bloque en pisos de protección oficial.",
                (
                    ("Aceptar la cuota", 2, 1),
                    ("Pagar una compensación", 0, 3),
                    ("Rechazarlo", 1, 0),
                ),
            ),
            (
                "Aparecen restos romanos en el solar de la torre.",
                (
                    ("Taparlos con cemento", 3, 0),
                    ("Financiar la excavación", 0, 1),
                    ("Abrir un museíto", 2, 3),
                ),
            ),
            (
                "Un periódico pregunta por las recalificaciones del último plan.",
                (
                    ("Enseñar los papeles", 2, 0),
                    ("Pagar publicidad", 0, 1),
                    ("Cambiar de tema", 3, 2),
                ),
            ),
            (
                "Los compradores de un bloque reclaman la fecha de entrega.",
                (("Entregar a medias", 2, 3), ("Indemnizar", 2, 1), ("Alargar el plazo", 1, 2)),
            ),
            (
                "El alcalde propone subir las tasas de la nueva avenida.",
                (
                    ("Aceptar la subida", 0, 1),
                    ("Negociar la mitad", 1, 0),
                    ("Pedir más edificabilidad", 3, 2),
                ),
            ),
            (
                "Un fondo extranjero quiere comprar medio barrio.",
                (
                    ("Vender al fondo", 1, 2),
                    ("Rechazar la oferta", 2, 1),
                    ("Asociarte con el fondo", 3, 0),
                ),
            ),
        ),
    ),
    "palco": DeskPack(
        "⚽ Eres presidente de un club de fútbol y hoy toca despacho: afición, vestuario, "
        "caja y concejal tiran cada uno de su lado.",
        ("🏟️ Afición", "💰 Caja", "👕 Vestuario", "🏛️ Concejal"),
        (
            (
                "El delantero estrella pide renovar con subida de sueldo.",
                (("Subirle la ficha", 2, 1), ("Venderlo", 1, 0), ("Mantener la oferta", 0, 2)),
            ),
            (
                "La grada ultra exige entradas gratis para el derbi.",
                (
                    ("Regalar las entradas", 0, 1),
                    ("Subir el abono", 1, 0),
                    ("Pactar un código ético", 3, 0),
                ),
            ),
            (
                "El concejal quiere reformar el estadio con dinero del club.",
                (
                    ("Aportar la mitad", 3, 1),
                    ("Pedir suelo a cambio", 1, 3),
                    ("Prometer estadio nuevo", 0, 1),
                ),
            ),
            (
                "El entrenador pide dos fichajes a mitad de temporada.",
                (
                    ("Fichar a los dos", 2, 1),
                    ("Fichar a un cedido", 0, 2),
                    ("Esperar al verano", 1, 0),
                ),
            ),
            (
                "El concejal ofrece el estadio municipal para un concierto en verano.",
                (
                    ("Aceptar el concierto", 3, 0),
                    ("Pedir parte de la taquilla", 1, 3),
                    ("Rechazarlo por el césped", 0, 3),
                ),
            ),
            (
                "Un tribunal deportivo sanciona al club por un cántico de la grada.",
                (
                    ("Pagar la multa", 0, 1),
                    ("Cerrar la grada", 3, 0),
                    ("Recurrir la sanción", 1, 3),
                ),
            ),
            (
                "El delantero suplente se queja de que no juega.",
                (
                    ("Darle minutos", 2, 0),
                    ("Ponerlo en venta", 1, 2),
                    ("Mejorarle el contrato", 2, 1),
                ),
            ),
            (
                "El concejal pide cerrar la calle los días de partido.",
                (("Cerrar la calle", 3, 0), ("Pagar lanzaderas", 0, 1), ("Pedir un parking", 1, 3)),
            ),
            (
                "La peña más antigua pide palco gratis por su aniversario.",
                (("Dárselo", 0, 1), ("Vender sus butacas", 1, 0), ("Invitar al concejal", 3, 2)),
            ),
            (
                "Hay que decidir si se vende al mejor jugador del club.",
                (("Venderlo", 1, 0), ("Retenerlo", 2, 1), ("Jugar al despiste", 0, 2)),
            ),
        ),
    ),
    "fomento": DeskPack(
        "🚄 Eres consejero delegado de una constructora y hoy toca despacho: el "
        "Ministerio, la plantilla y el calendario no se hablan.",
        ("🏛️ Gobierno", "💰 Caja", "👷 Plantilla", "🚧 Plazos"),
        (
            (
                "El Ministerio pide rebajar el presupuesto de un tramo del AVE.",
                (
                    ("Aceptar la rebaja", 0, 1),
                    ("Pedir un modificado", 1, 0),
                    ("Recortar la plantilla", 1, 2),
                ),
            ),
            (
                "La obra lleva dos meses de retraso por terrenos sin expropiar.",
                (
                    ("Pedir una prórroga", 3, 0),
                    ("Acelerar con turnos", 0, 2),
                    ("Pagar más por el suelo", 3, 1),
                ),
            ),
            (
                "El sindicato exige subir el convenio de la obra.",
                (
                    ("Pagar un plus", 2, 1),
                    ("Aplazar la negociación", 1, 2),
                    ("Subcontratar más", 3, 2),
                ),
            ),
            (
                "Una asociación ecologista denuncia el trazado por un humedal.",
                (
                    ("Desviar el trazado", 0, 3),
                    ("Encargar otro estudio", 3, 1),
                    ("Seguir adelante", 1, 0),
                ),
            ),
            (
                "Aparece una grieta en el viaducto recién entregado.",
                (
                    ("Repararla en silencio", 0, 1),
                    ("Inaugurar igual", 3, 0),
                    ("Cerrar y revisar", 2, 3),
                ),
            ),
            (
                "Una subcontrata quiebra y deja la obra sin hormigón.",
                (
                    ("Adelantar el pago", 3, 1),
                    ("Despedir a la subcontrata", 1, 2),
                    ("Pedir ayuda al Ministerio", 0, 3),
                ),
            ),
            (
                "El director general de Carreteras insinúa un modificado.",
                (
                    ("Aceptar el modificado", 1, 0),
                    ("Rechazarlo", 0, 1),
                    ("Ofrecerle un puesto", 0, 2),
                ),
            ),
            (
                "La plantilla quiere trabajar el sábado para recuperar plazo.",
                (
                    ("Pagar horas extra", 2, 1),
                    ("Contratar refuerzos", 2, 3),
                    ("Pedirlo sin extra", 3, 2),
                ),
            ),
            (
                "Un diputado reclama una parada de tren en su pueblo.",
                (
                    ("Prometer la estación", 0, 3),
                    ("Estudiar la viabilidad", 3, 0),
                    ("Negarse", 1, 0),
                ),
            ),
            (
                "Un túnel necesita dos meses más de excavación.",
                (
                    ("Pedir más plazo", 3, 0),
                    ("Doblar turnos", 0, 2),
                    ("Alquilar más máquinas", 2, 1),
                ),
            ),
        ),
    ),
    "internacional": DeskPack(
        "🌍 Eres presidente de una constructora del IBEX y hoy toca despacho: cobrar por "
        "adelantado es lo primero, lo demás ya se verá.",
        ("🌍 Clientes", "💰 Anticipos", "🏦 Bancos", "🤝 Embajada"),
        (
            (
                "Un país cliente quiere pagar la obra en tres plazos.",
                (
                    ("Exigir el anticipo", 1, 0),
                    ("Aceptar los plazos", 0, 1),
                    ("Pedir un aval", 2, 0),
                ),
            ),
            (
                "La embajada organiza una cena con el ministro local.",
                (("Ir con regalo", 3, 1), ("Mandar a un director", 0, 3), ("Excusarte", 1, 3)),
            ),
            (
                "Una tormenta de arena retrasa la obra en el desierto.",
                (
                    ("Alegar fuerza mayor", 2, 0),
                    ("Cubrir el sobrecoste", 0, 1),
                    ("Reprogramar entregas", 3, 2),
                ),
            ),
            (
                "El banco de desarrollo exige auditar las cuentas de la obra.",
                (("Abrir los libros", 2, 3), ("Dar un resumen", 3, 2), ("Cambiar de banco", 1, 2)),
            ),
            (
                "Un competidor ofrece hacer la obra un diez por ciento más barata.",
                (
                    ("Bajar el precio", 0, 1),
                    ("Alegar más calidad", 1, 0),
                    ("Pedir ayuda a la embajada", 3, 2),
                ),
            ),
            (
                "El país vecino quiere que subcontrates empresas locales.",
                (
                    ("Subcontratar locales", 0, 1),
                    ("Traer a los tuyos", 1, 0),
                    ("Crear una filial local", 3, 2),
                ),
            ),
            (
                "Cambia el Gobierno del país y quieren revisar el contrato.",
                (
                    ("Renegociar el contrato", 0, 1),
                    ("Ir al arbitraje", 1, 3),
                    ("Pedir amparo diplomático", 3, 0),
                ),
            ),
            (
                "Una moneda local se hunde y los anticipos valen menos.",
                (
                    ("Cobrar en divisas", 1, 0),
                    ("Cubrirte con el banco", 2, 1),
                    ("Subir el precio", 1, 2),
                ),
            ),
            (
                "Una ONG denuncia las condiciones del campamento de obra.",
                (
                    ("Mejorar los campamentos", 0, 1),
                    ("Hablar con la embajada", 3, 0),
                    ("Ignorar la denuncia", 1, 3),
                ),
            ),
            (
                "El banco ofrece refinanciar la deuda de la obra.",
                (
                    ("Aceptar la refinanciación", 2, 1),
                    ("Pagar al contado", 1, 2),
                    ("Pedir garantía estatal", 3, 2),
                ),
            ),
        ),
    ),
    # Hostelería
    "cadena_hotelera": DeskPack(
        "🏨 Eres consejero delegado de una cadena hotelera y hoy toca despacho: el fondo "
        "quiere margen y las reseñas quieren toallas limpias.",
        ("📈 Fondo", "👥 Plantilla", "⭐ Reseñas", "💰 Margen"),
        (
            (
                "El fondo pide subir el margen un tres por ciento este trimestre.",
                (
                    ("Recortar limpieza", 0, 2),
                    ("Subir las tarifas", 3, 2),
                    ("Congelar contrataciones", 0, 1),
                ),
            ),
            (
                "El comité de empresa pide fijos para las camareras de piso.",
                (
                    ("Hacerlas fijas", 1, 3),
                    ("Externalizar los pisos", 3, 1),
                    ("Prometer fijos futuros", 0, 1),
                ),
            ),
            (
                "Un vídeo viral cuenta que las toallas llevan tres semanas sin cambiarse.",
                (
                    ("Regalar una noche", 2, 3),
                    ("Despedir a la gobernanta", 0, 1),
                    ("Desmentirlo", 3, 2),
                ),
            ),
            (
                "Llega la temporada alta y faltan camareros en tres hoteles.",
                (
                    ("Doblar turnos", 3, 1),
                    ("Pagar un plus de verano", 1, 3),
                    ("Cerrar una planta", 2, 3),
                ),
            ),
            (
                "Un propietario quiere renegociar el alquiler del hotel.",
                (
                    ("Subir la renta", 0, 3),
                    ("Amenazar con irte", 3, 0),
                    ("Reformar la fachada", 2, 3),
                ),
            ),
            (
                "El director propone cobrar un extra por la tumbona.",
                (
                    ("Cobrar la tumbona", 3, 2),
                    ("Dejarla incluida", 2, 3),
                    ("Repartir pulseras", 0, 2),
                ),
            ),
            (
                "Los clientes se quejan del ruido de la obra del ala nueva.",
                (
                    ("Descontar la estancia", 2, 3),
                    ("Parar la obra", 2, 0),
                    ("Seguir con la obra", 0, 2),
                ),
            ),
            (
                "Un buscador de reservas sube su comisión.",
                (
                    ("Pagar la comisión", 2, 3),
                    ("Dejar el buscador", 3, 2),
                    ("Lanzar una web propia", 1, 0),
                ),
            ),
            (
                "El fondo quiere vender el hotel de playa a otro fondo.",
                (
                    ("Aceptar la venta", 0, 1),
                    ("Recomprar el hotel", 1, 0),
                    ("Proponer alquilarlo", 3, 1),
                ),
            ),
            (
                "La plantilla pide subir los sueldos por la inflación.",
                (
                    ("Subir un cinco por ciento", 1, 3),
                    ("Dar solo un bono", 1, 0),
                    ("Negarte a subir", 0, 1),
                ),
            ),
        ),
    ),
    "patronal": DeskPack(
        "🏛️ Eres presidente de la patronal turística y hoy toca despacho: hay mesa con "
        "todos y siempre hay crisis salvo en el balance.",
        ("🏖️ Hoteleros", "✊ Sindicatos", "🏛️ Gobierno", "📰 Opinión"),
        (
            (
                "Los sindicatos piden subir el convenio un doce por ciento.",
                (
                    ("Ofrecer un cuatro", 0, 1),
                    ("Aceptar la mitad", 1, 0),
                    ("Plantarte en la mesa", 0, 2),
                ),
            ),
            (
                "El Gobierno plantea limitar los pisos turísticos.",
                (
                    ("Apoyar el límite", 2, 0),
                    ("Pedir una moratoria", 0, 2),
                    ("Culpar a las plataformas", 3, 2),
                ),
            ),
            (
                "Un informe dice que la mitad de los contratos de hostelería son de cuatro horas.",
                (
                    ("Encargar otro informe", 2, 3),
                    ("Prometer fijos", 1, 0),
                    ("Hablar de empleo récord", 3, 1),
                ),
            ),
            (
                "Hay ruido por el turismo masivo en los barrios del centro.",
                (
                    ("Proponer una tasa", 2, 0),
                    ("Hablar de convivencia", 3, 2),
                    ("Defender el modelo", 0, 3),
                ),
            ),
            (
                "La temporada baja pide ayudas para no cerrar los hoteles.",
                (
                    ("Pedir ayudas directas", 0, 2),
                    ("Pedir rebajas fiscales", 0, 1),
                    ("Ofrecer abrir en invierno", 1, 0),
                ),
            ),
            (
                "Una huelga amenaza el puente de agosto.",
                (
                    ("Ceder en el plus", 1, 0),
                    ("Pedir servicios mínimos", 2, 1),
                    ("Aguantar el pulso", 0, 3),
                ),
            ),
            (
                "Se habla de un impuesto a las pernoctaciones.",
                (
                    ("Aceptarlo con matices", 2, 0),
                    ("Oponerte en público", 3, 2),
                    ("Pedir que sea voluntario", 0, 3),
                ),
            ),
            (
                "Los camareros de piso piden jubilación anticipada.",
                (("Apoyar la medida", 1, 0), ("Proponer un estudio", 2, 1), ("Rechazarla", 0, 1)),
            ),
            (
                "La región quiere imponer un cupo de visitantes.",
                (
                    ("Negociar un cupo flexible", 2, 0),
                    ("Rechazar los cupos", 0, 3),
                    ("Aceptar el cupo", 3, 0),
                ),
            ),
            (
                "Un fondo compra tres hoteles históricos para venderlos como residencias.",
                (
                    ("Pedir que se proteja", 3, 2),
                    ("Mirar hacia otro lado", 2, 3),
                    ("Ofrecer ayuda al gobierno", 2, 1),
                ),
            ),
        ),
    ),
    "dubai": DeskPack(
        "🏜️ Eres gurú gastronómico con franquicias en Dubái y hoy toca despacho: el "
        "inversor pone el dinero y tú pones el oro.",
        ("⭐ Estrellas", "💰 Inversor", "🍽️ Cocina", "📸 Redes"),
        (
            (
                "El inversor quiere que los platos lleven pan de oro.",
                (
                    ("Cubrirlo todo de oro", 1, 0),
                    ("Oro solo en el postre", 2, 1),
                    ("Negarte al brillo", 0, 1),
                ),
            ),
            (
                "La guía envía a un inspector a probar la carta nueva.",
                (
                    ("Cuidar cada plato", 0, 3),
                    ("Hacer la carta famosa", 3, 0),
                    ("Servir lo de siempre", 2, 3),
                ),
            ),
            (
                "Un centro comercial pide abrir otra sucursal en dos meses.",
                (
                    ("Abrir deprisa", 1, 0),
                    ("Pedir seis meses", 0, 1),
                    ("Abrir con carta corta", 2, 3),
                ),
            ),
            (
                "Un cocinero estrella se va a la competencia.",
                (("Subirle el sueldo", 2, 1), ("Dejarlo marchar", 1, 2), ("Hacerlo socio", 0, 1)),
            ),
            (
                "Una influencer pide mesa gratis y fotos en cocina.",
                (
                    ("Invitarla", 3, 2),
                    ("Cobrarle la cuenta", 1, 3),
                    ("Darle una mesa discreta", 2, 3),
                ),
            ),
            (
                "La espuma de la carta ha dado intoxicación a dos clientes.",
                (("Retirar la espuma", 2, 3), ("Pagar a los afectados", 0, 1), ("Negarlo", 3, 0)),
            ),
            (
                "El proveedor sube el precio del azafrán un cuarenta por ciento.",
                (
                    ("Subir el menú", 1, 0),
                    ("Usar menos azafrán", 2, 0),
                    ("Cambiar de proveedor", 0, 2),
                ),
            ),
            (
                "El socio local quiere un menú sin alcohol ni cerdo.",
                (
                    ("Adaptar la carta", 1, 0),
                    ("Mantener la carta", 0, 1),
                    ("Abrir un salón aparte", 3, 2),
                ),
            ),
            (
                "Te proponen un programa de cocina con vuelos en jet.",
                (
                    ("Aceptar el programa", 3, 2),
                    ("Seguir en el pase", 2, 3),
                    ("Pedir cachet", 1, 3),
                ),
            ),
            (
                "La carta de seis horas ya dura nueve.",
                (
                    ("Acortar la carta", 1, 0),
                    ("Servir el pase largo", 0, 2),
                    ("Hacerlo en directo", 3, 0),
                ),
            ),
        ),
    ),
    # Política
    "control": DeskPack(
        "🏛️ Eres presidente del Gobierno y hoy toca despacho: cada decisión contenta a unos "
        "y cabrea a otros.",
        ("🤝 Socios", "📰 Prensa", "📣 Calle", "💰 Caja"),
        (
            (
                "Tu socio de investidura pide el traspaso de Cercanías.",
                (("Dárselo", 0, 2), ("Crear una comisión", 1, 0), ("Ponerle precio", 3, 0)),
            ),
            (
                "Sale una encuesta mala a tres semanas de las autonómicas.",
                (("Anunciar ayudas", 2, 3), ("Entrevista amable", 1, 2), ("Cerrar filas", 0, 1)),
            ),
            (
                "Bruselas pide un ajuste antes de soltar los fondos.",
                (
                    ("Subir impuestos", 3, 2),
                    ("Recortar a los socios", 3, 0),
                    ("Ganar tiempo", 2, 3),
                ),
            ),
            (
                "Un ministro la lía en una entrevista de radio.",
                (("Cesarlo", 1, 0), ("Respaldarlo", 0, 1), ("Sacar otro tema", 2, 1)),
            ),
            (
                "Los transportistas amenazan con parar el país.",
                (
                    ("Bonificar el gasóleo", 2, 3),
                    ("Aguantar el pulso", 3, 2),
                    ("Culpar a la UE", 1, 0),
                ),
            ),
            (
                "Hay que aprobar los presupuestos y faltan cuatro votos.",
                (
                    ("Comprar los votos", 0, 3),
                    ("Prorrogar los viejos", 3, 1),
                    ("Ir a la calle", 2, 0),
                ),
            ),
            (
                "Un periódico prepara una exclusiva sobre tu partido.",
                (
                    ("Dar otra exclusiva", 1, 0),
                    ("Desmentir en la calle", 2, 1),
                    ("Pagar publicidad", 1, 3),
                ),
            ),
            (
                "Los pensionistas piden subir las pensiones con el IPC.",
                (("Subirlas", 2, 3), ("Subirlas a medias", 3, 2), ("Pactarlo con socios", 0, 3)),
            ),
            (
                "Una comunidad pide la condonación de su deuda.",
                (("Condonarla", 0, 3), ("Café para todos", 2, 3), ("Decir que no", 3, 0)),
            ),
            (
                "La oposición convoca una manifestación el domingo.",
                (("Ignorarla", 1, 2), ("Contraprogramar", 2, 3), ("Tender la mano", 1, 0)),
            ),
        ),
    ),
    "consejo": DeskPack(
        "💡 Eres consejero de una eléctrica tras dejar el cargo y hoy toca despacho: tu "
        "agenda de contactos vale más que cualquier central.",
        ("📈 Dividendo", "🏠 Clientes", "📞 Agenda", "⚖️ Regulador"),
        (
            (
                "Los accionistas piden más dividendo este año.",
                (
                    ("Repartir más", 0, 1),
                    ("Repartir solo la mitad", 1, 0),
                    ("Invertir en renovables", 3, 0),
                ),
            ),
            (
                "La factura de la luz sube y los clientes protestan.",
                (
                    ("Congelar tarifas", 1, 0),
                    ("Hablar de impuestos", 2, 1),
                    ("Ofrecer un bono social", 3, 0),
                ),
            ),
            (
                "El regulador abre un expediente por precios abusivos.",
                (
                    ("Colaborar con el regulador", 3, 2),
                    ("Llamar a un conocido", 2, 3),
                    ("Recurrir la multa", 0, 3),
                ),
            ),
            (
                "Un exministro del consejo pide subir sus honorarios.",
                (("Subirlos", 2, 0), ("Mantenerlos", 0, 2), ("Pagarle por conferencias", 2, 1)),
            ),
            (
                "Un apagón deja medio barrio sin luz una tarde.",
                (
                    ("Indemnizar a los vecinos", 1, 0),
                    ("Culpar al clima", 0, 1),
                    ("Invertir en la red", 3, 0),
                ),
            ),
            (
                "Nace un partido que promete nacionalizar las eléctricas.",
                (
                    ("Invitarlo a un café", 2, 3),
                    ("Hacer campaña de imagen", 1, 0),
                    ("Pagar a un tertuliano", 0, 1),
                ),
            ),
            (
                "La CNMC quiere topar los beneficios de la compañía.",
                (
                    ("Aceptar el tope", 3, 0),
                    ("Pedir una excepción", 0, 3),
                    ("Mover la sede fiscal", 0, 1),
                ),
            ),
            (
                "Un ministro pide que se contrate a su cuñado.",
                (
                    ("Contratar al cuñado", 2, 3),
                    ("Ofrecerle una beca", 3, 2),
                    ("Decir que no", 1, 2),
                ),
            ),
            (
                "Una central cierra y los empleados piden recolocación.",
                (("Recolocarlos", 1, 0), ("Prejubilarlos", 2, 1), ("Cerrar sin avisar", 0, 2)),
            ),
            (
                "Un tertuliano acusa a la compañía de cobrar por el sol.",
                (("Contestar en directo", 1, 2), ("Enviarle una cesta", 2, 1), ("Ignorarlo", 3, 1)),
            ),
        ),
    ),
    "trilogo": DeskPack(
        "🇪🇺 Eres vicepresidente del Parlamento Europeo y hoy toca despacho: todos quieren "
        "un acuerdo, siempre que lo firme otro.",
        ("🗳️ Grupo", "🤝 Consejo", "🏢 Comisión", "📰 Titulares"),
        (
            (
                "El Consejo propone retrasar la norma de plásticos.",
                (
                    ("Aceptar el retraso", 1, 0),
                    ("Ceder en la mitad", 0, 1),
                    ("Dejar el texto como está", 3, 1),
                ),
            ),
            (
                "La Comisión presenta un informe de impacto de mil páginas.",
                (
                    ("Pedir un resumen", 2, 3),
                    ("Leerlo por encima", 3, 2),
                    ("Pedir una enmienda", 0, 2),
                ),
            ),
            (
                "Tu grupo exige incluir una enmienda sobre la agricultura.",
                (("Incluirla", 0, 1), ("Negociarla", 1, 0), ("Dejarla fuera", 2, 0)),
            ),
            (
                "La prensa cuenta que llevas un año sin votar una enmienda.",
                (
                    ("Votar a lo grande", 3, 0),
                    ("Aclarar el malentendido", 3, 2),
                    ("Cambiar de tema", 1, 3),
                ),
            ),
            (
                "Un lobby ofrece financiar un viaje de estudios.",
                (
                    ("Aceptar el viaje", 1, 3),
                    ("Rechazarlo con foto", 3, 1),
                    ("Pedir reunión formal", 2, 3),
                ),
            ),
            (
                "El presidente rotatorio quiere cerrar el expediente antes de Navidad.",
                (
                    ("Cerrar deprisa", 1, 2),
                    ("Pedir más tiempo", 0, 1),
                    ("Cerrar con letra pequeña", 3, 0),
                ),
            ),
            (
                "Dos grupos no se ponen de acuerdo en una coma.",
                (
                    ("Proponer una coma neutra", 2, 0),
                    ("Votar la coma", 0, 2),
                    ("Aplazar la coma", 1, 0),
                ),
            ),
            (
                "La Comisión quiere una tasa nueva para financiarse.",
                (("Apoyar la tasa", 2, 3), ("Frenar la tasa", 0, 2), ("Pedir un estudio", 3, 1)),
            ),
            (
                "Un estado miembro amenaza con vetar el paquete.",
                (("Ceder al veto", 1, 0), ("Plantarle cara", 3, 1), ("Ofrecer fondos", 1, 2)),
            ),
            (
                "El acuerdo final es tan ambiguo que ambos lados lo celebran.",
                (("Celebrarlo juntos", 3, 0), ("Pedir claridad", 0, 3), ("Firmar sin leer", 2, 3)),
            ),
        ),
    ),
    "comisario": DeskPack(
        "🏢 Eres comisario europeo y hoy toca despacho: cada decisión es un trato con "
        "quien puede bloquearla.",
        ("🏰 Capitales", "🗳️ Cámara", "💼 Lobbies", "📜 Normativa"),
        (
            (
                "Una capital pide más margen para su déficit.",
                (("Dar margen", 0, 3), ("Mantener la regla", 3, 0), ("Pedir un plan", 1, 0)),
            ),
            (
                "La Cámara quiere endurecer tu directiva.",
                (
                    ("Aceptar los cambios", 1, 2),
                    ("Defender el texto", 3, 1),
                    ("Pactar con ellos", 1, 0),
                ),
            ),
            (
                "Un lobby tecnológico pide una exención en la ley de datos.",
                (("Dar la exención", 2, 3), ("Negarla", 3, 2), ("Retrasar la norma", 0, 3)),
            ),
            (
                "Una capital amenaza con bloquear el presupuesto.",
                (
                    ("Prometer fondos", 0, 3),
                    ("Aguantar el pulso", 3, 0),
                    ("Hablar con la Cámara", 1, 0),
                ),
            ),
            (
                "Un informe filtrado cuestiona el reparto de fondos.",
                (("Publicarlo entero", 1, 0), ("Negarlo", 2, 1), ("Culpar a la prensa", 0, 1)),
            ),
            (
                "La presidencia quiere simplificar mil páginas de normas.",
                (
                    ("Simplificar a lo grande", 3, 2),
                    ("Aplazar la reforma", 2, 3),
                    ("Dejarlo en un anexo", 0, 3),
                ),
            ),
            (
                "Un sector agrícola protesta con tractores en Bruselas.",
                (
                    ("Ofrecer ayudas", 0, 3),
                    ("Hablar de transición", 1, 0),
                    ("Mantener la norma", 3, 0),
                ),
            ),
            (
                "Una gran empresa pide una reunión a puerta cerrada.",
                (("Recibirla", 2, 1), ("Hacerla pública", 1, 2), ("Mandar a un asesor", 3, 2)),
            ),
            (
                "La Cámara pide tu dimisión por un fallo del programa.",
                (("Pedir perdón", 1, 3), ("Resistir", 2, 1), ("Culpar a la anterior", 0, 1)),
            ),
            (
                "Una capital pide ver un informe antes que las demás.",
                (("Enseñárselo", 0, 1), ("Dárselo a todos", 1, 0), ("Hacerte el despistado", 3, 0)),
            ),
        ),
    ),
    # Sanidad
    "gerencia": DeskPack(
        "📊 Eres gerente del hospital y hoy toca despacho: la Consejería quiere resultados "
        "sin gastar más y el hospital, gastar sin resultados.",
        ("🩺 Plantilla", "🛏️ Pacientes", "💰 Caja", "🏛️ Región"),
        (
            (
                "Urgencias está al límite y hay camas en el pasillo.",
                (
                    ("Abrir una planta más", 1, 2),
                    ("Derivar a otro centro", 3, 1),
                    ("Pedir refuerzos", 0, 3),
                ),
            ),
            (
                "La Consejería pide reducir la lista de espera un veinte por ciento.",
                (
                    ("Operar por la tarde", 3, 0),
                    ("Concertar quirófanos", 1, 2),
                    ("Cambiar las cifras", 3, 1),
                ),
            ),
            (
                "Los médicos piden cubrir las bajas de verano.",
                (
                    ("Contratar sustitutos", 0, 2),
                    ("Repartir las guardias", 2, 0),
                    ("Cerrar plantas", 2, 1),
                ),
            ),
            (
                "La máquina de resonancias lleva un mes averiada.",
                (
                    ("Comprar una nueva", 1, 2),
                    ("Alquilar una externa", 1, 3),
                    ("Reparar la vieja", 2, 1),
                ),
            ),
            (
                "Un sindicato convoca huelga de enfermería.",
                (
                    ("Ceder en turnos", 0, 2),
                    ("Pedir servicios mínimos", 3, 0),
                    ("Hablar con la Consejería", 3, 1),
                ),
            ),
            (
                "Los pacientes se quejan de la comida del hospital.",
                (
                    ("Cambiar la empresa", 1, 2),
                    ("Subir el menú", 1, 3),
                    ("Ignorar las quejas", 2, 1),
                ),
            ),
            (
                "La Consejería quiere inaugurar el ala nueva sin plantilla.",
                (("Inaugurarla", 3, 0), ("Posponerla", 0, 3), ("Abrirla a medias", 1, 2)),
            ),
            (
                "Un médico jefe pide dejar la gerencia por un cargo mejor.",
                (("Mejorarle el puesto", 0, 2), ("Dejarlo marchar", 2, 0), ("Promocionarlo", 3, 2)),
            ),
            (
                "Un periodista pregunta por los contratos de la limpieza.",
                (
                    ("Contestar claro", 1, 3),
                    ("Pasarle el expediente", 3, 1),
                    ("Llamar al director", 3, 0),
                ),
            ),
            (
                "Llega un brote de gripe más fuerte de lo previsto.",
                (
                    ("Suspender consultas", 1, 0),
                    ("Pedir material", 0, 2),
                    ("Pedir voluntarios", 3, 0),
                ),
            ),
        ),
    ),
    "consejeria": DeskPack(
        "🏛️ Eres consejero de Sanidad y hoy toca despacho: las listas de espera, los "
        "médicos y las cuentas tienen que cuadrar a la vez.",
        ("🩺 Médicos", "👥 Ciudadanos", "💰 Hacienda", "🗳️ Escaños"),
        (
            (
                "La oposición saca a la luz las listas de espera.",
                (("Anunciar un plan", 1, 2), ("Culpar al Estado", 3, 1), ("Abrir los datos", 1, 3)),
            ),
            (
                "Los médicos exigen revisar el sistema de guardias.",
                (
                    ("Subir las guardias", 0, 2),
                    ("Pedir paciencia", 2, 0),
                    ("Crear una comisión", 3, 0),
                ),
            ),
            (
                "Hacienda recorta el presupuesto de sanidad un cinco por ciento.",
                (
                    ("Aceptar el recorte", 2, 1),
                    ("Plantarte en el pleno", 1, 2),
                    ("Recortar la plantilla", 2, 0),
                ),
            ),
            (
                "Se cierra el hospital de una comarca pequeña.",
                (
                    ("Cerrarlo", 2, 1),
                    ("Mantenerlo a medias", 1, 2),
                    ("Hacerte una foto allí", 3, 1),
                ),
            ),
            (
                "Un hospital nuevo no tiene personal para abrir.",
                (
                    ("Inaugurar sin personal", 3, 0),
                    ("Contratar de golpe", 0, 2),
                    ("Esperar un año", 0, 3),
                ),
            ),
            (
                "Una plataforma ciudadana recoge firmas por la lista de espera.",
                (("Recibirlos", 1, 3), ("Desmentirlos", 3, 1), ("Ofrecer un teléfono", 3, 2)),
            ),
            (
                "Los sindicatos piden estabilizar a los interinos.",
                (
                    ("Estabilizar a todos", 0, 2),
                    ("Hacerlo por fases", 3, 0),
                    ("Prometer una oposición", 2, 0),
                ),
            ),
            (
                "Hay que decidir si se privatizan las lavanderías.",
                (
                    ("Externalizarlas", 2, 1),
                    ("Mantenerlas públicas", 1, 2),
                    ("Hacer un estudio", 3, 1),
                ),
            ),
            (
                "El presidente quiere anunciar un hospital por sorpresa.",
                (
                    ("Anunciarlo ya", 3, 2),
                    ("Pedir un estudio", 2, 3),
                    ("Pedir los fondos antes", 0, 3),
                ),
            ),
            (
                "Una pandemia nueva aparece en las noticias.",
                (
                    ("Declarar la alerta", 1, 3),
                    ("Quitarle importancia", 3, 1),
                    ("Comprar material", 0, 2),
                ),
            ),
        ),
    ),
    "conciertos": DeskPack(
        "🤝 Eres consejero delegado de un grupo hospitalario y hoy toca despacho: la "
        "pública no da abasto y tú tienes quirófanos libres.",
        ("🤝 Consejería", "💰 Margen", "🩺 Médicos", "⭐ Prestigio"),
        (
            (
                "La Consejería quiere concertarte seiscientas operaciones.",
                (
                    ("Aceptar el concierto", 0, 3),
                    ("Subir la tarifa", 1, 0),
                    ("Pedir un contrato largo", 1, 3),
                ),
            ),
            (
                "Los médicos piden cobrar más por operar de tarde.",
                (
                    ("Pagar un plus", 2, 1),
                    ("Contratar fuera", 1, 2),
                    ("Prometer horas libres", 2, 3),
                ),
            ),
            (
                "Un periódico habla de tus quirófanos subvencionados.",
                (("Enseñar las cuentas", 3, 1), ("Pagar una campaña", 3, 0), ("Callar", 1, 3)),
            ),
            (
                "La pública deriva pacientes complicados a tu clínica.",
                (("Aceptarlos", 0, 1), ("Rechazarlos", 1, 0), ("Derivar a especialistas", 3, 2)),
            ),
            (
                "Un fondo quiere comprar parte del grupo.",
                (
                    ("Vender una parte", 1, 3),
                    ("Rechazar la oferta", 3, 1),
                    ("Buscar un socio", 0, 3),
                ),
            ),
            (
                "Un paciente denuncia una factura inesperada.",
                (
                    ("Devolverle el dinero", 3, 1),
                    ("Alegar el contrato", 1, 3),
                    ("Ofrecer una revisión", 3, 2),
                ),
            ),
            (
                "Un cirujano famoso se va a la competencia.",
                (("Igualar la oferta", 2, 1), ("Dejarlo ir", 1, 2), ("Pagarle una consulta", 2, 3)),
            ),
            (
                "La Comunidad cambia al partido que firmó el concierto.",
                (("Renegociar", 0, 1), ("Dar una cena", 0, 3), ("Esperar", 1, 0)),
            ),
            (
                "Hay lista de espera en tu clínica privada.",
                (
                    ("Operar fines de semana", 3, 2),
                    ("Contratar cirujanos", 2, 1),
                    ("Derivar a la pública", 0, 3),
                ),
            ),
            (
                "La Consejería pide auditar la facturación.",
                (
                    ("Abrir las cuentas", 0, 1),
                    ("Contratar auditor propio", 3, 1),
                    ("Resistir", 1, 0),
                ),
            ),
        ),
    ),
    "aseguradora": DeskPack(
        "📈 Eres dueño de una aseguradora de salud y hoy toca despacho: lo que cobras es "
        "prima, lo que pagas es siniestralidad.",
        ("📈 Junta", "👥 Asegurados", "🏥 Hospitales", "💰 Primas"),
        (
            (
                "La junta pide subir los beneficios dos puntos.",
                (
                    ("Subir las primas", 0, 1),
                    ("Recortar cobertura", 3, 1),
                    ("Bajar tarifas a hospitales", 0, 2),
                ),
            ),
            (
                "Un hospital concertado sube sus precios.",
                (
                    ("Aceptar la subida", 2, 3),
                    ("Cambiar de hospital", 1, 2),
                    ("Negociar la mitad", 3, 2),
                ),
            ),
            (
                "Un asegurado reclama una prueba que la póliza excluye.",
                (
                    ("Cubrirla", 1, 0),
                    ("Alegar la letra pequeña", 0, 1),
                    ("Ofrecer descuento", 1, 3),
                ),
            ),
            (
                "La actuaria avisa de que sube la siniestralidad.",
                (("Subir la prima", 3, 1), ("Dejarlo estar", 1, 3), ("Revisar los casos", 0, 3)),
            ),
            (
                "Un tertuliano critica las exclusiones de tu póliza.",
                (
                    ("Contestar en el programa", 1, 0),
                    ("Comprar publicidad", 0, 3),
                    ("Cambiar la letra", 1, 3),
                ),
            ),
            (
                "Un hospital del grupo pide ampliar quirófanos.",
                (("Invertir", 2, 3), ("Aplazar", 3, 2), ("Pedir un préstamo", 2, 0)),
            ),
            (
                "Los asegurados jóvenes se cambian a la competencia.",
                (
                    ("Bajar la prima joven", 1, 3),
                    ("Hacer campaña de redes", 2, 1),
                    ("Dejarlos ir", 0, 1),
                ),
            ),
            (
                "La administración abre una inspección de seguros.",
                (("Colaborar", 1, 3), ("Alegar el cumplimiento", 0, 2), ("Cambiar de jefe", 3, 0)),
            ),
            (
                "Se acerca un invierno de gripe fuerte.",
                (
                    ("Reforzar cobertura", 1, 0),
                    ("Subir los copagos", 3, 1),
                    ("Comprar vacunas", 2, 3),
                ),
            ),
            (
                "El fondo inversor ofrece comprar la mayoría.",
                (("Vender", 0, 2), ("Rechazar la compra", 2, 0), ("Buscar socio", 3, 1)),
            ),
        ),
    ),
    # Oficina
    "comite": DeskPack(
        "🤝 Eres socio de la consultora y hoy toca despacho: aquí se factura la hora, no "
        "el resultado, y todos quieren su parte del cliente.",
        ("💼 Cliente", "⏱️ Horas", "👥 Equipo", "🤝 Socios"),
        (
            (
                "El cliente pide terminar el informe una semana antes.",
                (("Prometerlo", 0, 2), ("Pedir más horas", 1, 0), ("Subcontratar", 1, 3)),
            ),
            (
                "El director de ventas quiere vender un proyecto nuevo.",
                (
                    ("Aceptarlo", 3, 2),
                    ("Esperar a otro trimestre", 2, 3),
                    ("Cobrar la propuesta", 1, 0),
                ),
            ),
            (
                "El equipo lleva tres fines de semana trabajando.",
                (("Darles un día libre", 2, 1), ("Dar un bono", 2, 3), ("Seguir igual", 3, 2)),
            ),
            (
                "Un cliente discute la factura de horas.",
                (
                    ("Rebajar la factura", 0, 1),
                    ("Justificar las horas", 1, 0),
                    ("Pedir una reunión", 3, 0),
                ),
            ),
            (
                "Un júnior pide ascenso tras un año.",
                (("Ascenderlo", 2, 1), ("Prometer otro año", 1, 2), ("Pasarle más horas", 1, 3)),
            ),
            (
                "Otro socio pide un reparto de beneficios distinto.",
                (("Aceptar el cambio", 3, 1), ("Negociar", 1, 3), ("Quedarte como estás", 2, 3)),
            ),
            (
                "La licitación está hecha a medida de otra consultora.",
                (("Retirarte", 2, 0), ("Presentarte igual", 0, 2), ("Hacer una alianza", 3, 0)),
            ),
            (
                "Un cliente pide una presentación bonita sin datos.",
                (
                    ("Hacerla sin datos", 0, 1),
                    ("Añadir los datos", 1, 0),
                    ("Pedir un anticipo", 3, 0),
                ),
            ),
            (
                "RR. HH. propone un viernes de pizza y juegos.",
                (("Aprobarlo", 2, 1), ("Descartarlo", 1, 2), ("Cobrarlo al cliente", 3, 0)),
            ),
            (
                "Un colega dice que el proyecto es inviable.",
                (("Escucharlo", 2, 0), ("No hacerle caso", 0, 2), ("Cambiar el alcance", 1, 3)),
            ),
        ),
    ),
    "socio_director": DeskPack(
        "🏛️ Eres socio director de la consultora y hoy toca despacho: el Gobierno de "
        "turno tiene que seguir siendo cliente, gane quien gane.",
        ("🏛️ Gabinete", "💼 Socios", "💰 Facturas", "📞 Contactos"),
        (
            (
                "Un gabinete pide rebajar un diez por ciento el contrato.",
                (
                    ("Rebajar el precio", 0, 2),
                    ("Ofrecer más horas", 2, 0),
                    ("Pedir un modificado", 2, 3),
                ),
            ),
            (
                "Cambia el partido del Gobierno y llegan nuevos directores.",
                (
                    ("Invitarlos a comer", 3, 1),
                    ("Contratar a su asesor", 3, 2),
                    ("Esperar a que llamen", 1, 3),
                ),
            ),
            (
                "Un socio quiere dejar la firma y llevarse clientes.",
                (("Ofrecerle más reparto", 1, 2), ("Dejarlo ir", 2, 1), ("Pleitear", 0, 1)),
            ),
            (
                "La licitación pública sale con requisitos a tu medida.",
                (("Presentarte", 2, 0), ("Pedir más requisitos", 0, 3), ("Retirarte", 1, 2)),
            ),
            (
                "La app pública que desarrolláis se cae el primer día.",
                (
                    ("Asumir el fallo", 1, 0),
                    ("Culpar al proveedor", 0, 1),
                    ("Cobrar el arreglo", 2, 0),
                ),
            ),
            (
                "Un periódico publica un correo con la palabra «comisión».",
                (("Desmentirlo", 3, 1), ("Aclararlo", 0, 3), ("Pagar una auditoría", 1, 2)),
            ),
            (
                "Un cliente nuevo no paga hasta dentro de seis meses.",
                (
                    ("Esperar al cobro", 2, 1),
                    ("Aplicar intereses", 1, 0),
                    ("Pedir un anticipo", 2, 0),
                ),
            ),
            (
                "La patronal tecnológica pide apoyo para un manifiesto.",
                (("Firmarlo", 3, 1), ("Negarte con elegancia", 1, 3), ("Mandar a un socio", 0, 3)),
            ),
            (
                "El equipo pide retribución por el hackatón.",
                (("Pagarlo", 1, 2), ("Dar una camiseta", 2, 1), ("Ofrecer un puesto", 3, 1)),
            ),
            (
                "Un directivo del cliente te pide un favor sin papeles.",
                (("Hacerlo", 0, 2), ("Pedir por escrito", 2, 0), ("Escurrir el bulto", 1, 3)),
            ),
        ),
    ),
    "red_social": DeskPack(
        "📢 Eres tecnomillonario dueño de una red social y hoy toca despacho: anunciantes, "
        "reguladores y usuarios discuten el derecho a opinar, sobre todo el tuyo.",
        ("💰 Anuncios", "⚖️ Regulador", "👥 Usuarios", "😎 Ego"),
        (
            (
                "Un anunciante grande amenaza con retirar su publicidad.",
                (
                    ("Cambiar las reglas", 0, 3),
                    ("Plantarte", 3, 0),
                    ("Ofrecerle un descuento", 0, 2),
                ),
            ),
            (
                "El regulador multa a la red por no moderar contenido.",
                (("Pagar la multa", 1, 0), ("Recurrir", 3, 1), ("Contratar moderadores", 1, 3)),
            ),
            (
                "Los usuarios piden volver al orden cronológico.",
                (("Aceptar el cambio", 2, 0), ("Ignorarlos", 0, 2), ("Hacer una encuesta", 3, 2)),
            ),
            (
                "Un moderador despedido filtra las normas internas.",
                (("Publicar las normas", 2, 3), ("Demandarlo", 3, 2), ("Negarlo en directo", 3, 1)),
            ),
            (
                "Te piden quitar una cuenta famosa por insultos.",
                (("Suspenderla", 1, 3), ("Dejarla", 0, 1), ("Ponerle un aviso", 2, 0)),
            ),
            (
                "Un bot inunda la red con mensajes falsos.",
                (("Bloquearlo", 2, 3), ("Ignorarlo", 0, 2), ("Cobrar la verificación", 0, 1)),
            ),
            (
                "La red quiere cobrar por tener tick azul.",
                (
                    ("Cobrarlo", 0, 2),
                    ("Mantenerlo gratis", 2, 0),
                    ("Venderlo al mejor postor", 3, 1),
                ),
            ),
            (
                "Una periodista cuenta tus gastos en viajes.",
                (
                    ("Bloquearla", 3, 2),
                    ("Contestarle en público", 2, 1),
                    ("Invitarla a cenar", 3, 0),
                ),
            ),
            (
                "Una ley europea obliga a abrir los algoritmos.",
                (
                    ("Cumplir la ley", 1, 3),
                    ("Amenazar con irte", 3, 1),
                    ("Abrir solo una parte", 1, 2),
                ),
            ),
            (
                "Se cae la red la noche de unas elecciones.",
                (
                    ("Pedir disculpas", 2, 3),
                    ("Culpar a un hacker", 3, 1),
                    ("Prometer más servidores", 2, 0),
                ),
            ),
        ),
    ),
    # Reparto
    "direccion_correos": DeskPack(
        "📮 Eres director general de Correos y hoy toca despacho: si la empresa pierde "
        "dinero es servicio público y si gana, gestión eficiente.",
        ("📮 Servicio", "✊ Sindicatos", "🏛️ Ministro", "💰 Caja"),
        (
            (
                "El ministerio quiere cerrar tres oficinas rurales.",
                (("Cerrarlas", 3, 0), ("Mantenerlas", 0, 2), ("Abrir una al día", 1, 3)),
            ),
            (
                "Los sindicatos piden más plantilla para el reparto.",
                (("Contratar", 1, 3), ("Pagar horas extra", 1, 0), ("Subcontratar", 3, 1)),
            ),
            (
                "Una saca de cartas aparece en un contenedor.",
                (
                    ("Abrir una investigación", 2, 3),
                    ("Culparse en la prensa", 0, 2),
                    ("Cerrar el caso", 3, 0),
                ),
            ),
            (
                "La paquetería privada se queda con los clientes grandes.",
                (
                    ("Bajar el precio", 0, 3),
                    ("Mejorar la entrega", 0, 1),
                    ("Pedir una ayuda pública", 3, 2),
                ),
            ),
            (
                "Hace calor y los repartidores piden cambiar el turno.",
                (("Cambiar el turno", 1, 0), ("Dar más agua", 1, 3), ("Mantener el turno", 0, 1)),
            ),
            (
                "Un pueblo pide que vuelva el cartero diario.",
                (
                    ("Devolver el servicio", 0, 2),
                    ("Hacerlo dos días", 3, 0),
                    ("Mandar una furgoneta", 0, 1),
                ),
            ),
            (
                "El director financiero avisa de pérdidas este trimestre.",
                (
                    ("Recortar rutas", 3, 0),
                    ("Pedir una inyección", 3, 2),
                    ("Privatizar rutas", 2, 1),
                ),
            ),
            (
                "Una app nueva promete seguimiento en tiempo real.",
                (
                    ("Contratar la app", 2, 3),
                    ("Desarrollar la propia", 0, 3),
                    ("Esperar a otro año", 3, 0),
                ),
            ),
            (
                "Se acumulan las quejas por cartas que llegan tarde.",
                (("Pedir perdón", 0, 3), ("Culpar al sistema", 2, 0), ("Abrir otro centro", 1, 3)),
            ),
            (
                "Una elección se acerca y hay voto por correo.",
                (
                    ("Reforzar la plantilla", 1, 3),
                    ("Pedir ayuda a la policía", 2, 1),
                    ("Cuadrar turnos", 0, 1),
                ),
            ),
        ),
    ),
    "presidencia_correos": DeskPack(
        "🏛️ Eres presidente de Correos y hoy toca despacho: lo tuyo es la lealtad, no la "
        "contabilidad, y hoy tocan nombramientos y pérdidas.",
        ("🤝 Lealtad", "📊 Cuentas", "✊ Comité", "📰 Prensa"),
        (
            (
                "El ministro que te nombró quiere colocar a un conocido.",
                (
                    ("Contratarlo", 0, 3),
                    ("Darle un contrato menor", 0, 2),
                    ("Negarte al favor", 1, 0),
                ),
            ),
            (
                "El auditor pide revisar las cuentas del año.",
                (
                    ("Abrir los libros", 1, 0),
                    ("Pedir más tiempo", 0, 3),
                    ("Contratar otro auditor", 0, 1),
                ),
            ),
            (
                "El comité de empresa pide subir el sueldo de los carteros.",
                (("Subirlo", 2, 1), ("Hacerlo por fases", 1, 2), ("Hablar de la inflación", 3, 2)),
            ),
            (
                "Un reportaje revela las pérdidas de la empresa.",
                (("Dar la cara", 3, 0), ("Callar", 0, 3), ("Hablar de servicio público", 3, 1)),
            ),
            (
                "El consejo propone un plan de reestructuración.",
                (("Aprobarlo", 1, 2), ("Aplazarlo", 2, 1), ("Presentarlo en prensa", 3, 1)),
            ),
            (
                "Hay que elegir al director de una región.",
                (("Elegir al amigo", 0, 1), ("Elegir al mejor", 1, 0), ("Hacer un concurso", 3, 0)),
            ),
            (
                "La competencia ofrece comprar el servicio de paquetería.",
                (("Vender", 1, 2), ("Cerrar la puerta", 2, 1), ("Alquilar rutas", 1, 0)),
            ),
            (
                "La plantilla pide más seguridad en los reparto rurales.",
                (("Comprar furgonetas", 2, 1), ("Dar formación", 2, 3), ("Aplazar el gasto", 1, 2)),
            ),
            (
                "Una consejera pide explicaciones por los viajes de la empresa.",
                (
                    ("Enseñar los billetes", 1, 0),
                    ("Hablar de eficiencia", 3, 1),
                    ("Hablar del tiempo", 0, 3),
                ),
            ),
            (
                "Se acerca una huelga el día de Reyes.",
                (
                    ("Ceder en turnos", 2, 1),
                    ("Pedir servicios mínimos", 3, 2),
                    ("Hablar con el ministro", 0, 2),
                ),
            ),
        ),
    ),
    "cohete": DeskPack(
        "🚀 Eres multimillonario con cohete propio y hoy toca despacho: la agencia, el "
        "pueblo y los inversores quieren cosas distintas pagadas con dinero público.",
        ("🚀 Misión", "🏛️ Agencia", "💰 Subvención", "🏘️ Pueblo"),
        (
            (
                "La agencia espacial pide adelantar el lanzamiento un mes.",
                (("Adelantarlo", 1, 0), ("Pedir más fondos", 0, 1), ("Mantener la fecha", 3, 1)),
            ),
            (
                "El pueblo de la base protesta por el ruido de los motores.",
                (
                    ("Pagar insonorización", 3, 2),
                    ("Dar entradas gratis", 3, 0),
                    ("Ignorarlos", 0, 3),
                ),
            ),
            (
                "Un tanque falla en la prueba de presión.",
                (
                    ("Repetir la prueba", 0, 2),
                    ("Arreglar en silencio", 2, 0),
                    ("Enseñarlo al público", 1, 2),
                ),
            ),
            (
                "Hace falta una subvención para el próximo cohete.",
                (
                    ("Pedirla en grande", 2, 3),
                    ("Pedirla con detalle", 1, 2),
                    ("Dar un discurso", 2, 1),
                ),
            ),
            (
                "La alcaldesa pide una escuela nueva en el pueblo.",
                (("Financiarla", 3, 2), ("Prometerla", 1, 3), ("Hablar de empleo", 3, 0)),
            ),
            (
                "Un inversor quiere un viaje turístico al espacio.",
                (("Vender el viaje", 2, 0), ("Posponerlo", 0, 3), ("Hacer un sorteo", 3, 2)),
            ),
            (
                "La ministra pide un informe de impacto ambiental.",
                (
                    ("Hacer el informe", 1, 0),
                    ("Retrasar el informe", 0, 1),
                    ("Contratar una consultora", 1, 2),
                ),
            ),
            (
                "Un ingeniero denuncia falta de seguridad.",
                (("Escucharlo", 0, 2), ("Despedirlo", 2, 0), ("Mejorarle el sueldo", 0, 1)),
            ),
            (
                "El cohete cae al mar lejos de la costa.",
                (
                    ("Celebrar el éxito parcial", 3, 1),
                    ("Pedir perdón", 1, 2),
                    ("Pedir otro lanzamiento", 0, 1),
                ),
            ),
            (
                "La prensa pregunta quién paga el cohete.",
                (("Hablar de ciencia", 1, 2), ("Hablar de empleo", 3, 0), ("Callar", 2, 3)),
            ),
        ),
    ),
    # Funcionariado
    "subsecretarios": DeskPack(
        "🗓️ Eres subsecretario en la comisión del lunes a las 9:00 y hoy toca despacho: "
        "se pacta hoy para que mañana parezca que nadie ha decidido nada.",
        ("🏛️ Ministro", "🗂️ Trámite", "🤝 Pacto", "🕶️ Sigilo"),
        (
            (
                "El ministro quiere llevar un decreto sin informe previo.",
                (
                    ("Saltarse el informe", 0, 1),
                    ("Pedir el informe", 1, 0),
                    ("Hacerlo en urgencia", 2, 3),
                ),
            ),
            (
                "Otro ministerio no firma si no se incluye su enmienda.",
                (("Incluirla", 2, 1), ("Negociarla", 1, 2), ("Quitarla en secreto", 3, 2)),
            ),
            (
                "Un periódico tiene el borrador del decreto.",
                (("Desmentir", 3, 0), ("Confirmar", 0, 3), ("Hacerlo oficial", 1, 3)),
            ),
            (
                "La comisión se alarga hasta las tres de la tarde.",
                (("Cerrar con un pacto", 2, 1), ("Aplazar a mañana", 1, 2), ("Dar un café", 2, 3)),
            ),
            (
                "Hay que redactar el BOE a las 23:59.",
                (
                    ("Publicar tal cual", 1, 3),
                    ("Corregir la coma", 3, 1),
                    ("Dejarlo al día siguiente", 0, 1),
                ),
            ),
            (
                "Un secretario de Estado pide un nombramiento a dedo.",
                (("Nombrarlo", 2, 1), ("Abrir una convocatoria", 1, 2), ("Dar largas", 3, 2)),
            ),
            (
                "La oposición pregunta en el Congreso por el decreto.",
                (("Responder largo", 3, 1), ("Responder poco", 0, 3), ("Dar los datos", 1, 0)),
            ),
            (
                "Un ministro pide cambiar el texto a última hora.",
                (("Cambiarlo", 0, 2), ("Decir que es tarde", 2, 0), ("Hacer una nota", 1, 3)),
            ),
            (
                "Dos subsecretarios discuten por una competencia.",
                (("Repartirla", 2, 1), ("Quedarte con ella", 0, 2), ("Crear una comisión", 1, 0)),
            ),
            (
                "Llega un recurso que invalida un trámite.",
                (
                    ("Repetir el trámite", 1, 0),
                    ("Ignorar el recurso", 3, 1),
                    ("Pedir un informe", 3, 0),
                ),
            ),
        ),
    ),
    "consejo_estado": DeskPack(
        "🏛️ Eres consejero de Estado y hoy toca despacho: el dictamen no es vinculante y "
        "por eso nadie lo lee, pero todos quieren que diga lo suyo.",
        ("📜 Dictamen", "🎩 Peso", "🤝 Gobierno", "⏳ Plazo"),
        (
            (
                "El Gobierno pide un dictamen favorable a su ley.",
                (
                    ("Redactarlo favorable", 2, 0),
                    ("Escribir con reservas", 0, 2),
                    ("Pedir más tiempo", 3, 2),
                ),
            ),
            (
                "La ponente propone cambiar un párrafo clave.",
                (
                    ("Aceptar el cambio", 0, 2),
                    ("Mantener el párrafo", 2, 0),
                    ("Añadir una nota", 1, 3),
                ),
            ),
            (
                "Un consejero nato no ha leído el expediente.",
                (("Dejar que vote", 3, 0), ("Resumirle todo", 0, 3), ("Aplazar el pleno", 3, 1)),
            ),
            (
                "El presidente del Consejo quiere cerrar antes del puente.",
                (("Cerrarlo ya", 3, 0), ("Hacer el pleno largo", 0, 3), ("Votar a ciegas", 3, 1)),
            ),
            (
                "Un periódico critica que nadie lee los dictámenes.",
                (
                    ("Publicar un resumen", 1, 3),
                    ("Hacer una nota de prensa", 1, 2),
                    ("Dejarlo pasar", 3, 1),
                ),
            ),
            (
                "El Gobierno quiere saltarse el dictamen por urgencia.",
                (("Protestar", 1, 2), ("Aceptar la urgencia", 2, 1), ("Mandar un borrador", 3, 0)),
            ),
            (
                "Un exministro del Consejo defiende otra postura.",
                (("Escucharlo", 1, 0), ("Callarlo con tacto", 0, 1), ("Votar contra él", 2, 1)),
            ),
            (
                "La norma choca con una sentencia reciente.",
                (
                    ("Señalar el choque", 0, 2),
                    ("Pasarlo por alto", 2, 0),
                    ("Pedir una aclaración", 3, 2),
                ),
            ),
            (
                "Te ofrecen un cargo más alto en el Consejo.",
                (
                    ("Aceptarlo", 1, 2),
                    ("Rechazarlo con humildad", 0, 1),
                    ("Pedir algo a cambio", 2, 3),
                ),
            ),
            (
                "Hay que decidir si se publica el voto particular.",
                (("Publicarlo", 0, 2), ("Guardarlo", 2, 1), ("Reducirlo a una nota", 3, 0)),
            ),
        ),
    ),
    "andorra": DeskPack(
        "🏔️ Eres asesor de fortunas en Andorra y hoy toca despacho: tu cliente vive aquí "
        "según los papeles y nadie en Chamberí se entera.",
        ("🏔️ Cliente", "🏦 Banco", "🕵️ Hacienda", "🪪 Residencia"),
        (
            (
                "El cliente quiere comprar un yate sin que se note.",
                (("Ponerlo a nombre de otro", 1, 2), ("Declararlo", 2, 0), ("Alquilarlo", 3, 0)),
            ),
            (
                "El banco andorrano pide justificar el origen de los fondos.",
                (("Enseñar los papeles", 1, 0), ("Dar largas", 0, 1), ("Mover el dinero", 0, 2)),
            ),
            (
                "Hacienda abre un expediente por residencia fiscal.",
                (("Probar los días", 2, 0), ("Recurrir", 3, 2), ("Pagar algo", 2, 3)),
            ),
            (
                "El cliente quiere vivir tres meses en Madrid.",
                (
                    ("Contar los días", 3, 0),
                    ("Cambiar los billetes", 0, 3),
                    ("Alquilar un piso aquí", 3, 2),
                ),
            ),
            (
                "Un periodista pregunta por una sociedad en Panamá.",
                (("Negar", 1, 2), ("Contestar claro", 2, 0), ("Llamar al abogado", 1, 3)),
            ),
            (
                "El cliente propone una donación a una fundación.",
                (("Hacerla", 2, 1), ("Esperar un año", 0, 2), ("Dar menos", 1, 0)),
            ),
            (
                "Un notario pide papeles que no existen.",
                (("Crearlos", 3, 2), ("Cambiar de notario", 0, 3), ("Pedir un plazo", 3, 1)),
            ),
            (
                "La residencia pide demostrar actividad en el país.",
                (
                    ("Montar una oficina", 3, 1),
                    ("Alquilar un piso", 3, 2),
                    ("Contratar a un amigo", 0, 2),
                ),
            ),
            (
                "El cliente critica tu comisión.",
                (("Bajarla", 0, 1), ("Justificarla", 1, 0), ("Ofrecer otro servicio", 3, 0)),
            ),
            (
                "Un banco ofrece tipos mejores si mueves la cartera.",
                (("Mover la cartera", 1, 0), ("Quedarte", 0, 1), ("Dividirla", 2, 3)),
            ),
        ),
    ),
    # Influencer
    "rtve": DeskPack(
        "📡 Eres presidente de RTVE y hoy toca despacho: el consejo se reparte por cuotas "
        "y todos los partidos quieren su silla.",
        ("🏛️ Partidos", "📺 Audiencia", "🗞️ Redacción", "💰 Caja"),
        (
            (
                "Un partido pide más minutos en el informativo.",
                (("Dárselos", 0, 2), ("Dar la mitad", 2, 0), ("Hacer un debate", 1, 0)),
            ),
            (
                "La audiencia cae en la franja de tarde.",
                (
                    ("Fichar a un tertuliano", 1, 3),
                    ("Estrenar un concurso", 1, 2),
                    ("Repetir el programa", 3, 1),
                ),
            ),
            (
                "La redacción denuncia manipulación en un titular.",
                (("Corregirlo", 2, 0), ("Mantenerlo", 0, 2), ("Abrir un debate", 2, 1)),
            ),
            (
                "El consejo propone un director de informativos nuevo.",
                (("Aprobarlo", 0, 2), ("Vetarlo", 2, 0), ("Pedir un concurso", 3, 0)),
            ),
            (
                "Un programa de humor critica a un partido.",
                (("Mantenerlo", 1, 0), ("Cancelarlo", 0, 1), ("Cambiar la hora", 3, 1)),
            ),
            (
                "La corporación pierde dinero por los derechos del fútbol.",
                (("Renunciar al fútbol", 3, 1), ("Pagar más", 1, 3), ("Compartir derechos", 0, 1)),
            ),
            (
                "Hay que decidir el concierto de las campanadas.",
                (
                    ("Elegir al favorito", 1, 3),
                    ("Elegir al barato", 3, 1),
                    ("Hacer un casting", 2, 3),
                ),
            ),
            (
                "Una periodista veterana pide más libertad de edición.",
                (("Dársela", 2, 0), ("Aplazar la petición", 0, 2), ("Subirle el sueldo", 2, 3)),
            ),
            (
                "Un diputado llama para quejarse de una entrevista.",
                (
                    ("Disculparte", 0, 2),
                    ("Responder por carta", 3, 0),
                    ("Ignorar la llamada", 2, 0),
                ),
            ),
            (
                "La audiencia pide un informativo más breve.",
                (("Acortarlo", 1, 2), ("Dejarlo igual", 2, 1), ("Añadir deportes", 1, 3)),
            ),
        ),
    ),
    "partido": DeskPack(
        "🏛️ Eres fundador de un partido antisistema con escaño y hoy toca despacho: "
        "contra el sistema en directo y con el sueldo del sistema en el banco.",
        ("🗳️ Votantes", "✊ Militancia", "💰 Sueldo", "📰 Prensa"),
        (
            (
                "La militancia pide revisar el sueldo de los cargos.",
                (
                    ("Bajar los sueldos", 1, 2),
                    ("Subir las dietas", 2, 1),
                    ("Votar en asamblea", 1, 3),
                ),
            ),
            (
                "Un periodista descubre tu chalet en el campo.",
                (
                    ("Enseñarlo", 3, 0),
                    ("Decir que es de un amigo", 2, 3),
                    ("Cambiar de tema", 0, 3),
                ),
            ),
            (
                "Un eurodiputado de tu lista vota con el enemigo.",
                (("Expulsarlo", 1, 3), ("Pedir explicaciones", 3, 1), ("Perdonarlo", 2, 1)),
            ),
            (
                "Las encuestas bajan y hay que cambiar el discurso.",
                (
                    ("Subir el tono", 1, 0),
                    ("Suavizarlo", 0, 1),
                    ("Fichar a una cara conocida", 3, 1),
                ),
            ),
            (
                "Te ofrecen un cargo en un gobierno de coalición.",
                (("Aceptar", 2, 0), ("Rechazar", 1, 2), ("Pedir una consejería", 0, 1)),
            ),
            (
                "La asamblea quiere echar al secretario.",
                (("Defenderlo", 2, 1), ("Dejar que caiga", 1, 3), ("Convocar una votación", 1, 2)),
            ),
            (
                "Una cadena de tele quiere pagarte por una tertulia.",
                (("Ir", 2, 0), ("Rechazar", 1, 2), ("Mandar a un portavoz", 3, 1)),
            ),
            (
                "Un diario acusa de pagos opacos a tu fundación.",
                (("Abrir las cuentas", 0, 3), ("Demandar", 3, 0), ("Guardar silencio", 2, 3)),
            ),
            (
                "Un sector crítico pide que se refunde el partido.",
                (("Refundar", 1, 0), ("Resistir", 2, 1), ("Cambiar de logo", 0, 1)),
            ),
            (
                "Hay que decidir si se pacta con la casta.",
                (("Pactar", 2, 0), ("Negarse", 0, 2), ("Pactar en secreto", 2, 3)),
            ),
        ),
    ),
}
