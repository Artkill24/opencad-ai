"""
Analisi statica del codice CadQuery generato, basata su AST invece che su
posizione testuale grezza.

Perché AST e non regex sulla stringa:
- Traccia la storia REALE di ogni variabile (incluse le riassegnazioni tipo
  `result = result.faces(...).hole(...)`), non solo "quale sottostringa
  appare prima nel file".
- Non confonde due variabili indipendenti che compaiono nello stesso ordine
  per puro caso (falso positivo del vecchio approccio testuale).
- Ignora automaticamente commenti e stringhe, che un regex sul testo grezzo
  potrebbe leggere per errore.

Il modulo espone `detect_static_warnings(code_str) -> list[str]`, usata da
main.py esattamente come prima -- l'interfaccia esterna non cambia, cambia
solo l'affidabilità interna dell'analisi.
"""

import ast

# Operazioni che rimuovono materiale basandosi sulla selezione di una faccia
# (.faces() + .workplane() + .center()/.hole()/.cut()/.fillet()/.chamfer()).
# Se la variabile su cui operano ha già subito un .union() in precedenza,
# la faccia selezionata può appartenere al solido "sbagliato" del composto,
# o il suo bounding box locale può non coincidere col materiale reale --
# risultato: l'operazione va a vuoto senza sollevare alcun errore.
RISKY_AFTER_UNION = {"hole", "cut", "fillet", "chamfer"}


def _chain_calls_and_base(node):
    """
    Data un'espressione AST, ritorna (lista_metodi_chiamati_in_ordine, nome_variabile_base).

    Esempio: per `result.faces(">Z").workplane().hole(8)` ritorna
    (["faces", "workplane", "hole"], "result").

    nome_variabile_base è None se la catena non parte da un identificatore
    semplice tracciabile (es. parte da `cq.Workplane(...)`, un modulo).
    """
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            base_calls, base_name = _chain_calls_and_base(node.func.value)
            return base_calls + [node.func.attr], base_name
        # Chiamata diretta tipo qualcosa(), non un metodo (.attr) -- niente da tracciare
        return [], None

    if isinstance(node, ast.Name):
        return [], node.id

    if isinstance(node, ast.Attribute):
        # Es. cq.Workplane senza chiamata ancora -- risali comunque per coerenza
        base_calls, base_name = _chain_calls_and_base(node.value)
        return base_calls, base_name

    # Qualsiasi altro tipo di nodo (literal, subscript, BinOp, ecc.):
    # non c'è una variabile CadQuery tracciabile qui.
    return [], None


def _build_variable_histories(tree: ast.AST) -> dict:
    """
    Attraversa le assegnazioni di primo livello (`nome = espressione`) nel
    corpo dello script e costruisce, per ogni variabile, la sequenza
    cumulativa di metodi CadQuery chiamati nella sua storia -- inclusa
    l'eredità da riassegnazioni successive sulla stessa variabile.
    """
    histories = {}

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue  # salta tuple-assignment ed espressioni non semplici

        target_name = node.targets[0].id
        calls, base_name = _chain_calls_and_base(node.value)

        inherited = histories.get(base_name, []) if base_name else []
        histories[target_name] = inherited + calls

    return histories


def detect_static_warnings(code_str: str) -> list:
    """
    Controlli statici (nessuna esecuzione) su pattern noti per produrre
    fallimenti geometrici silenziosi in CadQuery. Non bloccano l'export,
    segnalano al chiamante che il risultato va verificato con attenzione
    (es. controllando volume/bounding box).
    """
    warnings = []

    try:
        tree = ast.parse(code_str)
    except SyntaxError:
        # A questo punto il codice è già stato eseguito con successo da
        # cad_engine (altrimenti non saremmo qui), quindi non dovrebbe
        # succedere -- ma non vogliamo far fallire l'endpoint per questo.
        return warnings

    histories = _build_variable_histories(tree)

    for var_name, calls in histories.items():
        if "union" not in calls:
            continue
        first_union_idx = calls.index("union")
        tail = calls[first_union_idx + 1:]

        risky_after = [op for op in tail if op in RISKY_AFTER_UNION]
        if not risky_after:
            continue

        # Il rischio reale (verificato empiricamente sul caso della staffa a L)
        # dipende da un offset esplicito via .center(x, y) applicato DOPO
        # .union() -- quell'offset assume che il centro del bounding box
        # della faccia selezionata coincida col materiale reale, il che è
        # falso su geometrie composte non simmetriche.
        # Senza .center() nel mezzo, l'operazione usa l'origine nativa della
        # faccia selezionata: per pezzi concentrici (es. un disco con mozzo
        # sullo stesso asse) questo è spesso corretto e NON va segnalato --
        # verificato su un caso reale (ingranaggio con mozzo, volume esatto
        # al mm^3 nonostante hole() dopo union()).
        if "center" not in tail:
            continue

        ops_list = ", ".join(f".{op}()" for op in dict.fromkeys(risky_after))
        warnings.append(
            f"Variabile '{var_name}': rilevate chiamate {ops_list} dopo .union() "
            "precedute da .center() con offset esplicito. Pattern noto per assumere "
            "erroneamente che il centro del bounding box della faccia selezionata "
            "coincida col materiale reale -- l'operazione può non tagliare nulla "
            "senza generare errori. Verifica volume/bounding box del risultato."
        )

    return warnings
