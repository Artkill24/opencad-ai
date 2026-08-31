"""
Libreria di esempi verificati per il recupero dinamico -- invece di un
system prompt sempre fisso (gli stessi esempi per ogni richiesta),
recupera l'esempio più simile strutturalmente alla richiesta corrente
tra le generazioni GIÀ VERIFICATE (eseguite con successo, senza avvisi
geometrici) e lo aggiunge al prompt per quella specifica generazione.

Approccio v1 deliberatamente semplice: sovrapposizione di parole chiave
(Jaccard su token, esclusi i numeri puri e le parole di collegamento
italiane -- così "90mm" vs "70mm" non penalizzano la somiglianza
STRUTTURALE tra due pezzi simili di taglie diverse, e "una"/"con"/"di"
non diluiscono il punteggio tra un prompt telegrafico e uno scritto per
esteso sullo stesso pezzo). Nessuna dipendenza nuova (niente
embedding/database vettoriale) -- se in futuro si rivelasse
insufficiente, è un punto di partenza sostituibile senza toccare il
resto della pipeline.
"""

import os
import re
import json

_LIBRARY_PATH = "outputs/example_library.json"
_MAX_ENTRIES = 200  # tetto per non far crescere il file all'infinito
# Abbassata da 0.35 a 0.30 insieme all'introduzione del filtro stopwords
# sotto -- verificato: un prompt telegrafico ("diametro 100mm, foro 35mm,
# 6 fori M6 su circonferenza 80mm") contro un esempio scritto per esteso
# ("Una flangia circolare, diametro 90mm... con foro centrale... e...
# fori... su una circonferenza di...") otteneva solo 0.273 di somiglianza
# SENZA filtro (troppe parole di collegamento diluivano il punteggio),
# 0.312 CON il filtro -- ancora sotto la vecchia soglia 0.35, ora sopra
# la nuova 0.30. Verificato anche che nessuno dei casi "diversi" testati
# in sessione (ingranaggio, cuscinetto, vite, rondella, blocco, albero)
# superi 0.30 per errore -- restano tutti sotto 0.11.
_MIN_SIMILARITY = 0.30

# Parole di collegamento italiane (articoli, preposizioni, congiunzioni)
# che riempiono il denominatore di Jaccard senza portare significato
# distintivo -- filtrate PRIMA del confronto, così un prompt telegrafico
# e uno scritto per esteso sullo stesso pezzo si assomigliano di più
# (verificato sopra), senza rendere il confronto meno selettivo su pezzi
# davvero diversi (stesso controllo di sicurezza verificato).
_STOPWORDS_IT = {
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "di", "a", "da",
    "in", "con", "su", "per", "tra", "fra", "e", "o", "che", "del", "della",
    "dei", "delle", "dello", "degli", "al", "allo", "alla", "ai", "agli",
    "alle", "sul", "sulla", "sui", "sugli", "sulle", "nel", "nella", "nei",
    "negli", "nelle",
}


def _tokenizza(testo: str) -> set:
    parole = re.findall(r"\w+", testo.lower())
    return set(p for p in parole if not p.isdigit() and p not in _STOPWORDS_IT)


def _somiglianza_jaccard(a: str, b: str) -> float:
    ta, tb = _tokenizza(a), _tokenizza(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _load_library() -> list:
    if not os.path.isfile(_LIBRARY_PATH):
        return []
    try:
        with open(_LIBRARY_PATH) as f:
            return json.load(f)
    except Exception:
        return []  # file corrotto o illeggibile -- non bloccare la generazione per questo


def add_verified_example(prompt: str, code: str, volume_mm3, bbox_mm: dict):
    """
    Aggiunge una generazione riuscita alla libreria -- va chiamata SOLO
    per generazioni senza avvisi geometrici (status == "success", non
    "geometria_sospetta"), per non insegnare al modello pattern di cui
    abbiamo motivo di dubitare. Un fallimento qui non deve mai bloccare
    la risposta già data all'utente (per questo non solleva eccezioni).
    """
    try:
        libreria = _load_library()
        libreria.append({
            "prompt": prompt,
            "code": code,
            "volume_mm3": volume_mm3,
            "bbox_mm": bbox_mm,
        })
        if len(libreria) > _MAX_ENTRIES:
            libreria = libreria[-_MAX_ENTRIES:]  # tieni le più recenti
        os.makedirs(os.path.dirname(_LIBRARY_PATH), exist_ok=True)
        with open(_LIBRARY_PATH, "w") as f:
            json.dump(libreria, f, indent=2)
    except Exception:
        pass


def find_similar_example(prompt: str):
    """
    Ritorna il dict dell'esempio più simile strutturalmente al prompt
    dato, o None se nessuno supera la soglia minima di somiglianza --
    meglio nessun esempio che uno fuorviante.
    """
    migliore, punteggio = _find_best_match(prompt)
    if punteggio >= _MIN_SIMILARITY:
        return migliore
    return None


def _find_best_match(prompt: str):
    """
    Come find_similar_example, ma ritorna SEMPRE il miglior candidato e
    il suo punteggio reale, anche sotto soglia -- usata per diagnostica
    (log del punteggio effettivo, non solo trovato/non trovato) senza
    duplicare la logica di ricerca in due posti.
    """
    libreria = _load_library()
    if not libreria:
        return None, 0.0
    migliore, punteggio_migliore = None, 0.0
    for esempio in libreria:
        s = _somiglianza_jaccard(prompt, esempio.get("prompt", ""))
        if s > punteggio_migliore:
            migliore, punteggio_migliore = esempio, s
    return migliore, punteggio_migliore


def format_example_addendum(esempio: dict) -> str:
    """Formatta l'esempio recuperato come testo da aggiungere al system prompt."""
    return (
        "\n\nESEMPIO RECENTE VERIFICATO (struttura simile alla richiesta attuale, "
        "generato con successo in precedenza -- prendi ispirazione dall'approccio, "
        "NON copiare le misure esatte, quelle vanno adattate alla richiesta corrente):\n"
        f"Richiesta originale: \"{esempio.get('prompt', '')}\"\n"
        f"```python\n{esempio.get('code', '')}\n```"
    )
