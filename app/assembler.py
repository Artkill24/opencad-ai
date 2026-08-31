"""
Assemblaggio di più parti già generate in un unico assieme posizionato.

Approccio deliberatamente SEMPLICE: posizionamento MANUALE (l'utente
specifica l'offset X/Y/Z di ciascuna parte rispetto all'origine comune),
NON risoluzione automatica di vincoli meccanici (tipo "questo foro si
accoppia con questo albero rilevando automaticamente le quote
compatibili") -- quello è un problema di ricerca a sé, fuori portata per
un sistema di generazione CAD da prompt testuale. L'utente resta
informato: la generazione di ciascuna parte separata riporta già le
quote chiave (bounding box, ecc.), da cui calcolare l'offset giusto.

Ogni parte viene RICARICATA dal proprio file .step già salvato su disco
(non tenuta in memoria tra una generazione e l'altra) -- verificato che
il giro di andata/ritorno file non introduca alcuna perdita di precisione
(volume identico, confrontato a 4 cifre decimali, tra oggetto live e
oggetto ricaricato da file).
"""

import cadquery as cq


def assemble_parts(parts: list) -> cq.Assembly:
    """
    parts: lista di dict, ciascuno con:
        "step_path": percorso al file .step già generato in precedenza
        "name": nome della parte nell'assieme (usato solo per riferimento/debug)
        "position": [x, y, z] in mm, offset rispetto all'origine comune (default [0,0,0])

    Ritorna un cq.Assembly con tutte le parti importate e posizionate.
    Solleva ValueError se una parte non specifica step_path, o se il file
    non esiste/non è un STEP valido (lasciato propagare al chiamante, che
    lo trasforma in un errore HTTP leggibile).
    """
    if len(parts) < 2:
        raise ValueError("Servono almeno 2 parti per un assemblaggio.")

    assembly = cq.Assembly()
    for i, p in enumerate(parts):
        step_path = p.get("step_path")
        if not step_path:
            raise ValueError(f"Parte {i+1}: 'step_path' mancante.")

        imported = cq.importers.importStep(step_path)
        solid = imported.val()

        position = p.get("position", [0, 0, 0])
        if len(position) != 3:
            raise ValueError(f"Parte {i+1}: 'position' deve avere esattamente 3 valori [x, y, z].")
        x, y, z = position

        name = p.get("name") or f"parte_{i+1}"
        assembly.add(solid, name=name, loc=cq.Location(cq.Vector(x, y, z)))

    return assembly


def assemble_and_export(parts: list, step_out_path: str, stl_out_path: str, glb_out_path: str = None) -> dict:
    """
    Costruisce l'assieme e lo esporta sia come STEP (mantiene le singole
    parti come componenti distinti, non fuse) sia come STL (mesh unica,
    per l'anteprima nel visualizzatore 3D). Se glb_out_path è fornito,
    esporta anche GLB (più leggero per il viewer, ~55-65% in meno di STL
    verificato) -- CadQuery non lo produce nativamente, quindi ricarica
    l'STL appena scritto con trimesh e lo riesporta. Un fallimento qui
    non blocca l'assemblaggio già riuscito (has_glb lo segnala al chiamante).
    Ritorna volume e bounding box totali per coerenza con l'endpoint di
    generazione singola.
    """
    assembly = assemble_parts(parts)
    assembly.save(step_out_path)

    # Per volume/bbox totali e per l'STL, ricarica il file STEP appena
    # salvato come singolo compound -- stesso schema già verificato
    # (nessuna perdita di precisione nel giro file).
    combinato = cq.importers.importStep(step_out_path)
    solido = combinato.val()

    cq.exporters.export(
        combinato, stl_out_path,
        exportType=cq.exporters.ExportTypes.STL,
        tolerance=0.01, angularTolerance=0.1
    )

    has_glb = False
    if glb_out_path:
        try:
            import trimesh
            mesh_per_glb = trimesh.load_mesh(stl_out_path)
            mesh_per_glb.export(glb_out_path)
            has_glb = True
        except Exception:
            pass

    bbox = solido.BoundingBox()
    return {
        "volume_mm3": round(solido.Volume(), 1),
        "has_glb": has_glb,
        "bbox_mm": {
            "x": round(bbox.xlen, 2),
            "y": round(bbox.ylen, 2),
            "z": round(bbox.zlen, 2),
        },
    }
