# Contribuire a OpenCAD-AI

Grazie per l'interesse. Prima di tutto, il principio che governa ogni scelta tecnica in questo progetto:

> **Verifica sempre con un numero concreto prima di fidarti — di una libreria esterna, di un modello AI, o di te stesso.**

In pratica: se aggiungi una nuova parte standard (es. una chiavetta, un profilato), non basta che il codice "sembri funzionare" — serve un confronto numerico contro una formula indipendente o una fonte dati ufficiale, nello stesso spirito di come sono state verificate le parti già presenti (vedi i commenti in `fasteners.py`, `gears.py`, `polyhedra.py`).

## Aree dove serve più aiuto

In ordine approssimativo di valore/impatto:

1. **Generatore INP per FEA** (CalculiX + Gmsh) — l'infrastruttura di base è verificata funzionante, manca il ponte da mesh 3D a file `.inp`. Il pezzo di lavoro singolo più prezioso rimasto aperto.
2. **Quotatura automatica nel DXF** — oggi il DXF ha solo il profilo geometrico (`dxf_export.py`), senza quote.
3. **Estensione del percorso deterministico** (`llm_pipeline.py`, `try_deterministic_fastener_code`) ad altre parti standard — ogni aggiunta segue lo stesso schema: estrarre parametri con alta confidenza dal prompt, rinunciare (fallback al modello) in caso di dubbio.
4. **Il bug noto di `.shell()` su profili da loft molto rastremati** (vedi commenti in `llm_pipeline.py` sulle regole spline/loft) — contenitori cavi a pareti sottili possono risultare sigillati.
5. **DFM per CNC/lamiera** — oggi il sistema copre solo la stampa 3D (FDM/Resina/SLS).
6. **Passare da Jaccard a embedding veri** per il recupero dinamico degli esempi (`example_library.py`) — il metodo attuale a parole chiave ha un limite noto e documentato nel codice.

## Come proporre una modifica

1. Apri una issue prima di un PR sostanzioso, per discutere l'approccio.
2. Se aggiungi geometria nuova (una parte standard, una primitiva), includi nella PR il calcolo di verifica indipendente che hai usato (formula a mano, confronto con una fonte ufficiale) — non solo il codice.
3. Se tocchi il system prompt (`llm_pipeline.py`), spiega quale comportamento reale hai osservato che giustifica la modifica — non aggiungere regole "per sicurezza" senza un caso concreto che le motivi.
4. Build e test locali via Docker (vedi README) prima di aprire la PR.

## Segnalare un bug

Se trovi un caso in cui il modello genera geometria sbagliata, la cosa più utile è: il prompt esatto usato, il codice generato, e (se puoi) il calcolo indipendente che dimostra l'errore — lo stesso standard di verifica usato in tutto il progetto.
