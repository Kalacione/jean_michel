# Analyse : introspection et accès repo — trous dans la raquette

**Date** : 2026-05-22  
**Contexte** : Un router (jean-michel) a demandé accès au README du repo pour contextualiser une tâche d'auto-amélioration. Il n'avait aucun outil adapté, a tenté d'appeler `workspace_manager` comme une fonction, puis a fallbacké sur `ask_human`. Ce comportement révèle plusieurs lacunes structurelles.

---

## 1. État actuel — matrice des grants

### 1.1 Tool grants par agent

| Agent | conv_read_file | workspace_view | workspace_list | workspace_create_file | workspace_str_replace | self_inspect | Autres |
|---|:---:|:---:|:---:|:---:|:---:|:---:|---|
| jean-michel | ✓ | — | — | — | — | — | clock |
| summarizer | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| synthesizer | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| weather-specialist | — | — | — | — | — | — | weather |
| wikipedia-specialist | — | ✓ | ✓ | ✓ | ✓ | — | wikipedia_search, wikipedia_get_page |
| comparator-specialist | — | ✓ | ✓ | ✓ | ✓ | — | — |
| archivist | — | — | — | — | — | — | — |
| critical-thinker | — | ✓ | ✓ | ✓ | ✓ | — | — |
| document-builder | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| workspace-manager | ✓ | ✓ | ✓ | ✓ | ✓ | — | — |
| meta-analyst | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — |
| code-runner | ✓ | ✓ | ✓ | ✓ | ✓ | — | bash_sandbox |

### 1.2 Scopes de self_inspect

Le tool `self_inspect` expose 7 scopes via un seul paramètre string. Il n'existe aucune restriction au niveau du grant — un agent qui a le tool peut appeler n'importe quel scope.

| Scope | Contenu | Agent légitime |
|---|---|---|
| `agents` | Roster agents + tools + paradigmes + sandbox config | meta-analyst |
| `paradigms` | Tous les paradigmes actifs, groupés section/catégorie | meta-analyst |
| `conversations` | Stats d'activité récente, taux d'échec, ask_human freq | meta-analyst |
| `sandbox` | Audit dernières 50 exécutions sandbox | meta-analyst, code-runner |
| `recent_summaries` | Contenu des derniers summary.md | meta-analyst |
| `architecture` | README.md (hardcodé) + db/schema.sql (hardcodé) | meta-analyst, code-runner, document-builder |
| `full` | agents + conversations | meta-analyst |

---

## 2. Problèmes identifiés

### 2.1 Trou #1 — Aucun accès aux fichiers du repo

Les agents n'ont **aucun moyen de lire les fichiers sources du projet** :
- `src/jeanmichel/orchestrator.py`
- `src/jeanmichel/tools/*.py`
- `docs/*.md`
- `tests/*.py`
- Tout autre fichier hors conversation/workspace

Le scope `architecture` de `self_inspect` contourne partiellement ce problème en lisant `README.md` et `db/schema.sql` — mais c'est hardcodé en Python. Ce n'est pas un outil général, c'est un patch.

**Impact** : Le meta-analyst peut proposer des améliorations SQL ou paradigmes, mais ne peut pas analyser le code source réel. Le code-runner écrit du code sans pouvoir lire les patterns existants. Un agent documentaire ne peut pas lire les docs existantes.

### 2.2 Trou #2 — self_inspect non scopé au niveau du grant

Un seul grant `self_inspect` donne accès à tous les scopes, y compris :
- `recent_summaries` — contenu de conversations précédentes
- `conversations` — statistiques d'activité (potentiellement sensible)
- `sandbox` — audit d'exécution de code

Si un agent intermédiaire (document-builder, code-runner) avait `self_inspect`, il pourrait appeler `scope="conversations"` ou `scope="recent_summaries"` sans que ça soit intentionnel.

**Impact** : Pas de contrôle de granularité. La solution actuelle (n'accorder self_inspect qu'au meta-analyst) est une workaround, pas une architecture.

### 2.3 Trou #3 — Workspace tools mal scopés

Plusieurs agents ont `workspace_create_file` et `workspace_str_replace` (accès écriture) sans raison claire :

- **summarizer** : sa mission est de résumer du texte entrant. Il n'a pas vocation à créer des fichiers workspace. Le grant `workspace_as_shared_memory` lui a été accordé pour éviter les doublons inter-agents — mais ça lui donne aussi un accès écriture complet.
- **synthesizer** : même cas. Sa mission est de merger des sorties pour le human. L'écriture workspace n'est pas son métier.
- **comparator-specialist** : orchestre des delegations, lit des résultats. N'a pas besoin d'écrire.
- **critical-thinker** : analyse des claims. Pourrait légitimement écrire son analyse, mais est-ce sa responsabilité ou celle du document-builder ?
- **wikipedia-specialist** : **a légitimement besoin d'écrire** pour persister les articles fetchés (fix du bug support_files). C'est le cas d'usage correct.

**Impact** : Le principe de moindre privilège est violé. Chaque agent avec écriture workspace peut créer ou modifier n'importe quel fichier dans le workspace, y compris les fichiers d'autres agents.

### 2.4 Trou #4 — Le router est aveugle à son propre système

`jean-michel` n'a que `clock` et `conv_read_file`. Il ne peut pas :
- Lire le README pour contextualiser une requête
- Savoir quels agents sont disponibles au-delà de ce qui est injecté dans son prompt
- Accéder à l'architecture du projet pour déléguer intelligemment

La réponse correcte à la conversation `14-05_8ad8f9ec7d6d` aurait dû être de déléguer au meta-analyst, qui lui a `self_inspect`. Mais jean-michel n'a pas les outils pour s'en rendre compte seul.

---

## 3. Solutions proposées

### 3.1 Nouveau tool : `repo_read_file`

Un outil **lecture seule** sur `REPO_ROOT`, avec les mêmes protections que `conv_read_file` (traversal guard, max_bytes).

```python
# Périmètre : tout fichier sous REPO_ROOT
# Exclusions dures : jeanmichel.db, conversations/, *.pyc, __pycache__
# Max bytes : 100_000 (identique à conv_read_file)
# Paramètre : relative_path (depuis REPO_ROOT)
```

**Grants proposés** :

| Agent | Justification |
|---|---|
| meta-analyst | Lire les sources pour proposer des changements précis |
| code-runner | Lire les patterns existants avant d'écrire du code |
| document-builder | Lire les docs existantes avant d'en produire de nouvelles |

**Pas de grant pour** : jean-michel (router, pas de raison de lire les sources), wikipedia-specialist, weather-specialist, summarizer, synthesizer, comparator-specialist, critical-thinker.

### 3.2 Découpage de self_inspect en scopes discrets

Deux options :

**Option A — Plusieurs outils distincts** (plus clean, plus de code)
- `self_inspect_config` → scopes agents + paradigms
- `self_inspect_activity` → scopes conversations + sandbox + recent_summaries
- `self_inspect_architecture` → scope architecture

**Option B — Restriction par paradigme** (moins de code, moins robuste)
- Garder un seul outil
- Ajouter un paradigme par type d'agent qui restreint les scopes autorisés
- Le LLM peut ignorer un paradigme, c'est fragile

**Recommandation** : Option A. Le découpage en outils distincts est la seule garantie réelle. Granularité claire, grants DB précis, pas de confiance aveugle dans le LLM.

### 3.3 Raffinage des grants workspace

Introduire deux niveaux explicites :

**Lecture seule** (`workspace_view` + `workspace_list`) :
- summarizer — vérifie si un résumé existe déjà, pas besoin d'écrire
- synthesizer — lit les artifacts pour les merger
- comparator-specialist — lit les données collectées par les spécialistes

**Lecture-écriture** (`workspace_view` + `workspace_list` + `workspace_create_file` + `workspace_str_replace`) :
- wikipedia-specialist — persiste les articles fetchés
- critical-thinker — écrit ses analyses
- document-builder — produit des documents
- workspace-manager — gère le workspace
- meta-analyst — écrit ses propositions
- code-runner — écrit et exécute du code

**Retirer** :
- `workspace_create_file` et `workspace_str_replace` de : summarizer, synthesizer, comparator-specialist

### 3.4 Paradigme dédié : `repo_read_discipline`

À créer pour les agents avec `repo_read_file` :
- Ne jamais modifier les fichiers lus (lecture seule)
- Ne lire que ce qui est nécessaire à la tâche (pas de scan exhaustif)
- Référencer les extraits lus dans les propositions (traçabilité)

---

## 4. Plan d'implémentation

### Phase 1 — Raffinage des grants workspace (low risk, immediate)
1. Retirer `workspace_create_file` et `workspace_str_replace` de summarizer, synthesizer, comparator-specialist
2. Mettre à jour `schema.sql` + migration SQL
3. Mettre à jour `agent_workspace_grants` (retirer summarizer, synthesizer, comparator-specialist)
4. Mettre à jour les tests

### Phase 2 — `repo_read_file` tool (medium effort)
1. Créer `src/jeanmichel/tools/repo_read_file.py`
   - Pas de `make_spec` (pas de binding conv_folder) — outil stateless avec `SPEC`
   - Guard : exclusions dures (db, conversations/, *.pyc)
   - Max bytes : 100_000
2. Enregistrer dans `build_registry`
3. Accorder à meta-analyst, code-runner, document-builder dans DB + schema.sql
4. Tests

### Phase 3 — Découpage self_inspect (medium effort, breaking change)
1. Créer `self_inspect_config.py`, `self_inspect_activity.py` (ou refactorer les scopes)
2. Migrer les grants DB
3. Mettre à jour les paradigmes `inspect_before_proposing`
4. Tests

### Phase 4 — Paradigme `repo_read_discipline`
1. Insérer paradigme dans DB + schema.sql
2. Binder aux agents concernés

---

## 5. Ce qu'on ne change pas

- Le router (jean-michel) **n'a pas accès** au repo. Une requête qui nécessite une analyse du repo doit être déléguée au meta-analyst. C'est le routing correct.
- La validation humaine reste systématique : les agents produisent des propositions dans le workspace, l'humain applique.
- `self_inspect` actuel reste intact jusqu'à la Phase 3 — migration non destructive.
