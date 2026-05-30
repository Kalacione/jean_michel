# Audit — Isolation de `user_memory` par utilisateur

> Statut : audit de cadrage (2026-05-30). Fait suite à `01_audit_api_async_webui.md`
> (point ouvert « user_memory reste GLOBALE en multi-user »). But : déduire la
> manière la plus **KISS** d'isoler la mémoire par utilisateur, garantir un
> stockage pertinent, et remplir des champs de base à la création du compte.

## Context

Depuis le frontal web (S1-S8), plusieurs utilisateurs partagent l'instance. Or
`user_memory` est **globale** : les faits perso d'Alice apparaissent dans les
prompts de Bob — **fuite de vie privée**. Pire que prévu : le **profil aussi**
fuite (tous les users web voient le `user_profile.toml` de l'hôte). On veut
isoler proprement, sans casser le CLI mono-utilisateur.

Idée directrice (Jeremy) : un utilisateur **`cli` par défaut** qui continue
d'utiliser le profil fichier (renommé `cli_profile.toml`), la structure des
champs du profil **reprise en BDD** pour les comptes web et remplie **à la
création**, et un stockage de mémoire qui reste pertinent.

## État des lieux

### 1. Schéma BDD

- **`user_memory`** (`db/migrations/migrate_101_user_memory.sql`) :
  `id, type(user|feedback|project|reference), code, title, description, content,
  created_at, modified_at`, **`UNIQUE (type, code)`**. **Aucune colonne `user_id`
  → table globale.** Index sur `type` et `modified_at`.
- **`web_users`** (`migrate_112`) : `id, username, password_hash, created_at`.
  **Aucun champ de profil.**
- **`conversation_users`** : association owner ↔ conversation (S1). Le CLI ne crée
  pas d'association (ses convs restent invisibles au web).

### 2. Le profil utilisateur — **structuré ET consommé par le code**

- `UserProfile` (`src/jeanmichel/config.py:234`) : dataclass
  `name, birthdate, city, country, language, interests, notes`.
  `load(path=USER_PROFILE_PATH)` lit `user_profile.toml` ; `render()` produit le
  bloc texte du prompt.
- **Point capital** : le profil n'est pas qu'un texte de prompt. Le dispatcher
  l'utilise **programmatiquement** — `dispatcher.execute_alexa(..., user_profile=…)`
  injecte `profile.city` comme défaut météo/horloge, et `language` pilote la
  langue de sortie. ⇒ **les champs doivent rester structurés par utilisateur**,
  pas un simple blob texte.

### 3. Injection dans le prompt — **le point de fuite**

`prompts.render_system_prompt_v2` (`src/jeanmichel/prompts.py:222`) compose le
bloc `## Human` = `user_profile_text` **+** `user_memory_block`. Les deux
arrivent du tour :

- `service/turn_runner._run_deep_turn` appelle `render_user_memory_index(conn)`
  (`src/jeanmichel/prompts.py:75`) — **SELECT global, sans filtre user_id** — et
  `load_agent_spec_v2(user_profile_text=profile.render())`.
- Le daemon (`api/app.py` `ws_turn`) passe `profile = UserProfile.load()` — **le
  même `cli_profile.toml`/`user_profile.toml` partagé pour tous les users web**.

⇒ Chaque tour web rend le **profil de l'hôte + la mémoire globale**, et y **écrit**.
Fuite croisée à la fois sur le profil et sur la mémoire.

### 4. Méthode actuelle de décision « quoi mémoriser »

- **Pilotée par le LLM**, réactive pendant un tour. Le paradigme
  `user_memory_discipline` dit : sauver un fait durable révélé par l'humain,
  mettre à jour si contredit/affiné, supprimer si obsolète, recall si pertinent,
  garder concis (60/150/1000).
- Le LLM voit l'index `## Known facts` injecté et appelle
  `manage_user_memory(action=…)`. Granté à **4 agents** : `jean-michel`,
  `strategist`, `news-specialist`, `code-fetcher`.
- Le tool est **statique/global** : enregistré via le `SPEC` module-level
  (`src/jeanmichel/tools/__init__.py:87`), son handler ouvre sa propre `db.connect`
  et opère sur **toute** la table. ⚠ Contraste avec les outils workspace/sandbox,
  **bindés par tour** via `make_spec(conv_folder)` (`tools/__init__.py:66-70, 99`).
- **Pas d'analyse batch des échanges** : rien n'extrait automatiquement des faits
  d'une conversation a posteriori. C'est purement réactif, dans le tour.

### 5. Bootstrap

`bootstrap.bootstrap_user_memory_from_profile` (`src/jeanmichel/bootstrap.py:30`) :
au 1er run, si la table est vide et le profil non-vide, **INSERT une** entrée
`user/personal-profile` (content = `profile.render()`). **Sans `user_id`**, global.

## Cible KISS — l'isolation

Principe : **une seule notion d'identité mémoire par tour** (`memory_user_id`),
propagée jusqu'au rendu du prompt ET au tool. Web = l'utilisateur authentifié
(= owner de la conv) ; CLI = un utilisateur réservé `cli`.

### A. Schéma (migration `migrate_113`)

```sql
-- 1. Profil structuré par compte : la structure du TOML, reprise en BDD.
ALTER TABLE web_users ADD COLUMN name        TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN birthdate   TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN city        TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN country     TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN language    TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN interests   TEXT NOT NULL DEFAULT '';
ALTER TABLE web_users ADD COLUMN notes       TEXT NOT NULL DEFAULT '';

-- 2. Utilisateur réservé `cli` (mot de passe inutilisable : pas de login web).
--    ⚠ NE PAS forcer l'id : des comptes web existent déjà (le 1er a pris id=1).
--    On laisse l'autoincrément et on référence l'id réel par sous-requête.
INSERT INTO web_users (username, password_hash, created_at)
VALUES ('cli', '!', <now>);

-- 3. user_memory scopée par user_id. SQLite ne sait pas ALTER une contrainte
--    UNIQUE → rebuild de table (créer la nouvelle, copier en assignant cli, drop,
--    rename). Les entrées existantes sont la mémoire CLI de Jeremy → user cli.
CREATE TABLE user_memory_new (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES web_users(id),
  type TEXT NOT NULL CHECK (type IN ('user','feedback','project','reference')),
  code TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
  content TEXT NOT NULL, created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
  UNIQUE (user_id, type, code)
);
INSERT INTO user_memory_new (id,user_id,type,code,title,description,content,created_at,modified_at)
  SELECT id, (SELECT id FROM web_users WHERE username='cli'),
         type, code, title, description, content, created_at, modified_at
  FROM user_memory;
DROP TABLE user_memory;
ALTER TABLE user_memory_new RENAME TO user_memory;
CREATE INDEX idx_user_memory_user ON user_memory(user_id);
CREATE INDEX idx_user_memory_type ON user_memory(type);
CREATE INDEX idx_user_memory_modified ON user_memory(modified_at DESC);
```

> ⚠ Migration **one-shot** (le rebuild n'est pas ré-applicable comme un
> `IF NOT EXISTS`). Gérer comme `migrate_102` (ALTER one-shot) dans les tests :
> tester la forme finale + l'idempotence des parties qui le permettent.

### B. Sources de profil (le `cli` garde son fichier)

| Utilisateur | Source `UserProfile` | mémoire (`memory_user_id`) |
|---|---|---|
| **`cli`** (CLI) | `cli_profile.toml` (renommage de `user_profile.toml`) | id du user réservé `cli` |
| **web user** | colonnes `web_users` (remplies à la création) | son `web_users.id` |

- `UserProfile` gagne un constructeur `from_row(row)` (colonnes web_users) à côté
  de `load(path)` (toml). `config.USER_PROFILE_PATH` → `CLI_PROFILE_PATH`
  (`cli_profile.toml`) ; idem `user_profile.example.toml` → `cli_profile.example.toml`.

### C. Threading `memory_user_id` dans le tour

```
daemon ws_turn (sait : user authentifié = owner)         CLI (toujours user cli=1)
        │                                                        │
        └──► turn_runner.run_turn(..., memory_user_id, profile) ◄┘
                 ├─ render_user_memory_index(conn, memory_user_id)   # filtre WHERE user_id=?
                 ├─ build_registry(..., memory_user_id)
                 │     └─ manage_user_memory devient make_spec(memory_user_id)  # comme workspace
                 └─ load_agent_spec_v2(user_profile_text=profile.render())
```

Changements ciblés :
- `service/memory.py` : chaque fonction (`save/recall/list_/update/delete`) gagne
  un param `user_id` et l'intègre au SQL (filtre + insert). `UNIQUE` devient
  `(user_id, type, code)`.
- `prompts.render_user_memory_index(conn, user_id, …)` : `WHERE user_id = ?`.
- `tools/manage_user_memory.py` : passe d'un `SPEC` statique à
  `make_spec(user_id)` ; le handler scope tout au `user_id` bindé. `build_registry`
  prend `memory_user_id` et construit ce spec (1 ligne, comme les outils workspace).
- `turn_runner.run_turn` + `cli.run_one_turn` + `executor.run_turn_streaming` :
  propagent `memory_user_id` (et la bonne source de profil).
- **API web** : les endpoints `/api/memory*` (S2/S5) scopent déjà sur
  `current_user` → ajouter `user_id=current_user.id` aux appels `service.memory`.

### D. Champs de base remplis à la création

- **Web** : à l'inscription / `./jm.sh --create-user`, collecter au minimum
  `language`, `city`, `country` (+ `name`) → écrits dans les colonnes `web_users`.
  Un petit **formulaire profil** côté front (réutilise le panneau Mémoire) permet
  de compléter/éditer ensuite.
- **Seed mémoire** : à la création, insérer l'entrée `user/personal-profile`
  scopée (`bootstrap` paramétré par `user_id` + profil). Le `cli` la seed depuis
  `cli_profile.toml` au 1er run (comportement actuel, juste scopé `user_id=1`).

### E. Stockage pertinent — on garde l'existant

La décision « quoi mémoriser » **ne change pas** : LLM + paradigme
`user_memory_discipline`, réactif dans le tour, désormais **par utilisateur**.
C'est suffisant et KISS. Une **analyse batch des échanges** (extraire des faits
en fin de conversation) est un chantier distinct et plus complexe — **différé**,
pas nécessaire pour l'isolation.

**Frontière d'écriture (règle ferme)** : le LLM écrit **uniquement** dans
`user_memory` (via `manage_user_memory`). Il **n'écrit jamais** les colonnes de
profil de `web_users` — `notes` y compris. Le profil structuré est
**humain-authored** : posé à la création, édité par l'utilisateur (formulaire).
Raisons : un seul chemin d'écriture LLM ; `user_memory` est la bonne structure
(typée, recall/update/delete par fait, plafond + curation) alors que `notes` est
un blob toujours injecté en entier ; et le profil structuré est lu **par le code**
(`execute_alexa` → `city`/`language`), donc à ne pas laisser le LLM corrompre.
Cas limite (changement d'un champ structuré détecté en conversation, ex.
déménagement → `city`) : auto-mise-à-jour du profil par le LLM = **différée**
(risquée) ; en v1 l'humain édite, le LLM accrète dans `user_memory`.

## Alternatives écartées (vérité crue)

- **Profil = simple entrée `user_memory` (pas de colonnes)** : rejeté — le code
  consomme `profile.city`/`language` **programmatiquement** (`execute_alexa`).
  Parser un blob texte serait fragile.
- **Table `web_user_profiles` 1:1 dédiée** : plus normalisé mais une table de
  plus. Colonnes sur `web_users` = plus KISS (la structure du TOML, à plat).
- **Garder le TOML pour tout le monde** : rejeté — aucune isolation.
- **`user_id` nullable + NULL = global** : rejeté — re-fuite par défaut ; un `cli`
  réservé non-nullable est plus sûr (pas de mémoire « orpheline » visible).

## Risques / points ouverts

- **Migration one-shot** (rebuild `user_memory`) : non ré-applicable ; sauvegarde
  recommandée (`./jm.sh --export-db`) avant application sur la vraie BDD.
- **`username='cli'` réservé** : `--create-user`/signup doivent refuser `cli`.
- **Mémoire pré-existante** = celle de Jeremy via CLI → légitimement assignée à
  `cli` (id=1). Si Jeremy veut aussi un compte web, sa mémoire CLI ne le suivra
  pas automatiquement (séparation voulue ; un éventuel « import » serait manuel).
- **`birthdate`/`interests`/`notes`** : optionnels à la création (seuls
  `language`/`city`/`country`/`name` sont « de base »).

## Découpage en sprints

- **M0 — Schéma** : `migrate_113` (colonnes profil `web_users` + user réservé
  `cli` + rebuild `user_memory` avec `user_id`/UNIQUE) ; régénérer `schema.sql` ;
  tests forme finale + idempotence partielle (cf. `test_migration_idempotence`).
- **M1 — Couche données scopée** : `service/memory.py` (+`user_id`),
  `prompts.render_user_memory_index(user_id)`, helpers `db.py` (create web user
  avec profil, get profil, `UserProfile.from_row`). Le tool `manage_user_memory`
  → `make_spec(user_id)`. Tests : isolation Alice≠Bob au niveau service.
- **M2 — Threading tour** : `memory_user_id` dans `turn_runner` / `executor` /
  `cli` ; profil par source (toml cli vs colonnes web). Endpoints `/api/memory*`
  scopés. Renommage `cli_profile.toml`. Tests : un tour web ne voit que SA mémoire
  + SON profil (prompt rendu) — la fuite est fermée.
- **M3 — Création & remplissage** : collecte des champs de base
  (`--create-user` interactif + formulaire profil front) ; seed `personal-profile`
  scopé ; bootstrap `cli` depuis `cli_profile.toml`. Tests : un nouveau compte a
  ses champs de base + son entrée profil.

Convention : à chaque sprint, les **~446 tests restent verts** + nouveaux tests.
