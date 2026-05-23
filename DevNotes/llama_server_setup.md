# Setup llama-server sur glados (Manjaro, 2× GV100)

## Contexte matériel

- **Machine** : glados
- **GPU** : 2× Quadro GV100, 32 768 MiB chacune
- **Architecture CUDA** : sm_70 (Volta) — **ne PAS upgrader le driver**
- **Driver** : 575.64.05 — version stable, les plus récents étaient foireux
- **CUDA runtime max** (nvidia-smi) : 12.9

---

## Problème CUDA 13 (pacman)

Le paquet `cuda` Manjaro installe la version **13.2**, qui a **supprimé le support de sm_70** (Volta).

```
nvcc fatal : Unsupported gpu architecture 'compute_70'
```

→ Il faut CUDA **12.x** pour compiler pour les GV100.

**Désinstaller cuda 13 avant d'installer cuda 12 :**

```bash
sudo pacman -R cuda cccl python-cccl
```

---

## Installation CUDA 12.6.3 (runfile NVIDIA)

Le runfile installe le toolkit uniquement dans `/usr/local/cuda-12.6/` sans toucher au driver.

```bash
# Téléchargement (~4.2 GB)
curl -L "https://developer.download.nvidia.com/compute/cuda/12.6.3/local_installers/cuda_12.6.3_560.35.05_linux.run" \
     -o /tmp/cuda12.run
chmod +x /tmp/cuda12.run

# Installation toolkit uniquement
# --override : ignore le warning "driver bundlé (560) plus vieux que le driver installé (575)"
sudo /tmp/cuda12.run --toolkit --silent --override
```

Résultat : `/usr/local/cuda-12.6/` + symlink `/usr/local/cuda → /usr/local/cuda-12.6`.

---

## Compilation llama-server

### Prérequis

```bash
sudo pacman -Sy cmake
# gcc-15 disponible en plus du gcc (16.x par défaut)
# nvcc cuda 12.6 dans /usr/local/cuda-12.6/bin/nvcc
```

### Clone + build

```bash
git clone --depth 1 --branch b9294 https://github.com/ggml-org/llama.cpp.git /tmp/llama.cpp
cd /tmp/llama.cpp

CUDACXX=/usr/local/cuda-12.6/bin/nvcc \
cmake -B build \
  -DGGML_CUDA=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF \
  -DCMAKE_CUDA_ARCHITECTURES=70 \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/gcc-15
# ^ sm_70 = GV100. gcc-15 requis (nvcc 12.6 ne supporte pas gcc 16)

cmake --build build --config Release -j $(nproc) --target llama-server

sudo install -m755 build/bin/llama-server /usr/local/bin/llama-server
```

### Piège cmake détecté en chemin

Si cmake dit `No CUDA devices found` pour `CMAKE_CUDA_ARCHITECTURES=native`, il ne détecte pas les GPUs à la config. **Toujours spécifier l'archi explicitement** : `-DCMAKE_CUDA_ARCHITECTURES=70`.

---

## Modèle : récupération du blob GGUF

Le blob ollama est un fichier GGUF brut, copiable directement.

```bash
# Identifier le gros blob (~16-23 GB selon quant)
sudo ls -lh /usr/share/ollama/.ollama/models/blobs/ | grep " [0-9]*G "

# Copier (le dossier blobs/ n'est pas traversable par kalacione sans sudo)
mkdir -p ~/models
sudo cp /usr/share/ollama/.ollama/models/blobs/sha256-f8b1da6dc139e6928159e536bc85602adbc1412018871732a878dedcad7ccafd \
        ~/models/gemma4-uncensored-Q4_K_M.gguf
sudo chown kalacione:kalacione ~/models/gemma4-uncensored-Q4_K_M.gguf
```

**Hashes connus :**

| Quant | SHA256 (début) | Taille | Statut ollama |
|-------|---------------|--------|---------------|
| Q4_K_M | `sha256-f8b1da6dc139...` | 16 GB | incompatible (quantization: unknown) |
| Q6_K_P | `sha256-e468cbb7ec1a...` | 22 GB | incompatible (quantization: unknown) |

Les deux quants HauhauCS sont marqués `quantization: unknown` par ollama ≤ 0.24 → erreur 500 au chargement. llama-server les charge nativement.

Le **mmproj** (vision projector, ~1.2 GB) n'est pas nécessaire — jean-michel est text-only.

---

## Test llama-server

```bash
# Lancer le serveur (charge sur les 2× GV100 avec -ngl 99)
llama-server -m ~/models/gemma4-uncensored-Q4_K_M.gguf \
  --jinja -c 32768 -ngl 99 \
  --host 127.0.0.1 --port 8080

# Test dans un autre terminal
curl -s http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma4","messages":[{"role":"user","content":"bonjour"}]}' \
  | python3 -m json.tool | grep '"content"'
```

---

## État au 2026-05-23 — ABANDON

**Verdict : cul-de-sac sur cette machine. On reste sur `gemma4:26b` officiel via Ollama.**

### Problèmes rencontrés (séquence)

1. Ollama 0.24 : les GGUFs HauhauCS (Q4_K_M et Q6_K_P) → `quantization: unknown`, erreur 500 au chargement. Le `gemma4:26b` officiel fonctionne.
2. llama-server b9294 : pas de binaire CUDA précompilé Linux. Compilation obligatoire.
3. CUDA 13.2 (pacman) : sm_70 (Volta/GV100) **supprimé**.
4. CUDA 12.6.3 (runfile NVIDIA) : installé OK, mais supporte GCC ≤ 13 seulement.
5. GCC 13 : **absent des repos Manjaro** (seulement GCC 15 et 16).
6. GCC 15 + `--allow-unsupported-compiler` : trop d'erreurs de compatibilité headers.

### Ce qui a été installé sur glados

- `/usr/local/cuda-12.6/` — CUDA 12.6.3 toolkit (runfile, toolkit-only)
- `/usr/lib/libxml2.so.2` — symlink vers `libxml2.so.16` (créé pour le runfile)
- `~/models/gemma4-uncensored-Q4_K_M.gguf` — blob GGUF copié (16 GB, peut être supprimé)
- `cmake` installé via pacman
- `cuda`, `cccl`, `cudnn` (cuda 13) désinstallés

### Options non explorées (pour une future tentative)

- **Binaire Vulkan précompilé** : `llama-b9294-bin-ubuntu-vulkan-x64.tar.gz` — aucune compilation, fonctionne si Vulkan disponible sur GV100 avec driver 575.
- **Docker Ubuntu 22.04 + cuda:12.6-devel** : build isolé, gcc 11 natif, propre.

