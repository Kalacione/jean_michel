# Plan — Refonte du système de mémoire : scopes, FTS, projets, consolidation

## Context

Le système de mémoire actuel (`user_memory`) ne sert qu'aux données utilisateur :
un `type ∈ {user,feedback,project,reference}` à plat qui mélange sémantique et
portée, un rappel par `code` exact, une injection user-scoped uniquement. On veut
en faire une mémoire long-terme multi-portée pour Jean-Michel.

Décisions verrouillées avec l'utilisateur (2026-06-08) :
- **Scope unique** comme dimension structurante : `world | user | project | tool`.
  On abandonne le `type` à plat (feedback/reference se replient sur le contenu).
- **Recherche FTS5 + ranking BM25** (déterministe, top-K + seuil), pas d'embeddings
  en v1.
- **Projets** : table dédiée, association **un-à-plusieurs** (1 projet → N convs ;
  une conv a 0 ou 1 projet), gérables et sélectionnables depuis le frontal web.
- **Consolidation en shadow** : l'analyse de proposition tourne **après l'émission
  de la réponse, à chaque tour**, en tâche de fond (non bloquante) — pendant que
  l'utilisateur lit/réfléchit, Jean-Michel introspecte. Recommandations surfacées
  **en fin de tour** (UX dédiée), revue de rattrapage en fin de conversation. Pas
  encore de détection au fil du raisonnement intra-tour.

**Principe directeur — déterminisme.** L'inclusion des mémoires dans les prompts est
**100 % déterministe** (pur filtre SQL par scope). Le LLM n'intervient QUE dans la
*proposition* de mémorisation (consolidation), qui est (a) human-in-the-loop, (b)
ancrée sur une **citation source vérifiée par string-match** — toute proposition non
ancrée est rejetée déterministiquement (anti-hallucination). La dédup/contradiction
au moment du save est de la **récupération FTS déterministe** présentée à l'humain,
jamais un jugement LLM.

**Frontière avec les paradigmes (à ne PAS dupliquer).** Les paradigmes = directives
comportementales statiques, écrites par le dev en migration, bindées via
`agent_paradigms`, rendues sous `# DIRECTIVES`. La mémoire `tool` = connaissance
opérationnelle *apprise*, éditable au runtime via l'outil mémoire, recherchable en
FTS, rendue sous `## Tool notes` dans `# CONTEXT`. Cycle de vie et provenance
différents. La mémoire `world` est globale (proche d'un paradigme global mais
éditable/curée à chaud, pas figée en migration).

---

## Modèle de données cible

### Table `memory` (généralise `user_memory`)
```sql
CREATE TABLE memory (
  id          INTEGER PRIMARY KEY,
  scope       TEXT NOT NULL CHECK (scope IN ('world','user','project','tool')),
  user_id     INTEGER REFERENCES web_users(id) ON DELETE CASCADE,  -- requis si scope='user'
  project_id  INTEGER REFERENCES projects(id)  ON DELETE CASCADE,  -- requis si scope='project'
  tool_code   TEXT,                                                -- requis si scope='tool'
  code        TEXT NOT NULL,
  title       TEXT NOT NULL,           -- <60
  description TEXT NOT NULL,           -- <150, injecté dans l'index prompt
  content     TEXT NOT NULL,           -- <1000, markdown, chargé via recall
  created_at  TEXT NOT NULL,
  modified_at TEXT NOT NULL
);
-- Unicité par cible de scope (index partiels : NULL-safe, déterministe)
CREATE UNIQUE INDEX ux_memory_world   ON memory(code)             WHERE scope='world';
CREATE UNIQUE INDEX ux_memory_user    ON memory(user_id, code)    WHERE scope='user';
CREATE UNIQUE INDEX ux_memory_project ON memory(project_id, code) WHERE scope='project';
CREATE UNIQUE INDEX ux_memory_tool    ON memory(tool_code, code)  WHERE scope='tool';
```

### FTS5 + BM25
```sql
CREATE VIRTUAL TABLE memory_fts USING fts5(
  title, description, content, content='memory', content_rowid='id');
-- + 3 triggers (AI/AD/AU) pour synchroniser memory → memory_fts
```
Recherche : `SELECT m.* FROM memory_fts f JOIN memory m ON m.id=f.rowid
WHERE memory_fts MATCH ? [AND m.scope=? …] ORDER BY bm25(memory_fts) LIMIT K` —
cap top-K + seuil de score (rejette le bruit). Filtrage scope/cible appliqué en SQL.

### Table `projects`
```sql
CREATE TABLE projects (
  id          INTEGER PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,  -- owner
  code        TEXT NOT NULL,
  name        TEXT NOT NULL,
  description TEXT,
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at  TEXT NOT NULL,
  modified_at TEXT NOT NULL,
  UNIQUE (user_id, code)
);
```
`conversations.project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL` (nullable).

### Migrations (numéros libres après 123)
- **migrate_124** — table `projects` + `conversations.project_id` (rebuild de
  `conversations` pour la FK `SET NULL`, suivant le pattern de rebuild de migrate_113).
- **migrate_125** — `user_memory` → `memory` : ajout `scope`/`project_id`/`tool_code`,
  toutes les rows existantes → `scope='user'` (user_id préservé), drop de `type` ;
  collisions de `code` résolues en préfixant l'ancien type (`feedback-…`, etc.).
  Crée `memory_fts` + triggers + les 4 index partiels. Re-peuple `memory_fts`.
- **migrate_126** — paradigmes : renomme `user_memory_discipline` → `memory_discipline`
  (mentionne `search` avant de conclure « je ne sais pas », et les actions `note_for_*`) ;
  +1 paradigme `tool_note_discipline` (quand écrire une mémoire `tool`) bindé aux agents
  pertinents ; re-grant `manage_memory` (ex-`manage_user_memory`) à `jean-michel`.
- Refléter **chaque** changement dans `db/schema.sql` (source de vérité, cf.
  `HOWTO_ADD_SPECIALIST_OR_TOOL.md`).

---

## Phase 0 — Cœur mémoire : scope + FTS

- **`src/jeanmichel/service/memory.py`** : remplacer `VALID_TYPES` par
  `VALID_SCOPES`. Généraliser `save/recall/list_/update/delete` pour porter
  `scope` + clé de cible (`user_id`/`project_id`/`tool_code`). Ajouter
  `search(conn, *, query, scope=None, target=…, limit=K, min_score=…)` (FTS5+BM25).
  Validation centralisée (partagée tool + API + consolidation) : exiger la bonne clé
  selon le scope, rejeter les clés incohérentes.
- **`src/jeanmichel/tools/manage_user_memory.py` → `manage_memory.py`** : renommer le
  module et `name="manage_memory"`. Actions : `save`, `recall`, `search`, `list`,
  `update`, `delete` + raccourcis ergonomiques **`note_for_world` / `note_for_user` /
  `note_for_project` / `note_for_tool`** (sucre au-dessus de `save` avec scope figé).
  `make_spec(user_id, project_id)` capture le contexte de la conversation pour les
  cibles `user`/`project`.
- **`src/jeanmichel/tools/__init__.py:build_registry`** : passer `project_id` (depuis
  la conv) en plus de `memory_user_id` au `make_spec`.
- **`src/jeanmichel/bootstrap.py`** : l'entrée bootstrap devient `scope='user'`,
  `code='personal-profile'` (inchangé fonctionnellement).
- **Tests** `tests/v2/test_user_memory.py` → `test_memory.py` : scopes, index partiels
  (unicité par cible), FTS search + ordre BM25 + seuil, raccourcis `note_for_*`.

## Phase 1 — Inclusion déterministe dans les prompts

- **`src/jeanmichel/prompts.py`** : `render_user_memory_index` →
  `render_memory_block(conn, *, user_id, project_id, tool_codes)` qui assemble par
  scope, en **index** (code : description, jamais le content) :
  - `## World knowledge` — tout `scope='world'` (cap, warning).
  - `## Known facts about the user` — `scope='user' AND user_id=?` (cap).
  - `## Project context` — si `project_id`, `scope='project' AND project_id=?` (cap).
  - `## Tool notes` — `scope='tool' AND tool_code IN (tool_codes)` (cap par outil).
  Hint de rappel : `manage_memory(action='recall'|'search', …)`.
- **`src/jeanmichel/orchestrator_v2.py:load_agent_spec_v2`** (~926-1023) : c'est le
  bon point d'ancrage — `tool_grants` y est déjà résolu (~968). Calculer le bloc
  mémoire **ici** (pas dans le caller) à partir de `user_id` + `project_id` (nouveaux
  params) + `tool_grants`, puis le passer à `render_system_prompt_v2`. Ainsi les
  mémoires `tool` se chargent automatiquement pour **tout** agent (main ET subagent
  via `spawn_subagent`) qui a l'outil granté — exactement le besoin exprimé.
- **`src/jeanmichel/service/turn_runner.py:_run_deep_turn`** (~229) : propager
  `project_id` (lu sur la conv) à `load_agent_spec_v2`. Le re-render au `--resume`
  (~309) reste valide.
- Caps de budget par scope + extension du warning « near capacity » existant.
- **Tests** : bloc déterministe par scope ; mémoire `tool` injectée ssi l'agent a
  l'outil granté ; mémoire `project` injectée ssi la conv a un projet.

## Phase 2 — Projets

- **DB** : `src/jeanmichel/db.py` — helpers `create_project`, `list_projects_for_user`,
  `get_project`, `update_project`, `delete_project`, `set_conversation_project`.
  Étendre `create_conversation`/`get_conversation` pour `project_id`.
- **Modèle** : `src/jeanmichel/models.py:Conversation` (+`project_id: int | None`).
- **Service** : `src/jeanmichel/service/project.py` (CRUD), wiring dans
  `service/conversation.py:create_conversation` (accepte `project_id`).
- **CLI** : `src/jeanmichel/cli.py` — flag `--project <code>` à la création/reprise.
- **API** : `src/jeanmichel/api/app.py` — routes `/api/projects` (GET/POST/PATCH/DELETE,
  user-scoped via `current_user`) ; `project_id` accepté à `POST /api/conversations`
  et `PATCH /api/conversations/{id}`.
- **Web** : `web/src/components/` — `ProjectsDialog.vue` (gestion CRUD) + sélecteur de
  projet à la création d'une conversation (dans `ConversationsDrawer.vue` /
  `MainLayout.vue`) ; store Pinia `projects.js`.
- **Tests** : CRUD projets, association/désassociation, `ON DELETE SET NULL`,
  isolation par user.

## Phase 3 — Consolidation en shadow (proposition human-in-the-loop)

### Moteur (déterministe + ancrage)
- **`src/jeanmichel/service/consolidation.py`** :
  - `propose(messages, *, llm, user_id, project_id) -> list[Candidate]` : appel LLM
    **contraint** (schema JSON strict) renvoyant des candidats
    `{scope, code, title, description, content, grounding_quote}`.
  - **Validation déterministe** : pour chaque candidat, vérifier que
    `grounding_quote` est bien un substring (normalisé) d'un message de la conv →
    **drop** sinon (anti-hallucination). Puis `memory.search` dans le scope/cible
    cible → attacher les top matches BM25 (doublon potentiel → proposer *extend* ;
    contradiction → présenter côte à côte). Dédup contre les candidats déjà proposés
    ce tour-ci / déjà persistés. Rien n'est écrit ici.
  - `apply(candidate, decision)` → `memory.save`/`update` après confirmation.
  - Persistance des candidats en attente : `conv_folder/pending_memory.json`
    (accumulés tour après tour, vidés quand traités).

### Déclenchement en SHADOW (après la réponse, non bloquant)
Point d'ancrage : **`src/jeanmichel/service/turn_runner.py:run_turn`**, juste **après**
que la réponse est produite/persistée et le snapshot de fin de tour émis (~205) — la
réponse est déjà partie chez l'utilisateur. Le pass de consolidation tourne alors en
arrière-plan, jamais dans le chemin critique de la réponse. Best-effort (ne lève jamais).
- **Gating** : uniquement sur les tours **DEEP** (les tours ALEXA single-fact n'ont
  rien à mémoriser) → un appel LLM contraint supplémentaire par tour DEEP seulement.
- **CLI** : `src/jeanmichel/cli.py` — lancer `propose(...)` dans un **thread de fond**
  (`concurrent.futures`) après l'affichage de la réponse ; le résultat est *stashé*
  dans `pending_memory.json`. Scope user = `cli_user_id`. Si l'utilisateur enchaîne un
  tour avant la fin, laisser terminer et empiler (pas d'annulation nécessaire).
- **API/web** : surface async-native. Après le `RequestCompleted` du WebSocket,
  déclencher une tâche de fond (`asyncio`/`BackgroundTasks`) qui appelle `propose(...)`
  et émet un nouvel event WebSocket **`MemoryConsolidationProposed`** (candidats +
  matches existants). Aucune écriture serveur sans confirmation client.
- **À la demande** : `POST /api/conversations/{id}/consolidate` (force un pass complet
  sur tout l'historique) pour rattrapage manuel.

### UX des recommandations en fin de tour
- **CLI** : après la réponse (et une fois le shadow prêt), afficher un bloc Rich
  discret — `💡 N élément(s) à mémoriser` listant `[scope] titre — description`.
  Commande légère de revue (ex. `/memo` ou raccourci) ouvrant un mini-flux
  accept / éditer / extend (vers un match existant) / ignorer, item par item. Ce qui
  n'est pas traité reste dans `pending_memory.json` et est re-proposé en fin de conv.
- **Web** : `EventTrace.vue` consomme `MemoryConsolidationProposed` → badge non
  intrusif (« N suggestions mémoire ») ; nouveau `MemoryReviewDialog.vue` présentant
  chaque candidat avec sa **citation source** (grounding), la comparaison côte à côte
  avec les matches FTS existants (doublon → bouton *Étendre* ; contradiction →
  surlignage), et accept/edit/reject. Application via `POST/PATCH /api/memory`.
  Les suggestions en attente persistent entre tours (badge cumulatif).
- **Event typé** `MemoryConsolidationProposed` ajouté à `src/jeanmichel/events.py`
  (11 → 12 classes ; mis à jour côté CLI live + `events.jsonl` + front).

### Tests
- Candidat non ancré rejeté ; doublon détecté → suggestion *extend* ; contradiction
  surfacée ; aucune écriture sans confirmation ; le shadow ne bloque jamais la réponse
  (le pass tourne après `run_turn` retour) ; ALEXA ne déclenche pas de pass ; les
  candidats en attente survivent à plusieurs tours dans `pending_memory.json`.

## Hors périmètre v1 (phases futures)
- Détection **intra-tour** au fil du raisonnement (le shadow v1 est post-réponse, par tour).
- Couche **embeddings / RAG sémantique** (si FTS5/BM25 se révèle insuffisant).
- Politique d'autorisation d'écriture `world`/`tool` en multi-utilisateur web
  (aujourd'hui ces scopes sont globaux/partagés — **point à trancher** avant
  d'ouvrir l'écriture `world`/`tool` côté web ; le CLI mono-user n'est pas concerné).

---

## Principes transverses
- Validation **centralisée** dans `service/memory.py` (tool + API + consolidation).
- Tout changement DB ⇒ migration `migrate_NNN_*.sql` **et** miroir dans `db/schema.sql`.
- Paradigmes **model-agnostic**, contenu en anglais ; `rationale` = note dev.
- Tests via `.venv/bin/python` exclusivement.
- Le renommage `manage_user_memory → manage_memory` et `user_memory → memory` est
  assumé (nom honnête vs nom trompeur) : mettre à jour grants `agent_tools`, refs
  Python, README, et le composant web `MemoryDialog.vue`.

## Vérification end-to-end
- `.venv/bin/python -m pytest tests/v2/test_memory.py tests/v2/test_schema_v2.py
  tests/v2/test_migration_idempotence.py -v`
- Migration : appliquer 124→126 sur une copie de `jeanmichel.db`, vérifier que les
  rows `user_memory` existantes deviennent `scope='user'` sans perte, et que
  `memory_fts` est peuplée (`SELECT count(*) FROM memory_fts`).
- Smoke CLI : `./jm.sh --mode chat --project demo` → écrire des `note_for_*`,
  vérifier que `## World knowledge` / `## Tool notes` apparaissent dans le prompt
  d'un agent ayant l'outil granté (via `debug/inspect_conv.py`), tester `search`
  (ordre BM25). Après une réponse DEEP, vérifier que le bloc `💡 N élément(s) à
  mémoriser` apparaît (shadow post-réponse) sans avoir retardé la réponse, et que la
  revue accepte une proposition ancrée / rejette une proposition inventée.
- Smoke web : `./jm.sh --serve` + front → créer un projet, l'associer à une nouvelle
  conversation, envoyer un tour DEEP et vérifier l'arrivée de l'event WebSocket
  `MemoryConsolidationProposed` + le `MemoryReviewDialog` ; valider/éditer un candidat ;
  tester aussi le `/consolidate` à la demande.
