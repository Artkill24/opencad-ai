"""
Solidi platonici pronti all'uso, costruiti via trimesh invece che via
OCCT/CadQuery a basso livello.

Perché questo cambio: il primo approccio (costruire una TopoDS_Shell da
facce triangolari indipendenti via BRepBuilderAPI_Sewing + ShapeFix_Solid)
si è rivelato inaffidabile in pratica -- verificato empiricamente: dopo
4 iterazioni di correzioni alle chiamate OCCT di basso livello, il sewing
produceva solidi strutturalmente incompleti (2-3 facce invece delle 4-36
attese, volumi sbagliati). trimesh è una libreria dedicata a mesh
triangolari, con riparazione automatica delle normali (fix_normals) e
verifica di integrità (is_watertight) integrate e testate -- qui produce
risultati corretti su tutti e 5 i solidi, verificato con volumi esatti
al centesimo di mm^3 contro le formule analitiche note.

Il compromesso onesto: questi solidi vengono esportati SOLO in formato
STL, non STEP -- lo STEP richiede un vero solido BREP (il tipo di
oggetto che la costruzione via OCCT, sopra, non riusciva a produrre in
modo affidabile), mentre trimesh lavora esclusivamente con mesh
triangolari. Per solidi platonici (spesso usati per prototipazione/stampa
3D più che per lavorazione CNC di precisione) l'STL è comunque il
formato più rilevante nella pratica.
"""

import numpy as np
import trimesh
from scipy.spatial import ConvexHull

PHI = (1 + 5 ** 0.5) / 2  # rapporto aureo, usato da icosaedro e dodecaedro


def _tetrahedron_vertices():
    return [(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)]


def _cube_vertices():
    return [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]


def _octahedron_vertices():
    return [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]


def _icosahedron_vertices():
    verts = []
    for s1 in (-1, 1):
        for s2 in (-1, 1):
            verts.append((0, s1 * 1, s2 * PHI))
            verts.append((s1 * 1, s2 * PHI, 0))
            verts.append((s2 * PHI, 0, s1 * 1))
    return verts


def _dodecahedron_vertices():
    verts = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    inv_phi = 1 / PHI
    for s1 in (-1, 1):
        for s2 in (-1, 1):
            verts.append((0, s1 * inv_phi, s2 * PHI))
            verts.append((s1 * inv_phi, s2 * PHI, 0))
            verts.append((s2 * PHI, 0, s1 * inv_phi))
    return verts


PLATONIC_SOLIDS = {
    "tetraedro": _tetrahedron_vertices,
    "cubo": _cube_vertices,
    "ottaedro": _octahedron_vertices,
    "icosaedro": _icosahedron_vertices,
    "dodecaedro": _dodecahedron_vertices,
}


def _normalize_to_diameter(vertices, target_diameter):
    """
    Riscala i vertici in modo che il diametro circoscritto (distanza tra i
    due vertici più lontani -- per questi solidi centralmente simmetrici,
    2x la distanza di un vertice qualsiasi dall'origine) sia esattamente
    target_diameter. Corrisponde a "misura la dimensione più larga del
    pezzo con un calibro", il modo più naturale di prendere una misura
    reale su un oggetto fisico.
    """
    pts = np.array(vertices, dtype=float)
    current_diameter = 2 * np.linalg.norm(pts[0])
    factor = target_diameter / current_diameter
    return pts * factor


def build_platonic_solid(name: str, diameter_mm: float) -> trimesh.Trimesh:
    """
    Costruisce un solido platonico come mesh trimesh, pronta per essere
    assegnata a 'result' -- cad_engine.py riconosce un trimesh.Trimesh e
    lo esporta in STL (non STEP, vedi nota in testa al modulo).

    name: uno tra "tetraedro", "cubo", "ottaedro", "icosaedro", "dodecaedro".
    diameter_mm: distanza tra i due punti più lontani del solido (il tipo
    di misura che si prende naturalmente con un calibro sull'oggetto reale).
    """
    key = name.strip().lower()
    if key not in PLATONIC_SOLIDS:
        raise ValueError(
            f"Solido platonico non supportato: '{name}'. "
            f"Disponibili: {', '.join(PLATONIC_SOLIDS.keys())}"
        )

    raw_vertices = PLATONIC_SOLIDS[key]()
    scaled_vertices = _normalize_to_diameter(raw_vertices, diameter_mm)

    hull = ConvexHull(scaled_vertices)
    mesh = trimesh.Trimesh(vertices=scaled_vertices, faces=hull.simplices, process=True)
    mesh.fix_normals()

    return mesh
