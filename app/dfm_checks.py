"""
Analisi DFM (Design for Manufacturing) sul file STL già esportato.

Quattro controlli, con onestà esplicita sui limiti di ciascuno:

1. Peso e costo materiale -- calcolo diretto da volume x densità, affidabile
   quanto lo è il volume stesso (già verificato altrove nella pipeline).
   I prezzi/kg sono INDICATIVI (variano per fornitore, quantità, mercato) --
   vanno trattati come riferimento, non come preventivo.

2. Overhang (sbalzi senza supporto) -- calcolo geometrico esatto sulla mesh:
   per ogni triangolo, l'angolo tra la sua normale e la verticale verso il
   basso. La SOGLIA che determina cosa è "a rischio" dipende dal processo
   di stampa (vedi PRINT_PROCESSES) -- FDM, resina e SLS hanno limiti fisici
   molto diversi tra loro, un unico numero fisso per tutti sarebbe fuorviante.

3. Spessore minimo pareti -- STIMA CAMPIONATA via ray casting da un
   sottoinsieme di facce, non un'analisi esaustiva. Può non individuare
   punti sottili isolati che non sono allineati con nessuna faccia
   campionata. La soglia di allarme dipende anch'essa dal processo (una
   resina SLA stampa pareti molto più sottili di un FDM).

4. Cavità chiuse (drenaggio) -- NON IMPLEMENTATO. Rilevante per resina e
   SLS (la resina/polvere in eccesso resta intrappolata in cavità senza
   foro di fuoriuscita) -- richiederebbe analisi topologica dei corpi
   annidati, più complessa dei controlli sopra. Lo segnaliamo esplicitamente
   come limite noto invece di ometterlo in silenzio.
"""

import numpy as np
import trimesh

# Densità (g/cm^3) e prezzo indicativo (EUR/kg, solo materiale grezzo,
# NESSUNA lavorazione/stampa/finitura inclusa) -- valori di riferimento
# ampiamente citati in letteratura tecnica, da verificare sempre col
# fornitore reale prima di usarli per un preventivo.
MATERIALS = {
    "PLA":      {"density_g_cm3": 1.24, "price_eur_kg": 20.0},
    "ABS":      {"density_g_cm3": 1.04, "price_eur_kg": 22.0},
    "PETG":     {"density_g_cm3": 1.27, "price_eur_kg": 25.0},
    "NYLON_PA12": {"density_g_cm3": 1.01, "price_eur_kg": 30.0},
    "ALLUMINIO_6061": {"density_g_cm3": 2.70, "price_eur_kg": 6.0},
    "ACCIAIO":  {"density_g_cm3": 7.85, "price_eur_kg": 2.0},
}

# Soglie DFM per processo di stampa/lavorazione. Valori indicativi (regole
# pratiche ampiamente citate nella comunità maker/prototipazione), NON
# specifiche di una macchina o un fornitore particolare -- una vera scheda
# tecnica del processo/macchina scelta prevale sempre su questi default.
PRINT_PROCESSES = {
    "FDM": {
        "label": "FDM (filamento fuso)",
        "overhang_threshold_deg": 45.0,
        "min_wall_warn_mm": 1.0,
        "note": (
            "Filamento fuso (PLA/ABS/PETG su stampanti tipo Prusa/Bambu/Ender). "
            "Regola pratica: sbalzi oltre 45 gradi dalla verticale richiedono "
            "supporti; pareti sotto 1mm rischiano di non stampare bene o rompersi."
        ),
    },
    "RESINA": {
        "label": "Resina (SLA/DLP)",
        "overhang_threshold_deg": 30.0,
        "min_wall_warn_mm": 0.5,
        "note": (
            "Fotopolimero SLA/DLP. Risoluzione più fine consente pareti più "
            "sottili rispetto a FDM, ma le forze di distacco dalla vasca resina "
            "rendono gli sbalzi più critici -- soglia di supporto più severa. "
            "Cavità chiuse intrappolano resina non polimerizzata: aggiungi fori "
            "di drenaggio manualmente, questo controllo non li rileva (vedi nota "
            "in testa al modulo)."
        ),
    },
    "SLS": {
        "label": "SLS (sinterizzazione polvere)",
        "overhang_threshold_deg": 0.0,  # il letto di polvere autosostiene: nessuno sbalzo va mai segnalato
        "min_wall_warn_mm": 0.7,
        "note": (
            "Sinterizzazione laser su letto di polvere (tipicamente nylon PA12). "
            "Il letto di polvere sostiene il pezzo durante la stampa: gli sbalzi "
            "NON richiedono supporti (per questo il controllo overhang è "
            "disattivato per questo processo). Attenzione a pareti sottili e a "
            "cavità chiuse (la polvere in eccesso resta intrappolata senza un "
            "foro di fuoriuscita) -- quest'ultimo controllo non è implementato."
        ),
    },
}

DEFAULT_PROCESS = "FDM"
DEFAULT_THICKNESS_SAMPLE_COUNT = 250


def _compute_weight_and_cost(volume_mm3: float, material: str) -> dict:
    material_key = material.upper()
    if material_key not in MATERIALS:
        return {
            "material": material,
            "error": f"Materiale '{material}' non in elenco. Disponibili: {', '.join(MATERIALS.keys())}",
        }

    props = MATERIALS[material_key]
    volume_cm3 = volume_mm3 / 1000.0
    weight_g = volume_cm3 * props["density_g_cm3"]
    cost_eur = (weight_g / 1000.0) * props["price_eur_kg"]

    return {
        "material": material_key,
        "weight_g": round(weight_g, 2),
        "estimated_cost_eur": round(cost_eur, 2),
        "cost_disclaimer": "Prezzo indicativo del solo materiale grezzo -- verifica sempre col fornitore.",
    }


def _compute_overhangs(mesh: trimesh.Trimesh, threshold_deg: float, process_label: str) -> dict:
    normals = mesh.face_normals
    areas = mesh.area_faces
    total_area = areas.sum()

    if total_area <= 0:
        return {"overhang_area_pct": None, "note": "Area totale della mesh nulla, impossibile analizzare."}

    normal_z = normals[:, 2]
    downward_mask = normal_z < 0

    # Angolo tra la normale e la verticale verso il basso (0,0,-1):
    # 0 gradi = faccia orizzontale rivolta in basso (sbalzo peggiore possibile),
    # 90 gradi = faccia verticale (mai a rischio, indipendentemente dalla soglia).
    alpha_deg = np.degrees(np.arccos(np.clip(-normal_z, -1.0, 1.0)))

    # La faccia alla base del pezzo (appoggiata sul piano di stampa) NON è
    # un overhang, anche se la sua normale punta verso il basso -- è
    # banalmente supportata dal piano stesso. Serve però attenzione a COME
    # si definisce "alla base": controllare se ALMENO UN vertice tocca il
    # piano è troppo permissivo -- un cono che tocca il piano solo con la
    # punta ha TUTTE le facce laterali che condividono quel vertice, quindi
    # verrebbero escluse per intero (verificato: bug reale, non solo un
    # caso di test, trovato con un cono di sbalzo noto che risultava sempre
    # 0% invece del ~50% atteso). La regola corretta richiede che l'INTERA
    # faccia (tutti e tre i vertici, non solo uno) sia vicina al piano.
    z_min = mesh.bounds[0][2]
    base_epsilon = max(mesh.scale * 1e-4, 1e-3)
    face_max_z = mesh.triangles[:, :, 2].max(axis=1)
    at_base_mask = face_max_z <= (z_min + base_epsilon)

    risky_mask = downward_mask & (alpha_deg < threshold_deg) & (~at_base_mask)
    risky_area = areas[risky_mask].sum()
    overhang_pct = 100.0 * risky_area / total_area

    if threshold_deg <= 0:
        note = f"Controllo overhang disattivato per {process_label} -- il processo autosostiene il pezzo durante la produzione."
    else:
        note = (
            "Percentuale di superficie (esclusa la base d'appoggio) con normale rivolta "
            f"verso il basso a meno di {threshold_deg:.0f} gradi dalla verticale -- "
            f"soglia per {process_label}."
        )

    return {
        "overhang_area_pct": round(overhang_pct, 2),
        "overhang_threshold_deg": threshold_deg,
        "note": note,
    }


def _find_open_boundary_vertices(mesh: trimesh.Trimesh) -> np.ndarray:
    """
    Trova i vertici che giacciono su un bordo APERTO della mesh (es. il
    bordo di un'apertura su un contenitore cavo) -- in una mesh chiusa
    ogni spigolo è condiviso da esattamente 2 triangoli; su un bordo
    aperto appartiene a un solo triangolo. Ritorna un array Nx3 di
    coordinate (vuoto se la mesh è completamente chiusa, es. un pezzo
    pieno -- in quel caso non c'è nulla da escludere, comportamento
    invariato rispetto a prima).
    """
    edges_sorted = np.sort(mesh.edges, axis=1)
    _, counts = np.unique(edges_sorted, axis=0, return_counts=True)
    unique_edges, first_idx, counts = np.unique(edges_sorted, axis=0, return_index=True, return_counts=True)
    bordo_mask = counts == 1
    vertici_bordo_idx = np.unique(unique_edges[bordo_mask])
    return mesh.vertices[vertici_bordo_idx]


def _estimate_min_wall_thickness(mesh: trimesh.Trimesh, sample_count: int) -> dict:
    n_faces = len(mesh.faces)
    if n_faces == 0:
        return {"estimated_min_wall_mm": None, "note": "Mesh senza facce."}

    if n_faces > sample_count:
        idx = np.random.default_rng(42).choice(n_faces, sample_count, replace=False)
    else:
        idx = np.arange(n_faces)

    centers = mesh.triangles_center[idx]
    normals = mesh.face_normals[idx]

    # Scostamento verso l'interno per evitare che il raggio colpisca
    # immediatamente la propria faccia di partenza (auto-intersezione a
    # distanza ~0). Scala con la dimensione del pezzo, non un valore fisso.
    epsilon = max(mesh.scale * 1e-4, 1e-4)
    origins = centers - normals * epsilon
    directions = -normals

    try:
        locations, index_ray, _ = mesh.ray.intersects_location(origins, directions)
    except Exception as e:
        return {"estimated_min_wall_mm": None, "note": f"Ray casting non riuscito: {e}"}

    if len(locations) == 0:
        return {
            "estimated_min_wall_mm": None,
            "note": "Nessuna intersezione trovata nel campione -- mesh probabilmente troppo semplice/aperta per questa stima.",
        }

    distances = np.linalg.norm(locations - origins[index_ray], axis=1)

    # Un raggio può colpire più facce lungo il suo percorso (mesh non
    # perfettamente convesse): teniamo solo la distanza minima per ciascun
    # raggio, che rappresenta lo spessore locale nel punto campionato.
    min_per_ray = {}
    origine_per_ray = {}
    for dist, ray_idx, origine in zip(distances, index_ray, origins[index_ray]):
        if ray_idx not in min_per_ray or dist < min_per_ray[ray_idx]:
            min_per_ray[ray_idx] = dist
            origine_per_ray[ray_idx] = origine

    if not min_per_ray:
        return {"estimated_min_wall_mm": None, "note": "Nessuna distanza valida calcolata."}

    # Esclude i campioni troppo vicini a un bordo APERTO (es. l'apertura di
    # un contenitore cavo) -- lì lo spessore reale si assottiglia verso
    # zero per pura geometria (parete esterna e interna che si incontrano
    # al bordo tagliato), non per un difetto del pezzo. Senza questa
    # esclusione, un contenitore aperto ben progettato risulterebbe sempre
    # segnalato come "troppo sottile", indipendentemente da quanto sia
    # spessa la parete lontano dal bordo.
    #
    # ONESTÀ SUI LIMITI: la logica di esclusione è verificata (nessun
    # falso positivo su mesh chiuse, rileva correttamente i bordi aperti
    # dove esistono) ma NON sono riuscito a riprodurre l'esatta anomalia
    # osservata in produzione (un vaso da loft con spessore realmente
    # sceso a 0.323mm vicino al bordo) su un tubo di prova semplice --
    # il tubo dava già la lettura corretta anche SENZA questa esclusione,
    # segno che l'anomalia originale è legata alla geometria curva/
    # rastremata del loft, non genericamente a "vicinanza a un bordo
    # aperto". Questa correzione resta comunque corretta e utile (esclude
    # un artefatto geometrico reale e noto), ma potrebbe non coprire ogni
    # caso -- da riverificare su un caso reale se il problema si ripresenta.
    punti_bordo = _find_open_boundary_vertices(mesh)
    ray_idxs = list(min_per_ray.keys())
    all_distances = np.array([min_per_ray[r] for r in ray_idxs])
    all_origins = np.array([origine_per_ray[r] for r in ray_idxs])

    if len(punti_bordo) > 0:
        soglia_bordo = mesh.scale * 0.03
        dist_da_bordo = np.min(
            np.linalg.norm(all_origins[:, None, :] - punti_bordo[None, :, :], axis=2),
            axis=1
        )
        lontano_da_bordo = dist_da_bordo > soglia_bordo
        n_esclusi = int((~lontano_da_bordo).sum())
    else:
        lontano_da_bordo = np.ones(len(all_distances), dtype=bool)
        n_esclusi = 0

    distanze_valide = all_distances[lontano_da_bordo]
    if len(distanze_valide) == 0:
        # Tutti i campioni erano vicino a un bordo -- pezzo troppo piccolo
        # o troppo dominato da aperture per questa stima; fallback ai dati
        # completi piuttosto che non dare nessuna stima.
        distanze_valide = all_distances

    estimated_min = distanze_valide.min()

    note = (
        "STIMA CAMPIONATA (ray casting da un sottoinsieme di facce), non un'analisi "
        "esaustiva -- può non rilevare punti sottili isolati non allineati col campione."
    )
    if n_esclusi > 0:
        note += (
            f" {n_esclusi} campioni vicino a un bordo aperto (es. l'apertura di un "
            "contenitore) esclusi dal calcolo -- lì lo spessore si assottiglia per "
            "geometria, non per difetto."
        )

    return {
        "estimated_min_wall_mm": round(float(estimated_min), 3),
        "sample_count": len(idx),
        "note": note,
    }


def analyze_manufacturability(
    stl_path: str,
    volume_mm3: float,
    material: str = "PLA",
    process: str = DEFAULT_PROCESS,
    overhang_threshold_deg: float = None,
    min_wall_warn_mm: float = None,
    thickness_sample_count: int = DEFAULT_THICKNESS_SAMPLE_COUNT,
) -> dict:
    """
    Analisi DFM completa su un file STL esportato. Ritorna un dict con
    process, weight_cost, overhangs, wall_thickness, e una lista di warnings
    testuali se qualche soglia viene superata. Non solleva eccezioni verso
    il chiamante: eventuali errori vengono incorporati nel dict di risposta,
    perché un fallimento qui non deve mai far fallire l'intera richiesta
    di generazione (il pezzo è comunque stato generato con successo).

    process: uno tra "FDM", "RESINA", "SLS" -- determina le soglie di default
    per overhang e spessore minimo (vedi PRINT_PROCESSES). overhang_threshold_deg
    e min_wall_warn_mm, se passati esplicitamente, sovrascrivono il default
    del processo scelto.
    """
    process_key = process.strip().upper()
    if process_key not in PRINT_PROCESSES:
        process_key = DEFAULT_PROCESS  # fallback silenzioso a un default sensato
    profile = PRINT_PROCESSES[process_key]

    effective_overhang_threshold = (
        overhang_threshold_deg if overhang_threshold_deg is not None else profile["overhang_threshold_deg"]
    )
    effective_min_wall = (
        min_wall_warn_mm if min_wall_warn_mm is not None else profile["min_wall_warn_mm"]
    )

    result = {
        "process": {
            "key": process_key,
            "label": profile["label"],
            "note": profile["note"],
        },
        "weight_cost": _compute_weight_and_cost(volume_mm3, material),
        "overhangs": None,
        "wall_thickness": None,
        "warnings": [],
    }

    try:
        mesh = trimesh.load_mesh(stl_path)
    except Exception as e:
        result["warnings"].append(f"Impossibile caricare la mesh per l'analisi DFM: {e}")
        return result

    overhangs = _compute_overhangs(mesh, effective_overhang_threshold, profile["label"])
    result["overhangs"] = overhangs
    if overhangs.get("overhang_area_pct") is not None and overhangs["overhang_area_pct"] > 5.0:
        result["warnings"].append(
            f"{overhangs['overhang_area_pct']:.1f}% della superficie ha sbalzi a rischio "
            f"(sotto {effective_overhang_threshold:.0f} gradi dalla verticale, soglia "
            f"{profile['label']}) -- valuta supporti in stampa o un riorientamento del pezzo."
        )

    thickness = _estimate_min_wall_thickness(mesh, thickness_sample_count)
    result["wall_thickness"] = thickness
    est = thickness.get("estimated_min_wall_mm")
    if est is not None and est < effective_min_wall:
        result["warnings"].append(
            f"Spessore minimo stimato {est}mm, sotto la soglia di {effective_min_wall}mm "
            f"per {profile['label']} -- rischio di rottura in produzione (stima campionata, "
            "verifica visivamente)."
        )

    return result
