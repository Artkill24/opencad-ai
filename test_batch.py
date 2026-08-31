"""
Batch test per la pipeline prompt -> CadQuery -> STEP/STL.

Uso:
    python test_batch.py                  # gira contro http://localhost:8000
    python test_batch.py --url http://... # endpoint diverso
    python test_batch.py --timeout 30      # timeout HTTP per richiesta (secondi)

Richiede solo la libreria standard + requests (gia' presente come dipendenza
di transito di alcuni pacchetti, ma se manca: pip install requests).
"""

import argparse
import json
import time
from datetime import datetime

import requests

# Prompt a difficolta' crescente: geometria base -> feature multiple ->
# operazioni booleane/fillet piu' delicate -> forme meno standard.
TEST_PROMPTS = [
    # --- Livello 1: geometria singola, nessuna feature ---
    "Un cubo pieno di lato 40mm.",
    "Un cilindro pieno di diametro 60mm e altezza 25mm.",

    # --- Livello 2: geometria + un singolo foro/taglio ---
    "Un disco piatto di diametro 80mm, spessore 8mm, con un foro centrale passante da 20mm.",
    "Una piastra rettangolare 100x60mm, spessore 5mm, con quattro fori agli angoli da 6mm, a 10mm dai bordi.",

    # --- Livello 3: pattern circolare (quello che ci ha rotto in origine) ---
    "Una flangia circolare diametro 90mm, spessore 10mm, foro centrale 30mm, con 8 fori M5 disposti lungo una circonferenza di diametro 70mm.",
    "Un ingranaggio semplificato: disco di diametro 100mm e spessore 6mm con 6 fori di alleggerimento da 12mm disposti su una circonferenza di 70mm.",

    # --- Livello 4: fillet/chamfer, piu' operazioni concatenate ---
    "Un blocco 50x50x20mm con tutti gli spigoli verticali arrotondati con un raggio di 5mm.",
    "Una staffa a L: due piastre di 60x30x5mm unite a 90 gradi, con un foro da 8mm su ciascuna piastra.",

    # --- Livello 5: forme cave / pareti sottili (DFM-sensibili) ---
    "Un contenitore a forma di scatola aperta: 80x60x40mm esterni, pareti spesse 3mm, senza coperchio.",
    "Un tubo cavo: diametro esterno 40mm, diametro interno 32mm, lunghezza 100mm.",

    # --- Livello 6: geometrie composte / meno standard ---
    "Un supporto a T: base rettangolare 80x40x10mm con una colonna cilindrica centrale di diametro 20mm e altezza 50mm.",
    "Una piastra esagonale (chiave da 40mm lato-lato), spessore 8mm, con un foro esagonale centrale da 10mm.",
]


def classify_error(error_msg: str) -> str:
    """Categorizzazione approssimativa dell'errore per capire dove intervenire."""
    if not error_msg:
        return "nessuno"
    msg = error_msg.lower()
    if "timeout" in msg:
        return "timeout"
    if "attributeerror" in msg or "has no attribute" in msg:
        return "metodo_inesistente"
    if "typeerror" in msg:
        return "argomenti_errati"
    if "non ha definito la variabile 'result'" in msg:
        return "result_mancante"
    if "deve essere cq.workplane" in msg:
        return "tipo_result_errato"
    if "boolean" in msg or "geom" in msg or "brep" in msg or "solid" in msg:
        return "geometria_degenere"
    if "errore api gemini" in msg:
        return "errore_llm_api"
    return "altro"


def run_batch(base_url: str, timeout: float):
    results = []
    print(f"Batch di {len(TEST_PROMPTS)} prompt contro {base_url}\n")

    for i, prompt in enumerate(TEST_PROMPTS, start=1):
        output_name = f"batch_test_{i:02d}"
        print(f"[{i:02d}/{len(TEST_PROMPTS)}] {prompt[:70]}...", end=" ", flush=True)

        start = time.time()
        try:
            resp = requests.post(
                f"{base_url}/api/v1/generate",
                json={"prompt": prompt, "output_name": output_name},
                timeout=timeout,
            )
            elapsed = time.time() - start

            if resp.status_code == 200:
                data = resp.json()
                print(f"OK ({elapsed:.1f}s)")
                results.append({
                    "index": i,
                    "prompt": prompt,
                    "success": True,
                    "elapsed_s": round(elapsed, 2),
                    "error": None,
                    "error_category": "nessuno",
                    "generated_code": data.get("generated_code", ""),
                    "files": data.get("files"),
                })
            else:
                detail = resp.json().get("detail", {})
                error_msg = detail.get("message", resp.text)
                category = classify_error(error_msg)
                print(f"FALLITO [{category}] ({elapsed:.1f}s)")
                results.append({
                    "index": i,
                    "prompt": prompt,
                    "success": False,
                    "elapsed_s": round(elapsed, 2),
                    "error": error_msg,
                    "error_category": category,
                    "generated_code": detail.get("generated_code", ""),
                    "files": None,
                })

        except requests.exceptions.Timeout:
            elapsed = time.time() - start
            print(f"TIMEOUT HTTP ({elapsed:.1f}s)")
            results.append({
                "index": i,
                "prompt": prompt,
                "success": False,
                "elapsed_s": round(elapsed, 2),
                "error": f"Timeout HTTP dopo {timeout}s",
                "error_category": "timeout_http",
                "generated_code": "",
                "files": None,
            })
        except requests.exceptions.RequestException as e:
            elapsed = time.time() - start
            print(f"ERRORE CONNESSIONE ({elapsed:.1f}s)")
            results.append({
                "index": i,
                "prompt": prompt,
                "success": False,
                "elapsed_s": round(elapsed, 2),
                "error": str(e),
                "error_category": "connessione",
                "generated_code": "",
                "files": None,
            })

    return results


def print_summary(results):
    total = len(results)
    successes = sum(1 for r in results if r["success"])
    failures = total - successes

    print("\n" + "=" * 60)
    print(f"RISULTATO: {successes}/{total} successi ({successes/total*100:.0f}%)")
    print("=" * 60)

    if failures:
        print("\nFallimenti per categoria:")
        categories = {}
        for r in results:
            if not r["success"]:
                categories[r["error_category"]] = categories.get(r["error_category"], 0) + 1
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count}")

        print("\nDettaglio fallimenti:")
        for r in results:
            if not r["success"]:
                print(f"  [{r['index']:02d}] {r['error_category']}: {r['prompt'][:60]}")
                first_line = (r["error"] or "").split("\n")[0]
                print(f"       -> {first_line[:100]}")


def save_report(results, path: str):
    report = {
        "timestamp": datetime.now().isoformat(),
        "total": len(results),
        "successes": sum(1 for r in results if r["success"]),
        "results": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport completo salvato in: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch test della pipeline OpenCAD-AI")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL del backend")
    parser.add_argument("--timeout", type=float, default=45.0, help="Timeout HTTP per richiesta (s)")
    parser.add_argument("--report", default="batch_report.json", help="Path del report JSON")
    args = parser.parse_args()

    results = run_batch(args.url, args.timeout)
    print_summary(results)
    save_report(results, args.report)
