# Plan d'implémentation — Workspace agents + Sandbox Docker

**Pour** : agent Claude 4.6 en mode agent dans VSCode, avec sous-agents.
**Projet** : Jean-Michel (assistant IA local à agents spécialisés).
**Référence repo** : `README.md` à la racine, `db/schema.sql`, `src/jeanmichel/`.

Ce plan est autonome — il documente ce qu'il faut savoir du projet, pas seulement ce qu'il faut coder.

---

## 0. Contexte projet (à lire avant tout)

**Jean-Michel** est un assistant IA multi-agents fonctionnant 100% local via Ollama (modèle Gemma 4). L'architecture est :
- Un **orchestrateur Python** (pas un LLM) qui dépile les requêtes, gère la BDD SQLite et écrit les artefacts sur disque.
- Des **agents** définis en BDD : `jean-michel` (router), `summarizer`, `weather-specialist`, `wikipedia-specialist`, `comparator-specialist`, `synthesizer` (finalizer), `archivist` (finalizer), `critical-thinker`.
- Des **outils Python** (`src/jeanmichel/tools/`) que les agents appellent via tool calls natifs Gemma 4.
- Une **conversation** = un dossier horodaté `conversations/{date}_{uuid}/` contenant tous les artefacts (prompts rendus, pensées capturées, briefings, réponses).

**Lecture obligatoire avant de coder** :
- `README.md` — vue d'ensemble.
- `docs/PROMPT_SKELETON.md` — comment les prompts sont rendus.
- `docs/HOWTO_ADD_SPECIALIST_OR_TOOL.md` — pattern pour ajouter outils et agents.
- `src/jeanmichel/tools/_base.py` — définition de `ToolSpec`.
- `src/jeanmichel/tools/__init__.py` — comment `build_registry` assemble les outils.
- `src/jeanmichel/tools/conv_read_file.py` — exemple d'outil context-bound qui sert de référence pour la validation de chemin.
- `src/jeanmichel/orchestrator.py` — comment les outils sont dispatchés et leurs résultats persistés.

---

## 1. Objectif global

Donner aux futurs agents Jean-Michel la capacité de :
- **Manipuler des fichiers** dans un sous-dossier `workspace/` de leur conversation, sans pouvoir corrompre les artefacts de l'orchestrateur ni la BDD.
- **Exécuter du code** dans un sandbox Docker isolé (pas d'accès réseau par défaut, pas d'accès au filesystem hôte).

Aucun agent existant n'utilisera ces capacités. Elles sont mises à disposition pour les agents futurs (`code-runner`, `data-analyst`, `document-builder`).

---

## 2. Principes non négociables

1. **Aucun agent LLM ne doit pouvoir écrire dans la BDD**. Toutes les modifications BDD passent par l'orchestrateur ou les outils admin manuels.
2. **Aucun agent LLM ne doit pouvoir écrire dans `conversation.md`, `summary.md`, ou les artefacts root** de la conversation. Lecture seule.
3. **Path traversal** : toute opération sur fichier valide le chemin via `Path.resolve()` + `is_relative_to()` avant tout I/O.
4. **Sandbox Docker** : `--network=none --cap-drop=ALL` non-root, ressources bornées, mounts contrôlés.
5. **Opt-in par agent** : un nouvel outil n'est utilisable que si l'agent a le grant correspondant en BDD (`agent_tools` table).

---

## 3. Distinction conceptuelle critique (à bien intégrer)

Deux mondes qui ne se mélangent pas :

| Concept | Nature | Géré par | Inventaire |
|---|---|---|---|
| **Artefacts de conversation** (table `artifacts`) | Trace immuable du dialogue agent : prompt rendu, thought capté, briefing émis, tool call, tool response, réponse produite | Orchestrateur | Table `artifacts` (existante) |
| **Fichiers workspace** | Outputs vivants des agents : CSV, scripts, extractions, fichiers temporaires modifiables/supprimables | Agents via outils `workspace_*` | **Pas d'inventaire BDD — le filesystem est l'inventaire** |
| **Exécutions sandbox** | Audit trail structuré des commandes exécutées dans le sandbox Docker | Outil `bash_sandbox` | Table dédiée `sandbox_executions` (à créer en phase 3) |

**La table `artifacts` n'est pas modifiée** par ce plan. Les fichiers workspace ne sont pas tracés en BDD. Si on veut savoir ce qu'il y a dans un workspace, on le liste (`workspace_list` ou `ls` côté humain).

Pour les commandes sandbox, le `tool_response` artifact existant capture déjà commande + résultat dans le flux conversationnel ; la table `sandbox_executions` complémente avec une vue structurée queryable a posteriori (utile pour debug, audit sécurité, métriques).

---

## 4. Phasage

| Phase | Périmètre | Durée estimée |
|---|---|---|
| **Phase 1** | Outils workspace (Python pur, pas de Docker) + filtrage par grants | 1-2 jours |
| **Phase 2** | Sandbox Docker `bash_sandbox` | 2-3 jours |
| **Phase 3** | Documentation et tests d'intégration | 0.5 jour |

La Phase 2 dépend de la Phase 1 (réutilise le filtrage grants et le pattern context-bound). La Phase 3 dépend des deux précédentes.

**Le schéma BDD est déjà en place** dans `db/schema.sql` livré avec ce plan. Il inclut :
- Les tables existantes (sections, categories, paradigms, agents, agent_paradigms, paradigm_modes, agent_tools, conversations, requests, artifacts).
- Les 3 nouvelles tables nécessaires : `agent_workspace_grants`, `agent_sandbox_grants`, `sandbox_executions`.

Aucune migration à écrire. L'agent qui exécute ce plan part d'une BDD à plat avec `rm jeanmichel.db && sqlite3 jeanmichel.db < db/schema.sql`.

---

## 5. PHASE 1 — Outils workspace (Python pur)

### 5.1 Pré-requis

- Le repo est cloné, `./jm.sh --install` a été exécuté, le venv est actif.
- Lire `src/jeanmichel/tools/conv_read_file.py` qui implémente déjà la validation de chemin sandboxée. Le nouveau pattern s'en inspire.

### 5.2 Constantes communes

Ajouter dans `src/jeanmichel/config.py` :

```python
# Workspace soft quota per conversation, in bytes.
WORKSPACE_QUOTA_BYTES = 256 * 1024 * 1024  # 256 MB
```

### 5.3 Fichiers à créer

Quatre nouveaux fichiers dans `src/jeanmichel/tools/` :

- `workspace_create_file.py`
- `workspace_str_replace.py`
- `workspace_view.py`
- `workspace_list.py`

**Pattern commun à tous** : chaque outil est `context-bound` (a besoin de `conv_folder`), expose une fonction `make_spec(conv_folder: Path) -> ToolSpec`, et utilise une closure pour capturer le workspace root.

**Helper partagé** : créer un module `src/jeanmichel/tools/_workspace.py` exportant les primitives communes :

```python
"""Shared workspace primitives used by workspace_* tools.

Centralizes path validation, quota check, and workspace root resolution.
"""

from __future__ import annotations
from pathlib import Path
from ..config import WORKSPACE_QUOTA_BYTES


def workspace_root_for(conv_folder: Path) -> Path:
    """Return the absolute workspace root for a conversation, creating it if missing."""
    root = (conv_folder / "workspace").resolve()
    root.mkdir(exist_ok=True)
    return root


def safe_resolve(workspace_root: Path, relative_path: str) -> Path:
    """Resolve `relative_path` inside `workspace_root`. Raise ValueError on escape.

    Refuses absolute paths, '..' that exits, and symlinks pointing outside.
    """
    if not relative_path or relative_path == ".":
        return workspace_root
    candidate = (workspace_root / relative_path).resolve()
    if not candidate.is_relative_to(workspace_root):
        raise ValueError(f"Path escapes workspace: {relative_path!r}")
    return candidate


def workspace_size(workspace_root: Path) -> int:
    """Total bytes used by all files in the workspace."""
    return sum(p.stat().st_size for p in workspace_root.rglob("*") if p.is_file())


def quota_remaining(workspace_root: Path) -> int:
    """Bytes still available before quota."""
    return max(0, WORKSPACE_QUOTA_BYTES - workspace_size(workspace_root))
```

### 5.4 Tâche 1.A — `workspace_create_file.py`

**Comportement** :
- Refuse l'écrasement (le fichier doit ne pas exister).
- Refuse si l'écriture ferait dépasser `WORKSPACE_QUOTA_BYTES`.
- Crée les sous-dossiers parents si nécessaire (`mkdir parents=True`).
- **N'écrit rien en BDD**. Le filesystem est l'inventaire.
- Retour JSON : `{"path": "...", "bytes_written": N}` ou `{"error": "..."}`.

**Critères d'acceptation** :
- [ ] Création nominale : `workspace_create_file("notes.md", "hello", "test")` crée `conversations/.../workspace/notes.md`.
- [ ] Path traversal refusé : `relative_path="../conversation.md"` → erreur, fichier root non touché.
- [ ] Pas d'écrasement : créer deux fois `notes.md` → deuxième appel renvoie une erreur, fichier original intact.
- [ ] Quota : remplir le workspace à 256 Mo + 1 byte → erreur explicite.
- [ ] Sous-dossier : `relative_path="data/results.json"` crée le dossier `data/`.
- [ ] **Aucune ligne ajoutée dans la table `artifacts`** suite à l'opération.

### 5.5 Tâche 1.B — `workspace_str_replace.py`

**Comportement** :
- `old_str` doit apparaître **exactement une fois** dans le fichier (sinon erreur).
- `new_str` peut être vide (= suppression).
- Écriture atomique : écrire dans un fichier temporaire `.tmp` puis renommer.
- **N'écrit rien en BDD**.
- Retour JSON : `{"path": "...", "occurrences_replaced": 1, "bytes_after": N}`.

**Critères d'acceptation** :
- [ ] Remplacement nominal d'une chaîne unique.
- [ ] 0 occurrence → erreur explicite.
- [ ] ≥2 occurrences → erreur listant le nombre.
- [ ] Suppression (`new_str=""`).
- [ ] Path traversal refusé.
- [ ] Atomicité : si l'écriture est interrompue (simuler via mock), le fichier original n'est pas corrompu.

### 5.6 Tâche 1.C — `workspace_view.py`

**Comportement** :
- Lit un fichier ou liste un dossier.
- **Lecture étendue** : un fichier root de la conversation (hors workspace) est lisible mais jamais modifiable. Cela couvre le cas d'usage de `conv_read_file`.
- `view_range=[start, end]` permet de lire des plages de lignes (1-indexé, `-1` = fin).
- `max_bytes` (défaut 100000) tronque la sortie.
- Retour JSON : `{"path": "...", "content": "...", "truncated": bool}` ou `{"directory": "...", "entries": [...]}`.

**Différence avec `conv_read_file` actuel** :
- Couvre le même périmètre (lecture root).
- Ajoute le listing.
- Ajoute la lecture du workspace.
- Ajoute `view_range`.

**Migration de `conv_read_file`** : ne pas le supprimer dans cette phase. Garder les deux outils en parallèle. Une migration de dépréciation se fera plus tard quand tous les agents auront migré.

**Critères d'acceptation** :
- [ ] Lecture nominale d'un fichier root (équivalent `conv_read_file`).
- [ ] Lecture nominale d'un fichier workspace.
- [ ] Listing du workspace (`relative_path=""`).
- [ ] `view_range=[1, 10]` retourne 10 lignes.
- [ ] `view_range=[5, -1]` retourne de la ligne 5 à la fin.
- [ ] Path traversal refusé.
- [ ] Fichier non-UTF-8 → erreur explicite (pas de crash).
- [ ] Tronquage > `max_bytes` retourne `truncated: true`.

### 5.7 Tâche 1.D — `workspace_list.py`

**Comportement** :
- Listing en arbre, max 2 niveaux de profondeur.
- Pour chaque entrée : `name`, `type` (`file|directory`), `size_bytes` (fichiers), `modified_at`.
- Sub-path optionnel pour lister un sous-dossier.

**Critères d'acceptation** :
- [ ] Workspace vide → arbre vide.
- [ ] Workspace avec arbre profond → coupe à 2 niveaux.
- [ ] Tri lexicographique stable.
- [ ] Path traversal refusé.

### 5.8 Tâche 1.E — Enregistrement dans `tools/__init__.py`

Modifier `src/jeanmichel/tools/__init__.py` pour intégrer les 4 nouveaux outils dans `build_registry`. Pattern à suivre : lire la fonction existante et imiter le pattern context-bound de `conv_read_file`.

### 5.9 Tâche 1.F — Tests automatisés

Créer `tests/test_workspace_tools.py`. Format pytest. Doit tester chaque critère d'acceptation des tâches 1.A à 1.D.

**Structure suggérée** :
```python
import json
from pathlib import Path
import pytest

from jeanmichel.tools.workspace_create_file import make_spec as create_spec

@pytest.fixture
def tmp_conv(tmp_path):
    """Provide a temporary conversation folder with workspace/."""
    (tmp_path / "workspace").mkdir()
    return tmp_path

def test_create_file_nominal(tmp_conv):
    spec = create_spec(tmp_conv)
    result = json.loads(spec.handler(
        relative_path="notes.md",
        content="hello",
        description="test",
    ))
    assert "error" not in result
    assert (tmp_conv / "workspace" / "notes.md").read_text() == "hello"

def test_create_file_path_traversal_blocked(tmp_conv):
    spec = create_spec(tmp_conv)
    result = json.loads(spec.handler(
        relative_path="../escape.md",
        content="bad",
        description="attack",
    ))
    assert "error" in result
    assert "escapes" in result["error"].lower()
    assert not (tmp_conv / "escape.md").exists()
```

Tous les critères d'acceptation listés en 5.4-5.7 doivent avoir un test.

**Critère d'acceptation phase 1** :
- [ ] `pytest tests/test_workspace_tools.py` passe à 100%.
- [ ] Aucune régression sur les tests existants : `pytest tests/` complet vert.

### 5.10 Tâche 1.G — Helpers DB pour lire les grants

Ajouter dans `src/jeanmichel/db.py` deux fonctions qui exploitent les tables `agent_workspace_grants` et `agent_sandbox_grants` (déjà présentes dans `db/schema.sql`) :

```python
def has_workspace_grant(conn: sqlite3.Connection, agent_id: int) -> bool:
    """Return True if the agent has write access to the workspace."""
    row = conn.execute(
        "SELECT 1 FROM agent_workspace_grants WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    return row is not None


def load_sandbox_grants(conn: sqlite3.Connection, agent_id: int) -> list[str]:
    """Return the list of sandbox commands granted to this agent."""
    return [
        r["command"] for r in conn.execute(
            "SELECT command FROM agent_sandbox_grants WHERE agent_id = ?",
            (agent_id,),
        ).fetchall()
    ]
```

**Critères d'acceptation** :
- [ ] `has_workspace_grant` retourne `True` après insertion d'une ligne, `False` sinon.
- [ ] `load_sandbox_grants` retourne la liste correcte ou liste vide.

### 5.11 Tâche 1.H — Filtrage côté orchestrateur

Modifier `src/jeanmichel/orchestrator.py` ou `src/jeanmichel/prompts.py:tools_payload_for_agent` : avant de construire le payload des tools pour un agent, vérifier que les outils workspace nécessitant le grant ne sont exposés qu'aux agents grantés.

**Approche** : les outils en écriture (`workspace_create_file`, `workspace_str_replace`) ne sont visibles dans le prompt que si l'agent a `has_workspace_grant=True`. Les outils en lecture (`workspace_view`, `workspace_list`) sont toujours visibles puisqu'ils ne mutent rien.

**Critères d'acceptation** :
- [ ] Un agent sans grant qui appellerait quand même `workspace_create_file` → erreur côté orchestrateur (`tool_response` d'erreur), pas un crash.
- [ ] Le test couvre les deux cas : avec grant (ça passe) et sans grant (refus).

---

## 6. PHASE 2 — Sandbox Docker `bash_sandbox`

### 6.1 Pré-requis

Phase 1 terminée. Docker installé sur le poste de dev. Tester `docker --version` avant de commencer.

### 6.2 Tâche 2.A — Image Docker

Créer `docker/sandbox/Dockerfile` :

```dockerfile
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        jq \
        curl ca-certificates \
        git \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Pre-create a non-root user.
RUN useradd -m -s /bin/bash -u 1000 sandbox

# Pre-create a python venv as sandbox user (reusable across runs).
USER sandbox
WORKDIR /home/sandbox
RUN python3 -m venv venv

# Default working dir when commands are exec'd.
WORKDIR /workspace
```

**Note** : `curl` et `git` sont pré-installés dans l'image mais inutilisables tant que `--network=none` est appliqué au runtime. Ils sont prêts pour un usage futur où on monterait un Docker network contrôlé.

### 6.3 Tâche 2.B — Commande `jm.sh --build-docker`

Modifier `jm.sh` pour ajouter une sous-commande qui builde l'image. Format à respecter : voir comment `--install` ou `--paradigm-matrix` sont implémentés.

```bash
elif [[ "$1" == "--build-docker" ]]; then
    docker build -t jeanmichel-sandbox:24.04 docker/sandbox/
    echo "✓ Sandbox image built: jeanmichel-sandbox:24.04"
    exit 0
```

**Critères d'acceptation** :
- [ ] `./jm.sh --build-docker` crée l'image en moins de 5 minutes la première fois.
- [ ] Re-exécution : utilise le cache Docker, dure < 10 secondes.

### 6.4 Tâche 2.C — Outil `bash_sandbox.py`

Créer `src/jeanmichel/tools/bash_sandbox.py`.

> **Note** : la table `sandbox_executions` existe déjà dans `db/schema.sql`. Aucune migration à écrire — il suffit d'y insérer les lignes via la fonction helper décrite plus bas.

**Lifecycle du container** :
- Container nommé `jm-sandbox-{conv_uuid}` — un par conversation.
- Démarrage paresseux : créé au premier appel `bash_sandbox` de la conversation.
- Démarré avec `tail -f /dev/null` pour rester vivant.
- Persistance entre les tours (le workspace est conservé).
- Nettoyage à la clôture de conversation : ajouter un hook dans l'orchestrateur.

**Setup au démarrage du container** :
```bash
docker run -d --rm \
  --name jm-sandbox-{uuid} \
  --network=none \
  --cap-drop=ALL \
  --memory=512m --cpus=1 \
  --user sandbox \
  -v {abs_workspace_path}:/workspace:rw \
  -w /workspace \
  jeanmichel-sandbox:24.04 \
  tail -f /dev/null
```

**Exécution de commande** :
```bash
docker exec jm-sandbox-{uuid} bash -c "<command>"
```

avec timeout via `subprocess.run(..., timeout=N)`.

**Garde-fous niveau handler** :
1. Vérifier l'agent a le grant `bash_sandbox` dans `agent_tools` (déjà filtré par l'orchestrateur, mais double-check côté handler).
2. Vérifier le **premier mot** de la commande figure dans `agent_sandbox_grants` pour cet agent.
3. Lancer/réutiliser le container.
4. Exécuter avec timeout.
5. **Enregistrer en BDD** : insérer une ligne dans `sandbox_executions` avec command, exit_code, duration_ms.
6. Retourner le JSON :
```json
{
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "duration_ms": 1234,
  "truncated": false
}
```

**Note sur l'enregistrement BDD** : c'est le seul outil qui écrit en BDD (outre l'orchestrateur). C'est un cas particulier justifié par l'audit sécurité. La fonction d'insertion est dans `db.py`, pas dans le tool directement.

Ajouter dans `src/jeanmichel/db.py` :

```python
def record_sandbox_execution(
    conn: sqlite3.Connection,
    request_id: str,
    command: str,
    exit_code: int | None,
    duration_ms: int,
    stdout_path: str | None = None,
    stderr_path: str | None = None,
) -> None:
    """Insert an audit row for a sandbox command execution."""
    conn.execute(
        "INSERT INTO sandbox_executions "
        "(request_id, command, exit_code, duration_ms, stdout_path, stderr_path, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (request_id, command, exit_code, duration_ms, stdout_path, stderr_path),
    )
    conn.commit()
```

L'outil `bash_sandbox` reçoit le `request_id` du contexte d'appel — mécanisme à mettre en place : `make_spec(conv_folder, request_id_provider)` où `request_id_provider` est une closure injectée par l'orchestrateur. Pattern existant à étudier : voir comment l'orchestrateur passe le contexte aux outils context-bound.

**Critères d'acceptation** :
- [ ] Exécution `python3 -c "print(1+1)"` retourne `{"exit_code": 0, "stdout": "2\n", ...}`.
- [ ] Une ligne apparaît dans `sandbox_executions` après l'exécution.
- [ ] Commande non-grantée (`curl`) → erreur explicite avant tout exec, **et** ligne en BDD avec exit_code NULL et command = la tentative refusée.
- [ ] Timeout 30s respecté.
- [ ] `--network=none` vérifié : `curl https://example.com` retourne une erreur réseau (test à faire si curl est granted, sinon ne s'applique pas).
- [ ] Le workspace `/workspace` dans le container = même contenu que `conversations/.../workspace/` côté host.
- [ ] Une écriture dans `/workspace` depuis le container est visible côté host.
- [ ] Le container survit entre 2 appels successifs dans la même conversation.

### 6.5 Tâche 2.D — Hook de nettoyage

Modifier `src/jeanmichel/orchestrator.py` : ajouter une méthode `cleanup_sandbox()` appelée à la clôture de la conversation. Doit lancer `docker rm -f jm-sandbox-{conv_uuid}` si le container existe.

Modifier `debug/clean_convs.py` : étendre la purge pour aussi nettoyer les containers Docker orphelins (containers `jm-sandbox-*` qui ne correspondent plus à une conversation active).

**Critère d'acceptation** :
- [ ] Après la fin d'une conversation, `docker ps -a` ne liste plus le container associé.
- [ ] `./jm.sh --clean` purge les conversations **et** les containers orphelins.

### 6.6 Tâche 2.E — Tests d'intégration sandbox

Créer `tests/test_sandbox.py`. Tests à implémenter :

- Container démarré paresseusement et persistant entre 2 appels.
- Commande non-grantée refusée.
- Timeout fonctionnel.
- Réseau bloqué.
- Écriture workspace visible des deux côtés.
- Nettoyage du container après cleanup.
- **Ligne `sandbox_executions` créée correctement** (vérifier command, exit_code, duration_ms).
- **Cascade DELETE** : supprimer la conversation entraîne la suppression des `sandbox_executions` liées.

**Note** : ces tests requièrent Docker présent. Marquer le module avec `pytest.mark.docker` pour pouvoir les skip dans une CI sans Docker.

**Critère d'acceptation** :
- [ ] `pytest tests/test_sandbox.py` passe à 100% sur un poste avec Docker.

---

## 7. PHASE 3 — Documentation et tests d'intégration

### 7.1 Tâche 3.A — Mise à jour README.md

Section "Stack" : ajouter Docker comme dépendance optionnelle pour la sandbox.

Section "Installation" : ajouter `./jm.sh --build-docker` aux étapes optionnelles.

Section "Arborescence du repo" : ajouter `docker/sandbox/Dockerfile` et les nouveaux fichiers `tools/workspace_*` + `tools/bash_sandbox.py`.

Nouvelle section "Workspace agents" expliquant le pattern :

```markdown
## Workspace agents

Some agents can manipulate files within their conversation's workspace folder
(`conversations/.../workspace/`). This space is sandboxed: agents cannot touch
artefacts at the conversation root, nor escape the workspace.

Tools available:
- `workspace_create_file` — create a new file
- `workspace_str_replace` — edit an existing file
- `workspace_view` — read files (extended scope: workspace + readonly conversation root)
- `workspace_list` — directory tree

Granting requires both:
- `agent_tools` row for each tool the agent uses
- `agent_workspace_grants` row to enable write access (read-only otherwise)

Soft quota: 256 MB per workspace.

Workspace files are NOT tracked in the `artifacts` table — the filesystem is
the inventory. Use `workspace_list` (LLM-side) or `ls` (human-side) to see what
is in there.
```

Section "Sandbox" pour `bash_sandbox` :

```markdown
## Sandbox

The `bash_sandbox` tool runs shell commands inside an isolated Docker
container, mounted on the conversation workspace.

Setup: `./jm.sh --build-docker` (prebuild the `jeanmichel-sandbox:24.04`
image; required only once or after Dockerfile updates).

Per-agent grants (in DB):
- `agent_tools` row with `tool_code='bash_sandbox'`
- `agent_sandbox_grants` rows for each authorized binary (e.g. 'python3',
  'jq', 'cat')

Hard guarantees:
- `--network=none` (no internet access)
- `--cap-drop=ALL` (no Linux capabilities)
- Non-root user
- Memory and CPU limits

Audit trail: every command execution is recorded in the `sandbox_executions`
table (queryable a posteriori). The `tool_response` artifact in the
conversation flow captures the same data in the conversational context.
```

### 7.2 Tâche 3.B — Mise à jour HOWTO_ADD_SPECIALIST_OR_TOOL.md

Ajouter une section "Cas particuliers" sur l'octroi de l'accès workspace + sandbox. Inclure un exemple SQL de création d'un agent `code-runner` avec workspace + sandbox grants :

```sql
-- Example: a code-runner agent with workspace write and python sandbox
INSERT INTO agents (code, name, role, mission, ...) VALUES ('code-runner', ...);

INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, t FROM agents, (
  SELECT 'workspace_create_file' AS t UNION SELECT 'workspace_str_replace'
  UNION SELECT 'workspace_view' UNION SELECT 'workspace_list'
  UNION SELECT 'bash_sandbox'
) WHERE code = 'code-runner';

INSERT INTO agent_workspace_grants (agent_id)
SELECT id FROM agents WHERE code = 'code-runner';

INSERT INTO agent_sandbox_grants (agent_id, command) VALUES
  ((SELECT id FROM agents WHERE code='code-runner'), 'python3'),
  ((SELECT id FROM agents WHERE code='code-runner'), 'cat'),
  ((SELECT id FROM agents WHERE code='code-runner'), 'ls'),
  ((SELECT id FROM agents WHERE code='code-runner'), 'jq');
```

### 7.3 Tâche 3.C — Test d'intégration end-to-end

Créer `tests/test_workspace_e2e.py`. Scénario : un agent fictif (via MockClient) émet plusieurs tool calls workspace en séquence, l'orchestrateur les dispatche, les fichiers apparaissent au bon endroit, **aucune ligne n'apparaît dans la table `artifacts`** pour les opérations workspace.

Si la phase 3 est intégrée : un test similaire pour `bash_sandbox` qui vérifie qu'une ligne apparaît dans `sandbox_executions`.

**Critère d'acceptation** :
- [ ] Le test e2e passe.

---

## 8. Découpage suggéré pour sous-agents

Si tu utilises Claude 4.6 en mode agent avec sous-agents, voici un découpage qui parallélise bien :

| Sous-agent | Tâches | Indépendance |
|---|---|---|
| **A** | 1.A + 1.B (create_file + str_replace, qui partagent `_workspace.py`) | Démarrer en parallèle de B |
| **B** | 1.C + 1.D (view + list, lecture seule, indépendants) | Parallèle à A |
| **C** | 1.E + 1.F (registry + tests pytest) | Démarre après A et B (dépend des spec créées) |
| **D** | 1.G + 1.H (helpers DB + filtrage orchestrateur) | Démarre après C |
| **E** | 2.A + 2.B (image Docker + jm.sh --build-docker) | Démarre quand Phase 1 terminée |
| **F** | 2.C + 2.D + 2.E (bash_sandbox + cleanup + tests) | Démarre après E |
| **G** | 3.A + 3.B + 3.C (docs + e2e) | Dernière étape, tout doit être fait |

Reco : **faire une PR par phase, pas par sous-agent**. Phase 1 = 1 PR. Phase 2 = 1 PR. Phase 3 = 1 PR. Réduit le risque de conflit de merge.

---

## 9. Rappels critiques pour l'agent qui exécute ce plan

1. **Lire `tools/conv_read_file.py` avant d'écrire les outils workspace**. C'est la référence du pattern de validation de chemin.
2. **Tous les retours d'outils sont des strings JSON** (cohérent avec la convention orchestrateur). `json.dumps({...})` partout, jamais retourner un dict directement.
3. **Pas de modification du contenu des paradigmes ni de la structure des prompts** dans ces phases — uniquement l'infrastructure.
4. **La table `artifacts` n'est jamais touchée** par ce plan. Pas de nouveau `kind`, pas de modification de la CHECK constraint. Les fichiers workspace ne sont pas des artefacts conversationnels (cf. §3).
5. **Tester avant de committer**. Si `pytest tests/` ne passe pas, ne pas merger.
6. **Frontmatter des artefacts existants** : les tool_call et tool_response du `bash_sandbox` doivent avoir le même frontmatter YAML que les autres outils. Voir `src/jeanmichel/persistence.py:_frontmatter`.
7. **En cas de doute** sur une décision architecturale non couverte ici : demander à l'utilisateur, ne pas trancher seul. Le projet a une doctrine forte (KISS, DB = source de vérité, rétrocompatibilité) qu'un agent peut violer involontairement.

---

## 10. Critères de succès finaux

Une fois le plan exécuté complètement :

- [ ] Un nouvel agent peut être créé via le HOWTO et bénéficier de workspace + sandbox.
- [ ] Aucun agent existant n'est affecté en comportement (régression zéro vérifiée par smoke tests).
- [ ] `./jm.sh --build-docker` fonctionne en moins de 5 minutes.
- [ ] `pytest tests/` complet vert, incluant les nouveaux modules.
- [ ] Path traversal vérifié refusé sur tous les outils workspace.
- [ ] Quota 256 Mo enforced.
- [ ] Sandbox sans réseau vérifié.
- [ ] Container nettoyé en fin de conversation.
- [ ] Table `sandbox_executions` reçoit bien une ligne par exécution.
- [ ] Aucune nouvelle ligne dans la table `artifacts` pour les opérations workspace.
- [ ] README et HOWTO à jour.

Si l'un de ces points n'est pas validé, la phase n'est pas terminée.
