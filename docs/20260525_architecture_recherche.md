# Refonte de l'architecture — l'orchestrateur comme arbitre, pas comme passe-plat

**Date :** 2026-05-25 (v2)
**Branche :** `tout_doux`
**Remplace :** v1 (terminologie incorrecte sur "turn", pivot architectural insuffisant)

---

## 0. Préambule honnête

Tu as raison sur les deux critiques.

**Sur la terminologie** : un *turn* dans notre système est un aller-retour utilisateur (input → réponse rendue). Tout ce que j'ai décrit comme "turn 0, turn 1, turn K+1" se déroule **à l'intérieur d'un seul turn**. Le vocabulaire correct est *itération LLM* ou *phase*.

**Sur le diagnostic** : ce qu'on a aujourd'hui n'est pas une mauvaise implémentation d'une bonne architecture. C'est une bonne implémentation d'une architecture qui ne peut pas marcher avec gemma4:26b. L'orchestrateur est un wrapper de boucle autour d'un LLM-router qui voit un tool `delegate_to` et qui doit "décider" tout : quelle phase, quel specialist, quel budget, quand s'arrêter. C'est la même fonction qu'un GPT-4 avec function calling — sauf qu'on n'a pas GPT-4. Empiler des paradigmes "you MUST" par-dessus n'a fait que pousser le modèle à boucler poliment.

La v1 disait "filtre les tools par phase". C'était un pansement structuré mais un pansement. Le vrai problème : **le LLM ne devrait pas avoir d'outil `delegate_to` du tout**.

---

## 1. Ce que Claude Code / Copilot / Aider font réellement

J'ai écrit en v1 qu'ils avaient une "machine à états en code". C'est vrai mais incomplet. Le point important :

**Le LLM n'a pas un tool `delegate_to_another_LLM`.** Le LLM est appelé par du code Python avec un prompt focalisé, retourne une sortie structurée, et le code Python décide quoi faire ensuite. Si une étape suivante doit utiliser un autre LLM (ou le même avec un autre system prompt), c'est **le code** qui fait l'appel.

| Système | Forme d'appel LLM | Qui orchestre |
|---|---|---|
| Claude Code | `client.messages.create(system=…, tools=[bash, edit, …])` dans une boucle Python | Code Python |
| GitHub Copilot Chat | Plusieurs prompts spécialisés (rewrite, explain, fix, agent) appelés selon la commande utilisateur | Code TypeScript |
| Aider | Un LLM par "rôle" (architect, editor, summarizer) — orchestré séquentiellement | `Coder.run()` |
| AutoGen | `GroupChatManager.select_speaker()` est code, pas LLM | Code |
| LangGraph | Nœuds = code, edges = code, contenu de nœud = LLM | Graphe explicite |

Aucun n'a un "super-agent" qui voit un outil `delegate_to_specialist` et qui décide d'invoquer un autre LLM via cet outil. Notre `delegate_to` est une **anti-pattern** : c'est faire passer une décision orchestrationnelle à travers le narrow channel du tool calling LLM.

---

## 2. Ce qu'on a réellement aujourd'hui

```
┌────────────────────────────────────────────────────────────────┐
│                  Orchestrator.run(user_input)                  │
│                                                                 │
│   1. detect_language                                            │
│   2. create conversation if needed                              │
│   3. _run_request(agent_code="jean-michel", ...)                │
│      │                                                          │
│      │   ┌──────────────────────────────────────────────┐      │
│      │   │  Big loop (max_steps = 30)                   │      │
│      │   │   - build system prompt (3500 tokens)        │      │
│      │   │   - call LLM                                  │      │
│      │   │   - if tool_calls:                            │      │
│      │   │       for call in tool_calls:                 │      │
│      │   │         if call == delegate_to:               │      │
│      │   │            yield from _run_request(...)  ◄────┼──┐   │
│      │   │         elif call == set_task_class: ...      │  │   │
│      │   │         elif call == manage_todo_list: ...    │  │   │
│      │   │         else: execute tool                    │  │   │
│      │   │   - if return_to_user → exit loop             │  │   │
│      │   └──────────────────────────────────────────────┘  │   │
│      │                          recursion (depth++)         │   │
│      └──────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────┘

Symptôme : tout passe par le même prompt jean-michel saturé.
            Le LLM est arbitre + planificateur + briefing-writer + synthétiseur.
            On lui demande de faire 5 boulots avec un prompt de 3500 tokens.
```

---

## 3. Ce qu'on veut

```
┌─────────────────────────────────────────────────────────────────┐
│                  Orchestrator.run_turn(user_input)              │
│                                                                  │
│   PHASE 1 — TRIAGE  (1 LLM call, sortie JSON validée)           │
│   ┌───────────────────────────────────────────────────────┐    │
│   │  prompt: "Classify request. If trivial, answer."      │    │
│   │  sortie: {class: "...", direct_answer: str|null}      │    │
│   │  ~400 tokens prompt, 1 call max, retry 1× si invalide │    │
│   └───────────┬───────────────────────────────────────────┘    │
│               │                                                  │
│       ┌───────┴────────┐                                         │
│       │ direct_answer? │── yes ──► PHASE 6 (SYNTH ou direct)    │
│       └───────┬────────┘                                         │
│              no                                                  │
│               ▼                                                  │
│   PHASE 2 — PLAN  (1 LLM call si medium/deep, sortie JSON)      │
│   ┌───────────────────────────────────────────────────────┐    │
│   │  prompt: "Decompose into specialist briefings"        │    │
│   │  sortie: {briefings: [{agent, question, expected_     │    │
│   │           files, max_searches, depends_on}, ...]}     │    │
│   │  Validation code: 2-12 briefings, agents existent,    │    │
│   │  pas de boucle dépendance                             │    │
│   └───────────┬───────────────────────────────────────────┘    │
│               │                                                  │
│               ▼                                                  │
│   PHASE 3 — EXECUTE  (N appels specialists, parallélisables)    │
│   ┌───────────────────────────────────────────────────────┐    │
│   │  for briefing in topological_order(briefings):        │    │
│   │     run_specialist(briefing)   # spawn sub-machine    │    │
│   │       │                                                │    │
│   │       └─► [ACT loop bornée][WRITE forcé][REPORT]      │    │
│   │                                                        │    │
│   │  collect: workspace files produced                    │    │
│   └───────────┬───────────────────────────────────────────┘    │
│               │                                                  │
│      ┌────────┴───────┐                                          │
│      │ class == deep? │── yes ──► PHASE 4 (CRITIQUE optionnelle)│
│      └────────┬───────┘                                          │
│              no                                                  │
│               ▼                                                  │
│   PHASE 5 — BUILD (si artefact final attendu)                   │
│   ┌───────────────────────────────────────────────────────┐    │
│   │  run_specialist(document-builder, support_files=all)  │    │
│   └───────────┬───────────────────────────────────────────┘    │
│               ▼                                                  │
│   PHASE 6 — SYNTHESIZE  (1 LLM call, prompt focalisé)           │
│   ┌───────────────────────────────────────────────────────┐    │
│   │  prompt: "Write user-facing answer from these files"  │    │
│   │  context: all summaries + key file excerpts           │    │
│   │  sortie: texte libre, langue de l'utilisateur         │    │
│   └───────────┬───────────────────────────────────────────┘    │
│               ▼                                                  │
│   PHASE 7 — ARCHIVE (existant : archivist met à jour summary)   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Le changement structurel** :
- Plus aucun LLM ne voit `delegate_to`, `set_task_class`, `manage_todo_list`, `signal_convergence`. Ce ne sont **plus des outils LLM**. Ce sont des transitions d'état du code Python.
- `jean-michel` comme entité unique disparaît. Il est remplacé par **trois prompts focalisés** appelés par le code aux phases 1, 2, 6 :
  - `prompts/triage.md` → "Classify + answer if trivial"
  - `prompts/plan.md` → "Decompose into briefings"
  - `prompts/synthesize.md` → "Write the final answer from these inputs"
- Les specialists (web-search, wikipedia, document-builder, critical-thinker, code-runner…) **restent quasi inchangés**. Ce sont eux qui ont vraiment besoin d'un LLM qui décide quoi outiller. L'orchestrateur les appelle par code, ils restent des sous-machines à état avec tools focalisés.

---

## 4. Schéma synoptique — recherche simple

Une requête `single_fact` (ex : "quelle heure est-il à Paris ?").

```
USER input
   │
   ▼
[ORCHESTRATOR] PHASE 1 (Triage)
   │  LLM call (prompt triage ~300 tokens) :
   │   "classify + direct_answer if trivial. Available specialists: ..."
   │  → sortie : {class: "single_fact",
   │              direct_answer: null,
   │              suggested_agent: "weather-specialist",
   │              question: "current time in Paris"}
   │
   ▼
[ORCHESTRATOR] PHASE 3 (Execute, briefing unique généré côté code)
   │  briefing = {agent: "weather-specialist",
   │              question: "current time in Paris",
   │              max_tool_calls: 2}
   │
   ▼
[WEATHER SPECIALIST] sub-machine
   │  ACT phase : weather(location="Paris") → JSON
   │  REPORT phase : report_findings(summary="It is 19:23 CEST in Paris")
   │
   ▼
[ORCHESTRATOR] PHASE 6 (Synthesize)
   │  LLM call (prompt synth ~400 tokens) :
   │   context = "User asked: ... / Specialist found: ..."
   │   → "Il est 19h23 à Paris."
   │
   ▼
USER reçoit la réponse

Coût : 2 LLM calls (triage + synth) + 1 specialist mini-loop.
Total : ~5-7 LLM calls max, ~3-5 secondes.

Pas de plan.md. Pas de todo.json. Pas de phase critique/build.
```

---

## 5. Schéma synoptique — recherche approfondie

`deep_research` (ex : la requête sur les sources de vérité).

```
USER input
   │
   ▼
[ORCHESTRATOR] PHASE 1 (Triage)
   │  LLM call → {class: "deep_research", direct_answer: null}
   │
   ▼
[ORCHESTRATOR] PHASE 2 (Plan)
   │  LLM call (prompt plan ~600 tokens, inclut catalogue agents) :
   │   "Decompose. Each briefing = 1 specialist + 1 narrow question.
   │    Output 3-10 briefings. JSON schema strict."
   │  → sortie :
   │    {briefings: [
   │       {id: 1, agent: "wikipedia-specialist",
   │        question: "List existing 'open data' encyclopedias",
   │        expected_files: ["workspace/encyclopedic.md"]},
   │       {id: 2, agent: "web-search-specialist",
   │        question: "3 scientific APIs (PubMed, arXiv, CrossRef etc)",
   │        expected_files: ["workspace/scientific.md"]},
   │       {id: 3, agent: "web-search-specialist",
   │        question: "3 news/RSS aggregator APIs",
   │        expected_files: ["workspace/news.md"]},
   │       {id: 4, agent: "web-search-specialist",
   │        question: "3 geographic/maps data APIs",
   │        expected_files: ["workspace/geo.md"]},
   │       {id: 5, agent: "critical-thinker",
   │        question: "Evaluate reliability of these sources",
   │        depends_on: [1,2,3,4],
   │        support_files: [encyclopedic.md, scientific.md, news.md, geo.md]},
   │       {id: 6, agent: "document-builder",
   │        question: "Final comparison table",
   │        depends_on: [1,2,3,4,5],
   │        expected_files: ["workspace/final.md"]}
   │    ]}
   │
   │  Validations code :
   │   - chaque agent existe en DB
   │   - graphe de dépendances sans cycle
   │   - chaque briefing a une question concrète (heuristique : ≥ 6 mots, ≠ titre générique)
   │   - retry 1× si invalide, sinon plan fallback
   │
   │  → todo.json écrit par le code (1 ligne par briefing)
   │  → plan.md écrit par le code
   │
   ▼
[ORCHESTRATOR] PHASE 3 (Execute, tri topologique)
   │
   ├─ briefings indépendants en parallèle (asyncio ou thread pool) :
   │
   │     [WIKIPEDIA-SPEC #1]    [WEB-SEARCH-SPEC #2]   [WEB-SEARCH-SPEC #3]   [WEB-SEARCH-SPEC #4]
   │             │                       │                       │                       │
   │       ACT (≤ 3 calls)         ACT (≤ 3 searches)      ACT (≤ 3 searches)      ACT (≤ 3 searches)
   │       WRITE (forcé)           WRITE (forcé)           WRITE (forcé)           WRITE (forcé)
   │       REPORT                  REPORT                  REPORT                  REPORT
   │             │                       │                       │                       │
   │             └───────────────────────┴───────────────────────┴───────────────────────┘
   │             │  (orchestrator attend ces 4, marque todo done, vérifie files exists)
   │             ▼
   │     [CRITICAL-THINKER #5]   support_files = les 4 .md
   │             │  ACT (lecture des fichiers, pas de web)
   │             │  WRITE (critique.md)
   │             │  REPORT
   │             ▼
   │     [DOCUMENT-BUILDER #6]   support_files = les 4 + critique.md
   │             │  ACT (lecture, pas de web)
   │             │  WRITE (final.md)
   │             │  REPORT
   │
   ▼
[ORCHESTRATOR] PHASE 6 (Synthesize)
   │  LLM call (prompt synth) :
   │   context = brief de la requête + summaries de chaque report + chemin final.md
   │   → "Voilà 13 sources trouvées, organisées par domaine.
   │      Le document complet est dans workspace/final.md."
   │
   ▼
USER reçoit la réponse + lien fichier.

Coût LLM total : 2 (triage + synth) + 6 specialists × ~3-5 calls = 20-32 LLM calls.
Le router-LLM est appelé 2 fois. Il n'y a plus de "router-LLM qui boucle 15 fois".
```

---

## 6. Brique par brique : ce qu'on garde, ce qu'on supprime, ce qu'on ajoute

### Ce qu'on **garde** (et qui marche déjà)

| Composant | État actuel | Devenir |
|---|---|---|
| `db.py` + schéma SQLite | OK | Inchangé. Plus de paradigmes côté router. |
| `persistence.py` (artifacts md, journal, summary) | OK | Inchangé. Granularité par phase au lieu de par tour LLM. |
| `tools/` (web_search, wikipedia, weather, workspace_*, conv_*, code-runner, etc.) | OK | Inchangé. Continuent d'être appelés par les specialists. |
| Le concept "specialist = LLM + outils focalisés + report_findings" | OK | Inchangé. C'est ici que le LLM fait du vrai travail. |
| `MockClient` pour les tests | OK | Inchangé. |
| CLI + events typés | OK | Adapté : nouveaux events `PhaseStarted`, `PlanCommitted`. |
| Workspace shared per-conversation | OK | Inchangé. |

### Ce qu'on **supprime**

| Composant | Raison |
|---|---|
| Tool `delegate_to` (LLM-facing) | Devient une transition d'état Python. Le LLM ne décide plus de déléguer. |
| Tool `set_task_class` (LLM-facing) | La sortie de la phase Triage **contient** la classe. Plus besoin d'un outil. |
| Tool `manage_todo_list` (LLM-facing pour le router) | Todo écrite par le code à partir de la sortie de PLAN. Reste éventuellement disponible pour les specialists (optionnel). |
| Tool `signal_convergence` | Idem, devient une transition. |
| Agent `jean-michel` en tant qu'entité DB avec `agent_paradigms` | Remplacé par 3 prompts statiques en fichiers. Plus de paradigmes empilés. |
| Paradigmes `assess_complexity_first`, `plan_before_complex_action`, `planning_with_todos`, `orchestrator_inquiry_loop`, `metacog_live_monitor`, `report_before_acting` (pour router) | Tous procéduraux. Remplacés par la state machine code-side. |
| `_ROUTER_DEEP_RESEARCH_FORBIDDEN_TOOLS` | N'a plus de sens : le router LLM n'a plus d'outils du tout. |
| Le gate `set_task_class` ajouté hier | N'a plus de sens, l'étape est dans le code. |
| `_empty_turns`, `_auto_update_todos`, `_search_call_count` gates ajoutés hier | Gardés mais simplifiés : ils protègent les specialists, plus le router. |

### Ce qu'on **ajoute**

| Composant | Rôle |
|---|---|
| `orchestrator/phases.py` | Enum `Phase` + transitions explicites + `PhaseRunner` |
| `orchestrator/triage.py` | Function `triage(user_input, conv_ctx) → TriageResult` |
| `orchestrator/planner.py` | Function `plan(triage_result, agents_catalog) → Plan(briefings)` |
| `orchestrator/dispatcher.py` | Function `execute_plan(plan, deps_graph) → ExecutionResult` — gère ordre topologique + parallélisme |
| `orchestrator/synthesizer.py` | Function `synthesize(user_input, execution_result) → str` |
| `prompts/triage.md`, `prompts/plan.md`, `prompts/synth.md` | Prompts focalisés, ≤ 800 tokens chacun |
| `schemas/triage.json`, `schemas/plan.json` | JSON Schema pour valider les sorties LLM |
| `tests/test_phase_*.py` | 1 fichier de tests par phase, avec MockClient |
| `Event PhaseStarted(phase: str)` | Rendu CLI : `▶ TRIAGE / ▶ PLAN / ▶ EXECUTE / ▶ SYNTHESIZE` |

---

## 7. Modèle d'appel LLM — pourquoi ça marche avec gemma4:26b

Aujourd'hui : 1 prompt de 3500 tokens, 5 rôles à jouer, sortie libre avec tool calls.

Cible :

| Phase | Prompt taille | Sortie attendue | Validation |
|---|---|---|---|
| Triage | ~400 tokens (question + énoncé des classes + format JSON + few-shot) | JSON `{class, direct_answer, suggested_agent}` | JSON Schema + retry 1× |
| Plan | ~700 tokens (question + catalogue agents + format JSON + few-shot) | JSON `{briefings: [...]}` | JSON Schema + checks (≥ 2 items, agents existent, pas de cycle) + retry 1× |
| Synthesize | ~600 tokens (question + summaries + style directives) | Texte libre, langue user | Longueur > 20 chars, retry 1× |
| Specialists (inchangé) | Selon agent | Selon agent | Selon agent |

Pour gemma4:26b, un prompt 400 tokens avec sortie JSON few-shot **marche bien**. C'est ce que font tous les pipelines RAG modernes (LlamaIndex, DSPy). On sort enfin du régime "demander à un 26b de jouer à GPT-4 agentique".

---

## 8. Mode `chat` / `vocal` — cas dégénéré

Pour les conversations triviales ("salut", "tu vas bien ?", "raconte une blague") :

```
USER input
  │
  ▼
[ORCHESTRATOR] PHASE 1 (Triage)
  │  LLM call → {class: "chat", direct_answer: "Salut, …"}
  │
  ▼
USER reçoit direct_answer

1 LLM call total. Pas de pipeline.
```

Le LLM de triage gère lui-même les réponses triviales. Pas besoin de phases 2-7.

---

## 9. Les garde-fous (et pourquoi ils sont triviaux maintenant)

| # | Garde-fou | Implémentation |
|---|---|---|
| G1 | Pas de boucle de planification | PHASE 2 est appelée **1 seule fois** par turn |
| G2 | Pas de re-classification | PHASE 1 est appelée **1 seule fois** par turn |
| G3 | Budget de recherche | `max_searches` est dans le briefing → specialist voit le tool disparaître à N |
| G4 | Délégation dupliquée | Impossible : le code ne ré-exécute pas un briefing déjà fait |
| G5 | Todo orpheline | `todo.json` est généré depuis `Plan`, jamais touché par un LLM |
| G6 | Plan trop générique | Validation heuristique sur sortie de PHASE 2 : titres ≥ 6 mots, ≠ générique |
| G7 | Specialist qui ne produit rien | Phase WRITE forcée : si `expected_files` non vide et 0 fichier → re-prompt 1× |
| G8 | Boucle infinie LLM | Borné par 3 retries × N phases = max ~20 calls hors specialists |
| G9 | Wall clock | Inchangé, mais s'applique phase par phase |
| G10 | Reprise après crash | Phase courante persistée en DB → reprise possible |

Aucun de ces garde-fous ne dépend de la coopération du LLM.

---

## 10. Plan de migration — réaliste

### Étape 1 — Prouver l'intuition (1 jour)

Implémenter **uniquement** PHASE 1 (Triage) en code, en parallèle de l'existant.

- Nouveau module `orchestrator/triage.py` avec `triage(user_input) → TriageResult`.
- Nouveau prompt court `prompts/triage.md`.
- JSON Schema strict.
- Test : avec MockClient, montrer que la classification est stable et que le `direct_answer` court-circuite tout pour les requêtes triviales.
- Brancher derrière un feature flag `JEANMICHEL_USE_NEW_TRIAGE=1`.

**Critère de succès** : sur 10 requêtes triviales, 1 LLM call total au lieu de 5-10 actuellement.

### Étape 2 — PHASE 2 (Plan) (1-2 jours)

- `orchestrator/planner.py`.
- Prompt `prompts/plan.md` avec catalogue agents généré à partir de la DB.
- Validation graphe.
- Test : sur la requête "sources de vérité", PHASE 2 doit produire 4-6 briefings ciblés (un par domaine), pas une todo générique.

### Étape 3 — PHASE 3 (Execute) (1-2 jours)

- `orchestrator/dispatcher.py` réutilise `_run_request` actuel pour les specialists (sans le code de delegation côté router).
- Tri topologique + parallélisme simple (asyncio).
- Validation : `expected_files` vérifiés post-execution.

### Étape 4 — PHASE 6 (Synthesize) (0.5 jour)

- `orchestrator/synthesizer.py`.
- Prompt `prompts/synth.md`.

### Étape 5 — Bascule + nettoyage (1 jour)

- Supprimer le tool `delegate_to` du code et de la DB.
- Supprimer l'agent `jean-michel` de la DB (ou le marquer `archived`).
- Supprimer les paradigmes procéduraux.
- Migration 062 finale.

### Étape 6 — Tests et docs (0.5 jour)

- Mettre à jour `HOWTO_ADD_SPECIALIST_OR_TOOL.md` (devient plus simple).
- Adapter tests existants (la plupart deviennent obsolètes ou se simplifient).

**Total :** 5-7 jours. C'est plus que la v1 (qui était 2 jours pour un pansement). C'est moins que continuer à ajouter des gates qui ne tiennent pas.

---

## 11. Ce qu'on perd

Soyons honnêtes :

1. **La récursivité illimitée** disparaît. Les specialists ne peuvent plus déléguer à d'autres specialists. Pour les cas où un specialist veut sous-déléguer (ex : comparator-specialist qui veut interroger 2 spécialistes domaine), on prévoit un mécanisme `sub_briefings` qui remonte à l'orchestrateur. **C'est une restriction salutaire** — c'est la récursivité ouverte qui produit les boucles à profondeur 6.

2. **La flexibilité du "le LLM décide tout"** disparaît. C'est précisément ce qu'on veut perdre.

3. **Le travail des dernières semaines** sur les gates côté router devient en partie obsolète. **Pas perdu** : les gates spécialiste (budget de recherche, WRITE forcé, validation files_produced) restent utiles et continuent de protéger les phases 3-5.

---

## 12. Comparatif visuel — pourquoi l'arbitre n'est plus un LLM

### Avant

```
            "tu DOIS classifier"
            "tu DOIS planifier"
            "tu DOIS gérer la todo"
            "tu DOIS respecter le budget"
            "tu DOIS déléguer aux bons agents"
            "tu DOIS éviter les boucles"
                       │
                       ▼
              ┌──────────────────┐
              │   LLM jean-michel │  ◄── reçoit 3500 tokens d'instructions
              │   (gemma4:26b)    │       et un tool delegate_to
              └──────────────────┘
                       │
              décide tout, échoue parfois.
```

### Après

```
              ┌──────────────────────────────┐
              │   ORCHESTRATOR (Python)      │
              │   - state machine            │
              │   - validation               │
              │   - budgets durs             │
              │   - parallélisation          │
              └────────┬─────────────────────┘
                       │ appelle pour des tâches précises
                       ▼
              ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
              │ LLM triage   │    │ LLM plan     │    │ LLM synth    │
              │ 400 tokens   │    │ 700 tokens   │    │ 600 tokens   │
              │ JSON out     │    │ JSON out     │    │ texte out    │
              └──────────────┘    └──────────────┘    └──────────────┘
                                          │
                                          │ orchestrator dispatch
                                          ▼
                            ┌──────────────────────────┐
                            │  Specialists (inchangés) │
                            │  LLM + tools focalisés   │
                            └──────────────────────────┘

L'orchestrateur arbitre. Les LLM exécutent des sous-tâches bornées.
```

---

## 13. Décision

Trois options :

1. **Continuer les pansements** sur l'archi actuelle. Coût marginal faible, ROI nul à terme.
2. **Pivot complet** (ce document, étapes 1-6). 5-7 jours. ROI : un système qui marche.
3. **Pivot incrémental** (étape 1 seule cette semaine, étape 2 plus tard). 1 jour pour valider. Si le triage en code donne déjà des résultats nets sur les requêtes triviales, on continue.

Ma recommandation : **option 3**. L'étape 1 est isolable, behind a flag, et démontre l'intuition. Si elle marche, l'étape 2 devient facile à justifier. Si elle ne marche pas, on a perdu 1 jour et appris quelque chose de structurant.

Le fichier `docs/HOWTO_ADD_SPECIALIST_OR_TOOL.md` deviendra plus court après ce pivot. C'est probablement le meilleur indicateur qu'on fait quelque chose de juste : ajouter un specialist deviendra "écrire un module Python avec ses tools + l'enregistrer dans la DB" et plus jamais "écrire 4 paradigmes pour expliquer au router qu'il existe".
