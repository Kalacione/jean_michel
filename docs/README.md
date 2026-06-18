# Jean-Michel — base de connaissance (docs)

Index des **références vivantes** (tenues à jour). Les vieux plans/audits datés, reliques v1 et le dossier
`DevNotes/` ont été dégagés — **l'historique git les conserve** (c'est là que vit le *pourquoi* d'une décision).

> Backlog en cours : [../todo.md](../todo.md). Entrée produit / setup : [../README.md](../README.md).

## Repères (comment ça marche)
- **Référent organisationnel** : `state.json` est le ledger autoritaire d'une conversation (tours, plans,
  todos, subagents, fichiers, `phase`, `plan_mode`). Il est poussé live à l'UI (event WS `ReferentSnapshot`),
  et un filet (`rebuild_from_events`) garantit qu'il est reconstructible depuis le journal d'events.
- **Plan & todo découplés** : un plan riche **par-id** (`workspace/plan_<id>.md`, lu à la demande, non
  réinjecté), un todo terse (`todo.json`, re-surfacé en `[TODO-RECAP]`). En mode plan, l'orchestrateur écrit le
  plan puis s'arrête pour validation ; un re-plan supersède le précédent (historique consultable, `GET …/plans`).
- **Fork de conversation** : enregistre sa lignée (conversation + commit source), exposée dans l'UI.
- **Stop** interrompt réellement le tour (ferme la connexion Ollama, annule pendant les tool calls) ; un
  garde-fou fait conclure une boucle sans progrès.
- **Maths** : le LaTeX du chat est rendu en KaTeX.
- **Modèles** : config dans `models.toml` (`[roles]`, `[context_window]`, `no_thinking`, `[voice]` ; défauts
  committés dans `models.example.toml`, env prioritaire) ; `num_ctx` épinglé par modèle, plafond 128k (anti-OOM
  VRAM). Roster et verdicts → [models_eval.md](models_eval.md).
- **Logs** : `logs/jean-michel.log` (rotatif + stderr), niveau via `JEANMICHEL_LOG_LEVEL`.

## Références vivantes

### Architecture & déterminisme de l'orchestrateur
- [architecture_v2.md](architecture_v2.md) — **le spec d'architecture v2** (implémenté dans `src/` ; le code en cite les n° de section). Le *pourquoi* + le contrat.
- [orchestrator_determinism.md](orchestrator_determinism.md) — paramètres live, modèles par rôle, garde-fous (**auto-généré** : `./jm.sh --orchestrator-map`).
- [agents_synoptic.md](agents_synoptic.md) — carte des chaînes d'agents (**auto-généré** : `./jm.sh --synoptic`).
- [PROMPT_SKELETON.md](PROMPT_SKELETON.md) — structure du prompt.
- [HOWTO_ADD_SPECIALIST_OR_TOOL.md](HOWTO_ADD_SPECIALIST_OR_TOOL.md) — ajouter un agent / outil (recettes SQL).

### Modèles
- [models_eval.md](models_eval.md) — roster par rôle, méthodo d'éval (`debug/eval_model.py`), verdicts, candidats code-router + câblage, budget VRAM.
- [benchmark_agents.md](benchmark_agents.md) — bench live chiffré (recherche multi-source) ; base des verdicts.
- [GEMMA4.md](GEMMA4.md) — cheat-sheet gemma4 (reasoner / compactor / subagent + multimodal).
- [../README.md](../README.md) §Modèles configurables — le foyer `models.toml`.

### Capacités & roadmap
- [image_vision.md](image_vision.md) — capacités vision (Gemma4), décisions de design.
- [tool_ideas.md](tool_ideas.md) — candidats d'outils à ajouter (géo, extraction de contenu).

### Système (références externes)
- [local_agent_stack.md](local_agent_stack.md) — digest de l'article SitePoint « la stack d'un agent local » (GGML→orchestration), mappé à notre stack Ollama + ce qu'on pourrait piocher (GBNF, Pydantic, KV-quant).
- [system_prompts/](system_prompts/) — prompts de référence (claude.ai, claude-code, opus, comet) pour inspiration / comparaison.

## Le « pourquoi » & le « quand »
Les plans livrés, audits historiques et le dossier `DevNotes/` ont été dégagés du dépôt courant ; le *pourquoi*
d'une décision est dans l'**historique git** (rien n'est perdu), et l'architecture dans
[architecture_v2.md](architecture_v2.md). Le *quand* (livraisons notables, évolution des migrations/paradigmes)
vit dans [../CHANGELOG.md](../CHANGELOG.md).
