"""
Ingranaggi a evolvente (spur gear a denti dritti) generati matematicamente,
non lasciati al modello LLM -- che fallirebbe quasi certamente provando a
derivare da solo la geometria della curva a evolvente (più complessa persino
dei poliedri platonici, che già falliva a calcolare in questa stessa sessione).

Geometria verificata numericamente prima di scrivere questo modulo (non
solo "sembra giusta"):
- raggi (base, primitivo, radice, esterno) confrontati con le formule
  standard ISO per dentatura a modulo normale (addendum=modulo,
  dedendum=1.25*modulo) -- esatti su un caso noto (modulo=2, denti=20)
- il profilo di un singolo dente verificato per raggio di partenza/arrivo
  e simmetria dei due fianchi
- l'ingranaggio completo (tutti i denti) verificato per monotonia
  angolare (nessuna auto-intersezione), raggi estremi esatti, e area
  plausibile (compresa tra area del cerchio di radice e area del cerchio
  esterno)

Costruzione 3D: profilo 2D (poligono chiuso che approssima l'evolvente con
punti) + estrusione + foro centrale opzionale -- usa SOLO operazioni
CadQuery già note per funzionare in questa pipeline (polyline, close,
extrude, faces/hole), nessuna nuova API rischiosa o libreria esterna non
verificabile.

LIMITE ONESTO: il profilo dell'evolvente è approssimato da un numero finito
di punti (non una curva analitica esatta) -- sufficiente per una
rappresentazione meccanicamente corretta e per stampa 3D/prototipazione,
non necessariamente per specifiche di produzione di precisione industriale.
Il raccordo alla radice tra un dente e l'altro è un arco semplice sul
cerchio di radice, non il vero raccordo trocoidale che produrrebbe un
utensile di taglio reale -- approssimazione comune e adeguata per la
grande maggioranza degli usi (visualizzazione, stampa 3D, prototipi).
"""

import numpy as np
import cadquery as cq


def _involute_xy(rb, t):
    """Punto sulla curva a evolvente di un cerchio base di raggio rb,
    parametrizzato dall'angolo di rotolamento t (radianti)."""
    x = rb * (np.cos(t) + t * np.sin(t))
    y = rb * (np.sin(t) - t * np.cos(t))
    return x, y


def _involute_angle_at_radius(rb, r):
    """Angolo di rotolamento t per cui l'evolvente raggiunge il raggio r."""
    ratio_sq = (r / rb) ** 2 - 1
    return np.sqrt(max(ratio_sq, 0))


def _gear_profile_points(module, teeth, pressure_angle_deg=20.0, points_per_flank=6, points_root_arc=3):
    """
    Calcola i punti del profilo 2D completo dell'ingranaggio (tutti i denti),
    in coordinate cartesiane, pronti per .polyline().

    Sistema di dentatura a modulo normale ISO (addendum=modulo,
    dedendum=1.25*modulo) -- lo standard più comune per ingranaggi a denti
    dritti.
    """
    phi = np.radians(pressure_angle_deg)
    pitch_r = module * teeth / 2
    base_r = pitch_r * np.cos(phi)
    outside_r = pitch_r + module
    root_r = pitch_r - 1.25 * module

    tooth_half_angle_at_pitch = np.pi / (2 * teeth)
    t_at_pitch = _involute_angle_at_radius(base_r, pitch_r)
    ix, iy = _involute_xy(base_r, t_at_pitch)
    angle_at_pitch_involute = np.arctan2(iy, ix)
    center_offset = tooth_half_angle_at_pitch + angle_at_pitch_involute

    t_max = _involute_angle_at_radius(base_r, outside_r)
    ts = np.linspace(0, t_max, points_per_flank)

    right_flank = []
    for t in ts:
        x, y = _involute_xy(base_r, t)
        ang = np.arctan2(y, x) - center_offset
        r = np.hypot(x, y)
        right_flank.append((ang, r))

    left_flank = [(-ang, r) for ang, r in reversed(right_flank)]

    tooth_angular_pitch = 2 * np.pi / teeth
    all_points_polar = []

    for i in range(teeth):
        rot = i * tooth_angular_pitch
        for ang, r in right_flank:
            all_points_polar.append((ang + rot, r))
        for ang, r in left_flank:
            all_points_polar.append((ang + rot, r))
        next_rot = (i + 1) * tooth_angular_pitch
        start_ang = left_flank[-1][0] + rot
        end_ang = right_flank[0][0] + next_rot
        if points_root_arc > 0:
            arc_angles = np.linspace(start_ang, end_ang, points_root_arc + 2)[1:-1]
            for a in arc_angles:
                all_points_polar.append((a, root_r))

    points_xy = [(r * np.cos(a), r * np.sin(a)) for a, r in all_points_polar]
    geometry = {
        "pitch_diameter": 2 * pitch_r,
        "base_diameter": 2 * base_r,
        "outside_diameter": 2 * outside_r,
        "root_diameter": 2 * root_r,
    }
    return points_xy, geometry


def build_spur_gear(
    module: float,
    teeth: int,
    thickness: float,
    bore_diameter: float = None,
    pressure_angle_deg: float = 20.0,
) -> cq.Workplane:
    """
    Costruisce un ingranaggio cilindrico a denti dritti (spur gear) con
    profilo a evolvente reale, non semplificato a un disco con tacche.

    module: modulo dell'ingranaggio in mm (rapporto diametro primitivo /
        numero di denti -- parametro standard per la dimensione dei denti;
        due ingranaggi che devono ingranare insieme DEVONO avere lo stesso
        modulo).
    teeth: numero di denti (intero, tipicamente >= 8 per evitare
        sottotaglio del profilo con angolo di pressione standard 20°).
    thickness: spessore assiale dell'ingranaggio in mm.
    bore_diameter: diametro del foro centrale in mm (opzionale, None per
        nessun foro).
    pressure_angle_deg: angolo di pressione in gradi (20° è lo standard
        più comune, non cambiarlo senza un motivo specifico).

    Ritorna un cq.Workplane pronto per essere assegnato a 'result'.
    """
    if teeth < 4:
        raise ValueError(f"Numero di denti troppo basso ({teeth}): servono almeno 4 denti per un profilo valido.")

    points, _geometry = _gear_profile_points(module, teeth, pressure_angle_deg)

    result = (
        cq.Workplane("XY")
        .polyline(points)
        .close()
        .extrude(thickness)
    )

    if bore_diameter:
        result = (
            result.faces(">Z")
            .workplane()
            .hole(bore_diameter)
        )

    return result
