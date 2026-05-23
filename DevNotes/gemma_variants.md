# Gemma 4 — variantes non censurées

## Modèle en service

**`HauhauCS/Gemma4-26B-A4B-Uncensored-HauhauCS-Balanced` — Q4_K_M (17 GB)**

Choisi pour :
- Même base exacte que `gemma4:26b` (google/gemma-4-26B-A4B-it) → zéro dégradation de dataset
- MoE : 25B total / 3.8B actifs par passe — throughput d'un 4B, raisonnement d'un 26B
- Lossless uncensoring (0/465 refusals) sans modifier les capacités du modèle
- Variant "Balanced" : répond à tout, peut cadrer en intro, mais ne refuse jamais
- Explicitement conçu pour l'agentic work (chaînes de tool calls, long context)
- Q4_K_M : format standard largement supporté par llama.cpp/ollama, 17 GB (même footprint que gemma4:26b)
- Q6_K_P (23 GB) abandonné — quantization type `unknown` dans ollama ≤ 0.24, format custom non supporté par llama.cpp

**Ollama tag** : `hf.co/HauhauCS/Gemma4-26B-A4B-Uncensored-HauhauCS-Balanced:Q4_K_M`

Pour utiliser un autre modèle ponctuellement (ex. retour sur gemma4:26b) :

```bash
JEANMICHEL_MODEL=gemma4:26b ./jm.sh
```

> **Note Q6_K_P** : ce format de quant est `unknown` pour ollama ≤ 0.24 / llama.cpp actuel. Préférer Q4_K_M ou Q8_0.

---

## Autres variantes disponibles (HauhauCS 26B)

| Quant | Taille | Usage recommandé |
|-------|--------|------------------|
| Q8_K_P | 27 GB | Qualité max, 1 GV100 limite |
| Q6_K_P | 23 GB | ~~En service~~ — **incompatible ollama/llama.cpp** (quantization unknown) |
| Q5_K_P | 19 GB | Économie VRAM si contexte très long |
| **Q4_K_M** | **17 GB** | **En service — standard, compatible llama.cpp** |
| IQ4_XS | 14 GB | Micro-tâches légères |
| IQ3_M  | 12 GB | Micro-tâches très contraintes |
| Q2_K_P | 11 GB | Dernier recours (dégradation notable) |

---

## Variantes rejetées

| Modèle | Raison |
|--------|--------|
| HauhauCS/Gemma-4-E4B (4B) | Downgrade sévère vs 26B |
| dealignai/Gemma-4-31B-JANG CRACK | MLX uniquement — Apple Silicon, incompatible GV100 |
| Kasper-Bankler/gemma-4-E2B (2B) | Trop petit |

---

## Agentic Aggressive (WIP)

HauhauCS travaille sur une variante **Aggressive** du 26B : répond directement sans cadrage introductif.
Disponible sur le même repo quand prête. Intéressant si le preamble du Balanced devient gênant.
