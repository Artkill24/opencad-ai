"""
Verifica geometrica dei file STEP prodotti da test_batch.py.
Carica ogni STEP in outputs/ e stampa bounding box (mm) e volume (mm^3),
cosi' puoi confrontarli con le dimensioni richieste nel prompt senza
aprire un visualizzatore CAD.
"""

import glob
import os

import cadquery as cq

OUTPUTS_DIR = "outputs"

EXPECTED = {
    "batch_test_01": "cubo 40x40x40mm",
    "batch_test_02": "cilindro diametro 60mm, altezza 25mm",
    "batch_test_03": "disco diametro 80mm, spessore 8mm, foro 20mm",
    "batch_test_04": "piastra 100x60x5mm, 4 fori da 6mm",
    "batch_test_05": "flangia diametro 90mm, spessore 10mm",
    "batch_test_06": "disco diametro 100mm, spessore 6mm",
    "batch_test_07": "blocco 50x50x20mm con fillet",
    "batch_test_08": "staffa a L, due piastre 60x30x5mm a 90 gradi",
    "batch_test_09": "scatola aperta 80x60x40mm, pareti 3mm (CAVA)",
    "batch_test_10": "tubo cavo: est.40mm int.32mm lung.100mm (CAVO)",
    "batch_test_11": "supporto a T: base 80x40x10 + colonna diam.20 alt.50",
    "batch_test_12": "piastra esagonale lato-lato 40mm, spessore 8mm",
}


def verify_file(step_path: str):
    name = os.path.splitext(os.path.basename(step_path))[0]
    try:
        result = cq.importers.importStep(step_path)
        solid = result.val()
        bbox = solid.BoundingBox()
        volume = solid.Volume()

        xlen = bbox.xlen
        ylen = bbox.ylen
        zlen = bbox.zlen

        expected = EXPECTED.get(name, "(nessuna attesa registrata)")
        print(f"{name}")
        print(f"  atteso:      {expected}")
        print(f"  bounding box: {xlen:.1f} x {ylen:.1f} x {zlen:.1f} mm")
        print(f"  volume:       {volume:.0f} mm^3")
        print()

    except Exception as e:
        print(f"{name}: ERRORE nel caricare/analizzare il file -> {e}\n")


if __name__ == "__main__":
    step_files = sorted(glob.glob(os.path.join(OUTPUTS_DIR, "batch_test_*.step")))

    if not step_files:
        print(f"Nessun file batch_test_*.step trovato in {OUTPUTS_DIR}/")
        print("Esegui prima test_batch.py per generarli.")
    else:
        print(f"Trovati {len(step_files)} file STEP da verificare.\n")
        for path in step_files:
            verify_file(path)
