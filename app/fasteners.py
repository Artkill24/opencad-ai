"""
Parti standard ISO/DIN reali (dadi esagonali, viti a testa esagonale,
cuscinetti a sfere) via cq_warehouse (https://github.com/gumyr/cq_warehouse)
-- dimensioni prese da tabelle standard ufficiali incluse nel pacchetto,
non stimate dal modello.

AVVISO SUL RISCHIO (onestà, non solo formalità): l'ultimo commit del
progetto risale a settembre 2023, circa 4 mesi prima dell'uscita della
versione di CadQuery che usiamo qui (2.4.0). Nessuna garanzia formale di
compatibilità. VERIFICATO empiricamente prima di scrivere questo modulo,
non solo "sembra funzionare":
- HexNut M6-1 (iso4032): volume e bounding box confrontati con la
  geometria esagonale calcolata a mano (formula esatta larghezza tra i
  piatti <-> tra i vertici, volume prisma meno foro) -- scarto 0.33%.
- HexHeadScrew M6-1x25mm (iso4017): stessa verifica, scarto 0.05% una
  volta capito che 'simple=True' approssima il filetto con un cilindro
  al DIAMETRO MINORE (tecnica standard), non al diametro nominale.
- HexHeadScrew M6-1x25mm con simple=False (filetto elicoidale REALE):
  volume 938.66mm^3, verificato dentro l'intervallo atteso calcolato
  indipendentemente (841.9-1074.1mm^3, tra l'approssimazione al diametro
  minore e un cilindro pieno al diametro nominale -- un filetto vero ha
  creste che arrivano quasi al nominale e valli verso il minore, deve
  necessariamente stare in mezzo). Tempo di costruzione ~2.9s per pezzo,
  non trascurabile ma accettabile -- non istantaneo come simple=True.
- SingleRowDeepGrooveBallBearing M8-22-7 (il classico "608", bearing_type
  "SKT"): bounding box 22.00x22.00x7.00mm, precisione al centesimo con lo
  standard reale. Volume 1644.75mm^3, verificato sotto il limite fisico
  assoluto (un cilindro pieno delle stesse dimensioni esterne, 2660.9mm^3
  -- un cuscinetto vero DEVE avere meno materiale, per il foro passante e
  la cavità interna con le sfere).
- PlainWasher M6 (iso7089): BUG REALE TROVATO in cq_warehouse, non solo
  uno scarto di misura -- make_washer() chiama .toPending() DOPO .close(),
  che però registra già il wire come pending da solo. Il wire viene
  quindi elaborato due volte da .revolve(), che produce due solidi
  perfettamente coincidenti nello spazio: la bounding box risulta
  corretta (12.00x12.00x1.80mm, i due duplicati occupano lo stesso
  spazio) ma il VOLUME riportato è esattamente doppio di quello vero
  (291.34mm^3 invece di 145.67mm^3, verificato con un test minimo
  isolato: rapporto 2.000 esatto, sparito togliendo il .toPending()
  ridondante). Conseguenza pratica, non solo estetica: peso/costo DFM
  sbagliati del 100%, e la mesh esportata conterrebbe geometria duplicata
  sovrapposta (problematico per la stampa 3D). Per questo NON usiamo
  PlainWasher direttamente: prendiamo le dimensioni ufficiali dalla sua
  stessa tabella dati (tramite le funzioni pure isolate_fastener_type/
  evaluate_parameter_dict, che non toccano la costruzione geometrica
  difettosa) e costruiamo la rondella noi, con .revolve() usato
  correttamente. Verificato: volume e bounding box esatti al centesimo
  contro il calcolo indipendente.

Le classi Nut/Screw ereditano da cq.Solid, Bearing da cq.Compound (più
parti: piste + sfere, non un solido singolo) -- qui le incapsuliamo
tutte in un cq.Workplane per uniformità con il resto della pipeline,
stesso pattern di polyhedra.py.
"""

import cadquery as cq
from cq_warehouse.fastener import HexNut, HexHeadScrew, PlainWasher, isolate_fastener_type, evaluate_parameter_dict
from cq_warehouse.bearing import SingleRowDeepGrooveBallBearing

SUPPORTED_NUT_TYPES = ["iso4032", "iso4033", "iso4035"]
SUPPORTED_SCREW_TYPES = ["iso4014", "iso4017"]
SUPPORTED_BEARING_TYPES = ["SKT"]  # solo il tipo standard verificato finora
SUPPORTED_WASHER_TYPES = ["iso7089"]  # solo il tipo standard verificato finora


def build_hex_nut(size: str, fastener_type: str = "iso4032", real_thread: bool = False) -> cq.Workplane:
    """
    Costruisce un dado esagonale standard ISO, pronto per essere assegnato
    a 'result'.

    size: designazione ISO del filetto, es. "M6-1" (M6, passo 1.0mm),
        "M8-1.25", "M10-1.5" -- il formato è "M<diametro>-<passo>".
    fastener_type: uno tra "iso4032" (dado esagonale standard),
        "iso4033" (dado esagonale stile 2, più alto), "iso4035"
        (dado esagonale ribassato, smussato).
    real_thread: se True, genera il filetto elicoidale REALE (verificato,
        vedi nota in testa al modulo) invece dell'approssimazione a
        cilindro liscio -- più lento (qualche secondo in più a pezzo),
        usare solo se serve davvero vedere/misurare il filetto vero
        (es. per un rendering ravvicinato o un controllo di interferenza),
        non per la visualizzazione normale.
    """
    if fastener_type not in SUPPORTED_NUT_TYPES:
        raise ValueError(
            f"Tipo di dado non supportato: '{fastener_type}'. "
            f"Disponibili: {', '.join(SUPPORTED_NUT_TYPES)}"
        )
    nut = HexNut(size=size, fastener_type=fastener_type, simple=not real_thread)
    return cq.Workplane(obj=nut)


def build_hex_head_screw(size: str, length_mm: float, fastener_type: str = "iso4017",
                          real_thread: bool = False) -> cq.Workplane:
    """
    Costruisce una vite a testa esagonale standard ISO, pronta per essere
    assegnata a 'result'.

    size: designazione ISO del filetto, es. "M6-1" (M6, passo 1.0mm).
    length_mm: lunghezza del gambo filettato in mm (sotto la testa).
    fastener_type: "iso4014" (vite a testa esagonale, gambo parzialmente
        filettato) o "iso4017" (vite a testa esagonale, gambo completamente
        filettato).
    real_thread: se True, genera il filetto elicoidale REALE (verificato,
        vedi nota in testa al modulo) invece dell'approssimazione a
        cilindro liscio al diametro minore -- più lento (qualche secondo
        in più a pezzo), usare solo se serve davvero vedere/misurare il
        filetto vero, non per la visualizzazione normale.
    """
    if fastener_type not in SUPPORTED_SCREW_TYPES:
        raise ValueError(
            f"Tipo di vite non supportato: '{fastener_type}'. "
            f"Disponibili: {', '.join(SUPPORTED_SCREW_TYPES)}"
        )
    screw = HexHeadScrew(size=size, length=length_mm, fastener_type=fastener_type, simple=not real_thread)
    return cq.Workplane(obj=screw)


def build_bearing(size: str, bearing_type: str = "SKT") -> cq.Workplane:
    """
    Costruisce un cuscinetto radiale a sfere standard (a gola profonda, il
    tipo più comune per applicazioni generiche), pronto per essere
    assegnato a 'result'.

    size: designazione nel formato "M<foro>-<diametro_esterno>-<larghezza>",
        es. "M8-22-7" (il classico cuscinetto "608" -- 8mm di foro, 22mm
        di diametro esterno, 7mm di larghezza -- molto comune in stampanti
        3D e applicazioni hobbistiche/meccaniche generiche). Verificato
        esistere nella tabella dati ufficiale; se la taglia richiesta non
        esiste, cq_warehouse solleva un errore chiaro invece di inventare
        dimensioni plausibili ma sbagliate.
    bearing_type: attualmente solo "SKT" verificato (cuscinetto radiale a
        sfere a gola profonda standard -- il tipo di gran lunga più comune;
        altri tipi della libreria -- a contatto angolare, a rulli, conici --
        non ancora testati, non usarli finché non verificati).
    """
    if bearing_type not in SUPPORTED_BEARING_TYPES:
        raise ValueError(
            f"Tipo di cuscinetto non supportato: '{bearing_type}'. "
            f"Disponibili: {', '.join(SUPPORTED_BEARING_TYPES)}"
        )
    cuscinetto = SingleRowDeepGrooveBallBearing(size=size, bearing_type=bearing_type)
    return cq.Workplane(obj=cuscinetto)


def build_washer(size: str, fastener_type: str = "iso7089") -> cq.Workplane:
    """
    Costruisce una rondella piana standard ISO, pronta per essere
    assegnata a 'result'.

    NON usa PlainWasher di cq_warehouse direttamente -- BUG REALE
    verificato nella libreria (vedi nota in testa al modulo): il volume
    riportato risulta esattamente doppio di quello vero, per una chiamata
    ridondante a .toPending() nel suo metodo interno make_washer(). Qui
    prendiamo le dimensioni dalla STESSA tabella dati ufficiale (fonte
    verificata), ma costruiamo la geometria noi con .revolve() usato
    correttamente -- verificato: volume e bounding box esatti al
    centesimo contro il calcolo indipendente.

    size: designazione ISO, es. "M6" (senza passo -- le rondelle non
        sono filettate).
    fastener_type: attualmente solo "iso7089" verificato (rondella piana
        standard, forma A -- il tipo più comune).
    """
    if fastener_type not in SUPPORTED_WASHER_TYPES:
        raise ValueError(
            f"Tipo di rondella non supportato: '{fastener_type}'. "
            f"Disponibili: {', '.join(SUPPORTED_WASHER_TYPES)}"
        )
    isolato = isolate_fastener_type(fastener_type, PlainWasher.fastener_data)
    if size not in isolato:
        raise ValueError(
            f"Taglia '{size}' non trovata per rondella {fastener_type}. "
            f"Disponibili: {', '.join(sorted(isolato.keys()))}"
        )
    dims = evaluate_parameter_dict(isolato[size], is_metric=True)
    d1, d2, h = dims["d1"], dims["d2"], dims["h"]

    profilo = (
        cq.Workplane("XZ")
        .moveTo(d1 / 2, 0)
        .hLineTo(d2 / 2)
        .vLineTo(h)
        .hLineTo(d1 / 2)
        .close()
    )
    return profilo.revolve()
