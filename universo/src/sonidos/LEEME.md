# Sonidos de las tragaperras de la plaza

MP3 mono de 44,1 kHz (~80 kbps) que usa la página del Jovaniverso. Cada uno es una
mezcla de capas sintetizadas con numpy y de capas de paquetes de Kenney
(https://kenney.nl), todos con licencia CC0 (dominio público, sin atribución
obligatoria: http://creativecommons.org/publicdomain/zero/1.0/). Lo genera `docs/sonidos_tragas.py`; no se editan a mano.

Regenerar (necesita red la primera vez, ffmpeg y numpy):

    .venv/Scripts/python docs/sonidos_tragas.py

Es determinista: semillas fijas y codificación bit a bit repetible.

## Paquetes externos

- **Casino Audio 1.1**, de Kenney Vleugels: https://kenney.nl/assets/casino-audio (licencia CC0).
- **Interface Sounds**, de Kenney Vleugels: https://kenney.nl/assets/interface-sounds (licencia CC0).
- **Impact Sounds**, de Kenney Vleugels: https://kenney.nl/assets/impact-sounds (licencia CC0).

## Archivos

### `boton.mp3`
- Sintetizado: clic seco (ruido agudo + tono de 1,9 kHz + golpecito grave).
- Externas (paquete, archivo original, URL, licencia):
  - Interface Sounds, `Audio/switch_002.ogg`, https://kenney.nl/assets/interface-sounds, CC0.

### `girar.mp3`
- Sintetizado: motor que sube (diente de sierra 70-300 Hz) y trinquete que se acelera.
- Externas (paquete, archivo original, URL, licencia):
  - Casino Audio 1.1, `Audio/dice-shake-2.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Impact Sounds, `Audio/impactMetal_light_001.ogg`, https://kenney.nl/assets/impact-sounds, CC0.

### `parada.mp3`
- Sintetizado: golpe grave (110 a 50 Hz) + chasquido agudo + resonancia metálica de 620 Hz.
- Externas (paquete, archivo original, URL, licencia):
  - Impact Sounds, `Audio/impactMetal_medium_002.ogg`, https://kenney.nl/assets/impact-sounds, CC0.

### `parada_lenta.mp3`
- Sintetizado: tics de trinquete que se frenan + golpe grave profundo + campana FM de 330 Hz.
- Externas (paquete, archivo original, URL, licencia):
  - Impact Sounds, `Audio/impactBell_heavy_000.ogg`, https://kenney.nl/assets/impact-sounds, CC0.
  - Impact Sounds, `Audio/impactMetal_heavy_001.ogg`, https://kenney.nl/assets/impact-sounds, CC0.

### `tension.mp3`
- Sintetizado: solo sintetizado: dos tonos que suben 2 octavas, trémolo que se acelera, ruido agudo y latido.
- Externas: ninguna (solo sintetizado).

### `moneda_1.mp3`
- Sintetizado: dos parciales agudos y una campanilla FM (tono distinto en cada variante).
- Externas (paquete, archivo original, URL, licencia):
  - Casino Audio 1.1, `Audio/chips-collide-1.ogg`, https://kenney.nl/assets/casino-audio, CC0.

### `moneda_2.mp3`
- Sintetizado: dos parciales agudos y una campanilla FM (tono distinto en cada variante).
- Externas (paquete, archivo original, URL, licencia):
  - Casino Audio 1.1, `Audio/chips-collide-2.ogg`, https://kenney.nl/assets/casino-audio, CC0.

### `moneda_3.mp3`
- Sintetizado: dos parciales agudos y una campanilla FM (tono distinto en cada variante).
- Externas (paquete, archivo original, URL, licencia):
  - Casino Audio 1.1, `Audio/chips-collide-3.ogg`, https://kenney.nl/assets/casino-audio, CC0.

### `casi.mp3`
- Sintetizado: tres notas de metal que se desinflan (sol, mi, do con caída de tono) y un soplido de ruido.
- Externas (paquete, archivo original, URL, licencia):
  - Interface Sounds, `Audio/drop_002.ogg`, https://kenney.nl/assets/interface-sounds, CC0.

### `premio.mp3`
- Sintetizado: arpegio de campanillas FM (do-mi-sol-do) y acorde de metal de do mayor.
- Externas (paquete, archivo original, URL, licencia):
  - Casino Audio 1.1, `Audio/chips-handle-2.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-stack-2.ogg`, https://kenney.nl/assets/casino-audio, CC0.

### `granpremio.mp3`
- Sintetizado: arpegio de siete campanillas, acorde de metal de cuatro notas, brillos y lluvia de tintineos.
- Externas (paquete, archivo original, URL, licencia):
  - Casino Audio 1.1, `Audio/chips-handle-1.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-handle-3.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-handle-5.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Impact Sounds, `Audio/impactBell_heavy_001.ogg`, https://kenney.nl/assets/impact-sounds, CC0.

### `mega.mp3`
- Sintetizado: dos arpegios (do y re mayor), acorde de metal de cinco notas, golpe grave y lluvia de monedas.
- Externas (paquete, archivo original, URL, licencia):
  - Casino Audio 1.1, `Audio/chips-stack-1.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-stack-3.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-stack-5.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Impact Sounds, `Audio/impactBell_heavy_002.ogg`, https://kenney.nl/assets/impact-sounds, CC0.

### `epico.mp3`
- Sintetizado: subida de ruido y seno, bombo, tres acordes de metal, cascada de campanillas y monedas.
- Externas (paquete, archivo original, URL, licencia):
  - Casino Audio 1.1, `Audio/chips-stack-2.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-stack-4.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-stack-6.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Impact Sounds, `Audio/impactBell_heavy_003.ogg`, https://kenney.nl/assets/impact-sounds, CC0.
  - Impact Sounds, `Audio/impactBell_heavy_004.ogg`, https://kenney.nl/assets/impact-sounds, CC0.
  - Impact Sounds, `Audio/impactMetal_heavy_002.ogg`, https://kenney.nl/assets/impact-sounds, CC0.

### `bote.mp3`
- Sintetizado: acordes de metal, campanas FM que repican en bucle, golpes graves y lluvia densa de monedas.
- Externas (paquete, archivo original, URL, licencia):
  - Casino Audio 1.1, `Audio/chips-handle-1.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-handle-2.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-handle-3.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-handle-4.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-stack-1.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-stack-2.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-stack-3.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Casino Audio 1.1, `Audio/chips-stack-4.ogg`, https://kenney.nl/assets/casino-audio, CC0.
  - Impact Sounds, `Audio/impactBell_heavy_000.ogg`, https://kenney.nl/assets/impact-sounds, CC0.
  - Impact Sounds, `Audio/impactBell_heavy_001.ogg`, https://kenney.nl/assets/impact-sounds, CC0.
  - Impact Sounds, `Audio/impactBell_heavy_004.ogg`, https://kenney.nl/assets/impact-sounds, CC0.

### `gratis.mp3`
- Sintetizado: carrera pentatónica de campanillas, brillo con trémolo de 12 Hz y acorde suave de metal.
- Externas (paquete, archivo original, URL, licencia):
  - Interface Sounds, `Audio/confirmation_003.ogg`, https://kenney.nl/assets/interface-sounds, CC0.
  - Interface Sounds, `Audio/glass_003.ogg`, https://kenney.nl/assets/interface-sounds, CC0.

### `auto.mp3`
- Sintetizado: dos pitidos de interfaz (784 y 1175 Hz) con caída suave.
- Externas (paquete, archivo original, URL, licencia):
  - Interface Sounds, `Audio/toggle_002.ogg`, https://kenney.nl/assets/interface-sounds, CC0.
