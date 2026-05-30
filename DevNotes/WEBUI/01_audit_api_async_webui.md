# Audit — API (daemon) + frontal web pour Jean-Michel

> Statut : audit de cadrage validé (2026-05-29). Sert de base aux sprints d'implémentation.
> Réf. architecture v2 : `DevNotes/REVOLUCION/06_proposition_v2.md`.

## Context

Jean-Michel v2 est aujourd'hui un **assistant local piloté par CLI**, mono-utilisateur,
**synchrone et bloquant**. On veut un **frontal web** (Vue 3 + Vuetify) calqué sur
Claude/ChatGPT. Le **backend** est un **daemon Python lancé à la main** sur l'hôte
(`./jm.sh --serve`) ; **seul le frontend est conteneurisé**. Le daemon doit **s'appuyer sur
l'orchestrateur existant** et ne **rien réimplémenter**.

Fonctionnalités cibles du frontal : drawer gauche (liste + reprise des conversations),
accès aux fichiers du workspace, gestion des infos utilisateur (`user_memory` déjà en BDD),
animations d'état (« réfléchit / délègue / cherche »), fenêtre de chat avec déroulé des
**pensées** et des **opérations** de l'orchestrateur.

**Deux constats KISS qui cadrent tout :**

1. **Rien à réécrire en async.** L'appel LLM est déjà sur un `ThreadPoolExecutor`
   (`src/jeanmichel/llm.py:172`), les events sont déjà émis via un callback et déjà
   sérialisables (`event.to_dict()`, `src/jeanmichel/events.py:26`) et déjà persistés
   (`events.jsonl`). On **enveloppe** le loop synchrone dans un worker thread et on **bridge**
   les events vers le web. Réécrire 900 lignes en `async/await` = cache-misère pour zéro gain
   (le goulot est le LLM, déjà I/O-bound).
2. **Le backend daemon tourne sur l'hôte → aucun problème Docker.** `bash_sandbox` appelle
   `docker` exactement comme le CLI aujourd'hui (`src/jeanmichel/tools/bash_sandbox.py`). Pas de
   socket à monter, pas de Docker-in-Docker. La conteneurisation se limite au frontend (SPA
   statique + nginx).

## Décisions validées

| Sujet | Décision |
|---|---|
| **Déploiement** | Backend = **daemon Python à la main** (`./jm.sh --serve`). **Seul le frontend est conteneurisé** |
| **Réutilisation** | **S'appuyer sur l'orchestrateur**, zéro doublon de logique |
| **Transport** | **WebSocket bidirectionnel** (events + réponses `ask_human` sur une connexion) |
| **Mode vocal** | **TTS dans le navigateur** (synthèse Piper côté serveur, octets streamés au front) |
| **Concurrence** | **Un seul tour actif global** (Ollama = 1 GPU) ; les autres font la queue |
| **Exposition / auth** | **LAN/distant, auth ultra-simple**. Multi-user : convs associées aux users (Alice ≠ Bob). **Schéma BDD non éclaté** → table d'association `user/conversation`. Le **CLI ne crée pas d'association** → ses convs invisibles dans le web |

## Principe directeur : réutiliser l'orchestrateur, **zéro doublon**

Point capital : **`run_main_loop` accepte DÉJÀ `event_emitter` et `ask_human_callback` en
injection** (`src/jeanmichel/cli.py:502-512`). L'orchestrateur est déjà conçu pour des transports
différents. Le seul couplage CLI = ~80 lignes de **glue** dans `_run_deep_turn`
(`src/jeanmichel/cli.py:371`) + le routage alexa/deep dans `run_one_turn`
(`src/jeanmichel/cli.py:269`). Le reste (`render_event`, spinner `console.status`, `make_ask_human`
prompt_toolkit, `voice.speak`) est **purement terminal**.

**Extraction (S0)** — on sort la logique de tour de `cli.py` vers un service partagé, CLI **et**
daemon l'appellent :

```
AVANT                                  APRÈS
cli.run_one_turn  ─┐                   service/turn_runner.run_turn(... , emitter, ask_human_cb)
cli._run_deep_turn ┘ (glue + render)        ├─ dispatcher.detect_language / classify / execute_alexa   (réutilisé)
                                            ├─ build agent spec + agent_resolver + tools_registry      (déplacé)
                                            └─ orchestrator_v2.run_main_loop(... , emitter, ask_human)  (INCHANGÉ)
cli garde : render_event, spinner, make_ask_human, voice.speak     (terminal only)
daemon fournit : emitter→queue WS, ask_human_cb→WS round-trip      (web only)
```

**Carte de réutilisation** (capacité web → code existant réutilisé / code neuf) :

| Capacité web | Réutilise l'existant | Neuf |
|---|---|---|
| Lister convs (user-scopé) | `db.list_active_conversations` → variante JOIN | 1 SQL + table assoc |
| Créer / reprendre conv | `_create_new_conversation`, `_resolve_resume`, `persistence.load_messages` (→ `conversation_svc`) | assoc insert |
| Messages / state / events | `persistence.load_messages/load_state/load_events` | — |
| Workspace arbre / fichier | handlers `workspace_list` / `workspace_view` (+ `safe_resolve`) | wrapper endpoint |
| user_memory CRUD | actions de `manage_user_memory` → **extraites en fonctions pures** | extraction (DRY) |
| Exécuter un tour | `turn_runner.run_turn` → `run_main_loop` **inchangé** | executor + extraction |
| Events live | param `event_emitter` (déjà là) → push WS | bridge |
| `ask_human` | param `ask_human_callback` (déjà là) → WS round-trip + timeout | callback web |
| Pensées | `LLMResponse.thinking` (existe, **non émis**) | 1 event `AgentThinking` + 1 point d'émission |
| TTS navigateur | boucle `voice.synthesize` + phrases `_DELEGATION_PHRASES`/`_TOOL_PHRASES` | `synthesize_to_bytes` + `phrase_for_event` |
| Langue / routage | `dispatcher.detect_language` / `classify` / `execute_alexa` | — |

## Architecture cible

```
Navigateur (Vue 3 + Vuetify)  ── REST + WebSocket + Web Audio (TTS) ──┐
                                                                      ▼
[CONTENEUR  jeanmichel-web]  nginx : sert la SPA + reverse-proxy /api & /ws
                                              │ (host.docker.internal:8000)
                                              ▼
[HÔTE]  daemon  ./jm.sh --serve   (uvicorn + FastAPI, lancé à la main)
 ├─ Auth          login → token signé ; dépendance current_user ; garde "owner"
 ├─ REST          conversations (user-scopées), messages, events, workspace, memory
 ├─ WS            /ws/conversations/{id}  tour + events + ask_human + final + audio
 ├─ TurnExecutor  1 worker global FIFO  →  service/turn_runner.run_turn()
 │                     ├─ emitter     → asyncio.Queue (call_soon_threadsafe) → WS
 │                     └─ ask_human   → bloque sur answer_box (timeout) ← WS {answer}
 ├─ Service PARTAGÉ avec le CLI :  turn_runner · conversation_svc · memory · voice.phrase_for_event
 └─ SQLite (jeanmichel.db) + conversations/   (mêmes fichiers que le CLI)
        │
        ├─ Ollama (hôte, GPU)          via OLLAMA_HOST
        ├─ docker (hôte)               bash_sandbox — IDENTIQUE au CLI, rien à changer
        └─ SearXNG (host-network)      web_search
```

### Le cœur technique : bridge sync↔async (sans toucher l'orchestrateur)

- Le worker thread exécute `turn_runner.run_turn()` → `run_main_loop()` inchangé.
- `event_emitter = lambda ev: loop.call_soon_threadsafe(async_q.put_nowait, ev.to_dict())` ;
  le handler WS `await async_q.get()` puis `ws.send_json(...)`. **Aucune dépendance externe.**
- `ask_human_callback(question, why)` : pousse `{type:"ask_human",…}` via le bridge, puis
  **bloque le worker** sur `answer_box.get(timeout=ASK_HUMAN_TIMEOUT_SECONDS)` (nouveau réglage).
  Le WS, sur `{type:"answer",text}`, fait `answer_box.put(text)`. Sur timeout : retourne une
  chaîne « (pas de réponse) » pour que le loop conclue — **aucun changement orchestrateur**.

## Surface API

**REST** (`/api`, JSON ; routes conv-scopées derrière la garde *owner*) :
- `POST /auth/login {username,password}` → `{token,user}` · `GET /auth/me`
- `GET /conversations` → **liste user-scopée** (JOIN `conversation_users`)
- `POST /conversations {mode}` → crée conv **+ association au user courant**
- `GET /conversations/{id}` (meta+state) · `POST /conversations/{id}/close`
- `GET /conversations/{id}/messages` · `GET /conversations/{id}/events`
- `GET /conversations/{id}/workspace` (arbre) · `GET …/workspace/file?path=` (`workspace_view`)
- `GET /memory[?type=]` · `GET /memory/{type}/{code}` · `POST /memory` ·
  `PATCH /memory/{type}/{code}` · `DELETE /memory/{type}/{code}`

**WebSocket** `/ws/conversations/{id}` (auth + owner à la connexion) :
- C→S : `{type:"turn",text}` · `{type:"answer",text}`
- S→C : `{type:"event",event}` · `{type:"thinking",agent,text}` · `{type:"ask_human",question,why}`
  · `{type:"final",answer}` · `{type:"audio",…}` (vocal) · `{type:"busy"|"queued",position}` ·
  `{type:"error",…}`

## Migration BDD (additive — ne touche aucune table existante)

`db/migrations/migrate_112_web_users.sql` :
```sql
CREATE TABLE web_users (
  id            INTEGER PRIMARY KEY,
  username      TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE TABLE conversation_users (
  user_id         INTEGER NOT NULL REFERENCES web_users(id),
  conversation_id TEXT    NOT NULL REFERENCES conversations(id),
  created_at      TEXT    NOT NULL,
  PRIMARY KEY (user_id, conversation_id)
);
CREATE INDEX idx_conv_users_user ON conversation_users(user_id);
```
Puis régénérer `db/schema.sql` via le dump (process documenté dans l'en-tête de `schema.sql`).

## Packaging / lancement

- `pyproject.toml` : décommenter + ajouter un groupe `[project.optional-dependencies] web`
  (`fastapi`, `uvicorn[standard]`, `argon2-cffi`, `itsdangerous`) ; script
  `jean-michel-serve = "jeanmichel.api.app:run"`.
- Nouveau package `src/jeanmichel/api/` : `app.py` (FastAPI `app` + `run()` uvicorn,
  bind `0.0.0.0:8000` pour le LAN), `auth.py`, `routes.py`, `ws.py`, `executor.py`.
- `jm.sh` : nouvelle branche `--serve` → `.venv/bin/python -m jeanmichel.api.app`. « À la main »
  = l'utilisateur lance `./jm.sh --serve` dans un terminal.

## Sprints

> Convention de fin de sprint : les **~360 tests v2 restent verts** ; chaque sprint ajoute ses tests.
> Le `MockClient` (`src/jeanmichel/llm.py`) permet de tester l'API sans Ollama.

- **S0 — Extraction & réutilisation (cœur « zéro doublon », zéro changement de comportement)**
  - `service/turn_runner.run_turn(conv_id, conv_folder, mode, user_text, dispatch_llm, main_llm,
    profile, *, event_emitter, ask_human_callback) -> str` : déplace detect_lang + classify +
    routage alexa/deep + build (agent spec, resolver, registry, seed messages) +
    `run_main_loop` **inchangé**.
  - `service/conversation_svc` (create/resume/close) et `service/memory` (CRUD extrait de
    l'outil ; le tool appelle ces fonctions). `voice.phrase_for_event(event)` (extrait de
    `src/jeanmichel/cli.py:468-478`).
  - `cli.py` réécrit pour appeler ces services ; garde uniquement le rendu terminal.
  - pyproject `web` deps + squelette `jeanmichel/api/app.py` (`app` FastAPI vide) + `--serve`.
  - *Accept.* : tests existants verts, CLI **strictement** inchangé pour l'utilisateur.

- **S1 — Auth + multi-utilisateur** : `migrate_112` + régénération `schema.sql` ; helpers `db.py`
  (`create_web_user`, `get_web_user_by_username`, `associate_conversation_user`,
  `list_conversations_for_user`, `user_owns_conversation`) ; `auth.py` (hash argon2, login → token
  signé itsdangerous, dépendances `current_user` + `require_conversation_owner`) ; création users
  via `debug/admin.py`. *Accept.* : round-trip token ; `403` sur conv d'autrui.

- **S2 — API lecture (user-scopée, GET sur helpers existants)** : conversations/messages/events/
  workspace/memory. *Accept.* : un user ne voit que ses convs ; path-traversal bloqué
  (`safe_resolve`). *Le frontal (S7) peut démarrer en parallèle dès ici.*

- **S3 — Tour + streaming WebSocket (cœur)** : `executor.py` (1 worker global FIFO ⇒ « un tour
  actif global » ; `TurnSession{conv_id, async_q, answer_box, status}`) ; bridge sync↔async ;
  `POST /conversations` crée conv + assoc ; WS pousse events + `{final}` ; concurrents → `{busy|
  queued}`. *Accept.* : un tour streame en live ; concurrents en file ; events toujours persistés
  pour replay. Testable via `MockClient`.

- **S4 — `ask_human` aller-retour + surface des pensées** : callback web avec **timeout**
  (`ASK_HUMAN_TIMEOUT_SECONDS`) ; `{answer}` débloque ; surfacer `LLMResponse.thinking` via un
  event additif `AgentThinking{agent,text}` (1 ajout `events.py` + 1 point d'émission autour de
  l'appel LLM). *Accept.* : pause/reprise + timeout sûrs ; pensées visibles.

- **S5 — Mutations `user_memory`** : `POST/PATCH/DELETE /memory` via `service/memory` (réutilise
  limites 60/150/1000 + seuils 100/90). Documenter la limite v1 (memory globale, cf. Risques).
  *Accept.* : CRUD API ≡ comportement de l'outil.

- **S6 — TTS navigateur** : `voice.synthesize_to_bytes(text) -> bytes|None` (WAV, réutilise la
  boucle `voice.synthesize`) ; stream audio réponse finale + annonces (`phrase_for_event`) en
  frames WS binaires ou `GET …/audio?turn=` ; lecture via Web Audio API. *Accept.* : réponse
  vocale jouée dans le navigateur + annonces pendant le travail.

- **S7 — Frontal Vue 3 + Vuetify** (dès S2) : Vite + Vue 3 + Vuetify 3 + `markdown-it`. Drawer =
  convs du user (reprise) ; chat (rendu markdown) ; timeline d'events avec **petites animations**
  (mapping type d'event → chip/état) ; explorateur workspace (arbre + viewer) ; panneau infos user
  (CRUD memory) ; login. Client WS (turn + modal `ask_human` + lecture audio). *Accept.* :
  bout-en-bout contre le daemon.

- **S8 — Conteneur frontend (léger)** : `web/Dockerfile` multi-stage (build node → nginx statique
  + reverse-proxy `/api` & `/ws` vers le daemon hôte) ; sur Linux,
  `extra_hosts: host.docker.internal:host-gateway`. Single origin ⇒ **pas de CORS**. *Accept.* :
  `docker run` du conteneur web → SPA fonctionnelle parlant au daemon hôte.

## Risques & points ouverts (vérité crue)

- **`user_memory` reste GLOBALE en multi-user.** Injectée dans *chaque* system prompt
  (`src/jeanmichel/prompts.py:75`) : les faits perso d'Alice apparaissent dans les prompts de Bob
  → **fuite de vie privée**. La contrainte « ne pas éclater la BDD » l'impose pour le v1 ⇒
  **réservé à un groupe de confiance**. Isolation = ajouter `user_id` à `user_memory` (changement
  de schéma, **différé**).
- **Pouvoir des users authentifiés = pleins pouvoirs de l'assistant**, dont `bash_sandbox`
  (exécution de code dans le Docker de l'hôte). Même modèle de confiance que le CLI aujourd'hui ;
  acceptable en groupe de confiance, à documenter.
- **Exposition LAN.** Le daemon bind `0.0.0.0` : joignable sur le réseau, l'auth est l'unique
  barrière. Mots de passe hashés (argon2) + tokens signés, mais **pas d'exposition Internet sans
  TLS** (reverse-proxy/tunnel à prévoir si besoin).
- **Un seul tour global = plafond de débit** : tous les users partagent un slot GPU ⇒ file
  d'attente affichée. Acceptable perso/petit groupe.
- **CLI vs web** : convs CLI invisibles dans le web (par design, pas d'association) ; éviter de
  piloter la même conv via CLI et daemon en parallèle (écritures `messages.json`/`state.json`).
- **Web UI** 
  - le bloc de reponse de jeanmichel apparait avant le bloc de detail de reflexion, au lieu de apres. (a revoir, le bloc reflexion est toujours en bas en fait, necessie reflexion)
  - dans la popup de workspace, ajouter un bouton pour telecharger le fichier, juste avant le nom du fichier actuellement selectionne (partie de droite de la popup)
  - possibilite d'uploader des fichiers qui vont dans le workspace (https://vuetifyjs.com/en/api/v-file-upload/)
  - une fois la phase de reflexion terminee, le bloc de reflexion doit etre "replie" et une icone permet de le deplier/replier
  - possibilite de supprimer des conversations
  - permettre de donner un titre a la conversation (le LLM pourrait en deduire un par defaut et le user pourrait l'editer)

## Vérification

- **Tests** : ~360 verts après S0 (preuve que l'extraction n'a rien cassé). Nouveaux : auth
  (token, `403` cross-user), endpoints read (user-scoping), WS turn via `MockClient`, `ask_human`
  round-trip + timeout, memory CRUD, idempotence migration (`test_migration_idempotence`,
  `test_schema_v2`).
- **E2E manuel** : `./jm.sh --serve` (hôte) + `docker run` du conteneur web → login Alice →
  créer conv → poser une question → voir les events s'animer → déclencher `ask_human` → vérifier
  que Bob ne voit pas la conv d'Alice → en mode vocal, entendre la réponse dans le navigateur.
