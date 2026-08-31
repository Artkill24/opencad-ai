import sys
import io
import os
import math
import traceback
import multiprocessing
import cadquery as cq
import trimesh

from polyhedra import build_platonic_solid
from drawing_generator import generate_technical_drawing
from gears import build_spur_gear
from fasteners import build_hex_nut, build_hex_head_screw, build_bearing, build_washer
from dxf_export import export_dxf_top_profile


def _run_in_subprocess(code_str: str, step_path: str, stl_path: str, drawing_path: str, glb_path: str,
                        dxf_path: str, part_name: str, material: str, queue: multiprocessing.Queue):
    """
    Gira in un processo separato: exec dello script, validazione result,
    export STEP/STL, calcolo volume/bounding box per verifica geometrica.
    Comunica solo dati picklabili (dict) al processo padre.
    """
    local_scope = {}
    # 'math' è sempre disponibile nell'ambiente di esecuzione, anche se il
    # codice generato non scrive esplicitamente "import math" -- capita
    # spesso su geometrie che richiedono trigonometria (poliedri, eliche)
    # dove il modello usa math.cos/math.sin ma dimentica l'import. Evita
    # un'intera classe di NameError altrimenti ricorrente in quei casi.
    #
    # 'build_platonic_solid' è disponibile per lo stesso motivo, ma per un
    # problema più profondo: il modello falliva ripetutamente (3-4 tentativi,
    # errori diversi ogni volta) provando a calcolare da solo i vertici di
    # un icosaedro con trigonometria a mano. Dargli una funzione già pronta
    # e verificata (vedi polyhedra.py) sposta il compito da "inventa la
    # geometria" a "scegli quale solido e quale dimensione".
    #
    # 'build_spur_gear' stesso principio, per ingranaggi a evolvente reali
    # -- profilo verificato numericamente contro le formule ISO standard
    # e contro il volume 3D effettivo (vedi gears.py), non lasciato al
    # modello che non riuscirebbe a derivare da solo la curva a evolvente.
    #
    # 'build_hex_nut'/'build_hex_head_screw'/'build_bearing' per parti
    # standard ISO reali (dadi/viti/cuscinetti) via cq_warehouse --
    # dimensioni da tabelle ufficiali, verificate contro geometria e
    # limiti fisici calcolati indipendentemente prima di collegarle qui
    # (vedi fasteners.py).
    global_scope = {
        "cq": cq, "math": math,
        "build_platonic_solid": build_platonic_solid,
        "build_spur_gear": build_spur_gear,
        "build_hex_nut": build_hex_nut,
        "build_hex_head_screw": build_hex_head_screw,
        "build_bearing": build_bearing,
        "build_washer": build_washer,
    }

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        exec(code_str, global_scope, local_scope)
        result = local_scope.get("result")

        if result is None:
            queue.put({"success": False, "error": "Lo script non ha definito la variabile 'result'."})
            return

        # Due percorsi possibili per 'result': un solido CadQuery normale
        # (Workplane/Assembly, la stragrande maggioranza dei casi -- export
        # STEP+STL via OCCT), oppure una mesh trimesh prodotta da
        # build_platonic_solid() -- export SOLO STL, perché una mesh
        # triangolare non è un solido BREP e non può produrre uno STEP
        # valido (vedi nota in polyhedra.py sul perché di questa scelta).
        if isinstance(result, trimesh.Trimesh):
            result.export(stl_path)

            # GLB per il viewer 3D -- verificato nel sandbox di sviluppo:
            # 55-65% più leggero di STL su mesh di media/alta densità,
            # nessuna perdita di precisione (volume identico al giro di
            # andata/ritorno). Fallimento qui non blocca la generazione
            # (il pezzo 3D è comunque riuscito, l'STL resta disponibile
            # per il download anche se il GLB fallisse).
            has_glb = False
            try:
                result.export(glb_path)
                has_glb = True
            except Exception:
                pass

            queue.put({
                "success": True,
                "error": None,
                "volume_mm3": round(float(result.volume), 1),
                "bbox_mm": {
                    "x": round(float(result.extents[0]), 2),
                    "y": round(float(result.extents[1]), 2),
                    "z": round(float(result.extents[2]), 2),
                },
                "mesh_only": True,  # cad_engine comunica al chiamante che non c'è uno STEP
                "has_drawing": False,  # niente disegno tecnico per i solidi mesh-only (vedi drawing_generator.py)
                "has_glb": has_glb,
                "has_dxf": False,  # niente profilo DXF per i solidi mesh-only (nessuna faccia BREP)
            })
            return

        if not isinstance(result, (cq.Workplane, cq.Assembly)):
            queue.put({
                "success": False,
                "error": (
                    f"'result' deve essere cq.Workplane, cq.Assembly, o il risultato di "
                    f"build_platonic_solid(), ottenuto {type(result)}."
                )
            })
            return

        cq.exporters.export(result, step_path, exportType=cq.exporters.ExportTypes.STEP)
        cq.exporters.export(
            result, stl_path,
            exportType=cq.exporters.ExportTypes.STL,
            tolerance=0.01, angularTolerance=0.1
        )

        # GLB per il viewer 3D -- CadQuery non lo esporta nativamente,
        # quindi ricarica l'STL appena scritto con trimesh e lo riesporta
        # in GLB. Stesso principio del disegno tecnico: un fallimento qui
        # non deve mai bloccare la generazione già riuscita.
        has_glb = False
        try:
            mesh_per_glb = trimesh.load_mesh(stl_path)
            mesh_per_glb.export(glb_path)
            has_glb = True
        except Exception:
            pass

        # DXF (profilo 2D piatto della faccia superiore, per taglio
        # laser/CNC) -- vedi dxf_export.py per il perché NON esportiamo
        # semplicemente l'intero solido (darebbe un wireframe 3D
        # inutilizzabile, verificato). Come sempre, un fallimento qui non
        # blocca la generazione già riuscita.
        dxf_result = export_dxf_top_profile(result, dxf_path)

        # Metriche geometriche per verifica post-hoc (solo per Workplane con un solido;
        # per geometrie composite/Assembly il calcolo è meno affidabile, lo saltiamo).
        volume_mm3 = None
        bbox_mm = None
        try:
            solid = result.val()
            if hasattr(solid, "Volume") and hasattr(solid, "BoundingBox"):
                volume_mm3 = round(solid.Volume(), 1)
                bbox = solid.BoundingBox()
                bbox_mm = {
                    "x": round(bbox.xlen, 2),
                    "y": round(bbox.ylen, 2),
                    "z": round(bbox.zlen, 2),
                }
        except Exception:
            pass  # metriche opzionali: se falliscono non blocchiamo l'export già riuscito

        # Disegno tecnico 2D (3 viste ortogonali + quote d'ingombro) --
        # fallimento qui non deve mai far fallire l'intera generazione,
        # il pezzo 3D è comunque riuscito. Serve bbox_mm già calcolato
        # (per il cartiglio con le quote), quindi va dopo il blocco sopra.
        drawing_result = generate_technical_drawing(
            result, drawing_path, bbox_mm, part_name=part_name, material=material
        )

        queue.put({
            "success": True,
            "error": None,
            "volume_mm3": volume_mm3,
            "bbox_mm": bbox_mm,
            "mesh_only": False,
            "has_drawing": drawing_result["success"],
            "drawing_error": drawing_result["error"],
            "has_glb": has_glb,
            "has_dxf": dxf_result["success"],
            "dxf_error": dxf_result["error"],
        })

    except Exception as e:
        queue.put({"success": False, "error": f"{str(e)}\n{traceback.format_exc()}"})
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def execute_and_export(code_str: str, base_filename: str = "output", timeout: int = 15,
                        material: str = "PLA", part_name: str = None) -> dict:
    """
    Esegue lo script CadQuery in un processo isolato con timeout hard.
    Ritorna dict con success, error, volume/bbox (se disponibili), e (se ok) files.
    files["step_path"] è None per i risultati mesh-only (solidi platonici via
    trimesh) -- non esiste alcuno STEP per quei casi, vedi mesh_only in result.
    files["drawing_path"] è None se il disegno tecnico non è stato generato
    (mesh-only, o generazione fallita -- vedi has_drawing/drawing_error).
    files["glb_path"] è None se l'export GLB è fallito (raro, non dovrebbe
    mai bloccare la generazione -- vedi has_glb). Il viewer usa GLB quando
    disponibile (più leggero di STL, ~55-65% in meno verificato), STL resta
    comunque sempre disponibile per il download.
    files["dxf_path"] è None se l'export DXF non è riuscito (mesh-only, o
    generazione fallita -- vedi has_dxf/dxf_error). Profilo 2D piatto
    della faccia superiore, per taglio laser/CNC -- non l'intero solido
    3D (vedi dxf_export.py sul perché).

    material/part_name: passati al solo scopo di popolare il cartiglio del
    disegno tecnico 2D (vedi drawing_generator.py) -- non influenzano la
    geometria generata. part_name di default usa il nome base del file se
    non specificato esplicitamente.
    """
    step_path = f"{base_filename}.step"
    stl_path = f"{base_filename}.stl"
    drawing_path = f"{base_filename}_drawing.svg"
    glb_path = f"{base_filename}.glb"
    dxf_path = f"{base_filename}.dxf"

    if part_name is None:
        part_name = os.path.basename(base_filename)

    queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=_run_in_subprocess,
        args=(code_str, step_path, stl_path, drawing_path, glb_path, dxf_path, part_name, material, queue)
    )
    proc.start()
    proc.join(timeout)

    if proc.is_alive():
        proc.terminate()
        proc.join()
        return {
            "success": False,
            "error": f"Timeout: esecuzione oltre {timeout}s (probabile loop infinito o geometria troppo complessa)."
        }

    if queue.empty():
        return {
            "success": False,
            "error": f"Processo terminato senza risultato (crash, exit code {proc.exitcode})."
        }

    result = queue.get()
    if result["success"]:
        result["files"] = {
            "step_path": None if result.get("mesh_only") else step_path,
            "stl_path": stl_path,
            "drawing_path": drawing_path if result.get("has_drawing") else None,
            "glb_path": glb_path if result.get("has_glb") else None,
            "dxf_path": dxf_path if result.get("has_dxf") else None,
        }
    return result
