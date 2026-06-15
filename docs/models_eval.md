# Évaluation des modèles — référence vivante

> Source de vérité du **roster** (qui joue quel rôle) et des **verdicts** (ce qu'on a testé, pros/cons).
> Non daté, on le met à jour à chaque éval. Les détails sont dans les deep-dives liés en bas.
> Les capacités (tools / thinking / vision) ci-dessous sont celles de `ollama show`, **pas supposées**.

## Comment mettre à jour après un test

1. `ollama pull <modèle>` puis `ollama show <modèle>` → noter capacités (tools/thinking/vision) + contexte natif.
2. `.venv/bin/python debug/eval_model.py <modèle> [autres…] --ctx 40960` → probes A–D (cf. Méthodo).
3. Ajouter/mettre à jour **une ligne** dans « Modèles testés » avec verdict + source (commit ou ce doc).
4. Si on l'adopte : `models.toml [roles]` (ou `[context_window]`, `no_thinking`) — jamais en dur dans un prompt.

---

## 1. Roster actuel (vérifié)

| Rôle | Modèle | Taille | Ctx épinglé / natif | Capacités (`ollama show`) | Pourquoi |
|---|---|---|---|---|---|
| dispatch (tier-0) | `granite4.1:8b` | 5.3 Go | 8192 / 131072 | tools | classif ALEXA/DEEP rapide, JSON forcé, **texte-only** (image ⇒ DEEP) |
| **main / orchestrateur** | `cogito:32b` | 19 Go | 40960 / 131072 | tools, **PAS de thinking** | gagnant éval tool-calling 5/5 ; rapide |
| code (router / analyst) | `qwen3:14b` | 9.3 Go | 40960 | tools, thinking | décompo méthodique ; rapide |
| code-runner / -node | `qwen3-coder:latest` | 18 Go | 128000 / 262144 | tools, **PAS de thinking** | gros contexte pour le code |
| reasoner / compactor / subagent | `gemma4:26b` | 17 Go | 32768 / 262144 | **vision, tools, thinking** | multimodal + finesse éditoriale |

**Conséquence visible** : sur un tour analyse/chat (tout `cogito`), le **bloc « réflexions » de l'UI reste
vide** — cogito n'a pas de canal thinking Ollama. Les modèles avec `thinking` (gemma4:26b, qwen3:14b)
remplissent ce bloc. Ce n'est pas un bug de streaming.

### Câblage (où ça se décide)
- `models.example.toml` (défauts committés) ⊕ `models.toml` (override local, gitignoré) ⊕ env (`JEANMICHEL_*`)
  — l'env gagne. Sections : `[roles]`, `[context_window]`, `no_thinking`, `[voice]`.
- `config.py` résout `MAIN_MODEL` / `DISPATCH_MODEL` / `CODE_MODEL` / `SUBAGENT_DEFAULT_MODEL` / `COMPACTOR_MODEL`
  / `REASONER_MODEL` ; `model_context_window()` (env → toml → défaut 32768, **plafond 128k** anti-OOM VRAM) ;
  `model_skips_thinking()` (liste `no_thinking`, aujourd'hui `["cogito:32b"]`).
- Par-agent : colonne `agents.model_override` en DB (ex. `qwen3:14b` épinglé sur code-router/code-analyst,
  `qwen3-coder:latest` sur code-runner). Sinon : router → `MAIN_MODEL`, specialist → `SUBAGENT_DEFAULT_MODEL`.
- **Paradigmes strictement model-agnostic** : aucun prompt ne nomme un modèle ; le choix = infra, pas comportement.

---

## 2. Méthodo d'évaluation

**Harness `debug/eval_model.py`** (cible : le rôle ORCHESTRATEUR = tool-calling/délégation) :
- **A. anti-garbage** — génère sans crash ni charabia (`!!!!`, `????`).
- **B. tool_call simple** — émet un `delegate_to(agent, task)` valide.
- **C. décompo multi-outils** — choisit le bon outil parmi plusieurs, args sains.
- **D. robustesse température** — re-joue B à temp 0.2 ET 0.6 (certains GGUF crashent « à chaud »).
- Invocation : `.venv/bin/python debug/eval_model.py <m1> <m2>… [--ctx N]`. **N'écrit rien** (stdout only).
- **À étendre (prévu)** : probe **E. compréhension de code** (snippet buggé → identifie le bug) pour départager
  les candidats code-router.

**Bench live qualitatif** (`DevNotes/benchmark_agents.md`) : vraies tâches (recherche multi-source…), on mesure
sources exploitables / hallucinations / densité / vitesse / couverture.

**Critère clé** : l'orchestrateur ET les spécialistes ont besoin des **tools** — un spécialiste conclut via
`report_back` (un tool). Un modèle **sans tools** est dégradé (il retombe sur le report_back-implicite-prose,
confiance « medium », cf. firefight). Le `thinking` est un *plus* d'observabilité, **pas** un prérequis.

---

## 3. Modèles testés — verdicts

| Modèle | Rôle essayé | Statut | Pros | Cons | Source |
|---|---|---|---|---|---|
| `cogito:32b` | orchestrateur | ✅ **en prod** | éval 5/5, tool-calling fiable, rapide | pas de canal thinking (réflexions UI vides) | `docs/20260614_model_selection.md`, commit `a00122b` |
| `cogito:14b` | orchestrateur | ❌ écarté | 4/5 | rate le tool_call simple (B) à certaines temp | `docs/20260614_model_selection.md` |
| `qwen3:14b` | code, recherche | ✅ **en prod** (code) | tools+thinking, ~2× + rapide que gemma en recherche, Pareto | — | `DevNotes/benchmark_agents.md` |
| `qwen3-coder:latest` | code-runner | ✅ **en prod** | conçu pour le code, contexte 128k | pas de thinking | `models.example.toml` |
| `gemma4:26b` | reasoner/compactor/subagent | ✅ **en prod** | **multimodal**, thinking, tools, finesse éditoriale | plus lent que qwen3 sur recherche | `docs/GEMMA4.md`, `DevNotes/benchmark_agents.md` |
| `granite4.1:8b` | dispatch | ✅ **en prod** | rapide, JSON forçable, spécialisé triage | texte-only (par design) | `DevNotes/REVOLUCION/06_proposition_v2.md` |
| `gemma4:latest` | tier-1 (tôt) | ❌ rejeté | rapide | « trop con » : sort du junk → rework humain | `DevNotes/benchmark_agents.md` |
| Nemotron-Orchestrator-8B Q5_K_M | orchestrateur | ❌ cassé | — | GGUF cassé : `!!!!`, crash runner HTTP 500 (→ a motivé le harness) | `docs/20260614_model_selection.md`, commit `2bcffc8` |
| `deepseek-r1:14b` | raisonneur (bench) | ❌ rejeté | — | **ne sait pas utiliser les tools** | `DevNotes/benchmark_agents.md` |
| `qwen3.6:27b` | raisonneur (bench) | ❌ trop lent | — | dépasse la limite ~120s (thinking) | `DevNotes/benchmark_agents.md` |
| famille `qwen2.5-coder` | code | ❌ retiré | — | viré du repo « à ne pas reconsidérer » (version testée) ; **le 32b n'a jamais été testé** (cf. candidats) | `docs/20260614_model_selection.md` §4 |
| HauhauCS Gemma4-26B uncensored Q6_K_P | reasoner/compactor | ⏳ reverté | dé-censuré sans perte, agentique | toolchain CUDA/GCC Manjaro → `llama-server` incompilable | commits `6bb85af` (adoption) / `125b0ff` (revert) |
| OpenAI API | orchestrateur | ❌ exclu | — | boycott (décision) | `docs/20260614_model_selection.md` §2 |
| Mistral | orchestrateur | ❌ rejeté | — | rejeté (raison non détaillée) | `docs/20260614_model_selection.md` §2 |

---

## 4. Candidats à tester (prochaine session)

> Faits vérifiés (`ollama show` / web) + ce qu'il reste à confirmer **dans le harness avant d'adopter**.

### deepseek-r1:32b — visé pour l'orchestration
- thinking ✓ (ce qui t'intéressait pour l'observabilité).
- ⚠️ **tools ✗ dans la version du registre Ollama** (`deepseek-r1:32b`) — bug connu ([ollama#10935](https://github.com/ollama/ollama/issues/10935),
  [#8517](https://github.com/ollama/ollama/issues/8517)) : DeepSeek-R1-0528 a ajouté le function-calling, mais
  Ollama n'a pas mis à jour le template du modèle de la librairie → renvoie « does not support tools ».
- Or **l'orchestrateur, son seul boulot, c'est le tool-calling** → la version library **échouerait l'éval (B/C/D)**.
  Et `deepseek-r1:14b` a déjà été recalé pour exactement ça.
- → Tester **uniquement** via le variant communautaire `MFDoom/deepseek-r1-tool-calling:32b` (template tools)
  — **risque GGUF** (comme Nemotron). Faire passer A–D **avant** d'envisager. Arbitrage probable :
  « thinking visible » vs « tool-calling fiable + rapidité » (cogito gagne déjà sur le 2e).

### qwen2.5-coder:32b — code-runner / code
- **tools ✓** (function-calling natif, balises `<tool_call>`), **pas de thinking** (OK pour un runner).
- Contexte : 32K par défaut en GGUF Ollama (montable — il faut set `num_ctx`, le modèle gère jusqu'à 131k).
- ⚠️ la **famille qwen2.5-coder a déjà été retirée** « à ne pas reconsidérer » — mais le **32b spécifiquement
  n'a jamais été testé** ; re-justifier (taille/qualité supérieures) avant adoption. Tester avec la probe E (code).

### phi-4 14b — nouveau spécialiste math/STEM
- math / raisonnement forts.
- ⚠️ **pas de tools natifs** sur `phi4:14b` standard ([ollama#9647](https://github.com/ollama/ollama/issues/9647))
  → soit un variant communautaire (`zac/phi4-tools`), soit `phi4-mini` (function-calling natif mais 3.8B).
- Or un **spécialiste doit `report_back` (un tool)** → sans tools, il est dégradé (fallback prose).
  À trancher : (a) variant tools, (b) `phi4-mini`, ou (c) rôle « raisonneur pur » sans délégation/report_back.

### glm-4.7-flash & qwen3:30b — candidats code-router (déjà notés)
- Cf. `docs/20260614_model_selection.md` §4 : tool-calling **à confirmer** dans le harness (bloquant pour le rôle).

---

## 5. Budget VRAM & contraintes hardware

- **2× Quadro GV100 32 Go = 64 Go** (PCIe, pas de NVLink → spread inter-GPU coûteux).
- Un 32b Q4 ≈ 19–22 Go tient sur une carte ; KV cache ∝ `num_ctx` → d'où le **plafond contexte 128k** + l'épinglage
  par modèle (`models.toml [context_window]`). Ollama 0.24 dimensionne sinon le contexte selon la VRAM (≥48 Gio →
  256k) ce qui faisait exploser la VRAM (cf. firefight).
- Politique en place : `keep_alive` court + **éviction au changement de modèle** (un seul modèle résident à la
  fois côté client). Donc multiplier les modèles dans une chaîne = reloads → latence ; à garder en tête en éval.

---

## Deep-dives (détails)
- `docs/20260614_model_selection.md` — éval orchestrateur + plan code-router (glm/qwen3:30b).
- `DevNotes/benchmark_agents.md` — bench live (recherche) chiffré.
- `docs/GEMMA4.md` — formatage de prompt + multimodal gemma4.
- `DevNotes/gemma_variants.md` — saga HauhauCS uncensored (voir via `git show 6bb85af`).
- `debug/eval_model.py` — le harness (probes A–D).
- `models.example.toml` + `src/jeanmichel/config.py` — le câblage.
