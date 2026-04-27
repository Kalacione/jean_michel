# Plan d'intervention — 27 avril 2026

## Causes racines identifiées

### 1. CLI coupe sur chaque newline (cause de TOUS les problèmes du test couscous)
`rich.Prompt.ask()` soumet à chaque `Enter`. Un texte collé avec des newlines génère une
conversation par paragraphe. La session couscous a créé 3 conversations distinctes au lieu d'une,
chacune recevant un fragment de texte hors contexte.

**Impact en cascade :**
- jean-michel reçoit un fragment de texte sans instruction → invente une action
- Les dossiers `42658d2ce9b8` et `3585028f2eba` sont des "chiures" automatiques
- Tous les problèmes #1-6 du rapport découlent de ce point

**Fix : A — CLI saisie multi-ligne**

---

### 2. jean-michel invente des agents inexistants (`cultural_history_specialist`)
Le system prompt ne lui communique pas la liste des spécialistes disponibles.
Il hallucine des noms d'agents (paradigme `mark_unverifiable` non appliqué à lui-même).

**DB confirme :** seuls `jean-michel`, `summarizer`, `synthesizer` existent.

**Fix : B — Injecter liste des agents dans le system prompt de jean-michel**

---

### 3. Le "Proceed." de l'orchestrateur est pris pour une instruction humaine
Quand `delegate_to` échoue (agent inconnu), l'erreur est injectée dans `running_user_text` :
```
Tool responses (in the order of your calls):
[delegate_to:cultural_history_specialist] [error] unknown agent: cultural_history_specialist

Proceed.
```
Le LLM interprète "Proceed." comme l'humain qui dit de continuer, pas comme l'orchestrateur.
Au tour suivant, son `Inbound briefing` contient `{inbound_text}` (la requête originale) mais
son `user message` dit "Proceed." — confusion maximale.

**Fix : C — Wording running_user_text explicitement système**

---

### 4. Statut DB "running" si exception non catchée pendant `_run_request`
La request reste en `running` si une exception non prévue traverse `_run_request`.
Le budget de steps épuisé est bien géré, mais une exception Python brute ne l'est pas.

**Fix : D — try/finally sur la boucle de steps**

---

### 5. Aucun script d'inspection des artefacts
Diagnostic manuel fastidieux (ls + cat). Un script standalone permettrait de voir rapidement
le déroulé d'une conversation donnée.

**Fix : E — Script `tools/inspect_conv.py`**

---

## Plan d'exécution

| ID  | Fichiers touchés                          | Priorité |
|-----|-------------------------------------------|----------|
| A   | `src/jeanmichel/cli.py`                   | P0       |
| B   | `src/jeanmichel/db.py`, `prompts.py`, `orchestrator.py` | P0 |
| C   | `src/jeanmichel/orchestrator.py`          | P1       |
| D   | `src/jeanmichel/orchestrator.py`          | P1       |
| E   | `tools/inspect_conv.py` (nouveau)         | P2       |

**Parallélisation :** A est indépendant (cli.py seul). B+C+D partagent orchestrator.py → séquentiel.
E est indépendant.

---

## Détail des fixes

### A — CLI saisie multi-ligne
Remplacer `rich.Prompt.ask()` par `prompt_toolkit.prompt()` avec `multiline=True`.
- `Enter` = nouvelle ligne
- `Meta+Enter` (Alt+Enter) = soumettre
- Afficher le hint dans le splash

### B — Injecter liste des agents dans le system prompt
- Ajouter `list_active_agents()` dans `db.py`
- Ajouter champ `available_agents` dans `PromptContext` (prompts.py)
- Ajouter section `## Available specialists` dans `render_system_prompt` (prompts.py)
- Passer la liste depuis `_run_request` (orchestrator.py)
- Injecter uniquement pour jean-michel (role=router) ou pour tous (choix : tous,
  un sous-agent peut aussi avoir besoin de re-déléguer)

### C — Wording running_user_text
Remplacer :
```python
"Tool responses (in the order of your calls):\n" + ...\n\nProceed."
```
Par :
```python
"[ORCHESTRATOR] Tool results below. Resume execution of your current task.\n\n" + ...
```

### D — Statut DB garanti
Ajouter `try/finally` autour de la boucle `for step in range(max_steps)` :
```python
try:
    for step in range(max_steps):
        ...
except Exception:
    with db.connect() as conn:
        db.update_request_status(conn, req_id, "failed", completed=True)
    raise
```

### E — Script d'inspection
Script `tools/inspect_conv.py` :
- Arg : `conversation_id` (ou préfixe)
- Liste les artefacts dans l'ordre chronologique
- Affiche le contenu de chaque fichier avec header coloré
- Options : `--agent`, `--kind` pour filtrer
