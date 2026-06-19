# Mémoire & paradigmes

Comment jean-michel apprend et se souvient. Trois types de mémoire (vocabulaire de l'état de l'art — cf.
[local_agent_stack.md](local_agent_stack.md)), un principe unique : **rien n'est écrit en mémoire sans
validation humaine**.

| Type | Chez nous | Où |
|---|---|---|
| **Épisodique** (le vécu brut) | transcripts + `events.jsonl` d'une conversation | dossier conv |
| **Sémantique** (les faits) | table `memory` — scopes `user` / `project` / `tool` | `service/memory.py` |
| **Procédural** (le savoir-faire, les règles) | les `paradigms` injectés en `# DIRECTIVES` | `db.py`, `prompts.render_directives` |

## Sémantique — la table `memory`
Un fait a un **scope** (`user` = sur l'humain, `project` = décision/contrainte du projet courant, `tool` = leçon
réutilisable sur un outil) + une **importance** (1-5). L'injection au prompt est **100 % déterministe** (pur SQL,
aucun LLM) : `prompts.render_memory_block` rend un **index** (`code : description`) par scope, **classé
`importance DESC, puis récence`**, plafonné ; le **corps complet** est chargé à la demande. La recherche est
FTS5 + BM25 (`manage_memory action=search`). **Pas d'embeddings** — choix assumé (déterminisme + simplicité).

`manage_memory` est **lecture seule** (`recall` / `search` / `list`). L'agent n'écrit jamais directement : toute
écriture passe par une **proposition** (ci-dessous) que l'humain valide.

## La boucle de capture → revue
Deux producteurs de **candidats**, déclenchés **en direct** (jamais sur timer), convergent vers une file unique
(`pending_consolidation` en DB) ; l'humain tranche.

1. **`propose_memory`** (outil agent) — capture explicite (« garde ça en mémoire » / « note pour plus tard ») ou
   opportuniste. `kind="fact"` → candidat mémoire ; `kind="rule"` → candidat paradigme.
2. **Le beat de réflexion** — à la complétion de **chaque tour deep** (petit modèle `JEANMICHEL_REFLECTION_MODEL`,
   contexte frais), `consolidation.propose()` extrait des candidats **ancrés** (un fait doit citer verbatim un
   message *user* ou un résultat d'*outil* — jamais les dires de l'assistant : anti-hallucination) et **peut**
   aussi proposer une **règle** comportementale. C'est le *scaffold* : un agent en pleine tâche propose rarement
   de lui-même. Déclenché à la frontière de tour, **pas** par un daemon de fond.

À la proposition, le traitement est déterministe : **dédup FTS** vs l'existant → `suggested_action`
(`new` / `extend` / `supersede`). Le meta-analyst alimente aussi cette file (il propose des règles ancrées sur le
roster réel via `self_inspect_config` ; il propose, n'applique jamais).

**Revue humaine** : CLI `/memo` (`review_pending`) + web `MemoryReviewDialog`. Pour un fait : `save` / `extend` /
`supersede` (remplace une entrée périmée — fraîcheur façon mem0) / `delete` / `drop`. Rien ne s'écrit sans ce feu vert.

## Procédural — promotion en paradigme
Une **leçon** (méthodo, correctif d'erreur récurrente) est un candidat `kind="rule"`. La dédup se fait vs **TOUS**
les paradigmes (sans filtre d'agent) : si un paradigme proche existe mais n'est pas granté à l'agent,
**binder l'existant** vaut mieux que dupliquer. À l'approbation (`apply_rule_candidate`) :
- **create** → `db.create_paradigm(active=0)` : le paradigme naît **éteint** (visible mais injecté nulle part)
  jusqu'à ce que l'humain l'active + le bind — anti-bloat du prompt, rien ne s'auto-applique ;
- **bind** → `db.bind_paradigm` sur un paradigme existant.

Curation des paradigmes : dans l'**app web** (`ParadigmsDialog` — catalogue éditable : content + rationale +
modes + bindings, et un onglet **Promotions** qui revoit les candidats-règle) ou en **CLI** (`debug/admin.py` :
`paradigm <code>`, `paradigms`, `promotions`, `bind`/`toggle-paradigm`). Le serveur autonome `paradigm_matrix`
(antérieur à l'app web) a été retiré.

## Les fichiers
`service/memory.py` (CRUD + FTS) · `service/consolidation.py` (propose / add_candidate / add_rule_candidate /
apply_candidate / apply_rule_candidate + la file `pending_consolidation`) · `tools/propose_memory.py` ·
`tools/manage_memory.py` (lecture) · `prompts.render_memory_block` + `render_directives` (injection déterministe) ·
`db.py` (paradigms : create/bind). Câblage des modèles : [orchestrator_determinism.md](orchestrator_determinism.md).
