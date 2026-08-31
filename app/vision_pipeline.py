"""
Foto -> descrizione geometrica PROPORZIONALE (non foto -> misure assolute).

Perché niente numeri assoluti in questo step: una singola fotografia non
contiene l'informazione di scala necessaria per misure affidabili -- anche
chiedendo di confrontare pixel con un oggetto di riferimento nell'inquadratura,
la stima resta fragile (dipende da prospettiva, angolo di ripresa, distanza).
Il modo affidabile di ottenere una misura reale è che l'utente stesso la prenda
con un calibro/righello sull'oggetto fisico -- non che l'AI la indovini da una
foto.

Quindi questo modulo fa una cosa sola, e la fa bene: guarda la foto e descrive
la FORMA in termini proporzionali (rapporti, multipli) rispetto a UNA
dimensione caratteristica che sceglie e nomina esplicitamente (es. "il
diametro esterno"). L'utente fornisce poi il valore reale in mm di quella
singola dimensione, misurata sull'oggetto vero -- e quel valore, combinato
con le proporzioni descritte, permette al passo successivo (generazione CAD,
già esistente) di calcolare tutte le quote in millimetri.
"""

import base64

from llm_pipeline import _call_gemini

VISION_SYSTEM_PROMPT = """
Sei un ingegnere meccanico che osserva la fotografia di un oggetto fisico e
ne descrive la FORMA -- non le dimensioni assolute -- per la ricostruzione CAD.

Il tuo compito NON è generare codice, e NON è stimare misure in millimetri:
una singola fotografia non contiene l'informazione di scala necessaria per
misure affidabili, quindi qualunque numero assoluto che daresti sarebbe
inventato. Descrivi invece la geometria in termini PROPORZIONALI, tutti
relativi a UNA sola dimensione caratteristica dell'oggetto che tu stesso
scegli e nomini chiaramente (es. "il diametro esterno del cilindro",
"la lunghezza della base", "l'altezza totale").

REGOLE RIGIDE:
1. La primissima riga della risposta deve essere ESATTAMENTE in questo
   formato, senza altro testo prima:
   DIMENSIONE_RIFERIMENTO: <nome breve e chiaro della dimensione scelta>
   Esempio: DIMENSIONE_RIFERIMENTO: diametro esterno del cilindro
2. Dopo quella riga, descrivi la forma come combinazione di primitive
   geometriche semplici (cilindri, blocchi, coni, fori, smussi, pattern
   circolari), esprimendo OGNI altra misura come frazione o multiplo della
   dimensione di riferimento -- es. "l'altezza è circa 1.5 volte la
   dimensione di riferimento", "il foro centrale ha diametro pari a un
   terzo della dimensione di riferimento", "sono presenti 4 fori aggiuntivi
   disposti su una circonferenza pari a due terzi della dimensione di
   riferimento".
3. NON usare MAI numeri assoluti in millimetri, centimetri o pollici in
   questa fase -- solo rapporti, frazioni, multipli della dimensione di
   riferimento.
4. Evita forme organiche, curve libere o dettagli che non si possono
   ridurre a primitive geometriche semplici; se necessario, semplifica e
   dillo esplicitamente nella descrizione.
5. Non descrivere colori, materiali, texture, riflessi o dettagli estetici
   -- SOLO geometria: forma, proporzioni, fori, simmetrie, spessori relativi.
6. Rispondi ESCLUSIVAMENTE con la riga DIMENSIONE_RIFERIMENTO seguita dalla
   descrizione proporzionale. Nessuna premessa, nessuna spiegazione del tuo
   ragionamento, nessun markdown.
"""


def describe_shape_from_image(image_bytes: bytes, mime_type: str) -> dict:
    """
    Manda la foto a Gemini in modalità visione e ritorna la forma come
    descrizione proporzionale, insieme al nome della dimensione di
    riferimento scelta dal modello -- che il chiamante userà per etichettare
    il campo dove l'utente inserirà la misura reale in mm.

    Ritorna: {"reference_dimension_name": str, "proportional_description": str}
    """
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    contents = [{
        "role": "user",
        "parts": [
            {"text": "Analizza questa fotografia e descrivi la FORMA dell'oggetto in termini proporzionali, seguendo le regole date."},
            {"inline_data": {"mime_type": mime_type, "data": image_b64}},
        ],
    }]

    raw = _call_gemini(contents, system_instruction=VISION_SYSTEM_PROMPT).strip()

    first_line, _, rest = raw.partition("\n")
    prefix = "DIMENSIONE_RIFERIMENTO:"
    if first_line.strip().upper().startswith(prefix):
        reference_name = first_line.split(":", 1)[1].strip()
        description = rest.strip()
    else:
        reference_name = "dimensione principale"
        description = raw

    return {
        "reference_dimension_name": reference_name,
        "proportional_description": description,
    }


TECHNICAL_DRAWING_SYSTEM_PROMPT = """
Sei un ingegnere meccanico che analizza un disegno tecnico (tavola di
fabbricazione, disegno d'assieme, disegno costruttivo) per estrarne i dati
necessari alla ricostruzione CAD -- a differenza di una foto casuale di un
oggetto, un disegno tecnico HA le quote scritte esplicitamente: quando un
numero è leggibile, riportalo esattamente come scritto, NON approssimarlo
e NON inventarlo se non è leggibile.

REGOLA PIÙ IMPORTANTE, SOPRA TUTTE LE ALTRE: se una quota, una tolleranza o
un dettaglio non è leggibile con certezza (testo piccolo, sfocato, ambiguo),
mettilo nella sezione QUOTE_INCERTE con una nota sul perché, MAI nella
sezione QUOTE_SICURE. Un utente che si fida di una quota sbagliata rischia
un pezzo reale sbagliato -- è molto peggio che dire onestamente "non sono
sicuro". Se un disegno intero è troppo degradato per essere letto in modo
affidabile, dillo chiaramente invece di produrre un'estrazione inventata.

Un disegno può mostrare PIÙ PARTI distinte (es. un disegno d'assieme con
numeri di posizione tipo ①②, o più viste di componenti diversi sullo stesso
foglio) -- identifica ogni parte separatamente, non fondere le quote di
parti diverse insieme.

Riconosci le notazioni standard più comuni:
- Ø = diametro; quote con classe di tolleranza (es. "36k6", "30H7") sono
  quote di accoppiamento di precisione -- riportale, ma segnala che sono
  tolleranze di lavorazione fine, non necessarie per la forma 3D di base.
- Ingranaggi: m=modulo, z=numero di denti, dp=diametro primitivo,
  da=diametro esterno, θ=angolo di pressione -- se presenti tutti,
  segnalali esplicitamente come "PARAMETRI_INGRANAGGIO" a parte, sono
  direttamente utilizzabili per una generazione parametrica precisa.
- Smussi in formato "2.5x45°" (lunghezza x angolo), raggi "R25", sedi per
  linguette/chiavette, scanalature.
- Simboli di finitura superficiale (√, ∇) -- ignorali per la geometria,
  non modelliamo la rugosità.

FORMATO DI RISPOSTA OBBLIGATORIO (nessun testo fuori da questo schema):

PARTE 1: <nome/numero della parte se indicato nel disegno, altrimenti una
breve descrizione tipo "corpo principale">
QUOTE_SICURE:
- <cosa misura>: <valore> <unità>
(una riga per quota leggibile con certezza; se è un parametro di
ingranaggio mettilo comunque qui E ripetilo in PARAMETRI_INGRANAGGIO)
PARAMETRI_INGRANAGGIO: <solo se presente -- m=... z=... dp=... da=... θ=...>
QUOTE_INCERTE:
- <cosa sembra essere>: <valore stimato se possibile, altrimenti "illeggibile"> (<motivo>)
(ometti questa sezione se non ci sono quote incerte)
PROMPT_SUGGERITO: <una frase in italiano, pronta per generare questa parte
via CAD parametrico, che usa SOLO le quote sicure e i parametri ingranaggio
-- non includere le quote incerte nel prompt>

PARTE 2: ...
(ripeti per ogni parte distinta trovata nel disegno)

Se il disegno mostra una sola parte, produci comunque un solo blocco
"PARTE 1: ...".
"""


def analyze_technical_drawing(image_bytes: bytes, mime_type: str) -> dict:
    """
    Manda un disegno tecnico a Gemini in modalità visione e ritorna le
    parti rilevate con le loro quote, separando esplicitamente quelle
    lette con certezza da quelle incerte -- MAI inventate per completare
    lo schema. A differenza di describe_shape_from_image(), qui i numeri
    assoluti sono legittimi (sono scritti sul disegno), ma solo se
    davvero leggibili.

    Ritorna: {"raw_text": str, "parse_error": str o None}. Il parsing
    strutturato in parti è lasciato al chiamante (o a un parser dedicato
    da aggiungere se questo approccio si dimostra affidabile) -- per ora
    ritorniamo il testo strutturato grezzo, pensato per essere leggibile
    e verificabile direttamente da un umano prima di fidarsene.
    """
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    contents = [{
        "role": "user",
        "parts": [
            {"text": "Analizza questo disegno tecnico ed estrai i dati seguendo ESATTAMENTE il formato richiesto."},
            {"inline_data": {"mime_type": mime_type, "data": image_b64}},
        ],
    }]

    try:
        raw = _call_gemini(contents, system_instruction=TECHNICAL_DRAWING_SYSTEM_PROMPT).strip()
        return {"raw_text": raw, "parse_error": None}
    except Exception as e:
        return {"raw_text": "", "parse_error": str(e)}
