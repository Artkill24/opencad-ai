"""
Generatore di disegni tecnici 2D a partire da un solido CadQuery.

Le tre direzioni di proiezione usate qui NON sono gli assi puri (1,0,0)
ecc: verificato empiricamente che gli assi puri causano un caso degenere
nella costruzione del sistema di coordinate OCCT sottostante (gp_Ax2),
producendo viste sbagliate senza sollevare errori. Una piccola perturbazione
(1e-3) evita il caso degenere restando praticamente ortogonale.

Mappatura vista -> assi visibili, verificata numericamente su un blocco
di prova 80x40x20mm (non assunta dalla documentazione, misurata):
  - "top"   (lungo Z): mostra X in orizzontale, Y in verticale (pianta)
  - "front" (lungo Y): mostra Z in orizzontale, X in verticale (fronte)
  - "side"  (lungo X): mostra Z in orizzontale, Y in verticale (laterale)

LIMITI ONESTI (v1):
- Solo le quote d'ingombro complessive (bounding box) sono mostrate nel
  cartiglio, non le quote di ogni feature interna (fori, raggi, smussi) --
  un vero disegno GD&T completo è un lavoro sostanzialmente più grande.
- Le linee nascoste sono mostrate tratteggiate (comportamento di default
  dell'exporter CadQuery), non disattivabili in questa v1.
- Non applicabile ai solidi platonici mesh-only (build_platonic_solid) --
  quelli non hanno un BREP da cui l'exporter SVG possa generare proiezioni;
  servirebbe una logica di proiezione basata sulla mesh triangolare.
- La composizione del foglio (posizione delle 3 viste, dimensioni) è
  verificata solo strutturalmente (XML valido, viste presenti) -- non ho
  potuto verificare visivamente che il risultato sia leggibile/ben
  impaginato, perché non ho un modo di vedere il rendering da qui.
"""

import os
import tempfile
import datetime
import xml.etree.ElementTree as ET

import cadquery as cq

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)  # evita il prefisso ns0: nell'output serializzato

_EPS = 0.001
VIEW_DIRECTIONS = {
    "top":   (_EPS, _EPS, 1.0),
    "front": (_EPS, 1.0, _EPS),
    "side":  (1.0, _EPS, _EPS),
}
VIEW_LABELS = {"top": "PIANTA", "front": "FRONTE", "side": "LATERALE"}


def _export_view_fragment(result, direction, width: float, height: float) -> str:
    """
    Esporta una singola vista e ne estrae il gruppo <g> principale (il
    disegno vero e proprio) come stringa XML valida, pronta per essere
    incorporata in un foglio con più viste. Usa xml.etree invece di regex
    sul testo grezzo -- più robusto per estrarre un tag annidato senza
    rischiare di tagliare a metà per un match greedy sbagliato.
    """
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cq.exporters.export(
            result, tmp_path, exportType=cq.exporters.ExportTypes.SVG,
            opt={
                "projectionDir": direction,
                "showAxes": False,
                "width": width,
                "height": height,
                # Margini di default (marginLeft=200, marginTop=20) sono
                # pensati per un canvas da 800px -- su un canvas piccolo
                # come il nostro (260px) un margine di 200px manda in
                # confusione il calcolo interno di scala/posizionamento,
                # facendo sbordare il disegno fuori dal proprio riquadro
                # (verificato: bug reale osservato nella vista PIANTA,
                # non solo un'ipotesi). Margini proporzionati alla nostra
                # dimensione reale risolvono.
                "marginLeft": max(10, width * 0.05),
                "marginTop": max(10, height * 0.05),
                "showHidden": True,
            },
        )
        tree = ET.parse(tmp_path)
        root = tree.getroot()
        main_group = root.find(f"{{{SVG_NS}}}g")
        if main_group is None:
            raise RuntimeError("Nessun gruppo <g> trovato nell'SVG esportato -- formato inatteso.")
        return ET.tostring(main_group, encoding="unicode")
    finally:
        os.unlink(tmp_path)


def _fit_view_size(content_w_mm: float, content_h_mm: float, max_w_px: float, max_h_px: float) -> tuple:
    """
    Calcola le dimensioni del canvas SVG per una vista, mantenendo le
    proporzioni reali del contenuto (content_w_mm/content_h_mm, dalla
    bounding box 3D) invece di forzare sempre lo stesso riquadro fisso
    per ogni vista -- che sprecava spazio su pezzi sottili (verificato:
    fronte/laterale di un pezzo stretto usavano solo una piccola frazione
    del riquadro 260x220 assegnato). Il canvas risultante riempie il più
    possibile la cella massima (max_w_px x max_h_px) senza deformare le
    proporzioni.
    """
    if content_w_mm <= 0 or content_h_mm <= 0:
        return max_w_px, max_h_px
    aspect = content_w_mm / content_h_mm
    w = max_w_px
    h = w / aspect
    if h > max_h_px:
        h = max_h_px
        w = h * aspect
    return w, h


def _estimate_text_width(text: str, font_size: float) -> float:
    """
    Stima approssimata della larghezza di un testo in font monospace
    (~0.6 x font-size per carattere, rapporto comune per font monospace) --
    NON una misura di rendering reale (non ho un motore di metriche font
    da qui), ma sufficiente a evitare lo sbordamento grossolano osservato
    (nome pezzo lungo che usciva dal cartiglio e dal foglio senza alcun
    limite, verificato su un caso reale).
    """
    return len(text) * font_size * 0.6


def _truncate_to_width(text: str, font_size: float, max_width_px: float) -> str:
    """Accorcia il testo (con '…' finale) finché la sua larghezza stimata
    rientra in max_width_px. Se il testo ci sta già, lo ritorna invariato."""
    if _estimate_text_width(text, font_size) <= max_width_px:
        return text
    for n in range(len(text) - 1, 0, -1):
        candidato = text[:n] + "…"
        if _estimate_text_width(candidato, font_size) <= max_width_px:
            return candidato
    return "…"


def _build_title_block(x: float, y: float, width: float, height: float,
                        part_name: str, material: str, bbox_mm: dict) -> str:
    """
    Cartiglio ispirato alla pratica comune ISO 7200 -- NON una riproduzione
    certificata byte-per-byte dello standard formale (non ho il documento
    della norma come riferimento diretto per verificarne le proporzioni
    esatte). Campi inclusi: solo quelli che il sistema conosce per certo
    (nome pezzo, materiale, scala, data, ingombro) -- niente numero di
    disegno o responsabile approvazione, dati che non abbiamo e che
    inventare sarebbe peggio che ometterli.
    """
    oggi = datetime.date.today().strftime("%d/%m/%Y")
    ingombro = "?"
    if bbox_mm:
        ingombro = f"{bbox_mm.get('x', '?')} x {bbox_mm.get('y', '?')} x {bbox_mm.get('z', '?')} mm"

    righe = [
        ("PEZZO", part_name or "-"),
        ("MATERIALE", material or "-"),
        ("SCALA", "1:1"),
        ("DATA", oggi),
        ("INGOMBRO", ingombro),
    ]

    row_h = height / len(righe)
    label_w = width * 0.32

    parts = [f'<g transform="translate({x},{y})">']
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="white" stroke="black" stroke-width="1"/>')
    parts.append(f'<line x1="{label_w}" y1="0" x2="{label_w}" y2="{height}" stroke="black" stroke-width="0.5"/>')
    for i, (label, value) in enumerate(righe):
        ry = i * row_h
        if i > 0:
            parts.append(f'<line x1="0" y1="{ry}" x2="{width}" y2="{ry}" stroke="black" stroke-width="0.5"/>')
        parts.append(
            f'<text x="4" y="{ry + row_h * 0.65:.1f}" font-size="8" '
            f'font-family="monospace" font-weight="bold">{label}</text>'
        )
        # Larghezza disponibile per il valore: colonna valore meno un
        # margine di sicurezza su entrambi i lati -- il nome pezzo (spesso
        # lungo, generato dal prompt) sbordava dal cartiglio e dal foglio
        # senza questo controllo (verificato su un caso reale).
        valore_max_w = width - label_w - 8
        valore_troncato = _truncate_to_width(str(value), 8, valore_max_w)
        parts.append(
            f'<text x="{label_w + 4:.1f}" y="{ry + row_h * 0.65:.1f}" font-size="8" '
            f'font-family="monospace">{valore_troncato}</text>'
        )
    parts.append("</g>")
    return "".join(parts)


def generate_technical_drawing(result, svg_path: str, bbox_mm: dict = None,
                                part_name: str = None, material: str = None) -> dict:
    """
    Genera un foglio disegno con 3 viste ortogonali (pianta, fronte,
    laterale) più un cartiglio (nome pezzo, materiale, scala, data,
    ingombro), a partire da un solido CadQuery (Workplane/Assembly --
    non applicabile a mesh trimesh).

    Ritorna {"success": bool, "error": str o None}. Non solleva eccezioni
    verso il chiamante -- un fallimento qui è un'informazione in meno, non
    un motivo per considerare fallita l'intera generazione del pezzo.
    """
    try:
        max_w, max_h = 260, 220
        margin = 30
        title_block_w, title_block_h = 320, 90

        # Dimensioni contenuto per vista, dalla mappatura vista -> assi
        # visibili verificata numericamente in precedenza (vedi commento
        # in testa al modulo): pianta mostra X/Y, fronte mostra Z/X,
        # laterale mostra Z/Y. Con queste proporzioni reali (non un
        # riquadro fisso uguale per tutte) ogni vista usa lo spazio in
        # modo proporzionato alla propria forma.
        if bbox_mm:
            content_dims = {
                "top":   (bbox_mm.get("x", 1), bbox_mm.get("y", 1)),
                "front": (bbox_mm.get("z", 1), bbox_mm.get("x", 1)),
                "side":  (bbox_mm.get("z", 1), bbox_mm.get("y", 1)),
            }
        else:
            content_dims = {"top": (1, 1), "front": (1, 1), "side": (1, 1)}

        view_sizes = {
            nome: _fit_view_size(cw, ch, max_w, max_h)
            for nome, (cw, ch) in content_dims.items()
        }

        fragments = {
            nome: _export_view_fragment(result, direzione, *view_sizes[nome])
            for nome, direzione in VIEW_DIRECTIONS.items()
        }

        w_top, h_top = view_sizes["top"]
        w_front, h_front = view_sizes["front"]
        w_side, h_side = view_sizes["side"]

        # Disposizione: pianta in alto a sinistra, fronte in basso a
        # sinistra, laterale in basso a destra -- stesso layout di prima,
        # ma ora ogni cella ha la propria dimensione invece di un riquadro
        # fisso uguale per tutte.
        riga2_y = margin * 2 + h_top
        riga2_h = max(h_front, h_side)
        cartiglio_y = riga2_y + riga2_h + margin

        # Il testo del disclaimer in basso deve entrare nel foglio --
        # senza questo controllo veniva tagliato a metà (verificato su un
        # caso reale), perché la larghezza del foglio non teneva conto
        # della larghezza reale di quel testo, solo di viste e cartiglio.
        disclaimer_text = "Solo quote d'ingombro complessive -- non un disegno GD&T completo."
        disclaimer_w = margin + _estimate_text_width(disclaimer_text, 9) + margin

        sheet_w = max(
            margin * 2 + w_top,
            margin * 3 + w_front + w_side,
            margin * 2 + title_block_w,
            disclaimer_w,
        )
        sheet_h = cartiglio_y + title_block_h + margin

        positions = {
            "top":   (margin, margin, w_top, h_top),
            "front": (margin, riga2_y, w_front, h_front),
            "side":  (margin * 2 + w_front, riga2_y, w_side, h_side),
        }

        svg_parts = [
            f'<svg xmlns="{SVG_NS}" width="{sheet_w}" height="{sheet_h}">',
            f'<rect x="0" y="0" width="{sheet_w}" height="{sheet_h}" fill="white" stroke="black" stroke-width="1"/>',
        ]

        for nome, (x, y, w, h) in positions.items():
            svg_parts.append(f'<g transform="translate({x},{y})">')
            svg_parts.append(
                f'<rect x="0" y="0" width="{w}" height="{h}" '
                'fill="none" stroke="#cccccc" stroke-width="0.5"/>'
            )
            svg_parts.append(fragments[nome])
            svg_parts.append(
                f'<text x="5" y="{h - 5}" font-size="10" font-family="monospace">'
                f'{VIEW_LABELS[nome]}</text>'
            )
            svg_parts.append("</g>")

        # Cartiglio in basso a destra -- posizione convenzionale nel
        # disegno tecnico. Sotto, una riga di disclaimer sui limiti (solo
        # ingombro complessivo, non un GD&T completo) resta come prima.
        cartiglio_x = sheet_w - margin - title_block_w
        svg_parts.append(_build_title_block(
            cartiglio_x, cartiglio_y, title_block_w, title_block_h,
            part_name, material, bbox_mm
        ))
        svg_parts.append(
            f'<text x="{margin}" y="{sheet_h - 8}" font-size="9" '
            'font-family="monospace" fill="#666666">'
            "Solo quote d'ingombro complessive -- non un disegno GD&amp;T completo.</text>"
        )

        svg_parts.append("</svg>")
        full_svg = "\n".join(svg_parts)

        # Verifica strutturale prima di scrivere: l'XML composto deve
        # essere valido (non solo "sembrare" corretto per concatenazione
        # di stringhe) -- se ET.fromstring fallisce, meglio scoprirlo qui
        # con un errore chiaro che spedire un file rotto.
        ET.fromstring(full_svg)

        with open(svg_path, "w") as f:
            f.write(full_svg)

        return {"success": True, "error": None}

    except Exception as e:
        return {"success": False, "error": str(e)}
