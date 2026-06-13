# Audit — pourquoi le mode code « ne s'enchaîne pas » (+ plan K.I.S.S. de correction)

> **Acquis (ne pas refaire)** : P0–P6 + code-router (qwen3:14b) livrés. Toutes les briques EXISTENT
> (worktree, repo tools, CRP, délibération). Le problème n'est PAS « il manque une feature » :
> **l'enchaînement casse parce que le prompt ne dit pas clairement QUEL espace de code utiliser QUAND.**
> C'est l'audit demandé (« on analyse, on trace, on check… on écrit un rapport ») + le correctif minimal.
>
> **STATUT 2026-06-13 — ÉTAGE A LIVRÉ** (732 tests verts, migrate_135 appliqué live) : `code_space_doctrine`
> (prio 8), `workspace_tools_only` retiré du mode code, outil read-only `repo_git`, notice `[CODE-REPO]`
> réorientée. ÉTAGE B (sandbox projet conteneurisée) et C (finitions) restent à faire.

## Context (tracé sur un vrai run)

Conv `2026-06-13_01-03_af2f5bc1…`, mode `code`. Demande : **« De quand date les derniers commits ? »**
- `code-router` (✅ sélectionné) a délégué **3× la même tâche** à `code-runner` (« run `git log` in `/home/.../jean-michel` »).
- `code-runner` a appelé **`bash_sandbox` 5×** pour lancer `git` → échec *« le répertoire n'existe pas »* → hook deny (doublon).
- **64 appels LLM**, un subagent à **102 messages**, zéro résultat. Le worktree existait pourtant (`jm/conv-af2f…`).

## Le diagnostic (validé par le dump du system-prompt + la DB)

**Le trou dans la raquette = la DOCTRINE des espaces de code dans le prompt** (confirmé par l'utilisateur).
Le worker reçoit des consignes **contradictoires** et suit la plus forte (la mauvaise) :

| Famille | Où | Mode | Force |
|---|---|---|---|
| Mission IDENTITY (« write to workspace, run in `bash_sandbox` ») + bloc *Execution* + `workspace_tools_only` + `test_in_sandbox_when_runnable` | haut du prompt | **ALL** | 🔊 forte |
| `prefer_repo_tools_over_bash` + `repo_intervention_discipline` | bas du prompt | `code` | 🔈 enfouie |

`workspace_tools_only` dit « le filesystem [scratch] EST la source de vérité » → le worker traite le
scratch comme l'espace de travail, alors qu'**en mode code la source de vérité est le repo (worktree)**.

Causes racines :
- **C1 — Aucun outil git.** `repo_read/grep/glob/edit/write/test` ne font pas `git log`. Seul recours :
  `bash_sandbox`+`git`. → la question testée est *sans réponse possible*.
- **C2 — `bash_sandbox` ne voit jamais le repo** ([bash_sandbox.py:72-78](src/jeanmichel/tools/bash_sandbox.py#L72-L78) :
  `--network=none`, monte uniquement le scratch). C'est correct (sandbox = code généré non fiable) mais
  rend l'instinct « bash+git » impossible. → **il manque un espace pour faire tourner des commandes DANS le repo.**
- **C3 — Paradigmes contradictoires** (tableau ci-dessus). **Réponse à « les paradigmes sont raccords ? » : NON.**
- **C4 — Mauvaise orientation.** La notice `[CODE-REPO]` ([hooks.py:356](src/jeanmichel/hooks.py#L356)) donne le
  **chemin du repo live** ; le router a briefé « exécute git dans `/home/…` » → chemin que le sandbox ne voit pas,
  et qui n'est pas le worktree.
- **C5 — Pas d'adaptation.** CRP vide (briefings de 229–314 car.), router ré-délègue verbatim 3× malgré 3 `low`.

## La cible — doctrine des espaces (modèle utilisateur, à graver dans le prompt code)

| Espace | Quand | Écriture | Commandes |
|---|---|---|---|
| **repo** (worktree) | mode code, un repo est attaché — **le gros du taf** | fichiers du projet via `repo_edit`/`repo_write` | git/test/build via outils repo (jamais `bash_sandbox`) |
| **workspace** (scratch conv) | défaut **quand PAS de repo** ; sinon rapports / brouillons / bouts de code à tester | `workspace_*` | — |
| **sandbox** (`bash_sandbox`, network=none) | tester du **code généré/jetable** issu du workspace | — | code non fiable, isolé |
| **« sandbox projet »** (à statuer, F2) | faire tourner des commandes **contre le repo** (test/build/git) | — | dans le worktree |

Règle simple injectée au worker : *en mode code, le travail se fait dans le repo via `repo_*` ; workspace+
sandbox ne servent qu'aux rapports et aux bouts de code jetables à tester ; pour inspecter/exécuter contre
le repo, utiliser les outils repo (pas `bash_sandbox`, il ne voit pas le repo).*

## Plan K.I.S.S. de correction (du plus rentable au moins)

> On **recâble**, on n'empile pas. Paradigmes code = `paradigm_modes='code'` (anti-fuite), anglais,
> model-agnostic. Dual-write `schema.sql` ↔ migration ↔ live + chaîne d'idempotence.

**F1 — Doctrine des espaces dans le prompt (LE fix, headline). — ✅ LIVRÉ (migrate_135).**
1. Nouveau paradigme `code`-only `code_space_doctrine` (anglais) encodant le tableau ci-dessus, placé
   en **tête de comportement** (`order_priority=8`), bindé à `code-runner` + `code-runner-node`.
2. **Gate `workspace_tools_only` HORS du mode code** (lignes `paradigm_modes` = tous modes sauf `code` ;
   mécanisme [db.py:73-74](src/jeanmichel/db.py#L73-L74)). En code, le scratch n'est plus « la source de vérité ».
3. On **garde** `test_in_sandbox_when_runnable`/`verify_execution_output` (le sandbox reste légitime pour
   du code généré — exigence explicite). On subordonne, on ne supprime pas.
4. Si la Mission IDENTITY globale reste trop « sandbox-centric » après 1-3, ajuster la *mission* DB de
   `code-runner`/`-node` (décidé après ré-observation, pas à l'aveugle).

**F2 — Combler les espaces manquants (git + commandes repo).**
*Modèle de menace (analyse) :* le risque n'est PAS d'éditer/renommer/supprimer des fichiers DU repo
(c'est le job ; le worktree git le contient). Le risque est qu'une commande **s'échappe du repo**
(`rm -rf ~`, `~/.ssh`, `.env` du repo live, réseau, autres projets). → l'exec hôte arbitraire est
**exclue** (donne les clés de la machine). Le bon périmètre pour des commandes arbitraires = **conteneur**.
- **F2a — `repo_git` (read-only : `log/show/diff/status/blame`), hôte, `cwd=worktree` — ✅ LIVRÉ.** Calqué
  verbatim sur [repo_test.py:34-55](src/jeanmichel/tools/repo_test.py#L34-L55). Sûr **même en hôte** (sous-commandes
  fixes en lecture seule, pas un shell, ne peut ni écrire ni s'échapper). Débloque à lui seul la requête testée.
- **F2b — « sandbox projet » = conteneur PAR PROJET montant le repo** (chemin fixe `/app`, `WORKDIR /app`).
  Étend la machinerie EXISTANTE `docker/sandbox/` + `jm.sh --build-docker` + colonne `sandbox_image`
  (aujourd'hui per-variant : `jeanmichel-sandbox:py-alpine`/`:node-alpine`, choisi par agent) — on passe au
  grain **par-projet**.
  - **Image par-projet (idée user, meilleure que cloud_init).** Les projets sont hétérogènes (py/node/bun,
    Node 22≠24) → pas de base unique. Dockerfile fourni par le projet (conventionnel `.jm/Dockerfile` ou
    référencé en config projet) → buildé en `jeanmichel-sandbox:project-<id>` (rebuild si hash change).
    **Défaut = alpine minimal (bash+git+coreutils)** si pas de Dockerfile → couvre git/fichiers sans setup.
  - **Le paradoxe réseau DISPARAÎT** : deps installées au **build** (réseau ON, 1×, caché par layers) → le
    conteneur de l'agent tourne **`--network=none` dès la naissance**. Plus de provision-puis-disconnect.
    *Garde-fou* : on build depuis le Dockerfile *commité/propriétaire*, jamais un Dockerfile librement
    édité-puis-buildé par le LLM avec réseau (surface d'exfiltration au build).
  - **Droits (piège docker)** : `docker run --user $(id -u):$(id -g)` → fichiers créés = bonne propriété
    hôte ; HOME écrivable. Shell complet (mv/rm/rename/git/sed/build) **confiné, sans home/clés hôte**.
  - **Local + SSH unifiés sur un checkout autonome** (clone : `.git` self-contained → git marche en
    conteneur, corruption confinée, source jamais montée).
  - **Cycle de vie** : `--rm` + réutilisé tant que vivant (`_container_running`, existe) ; **arrêt propre**
    = ajouter `reap_sandboxes(None)` au `_lifespan` daemon ([api/app.py:57-64](src/jeanmichel/api/app.py#L57-L64),
    aujourd'hui il ne coupe que MCP) ; **balayage orphelins au démarrage** ; idle-reap déjà là.
- **F2c — (naturel une fois F2b en place) `repo_test` bascule DANS le conteneur du projet** (deps présentes,
  isolation réseau). Reste hôte tant que F2b n'est pas rodé.

**F3 — Réorienter la notice `[CODE-REPO]` + contrat de briefing. — ✅ LIVRÉ.**
Plus de chemin live ; on briefe le worker par **ce qu'il faut accomplir dans le repo**, jamais « exécute une
commande à tel chemin » ; `repo_git` ajouté à la liste d'outils citée.

**F4 — (secondaire, si encore observé après F1-F3) garde anti ré-délégation verbatim** (router change
d'approche/escalade après 2× `low` sur tâche quasi identique).

**Livrable demandé** : rapport versionné `docs/20260612_improve_thinking/audit_de_la_bouse.md`
(déjà créé par l'utilisateur — à finaliser : C1-C5 + doctrine + F1-F4 dans leur version à jour).

## Fichiers
- **Étage A — neuf** : `src/jeanmichel/tools/repo_git.py`, `db/migrations/migrate_135_*.sql`,
  `tests/v2/test_repo_git_tool.py`. **Modifiés** : `tools/__init__.py` (registre), `hooks.py` (F3),
  `db/schema.sql` (miroir grants+paradigmes+paradigm_modes), `tests/v2/test_migration_idempotence.py`
  (chaîne+compteurs), `docs/20260612_improve_thinking/audit_de_la_bouse.md`, éventuellement mission DB (F1.4).
- **Étage B — neuf** : `src/jeanmichel/tools/repo_sandbox.py` (conteneur projet), Dockerfile défaut
  `docker/sandbox/Dockerfile.repo-default` (alpine+git), build per-projet dans `jm.sh --build-docker`.
  **Modifiés** : `worktree.py` (checkout autonome local→clone), `api/app.py` (reap au shutdown + sweep
  démarrage), `bash_sandbox.py`/lifecycle partagé, config (chemin Dockerfile projet).

## Vérification (backend `--serve`)
1. Rejouer « de quand datent les derniers commits ? » → délègue → **`repo_git log`** (pas `bash_sandbox`) →
   date réelle, **1 délégation**, pas de boucle 64-LLM.
2. `events.jsonl` : aucun `bash_sandbox` pour inspecter le repo ; il n'apparaît que pour du code généré.
3. Vraie petite édition multi-fichiers : repo_read→repo_edit (gate)→repo_test, diff sur branche, tree live intact.
4. Non-régression hors code : chat + research → `workspace_tools_only` s'applique encore, zéro fuite `code`.
5. ✅ `pytest tests/v2` vert (**732 passed**) — repo_git, chaîne migrate_135, doctrine présente en mode code /
   `workspace_tools_only` absente du code (présente en chat), via le loader réel.

> Reste à valider en `--serve` : items 1-3 (le run E2E réel — le test de la thèse).

## Séquence recommandée (avis éclairé — du rentable/sûr au plus lourd)
1. **Étage A — recâblage prompt + git read-only + audit** (rapide, zéro risque sécu, feedback immédiat) :
   F1 (doctrine des espaces) + F3 (réorientation notice) + **F2a `repo_git` read-only** + `AUDIT.md`.
   → la requête « derniers commits » répond, la chaîne arrête de patiner. *Prérequis du reste : le prompt
   doit déjà enseigner `/repo` et la « sandbox projet ».*
2. **Étage B — sandbox projet (le gros morceau)** : F2b conteneur PAR PROJET (Dockerfile projet, défaut
   alpine ; `--network=none` dès la naissance ; mount repo `/app` ; `--user $(id -u):$(id -g)` ; checkout
   autonome local/ssh) + cycle de vie (shutdown propre + balayage orphelins). → intervention complète
   (mv/rm/rename/git/build), confinée, sans clés hôte.
3. **Étage C — finitions** : `repo_test` (F2c) bascule dans le conteneur du projet ; garde anti
   ré-délégation (F4) si encore observée.

Étage A est livrable et testable seul. B est substantiel (pas « ce soir »). C optionnel/tuning.
