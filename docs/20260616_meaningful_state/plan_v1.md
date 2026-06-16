# Plan — Conversation Ledger : état des lieux complet PUIS réagencement

## Context

Le `state` est anémique et **recréé from scratch chaque tour, jamais rechargé dans la boucle**
(`load_state` ne sert qu'au front) → rien d'organisationnel ne survit. La vision (utilisateur) : le
state devient le **référent autoritaire, étoffé, complet, à jour**, **maintenu par l'orchestrateur
(déterministe)** ; on **lit** partout, on ne **dérive** plus rien ; il **persiste** (reprise d'état).
`events.jsonl` = journal ; `rebuild_from_events` = **filet** anti-drift (pas le chemin de lecture).

**Stockage — par conversation, en couches DANS le dossier de la conv :**
- **`racine/`** — DÉTERMINISTE, propriété de l'orchestrateur → **c'est là que vit `state.json`** (le référent ; il PORTE le statut + les métadonnées des plans). Aussi : `messages.json`, `events.jsonl`, les fichiers **par plan** (`plan_<id>.md`, `todo_<id>.json`), `subagent_*.json`. (Cible : plus de `plan_status.json`, ni de `plan.md`/`todo.json` uniques — voir B.) ;
- **`workspace/`** — écrit par les LLM (sorties, plans rendus, résumés, recherches) ;
- **`worktree/`** — fichiers de repo, **mode code uniquement** ;
- **`llm_streams/`** — thinking LLM brut (debug + reprise).

**Règle de fond (utilisateur, non négociable)** : sans **état des lieux complet** de TOUS les endroits où
l'orchestrateur modifie state/ledger, le référent maintenu va driter → on fait de la merde. Donc :
**l'inventaire d'abord (ci-dessous, vérité du code par grep) ; le réagencement seulement après.**

## Schéma cible du `state.json` (base utilisateur `DevNotes/OUIIIIIIIII.js` + manques foldés)

**Pas de `ledger.json` séparé — `state.json` SOUS STÉROÏDES *EST* le ledger.** « Ledger » = le RÔLE (référent organisationnel), pas un 2e fichier (qui dupliquerait → drift = le bordel qu'on évite). **Trois rôles, trois foyers :**
- **`state.json`** = LE RÉFÉRENT : index + statuts + progression + liens + pointeurs. **Léger**, lu directement partout, rechargé en début de tour.
- **`events.jsonl`** = LE JOURNAL : faits immuables (l'historique) → alimente le filet `rebuild_from_events`.
- **fichiers de contenu** (`plan_<id>.md`, `todo_<id>.json`, `subagent_<id>.json`) = LE VOLUMINEUX : **référencés par path** depuis state.json, jamais dupliqués dedans.

**Périmètres NON couverts par `state.json` (et leur foyer)** : historique d'évolution → `events.jsonl` + snapshots git (state.json est le COURANT) · contenu volumineux (markdown, traces, items) → fichiers dédiés référencés · lignée cross-conversation (fork) → table DB `conversations` (`parent_conv_id`) + git · thinking brut → `llm_streams/`.
```jsonc
{
  // — éphémère, recalculé chaque tour mais INSCRIT (plus de dérivation à la volée) —
  "budget": { "system_reserve": 0, "output_reserve": 0, "working_budget": 0, "working_used": 0 },
  "depth_current": 0,
  "counters": { "search_calls_total": 0, "search_calls_since_last_persist": 0 },
  "stocktake_due": false,            // ex-`reeval_pending` (renommé) : un spécialiste est revenu → réévaluer
  // — round-trip ask_human (inchangé) —
  "active_subagent": null, "blocked_subagent_code": null,
  "blocked_subagent_request_id": null, "pending_human_answer": null,
  // — mode du TOUR courant (≠ active_plan_id) —
  "plan_mode": false,                // CONSERVÉ (bool) : "ce tour est un tour de PLAN". Pilote le FRONT (toggle Plan/Edit, bandeau) ET l'interne (PreToolUse, halte). Orthogonal à active_plan_id. NON renommé.
  // — organisation —
  "phase": "idle",                   // planning|awaiting_approval|executing|answered|idle (inscrite aux transitions)
  "active_plan_id": null,            // "id1"… ou null (pas de plan actif)
  "active_todo_id": null,            // "t1"… ou null (le tracker courant ; PEUT être plan-less)
  "plans": {
    "id1": {
      "status": "in_progress",       // pending|in_progress|blocked|failed|completed (exécution)
      "approved": false,             // acceptation HUMAINE (≠ status) — [meilleur que mon proposed/accepted]
      "plan_file": "plan_id1.md",
      "todo_id": "t1",               // → todos{} (le tracker de ce plan) ou null
      "created_at_request": "req_…",
      "files": [ { "path": "…", "layer": "workspace|worktree", "produced_by": "req_…|sub_…" } ], // [G4]
      "subagents": [ { "request_id": "sub_…", "agent": "code-runner", "parent_request": "req_…",
                       "confidence": "high", "files_produced": ["…"] } ]                         // [G1]
    }
  },
  // — todos PREMIER NIVEAU, DÉCOUPLÉS des plans (un todo peut être plan-less) —
  "todos": {
    "t1": { "plan_id": "id1",        // ou null = todo SANS plan (self-tracker, ex. agent de recherche)
            "owner": "orchestrator", // ou "sub_<request_id>"
            "status": "in_progress", "done": 3, "total": 5, "current_step": "s4",  // [G3] progression INSCRITE
            "file": "todo_t1.json",  // items détaillés (volumineux) hors state
            "created_at_request": "req_…" }
  },
  "requests": [ { "id": "req_…", "mode": "plan|edit", "plan_id": "id1|null", "started": "…",
                  "ended": "…", "last_iteration_utc": "…", "outcome": "answered|halted|aborted",
                  "summary": "…" } ],                                                            // [G2]
  "lineage": { "parent_conv_id": null, "parent_commit": null }                                   // [G5] inclus maintenant
}
```
**Décidé** : fold des 4 manques [G1 subagents · G2 request_id+requests[] · G3 todo inscrit · G4 files layer+producteur] + [G5 lignée **maintenant**]. **`plan_mode` CONSERVÉ** (bool) — contrat FRONT (toggle Plan/Edit, bandeau) + INTERNE (PreToolUse, halte) ; orthogonal à `active_plan_id` (quel plan) ; **pas renommé** (renommer = churn front + ~6 sites internes, cf. D, pour zéro gain). `reeval_pending`→`stocktake_due` (interne, peu de sites). **Todos PREMIER NIVEAU, découplés des plans** (`todos{}` + `plan_id` nullable) → multiples ET plan-less. `phase` inscrite. (À part : `working_budget` ridicule vs `system_reserve` = optim contexte séparée.)

### Todos multiples & plan-less (analyse — retour utilisateur)
- **Multiples** : un todo PAR plan via `plans[id].todo_id` → `todos{}` ; `active_todo_id` = le tracker courant.
- **Plan-less, cas 1 — orchestrateur self-tracker** : tâche multi-étapes en EDIT sans plan formel (déjà acté : plan/todo découplés ; le nudge EDIT l'invite). `owner="orchestrator"`, `plan_id=null`. ✅ **couvert nativement** par `todos{}` premier-niveau.
- **Plan-less, cas 2 — subagent (ex. agent de recherche) qui traque sa progression** : NOUVEAU. Tension : les subagents sont **isolés** (sub_messages, briefing→report) et **n'écrivent PAS** les fichiers conv (invariant `_persist` main-only, cf. A/F). Donc un todo de subagent ne doit **pas** écrire `state.json` directement. Voie propre : le subagent traque **en interne** (tracker dans sa sub-state) et le **remonte via `report_back`** ; l'orchestrateur (seul writer du state) **mirroir** la progression dans `todos{}` avec `owner="sub_<request_id>"`. → Le schéma l'**accommode** (todos keyés + `owner`), mais on **ne construit PAS** le tracker-subagent en v1 (spéculatif) ; porte ouverte, à re-décider quand le cas se présente.

---

## ÉTAT DES LIEUX (vérité du code — grep déterministe + audit)

### A. Mutations de l'état EN MÉMOIRE (`ConversationState`)
- **Constructeurs** : `orchestrator_v2.py:1056` (main : `depth_current=0, plan_mode`) · `:1198` (subagent `sub_state` : `depth+1`, `plan_mode` propagé).
- **Budget** : `orchestrator_v2.py:273-277` `_initialize_state` (`system_reserve_tokens`, `output_reserve_tokens`, `working_budget`) — appelé au début du tour **et à chaque spawn de subagent** ; `compaction.py:145` (`working_tokens_used`, recalculé après chaque niveau de compaction).
- **Compteurs recherche** : `hooks.py:295-296` (`search_calls_total +=`, `search_calls_since_last_persist +=`) · `:298` (`=0` sur write workspace) · `:323` (`=0` après nudge force-persist).
- **`reeval_pending`** : `hooks.py:300` (`=False` sur todo_write/update) · `:360` (`=True` sur retour de délégation).
- **`active_subagent`** : `hooks.py:357` (`=None`) — **écrit, jamais lu** (sous-utilisé).
- **Round-trip ask_human (P5)** : `orchestrator_v2.py:776` (`pending_human_answer=answer`) · `:877-879` (reset du trio avant resume) · `:922-923` (`blocked_subagent_code/request_id` sur retour low).

### B. Écrivains des ARTEFACTS PERSISTANTS racine
- **todo.json** : `tools/todo_write.py:38` (`save_todo`) · `todo.py:221` (`save_todo` via `set_status`←todo_update) · `api/app.py:318` (PUT /todo, édition humaine). **clear** : `tools/todo_write.py:32`, `tools/todo_update.py:33`, `orchestrator_v2.py:599` (conclusion EDIT).
- **plan.md** : `tools/plan_write.py:31` (`save_plan`) · `api/app.py:338` (PUT /plan). **clear** : `api/app.py:340` (`clear_plan`, PUT vide). → **CIBLE** : devient l'objet `plans` du ledger — un plan PAR id, chacun pointant son `plan_file` (`plan_<id>.md`) ; le `plan.md` global unique disparaît (multiplicité). Cf. `DevNotes/OUIIIIIIIII.js`.
- **plan_status.json** : `todo.py:148` (`accepted`) · `:150` (`proposed`) via `reconcile_plan_status_on_turn` (appelé `orchestrator_v2.py:1058` début, `:1096` fin) ; clear via `clear_plan`. → **CIBLE** : **migre dans `state.json`/ledger** (`ledger.plans[id].{status, approved}`) ; le sidecar `plan_status.json` est **SUPPRIMÉ**. Cf. `DevNotes/OUIIIIIIIII.js`.
- **state.json** : `orchestrator_v2.py:448` (`save_state`, dans `_persist`, **main agent uniquement**).
- **messages.json** : `orchestrator_v2.py:447` (`_persist`) **+** `service/turn_runner.py:85` (2e écrivain — à confirmer : persistance du message user/finalisation).
- **events.jsonl** : `orchestrator_v2.py:290` (`append_event` via `_emit`) **+** `api/reflection.py:116` (le daemon append `MemoryConsolidationProposed`).
- **subagent_<id>.json** : `orchestrator_v2.py:1245` (`save_sub_messages`).

### C. Mutations de la ligne DB `conversations` (état niveau base)
- `db.py:161` `update_conversation_language` · `:174` close (`status='closed'`) · `:209` `set_title` · `:215` `set_title_if_empty` · `:226` `touch_modified_at` (ordre de liste) · `:421` `set_conversation_project` · création `:149`.
- **`current_phase` / `task_class`** : colonnes présentes, **aucun writer ni reader** → à piloter depuis le ledger (phase) + dispatcher (task_class).

### D. LECTURES / DÉRIVATIONS runtime (→ à remplacer par lecture du ledger)
- `load_plan` : `hooks.py:402` (_refresh_plan_doc), `:526` (has_plan) ; `orchestrator_v2.py:577` (garde plan), `:672` (halte).
- `load_todo` : `hooks.py:427` (_refresh_todo_recap), `:527` (has_todo) ; `context_packet.py:79` ; `todo.py` (set_status).
- `load_plan_status` : `todo.py:147` (reconcile).
- `_count_delegations(messages)` : `hooks.py:468` (def) → `:565` (≥2 ⇒ nudge tracker).
- Budget/ratio : `compaction.py:129-145` (`compute_working_ratio`/`_sync_state`, recalcul depuis messages) ; `orchestrator_v2.py:457` (ratio → WorkingBudgetUpdate).
- `worktree.exists` : `hooks.py:449`,`:556` ; `tools/__init__.py:154` ; `context_packet.py:176` ; `orchestrator_v2.py:362`,`:932`.
- `reeval_pending` (lecture) : `hooks.py:200`,`:551`. `plan_mode` (branches) : `hooks.py:201`,`:508` ; `orchestrator_v2.py:577`,`:598`,`:670` ; `compaction.py:398`.

### E. Champs SOUS-UTILISÉS à alimenter (pas morts)
- `last_iteration_at_utc` (`models.py:84`) : aucun writer/reader → alimenter (timestamp par itération ; `requests[].last_iteration_utc`).
- DB `current_phase`/`task_class` : voir C → piloter depuis le ledger.
- `active_subagent` : écrit-jamais-lu → porter dans `ledger.subagents` ou retirer.

### F. Effets de bord persistance / snapshot / front
- **Fondation cassée** : `save_state` (main only) écrit, mais **rien ne recharge le state dans la boucle** (`load_state` = front only, `app.py:283`). State recréé frais chaque tour.
- **Snapshot** (`snapshot.py`) : `git add -A` commit TOUT le dossier racine. **INVARIANT requis** : `state.json` + ses fichiers référencés (`plan_<id>.md`, `todo_<id>.json`) + `events.jsonl` doivent être versionnés **ENSEMBLE** à chaque snapshot — sinon un `revert` restaure le contenu mais laisse des **références obsolètes** dans le state. ✅ OK par construction (`git reset --hard`/`git archive` prennent tout l'arbre du commit → state + contenu + events reviennent au MÊME point ; le filet `rebuild_from_events` sur l'events reverté redonne le state reverté). `_REPO_GITIGNORE` n'exclut que `workspace/.thumbs/` + `*.tmp`. ⚠ à préserver si un jour on déplace un de ces fichiers hors du dossier conv.
- **Front** : lit **uniquement** `st.plan_mode` (`conversations.js:149`, fallback legacy). → le ledger étend la forme de `/state` ; le front lira phase/plans/liens.
- **Hors-scope état** : `conversation.md` (`persistence.py:56`) + `artifacts/` (`write_artifact:46`) = journal lisible + outputs LLM (lus par conv_history_scan/self_inspect), **pas** de l'état orchestrateur.

### G. Faits organisationnels → site d'inscription → event existant (pour le filet `rebuild_from_events`)
- **turn start** : `orchestrator_v2.py:1071` → `RequestStarted` (⚠ pas de `request_id`).
- **turn end** : final `:603-609` / fallback vide `:561-570` / halte PLAN `:673-680` → `RequestCompleted` ; **aborted/cancel/llm-fail `:452,:500,:685` → AUCUN event**.
- **plan proposé** : halte `:670-680` / outil `plan_write:31` → **aucun domain event**.
- **plan accepté/superseded** : reconcile `todo.py:148/:150` → **aucun event**.
- **todo create/update/clear** : outils + `:599` → **aucun event**.
- **subagent spawn/return** : `:1108`/`:1271` → `DelegationStarted`/`DelegationCompleted` (⚠ pas de `request_id` sur l'event).
- **fichiers produits** : outils workspace + `report_back.files_produced` → **aucun event** (sauf liste dans DelegationCompleted).
- **phase** : implicite (`plan_mode` + `plan_status`) → **aucun event**.
- ⇒ Pour le filet : ajouter `request_id`/`parent_request_id` sur les events + **domain events** (PlanProposed/Accepted/Superseded, TodoSnapshot, FileProduced, PhaseChanged, SubagentSpawned/Returned).

---

## RÉAGENCEMENT (gated : seulement une fois l'inventaire figé & validé)

**Étape 0 — Figer l'inventaire** : écrire ci-dessus dans `docs/AAAAMMJJ_conversation_state_inventory.md`
(référence de travail durable). C'est le livrable préalable que l'utilisateur valide.

**Phase 0 — Référent persistant (PAS de nouveau fichier : on ÉTOFFE `state.json`)** : étendre `ConversationState` (`models.py`) avec les champs organisationnels du schéma (`active_plan_id`, `plans{}`, `requests[]`, `lineage`, `phase`, `turn_mode`, `stocktake_due`…). `run_main_loop` : **`load_state` EN DÉBUT DE TOUR** (aujourd'hui jamais rechargé dans la boucle — c'est LE fix de fond), recalcul de l'éphémère pur (budget) **inscrit**, `save_state` en fin (déjà câblé). Alimenter `last_iteration_at_utc`. (`models.py`, `orchestrator_v2.py`, `persistence.py` ; **pas de `ledger.py`**.)

**Phase 1 — Inscrire au fil + lire le state + filet** : à CHAQUE site listé en A/B/C, inscrire `state.json` (phase, plans+statuts, todos-progression, subagents+request_id, files+liens+layer, counters, budget). Remplacer les dérivations D par des **lectures de `state.json`**. Ajouter `request_id` + domain events (G) et `rebuild_from_events` (reconstruit `state.json`) ; **test clé : maintenu == reconstruit** (garde-fou anti-drift). Front lit `state.json`.

**Phase 2 — Multiplicité (payoff, esquissé — re-planifié après P1)** : `plans[]`/`todos[]` multiples avec statuts (re-plan → `superseded`, gardé) ; lignée fork `parent_conv_id`/`parent_commit` sur `conversations` (migration) + UI « forké de X ».

## Tests
- Inscription : après un tour, le ledger reflète A/B/C correctement ; survit d'un tour à l'autre (reprise).
- **Filet** : `rebuild_from_events` pur + idempotent ; **égalité maintenu == reconstruit** sur scénario (anti-drift). C'est le test qui garantit qu'aucun site d'écriture n'a été oublié.
- Lectures (D) basculées sur le ledger sans régression de comportement (nudges/budget/branches).
- `pytest tests/v2` vert + `ruff` clean ; front affiche le ledger (build).

## Vérification empirique (daemon redémarré)
1. Conv multi-tours → `state.json` complet, **survit**, reflète phase + plans (proposed→accepted) + todos + subagents + fichiers liés + budget inscrit ; reprise d'état OK.
2. Supprimer `state.json` → `rebuild_from_events` redonne le même (filet) ; re-planifier → ancien `superseded`, fil lisible.
3. Le front montre l'organisation — on ne s'y perd plus.
