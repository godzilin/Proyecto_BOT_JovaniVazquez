// Constantes de la luz de la hora dorada, compartidas por cielo, mar y sol.
import { Color, Vector3 } from "three";

export const DIR_SOL = new Vector3(-0.62, 0.36, 0.5).normalize();
export const COLOR_SOL = new Color(0xffd29a);
export const COLOR_HORIZONTE = new Color(0xf3cfa4);
export const COLOR_CENIT = new Color(0x5f9fe0);
