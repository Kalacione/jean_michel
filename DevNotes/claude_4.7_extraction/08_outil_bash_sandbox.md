# Outil — `bash_sandbox`

## Définition

Exécute une commande shell dans un container Docker isolé, monté sur le workspace de la conversation. Calqué sur le `bash_tool` du sysprompt Claude, mais avec un set de commandes restreint et le filesystem hôte hors d'atteinte.

## Paramètres

| Param | Type | Required | Description |
|---|---|:-:|---|
| `command` | string | ✓ | Commande shell à exécuter. Le binaire doit être dans la liste blanche grantée à l'agent. |
| `description` | string | ✓ | Justification de la commande (loggée). |
| `timeout_seconds` | integer | (default 30) | Timeout d'exécution. Hard cap : 60 secondes. |

## Comportement

1. Vérifier que l'agent a le grant `bash_sandbox` dans `agent_tools`.
2. Vérifier que le **premier mot de la commande** figure dans `agent_sandbox_grants` pour cet agent.
3. Démarrer (ou réutiliser) le container Docker associé à la conversation :
   - Image : `jeanmichel-sandbox:24.04` (build local)
   - Mounts : `/workspace` (rw, sur conversations/{id}/workspace) ; `/conversation` (ro, sur conversations/{id}/) — ne sert qu'en cas de besoin de lecture, à voir si on l'expose ou non
   - Réseau : `--network=none`
   - User : non-root, `--cap-drop=ALL`
   - Ressources : `--memory=512m --cpus=1`
4. Exécuter la commande dans le container avec le timeout.
5. Capturer stdout, stderr, exit code.
6. Loguer comme artefact `kind='sandbox_command'` (commande, stdout/stderr tronqués si grands, exit code, durée).
7. Retour structuré :
```json
{
  "exit_code": 0,
  "stdout": "...",
  "stderr": "...",
  "duration_ms": 1234,
  "truncated": false
}
```

## Garde-fous

### Niveau 1 — Vérification du grant
```python
if "bash_sandbox" not in agent_tools[agent_id]:
    return error("Agent does not have bash_sandbox capability")
```

### Niveau 2 — Vérification du binaire
```python
binary = command.split()[0]
allowed = sandbox_grants[agent_id]
if binary not in allowed:
    return error(f"Command '{binary}' not allowed for this agent")
```

### Niveau 3 — Sandbox Docker
- Pas de réseau (`--network=none`).
- Pas de privilèges (`--cap-drop=ALL`).
- Mounts contrôlés (workspace rw, conversation ro).
- Pas de mount du filesystem hôte.

### Niveau 4 — Limites de ressources
- CPU : 1 core max.
- RAM : 512 Mo max.
- Timeout : 30s par défaut, 60s max.
- Disk quota : déjà géré sur le workspace côté outils Python.

### Niveau 5 — Audit trail
Chaque exécution est loggée comme artefact :
```yaml
---
conversation_id: ...
request_id: ...
agent: code-runner
kind: sandbox_command
utc: ...
---

**command**: python3 analyze.py
**exit_code**: 0
**duration_ms**: 1234

## stdout
```
...
```

## stderr
(empty)
```

## Erreurs typiques

```json
{"error": "Agent does not have bash_sandbox capability"}
{"error": "Command 'curl' not allowed for this agent (allowed: python3, ls, cat, jq)"}
{"error": "Sandbox timeout exceeded (60s)"}
{"error": "Sandbox container failed to start: <reason>"}
```

## Intégration Jean-Michel

### Image Docker dédiée

Créer `docker/sandbox/Dockerfile` :

```dockerfile
FROM ubuntu:24.04

# Outils minimums pré-installés
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip jq \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# User non-root
RUN useradd -m -s /bin/bash sandbox
USER sandbox
WORKDIR /workspace

# Pas de pip install actif — environnement figé
```

Build :
```bash
docker build -t jeanmichel-sandbox:24.04 docker/sandbox/
```

Le build est fait au `jm.sh --install` ou via une commande dédiée `jm.sh --build-sandbox`.

### Module Python

`src/jeanmichel/tools/bash_sandbox.py` (esquisse) :

```python
import json
import subprocess
from pathlib import Path
from ._base import ToolSpec

MAX_TIMEOUT = 60
MAX_OUTPUT_BYTES = 50_000

def make_spec(conv_folder: Path, agent_code: str, allowed_commands: list[str]) -> ToolSpec:
    workspace_root = (conv_folder / "workspace").resolve()
    workspace_root.mkdir(exist_ok=True)
    container_name = f"jm-sandbox-{conv_folder.name}"  # one container per conversation

    def _ensure_container():
        # idempotent: start if not running
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or result.stdout.strip() != "true":
            subprocess.run([
                "docker", "run", "-d", "--rm",
                "--name", container_name,
                "--network=none",
                "--cap-drop=ALL",
                "--memory=512m", "--cpus=1",
                "--user", "sandbox",
                "-v", f"{workspace_root}:/workspace:rw",
                "-w", "/workspace",
                "jeanmichel-sandbox:24.04",
                "tail", "-f", "/dev/null",   # keep alive
            ], check=True)

    def _handler(command: str, description: str, timeout_seconds: int = 30) -> str:
        try:
            timeout = min(timeout_seconds, MAX_TIMEOUT)
            binary = command.strip().split(maxsplit=1)[0]
            if binary not in allowed_commands:
                return json.dumps({
                    "error": f"Command '{binary}' not allowed (allowed: {', '.join(allowed_commands)})"
                })
            _ensure_container()
            proc = subprocess.run(
                ["docker", "exec", container_name, "bash", "-c", command],
                capture_output=True, text=True, timeout=timeout,
            )
            stdout = proc.stdout[:MAX_OUTPUT_BYTES]
            stderr = proc.stderr[:MAX_OUTPUT_BYTES]
            return json.dumps({
                "exit_code": proc.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": len(proc.stdout) > MAX_OUTPUT_BYTES or len(proc.stderr) > MAX_OUTPUT_BYTES,
            })
        except subprocess.TimeoutExpired:
            return json.dumps({"error": f"Sandbox timeout exceeded ({timeout}s)"})
        except Exception as e:
            return json.dumps({"error": f"Tool failed: {e}"})

    return ToolSpec(
        name="bash_sandbox",
        description=(
            f"Run a shell command in an isolated Linux sandbox mounted on the workspace. "
            f"Allowed commands for this agent: {', '.join(allowed_commands)}. "
            "No network access. Workspace is at /workspace inside the sandbox."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command":         {"type": "string"},
                "description":     {"type": "string"},
                "timeout_seconds": {"type": "integer", "default": 30, "maximum": MAX_TIMEOUT},
            },
            "required": ["command", "description"],
        },
        handler=_handler,
    )
```

Note : la signature `make_spec` a un paramètre supplémentaire `allowed_commands` qu'il faut résoudre depuis la BDD avant l'appel. Adapter le `build_registry` pour qu'il accepte un `agent_code` quand il est disponible. Variante : exposer `bash_sandbox` avec une liste vide par défaut, et résoudre `allowed_commands` au moment de l'appel via un lookup BDD côté handler.

### Grant en BDD

Aucun agent existant ne reçoit cet outil. Pour de futurs agents :

```sql
INSERT INTO agent_tools (agent_id, tool_code)
SELECT id, 'bash_sandbox' FROM agents WHERE code='code-runner';

INSERT INTO agent_sandbox_grants (agent_id, command) VALUES
  ((SELECT id FROM agents WHERE code='code-runner'), 'python3'),
  ((SELECT id FROM agents WHERE code='code-runner'), 'cat'),
  ((SELECT id FROM agents WHERE code='code-runner'), 'ls'),
  ((SELECT id FROM agents WHERE code='code-runner'), 'jq');
```

### Lifecycle du container

- Création paresseuse : au premier appel `bash_sandbox`.
- Persistance pendant la conversation.
- Destruction à la clôture de la conversation (hook orchestrateur) ou après 30 min d'inactivité (cron de nettoyage côté `jm.sh --clean`).

## Tests à prévoir

- Exécution nominale `python3 -c "print(1+1)"`.
- Refus commande non-grantée.
- Timeout.
- Refus écriture hors workspace.
- Pas d'accès réseau (`curl https://example.com` doit échouer).
- Le container se ferme à la clôture de conversation.

## Phasing recommandé

Cet outil **n'est pas urgent**. Implémentation phase B après stabilisation des outils workspace_*.

**Phase A** (workspace_create_file, workspace_str_replace, workspace_view, workspace_list) suffit pour ~80 % des cas d'usage de manipulation de fichiers. Le sandbox bash devient nécessaire seulement pour : exécution de scripts complexes, traitement de données via outils CLI standards, génération de rapports nécessitant des outils externes.

Prendre le temps de la phase A pour valider le pattern de validation de chemin et la persistance par conversation avant d'ajouter la complexité Docker.
