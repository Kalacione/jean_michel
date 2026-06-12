# E2E réel (mode code) + checklist de tuning — pour ne pas oublier

> Statut : P0→P6 livrés et verts (709 tests). Reste **la validation en conditions réelles**
> (Ollama, vrai repo) — c'est elle qui valide la thèse « contexte solide + raisonnement bordé ⇒
> un petit modèle codeur suffit ». Ce doc fige le protocole + les leviers de tuning à régler
> APRÈS observation (on ne tune pas à l'aveugle).

## Pré-requis

- `.env` : `JEANMICHEL_CODE_WORKTREE_ENABLED=1` (déjà posé). `repo_test` auto-détecte l'interpréteur
  (`.venv/bin/python`), rien à configurer pour le dogfood.
- **graphify** : auto-servi par `./jm.sh` (CLI) **et** `./jm.sh --serve` si `JEANMICHEL_GRAPHIFY_ENABLED=1`
  (déjà). Pré-requis : un graphe construit (`./graphify.sh build` ; rafraîchir `./graphify.sh update`).
- Tableau de bord : `./jm.sh --synoptic` (chaînes d'agents) + `./jm.sh --orchestrator-map` (params
  déterministes live). À garder ouverts pour voir « où ça coince ».

## Protocole E2E

1. Lancer `./jm.sh --mode code` (CLI) — graphify démarre tout seul ; un worktree `jm/conv-<id>` est
   créé sous `conversations/<id>/worktree/` (le tree live n'est jamais touché).
2. Donner une **vraie petite feature multi-fichiers** sur le repo jean-michel lui-même. Exemples
   calibrés (assez gros pour déclencher la sonde, assez petit pour finir) :
   - « ajoute un flag `--json` à `./jm.sh --synoptic` qui sort le mermaid+roster en JSON » ;
   - « ajoute un champ `truncated` au résultat de `repo_grep` quand head_limit coupe, + test » ;
   - « refactore l'extraction d'identifiants du CRP dans une fonction réutilisable + test ».
3. Observer le déroulé (CLI events + `conversations/<id>/events.jsonl` + le worktree).

### Checklist d'observation (ce qui DOIT se voir)

- [ ] **Worktree** créé (`git -C <repo> worktree list` montre `jm/conv-<id>`) ; `git status` du tree live **propre**.
- [ ] Le router émet un **`todo.json`** (décomposition PDCA, un seul `in_progress`).
- [ ] Chaque délégation à un worker code porte un **Context Packet** (`## Reconstructed context` :
      graphe / grep / source / diff) — visible dans `conversations/<id>/subagent_*.json`.
- [ ] **read-before-edit** : un `repo_edit` sans `repo_read` préalable est refusé (`read_before_edit`).
- [ ] **`repo_test`** renvoie un résultat structuré (`passed`/`failed`/`counts`) ; sur échec le router
      révise le TODO.
- [ ] Sur un **step dur** : thèse → antithèse → synthèse + verdict `sergent-kiss` tracés dans
      `events.jsonl` (délégations `critical-coder`/`sergent-kiss`). Sur un **step trivial** : AUCUNE
      délibération (la sonde n'a pas fire).
- [ ] Fin : un **diff revu** sur la branche ; rien d'écrit dans `jeanmichel.db`/`.env`/`conversations/`.

### Critères de succès

- La feature est **complétée méthodiquement** (todo vivant correct), les tests passent dans le worktree,
  aucun fichier protégé touché, le diff est propre et minimal (pas d'over-engineering — le sergent-KISS
  a fait son office).

## Test de la thèse « petit modèle »

Une fois un run de référence obtenu (codeur = `qwen3-coder:latest`), rejouer **la même tâche** avec
`code-runner.model_override` rétréci (un coder plus petit) — via la GUI paradigm-matrix ou
`UPDATE agents SET model_override=... WHERE code='code-runner'` (+ backup DB). Comparer : la tâche
tient-elle grâce au contexte pré-assemblé (CRP) + la délibération ? C'est le **critère de succès du
projet**.

## Checklist de tuning (À FAIRE APRÈS OBSERVATION, pas avant)

| Levier | Où | Quand l'ajuster |
|---|---|---|
| Sonde de complexité (`_HARD_KEYWORDS`, ≥2 fichiers) | `src/jeanmichel/deliberation.py` | si la délibération fire trop (steps triviaux) ou pas assez (steps durs ratés) |
| `sergent-kiss.model_override` (NULL = défaut aujourd'hui) | DB `agents` | si les verdicts PASS/REWORK sont faibles → modèle plus fort |
| `critical-coder.model_override` (gemma4:26b) | DB `agents` | si les angles manquent de mordant / trop lents |
| Scope du diff aval | `orchestrator_v2._handle_tool_call` (review_diff) | aujourd'hui = `git diff` worktree **complet** ; si bruyant → restreindre au **delta du step** |
| Coût délibération (3-4 appels LLM séquentiels) | `deliberation.MAX_REWORK`, sonde | si latence trop forte sur 1 GPU → resserrer la sonde, baisser MAX_REWORK |
| Caps des tranches CRP (`_SRC_LINES_CAP`, `_DIFF_LINES_CAP`, `_GREP_HITS_CAP`, `_TOTAL_CAP`) | `context_packet.py` | si le packet sature le ctx du worker, ou au contraire trop maigre |
| Fraîcheur graphe | `repo_graph_refresh` (appel post-édition structurelle) | si les requêtes structurelles reflètent l'avant-édition trop souvent |

## Non-régression (obligatoire après tuning)

Rejouer **hors mode code** : un brief research (strategist→specialists), une comparaison, un tour
chat, un tour vocal → confirmer qu'aucun outil/paradigme code n'a fui et que la latence est inchangée
(`pytest tests/v2` reste vert ; conftest épingle `CODE_WORKTREE_ENABLED=off` côté tests).
