from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
from llm_pipeline import generate_cad_code_with_repair
from cad_engine import execute_and_export
from example_library import add_verified_example
from geometry_checks import detect_static_warnings
from dfm_checks import analyze_manufacturability, MATERIALS, PRINT_PROCESSES
from vision_pipeline import describe_shape_from_image, analyze_technical_drawing
from assembler import assemble_and_export

app = FastAPI(title="OpenCAD-AI Core Backend", version="0.1.0")

os.makedirs("outputs", exist_ok=True)
app.mount("/outputs", StaticFiles(directory="outputs"), name="outputs")
app.mount("/static", StaticFiles(directory="static"), name="static")


class CADRequest(BaseModel):
    prompt: str
    output_name: str = "generated_model"
    # Alzato da 15 a 45s: il default originale non teneva conto del lavoro
    # aggiunto in questa sessione DOPO la generazione del solido stesso --
    # export GLB (ricarica STL con trimesh), export DXF, disegno tecnico a
    # 3 viste (tre proiezioni HLR separate), analisi DFM con ray casting.
    # Su parti standard con geometria pesante (es. filetto reale via
    # cq_warehouse, ~2.9s solo per la costruzione) la somma di TUTTI i
    # passaggi post-generazione aggiunti oggi (STL, GLB, DXF, disegno
    # tecnico a 3 viste con HLR -- storicamente lento su geometrie
    # complesse, stima DFM via ray-casting su più facce per via del
    # filetto) si accumula in sequenza. 45s si è rivelato insufficiente su
    # un caso reale (vite con filetto reale + intera pipeline di export) --
    # alzato con più margine, non solo un altro piccolo ritocco.
    timeout: int = 120
    max_attempts: int = 3
    material: str = "PLA"
    process: str = "FDM"
    overhang_threshold_deg: float = None  # None = usa il default del processo scelto
    min_wall_warn_mm: float = None        # None = usa il default del processo scelto
    provider: str = "gemini"  # "gemini" (verificato) o "groq" (veloce, non verificato -- vedi llm_pipeline.py)


class AssemblyPart(BaseModel):
    step_path: str
    name: str = None
    position: list[float] = [0.0, 0.0, 0.0]


class AssemblyRequest(BaseModel):
    parts: list[AssemblyPart]
    output_name: str = "assieme"


@app.get("/")
async def serve_frontend():
    return FileResponse("static/index.html")


@app.get("/api/v1/materials")
async def list_materials():
    return {"materials": list(MATERIALS.keys())}


@app.get("/api/v1/processes")
async def list_processes():
    return {
        "processes": [
            {"key": key, "label": profile["label"]}
            for key, profile in PRINT_PROCESSES.items()
        ]
    }


@app.post("/api/v1/describe-image")
async def describe_image(file: UploadFile = File(...)):
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail={"message": "File immagine vuoto."})

    mime_type = file.content_type or "image/jpeg"

    try:
        result = describe_shape_from_image(image_bytes, mime_type)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail={"message": f"Errore durante l'analisi dell'immagine: {e}"}
        )

    return result


@app.post("/api/v1/analyze-drawing")
async def analyze_drawing(file: UploadFile = File(...)):
    """
    Analizza un disegno tecnico (non una foto di un oggetto) ed estrae le
    parti rilevate con le loro quote -- endpoint separato da /describe-image
    perché il compito è diverso: qui i numeri assoluti sono legittimi
    (scritti sul disegno), lì sono vietati (una foto non ha scala affidabile).
    """
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail={"message": "File immagine vuoto."})

    mime_type = file.content_type or "image/jpeg"

    result = analyze_technical_drawing(image_bytes, mime_type)
    if result["parse_error"]:
        raise HTTPException(
            status_code=502,
            detail={"message": f"Errore durante l'analisi del disegno: {result['parse_error']}"}
        )

    return {"raw_text": result["raw_text"]}


@app.get("/api/v1/list-parts")
async def list_parts():
    """
    Elenca i file .step già generati e salvati in outputs/, disponibili
    per essere selezionati e combinati in un assemblaggio. Solo il nome
    file e il percorso -- le quote di ciascuna parte sono già state
    mostrate all'utente al momento della sua generazione (bbox/volume),
    non le ricalcoliamo qui per restare rapidi con molte parti salvate.
    """
    output_dir = "outputs"
    if not os.path.isdir(output_dir):
        return {"parts": []}

    step_files = sorted(f for f in os.listdir(output_dir) if f.endswith(".step"))
    return {
        "parts": [
            {"name": f[:-5], "step_path": os.path.join(output_dir, f)}
            for f in step_files
        ]
    }


@app.post("/api/v1/assemble")
async def assemble(request: AssemblyRequest):
    """
    Combina più parti già generate (ciascuna un proprio file .step) in un
    assieme unico, posizionate secondo l'offset X/Y/Z fornito per
    ciascuna -- posizionamento MANUALE, non un risolutore automatico di
    vincoli meccanici (vedi assembler.py per il perché).
    """
    if len(request.parts) < 2:
        raise HTTPException(
            status_code=422,
            detail={"message": "Servono almeno 2 parti per un assemblaggio."}
        )

    for p in request.parts:
        if not os.path.isfile(p.step_path):
            raise HTTPException(
                status_code=404,
                detail={"message": f"File non trovato: {p.step_path}"}
            )

    output_dir = "outputs"
    os.makedirs(output_dir, exist_ok=True)
    step_out = os.path.join(output_dir, f"{request.output_name}.step")
    stl_out = os.path.join(output_dir, f"{request.output_name}.stl")
    glb_out = os.path.join(output_dir, f"{request.output_name}.glb")

    try:
        result = assemble_and_export(
            [p.model_dump() for p in request.parts],
            step_out_path=step_out,
            stl_out_path=stl_out,
            glb_out_path=glb_out,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"message": str(e)})
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"message": f"Errore durante l'assemblaggio: {e}"}
        )

    return {
        "volume_mm3": result["volume_mm3"],
        "bbox_mm": result["bbox_mm"],
        "files": {
            "step_path": step_out,
            "stl_path": stl_out,
            "glb_path": glb_out if result.get("has_glb") else None,
        },
    }


@app.post("/api/v1/generate")
async def generate_cad(request: CADRequest):
    output_dir = "outputs"
    os.makedirs(output_dir, exist_ok=True)
    file_prefix = os.path.join(output_dir, request.output_name)

    def execute_fn(code: str) -> dict:
        return execute_and_export(
            code, base_filename=file_prefix, timeout=request.timeout,
            material=request.material, part_name=request.output_name,
        )

    repair_result = generate_cad_code_with_repair(
        request.prompt, execute_fn, max_attempts=request.max_attempts, provider=request.provider
    )

    python_code = repair_result["code"]
    exec_result = repair_result["exec_result"]

    if not exec_result["success"]:
        raise HTTPException(
            status_code=422,
            detail={
                "status": "compilation_error",
                "message": exec_result["error"],
                "generated_code": python_code,
                "attempts": repair_result["attempts"],
            }
        )

    warnings = detect_static_warnings(python_code)

    # L'analisi DFM gira solo su un export riuscito, e non deve mai far
    # fallire la richiesta: un problema qui è un'informazione in meno,
    # non un motivo per buttare via un pezzo generato correttamente.
    dfm = analyze_manufacturability(
        stl_path=exec_result["files"]["stl_path"],
        volume_mm3=exec_result.get("volume_mm3") or 0.0,
        material=request.material,
        process=request.process,
        overhang_threshold_deg=request.overhang_threshold_deg,
        min_wall_warn_mm=request.min_wall_warn_mm,
    )
    warnings = warnings + dfm.get("warnings", [])

    status = "geometria_sospetta" if warnings else "success"

    # Libreria di esempi verificati per il recupero dinamico (vedi
    # example_library.py) -- solo generazioni SENZA avvisi geometrici, e
    # solo quelle scritte davvero dal modello (il percorso deterministico
    # non ha nulla da "insegnare", è codice nostro non generato). Un
    # fallimento qui non deve mai bloccare la risposta già pronta.
    if status == "success" and not repair_result.get("deterministic"):
        add_verified_example(
            request.prompt, python_code,
            exec_result.get("volume_mm3"), exec_result.get("bbox_mm"),
        )

    return {
        "status": status,
        "warnings": warnings,
        "generated_code": python_code,
        "volume_mm3": exec_result.get("volume_mm3"),
        "bbox_mm": exec_result.get("bbox_mm"),
        "files": exec_result["files"],
        "attempts": repair_result["attempts"],
        "repaired": repair_result["repaired"],
        # Presente e True solo se il percorso deterministico per parti
        # standard (vedi try_deterministic_fastener_code in llm_pipeline.py)
        # ha generato questo risultato -- nessuna chiamata al modello.
        "deterministic": repair_result.get("deterministic", False),
        "dfm": {
            "process": dfm.get("process"),
            "weight_cost": dfm.get("weight_cost"),
            "overhangs": dfm.get("overhangs"),
            "wall_thickness": dfm.get("wall_thickness"),
        },
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}
