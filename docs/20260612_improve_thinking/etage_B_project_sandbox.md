# Étage B — la « sandbox projet » (conteneur par-projet pour commandes contre le repo)

> Suite de [audit_de_la_bouse.md](audit_de_la_bouse.md). Étage A (doctrine des espaces + `repo_git`
> read-only) est livré : le worker inspecte le repo sans `bash_sandbox`. Manque le **4ᵉ espace** : un
> endroit pour **faire tourner des commandes CONTRE le repo** (build, test, lint, `mv`/`rm`/rename, git
> d'écriture). Ce doc fige le design pour ne rien oublier en route.

## Le besoin (mots de l'utilisateur)

- Le gros du taf se fait **dans le repo** ; workspace + `bash_sandbox` restent pour rapports / bouts de code jetables.
- Il faut **pouvoir faire tourner des commandes** sur le repo (« un genre de sandbox projet »).
- **Pas les clés de la machine au LLM** : commandes arbitraires ⇒ **confinées** (réseau, home, clés, autres projets hors d'atteinte).
- Les projets sont **hétérogènes** (py / node / bun, Node 22 ≠ 24) ⇒ **pas d'image unique**.
- Provisioning des deps via un **Dockerfile par projet** (défaut « alpine tout con » bash+git) ; conteneurs en `--rm`,
  repo monté à un **chemin fixe** (`/app`), **bons droits** ; restart + **arrêt propre** quand le système se coupe.

## Décisions figées

1. **Périmètre des commandes arbitraires = conteneur** (jamais l'exec hôte : ça donne les clés de la machine).
2. **Une image par projet**, buildée depuis un **Dockerfile fourni par le projet** ; **défaut** = image minimale alpine+git+coreutils.
3. **Le réseau n'existe qu'au BUILD** (deps installées là, 1×, caché par layers docker) ; le conteneur d'exécution tourne **`--network=none` dès sa naissance** → le paradoxe réseau du `cloud_init` disparaît.
4. **Le conteneur monte le worktree lié existant** (`/app`). Le clone autonome (pour que `git` marche DANS le
   conteneur) est **différé** : à l'implémentation, il rippe sur la sémantique `source_repo`/`repo_test`/CRP
   (le `git-common-dir` d'un clone ≠ la source ; `.venv`/graphify vivent dans la source) — risque > gain immédiat.
   Sécurité **équivalente** (la source `.git` n'est montée dans aucun cas). git en conteneur via l'hôte `repo_git`
   (read-only) ; on clonera SI un outil de build appelle git en interne et casse (cf. §Checkout).
5. **Réutilise la machinerie existante** : `bash_sandbox._start_container` (déjà `--user uid:gid --cap-drop=ALL --memory --cpus --network=none`), `docker/sandbox/` + `jm.sh --build-docker`, colonne `agents.sandbox_image`.

## Modèle de checkout : clone autonome (changement vs P0)

**Aujourd'hui** ([worktree.py](../../src/jeanmichel/worktree.py)) : repo local → `git worktree add` (worktree **lié** :
son `.git` est un fichier pointant vers le `.git` de la source). repo ssh → `_ensure_clone_cached` puis worktree.

**Problème pour le conteneur** : un worktree lié monté seul à `/app` casse `git` (le gitdir pointe **hors** du mount) —
or beaucoup de build/test invoquent git (`setuptools_scm`, `git describe`, pre-commit…).

**Décision** : par conversation, **checkout autonome** =
- **local** : `git clone --local <source> <conv>/repo` → objets **hardlinkés** (rapide, peu d'espace), `.git` **self-contained**.
- **ssh** : clone normal (déjà caché dans `repos-cache/`).
- branche `jm/conv-<id>` créée dans le clone ; **la source n'est jamais montée** dans le conteneur (isolation : une corruption reste dans le clone).
- les outils hôte `repo_*` (`repo_read/grep/glob/edit/write/test/git`) opèrent sur ce clone (inchangé côté API : `worktree_path_for`/`source_repo` renvoient le clone).
- *Push-back vers la source* = étape future, gatée (hors Étage B).

> Impact code : `worktree.create_worktree` (branche local→clone `--local`), `source_repo`, `remove_worktree`,
> `branch_name` conservés. Le CRP (`context_packet._graphify_slice`, diff) et `repo_test` visent déjà
> `worktree.source_repo`/`worktree_root` → suivent automatiquement.

## L'outil + le conteneur

**Nouvel outil `repo_exec`** (granté à `code-runner` + `code-runner-node`), calqué sur `bash_sandbox` :
- lance/réutilise un conteneur **par conversation** `jm-repo-<conv_id>` puis `docker exec` la commande, `cwd=/app`.
- `docker run -d --rm --name jm-repo-<id> --network=none --cap-drop=ALL --memory=… --cpus=… --user $(uid):$(gid) -v <clone>:/app:rw -w /app <image> tail -f /dev/null`.
- renvoie un résultat structuré `{exit_code, stdout_tail, stderr_tail}` (comme `repo_test`).
- **image** = `agents.sandbox_image` per-projet si présente, sinon `jeanmichel-sandbox:project-<id>` si buildée, sinon `jeanmichel-sandbox:repo-default`.

> `bash_sandbox` (scratch, code généré) **reste** tel quel. `repo_exec` est l'espace « commandes contre le repo ».
> `repo_test` (Étage C) basculera de l'hôte vers `repo_exec`/le conteneur projet une fois rodé.

## Build d'image par-projet

- **Source du Dockerfile** : convention repo `.jm/Dockerfile` **ou** chemin en config projet (`projects`). Absent ⇒ `repo-default`.
- **Build** : `docker build -t jeanmichel-sandbox:project-<id> -f <dockerfile> <contexte>` — **réseau autorisé ICI seulement**.
- **Quand** : à l'attache du repo / 1ᵉʳ `repo_exec` ; **rebuild si le hash du Dockerfile change** (tag par hash ou label).
- **Défaut** : `docker/sandbox/Dockerfile.repo-default` (alpine + git + coreutils + bash) buildé en `jeanmichel-sandbox:repo-default` via `jm.sh --build-docker repo-default` (+ inclus dans `all`).
- **Garde-fou confiance** : on build depuis le Dockerfile **commité/propriétaire**, **jamais** un Dockerfile librement
  édité-puis-buildé par le LLM (un build a le réseau → surface d'exfiltration). L'agent peut le *proposer* ; le builder reste l'humain/propriétaire.

## Cycle de vie (trou actuel à combler)

`reap_sandboxes()` existe ([bash_sandbox.py:118](../../src/jeanmichel/tools/bash_sandbox.py#L118)) mais n'est appelé
QUE par `jm.sh --reap-sandboxes` (manuel) — **rien ne reap au shutdown**. À faire :
- **Nommage** `jm-repo-<id>` (parallèle à `jm-sandbox-`) ; généraliser le reap aux deux préfixes (ou un label `jeanmichel.sandbox=1`).
- **Arrêt propre** : `reap_sandboxes(None)` (stop = remove, conteneurs en `--rm`) dans le `_lifespan` du daemon
  ([api/app.py:57-64](../../src/jeanmichel/api/app.py#L57-L64), aujourd'hui ne coupe que MCP) **et** le teardown CLI.
- **Balayage orphelins au démarrage** (sessions crashées) : `reap_sandboxes(None)` au boot.
- **Idle-reap** : appeler `reap_sandboxes(max_idle_minutes)` (déjà paramétrable) périodiquement / par tour.
- **Restart** : conteneur absent ⇒ recréer (logique `_container_running` existante).

## Sécurité (récap)

`--network=none` (run) · `--cap-drop=ALL` · `--user $(uid):$(gid)` (droits hôte corrects sur les fichiers créés) ·
`--memory`/`--cpus` · mount **uniquement** le clone à `/app` (ni home, ni `~/.ssh`, ni autres projets, ni la source) ·
Dockerfile propriétaire (build de confiance). Une commande destructrice (`rm -rf`) reste **confinée au clone**, jetable et git-isolé.

## Mise à jour de la doctrine (prompt)

Quand `repo_exec` existe, **étendre `code_space_doctrine`** (migrate suivante) : 4ᵉ espace = « pour exécuter des commandes
contre le repo (build/test/lint, déplacer/supprimer des fichiers), utilise `repo_exec` (conteneur du projet, offline,
confiné au repo) — pas `bash_sandbox` (qui ne voit que le scratch) ni l'hôte ». Garder anglais + code-only + dual-write.

## Découpage en sous-étapes (livrables testables)

- **B1 — Checkout autonome (PROCHAINE ÉTAPE — confirmé)** : `create_worktree` local → `git clone --local`
  (clone autonome ⇒ `.git` self-contained ⇒ **git marche dans le conteneur** + débloque git-write/checkpoint) ;
  `source_repo` lira `remote.origin.url` pour rester sur le repo d'origine (`.venv`/graphify). *Décision user :
  on l'a séquencé après B4/B5 ; le worktree lié actuel monte déjà OK (sécurité équivalente, git en lecture via repo_git hôte).*
- **B2 — Image défaut : ABANDONNÉ** — `repo_exec` réutilise l'image `sandbox_image` de l'agent (py/node-alpine, déjà buildées). Pas de nouvelle image à maintenir.
- **B3 — `repo_exec` + conteneur projet — ✅ LIVRÉ** : outil + `_start_repo_container` (mount `/app`, network=none, --user, --cap-drop) ; grant migrate_136 ; tests (mock docker).
- **B4 — Build per-projet — ✅ LIVRÉ** : `_resolve_image` lit `<source_repo>/.jm/Dockerfile` (OWNER, pas le worktree éditable), build tagé par hash (`project-<sha1>`), rebuild si changement, fallback image agent ; build = seul moment réseau.
- **B5 — Cycle de vie — ✅ LIVRÉ** : `reap_sandboxes` couvre `jm-sandbox-`/`jm-repo-` + filtre `conv_id` ; reap au shutdown daemon (`_lifespan`) + sweep démarrage + reap par-conv au teardown CLI.
- **B6 — Doctrine — ✅ LIVRÉ** : `code_space_doctrine` nomme la PROJECT SANDBOX (`repo_exec`) ; dual-write + tests par mode.
- **B7 — (Étage C) `repo_test` → conteneur** ; garde anti ré-délégation (F4) si encore observée.

## Risques / questions ouvertes

- **Coût clone** gros repo : `--local` (hardlinks) atténue ; sinon `--shared` (attention à la durée de vie de la source).
- **Temps de build** 1ᵉʳ image projet : normal (caché ensuite) ; afficher un statut.
- **Découverte du Dockerfile** : repo (`.jm/Dockerfile`) vs config projet — décider la priorité (proposition : repo d'abord, config en override).
- **Sortie volumineuse** d'une commande : cap stdout/stderr (comme `repo_test`).
- **`repo_test` host vs conteneur** (B7) : migrer seulement quand B1-B5 rodés, sinon double source de vérité.

## Vérification (cible)

1. Projet sans Dockerfile : `repo_exec("ls")`, `repo_exec("git status")` tournent dans `jeanmichel-sandbox:repo-default`, offline, `/app`.
2. Projet avec `.jm/Dockerfile` (ex. python+deps) : `repo_exec("pytest -q")` voit les deps ; build 1×, réutilisé.
3. Sécurité : `repo_exec("cat ~/.ssh/id_rsa")` / `repo_exec("curl …")` échouent (pas de home, pas de réseau).
4. Fichiers créés par le conteneur ont la **propriété hôte** (uid) ; `mv`/`rm` confinés au clone, tree source intact.
5. Arrêt du daemon → conteneurs `jm-repo-*` stoppés ; redémarrage → orphelins balayés.
6. `pytest tests/v2` vert (B1-B6) ; non-régression `bash_sandbox` (scratch) inchangé.
