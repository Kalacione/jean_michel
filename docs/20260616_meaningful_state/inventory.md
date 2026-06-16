# Inventaire exhaustif — état/ledger de conversation (référence de travail)

> **But** : énumération DÉTERMINISTE (grep + audit) de **TOUS** les endroits où l'orchestrateur
> touche à l'état de conversation — pour qu'en passant le `state.json` au référent maintenu, on
> n'oublie **aucun** site (sinon drift → on fait de la merde). C'est la checklist de la Phase 1.
> Source de vérité du code au 2026-06-16. Marqueurs : ✅ déjà traité (Phase 0) · ⬜ à faire (Phase 1).
>
> Méthode : `grep "state\.\w+ *(=|+=)"`, `grep` des writers de fichiers racine, `grep` des UPDATE
> de la ligne DB `conversations`, `grep` des lectures/dérivations runtime. Schéma cible + décisions :
> voir `plan_v1.md` (§ Schéma) ; ce fichier-ci = l'état des lieux brut.

---

## A. Mutations de l'état EN MÉMOIRE (`ConversationState`)

| Site | Champ(s) | Quand | Statut |
|---|---|---|---|
| `orchestrator_v2.py` run_main_loop (≈1058) | construction du state du tour | début de tour | ✅ 0c : `from_dict(load_state)` + `reset_ephemeral` |
| `orchestrator_v2.py` ≈1200 (`sub_state`) | `depth_current+1`, `plan_mode` propagé | spawn subagent | ⬜ (sub_state reste frais/isolé — à garder tel quel) |
| `orchestrator_v2.py:273-277` `_initialize_state` | `system_reserve_tokens`, `output_reserve_tokens`, `working_budget` | début de tour + chaque spawn | ⬜ budget = éphémère recalculé (rester) |
| `compaction.py:145` `_sync_state` | `working_tokens_used` | après chaque niveau de compaction | ⬜ éphémère recalculé |
| `orchestrator_v2.py` loop top | `last_iteration_at_utc` | chaque itération | ✅ 0c : alimenté |
| `hooks.py:295-296` | `search_calls_total +=`, `search_calls_since_last_persist +=` | PostToolUse sur recherche | ⬜ |
| `hooks.py:298`, `:323` | `search_calls_since_last_persist = 0` | write workspace / après nudge force-persist | ⬜ |
| `hooks.py:300` | `stocktake_due = False` | sur todo_write/update | ⬜ |
| `hooks.py:360` | `stocktake_due = True` | sur retour de délégation (OnDelegateReturn) | ⬜ |
| `hooks.py:357` | `active_subagent = None` | OnDelegateReturn | ⬜ (écrit-jamais-lu → E) |
| `orchestrator_v2.py:776` | `pending_human_answer = answer` | callback ask_human | ⬜ (round-trip, reset par tour ✅ 0c) |
| `orchestrator_v2.py:877-879` | reset trio `blocked_subagent_*` + `pending_human_answer` | avant resume P5 | ⬜ |
| `orchestrator_v2.py:922-923` | `blocked_subagent_code/request_id` | sur retour low « HUMAN INPUT NEEDED » | ⬜ |

## B. Écrivains des ARTEFACTS PERSISTANTS (racine de conv)

| Fichier | Sites d'écriture | Cible Phase 1 |
|---|---|---|
| **todo.json** | `tools/todo_write.py:38` (save_todo) · `todo.py:221` (set_status←todo_update) · `api/app.py:318` (PUT /todo) · clear : `todo_write.py:32`, `todo_update.py:33`, `orchestrator_v2.py:599` (conclusion EDIT) | → `state.todos{}` (progression inscrite) ; fichier `todo_<id>.json` = items détaillés |
| **plan.md** | `tools/plan_write.py:31` (save_plan) · `api/app.py:338` (PUT /plan) · clear `app.py:340` | → `state.plans{}` (un par id) ; fichier `plan_<id>.md` = contenu |
| **plan_status.json** | `todo.py:148`/`:150` (set_plan_status via reconcile, appelé orch ≈1060/≈1098) · clear via clear_plan | → **SUPPRIMÉ**, migre dans `state.plans[id].{status,approved}` |
| **state.json** | `orchestrator_v2.py:448` (save_state, `_persist`, main only) | → c'est LE référent (s'étoffe) |
| **messages.json** | `orchestrator_v2.py:447` (`_persist`) + `service/turn_runner.py:85` | inchangé (journal de messages) |
| **events.jsonl** | `orchestrator_v2.py:290` (append_event via `_emit`) + `api/reflection.py:116` (daemon) | inchangé (journal → filet) |
| **subagent_<id>.json** | `orchestrator_v2.py:1245` (save_sub_messages) | inchangé (trace subagent) ; indexé par `state.subagents[]` |

## C. Mutations de la ligne DB `conversations`

| Site | Action |
|---|---|
| `db.py:149` | création (`create_conversation`) |
| `db.py:161` | `update_conversation_language` |
| `db.py:174` | close (`status='closed'`) |
| `db.py:209` / `:215` | `set_title` / `set_title_if_empty` |
| `db.py:226` | `touch_modified_at` (ordre de liste) |
| `db.py:421` | `set_conversation_project` |
| **`current_phase` / `task_class`** | colonnes **présentes, aucun writer/reader** → à piloter depuis `state.phase` + dispatcher (E) |

## D. LECTURES / DÉRIVATIONS runtime → à remplacer par lecture du `state`

| Dérivation | Sites | Remplacement |
|---|---|---|
| `load_plan` (existence/halte) | `hooks.py:402`,`:526` ; `orchestrator_v2.py:577`,`:672` | `state.plans` / `state.active_plan_id` |
| `load_todo` (has_todo) | `hooks.py:427`,`:527` ; `context_packet.py:79` ; `todo.py` (set_status) | `state.todos` |
| `load_plan_status` | `todo.py:147` (reconcile) | `state.plans[id].{status,approved}` |
| `_count_delegations(messages)` | `hooks.py:468` (def) → `:565` (≥2 ⇒ nudge) | compteur inscrit (`state.requests`/counters) |
| budget/ratio | `compaction.py:129-145` ; `orchestrator_v2.py:457` | budget inscrit (reste recalculé, mais lisible) |
| `worktree.exists` | `hooks.py:449`,`:556` ; `tools/__init__.py:154` ; `context_packet.py:176` ; `orchestrator_v2.py:362`,`:932` | (mode code) — éventuel flag inscrit |
| `stocktake_due` (lecture) | `hooks.py:200`,`:551` | inchangé (champ state) |
| `plan_mode` (branches) | `hooks.py:201`,`:508` ; `orchestrator_v2.py:577`,`:598`,`:670` ; `compaction.py:398` | inchangé (champ state, par-tour) |

## E. Champs SOUS-UTILISÉS à alimenter (pas morts)

- `last_iteration_at_utc` (`models.py`) : **✅ 0c** alimenté chaque itération. (Cible Phase 1 : aussi dans `requests[].last_iteration_utc`.)
- DB `current_phase` / `task_class` : aucun writer/reader → piloter depuis `state.phase` + dispatcher.
- `active_subagent` (state) : écrit-jamais-lu (`hooks.py:357`) → porter dans `state.subagents[]` ou retirer.

## F. Effets de bord : persistance / snapshot / front

- **Fondation** : `save_state` (main only, `orchestrator_v2.py:448`) écrivait, mais **rien ne rechargeait** dans la boucle. **✅ 0c** : `load_state` est désormais appelé en début de tour (`from_dict` + `reset_ephemeral`). `load_state` reste aussi utilisé par le front (`GET /state`, `app.py:283`).
- **Snapshot** (`snapshot.py`) : `git add -A` commit TOUT le dossier racine → `state.json` + fichiers `plan_<id>.md`/`todo_<id>.json` + `events.jsonl` versionnés ENSEMBLE. **INVARIANT** : doivent rester dans le snapshot (sinon un `revert` laisse des références obsolètes dans le state). OK par construction (`git reset --hard`/`git archive`). `_REPO_GITIGNORE` n'exclut que `workspace/.thumbs/` + `*.tmp`.
- **Front** : lit **uniquement** `st.plan_mode` aujourd'hui (`conversations.js:149`, fallback legacy) → le référent étend la forme de `/state` ; le front lira phase/plans/liens (Phase 1.7).
- **Hors-scope état** : `conversation.md` (`persistence.py:56`) + `artifacts/` (`write_artifact:46`) = journal lisible + outputs LLM (pas de l'état orchestrateur).

## G. Faits organisationnels → site d'inscription → event existant (pour le filet `rebuild_from_events`)

| Fait | Site | Event existant | Manque |
|---|---|---|---|
| turn start | `orchestrator_v2.py` (RequestStarted emit) | `RequestStarted` | pas de `request_id` |
| turn end | conclusions (final / fallback vide / halte PLAN / aborted) | `RequestCompleted` (sauf aborted/cancel/llm-fail) | events manquants sur abort |
| plan proposé | halte PLAN + `plan_write` | — | domain event `PlanProposed` |
| plan accepté/superseded | reconcile (`todo.py:148/150`) | — | `PlanAccepted`/`PlanSuperseded` |
| todo create/update/clear | outils todo + conclusion EDIT | — | `TodoSnapshot` |
| subagent spawn/return | spawn/return | `DelegationStarted`/`Completed` | pas de `request_id` sur l'event |
| fichiers produits | writes workspace + `report_back.files_produced` | (liste dans DelegationCompleted) | `FileProduced` |
| phase | implicite (`plan_mode`+`plan_status`) | — | `PhaseChanged` |

⇒ **Filet** : ajouter `request_id`/`parent_request_id` sur les events + domain events ci-dessus, puis `rebuild_from_events(events) -> state` ; test **« maintenu == reconstruit »** = garde-fou anti-drift (prouve qu'aucun site d'écriture n'a été oublié).

---

## Checklist Phase 1 (ordre de dépendances)
1. ⬜ `request_id` stable (boucle + events).
2. ⬜ Inscrire `requests[]` + `phase` (début/fin de tour, transitions).
3. ⬜ Inscrire `plans` + `todos` (découplés) ; migrer `plan_status.json` → state ; supprimer le sidecar.
4. ⬜ Inscrire `subagents[]` + `files[]` (layer + producteur).
5. ⬜ Basculer les lectures D vers le state (has_plan/has_todo/_count_delegations/budget).
6. ⬜ `rebuild_from_events` + domain events + test « maintenu == reconstruit ».
7. ⬜ Front : `/state` expose le référent (phase/plans/liens).
