# Architecture des séquences de recherche — état actuel, modèles externes, cible

**Date :** 2026-05-25
**Branche :** `tout_doux`
**Auteur :** GitHub Copilot
**Objet :** Diagnostic des boucles et dérapages observés + schéma cible pour les flux de recherche simple et approfondie.

> **TL;DR** — Le système actuel demande au LLM de **piloter sa propre méta-procédure** (classifier, planifier, mettre à jour les todos, respecter un budget) via des paradigmes. Tous les modèles qui réussissent (Claude Code, Copilot, AutoGen, LangGraph, Aider…) font l'inverse : **l'orchestrateur impose les phases comme une machine à états**, et le LLM ne décide que du contenu *dans* une phase. Notre LLM (gemma4:26b, qwen) n'a pas la robustesse de suivi d'instructions de Claude Sonnet 4 ; il faut donc retirer ces décisions du prompt et les mettre dans le code.

---

## 1. Ce qu'on observe aujourd'hui (preuves)

### Symptôme A — Boucle infinie sur `set_task_class`
**Conversation :** `2026-05-25_17-49_6c35e929cb43`

Le LLM appelle 5 fois `set_task_class('deep_research')` malgré le cache, et 3 fois `manage_todo_list` avec la même todo générique :
```
1. Search for reliable sources
2. Critically evaluate (depends_on: 1)
3. Compile findings (depends_on: 2)
```

→ **Aucune délégation n'a jamais lieu.** Force-stop après 3 `duplicate_blocked` consécutifs. Workspace vide.

**Cause racine :** dans chaque `thought`, le LLM "re-planifie depuis zéro" parce que les paradigmes lui disent "AVANT toute action, classifier puis planifier". Il classifie, planifie, mais à chaque nouveau turn il *recommence le même rituel d'amorçage* au lieu d'avancer. Le retour `duplicate_blocked` avec `cached: true` est techniquement parfait mais le LLM **ne le lit pas comme un signal d'avancement** — il le re-tente.

### Symptôme B — Aspiration internet sans direction
**Conversation :** `2026-05-25_15-59_4415203edb29`

`web-search-specialist` enchaîne **15+ appels `web_search`** avec des requêtes redondantes ("reliable APIs", "open data sources", "structured knowledge bases"…) sans jamais s'arrêter. Notre garde-fou de budget (10 appels max) a été ajouté hier mais déclenche trop tard et de manière non lisible par le LLM (le `tools_payload` est tronqué silencieusement à la conclusion).

### Symptôme C — Une seule todo list ridicule
3 étapes vagues, copiées-collées de l'énoncé. Aucun découpage par sous-domaine ("encyclopédique", "scientifique", "actualité", "technique", "géographique" sont mentionnés dans le brief). Le LLM produit la todo la plus paresseuse possible parce que **rien ne mesure sa qualité**.

### Symptôme D — Prompt système obèse
Le prompt jean-michel actuel fait ~3500 tokens **avant** le briefing. Il contient :
- 11 délégations agents avec descriptions longues
- 9 sections de directives (`Precision`, `Style`, `Clarification`, `Restrictions`, `Sources`, `Epistemic posture`, `Bias hygiene`, `Metacognition`, `Inquiry method`)
- 12 paradigmes actifs, dont 3 qui parlent de planning de manière redondante (`assess_complexity_first`, `plan_before_complex_action`, `planning_with_todos`)
- Une section `Budget` injectée dynamiquement avec instructions "SIGNAL: …"

Pour gemma4:26b, c'est trop. Le modèle "perd" les instructions en cours de route et retombe sur des comportements génériques (re-classifier, re-planifier).

---

## 2. Ce que font les autres (et pourquoi ça marche)

| Système | Qui décide des phases | Qui décide du contenu | Garde-fous |
|---|---|---|---|
| **Claude Code** (Anthropic) | Code Python (`ToolUseLoop`) | LLM Claude | Boucle bornée par `max_iterations` + détection de répétition côté code |
| **GitHub Copilot Chat** | Code TypeScript (`ChatRequestTurn`) | LLM | Tool calls limités par contexte, pas par "instruction" au LLM |
| **Aider** | Code Python (`Coder.run`) | LLM | Diff appliqué/rejeté par le code, pas par le LLM |
| **AutoGen (Microsoft)** | `GroupChatManager` (code) | Agents LLM | Round-robin imposé, `max_rounds` strict |
| **LangGraph** | Graphe d'états explicite | LLM dans chaque nœud | Transitions = code, pas prompt |
| **DSPy** | Compilateur de signatures | LLM optimisé | Re-prompt automatique sur échec |
| **OpenAI Swarm** | `handoff` = retour structuré | LLM | Handoffs validés par schéma |

**Pattern commun :** le LLM ne **décide jamais** quand "passer à la phase suivante". Il **produit le contenu** d'une phase, et le code décide. Les paradigmes type "you MUST call X before Y" sont **un signe de mauvaise architecture** : si c'est obligatoire, c'est au code de le faire.

### Le retournement à opérer

Aujourd'hui :
```
Paradigme: "Tu DOIS appeler set_task_class avant de déléguer."
→ Si LLM oublie / boucle / mal interprète → catastrophe
```

Cible :
```
Code: au turn 0 du router, le tool delegate_to n'est PAS visible.
      Seuls set_task_class + (optionnellement) ask_human + return_to_user le sont.
      Une fois set_task_class appelé, le code passe à la phase suivante.
```

Le LLM n'a même pas l'opportunité de boucler — il ne voit que les outils valides pour son état courant.

---

## 3. Schéma cible : machine à états du Router

### 3.1 Vue d'ensemble

```
                        ┌──────────────────────────────────────┐
                        │      USER REQUEST (turn 0)           │
                        └────────────────┬─────────────────────┘
                                         ▼
        ┌─────────────────────────────────────────────────────────────┐
        │  PHASE 0 — TRIAGE (forcé code-side, 1 LLM turn max)         │
        │                                                              │
        │  Tools visibles: set_task_class, ask_human, return_to_user  │
        │  Sortie attendue: EXACTEMENT 1 appel set_task_class          │
        │  Si return_to_user → réponse triviale, fin                   │
        │  Si ask_human → on attend l'utilisateur, retour PHASE 0      │
        │  Si autre tool → erreur structurée + même tools, retry       │
        └────────────────┬─────────────────────────────────────────────┘
                         │
        ┌────────────────┴────────────────┐
        ▼                ▼                ▼
   single_fact      medium_task      deep_research
        │                │                │
        ▼                ▼                ▼
  ┌──────────┐    ┌────────────┐   ┌─────────────────────┐
  │ EXECUTE  │    │ PLAN+EXEC  │   │ PLAN → GATHER →     │
  │ direct   │    │ (todo: 2-5 │   │ CRITIQUE → BUILD →  │
  │ (1-2     │    │  étapes)   │   │ SYNTH               │
  │  outils) │    │            │   │                     │
  └────┬─────┘    └─────┬──────┘   └──────────┬──────────┘
       │                │                     │
       └────────────────┴──────────┬──────────┘
                                   ▼
                      ┌─────────────────────────┐
                      │  PHASE FINAL — RENDU    │
                      │  return_to_user OU      │
                      │  handoff à archivist    │
                      └─────────────────────────┘
```

### 3.2 Garde-fous code-side par phase

| Phase | Outils visibles | Borne dure | Boucle détectée si |
|---|---|---|---|
| **0 — Triage** | `set_task_class`, `ask_human`, `return_to_user` | 1 turn LLM | 2e turn sans tool valide |
| **1 — Plan** (deep_research uniquement) | `manage_todo_list(write)`, `ask_human` | 1 turn LLM | todo < 3 items OU items génériques (heuristique) |
| **2 — Gather** | `delegate_to` (uniquement agents `gather` : web-search, wikipedia, weather…), `manage_todo_list(update)` | N délégations (N = nb d'items `gather` dans todo) | 2 délégations identiques OU specialist retourne `gather_done` avec 0 fichier |
| **3 — Critique** (deep_research) | `delegate_to(critical-thinker)`, `manage_todo_list(update)` | 1 délégation | optionnel ; passe à 4 si todo n'en a pas |
| **4 — Build** | `delegate_to(document-builder, code-runner)`, `manage_todo_list(update)` | 1 délégation | document-builder retourne 0 fichier |
| **5 — Final** | `return_to_user`, `delegate_to(archivist)` | 1 turn LLM | — |

**Règle d'or :** le LLM voit `tools_payload` filtré par phase. Pas besoin de paradigmes "MUST". L'outil non-pertinent **n'existe pas** dans cette phase.

### 3.3 Le todo.json n'est plus géré par le LLM

Aujourd'hui : le LLM appelle `manage_todo_list(update_status)` après chaque délégation. Il oublie. Tu as ajouté `_auto_update_todos()` hier.

Cible :
- Le LLM appelle `manage_todo_list(write)` UNE fois en phase 1.
- Tous les updates sont **structurels** côté orchestrateur :
  - `pending → in_progress` quand `delegate_to(assignee_hint=X)` part
  - `in_progress → completed` quand le specialist retourne `gather_done` / `build_done`
  - `in_progress → blocked` si le specialist retourne `failed` ou si budget atteint
- L'outil `manage_todo_list(update_status)` est **supprimé** de la grant du router.

---

## 4. Schéma cible : flux d'un specialist

### 4.1 Web-search-specialist — la version qui marche

```
┌─────────────────────────────────────────────────────────────┐
│ BRIEFING reçu (1 question précise, max 2 lignes)            │
│ Ex: "List 5 programmatically-accessible scientific data     │
│      APIs (not Wikipedia). For each: name, endpoint, what   │
│      domains it covers. Write to workspace/scientific.md"   │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
        ┌──────────────────────────────────────┐
        │  PHASE A — SEARCH (max 3 calls)      │
        │                                       │
        │  Tools: web_search uniquement        │
        │  Stop conditions (code-side):        │
        │   - 3 appels effectués, OU           │
        │   - 2 résultats jugés exploitables   │
        │     (heuristique: domaine ≠         │
        │     SEO-junk, longueur ≥ 200 chars) │
        └──────────────────┬───────────────────┘
                           ▼
        ┌──────────────────────────────────────┐
        │  PHASE B — READ (max 5 fetches)      │
        │                                       │
        │  Tools: web_fetch (si on l'a) ou     │
        │         résumé des extraits search   │
        │  Stop: 5 pages lues OU question      │
        │        couverte (LLM signale)         │
        └──────────────────┬───────────────────┘
                           ▼
        ┌──────────────────────────────────────┐
        │  PHASE C — WRITE (forcé)             │
        │                                       │
        │  Tools: workspace_create_file        │
        │  Le LLM DOIT produire au moins       │
        │  1 fichier avant report_findings.    │
        │  Si 0 fichier → orchestrator force   │
        │  un retry avec briefing "écris ton   │
        │  brouillon maintenant".              │
        └──────────────────┬───────────────────┘
                           ▼
        ┌──────────────────────────────────────┐
        │  PHASE D — REPORT                    │
        │                                       │
        │  Tools: report_findings              │
        │  Schéma strict:                      │
        │   {summary: 1-3 phrases,             │
        │    files_produced: [paths],          │
        │    completion: "gather_done"}        │
        │  Validation: si files_produced       │
        │  vide ET phase C a réussi → erreur   │
        └──────────────────────────────────────┘
```

**Le budget de recherche n'est pas un paradigme.** C'est une borne dure code-side. Le LLM voit le tool `web_search` disparaître après 3 calls. Pas de "you SHOULD stop after 3 searches".

### 4.2 Découpage du briefing par le router

Le router ne dit pas "trouve des sources". Il **émet N briefings parallèles**, un par sous-domaine :

```
Phase Gather (deep_research, domaines = [encyclopédique, scientifique, ...]):

  parallel:
    delegate_to(web-search-specialist,
                briefing="3 encyclopedic APIs other than Wikipedia",
                expected={completion_verb: gather_done,
                          must_produce: [workspace/encyclopedic.md]})
    delegate_to(web-search-specialist,
                briefing="3 scientific data APIs (PubMed, arXiv, etc.)",
                expected={completion_verb: gather_done,
                          must_produce: [workspace/scientific.md]})
    delegate_to(wikipedia-specialist,
                briefing="Wikipedia article 'List of open APIs'",
                expected={completion_verb: gather_done,
                          must_produce: [workspace/wp_open_apis.md]})
```

Aujourd'hui, le router envoie **UN seul** briefing fourre-tout au web-search-specialist. C'est lui qui se noie ensuite avec 15 recherches incohérentes.

**Garde-fou code-side :** dans la phase Gather, si la todo a N items et le router émet 1 seule délégation pour les N → warning structuré : "Votre todo a N items, vous n'avez délégué qu'1. Émettez N délégations ou réduisez la todo."

---

## 5. Schéma comparatif : simple vs approfondi

### 5.1 Recherche simple (`single_fact`) — flux court

```
USER ─► [Router turn 0]
              │
              │ set_task_class("single_fact")  [seul tool dispo]
              ▼
        [Router turn 1]
              │
              │ tools dispo: delegate_to(wikipedia|weather|web-search) + return_to_user
              │ délégation UNIQUE attendue
              ▼
        [Specialist turn 1..N]   (max 3 tool calls hors report_findings)
              │
              │ report_findings(summary, files_produced)
              ▼
        [Router turn 2]
              │
              │ tools dispo: return_to_user UNIQUEMENT
              ▼
        USER (réponse)

Budget total: ≤ 6 turns LLM, ≤ 5 tool calls externes.
```

**Aucune todo, aucun plan, aucun fichier workspace requis.**

### 5.2 Recherche approfondie (`deep_research`) — flux long

```
USER ─► [Router turn 0]                    PHASE 0 (Triage)
              │ set_task_class("deep_research")
              ▼
        [Router turn 1]                    PHASE 1 (Plan)
              │ manage_todo_list(write, todos=[5-12 items précis])
              │ Validation code: ≥ 3 items, chaque item a assignee_hint
              ▼
        [Router turn 2..K]                 PHASE 2 (Gather)
              │ délégations parallèles vers gather specialists
              │ orchestrator: marque todos in_progress/completed
              │
              ├─► [web-search-spec]  ──► gather_done + workspace/X.md
              ├─► [web-search-spec]  ──► gather_done + workspace/Y.md
              └─► [wikipedia-spec]   ──► gather_done + workspace/Z.md
              ▼
        [Router turn K+1]                  PHASE 3 (Critique) — optionnelle
              │ delegate_to(critical-thinker,
              │             briefing="Évalue la fiabilité de chaque source listée",
              │             support_files=[X.md, Y.md, Z.md])
              │
              └─► [critical-thinker] ──► critic_done + workspace/critique.md
              ▼
        [Router turn K+2]                  PHASE 4 (Build)
              │ delegate_to(document-builder,
              │             briefing="Tableau final à partir de ces fichiers",
              │             support_files=[X.md, Y.md, Z.md, critique.md])
              │
              └─► [document-builder] ──► build_done + workspace/final.md
              ▼
        [Router turn K+3]                  PHASE 5 (Final)
              │ tools dispo: return_to_user UNIQUEMENT
              │ briefing pour return_to_user inclut: [final.md path + summary]
              ▼
        USER (réponse pointant vers final.md)

Budget total: ≤ 15 turns LLM, ≤ 30 tool calls externes (somme spécialistes).
Boucle détectée si:
  - 2 délégations identiques (même agent + même briefing)
  - Phase Gather > 20 min wall-clock
  - 0 fichier produit après Phase Gather complète
```

---

## 6. Garde-fous : récapitulatif des règles structurelles

### 6.1 Côté orchestrateur (code)

| # | Règle | Quand | Action |
|---|---|---|---|
| G1 | **Filter `tools_payload` par phase** | Avant chaque appel LLM | Retirer les outils non pertinents pour la phase courante |
| G2 | **1 seul `set_task_class` par requête** | Phase 0 → 1 | Une fois appelé, le tool disparaît du payload |
| G3 | **`manage_todo_list(write)` 1× max** | Phase 1 → 2 | Le tool disparaît après écriture initiale |
| G4 | **Auto-update todo** | À chaque `delegate_to` retournant | Pas de tool LLM pour ça |
| G5 | **Budget par specialist par phase** | Pendant Phase 2 | `web_search` retiré après N appels |
| G6 | **Validation "fichier produit"** | À la fin de chaque délégation | Si specialist promet `gather_done` mais 0 fichier → re-prompt structuré |
| G7 | **Détection délégation dupliquée** | Avant chaque `delegate_to` | Fingerprint (agent_code + hash(briefing)) ; bloqué si déjà vu |
| G8 | **Détection todo générique** | Après `manage_todo_list(write)` | Heuristique : titres < 8 mots et identiques entre items → re-prompt |
| G9 | **Hard stop turn count** | Continu | `max_turns_per_request = 20` |
| G10 | **Hard stop tool count** | Continu | `max_tool_calls_per_request = 50` (somme spec) |

### 6.2 Côté prompt (paradigmes)

**Supprimer :**
- `assess_complexity_first` → remplacé par G1+G2
- `plan_before_complex_action` → remplacé par G1+G3
- `planning_with_todos` → remplacé par G3+G4
- Les passages "you MUST call X before Y" partout
- La section "Budget" injectée dynamiquement (le LLM ne sait pas l'utiliser ; les budgets sont durs)

**Conserver (paradigmes de qualité de contenu, pas de procédure) :**
- `concise_output`, `epistemic_posture`, `bias_hygiene`, `source_admission_criteria`
- `wikipedia_search_strategy` (mais raccourci, juste "queries en anglais")
- `report_before_acting` (pour les write specialists)

**Effet attendu sur le prompt système :** ~3500 tokens → ~1500 tokens. Gemma4:26b respire.

---

## 7. Diagrammes de transition d'état (machine de Mealy)

### 7.1 Router

```
States: TRIAGE → (CLASSIFY) → PLAN | EXECUTE | TRIVIAL
PLAN     → (TODO_WRITTEN) → GATHER
GATHER   → (ALL_GATHER_DONE) → CRITIQUE | BUILD
CRITIQUE → (CRITIC_DONE) → BUILD
BUILD    → (BUILD_DONE) → FINAL
EXECUTE  → (SPECIALIST_DONE) → FINAL
TRIVIAL  → (DIRECT_ANSWER) → END
FINAL    → (USER_REPLY) → END

Any state can transition to ABORT on:
  - duplicate delegation detected
  - turn count exceeded
  - LLM produces 2 consecutive empty turns
```

### 7.2 Specialist (générique)

```
States: BRIEFED → ACT_LOOP → REPORT
ACT_LOOP: action tools (search/fetch/read/write)
          - bounded by per-tool budget
          - exit on report_findings OR budget exhausted
REPORT:   single report_findings call
          - schema-validated
          - completion_verb required
          - files_produced verified vs filesystem
```

---

## 8. Plan d'action priorisé (rollout)

### Phase A — Décharger le LLM (priorité absolue)
1. Implémenter `_phase_state` dans l'orchestrateur (enum : TRIAGE, PLAN, GATHER, CRITIQUE, BUILD, FINAL).
2. Filtrer `tools_payload` selon `_phase_state`. **C'est le changement le plus impactant.**
3. Supprimer les paradigmes `assess_complexity_first`, `plan_before_complex_action`, `planning_with_todos` (migration 062).
4. Tests d'intégration MockClient sur le flux complet `deep_research`.

### Phase B — Endiguer les boucles
5. Fingerprint des délégations (G7) avec re-prompt structuré "tu as déjà délégué ça, vérifie le résultat workspace/X.md".
6. Validation post-délégation (G6) : si `files_produced` vide alors que `must_produce` non vide → re-délégation 1× avec briefing renforcé.
7. Détection todo générique (G8) — heuristique simple : tous les titres < 10 mots OU contiennent ["search", "find", "evaluate", "compile"] sans noms propres → re-prompt.

### Phase C — Qualité des briefings
8. Quand `task_class == deep_research`, après `manage_todo_list(write)`, l'orchestrateur **génère lui-même** les N briefings parallèles à partir des items todo (un item = un briefing), sans laisser le LLM les réinventer.
9. Schéma strict pour `briefing` : `{question, scope, expected_files, max_tool_calls}`.

### Phase D — Observabilité
10. Event `PhaseTransition(from, to, reason)` yieldé par l'orchestrateur, affiché par CLI.
11. Commande `./jm.sh --replay <conv_id>` qui rejoue les events sans LLM pour debug.

---

## 9. Pourquoi ça va marcher cette fois

| Aujourd'hui | Cible |
|---|---|
| Le LLM décide quand passer à la phase suivante | Le code décide, le LLM ne voit que les outils valides |
| Les MUST sont dans le prompt | Les MUST sont l'absence d'alternative dans `tools_payload` |
| Le LLM gère la todo | L'orchestrateur gère la todo, le LLM la rédige une fois |
| Le LLM choisit son budget de recherche | Le code retire le tool quand le budget est atteint |
| Les boucles sont détectées tard (5 duplicates) | Les boucles sont impossibles (tool absent) |
| Prompt 3500 tokens, 12 paradigmes | Prompt 1500 tokens, 6 paradigmes (contenu, pas procédure) |
| 1 briefing fourre-tout → 15 searches en boucle | N briefings ciblés en parallèle, 3 searches chacun |

**Le principe fondamental :** ne jamais demander au LLM ce que le code peut décider. Les paradigmes ne servent qu'à orienter la **qualité du contenu** dans une phase, jamais à **enchaîner les phases**.

---

## 10. Question ouverte (à toi)

Cette refactorisation représente ~2 jours de travail :
- ~400 LOC dans `orchestrator.py` (state machine + filtrage tools)
- Migration DB 062 (purge des 3 paradigmes procéduraux)
- Refactor de `prompts.py` (sections conditionnelles par phase)
- ~30 nouveaux tests + adaptation de l'existant
- Mise à jour `docs/HOWTO_ADD_SPECIALIST_OR_TOOL.md`

**Alternative low-cost (1h)** si tu veux d'abord valider l'intuition :
- Garder l'archi actuelle.
- Juste filtrer `tools_payload` au turn 0 du router : ne montrer QUE `set_task_class` + `ask_human` + `return_to_user`.
- Au turn 1 (après set_task_class), montrer le reste sauf `set_task_class`.
- Mesurer si gemma4:26b cesse de boucler.

Si ça suffit pour démontrer que le diagnostic est bon, on industrialise. Sinon, on saute directement à la state machine.
