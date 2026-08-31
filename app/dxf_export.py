"""
Export DXF per taglio laser/CNC -- profilo 2D piatto della faccia
superiore del pezzo, NON il wireframe 3D completo del solido.

Verificato: cq.exporters.export() con DXF su un intero Workplane 3D
esporta TUTTI gli spigoli del solido in 3D (comprese le facce superiore
E inferiore duplicate, più le linee verticali di collegamento) -- un
risultato tecnicamente valido come file DXF, ma inutile per il taglio
laser (il laser si confonderebbe con cerchi duplicati a "altezze" diverse
e linee verticali senza senso in 2D; verificato su un caso reale: 12
entità invece delle 2 attese). Selezionando solo `.faces(">Z").wires()`
prima dell'export si ottiene il profilo piatto singolo corretto --
verificato: esattamente le entità attese (2 CIRCLE sui raggi giusti per
una flangia forata), niente duplicati, rilette indipendentemente con
ezdxf per la conferma.

LIMITE ONESTO: funziona correttamente per pezzi con una faccia superiore
PIATTA (la stragrande maggioranza dei casi tipici per il taglio laser --
piastre, flange, staffe). Per un pezzo la cui faccia superiore fosse
curva (es. la cima di una sfera), .wires() darebbe comunque un contorno,
ma non necessariamente il profilo "utile" per il taglio -- non verificato
su quel caso, considerarlo fuori scope per ora.
"""

import cadquery as cq


def export_dxf_top_profile(result, dxf_path: str) -> dict:
    """
    Esporta il profilo 2D piatto della faccia superiore di 'result' come
    DXF, pronto per taglio laser/CNC. Ritorna {"success": bool, "error": str o None}.
    Non solleva eccezioni verso il chiamante -- un fallimento qui è
    un'informazione in meno, non un motivo per considerare fallita
    l'intera generazione del pezzo (che è comunque riuscita).
    """
    try:
        top_wires = result.faces(">Z").wires()
        cq.exporters.export(top_wires, dxf_path, exportType=cq.exporters.ExportTypes.DXF)
        return {"success": True, "error": None}
    except Exception as e:
        return {"success": False, "error": str(e)}
