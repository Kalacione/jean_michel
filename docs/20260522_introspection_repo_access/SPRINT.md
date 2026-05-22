# Sprint — introspection & accès repo

**Dossier** : `docs/20260522_introspection_repo_access/`  
**Analyse de référence** : `ANALYSE.md`  
**Branch** : `convergence_gate`

---

## Tâches

### ✅ Analyse initiale
Voir `ANALYSE.md` — 4 trous identifiés, 4 phases.

---

### [ ] P4 — Paradigme `meta_analysis_routing` → jean-michel
**Objectif** : Guider jean-michel à déléguer au meta-analyst toute tâche d'introspection système, sans tenter d'y accéder directement.

- [ ] Insérer paradigme 104 (`meta_analysis_routing`) — catégorie 11 (handoff)
- [ ] Binder à jean-michel (id=1) dans `agent_paradigms`
- [ ] Migration SQL `db/migrate_013_meta_analysis_routing.sql`
- [ ] Mise à jour `db/schema.sql`
- [ ] Tests verts

---

### [ ] P3 — Workspace write grants : principe de moindre privilège
**Objectif** : Retirer `workspace_create_file` + `workspace_str_replace` + `agent_workspace_grants` des agents sans vocation d'écriture.

Agents concernés : summarizer (2), synthesizer (3), comparator-specialist (6)

- [ ] `DELETE` agent_tools pour (2, 3, 6) × (workspace_create_file, workspace_str_replace)
- [ ] `DELETE` agent_workspace_grants pour (2), (3), (6)
- [ ] Migration SQL `db/migrate_014_workspace_grants_least_privilege.sql`
- [ ] Mise à jour `db/schema.sql`
- [ ] Mise à jour paradigme 103 `workspace_as_shared_memory` : clarifier que certains agents sont read-only
- [ ] Tests mis à jour (test_db.py : assertions grants summarizer/synthesizer)

---

### [ ] P2 — Découpage `self_inspect` en outils distincts
**Objectif** : Granularité réelle des grants — pas de "tout ou rien" sur 7 scopes.

Découpage proposé :
- `self_inspect_config` → scopes `agents` + `paradigms`
- `self_inspect_activity` → scopes `conversations` + `sandbox` + `recent_summaries`
- `self_inspect_architecture` → scope `architecture` (README + schema.sql)

Grants cibles :
| Outil | meta-analyst | code-runner | document-builder |
|---|:---:|:---:|:---:|
| self_inspect_config | ✓ | — | — |
| self_inspect_activity | ✓ | — | — |
| self_inspect_architecture | ✓ | ✓ | ✓ |

- [ ] Créer `src/jeanmichel/tools/self_inspect_config.py`
- [ ] Créer `src/jeanmichel/tools/self_inspect_activity.py`
- [ ] Créer `src/jeanmichel/tools/self_inspect_architecture.py`
- [ ] Déprécier/supprimer ancien `self_inspect` (ou le garder en alias meta-analyst-only pendant transition)
- [ ] Enregistrer les 3 outils dans `build_registry`
- [ ] Migration SQL `db/migrate_015_self_inspect_split.sql`
- [ ] Mise à jour `db/schema.sql`
- [ ] Mise à jour paradigme `inspect_before_proposing` (94)
- [ ] Tests

---

### [ ] P1 — `repo_read_file` : BLOQUÉ — réflexion sécurité requise

**⚠ Ne pas implémenter avant le doc de réflexion.**

Voir `REFLEXION_SANDBOX_SECURITE.md` (à créer).

Questions ouvertes :
- Quels fichiers sont accessibles ? (src/ ? docs/ ? tests/ ? .git ?)
- Quelle exclusion pour éviter qu'un LLM lise ses propres credentials/secrets ?
- Quel agent peut avoir ce grant ? Avec quelles guardrails comportementales ?
- Risque de boucle : meta-analyst lit le code → propose un changement → code-runner l'applique → sans validation humaine = catastrophe
- La sandbox docker isole l'exécution mais pas la lecture du repo
- Git : un `git reset --hard` ou une corruption du schema.sql serait irréversible sans backup

---

### [ ] P1 (préalable) — Doc réflexion : sandbox, sécurité, repo access
Fichier cible : `docs/20260522_introspection_repo_access/REFLEXION_SANDBOX_SECURITE.md`

Questions à trancher :
1. Périmètre de lecture autorisé (inclus/exclus par chemin)
2. Séparation lecture vs exécution : peut-on lire sans risque d'exécution ?
3. Pipeline de validation humaine : à quel step l'humain intervient-il ?
4. Protections git : snapshot/backup avant toute modification proposée ?
5. Guardrails LLM : paradigmes anti-hallucination pour agents avec repo access
6. Audit trail : comment tracer "cet agent a lu ce fichier à cet instant" ?

---

## État du branch

```
convergence_gate
├── fix: ask_human list coercion
├── fix: support_files validation + delegate_to desc
├── fix: MAX_RECURSION_DEPTH=10
├── refactor: tool_response stub pour workspace_view + conv_read_file
└── docs: analyse introspection + accès repo
```
