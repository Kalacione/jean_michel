# Design — « brancher un repo » dynamiquement à une conversation

> **Statut : DESIGN (pas encore implémenté).** Décidé avec l'utilisateur le 2026-06-12 :
> attache au **niveau projet**, supporte **local + ssh**, design d'abord, implémentation = sprint
> dédié ensuite. Embryon : `todo.md`.

## Besoin

Aujourd'hui le repo cible du mode `code` est **unique et global** : `config.PROJECT_ROOT`
(env `JEANMICHEL_PROJECT_ROOT`, défaut = le repo jean-michel lui-même). On veut que l'utilisateur,
**dans l'interface web**, renseigne un paramètre « dépôt de code » (chemin **local** ou URL **ssh**)
et que le système devienne capable d'y accéder, d'analyser les sources, et de le faire vivre
(éditer, lancer les tests) — par conversation, sans toucher au tree live.

## Décision : attache au niveau PROJET

`projects` existe déjà (migrate_124 : table + scope mémoire + dialog web `ProjectsDialog.vue`). On y
attache le repo. Les conversations en mode `code` rattachées au projet **héritent** du repo.

**Pourquoi projet et pas conversation** : on attache un repo **une fois**, toutes les convs du projet
en profitent ; fork/snapshot de conv triviaux (pas de duplication du repo) ; le dialog projet web
existe déjà. Une conv code sans projet → fallback `config.PROJECT_ROOT` (rétro-compatible ; CLI inchangé).

## Stockage (migration `migrate_133`)

```sql
ALTER TABLE projects ADD COLUMN code_repo TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN repo_kind TEXT NOT NULL DEFAULT 'local'
  CHECK (repo_kind IN ('local','ssh'));
```
- `code_repo` : chemin absolu (local) ou URL ssh (`git@host:org/repo.git`). Vide ⇒ fallback PROJECT_ROOT.
- Miroir `db/schema.sql` + dual-write ; `db.create_project/update_project/get_project` + `service/project.py`
  transportent les 2 champs. (Migration libre : 133 ; 132 = comparator.)

## Matérialisation du repo (`worktree.py`)

`create_worktree(conv_folder, conv_id, source=None, kind="local")` — `source` vide ⇒ `config.PROJECT_ROOT`.
- **local** : `git worktree add` depuis `source` (mécanisme actuel, inchangé).
- **ssh** : pas de worktree direct sur une URL → **cloner d'abord**. `_ensure_clone_cached(url, project_id)`
  clone une fois dans un cache **par projet** hors de `conversations/` (ex. `repos-cache/<project_id>/repo`),
  idempotent + lock anti-course ; puis `git worktree add` depuis ce clone. Le clone survit aux convs
  (partagé par toutes les convs du projet) ; refresh = supprimer le cache / `git -C <clone> fetch`.

## Threading (où `config.PROJECT_ROOT` doit devenir « la racine du worktree »)

`service/conversation.create_conversation(mode, project_id)` lit `code_repo`/`repo_kind` du projet et
les passe à `worktree.create_worktree`. Ensuite, **les outils repo sont déjà per-conversation** via
`_repo.worktree_root(conv_folder)` → pas de changement. À recâbler (lisent encore `config.PROJECT_ROOT`) :
- `tools/repo_test.py::_default_python` → résoudre `.venv` contre la **racine du worktree** (le checkout EST le repo).
- `tools/repo_graph_refresh.py` → `graphify update` avec `cwd = worktree_root`.
- `context_packet._graphify_slice` → lire le graphe du worktree (sinon fallback PROJECT_ROOT).

## API + Frontend (web)

- `src/jeanmichel/api/app.py` : `ProjectSaveRequest` + `ProjectUpdateRequest` gagnent `code_repo` (str)
  + `repo_kind` ('local'|'ssh'). (`CreateConversationRequest` inchangé — la conv hérite du projet.)
- `web/src/components/ProjectsDialog.vue` : champ « Dépôt de code (chemin local ou URL SSH) » + select
  kind dans le formulaire de création/édition de projet. `web/src/api.js` passe l'objet tel quel.
- `ConversationsDrawer.vue` reste simple (mode + projet) ; affichage indicatif du repo hérité.

## SSH / sécurité

- Le clone ssh utilise les **clés ssh de l'hôte** (`~/.ssh`/agent) — les outils repo + git tournent
  sur l'**hôte** (le repo est de confiance, posé par le propriétaire du projet). Pré-requis à documenter :
  clés présentes + `git clone <url>` testé manuellement avant attache.
- Échecs (URL invalide, auth, réseau, disque) → `_ensure_clone_cached` lève proprement ;
  `create_worktree` dégrade (best-effort, ne casse pas la conv) + remonte un message clair.

## Bonus (todo.md) — git en mode repo

Outil **lecture seule** `repo_git` (sous-commandes `log`/`diff`/`blame`/`show`, bornées, sur le
worktree) pour donner aux workers l'historique/contexte d'évolution. Granté code-runner/code-fetcher.
À spécifier dans le sprint d'implémentation.

## Rétro-compatibilité

`code_repo` vide ⇒ `config.PROJECT_ROOT` (dogfood actuel). Conversations existantes + CLI (sans
project_id) : inchangés. Tout le mécanisme reste **opt-in** derrière `CODE_WORKTREE_ENABLED`.

## Fichiers impactés (sprint d'implémentation)

| Fichier | Changement |
|---|---|
| `db/migrations/migrate_133_*.sql` + `db/schema.sql` | colonnes `code_repo`/`repo_kind` sur `projects` |
| `src/jeanmichel/db.py`, `service/project.py` | porter les 2 champs (create/update/get) |
| `src/jeanmichel/worktree.py` | `create_worktree(source, kind)` + `_ensure_clone_cached` (ssh) |
| `src/jeanmichel/service/conversation.py` | lire le repo du projet → worktree |
| `src/jeanmichel/tools/repo_test.py`, `repo_graph_refresh.py`, `context_packet.py` | racine = worktree au lieu de PROJECT_ROOT |
| `src/jeanmichel/api/app.py` | `ProjectSaveRequest`/`ProjectUpdateRequest` + endpoints |
| `web/src/components/ProjectsDialog.vue`, `api.js` | champ repo + kind |
| (bonus) `src/jeanmichel/tools/repo_git.py` | outil git lecture seule |

## Bug à corriger au passage

`db.list_conversations_for_user` (SELECT ~l.318) **omet `project_id`** → l'UI ne peut pas afficher
le rattachement projet. À inclure dans le SELECT.

## Risques / questions ouvertes pour le sprint

- Emplacement + politique de refresh du cache de clones ssh (TTL ? bouton « refresh repo » ?).
- Lock concurrent sur le clone (2 convs du même projet en parallèle).
- Disque (gros repos × projets) — clones partagés par projet, nettoyés à la suppression du projet.
- Plus tard (si besoin) : override repo au **niveau conversation** par-dessus le projet (non retenu ce tour).
