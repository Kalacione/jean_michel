# 07 — Plan d'implémentation v2

> Plan opérationnel découlant de l'architecture validée en
> `06_proposition_v2.md`. Découpage en 9 phases avec dépendances
> explicites, fichiers touchés, tests à ajouter, critère de complétion
> (definition of done). Ce doc se veut exécutable — un développeur doit
> pouvoir attaquer une phase sans relire les 5 docs précédents.

## Vue d'ensemble

```
Phase 0 ── Paradigm audit (livrable papier, pas de code)
                │
                ▼
Phase 1 ── LLMClient multi-turn + events.py + persistence
                │
        ┌───────┴───────┐
        ▼               ▼
Phase 2          Phase 3
Hooks + 4-level   Dispatcher Tier 0
compaction       (granite + JSON forcé)
        │               │
        └───────┬───────┘
                ▼
Phase 4 ── Main loop + spawn_subagent + report_back + délégation imbriquée
                │
                ▼
Phase 5 ── manage_user_memory (tool + table + bootstrap toml)
                │
                ▼
Phase 6 ── Migration BDD + bascule code (drop legacy)
                │
                ▼
Phase 7 ── CLI + jm.sh adaptation
                │
                ▼
Phase 8 ── Smoke test E2E + nettoyage final
```

Les phases 1–4 sont strictement séquentielles sur les chemins critiques.
La phase 7 (CLI) peut démarrer en parallèle dès que la phase 1 (events.py)
est finie — elle ne dépend pas du main loop pour ses tests unitaires.
La phase 0 est indépendante : elle peut être faite en début ou en
chevauchement avec la phase 1, l'important est que sa migration soit
prête avant la phase 6.

## Conventions

- **Une phase = un PR**. Mergé sur `revolucion` seulement quand la
  definition of done est satisfaite et que le critère de tests verts est
  atteint.
- **Tests v2 = suite nouvelle dans `tests/v2/`** (cf. §12 de 06).
  Les anciens tests `tests/*.py` restent à leur place jusqu'à la phase 8
  où on les supprime — sauf ceux qu'on a explicitement cueillis et
  adaptés (mention dans la phase concernée).
- **Pas d'amend ni de force-push** sur les commits mergés.
- **Chaque phase doit pouvoir tourner en isolation** via MockClient —
  pas de dépendance Ollama pour les tests.

---

## Phase 0 — Audit des paradigmes BDD

**Objectif** : produire le tableau exhaustif paradigme-par-paradigme
décrit en `06_proposition_v2.md §11 bis`, et rédiger la migration SQL
de nettoyage (sans l'exécuter).

**Livrable** : deux fichiers, aucun code Python.

1. `DevNotes/REVOLUCION/08_paradigm_audit_table.md` — tableau Markdown
   avec une ligne par paradigme : `paradigm_id | code | classe (A-F) |
   décision (keep|edit|rewrite|delete|merge) | nouveau contenu si
   réécriture | justification`. Source des paradigmes existants :
   `db/schema.sql` + toutes les migrations `db/migrations/migrate_*.sql`.
2. `db/migrations/migrate_100_paradigm_realignment.sql` — la migration
   SQL qui découle du tableau, idempotente comme les autres migrations
   du projet.

**Méthode** :

- Exporter la liste des paradigmes actifs en BDD :
  `sqlite3 jeanmichel.db "SELECT id, code, content FROM paradigms WHERE active=1 ORDER BY id;"`
- Pour chaque entrée, appliquer la grille des 5 critères de qualité
  (§11 bis de 06). Classer en A/B/C/D/E/F.
- Classes A, B, C, D : décision par défaut `keep` (sauf cas particulier
  signalé en justification).
- Classe E : décision `keep-with-edits` ou `rewrite` selon l'écart.
- Classe F : décision `delete`.
- Ajouter les 5 paradigmes nouveaux (cf. §11 bis tableau "Paradigmes
  nouveaux à introduire").

**Tests** : aucun (livrable documentaire).

**Critère de complétion** :
- Tableau complet, aucune ligne sans décision.
- Migration SQL relue par au moins une seconde personne (review humain
  obligatoire — c'est le cœur des prompts de tous les agents).
- Le fichier `migrate_100_*.sql` est valide syntaxiquement (`sqlite3 :memory:
  < db/migrations/migrate_100_*.sql` doit s'exécuter sans erreur sur une
  base fraîche issue de `db/schema.sql`).

**Dépendances** : aucune.

---

## Phase 1 — LLMClient multi-turn + module events + persistence

**Objectif** : fonder l'infrastructure de base. Un LLMClient qui accepte
`messages[]` natif Ollama, un module `events.py` qui définit les
dataclasses d'événements, et une couche de persistence qui sait
sérialiser/désérialiser `messages.json`, `state.json`, `events.jsonl`,
`subagent_<id>.json`.

**Fichiers** :

- `src/jeanmichel/llm.py` — refactor :
  - `chat(*, model: str, messages: list[dict], tools: list[dict],
    temperature: float, thinking: bool, format: str | None = None) -> LLMResponse`
  - Suppression de l'ancien `chat(system=, user=, ...)` (ou maintien en
    deprecated wrapper qui appelle le nouveau).
  - Ajout du paramètre `format="json"` pour le dispatcher.
- `src/jeanmichel/events.py` — nouveau module. Dataclasses pour les 11
  events listés en §6 bis du doc 06. Chacune avec un `to_dict()` pour
  sérialisation events.jsonl.
- `src/jeanmichel/persistence.py` — refactor :
  - `save_messages(conv_folder, messages: list[dict])` — atomic write
    de `messages.json`.
  - `load_messages(conv_folder) -> list[dict]`
  - `save_state(conv_folder, state: ConversationState)` / `load_state`
  - `append_event(conv_folder, event)` — append-only à `events.jsonl`.
  - `save_sub_messages(conv_folder, request_id, sub_messages)` —
    `subagent_<request_id>.json`.

**Tests** dans `tests/v2/test_llm_client.py`, `tests/v2/test_events.py`,
`tests/v2/test_persistence.py` :

- `MockClient` adapté à la nouvelle signature `chat(messages=...)`.
- Roundtrip serialisation/désérialisation pour chaque event.
- `events.jsonl` reste valide JSONL après 1000 appends concurrent-safe
  (utiliser un lock fichier ou un append atomique).
- `messages.json` survit à un crash simulé (write atomique via
  tempfile + rename).

**Critère de complétion** :
- `llm.chat(messages=[{role:"user",content:"hello"}], ...)` retourne
  un `LLMResponse` valide via MockClient.
- Les 11 events ont chacun un test de sérialisation.
- `tests/v2/` pytest verts.

**Dépendances** : aucune.

---

## Phase 2 — Hooks framework + 4-level compaction

**Objectif** : implémenter les 4 hooks décrits en §7 du doc 06, et
l'escalade de compaction Snip → Microcompact → Collapse → Autocompact.

**Fichiers** :

- `src/jeanmichel/hooks.py` — nouveau module :
  - `Hook` ABC + 4 implémentations concrètes (`PreLLMCall`, `PreToolUse`,
    `PostToolUse`, `OnDelegateReturn`).
  - `Decision` dataclass pour le retour de `PreToolUse`.
  - Registre des hooks (dict typé) chargé via `config.py`.
- `src/jeanmichel/compaction.py` — nouveau module :
  - `compact_snip(messages, state)`
  - `compact_microcompact(messages, state)` — détecte tools
    microcompactables, remplace `content` par stub si > 1500 tokens.
  - `compact_collapse(messages, state)` — appel LLM (compactor model)
    sur la fenêtre du milieu.
  - `compact_autocompact(messages, state)` — appel LLM sur tout le milieu.
  - La fonction d'escalade qui choisit le niveau selon
    `working_tokens_used / working_budget`.
- `src/jeanmichel/config.py` — ajout des constantes
  `COMPACTION_THRESHOLDS`, `MICROCOMPACT_TOKEN_THRESHOLD`,
  `COMPACTOR_MODEL` + lecture env vars correspondantes.

**Tests** dans `tests/v2/test_hooks.py`, `tests/v2/test_compaction.py` :

- Chaque hook isolément : input → effet attendu, sans exécution réelle
  de tool ni de LLM.
- Snip : ne touche pas le system message, drop les nudges honorés.
- Microcompact : remplace bien le content > seuil par un stub, conserve
  les < seuil tels quels.
- Collapse : utilise MockClient pour simuler l'appel compactor, vérifie
  que les `report_back` returns sont préservés.
- Autocompact : ramène le messages[] à ≤ 30 % d'occupation simulée.
- Escalade : pour un ratio donné, seul le bon niveau (+ ceux en dessous)
  est appelé.

**Critère de complétion** :
- Pour un `messages[]` de 50 turns simulés à 85 % du WORKING,
  l'escalade descend bien à compact_collapse.
- Les 4 fonctions sont individuellement testées.

**Dépendances** : Phase 1.

---

## Phase 3 — Dispatcher Tier 0

**Objectif** : implémenter la porte d'entrée du système — classification
ALEXA/DEEP via granite4.1:8b avec JSON forcé, plus l'exécution directe
des tools ALEXA.

**Fichiers** :

- `src/jeanmichel/dispatcher.py` — nouveau module :
  - `classify(user_text: str, llm: LLMClient) -> DispatchDecision`
  - `DispatchDecision` dataclass : `intent`, `tool`, `args`, `confidence`.
  - Validation JSON Schema du retour granite.
  - 1 retry sur parse fail, puis fallback DEEP.
  - `execute_alexa(decision, llm, user_lang) -> str` — exécute le tool
    natif, formate la réponse via template Python pour `clock`/`weather`,
    ou un second appel granite court pour `wikipedia_search`.
- `src/jeanmichel/config.py` — ajout `DISPATCH_MODEL` + lecture env
  `JEANMICHEL_DISPATCH_MODEL`.
- `src/jeanmichel/prompts.py` — ajout du prompt statique dispatcher
  (cf. §3 doc 06, recopié verbatim).

**Tests** dans `tests/v2/test_dispatcher.py` :

- Parse JSON valide : intent=alexa + tool valide → DispatchDecision OK.
- Parse JSON valide : intent=deep → DispatchDecision OK.
- Parse JSON invalide : retry once, puis fallback DEEP.
- Tool inconnu : fallback DEEP.
- Exécution ALEXA `clock` : retour formaté dans la langue de l'utilisateur.
- Exécution ALEXA `weather` : appel tool + format Python.
- Exécution ALEXA `wikipedia_search` : appel tool + second granite call
  via MockClient.

**Critère de complétion** :
- Tous les tests verts.
- Sur un appel direct (sans Ollama, via MockClient) : "quelle heure
  est-il ?" → intent=alexa + clock → réponse formatée.

**Dépendances** : Phase 1.

---

## Phase 4 — Main loop + spawn_subagent + délégation imbriquée

**Objectif** : implémenter le cœur — le main loop Tier 1, le
`spawn_subagent` Tier 2 avec délégation imbriquée jusqu'à `MAX_DEPTH`,
et le tool `report_back`.

**Fichiers** :

- `src/jeanmichel/orchestrator.py` — réécriture totale :
  - `run_main_loop(conv, user_text) -> str` — implémentation littérale du
    pseudo-code §4 doc 06, ≤ 50 lignes effectives.
  - `spawn_subagent(caller, agent_code, briefing, depth) -> SubResult` —
    seule fonction qui instancie un nouveau `messages[]`.
  - Boucle subagent : version paramétrée de `run_main_loop` qui sait
    qu'elle doit terminer par `report_back`.
  - Ancien `_run_request` archivé dans `orchestrator_legacy.py` (à
    supprimer en phase 8).
- `src/jeanmichel/tools/delegate_to.py` — implémentation du tool, qui
  appelle `spawn_subagent`.
- `src/jeanmichel/tools/report_back.py` — implémentation du tool, qui
  valide le schéma (notamment `low_confidence_reason` obligatoire si
  confidence=low) et arrête la boucle du subagent.
- `src/jeanmichel/prompts.py` — ajout d'un rendu prompt pour le subagent
  (ajout des sections `Inbound briefing` + obligation de terminer par
  `report_back`).
- `src/jeanmichel/config.py` — ajout `MAIN_MODEL`, `SUBAGENT_DEFAULT_MODEL`,
  `MAX_DEPTH`, `MAX_SEARCH_CALLS_PER_TURN`, `WALL_CLOCK_TURN_SECONDS`.

**Tests** dans `tests/v2/test_main_loop.py`, `tests/v2/test_subagent.py` :

- DEEP path simple : un tour, le main agent émet un assistant sans
  tool_calls → return content. Vérifier persistence `messages.json`.
- DEEP avec tool natif (web_search) : un tool_call, exécution, append
  role=tool, suivi d'un assistant final.
- DEEP avec délégation niveau 1 : main → delegate_to → subagent →
  report_back → main reprend. Vérifier que le subagent a un messages[]
  vide d'historique caller.
- DEEP avec délégation niveau 2 (imbriquée) : main → sub A → sub B →
  report_back A → A continue → report_back main. Vérifier la profondeur
  trackée + persistence `subagent_*.json`.
- MAX_DEPTH refus : tentative de spawn au-delà → deny via PreToolUse.
- `report_back` avec confidence=low sans low_confidence_reason : refus,
  re-tentative.
- Whitelist `agent_delegation_targets` : agent A tente delegate vers B
  hors whitelist → deny.

**Critère de complétion** :
- Un cas E2E "compare X et Y" (via MockClient scripté) traverse :
  dispatcher → main → 2 delegations parallèles → synthèse → reply.
- `run_main_loop` fait ≤ 60 lignes effectives (legging up the +10 lignes
  pour les imports et docstring).

**Dépendances** : Phases 1, 2, 3.

---

## Phase 5 — `manage_user_memory` (tool + table + bootstrap)

**Objectif** : implémenter la mémoire long-terme utilisateur décrite en
§10 doc 06.

**Fichiers** :

- `db/migrations/migrate_101_user_memory.sql` — création de la table
  `user_memory` + index.
- `src/jeanmichel/tools/manage_user_memory.py` — nouveau tool, un seul,
  multi-action (save/recall/list/update/delete).
- `src/jeanmichel/prompts.py` — extension du rendu `## Human` block pour
  prepend l'index des entrées user_memory (limité aux 100 plus récentes,
  warning event si ≥ 90).
- `src/jeanmichel/bootstrap.py` — nouveau module (ou ajout à
  `config.py`) qui lit `user_profile.toml` au premier démarrage et crée
  une entrée `user / personal-profile` si la table est vide.
- Migration de seed : ajouter `jean-michel` à `agent_tools` avec
  `tool_code='manage_user_memory'`.
- Migration de seed : ajouter le paradigme `user_memory_discipline`
  (cf. §11 bis doc 06) + binding sur jean-michel.

**Tests** dans `tests/v2/test_user_memory.py` :

- CRUD basique : save → list → recall → update → delete.
- Unicité `(type, code)` : second save avec même clé échoue, suggère
  update.
- Index dans le system prompt : présent + bien tronqué à 100 entrées.
- Event `MemoryNearCapacity` émis à 90 entrées.
- Bootstrap depuis `user_profile.toml` : description toml → entrée user.

**Critère de complétion** :
- Migration applicable sur base v2 fraîche sans erreur.
- Tool appelable via MockClient avec les 5 actions.
- Bootstrap idempotent : 2 démarrages successifs ne créent qu'une seule
  entrée bootstrap.

**Dépendances** : Phase 4 (le tool est utilisé par le main agent).

---

## Phase 6 — Migration BDD + bascule code

**Objectif** : appliquer la migration de nettoyage des paradigmes
(Phase 0), virer les tables BDD obsolètes (cf. §9 doc 06), ajouter la
colonne `agents.model_override`, et basculer le code legacy vers le code
v2.

**Fichiers / opérations** :

- Application de `db/migrations/migrate_100_paradigm_realignment.sql`
  (Phase 0).
- Nouvelle migration `db/migrations/migrate_102_drop_runtime_tables.sql` :
  - `DROP TABLE requests, artifacts, conversation_phases, sandbox_executions;`
  - `ALTER TABLE agents ADD COLUMN model_override TEXT NULL;`
  - Cleanup de `agent_delegation_targets` (re-seed une whitelist
    raisonnable par défaut, vide aujourd'hui).
- `db/schema.sql` consolidé : régénéré pour refléter le schéma final v2
  (ne plus contenir les tables virées, contenir `user_memory`).
- Suppression des modules legacy :
  - `src/jeanmichel/plan_writer.py` (ou laissé en helper de rendu à la
    demande, à décider à ce moment).
  - Imports/exports legacy dans `orchestrator_legacy.py` archivé en
    Phase 4.
- Suppression des tools devenus inutiles côté code :
  `set_task_class`, `manage_todo_list`, `signal_convergence`,
  `report_findings` (au sens main agent), `planner_done`, `gather_done`,
  `critic_done`, `build_done`, `return_to_user`. Leur enregistrement
  dans `build_registry` disparaît.
- Suppression du `running_user_text` reconstruit, `render_plan_recap`,
  et des 5 gates inlines dans le code archivé.

**Tests** :

- `tests/v2/test_schema_v2.py` : vérifie qu'aucune référence aux tables
  virées ne subsiste dans le code Python source (grep AST simple).
- `tests/v2/test_migration_idempotence.py` : appliquer 100 puis 101 puis
  102 sur une base v1 fraîche → schéma final v2 correct.
- `tests/v2/test_no_orphan_paradigms.py` : vérifie qu'aucun
  `agent_paradigms` ne pointe vers un paradigme supprimé.

**Critère de complétion** :
- `./jm.sh --install` sur un dossier vierge produit une base v2 propre.
- Aucun test v2 ne fait référence à `requests` / `artifacts` /
  `conversation_phases` / `sandbox_executions`.
- L'agent `archivist` est désactivé (`active=0`) ou supprimé selon le
  retour humain.

**Dépendances** : Phase 0 (migration paradigmes), Phase 5 (table
user_memory existe).

---

## Phase 7 — CLI + jm.sh adaptation

**Objectif** : adapter les deux points d'entrée utilisateur conformément
au §11 ter du doc 06.

**Fichiers** :

- `src/jeanmichel/cli.py` — refactor selon §11 ter B :
  - Suppression des imports d'events obsolètes (TodoListUpdated,
    SignalConvergenceRedirected, SoftDeadlineReached, ForcedConvergence,
    ReportFindingsReceived, SummaryUpdated).
  - Ajout des nouveaux imports depuis `src/jeanmichel/events.py`.
  - Remplacement de `_render_todo_panel` par `_render_delegation_tree`.
  - `_prewarm` étendu pour warmer dispatcher + main + compactor.
  - Argument `--main-model` et `--dispatch-model`, `--model` deprecated.
  - `--resume` lit `messages.json` + `state.json`.
- `jm.sh` :
  - `--inspect-conv` : refonte pour lire `messages.json` + `events.jsonl`
    + `subagent_*.json` (réécrire `debug/inspect_conv.py`).
  - `--meta-analysis` : prompt corrigé pour appeler
    `self_inspect_config` / `self_inspect_activity` /
    `self_inspect_architecture` au lieu de `self_inspect(scope=...)`
    inexistant.
- `debug/inspect_conv.py` : réécriture pour traverser le filesystem
  per-conv au lieu d'interroger `requests` / `artifacts`.

**Tests** dans `tests/v2/test_cli_rendering.py` :

- Snapshot test : pour chaque event v2, vérifier que le rendu CLI
  match un golden text attendu.
- `--resume` end-to-end via MockClient : un crash simulé après 3 tours,
  puis `--resume`, doit reprendre au tour 4 avec messages chargés.
- `_prewarm` avec MockClient qui simule l'absence de granite : log
  warning mais ne crash pas.

**Critère de complétion** :
- `./jm.sh --inspect-conv <id>` produit une sortie lisible sur une
  conversation v2.
- CLI rendering survit à un crash CLI pendant que l'orchestrateur tourne
  (replay events.jsonl).

**Dépendances** : Phase 1 (events.py).
Peut démarrer en parallèle avec les phases 2-6 dès que la phase 1
est finie.

---

## Phase 8 — Smoke test E2E + nettoyage final

**Objectif** : valider que le système v2 fonctionne de bout en bout sur
Ollama réel, retirer le code legacy archivé, mettre à jour la doc.

**Actions** :

- `tests/v2/test_smoke_e2e.py` (avec décorateur `pytest.mark.requires_ollama`,
  skipped en CI sans Ollama) : démarre une conversation, pose une
  question ALEXA simple, vérifie la réponse. Pose une question DEEP
  avec une délégation, vérifie le `report_back` et la réponse finale.
- Suppression définitive :
  - `src/jeanmichel/orchestrator_legacy.py` (archivé en Phase 4).
  - Anciens tests `tests/*.py` non cueillis vers `tests/v2/`.
  - Anciens tools inutilisés (cf. Phase 6).
- Mise à jour `README.md` :
  - Section "Concept" reformulée pour Tier 0/1/2 + hooks + user_memory.
  - Section "Modes" reformulée (vocal devient option TTS).
  - Section "Persistance" reformulée (messages.json + state.json +
    events.jsonl per-conv).
  - Section "Stack" : pas de changement notable.
- Mise à jour `docs/PROMPT_SKELETON.md` pour refléter le nouveau rendu
  prompt (user_memory index inclus, plus de plan.md injection).
- Tag git `v2.0.0` sur la branche `revolucion`, prêt pour merge sur
  `main`.

**Critère de complétion** :
- Sur ta machine de référence (2x GV100) :
  - Une session "quelle heure est-il ?" → réponse en < 2 s.
  - Une session "compare Python 3.14 vs Rust 1.85 pour les match
    patterns" → délégation, recherches, synthèse, réponse cohérente.
- Test smoke vert avec Ollama réel.
- README et docs alignés avec le code.
- Pas de référence au code legacy dans `src/jeanmichel/`.

**Dépendances** : toutes les phases précédentes.

---

## Récap des fichiers créés vs touchés

| Type      | Fichier                                                   | Phase |
|-----------|-----------------------------------------------------------|-------|
| nouveau   | `DevNotes/REVOLUCION/08_paradigm_audit_table.md`          | 0     |
| nouveau   | `db/migrations/migrate_100_paradigm_realignment.sql`      | 0     |
| nouveau   | `src/jeanmichel/events.py`                                | 1     |
| modifié   | `src/jeanmichel/llm.py`                                   | 1     |
| modifié   | `src/jeanmichel/persistence.py`                           | 1     |
| nouveau   | `src/jeanmichel/hooks.py`                                 | 2     |
| nouveau   | `src/jeanmichel/compaction.py`                            | 2     |
| modifié   | `src/jeanmichel/config.py`                                | 2-5   |
| nouveau   | `src/jeanmichel/dispatcher.py`                            | 3     |
| modifié   | `src/jeanmichel/prompts.py`                               | 3,4,5 |
| modifié   | `src/jeanmichel/orchestrator.py`                          | 4     |
| nouveau   | `src/jeanmichel/tools/delegate_to.py`                     | 4     |
| nouveau   | `src/jeanmichel/tools/report_back.py`                     | 4     |
| nouveau   | `db/migrations/migrate_101_user_memory.sql`               | 5     |
| nouveau   | `src/jeanmichel/tools/manage_user_memory.py`              | 5     |
| nouveau   | `src/jeanmichel/bootstrap.py`                             | 5     |
| nouveau   | `db/migrations/migrate_102_drop_runtime_tables.sql`       | 6     |
| modifié   | `db/schema.sql` (consolidé v2)                            | 6     |
| supprimé  | `src/jeanmichel/plan_writer.py` (ou refactoré)            | 6     |
| modifié   | `src/jeanmichel/cli.py`                                   | 7     |
| modifié   | `jm.sh` (--inspect-conv, --meta-analysis)                 | 7     |
| modifié   | `debug/inspect_conv.py`                                   | 7     |
| nouveau   | `tests/v2/` (suite complète)                              | 1-8   |
| modifié   | `README.md`, `docs/PROMPT_SKELETON.md`                    | 8     |

## Estimation indicative

Pour donner un ordre d'idée (à valider en cours de route) :

| Phase | Charge estimée    | Risque                                                              |
|-------|-------------------|---------------------------------------------------------------------|
| 0     | 1-2 jours         | Faible — travail papier, mais relecture critique requise            |
| 1     | 2-3 jours         | Moyen — API LLMClient change, beaucoup d'impacts en aval            |
| 2     | 2-3 jours         | Moyen — la compaction est subtile, surtout collapse/autocompact     |
| 3     | 1-2 jours         | Faible — module bien borné                                          |
| 4     | 3-4 jours         | **Élevé** — c'est le cœur du système, beaucoup d'edge cases         |
| 5     | 1-2 jours         | Faible — pattern CRUD bien connu                                    |
| 6     | 1 jour            | Moyen — migration BDD irréversible, à tester sur copie d'abord      |
| 7     | 2 jours           | Faible — pattern de refactor mécanique                              |
| 8     | 1-2 jours         | Moyen — premier vrai contact avec Ollama, surprises possibles       |

Total ordre de grandeur : **14-21 jours-personnes**, parallélisable
partiellement (Phase 7 et certaines parties de Phase 0).

## Critères de validation globale

Avant le merge final sur `main`, vérifier que :

1. Chaque défaut listé dans `01_audit_orchestrateur.md` a une réponse
   implémentée et testée (référence croisée avec le tableau §13 du
   doc 06).
2. Chaque anti-pattern listé dans `04_audit_complementaire.md` est
   éliminé (référence croisée).
3. Aucun paradigme actif en BDD ne fait référence à un tool supprimé
   (test automatique).
4. Aucun fichier source Python n'importe depuis un module legacy
   supprimé (test automatique).
5. Le smoke test E2E avec Ollama réel passe sur deux cas représentatifs
   (ALEXA + DEEP nested).
6. La doc README + PROMPT_SKELETON est à jour.

## Prochaine étape

Validation de ce plan d'implémentation par toi. Ajustements possibles
sur :

- L'ordre / le découpage des phases.
- L'estimation indicative.
- Les fichiers touchés.
- Les critères de complétion par phase.

Une fois validé, on attaque la **Phase 0** (audit paradigmes) qui est
indépendante et déblocque tout le reste.
