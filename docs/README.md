# Jean-Michel — base de connaissance (docs)

Index des **références vivantes** (tenues à jour). Les vieux plans/audits datés et reliques v1 ont été
dégagés — **l'historique git les conserve** (c'est là que vit le *pourquoi* d'une décision). Le dossier
`DevNotes/` peut compléter (réflexion de fond) s'il est présent localement.

> Backlog en cours : [../todo.md](../todo.md). Entrée produit / setup : [../README.md](../README.md).

## État courant (snapshot 2026-06-17)
- **Référent organisationnel** : `state.json` est LE ledger autoritaire de la conversation (tours, plans,
  todos, subagents, fichiers, `phase`, `plan_mode`) ; filet `rebuild_from_events` (« maintenu == reconstruit »),
  poussé live à l'UI via l'event WS `ReferentSnapshot`.
- **Plan & todo découplés** : plan riche **par-id** (`workspace/plan_<id>.md`, **NON** réinjecté — lu à la
  demande via nudge), todo terse (`todo.json`, re-surfacé `[TODO-RECAP]`) ; mode plan = écrit le plan puis
  **halte déterministe** ; multiplicité (supersede + historique `GET …/plans`).
- **Lignée de fork** en DB (`parent_conv_id`/`parent_commit`, migration 151) → API → UI.
- **Stop interruptible + garde-fou boucle** (R5) : Stop ferme la connexion Ollama et annule pendant les tool
  calls ; un garde-fou sans-progrès fait conclure la boucle seule.
- **Maths LaTeX** rendues en KaTeX dans le chat (`katex` direct ; le plugin CJS était cassé par l'optim de deps Vite).
- **graphify supprimé** du système (câblé mais inerte + RAM) — n'existe plus.
- **Config modèle = foyer unique `models.toml`** (`[roles]`, `[context_window]`, `no_thinking`, `[voice]` ;
  défauts committés dans `models.example.toml` ; env = override). Orchestrateur (`main`) = **cogito:32b**
  (gagnant d'une éval tool-calling) ; dispatch = granite4.1:8b ; code-router = qwen3:14b ;
  reasoner/compactor/subagent = gemma4:26b ; code-runner = qwen3-coder:latest.
- **VRAM** : `num_ctx` épinglé par modèle avec plafond (128k) — fini l'OOM (un KV 128k sur un 8B ≈ 54 Go).
- **Résilience WS / streaming** : keepalive serveur désactivé (la famine GIL des tours longs le déclenchait à
  tort en 1011), `final` émis **après** la shadow-consolidation (sinon l'accept du plan était droppé), tokens
  streamés live (thinking ≠ réponse), `no_thinking` proactif pour les modèles sans canal Ollama.
- **Logging daemon** : `logs/jean-michel.log` (rotatif, + stderr), niveau via `JEANMICHEL_LOG_LEVEL`.

## Références vivantes

### Architecture & déterminisme de l'orchestrateur
- [orchestrator_determinism.md](orchestrator_determinism.md) — paramètres live, modèles par rôle, garde-fous (**auto-généré** : `./jm.sh --orchestrator-map`).
- [agents_synoptic.md](agents_synoptic.md) — carte des chaînes d'agents (**auto-généré** : `./jm.sh --synoptic`).
- [PROMPT_SKELETON.md](PROMPT_SKELETON.md) — structure du prompt v2.
- [HOWTO_ADD_SPECIALIST_OR_TOOL.md](HOWTO_ADD_SPECIALIST_OR_TOOL.md) — ajouter un agent / outil (recettes SQL).

### Modèles
- [models_eval.md](models_eval.md) — roster par rôle, méthodo d'éval (`debug/eval_model.py`), verdicts, candidats code-router + câblage, budget VRAM.
- [GEMMA4.md](GEMMA4.md) — cheat-sheet gemma4 (reasoner / compactor / subagent + multimodal).
- [../README.md](../README.md) §Modèles configurables — le foyer `models.toml`.

### Système (références externes)
- [system_prompts/](system_prompts/) — prompts de référence (claude.ai, claude-code, opus, comet) pour inspiration / comparaison.

## Le « pourquoi » (archéologie & réflexion de fond)
Les plans livrés et audits historiques ont été dégagés du dépôt courant ; le *pourquoi* d'une décision est
dans l'**historique git** (rien n'est perdu). Le dossier `DevNotes/` (réflexion de fond) le complète s'il est
conservé localement.
