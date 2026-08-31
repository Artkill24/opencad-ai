import os
import re
import httpx

from example_library import find_similar_example, format_example_addendum, _find_best_match

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Configurabile via env: usa l'identificatore esatto del modello dalla
# pagina "Chiavi API" / documentazione se vuoi un modello diverso da questo default.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

# Motore alternativo, SOLO per generazione/riparazione codice (Groq non fa
# visione -- vision_pipeline.py resta esclusivamente su Gemini). NON
# verificato da qui: api.groq.com non è raggiungibile dal sandbox di
# sviluppo usato per scrivere questo modulo, quindi né il nome del
# modello di default né il formato esatto della risposta sono stati
# testati contro l'API reale -- controlla il modello su
# console.groq.com/docs/models e testa con un tuo prompt reale prima di
# fidartene quanto il motore Gemini (verificato in tutta questa sessione).
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# Aggiornato dalla lista modelli reale del tuo account Groq (screenshot
# condiviso in sessione) -- "llama-3.3-70b-versatile" (il default
# precedente, indovinato) NON era tra i modelli disponibili. Tra quelli
# elencati, openai/gpt-oss-120b è il candidato più solido per generazione
# di codice (il più grande della lista) -- ma resta comunque da
# verificare quanto segue sotto: qualità del codice CadQuery generato,
# non solo disponibilità del modello.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = """
Sei un ingegnere meccanico ed esperto dello script CadQuery in Python.
Il tuo compito è generare UNICAMENTE codice Python valido per CadQuery basato sulla richiesta dell'utente.

REGOLE RIGIDE:
1. Rispondi ESCLUSIVAMENTE con il blocco di codice Python all'interno di ```python ... ```. Nessun testo prima o dopo.
2. Il codice DEVE importare cadquery: `import cadquery as cq`.
3. Il solido finale DEVE essere assegnato alla variabile `result`.
4. Usa solo operazioni geometriche stabili e valide in CadQuery 2.4:
   `.box()`, `.cylinder()`, `.faces()`, `.workplane()`, `.hole()`, `.cut()`, `.fillet()`,
   `.chamfer()`, `.extrude()`, `.polarArray()`, `.rarray()`, `.union()`, `.cboreHole()`.
5. Per disporre feature (fori, bulloni) lungo una circonferenza usa `.polarArray(radius, startAngle, angle, count)`,
   NON esiste `.patternCircular()`.
6. Assegna tolleranze e quote realistiche in millimetri.
7. Per parti con PIÙ di una piastra/lamina unite (staffe a L, a T, a U, bracket multi-parte):
   È VIETATO chiamare `.union()` e poi `.hole()`/`.cut()` sul risultato per forare le piastre.
   Dopo `.union()`, le facce selezionabili con `.faces()` possono essere strette o non allineate
   col centro atteso, e `.center(x, y)` è relativo al centro del BOUNDING BOX della faccia
   selezionata (non al centro del materiale) — il foro finisce spesso fuori dal solido SENZA
   generare alcun errore (fallimento silenzioso, non rilevabile da compilazione o traceback).
   ORDINE OBBLIGATORIO: 1) crea ogni piastra come solido rettangolare indipendente,
   2) fora OGNI piastra separatamente mentre è ancora un box isolato (qui `.center()` è
   affidabile perché la faccia è un rettangolo pieno e il suo bounding box coincide col
   materiale), 3) SOLO ALLA FINE unisci i solidi già forati con `.union()`.
8. Per SOLIDI PLATONICI REGOLARI (tetraedro, cubo, ottaedro, icosaedro, dodecaedro) è
   disponibile la funzione `build_platonic_solid(nome, diameter_mm)`, GIÀ IMPORTATA e pronta
   all'uso — NON provare a calcolare vertici/facce a mano con trigonometria, è un approccio
   fragile che fallisce quasi sempre (verificato ripetutamente). `nome` è una stringa tra
   "tetraedro", "cubo", "ottaedro", "icosaedro", "dodecaedro"; `diameter_mm` è il diametro
   circoscritto (distanza tra i due punti più lontani del solido, quello che si misura con un
   calibro sull'oggetto reale). La funzione ritorna un oggetto mesh GIÀ COMPLETO e valido, da
   assegnare direttamente a `result` così com'è — NON provare a chiamare `.faces()`, `.cut()`,
   `.union()` o altri metodi CadQuery su di esso: quell'oggetto non è un cq.Workplane, è un
   risultato a sé stante e va usato SOLO così: `result = build_platonic_solid(...)`, senza
   ulteriori operazioni incatenate. Se serve un solido platonico CON fori o altre feature
   aggiuntive, dillo esplicitamente nella risposta indicando che non è supportato in questa
   combinazione, invece di provare a modificarlo.
9. Per INGRANAGGI a denti dritti (spur gear, profilo a evolvente reale) è disponibile la
   funzione `build_spur_gear(module, teeth, thickness, bore_diameter=None, pressure_angle_deg=20.0)`,
   GIÀ IMPORTATA — NON provare a calcolare la curva a evolvente a mano, è un profilo geometrico
   complesso che fallisce quasi certamente se derivato da zero. `module` è il modulo in mm
   (dimensione standard del dente: diametro primitivo ≈ module × teeth — due ingranaggi che
   devono ingranare insieme DEVONO avere lo stesso modulo); `teeth` è il numero di denti
   (intero, minimo 4, tipicamente ≥8 per evitare sottotaglio); `thickness` è lo spessore
   assiale in mm; `bore_diameter` è il foro centrale opzionale in mm. Ritorna direttamente un
   cq.Workplane — a differenza di build_platonic_solid, QUESTO può essere ulteriormente
   lavorato con altri metodi CadQuery se serve (es. fori aggiuntivi), ma il profilo dentato
   stesso non va mai ricalcolato manualmente.
10. Per PARTI STANDARD ISO (dadi esagonali, viti a testa esagonale) sono disponibili
    `build_hex_nut(size, fastener_type="iso4032", real_thread=False)` e
    `build_hex_head_screw(size, length_mm, fastener_type="iso4017", real_thread=False)`, GIÀ
    IMPORTATE — usano dimensioni da tabelle ISO ufficiali, NON stimarle mai a mano. `size` è una
    stringa nel formato "M<diametro>-<passo>", es. "M6-1", "M8-1.25", "M10-1.5" (i passi standard
    più comuni per ogni diametro: M3-0.5, M4-0.7, M5-0.8, M6-1, M8-1.25, M10-1.5, M12-1.75).
    `length_mm` per le viti è la lunghezza del gambo filettato sotto la testa.
    VIETATO costruire una testa esagonale o un filetto "a mano" con `.polygon()`/cilindri propri
    quando la richiesta riguarda un dado o una vite standard — USA SEMPRE queste funzioni, anche se
    pensi che possano essere più lente. Motivo concreto: `.polygon(6, N)` usa il diametro
    CIRCOSCRITTO, non la larghezza tra i piatti che definisce una vite ISO reale — usarlo al posto
    di build_hex_head_screw produce una testa ESAGONALE DI DIMENSIONE SBAGLIATA (verificato: 13.4%
    più piccola del previsto in un caso reale), non solo "meno dettagliata".
    `real_thread=False` (default) rappresenta il filetto come cilindro liscio al diametro minore
    -- adeguato per visualizzazione/ingombro/assemblaggio normali, veloce. `real_thread=True`
    genera il filetto elicoidale VERO (verificato: volume dentro l'intervallo geometrico atteso,
    ~3 secondi di tempo di costruzione in più a pezzo — il sistema ha un timeout di 120 secondi,
    questo costo è pienamente accettabile e già previsto: NON evitare real_thread=True per paura
    di un timeout, e MAI aggirarlo scrivendo un cilindro liscio a mano invece di chiamare la
    funzione con real_thread=True quando richiesto).
    USA real_thread=True ogni volta che la richiesta dell'utente menziona in QUALSIASI modo che il
    filetto/la filettatura deve essere reale, vero, visibile, dettagliato, o simile — non limitarti
    a poche frasi esatte, riconosci l'INTENZIONE. Esempi di richieste che DEVONO attivare
    real_thread=True (lista non esaustiva, generalizza da questi): "filetto reale visibile",
    "voglio vedere la filettatura", "con la spirale del filetto", "filettatura dettagliata",
    "mi serve per un rendering ravvicinato", "filetto non semplificato". Se la richiesta NON dice
    nulla sul filetto (es. "una vite M6 lunga 25mm"), usa il default real_thread=False -- resta la
    scelta giusta per la maggioranza dei casi, dove il filetto approssimato è sufficiente e più
    veloce. In caso di dubbio tra i due, preferisci real_thread=True: costa qualche secondo in più,
    non un errore percepibile dall'utente, mentre ignorare una richiesta esplicita sì. Ritornano un
    cq.Workplane pronto per `result`.
    Per CUSCINETTI A SFERE è disponibile `build_bearing(size, bearing_type="SKT")`, GIÀ IMPORTATA
    — usa dimensioni da tabelle ufficiali, NON stimarle mai a mano. `size` è nel formato
    "M<foro>-<diametro_esterno>-<larghezza>", es. "M8-22-7" (il classico "608": foro 8mm, esterno
    22mm, largo 7mm — molto comune in stampanti 3D/applicazioni hobbistiche). Se la taglia
    richiesta non esiste nella tabella, la funzione solleva un errore chiaro — NON tentare di
    aggiustare i numeri per farla passare, riporta l'errore. `bearing_type="SKT"` è l'UNICO
    verificato finora (cuscinetto radiale standard a gola profonda) — non usare altri valori anche
    se ti sembrano plausibili. Ritorna un cq.Workplane pronto per `result`.
    Per RONDELLE è disponibile `build_washer(size, fastener_type="iso7089")`, GIÀ IMPORTATA — `size`
    è solo "M<diametro>" (es. "M6", SENZA passo — le rondelle non sono filettate).
    `fastener_type="iso7089"` è l'UNICO verificato finora (rondella piana standard, forma A).
    VIETATO costruire una rondella a mano con `.circle().circle().extrude()` quando la funzione è
    disponibile — usala sempre.
11. Per FORME ORGANICHE/CURVE LIBERE (vasi, bottiglie, profili non rettilinei) usa `.spline()`
    seguito da `.loft()` -- MA con due regole precise, verificate empiricamente (violarle causa
    errori o, peggio, geometria SBAGLIATA senza alcun errore visibile):
    a) `.workplane(offset=N)` è CUMULATIVO rispetto al piano corrente, NON assoluto dalla base.
       Per piani a z=30 e z=60 dalla base, il secondo offset deve essere `.workplane(offset=30)`
       (30 in più rispetto al piano precedente), NON `.workplane(offset=60)` (che finirebbe a
       z=90). Calcola sempre la DIFFERENZA rispetto al piano precedente, mai la quota assoluta.
    b) Dopo `.spline(punti)`, aggiungi SEMPRE `.close()` esplicito -- NON usare `periodic=True`
       da solo. Verificato: `periodic=True` da solo crea una curva chiusa geometricamente ma
       NON la registra come "wire" per il loft, che poi o fallisce ("No pending wires present")
       o peggio, se mescolato con altri profili tipo cerchio, ignora SILENZIOSAMENTE il profilo
       spline e collega gli altri profili direttamente tra loro (verificato: un loft
       cerchio→spline→cerchio con periodic=True dava una geometria valida ma matematicamente
       identica a un loft cerchio→cerchio DIRETTO, come se la spline non esistesse -- nessun
       errore, solo la forma sbagliata). Con `.spline(punti).close()` il profilo viene
       registrato correttamente e usato dal loft.
    Se `.loft()` fallisce comunque, NON tentare varianti a caso -- indica nella risposta che
    la geometria richiesta non è riuscita a essere generata con curve libere.
12. Per VASI/CONTENITORI CAVI (svuotare un solido pieno lasciando una parete di spessore
    scelto) usa `.faces(">Z").shell(-spessore_mm)` DOPO aver costruito il solido pieno (loft
    incluso) -- `.faces(">Z")` seleziona la faccia superiore da rimuovere (l'apertura),
    `shell()` con valore NEGATIVO scava verso l'interno lasciando quello spessore di parete.
    Verificato funzionante anche su solidi da loft con profili spline (non solo su forme
    semplici come box/cilindri). NOTA IMPORTANTE: lo spessore reale della parete si assottiglia
    naturalmente ad avvicinarsi al bordo dell'apertura (dove parete esterna e interna si
    incontrano) -- è una caratteristica geometrica normale di qualsiasi contenitore con bordo
    tagliato, NON un difetto da correggere o evitare.
    SE IL SOLIDO PIENO VIENE POI SVUOTATO CON `.shell()`, il `.loft()` DEVE usare
    `ruled=False` -- MAI `ruled=True`. Verificato empiricamente, isolato con certezza: con gli
    STESSI identici profili e spessore, `ruled=True` fa fallire SEMPRE `.shell()`
    (`BRep_API: command not done`), `ruled=False` funziona SEMPRE. Motivo: `ruled=True` crea
    una superficie a segmenti dritti con spigoli vivi tra un profilo e l'altro, e scavare uno
    spigolo vivo con spessore costante genera quasi sempre autointersezioni. Il parametro
    `kind` di `.shell()` (es. `kind="arc"`) NON ha alcun effetto su questo fallimento -- non è
    la causa, non provare a "risolvere" cambiando quello. Se serve un solido pieno (senza
    successivo shell), `ruled=True` o `ruled=False` vanno bene entrambi.
    LIMITE NOTO NON RISOLTO (dichiaralo esplicitamente nella risposta se richiesto, non
    fingere che funzioni): su un profilo che si RESTRINGE FORTEMENTE verso l'apertura (es.
    da raggio 25mm a metà altezza a raggio 12mm in cima, oltre il 50% di restringimento),
    `.faces(">Z").shell()` PUÒ produrre un guscio apparentemente cavo ma in realtà COMPLETAMENTE
    SIGILLATO (nessuna vera apertura), senza sollevare alcun errore -- verificato con certezza
    (0 bordi aperti trovati nella mesh esportata). Tentativi di rimediare con un taglio
    successivo (`.cutBlind()`, anche con profondità generosa, o `.cutThruAll()`) NON hanno
    risolto il problema in modo affidabile durante la verifica -- causa non identificata con
    certezza. Per profili con restringimento forte verso l'apertura, NON promettere che il
    risultato sarà un contenitore realmente apribile -- genera comunque il pezzo ma segnala
    nella risposta che l'apertura andrebbe verificata visivamente prima di considerarla
    affidabile. Su profili con restringimento MODERATO (es. cilindro dritto, o rastremazione
    lieve) la tecnica resta verificata e affidabile.

ESEMPIO 4 (Ingranaggio modulo 2, 20 denti, spessore 8mm, foro 10mm):
```python
import cadquery as cq

result = build_spur_gear(module=2.0, teeth=20, thickness=8.0, bore_diameter=10.0)
```

ESEMPIO 5 (Vite esagonale M6, lunghezza 25mm):
```python
import cadquery as cq

result = build_hex_head_screw(size="M6-1", length_mm=25.0, fastener_type="iso4017")
```

ESEMPIO 5b (Cuscinetto 608, il più comune -- foro 8mm, esterno 22mm, largo 7mm):
```python
import cadquery as cq

result = build_bearing(size="M8-22-7", bearing_type="SKT")
```

ESEMPIO 6 (Vaso organico: profilo a 8 punti, base r=20, centro r=25, cima r=12, altezza 60mm):
```python
import cadquery as cq
import math

def profilo_stella(raggio, n_punti=8):
    return [
        (raggio * math.cos(2 * math.pi * i / n_punti), raggio * math.sin(2 * math.pi * i / n_punti))
        for i in range(n_punti)
    ]

result = (
    cq.Workplane("XY")
    .spline(profilo_stella(20)).close()
    .workplane(offset=30)          # +30 dal piano base (z=0 -> z=30)
    .spline(profilo_stella(25)).close()
    .workplane(offset=30)          # +30 dal piano PRECEDENTE (z=30 -> z=60, non z=90!)
    .spline(profilo_stella(12)).close()
    .loft(ruled=False)
)
```

ESEMPIO 7 (Stesso vaso, ma CAVO con apertura in cima, parete 2mm):
```python
import cadquery as cq
import math

def profilo_stella(raggio, n_punti=8):
    return [
        (raggio * math.cos(2 * math.pi * i / n_punti), raggio * math.sin(2 * math.pi * i / n_punti))
        for i in range(n_punti)
    ]

vaso_pieno = (
    cq.Workplane("XY")
    .spline(profilo_stella(20)).close()
    .workplane(offset=30)
    .spline(profilo_stella(25)).close()
    .workplane(offset=30)
    .spline(profilo_stella(12)).close()
    .loft(ruled=False)  # ruled=False OBBLIGATORIO qui: con ruled=True lo shell() sotto fallisce sempre
)

result = vaso_pieno.faces(">Z").shell(-2.0)
```

ESEMPIO 1 (Flangia forata con 4 fori):
```python
import cadquery as cq

outer_diameter = 100.0
inner_diameter = 40.0
thickness = 10.0
bolt_circle_diameter = 75.0
bolt_hole_diameter = 8.5
num_holes = 4

result = (
    cq.Workplane("XY")
    .circle(outer_diameter / 2)
    .circle(inner_diameter / 2)
    .extrude(thickness)
    .faces(">Z")
    .workplane()
    .polarArray(bolt_circle_diameter / 2, 0, 360, num_holes)
    .hole(bolt_hole_diameter)
)
```

ESEMPIO 2 (Staffa a L con foro su ciascuna piastra — pattern corretto multi-solido):
```python
import cadquery as cq

plate_length = 60.0
plate_width = 30.0
thickness = 5.0
hole_diameter = 8.0

# Piastra orizzontale: rettangolo pieno, foro inciso col proprio centro locale
horizontal_plate = (
    cq.Workplane("XY")
    .box(plate_length, plate_width, thickness, centered=(False, False, False))
    .faces(">Z")
    .workplane()
    .center(plate_length / 2, plate_width / 2)
    .hole(hole_diameter)
)

# Piastra verticale: stesso principio, poi ruotata e traslata per unirsi a 90 gradi
vertical_plate = (
    cq.Workplane("XY")
    .box(plate_length, plate_width, thickness, centered=(False, False, False))
    .faces(">Z")
    .workplane()
    .center(plate_length / 2, plate_width / 2)
    .hole(hole_diameter)
    .rotate((0, 0, 0), (0, 1, 0), 90)
    .translate((0, 0, thickness))
)

result = horizontal_plate.union(vertical_plate)
```

ESEMPIO 3 (Icosaedro con diametro circoscritto 40mm — usa la funzione pronta, non trigonometria a mano):
```python
import cadquery as cq

result = build_platonic_solid("icosaedro", diameter_mm=40.0)
```
"""


def _call_gemini(contents: list, system_instruction: str = None) -> str:
    """
    Chiamata Gemini grezza: manda la history dei turni ('contents' nel formato
    dell'API), ritorna il testo della risposta. Non fa parsing del codice --
    quello resta responsabilità del chiamante, perché il chiamante sa se sta
    gestendo il primo turno o un turno di correzione.

    system_instruction: override opzionale del system prompt di default
    (SYSTEM_PROMPT, specializzato per generare CadQuery). Usato da
    vision_pipeline.py per lo step di visione, che ha un compito diverso
    (descrivere, non generare codice) e quindi un system prompt diverso --
    ma la meccanica della chiamata HTTP è identica, niente da duplicare.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY non impostata in .env")

    payload = {
        "system_instruction": {"parts": [{"text": system_instruction or SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.1},
    }

    with httpx.Client(timeout=60.0) as http_client:
        resp = http_client.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=payload,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Errore API Gemini ({resp.status_code}): {resp.text}"
        )

    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Risposta Gemini in formato inatteso: {data}") from e


def _call_groq(messages: list) -> str:
    """
    Chiamata Groq grezza (API compatibile OpenAI, endpoint chat/completions
    standard) -- alternativa VELOCE a Gemini, SOLO per generazione codice
    (niente visione). NON verificata da qui, vedi nota sul default di
    GROQ_MODEL in testa al modulo -- se la risposta arriva in un formato
    diverso da quello atteso, questa funzione lo segnala esplicitamente
    nell'errore invece di fallire in modo silenzioso o inventare un
    parsing che sembra funzionare.

    messages: già nel formato nativo OpenAI (lista di {"role", "content"}),
    a differenza di _call_gemini che riceve 'contents' nel formato Gemini
    -- i due formati sono tradotti a monte da chi costruisce la
    conversazione (vedi generate_cad_code_with_repair), non qui.
    """
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY non impostata in .env")

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.1,
    }

    with httpx.Client(timeout=60.0) as http_client:
        resp = http_client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json=payload,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Errore API Groq ({resp.status_code}): {resp.text}")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Risposta Groq in formato inatteso: {data}") from e


def _neutral_to_gemini_contents(neutral: list) -> list:
    """Converte la conversazione neutra ('user'/'assistant', testo) nel
    formato Gemini ('user'/'model', 'parts')."""
    role_map = {"user": "user", "assistant": "model"}
    return [{"role": role_map[r], "parts": [{"text": t}]} for r, t in neutral]


def _neutral_to_groq_messages(neutral: list, system_instruction: str = None) -> list:
    """Converte la conversazione neutra nel formato OpenAI/Groq, con il
    system prompt come primo messaggio (Gemini lo tratta separatamente
    via system_instruction, Groq lo vuole dentro la lista messages)."""
    messages = [{"role": "system", "content": system_instruction or SYSTEM_PROMPT}]
    for r, t in neutral:
        messages.append({"role": r, "content": t})
    return messages


def _call_llm(neutral_conversation: list, provider: str = "gemini", system_instruction: str = None) -> str:
    """
    Punto di ingresso unico per la generazione/riparazione codice, che
    instrada verso il motore scelto mantenendo la stessa logica di
    conversazione (storia di tentativi ed errori) indipendentemente dal
    provider -- solo la traduzione nel formato nativo dell'API cambia.

    system_instruction: se omesso (None), usa SYSTEM_PROMPT di default --
    comportamento identico a prima di questa modifica. Se fornito (es. con
    un esempio recuperato dinamicamente, vedi example_library.py),
    sostituisce interamente il system prompt per QUESTA chiamata.
    """
    if provider == "groq":
        messages = _neutral_to_groq_messages(neutral_conversation, system_instruction)
        return _call_groq(messages)
    contents = _neutral_to_gemini_contents(neutral_conversation)
    return _call_gemini(contents, system_instruction=system_instruction)


def _extract_code(response_text: str) -> str:
    """Estrae il blocco ```python ... ``` dalla risposta, con fallback se il formato non è rispettato."""
    match = re.search(r"```python\n(.*?)\n```", response_text, re.DOTALL)
    if match:
        return match.group(1).strip()

    code_lines = [line for line in response_text.split("\n") if not line.strip().startswith("```")]
    return "\n".join(code_lines).strip()


# Parole che, insieme a un riferimento al filetto, indicano che l'utente
# vuole vederlo/misurarlo per davvero -- vedi _force_real_thread_if_requested.
_REAL_THREAD_TRIGGER_WORDS = ["reale", "vero", "vera", "visibile", "dettagliat", "non semplificat", "spirale"]


def _should_force_real_thread(prompt: str) -> bool:
    """
    Controllo DETERMINISTICO di riserva, non solo un'istruzione nel system
    prompt: verificato empiricamente che il modello a volte scrive
    real_thread=False anche quando l'utente chiede esplicitamente un
    filetto "reale visibile" -- una frase che combacia parola per parola
    con un esempio dato nel system prompt, eppure ignorata. Non ci si può
    fidare al 100% che il modello segua sempre un'istruzione condizionale,
    quindi qui verifichiamo il prompt originale direttamente (stesso
    principio usato in tutto il progetto: dato verificabile invece di
    fiducia nel giudizio del modello).
    """
    prompt_lower = prompt.lower()
    ha_filetto = "filett" in prompt_lower  # copre filetto/filettatura/filettato/filettata
    ha_trigger = any(w in prompt_lower for w in _REAL_THREAD_TRIGGER_WORDS)
    return ha_filetto and ha_trigger


def _force_real_thread_if_requested(code: str, prompt: str) -> str:
    """
    Se il prompt originale chiede esplicitamente un filetto reale (vedi
    _should_force_real_thread) ma il codice generato ha comunque scritto
    real_thread=False, lo corregge direttamente nel testo del codice
    prima dell'esecuzione -- non richiede un altro giro di generazione,
    e non dipende dal modello che ci riprovi correttamente.
    """
    if _should_force_real_thread(prompt):
        code = re.sub(r"real_thread\s*=\s*False", "real_thread=True", code)
    return code


def _fix_shell_incompatible_ruled_loft(code: str) -> str:
    """
    Se il codice usa sia 'ruled=True' che '.shell(' nello stesso script,
    forza ruled=False -- verificato empiricamente in una sessione
    precedente: '.shell()' su una superficie da loft con ruled=True
    fallisce SEMPRE (100% fallimento su tutti i casi testati), ruled=False
    funziona SEMPRE (100% successo). Correzione deterministica diretta sul
    testo prima dell'esecuzione, stesso principio già usato sopra per
    real_thread -- meglio correggere con certezza che sperare che il
    modello scriva ruled=False da solo.

    Approssimazione onesta, non nascosta: la sostituzione è globale nel
    file, non scoperta solo sulla specifica variabile che viene
    "shellata" -- se lo script avesse un secondo .loft() NON shellato con
    ruled=True intenzionale, verrebbe comunque forzato a ruled=False
    (cambia leggermente lo stile di interpolazione tra i profili, NON
    causa un fallimento). Dato quanto è ristretto l'uso di .loft()/.shell()
    nel nostro sistema (solo forme organiche, un solo loft per script
    nella grande maggioranza dei casi osservati), il rischio pratico di
    questo falso positivo è basso.
    """
    if "ruled=True" in code and ".shell(" in code:
        code = code.replace("ruled=True", "ruled=False")
    return code


# Parole che indicano una richiesta di dado/vite/bullone/rondella standard --
# vedi _prompt_requests_standard_fastener.
_FASTENER_PROMPT_WORDS = ["vite", "viti", "dado", "dadi", "bullone", "bulloni", "rondella", "rondelle"]


def _prompt_requests_standard_fastener(prompt: str) -> bool:
    """Rileva se il prompt chiede chiaramente un dado/vite/bullone/rondella standard."""
    prompt_lower = prompt.lower()
    return any(w in prompt_lower for w in _FASTENER_PROMPT_WORDS)


def _code_uses_fastener_functions(code: str) -> bool:
    """Verifica se il codice usa davvero le funzioni verificate per parti standard."""
    return (
        "build_hex_head_screw(" in code
        or "build_hex_nut(" in code
        or "build_washer(" in code
    )


_FASTENER_BYPASS_ERROR = (
    "ERRORE: hai scritto geometria esagonale/filetto A MANO invece di chiamare "
    "build_hex_head_screw()/build_hex_nut(), che sono GIÀ IMPORTATE e pronte all'uso. "
    "Ogni volta che qualcuno prova a ricostruire questa geometria a mano commette un errore "
    "dimensionale diverso (verificato su casi reali: testa esagonale 13.4% troppo piccola per "
    "aver confuso diametro circoscritto e larghezza tra i piatti; gambo posizionato con un vuoto "
    "di 12.5mm rispetto alla testa per un errore di traslazione; gambo completamente liscio senza "
    "alcuna filettatura nonostante fosse richiesta esplicitamente). NON RISCRIVERE la geometria. "
    "Sostituisci l'INTERO corpo del tuo script con SOLO questo pattern, adattando size/length_mm/"
    "real_thread ai valori richiesti nella conversazione originale:\n\n"
    "```python\n"
    "import cadquery as cq\n\n"
    "result = build_hex_head_screw(size=\"M<diametro>-<passo>\", length_mm=<lunghezza>, "
    "fastener_type=\"iso4017\", real_thread=<True o False>)\n"
    "```\n\n"
    "(oppure build_hex_nut(...) se la richiesta era per un dado, build_washer(size, "
    "fastener_type=\"iso7089\") se era per una rondella -- non una vite). "
    "Il costo in tempo di real_thread=True (~3 secondi) è già previsto dal sistema "
    "(timeout 120 secondi) -- non è un motivo per evitare la funzione."
)


def _summarize_error(error_msg: str, max_chars: int = 600) -> str:
    """
    I traceback di CadQuery/OCCT sono spesso enormi e pieni di rumore interno
    (frame di libreria C++, path assoluti). Mandiamo al modello solo la parte
    utile: la prima riga (di solito 'TipoErrore: messaggio') più eventuali
    righe successive fino al limite di caratteri, non l'intero traceback.
    """
    lines = error_msg.strip().split("\n")
    summary = lines[0]
    for line in lines[1:]:
        if len(summary) + len(line) > max_chars:
            break
        summary += "\n" + line
    return summary


def generate_cad_code(prompt: str) -> str:
    """
    Genera codice CadQuery per un prompt, senza retry.
    Usata per compatibilità/test unitari puntuali. Il flusso con auto-repair
    è generate_cad_code_with_repair(), usata dall'endpoint.
    """
    contents = [{"role": "user", "parts": [{"text": f"Crea il modello CAD per: {prompt}"}]}]
    response_text = _call_gemini(contents)
    return _extract_code(response_text)


def generate_cad_code_with_repair(prompt: str, execute_fn, max_attempts: int = 3, provider: str = "gemini") -> dict:
    """
    Genera codice CadQuery con retry automatico sugli errori di COMPILAZIONE
    (non sui warning geometrici del rilevatore statico -- quelli sono euristiche,
    non certezze, e ritentarli alla cieca rischia di peggiorare casi corretti).

    execute_fn: callable che prende il codice generato e ritorna il dict di
    execute_and_export() (con "success"/"error"). Iniettato dal chiamante
    (main.py) invece di importato qui, per non creare una dipendenza
    circolare tra llm_pipeline e cad_engine e per restare testabile in isolamento.

    provider: "gemini" (default, motore verificato in tutta questa sessione),
    "groq" (alternativa veloce, NON verificata da qui -- vedi nota su
    GROQ_MODEL in testa al modulo), o "hybrid" (Gemini per il PRIMO
    tentativo -- dove capire bene la richiesta conta di più -- e Groq per
    i turni di CORREZIONE successivi, dove l'errore da correggere è già
    molto specifico e conta di più la velocità di iterazione; servono
    entrambe le chiavi API impostate). Con provider="gemini" o "groq" il
    comportamento è identico bit-per-bit a prima di questa modifica.

    Prima di chiamare il modello, prova il percorso DETERMINISTICO per
    richieste di parti standard semplici (vedi try_deterministic_fastener_code)
    -- se riesce, salta del tutto la generazione via modello (attempts=0,
    deterministic=True nel risultato), eliminando alla radice il rischio
    di aggiramento delle funzioni verificate. Se il percorso deterministico
    non si applica (prompt non riconosciuto con certezza, o più complesso
    di una richiesta pura) o fallisce inaspettatamente, procede con la
    generazione via modello esattamente come prima.

    Ritorna un dict con: code, exec_result, attempts, repaired (bool),
    deterministic (bool, presente solo se il percorso deterministico ha avuto successo).
    """
    deterministic_code = try_deterministic_fastener_code(prompt)
    if deterministic_code is not None:
        exec_result = execute_fn(deterministic_code)
        if exec_result["success"]:
            return {
                "code": deterministic_code,
                "exec_result": exec_result,
                "attempts": 0,
                "repaired": False,
                "deterministic": True,
            }
        # Parametri estratti correttamente ma l'esecuzione ha comunque
        # fallito per qualche altro motivo (raro) -- non arrenderti subito,
        # dai comunque una possibilità al modello invece di fallire qui.

    # Recupero dinamico: se una generazione VERIFICATA precedente è
    # strutturalmente simile a questa richiesta (vedi example_library.py),
    # la aggiungiamo al system prompt SOLO per questa chiamata -- integra
    # gli esempi fissi con uno pertinente al caso specifico, invece di
    # affidarsi sempre agli stessi 7 esempi indipendentemente dalla
    # richiesta. Se non c'è nulla di abbastanza simile (soglia di
    # somiglianza), instruction resta il system prompt di sempre --
    # nessun rischio di iniettare un esempio fuorviante.
    dynamic_instruction = SYSTEM_PROMPT
    esempio_simile = find_similar_example(prompt)
    if esempio_simile:
        dynamic_instruction = SYSTEM_PROMPT + format_example_addendum(esempio_simile)

    # Log diagnostico temporaneo: conferma diretta (nei log del container,
    # visibili nel terminale dove gira "docker run") se il recupero
    # dinamico ha trovato un esempio e con quale punteggio reale --
    # verifica più diretta della semplice somiglianza strutturale del
    # codice generato.
    _migliore_diag, _punteggio_diag = _find_best_match(prompt)
    if esempio_simile:
        print(
            f"[recupero esempi] TROVATO (punteggio {_punteggio_diag:.3f} >= soglia 0.35): "
            f"\"{esempio_simile.get('prompt', '')[:70]}\""
        )
    else:
        print(
            f"[recupero esempi] nessun esempio sopra soglia "
            f"(miglior punteggio: {_punteggio_diag:.3f})"
        )

    conversation = [("user", f"Crea il modello CAD per: {prompt}")]

    last_code = None
    last_exec_result = None

    for attempt in range(1, max_attempts + 1):
        if provider == "hybrid":
            effective_provider = "gemini" if attempt == 1 else "groq"
        else:
            effective_provider = provider

        response_text = _call_llm(conversation, provider=effective_provider, system_instruction=dynamic_instruction)
        code = _extract_code(response_text)
        code = _force_real_thread_if_requested(code, prompt)
        code = _fix_shell_incompatible_ruled_loft(code)
        exec_result = execute_fn(code)

        last_code = code
        last_exec_result = exec_result

        # Esecuzione riuscita non basta per una richiesta di parte standard:
        # il codice potrebbe aver "eseguito bene" una geometria esagonale
        # scritta a mano invece di build_hex_head_screw()/build_hex_nut(),
        # producendo dimensioni SBAGLIATE (verificato: 13.4% troppo piccola
        # in un caso reale, .polygon() usa il diametro circoscritto, non la
        # larghezza tra i piatti) -- un errore che l'esecuzione da sola non
        # rileva, quindi lo controlliamo esplicitamente qui.
        fastener_bypassed = (
            exec_result["success"]
            and _prompt_requests_standard_fastener(prompt)
            and not _code_uses_fastener_functions(code)
        )

        if exec_result["success"] and not fastener_bypassed:
            return {
                "code": code,
                "exec_result": exec_result,
                "attempts": attempt,
                "repaired": attempt > 1,
            }

        if attempt == max_attempts:
            if fastener_bypassed:
                # Non accettare silenziosamente un pezzo con dimensioni
                # sbagliate solo perché "esegue senza errori" -- l'esecuzione
                # riuscita non significa geometria corretta quando la
                # funzione verificata è stata aggirata. Un fallimento
                # esplicito, con una spiegazione chiara, è più onesto di un
                # pezzo con dimensioni sbagliate consegnato come se fosse
                # riuscito (verificato: succede davvero, non un caso limite
                # teorico -- tre bug dimensionali diversi osservati in tre
                # tentativi separati di aggiramento).
                last_exec_result = {
                    "success": False,
                    "error": (
                        "Impossibile generare la parte standard richiesta con dimensioni "
                        "affidabili: il modello ha ripetutamente scritto la geometria a mano "
                        "invece di usare build_hex_head_screw()/build_hex_nut() (già disponibili "
                        "e pronte all'uso), anche dopo essere stato corretto esplicitamente. "
                        "Riprova la stessa richiesta (Gemini non è deterministico, un nuovo "
                        "tentativo può riuscire), o semplifica la richiesta."
                    ),
                }
            break  # esaurito il budget di tentativi, usciamo con l'ultimo risultato disponibile

        if fastener_bypassed:
            conversation.append(("assistant", response_text))
            conversation.append(("user", _FASTENER_BYPASS_ERROR))
            continue

        # Prepara il prossimo turno: il modello vede il proprio codice fallito
        # e l'errore esatto, come farebbe un collega che gli segnala un bug.
        error_summary = _summarize_error(exec_result["error"])
        conversation.append(("assistant", response_text))
        conversation.append((
            "user",
            f"Il codice ha generato questo errore durante l'esecuzione:\n\n"
            f"{error_summary}\n\n"
            "Correggi SOLO il problema che ha causato questo errore specifico. "
            "Mantieni invariato il resto della logica. Rispondi di nuovo con "
            "l'intero script corretto nello stesso formato ```python ... ```."
        ))

    return {
        "code": last_code,
        "exec_result": last_exec_result,
        "attempts": max_attempts,
        "repaired": False,
    }


# ============================================================================
# Percorso DETERMINISTICO per richieste di parti standard semplici (vite/
# dado/cuscinetto) -- elimina alla radice il rischio di aggiramento (vedi
# _FASTENER_BYPASS_ERROR sopra): se riusciamo a estrarre i parametri con
# ALTA CONFIDENZA dal prompt, costruiamo la chiamata direttamente noi,
# SENZA passare dal modello.
#
# Principio guida, applicato ovunque in questo modulo: RINUNCIARE (fallback
# alla generazione normale via modello) è sempre preferibile a INDOVINARE.
# In particolare, rinunciamo anche se il prompt sembra contenere qualcosa
# OLTRE a una richiesta di parte standard pura (es. "vite M6 lunga 25mm con
# una testa a forma di stella") -- un'estrazione troppo aggressiva
# rischierebbe di ignorare silenziosamente la personalizzazione richiesta,
# un errore deterministico e silenzioso, peggiore di quello che stiamo
# risolvendo.
# ============================================================================

_SIZE_RE = re.compile(r"\bM\s*(\d+(?:[.,]\d+)?)(?:[-x](\d+(?:[.,]\d+)?))?\b", re.IGNORECASE)
_LENGTH_RE = re.compile(r"lung\w*[^0-9]{0,20}?(\d+(?:[.,]\d+)?)\s*mm", re.IGNORECASE)

# Passi standard ISO più comuni per diametro -- dato di riferimento
# ingegneristico noto, non stimato (già usato altrove nel system prompt
# senza obiezioni). Se il diametro richiesto non è tra questi, rinuncia
# al percorso deterministico piuttosto che indovinare un passo.
_STANDARD_PITCHES = {
    "2": "0.4", "2.5": "0.45", "3": "0.5", "3.5": "0.6", "4": "0.7",
    "5": "0.8", "6": "1", "8": "1.25", "10": "1.5", "12": "1.75",
}

# Solo taglie di cuscinetto VERIFICATE con numeri concreti in questa
# sessione (vedi fasteners.py) -- non aggiungere altre taglie standard
# "note a memoria" senza prima verificarle allo stesso modo (bounding
# box/volume contro un limite fisico calcolato indipendentemente).
_VERIFIED_BEARING_SIZES = {"608": "M8-22-7"}

# Parole la cui presenza indica quasi certamente una richiesta più
# complessa di una semplice parte standard -- se una di queste compare,
# rinuncia al percorso deterministico anche se size/length si estraggono
# correttamente, per non rischiare di ignorare una personalizzazione.
_CUSTOM_FEATURE_WORDS = [
    "stella", "personalizzat", "custom", "speciale", "diverso", "smusso",
    "smussat", "foro aggiuntivo", "inciso", "incisione", "logo", "scritta",
    "esagono modificato", "testa a", "forma di", "con un ", "e anche",
]

# Limite di lunghezza del prompt oltre il quale rinunciamo comunque --
# una richiesta di parte standard pura è tipicamente breve; un prompt
# lungo suggerisce dettagli aggiuntivi che l'estrazione a parole chiave
# potrebbe non cogliere.
_MAX_PROMPT_LEN_FOR_DETERMINISTIC = 140


def _prompt_seems_pure_fastener_request(prompt: str) -> bool:
    """Controllo di sicurezza aggiuntivo: rinuncia se il prompt sembra
    contenere altro oltre a una richiesta di parte standard pura."""
    if len(prompt) > _MAX_PROMPT_LEN_FOR_DETERMINISTIC:
        return False
    prompt_lower = prompt.lower()
    return not any(w in prompt_lower for w in _CUSTOM_FEATURE_WORDS)


def _extract_thread_size(prompt: str):
    """Estrae 'M<diametro>-<passo>'. Ritorna None se il passo non è
    esplicito e il diametro non è tra quelli noti (mai indovinare)."""
    match = _SIZE_RE.search(prompt)
    if not match:
        return None
    diametro = match.group(1).replace(",", ".")
    passo_esplicito = match.group(2)
    if passo_esplicito:
        return f"M{diametro}-{passo_esplicito.replace(',', '.')}"
    passo_default = _STANDARD_PITCHES.get(diametro)
    if passo_default is None:
        return None
    return f"M{diametro}-{passo_default}"


def _extract_length_mm(prompt: str):
    """Estrae la lunghezza in mm vicino a una parola come 'lunghezza'/'lungo'."""
    match = _LENGTH_RE.search(prompt)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def try_deterministic_fastener_code(prompt: str):
    """
    Prova a costruire il codice per una richiesta di vite/dado/cuscinetto/
    rondella SEMPLICE, senza passare dal modello. Ritorna il codice pronto
    se tutti i parametri necessari sono estratti con alta confidenza,
    altrimenti None -- il chiamante procede con la generazione normale
    via modello.
    """
    if not _prompt_seems_pure_fastener_request(prompt):
        return None

    prompt_lower = prompt.lower()

    if "cuscinetto" in prompt_lower or "cuscinetti" in prompt_lower:
        for numero, size in _VERIFIED_BEARING_SIZES.items():
            if numero in prompt_lower:
                return (
                    "import cadquery as cq\n\n"
                    f'result = build_bearing(size="{size}", bearing_type="SKT")\n'
                )
        return None  # taglia non tra quelle verificate -- fallback al modello

    if "rondella" in prompt_lower or "rondelle" in prompt_lower:
        # Le rondelle non sono filettate -- solo "M<diametro>", niente passo.
        # A differenza dei cuscinetti (una sola taglia verificata a mano),
        # qui abbiamo verificato sia i dati ufficiali sia il METODO di
        # costruzione (vedi build_washer in fasteners.py) -- generalizza a
        # qualsiasi taglia della stessa tabella, non solo M6. Se la taglia
        # non esiste, build_washer solleva un errore chiaro invece di
        # produrre geometria silenziosamente sbagliata.
        size_match = _SIZE_RE.search(prompt)
        if not size_match:
            return None
        diametro = size_match.group(1).replace(",", ".")
        return (
            "import cadquery as cq\n\n"
            f'result = build_washer(size="M{diametro}", fastener_type="iso7089")\n'
        )

    is_screw = any(w in prompt_lower for w in ["vite", "viti", "bullone", "bulloni"])
    is_nut = any(w in prompt_lower for w in ["dado", "dadi"])
    if not is_screw and not is_nut:
        return None

    size = _extract_thread_size(prompt)
    if size is None:
        return None

    real_thread = _should_force_real_thread(prompt)

    if is_screw:
        length_mm = _extract_length_mm(prompt)
        if length_mm is None:
            return None  # niente lunghezza affidabile -- fallback al modello
        return (
            "import cadquery as cq\n\n"
            f'result = build_hex_head_screw(size="{size}", length_mm={length_mm}, '
            f'fastener_type="iso4017", real_thread={real_thread})\n'
        )

    return (
        "import cadquery as cq\n\n"
        f'result = build_hex_nut(size="{size}", fastener_type="iso4032", real_thread={real_thread})\n'
    )
