Voici le rapport complet.

---

## Rapport : Utilisation GPU et configuration Ollama dans Jean-Michel

### 1. Diagnostic : pourquoi un seul GPU est utilisé

**Réponse courte : c'est le comportement attendu d'Ollama, pas un bug.**

Les logs journald confirment tout :

```
GPULayers:43[ID:GPU-69f6d720... Layers:43(0..42)]
offloaded 43/43 layers to GPU
model weights  → CUDA0 : 8.9 GiB
kv cache       → CUDA0 : 2.2 GiB
compute graph  → CUDA0 : 3.3 GiB
total memory          : 14.9 GiB
```

La totalité du modèle (14,9 GiB) est chargée sur **CUDA0 uniquement**. CUDA1 n'est pas sollicité. C'est la stratégie *fit-first* d'Ollama : il tente d'abord de faire tenir le modèle entier sur un seul GPU. Chacun de tes Quadro GV100 a 32 GiB — le modèle rentre largement. Ollama n'a donc aucune raison de répartir la charge sur deux appareils.

**La répartition multi-GPU n'est déclenchée automatiquement que lorsque le modèle ne tient pas sur un seul GPU.** Ce n'est pas le cas ici.

---

### 2. Analyse du code Jean-Michel

#### Ce qui est fait

```python
# llm.py — seul paramètre options passé
"options": {"temperature": temperature}
```

`temperature` est lue depuis la DB (`agents.temperature`) et forwarded. C'est correct.

#### Ce qui est absent

Le client Python Ollama expose un objet `Options` riche :

| Clé | Type | Rôle |
|---|---|---|
| `num_gpu` | int | Nombre de layers offloadés (≠ nombre de GPUs) |
| `main_gpu` | int | Index du GPU principal |
| `num_ctx` | int | Taille de la fenêtre de contexte |
| `num_batch` | int | Taille du batch de prompt processing |
| `numa` | bool | Utiliser le NUMA awareness |

Aucune de ces clés n'est passée dans le code actuel. Cela n'est pas un défaut critique — les defaults d'Ollama sont raisonnables — mais c'est un levier d'optimisation inexploité.

#### Configuration Ollama dans le projet

| Paramètre | Source | Valeur actuelle |
|---|---|---|
| Modèle | `JEANMICHEL_MODEL` env ou `--model` | `gemma4:latest` |
| Host | `OLLAMA_HOST` env | `http://localhost:11434` |
| Options de génération | hardcodé dans `chat()` | `{"temperature": X}` uniquement |
| Options de chargement | absentes | — |
| Config service | ollama.service | `PATH` seulement, aucun env GPU |

---

### 3. La vraie question : vaut-il la peine de forcer le multi-GPU ?

**Non, dans l'état actuel, et voici pourquoi.**

Le multi-GPU dans Ollama (tensor split) introduit une **latence inter-GPU** sur le PCIe ou NVLink à chaque layer. Quand le modèle tient sur un seul GPU, utiliser deux GPUs est généralement **plus lent**, pas plus rapide, pour une inférence séquentielle.

Les deux GV100 sont mieux exploités ainsi :
- **GPU 0** → modèle actif (inférence Jean-Michel)
- **GPU 1** → disponible pour un second modèle chargé simultanément, ou pour des requêtes parallèles si Ollama `num_parallel > 1`

Forcer `OLLAMA_SCHED_SPREAD=1` (env var qui force la répartition même quand le modèle tient) dégraderait probablement les performances pour du texte séquentiel.

---

### 4. Ce qui mériterait d'être configuré (sans usine à gaz)

#### Côté serveur Ollama — via systemd drop-in

Les options utiles pour ton setup (2× GV100, 32 GiB chacun) ne peuvent être définies qu'**au démarrage du serveur**, pas par requête. Le vecteur naturel est un fichier drop-in systemd :

```ini
# /etc/systemd/system/ollama.service.d/gpu.conf
[Service]
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_FLASH_ATTENTION=1"
```

- `OLLAMA_NUM_PARALLEL=2` : permet à Ollama de traiter 2 requêtes simultanées (utile quand Jean-Michel délègue à plusieurs spécialistes en parallèle via `asyncio.gather`)
- `OLLAMA_FLASH_ATTENTION=1` : réduit la mémoire KV cache (~2× selon le modèle)
- `OLLAMA_SCHED_SPREAD=1` : à éviter sauf si on charge un modèle qui dépasse 32 GiB

Ce n'est pas du ressort de Jean-Michel (c'est la config du serveur, pas du client).

#### Côté client Jean-Michel — options par requête

Les options `Options` passables par requête se divisent en deux familles :

**Load-time** (appliquées au chargement du modèle en mémoire — `num_ctx`, `num_gpu`, `num_batch`) : changer ces valeurs d'une requête à l'autre force un rechargement complet du modèle, ce qui est coûteux. À éviter dans un usage conversationnel.

**Runtime** (appliquées à chaque génération — `temperature`, `top_p`, `top_k`, `num_predict`, `seed`) : sûres à varier par requête, déjà stockées per-agent en DB.

La seule option runtime manquante qui aurait de la valeur serait `num_predict` (limiter la longueur de sortie par agent) — mais c'est un feature, pas un bug.

---

### 5. Recommandation pour externaliser la config Ollama proprement

Le vecteur le plus simple et cohérent avec l'architecture existante :

**Ajouter une section `[ollama]` dans user_profile.toml** (ou un `ollama_profile.toml` dédié).

Pourquoi user_profile.toml est un bon endroit :
- Déjà chargé par `config.py`
- Déjà exclu du versionning (local à la machine)
- Déjà l'endroit où on met ce qui varie entre installations

Ce qu'on y mettrait :

```toml
[ollama]
# Options passées à chaque requête (runtime options uniquement)
# Toutes optionnelles — absentes = Ollama décide
num_predict = 2048      # longueur max de réponse
seed = -1               # -1 = aléatoire
```

Les load-time options (`num_ctx`, `num_batch`) **ne doivent pas** être dans le client Python — elles appartiennent à la config serveur (systemd) pour éviter les rechargements de modèle intempestifs.

Le seul code à toucher serait `config.py` (lire la section `[ollama]`) et `llm.py` (merger les options dans le dict `options`). Moins de 20 lignes, zéro nouvelle abstraction.

---

### Synthèse

| Constat | Gravité | Action |
|---|---|---|
| Un seul GPU utilisé | Normal — le modèle tient sur 32 GiB | Rien à faire (sauf vouloir `SCHED_SPREAD` explicitement) |
| `options` limité à `temperature` | Cosmétique | Peut être enrichi sans urgence |
| Pas de `OLLAMA_NUM_PARALLEL` | Optimisation manquée | Configurer via drop-in systemd si les délégations parallèles sont utilisées |
| `OLLAMA_FLASH_ATTENTION` désactivé | Optimisation manquée | Activer côté serveur (réduit la VRAM KV cache) |
| Pas d'externalisation des options runtime | Manque de flexibilité | Section `[ollama]` dans user_profile.toml suffit |