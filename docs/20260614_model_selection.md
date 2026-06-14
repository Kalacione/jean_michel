# Sélection des modèles par rôle — rapport (2026-06-14)

État des recherches/décisions sur les modèles Ollama par rôle. **Le rôle orchestrateur est tranché et livré ;
le rôle code-router/code-analyst est à évaluer (candidats listés ci-dessous, à faire plus tard).**

## 1. Architecture config (livrée)

Toute la config modèle vit dans **`models.toml`** (gitignored ; défauts dans `models.example.toml` committé ;
merge par clé ; env `JEANMICHEL_*_MODEL` / `JEANMICHEL_CTX_WINDOW_<slug>` gagne toujours). Sections `[roles]`,
`[context_window]`, `[voice]`. `num_ctx` épinglé par modèle avec **plafond 128k** (`config.model_context_window`).
Commits : `2bcffc8` (config + VRAM) et `a00122b` (orchestrateur cogito:32b + cleanup).

## 2. Orchestrateur (rôle `main`) — TRANCHÉ : `cogito:32b`

Éval live head-to-head via `debug/eval_model.py` (num_ctx 40960) :

| Test | cogito:14b | **cogito:32b** | qwen3:14b |
|---|---|---|---|
| A. génération anti-garbage | ✅ 2.8s | ✅ 4.6s | ✅ 9.5s |
| B. tool_call simple | ❌ 0 calls | ✅ delegate_to | ❌ 0 calls |
| C. décomposition multi-outil | ✅ | ✅ | ✅ 14.8s |
| D. temp 0.2 / 0.6 | ✅/✅ | ✅/✅ | ✅/✅ |
| **Total** | 4/5 | **5/5** | 4/5 |

cogito:32b = seul à émettre un tool_call propre sur la délégation simple, aux 2 températures, 2-10× plus rapide.
Communautaire (Deep Cogito), 19 Go.

**Écartés (prouvé/choix user)** : Nemotron-Orchestrator-8B Q5_K_M (GGUF cassé — garbage `!!!!` / crash runner,
supprimé d'Ollama) ; OpenAI (boycott) ; Mistral (rejeté) ; gemma4:latest (« trop con », supprimé).

## 3. Roster Ollama actuel (5 modèles, tous utilisés)

| Modèle | Rôle | ctx épinglé |
|---|---|---|
| cogito:32b | orchestrateur (`main`) | 40960 |
| granite4.1:8b | dispatcher (tier-0) | 8192 |
| qwen3:14b | **code-router / code-analyst** (à remplacer ?) | 40960 |
| qwen3-coder:latest | code-runner (génération) | 128000 |
| gemma4:26b | reasoners / compactor / subagent | 32768 |

## 4. À FAIRE — rôle code-router / code-analyst (remplacer qwen3:14b)

**Besoin** : décomposition + délégation (**tool-calling**) pour code-router ; **analyse de code** pour
code-analyst. PAS de la génération (ça = code-runner). **Contrainte : tenir sur UN SEUL GPU (32 Go)** →
poids + KV ≤ ~24-30 Go. Communautaire, **GGUF Ollama officiel/rôdé** (leçon Nemotron).

### Candidats retenus (tous tiennent sur 32 Go)
- **glm-4.7-flash** — 30B-A3B MoE, **19 Go** (Q4), Zhipu (communautaire), « strongest in the 30B class »,
  coding + agentic. ⚠️ tool-calling NON confirmé sur la page Ollama → **à vérifier au harness** (bloquant pour
  code-router).
- **qwen3:30b** — a3b MoE, 19 Go, agentic + code (officiel Ollama).
- **cogito:32b** — déjà pullé, gagnant orchestrateur ; pourrait servir aussi code-router (raisonnement + tools).
- Référence/incumbent : **qwen3:14b**.
- ❌ **qwen2.5-coder** — NE PAS reconsidérer (viré du repo/Ollama).
- deepseek-coder-v2:16b — tool-calling seulement via variantes communautaires (faible pull) → risque GGUF.

### Plan d'éval (à exécuter plus tard)
1. Étendre `debug/eval_model.py` avec une sonde **E. compréhension de code** (snippet buggé → identifie le bug,
   pas de garbage) pour le volet code-analyst.
2. `ollama pull glm-4.7-flash` + `ollama pull qwen3:30b` (cogito:32b déjà là).
3. Head-to-head vs qwen3:14b : tool-calling (B/C/D) + sonde code (E). **Vérifier surtout que glm tool-call.**
4. Garder le meilleur ; fallback qwen3:14b.

### Câblage du gagnant (point de vigilance — résolution du modèle)
Le modèle de ces deux agents se résout par DEUX chemins, à réconcilier :
- **code-router** (= main agent en mode code) : `turn_runner.py:333-337` met `main_agent.model =
  MODE_ROUTER_MODEL["code"]` (= `CODE_MODEL`). MAIS `load_agent` (`orchestrator_v2.py:1296-1381`) applique
  aussi `agents.model_override` si non-NULL. **Précédence à confirmer.** Les deux valent `qwen3:14b`
  aujourd'hui (config + DB).
- **code-analyst** (subagent) : résolu via `agents.model_override` (`= qwen3:14b`).

Donc câbler le gagnant = (a) `[roles].code` dans models.example.toml + `[context_window]` 40960, ET
(b) **migration manuelle** (`migrate_NNN`) pour `agents.model_override` de code-router + code-analyst →
gagnant (+ mirror `db/schema.sql` + apply live + test idempotence). Cf. mémoire migrations manuelles.

## 5. Point E — rôle orchestrateur DÉDIÉ vs jean-michel chat (DÉFÉRÉ)

Question de fond : créer un agent orchestrateur lean (profil paradigmes minimal) séparé du jean-michel
« assistant chat/analyse » (qui porte les ~46 paradigmes de raisonnement), le dispatcher routant
deep/multi-étapes → orchestrateur, chat → assistant.

**Décision : déféré.** Préférence user = **rester le plus déterministe possible sur l'orchestrateur**
(l'orchestrateur Python déterministe marche ; on ne veut pas y rajouter de complexité LLM-driven). À
re-trancher seulement si le besoin se confirme sur données réelles, après le réglage du rôle code.
