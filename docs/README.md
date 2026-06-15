# Jean-Michel — base de connaissance (docs)

Index de la connaissance acquise : **design, audits, plans** par thème. Les docs **datés** (`AAAAMMJJ_…`) sont
des **archives point-in-time** (on ne les réécrit pas — on s'y réfère pour le *pourquoi*). Les docs **non datés**
sont des **références vivantes** (tenues à jour).

> Pour le backlog en cours, voir [../todo.md](../todo.md). Entrée produit/setup : [../README.md](../README.md).

## État courant (snapshot post-batch 2026-06-15)
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

## Par thème

### Architecture & déterminisme de l'orchestrateur
- [orchestrator_determinism.md](orchestrator_determinism.md) — paramètres, modèles par rôle, CRP, garde-fous (vivant).
- [agents_synoptic.md](agents_synoptic.md) — carte des chaînes d'agents (auto-généré : `./jm.sh --synoptic`).
- [PROMPT_SKELETON.md](PROMPT_SKELETON.md) — structure du prompt.
- [agents_synoptic.md](agents_synoptic.md) + [HOWTO_ADD_SPECIALIST_OR_TOOL.md](HOWTO_ADD_SPECIALIST_OR_TOOL.md) — ajouter un specialist/outil.

### Config modèles (models.toml, rôles, VRAM, no-thinking)
- [../README.md](../README.md) (section config) — foyer `models.toml`.
- [20260614_model_selection.md](20260614_model_selection.md) — éval orchestrateur (cogito gagnant), candidats
  code-router, harness `debug/eval_model.py`, préférences modèle.
- [GEMMA4.md](GEMMA4.md) — cheat-sheet gemma (reasoner/compactor/subagent ; plus le routeur par défaut).

### Plan mode
- [20260613_plan_mode/audit_orchestration.md](20260613_plan_mode/audit_orchestration.md) — audit + décisions (Claude plan mode, etc.).

### Chaîne code / intervention sur un vrai repo (Étages A/B/C, sandbox projet)
- [20260612_improve_thinking/](20260612_improve_thinking/) — plan, étage B (sandbox projet), branchable repo, E2E/tuning.
- [20260522_introspection_repo_access/](20260522_introspection_repo_access/) — analyse + sprint accès repo.
- [20260503_workspace_sandbox/PLAN_WORKSPACE_SANDBOX.md](20260503_workspace_sandbox/PLAN_WORKSPACE_SANDBOX.md) — sandbox workspace.

### Mémoire
- [20260608_improve_memory/plan.md](20260608_improve_memory/plan.md) — refonte mémoire (scopes, FTS, projets, consolidation shadow).
- [20260503_lifecycle_conversation/RAPPORT_LIFECYCLE_CONVERSATION.md](20260503_lifecycle_conversation/RAPPORT_LIFECYCLE_CONVERSATION.md) — cycle de vie d'une conversation.

### Recherche / specialists / tools
- [20260525_architecture_recherche.md](20260525_architecture_recherche.md) — architecture de la recherche.
- [HOWTO_ADD_SPECIALIST_OR_TOOL.md](HOWTO_ADD_SPECIALIST_OR_TOOL.md) — ajouter un agent/outil.
- [sprints/](sprints/) — plans d'implémentation par feature (comparator, weather, modes, paradigm matrix, tools, intervention).
- [20260524_need_input_auto.md](20260524_need_input_auto.md) — ask_human / besoin d'input.

### Audits & refontes historiques (archives)
- [20260501_audit_recalibrage/](20260501_audit_recalibrage/) — audit + recalibrage + changelog.
- [20260503_audit_injection_summary/](20260503_audit_injection_summary/) · [20260503_slip_ou_calecon/](20260503_slip_ou_calecon/)
- [20260522_abyss/](20260522_abyss/) — abyss + proposition meta-analysis.

### Système (prompts de référence externes)
- [system_prompts/](system_prompts/) — prompts de référence (claude.ai, claude-code, opus, comet) pour inspiration/comparaison.
