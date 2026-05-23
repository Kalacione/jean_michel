# Gemma 4 — variantes non censurées

## Modèle en service

**`HauhauCS/Gemma4-26B-A4B-Uncensored-HauhauCS-Balanced` — Q6_K_P (23 GB)**

Choisi pour :
- Même base exacte que `gemma4:26b` (google/gemma-4-26B-A4B-it) → zéro dégradation de dataset
- MoE : 25B total / 3.8B actifs par passe — throughput d'un 4B, raisonnement d'un 26B
- Lossless uncensoring (0/465 refusals) sans modifier les capacités du modèle
- Variant "Balanced" : répond à tout, peut cadrer en intro, mais ne refuse jamais
- Explicitement conçu pour l'agentic work (chaînes de tool calls, long context)
- Q6_K_P : qualité quasi-Q8 grâce au profil par couche HauhauCS, 23 GB laisse du headroom sur GV100 32GB
- Compatible Ollama/llama.cpp natif

**Ollama tag** : `hf.co/HauhauCS/Gemma4-26B-A4B-Uncensored-HauhauCS-Balanced:Q6_K_P`

---

## Autres variantes disponibles (HauhauCS 26B)

| Quant | Taille | Usage recommandé |
|-------|--------|------------------|
| Q8_K_P | 27 GB | Qualité max, 1 GV100 limite |
| **Q6_K_P** | **23 GB** | **En service — sweet spot qualité/VRAM** |
| Q5_K_P | 19 GB | Économie VRAM si contexte très long |
| Q4_K_M | 17 GB | Tâches rapides, même footprint que l'original |
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
