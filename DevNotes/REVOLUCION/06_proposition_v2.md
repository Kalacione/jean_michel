# 06 — Architecture cible v2

> Refonte de `02_architecture_cible.md` après prise en compte du retour
> utilisateur (`03_*`), de l'audit complémentaire (`04_*`) et de l'inspiration
> Claude Code / Copilot (`05_*`).
>
> Différence avec 02 : on ne remplace pas la mémoire LLM par un task tree.
> On donne au LLM sa mémoire conversationnelle native via le format
> `messages[]` d'Ollama. Le task tree et l'audit BDD restent côté Python,
> à des fins de traçabilité, pas comme substitut de contexte.

## 0. Le diagnostic en une phrase

Le sabotage initial de Jean-Michel ([04_audit_complementaire.md §7](DevNotes/REVOLUCION/04_audit_complementaire.md))
est que [llm.py L.74-79](src/jeanmichel/llm.py#L74) construit à chaque appel
`messages=[{system}, {user}]` au lieu d'accumuler les `assistant` et `tool`
turns. Tout l'empilement de garde-fous (7 budgets, 5 gates inline, paradigmes
correctifs, MUST en cascade) compense cette unique privation. La v2 retire
le sabotage et laisse tomber la moitié de la compensation.

## 1. Principes fondateurs (v2)

Chaque principe énonce un changement concret par rapport à l'existant.

1. **L'orchestrateur Python est sans état interne pour la décision.** La
   boucle principale n'embarque pas de `current_state`, pas de table de
   transitions, pas de drapeau de phase. Elle lit les `tool_calls` émis par
   le LLM et les exécute. L'état de la conversation vit dans `messages[]`
   (côté LLM) et `state.json` (côté Python, un snapshot de compteurs, pas
   une machine).

2. **Le LLM voit son histoire complète via le format natif Ollama.**
   `messages[]` accumule `system → user → assistant → tool → assistant → tool → …`.
   Le LLM peut référencer ses propres tool_calls et ses propres
   raisonnements précédents directement. Aucun récap reconstruit.

3. **Plusieurs modèles, choix entièrement configurable.** Quatre slots
   de modèles nommés (`DISPATCH_MODEL`, `MAIN_MODEL`, `COMPACTOR_MODEL`,
   `SUBAGENT_DEFAULT_MODEL`) définis dans `config.py`. Chacun peut être
   surchargé par env var et par CLI flag (cf. §12 pour la chaîne de
   précédence). Per-agent : la table `agents` reçoit une colonne
   `model_override` qui permet d'assigner un modèle spécifique à un
   subagent donné (ex: `critical-thinker` → gemma4:26b si on veut plus
   de fidélité d'analyse). Le choix est fait par le code de
   l'orchestrateur, jamais par un LLM amont, et **rien n'est hardcoded**.

4. **Toute exigence déterministe est un hook Python, pas une consigne
   prompt.** Si on doit garantir un comportement (refus, validation,
   dédup, écriture forcée vers workspace), c'est une fonction Python qui
   intercepte le `tool_call` ou le `tool_response`. Les MUST en cascade
   des migrations 057-061 disparaissent.

5. **Le `delegate_to` instancie un nouveau contexte LLM, pas une frame
   récursive.** Le subagent reçoit son propre `messages[]` initial. Au
   retour, il transmet au parent un dict `{summary, files_produced,
   confidence}`. Son historique LLM n'est jamais exposé au parent.

6. **Profondeur bornée, largeur dynamique.** Un hook refuse `delegate_to`
   au-delà de `MAX_DEPTH=5`. La largeur (nombre de `delegate_to` au même
   niveau) n'est pas plafonnée numériquement — elle est régulée par les
   garde-fous turn-wide (`MAX_SEARCH=10`, wall-clock 900 s) et par la
   compaction de contexte de l'agent qui décide les délégations. Chaque
   subagent reçoit sa propre fenêtre de contexte partitionnée, il n'y a
   pas de budget hérité entre niveaux.

7. **Budget de contexte partitionné, inspiré de Copilot CLI**. La fenêtre
   de contexte du modèle (typ. 128k pour gemma4) est découpée en trois
   zones :
   - `SYSTEM_RESERVE` : taille du system prompt rendu + tools payload.
     Mesurée au démarrage du tour, immuable pendant le tour.
   - `OUTPUT_RESERVE` : 15 % du contexte total, réservé pour que la
     réponse finale de l'assistant ne soit jamais tronquée. Ratio
     délibérément plus bas que les 25–30 % de Copilot : les outputs longs
     sont écrits au workspace au fil de l'eau (paradigme
     `workspace_progressive_write`, §11 bis), donc la réponse finale est
     toujours courte (résumé + pointeurs vers les fichiers).
   - `WORKING` = contexte total − SYSTEM_RESERVE − OUTPUT_RESERVE. C'est
     l'espace disponible pour la croissance du `messages[]` (assistant
     turns + tool messages).

   La compaction s'applique uniquement au `WORKING`, en escalade à
   plusieurs niveaux (cf. §7). La VRAM disponible est un paramètre
   matériel séparé qui dicte quels modèles peuvent être chargés
   simultanément, pas un quota dépensable par tour.

8. **Workspace = mémoire partagée inter-agents.** Inchangé sur le principe.
   Le changement est qu'un hook force l'écriture de findings vers le
   workspace après N tool calls de recherche sans persist, plutôt qu'un
   paradigme implorant le LLM de le faire.

9. **K.I.S.S strict.** Tout composant qui peut être supprimé (sans perdre
   une garantie listée ici) est supprimé.

## 2. Diagramme global

```
                       CLI (humain) — user_text
                              │
                              ▼
        ┌─────────────────────────────────────────────────────┐
        │  TIER 0 — DISPATCHER  (granite4.1:8b, no thinking)  │
        │  format="json" forcé. Sortie : intent + tool? + args│
        └─────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴────────────────┐
              ▼                                ▼
     intent == "alexa"                  intent == "deep"
              │                                │
              ▼                                ▼
      Exécute le tool en Python       ┌─────────────────────────┐
      → réponse formatée              │ TIER 1 — MAIN AGENT     │
      (template Python par défaut,    │ (gemma4:latest)         │
      LLM short-call si nécessaire)   │ messages[] multi-turn   │
                                      │ tools natifs            │
                                      └─────────────────────────┘
                                            │
                                            │ (delegate_to)
                                            ▼
                                      ┌─────────────────────────┐
                                      │ TIER 2 — SUBAGENT       │
                                      │ messages[] frais        │
                                      │ scoped tools + paradigms│
                                      │ Renvoie un dict struct  │
                                      └─────────────────────────┘

Hooks Python autour de chaque appel LLM et de chaque tool call :
  PreLLMCall       → escalade de compaction à 4 niveaux sur le WORKING (§7)
  PreToolUse       → valide les grants, déduplique avec contexte, gates depth/MAX_SEARCH
  PostToolUse      → persist forcé après N research calls, audit sandbox
  OnDelegateReturn → push du résultat structuré dans le messages[] du caller
```

## 3. Tier 0 — Dispatcher (§A)

### Modèle et configuration

- **Modèle Ollama** : `config.DISPATCH_MODEL` (défaut : `granite4.1:8b`,
  5.3 GB, déjà présent en local). Override par env
  `JEANMICHEL_DISPATCH_MODEL` ou CLI `--dispatch-model`.
- **Thinking** : désactivé. Le dispatcher n'a pas besoin de raisonner
  pour classifier ; un raisonnement coûterait latence et tokens sans gain.
- **Température** : 0.0 (output déterministe).
- **Format** : `format: "json"` côté Ollama (option officielle qui contraint
  le modèle à produire un JSON syntaxiquement valide). Validation Python
  du schéma derrière, indépendamment du modèle.

### Prompt

Statique, défini une fois en Python, jamais composé dynamiquement :

```
You classify a user request. Reply with strict JSON of shape:
{
  "intent": "alexa" | "deep",
  "tool":   "clock" | "weather" | "wikipedia_search" | null,
  "args":   { ... }
}

intent="alexa" when ONE tool from the list can satisfy the request directly:
  - clock              : current time / date
  - weather            : current weather or forecast at a location
  - wikipedia_search   : single factual lookup (definition, dates, identity)

intent="deep" for everything else (comparison, multi-step research,
codebase analysis, document production, debugging, advice).

If you cannot decide, answer "deep". Never invent a tool name not in
the list above.
```

### Routage en sortie

- **`intent == "alexa"` et `tool` reconnu** : l'orchestrateur exécute le
  tool natif Python, puis :
  - Si la sortie tool contient déjà du texte adapté (cas `clock`, `weather`
    avec template existant), formatage déterministe en Python dans la
    langue de l'utilisateur (utilisation de `user_profile.toml` + détection
    par `langdetect`).
  - Sinon (cas `wikipedia_search` qui retourne du JSON brut), un second
    appel `granite4.1:8b` court reformule en une réponse 2-4 phrases dans
    la langue de l'utilisateur. Ce second appel n'a pas de tool, pas de
    thinking, et reçoit la sortie tool en `role=user`.
- **`intent == "alexa"` et `tool == null`** : le LLM a sur-classifié vers
  ALEXA sans pouvoir nommer le tool. L'orchestrateur traite ça comme DEEP.
- **`intent == "deep"`** : bascule Tier 1.
- **Sortie non-parsable malgré `format: "json"`** : 1 retry avec le même
  prompt. Si échec, bascule DEEP. Le défaut sûr est de réfléchir
  davantage, jamais de deviner un tool.

### Précision attendue

Pas de chiffre de latence cible posé arbitrairement dans ce document.
La performance se mesure après implémentation — voir benchmark en phase
de validation post-implémentation.

## 4. Tier 1 — Main agent en boucle multi-turn (§B)

### Modèle et configuration

- **Modèle** : `config.MAIN_MODEL` (défaut : `gemma4:latest`, 9.6 GB).
  Override par env `JEANMICHEL_MAIN_MODEL` ou CLI `--main-model` pour
  passer à `gemma4:26b` ou `qwen3:14b` selon la configuration matérielle
  (cf. §11 ter B). Choix du défaut justifié : gemma4 dans sa version
  courante a démontré le meilleur équilibre tool-use / thinking sur ce
  projet d'après les commits historiques.
- **Thinking** : activé (le système prompt expose la canalisation thought).
- **Température** : 0.2.

### Boucle Python (corrigée)

```python
def run_main_loop(conv: Conversation, user_text: str) -> str:
    """Run the main agent loop on a deep request. Returns the user-facing answer."""
    messages = conv.messages_or_init()  # see §6 — persisted across turns
    messages.append({"role": "user", "content": user_text})

    state = conv.state
    # Partitioned budget (cf. §1.7).
    total_ctx = model_context_window("gemma4:latest")       # ex. 128_000
    tools_payload = tools_payload_for(conv.main_agent)
    state.system_reserve_tokens = estimate_tokens(messages[0]) + estimate_tokens(tools_payload)
    state.output_reserve_tokens = int(0.15 * total_ctx)     # 15 % — outputs longs écrits au workspace
    state.working_budget = total_ctx - state.system_reserve_tokens - state.output_reserve_tokens

    while True:
        # PreLLMCall hook may MUTATE messages (4-level compaction escalation, cf. §7).
        hooks.run("PreLLMCall", messages=messages, state=state)

        resp = llm.chat(
            model="gemma4:latest",
            messages=messages,
            tools=tools_payload_for(conv.main_agent),
            thinking=True,
            temperature=0.2,
        )

        # Append the assistant turn verbatim (content + tool_calls).
        messages.append({
            "role": "assistant",
            "content": resp.content,
            "thinking": resp.thinking,        # captured but not re-sent to LLM
            "tool_calls": resp.tool_calls,
        })

        if not resp.tool_calls:
            # No more work to do. The assistant content IS the final answer.
            conv.persist(messages, state)
            return resp.content

        for call in resp.tool_calls:
            decision = hooks.run("PreToolUse", call=call, state=state)
            if decision.deny:
                tool_result = {"error": decision.reason, "summary": decision.reason}
            else:
                tool_result = tool_registry.execute(call, conv)
            hooks.run("PostToolUse", call=call, result=tool_result, messages=messages, state=state)

            # Ollama tool message shape: {"role":"tool","tool_name":...,"content":...}
            messages.append({
                "role": "tool",
                "tool_name": call.name,
                "content": json.dumps(tool_result, ensure_ascii=False),
            })

        conv.persist(messages, state)  # crash-safe persistence each iteration
        # No explicit "budget remaining" debit: the PreLLMCall hook handles
        # working-budget pressure via the 4-level compaction escalation.
        # Wall-clock safety net (§8) catches pathological loops.
```

Points techniques :

- **Condition d'arrêt** : `resp.tool_calls` vide. Le LLM signale qu'il a fini
  en émettant un `assistant` turn sans tool. Le `content` de ce turn est
  la réponse à l'utilisateur. Pas besoin d'un tool `return_to_user` dédié
  (on retire celui qu'on avait, simplification).
- **Format `tool` message** : `{role: "tool", tool_name: <name>, content: <stringified result>}`,
  conformément à l'API Ollama actuelle (vérifié 2026-05).
- **Pression de contexte** : gérée exclusivement par `PreLLMCall` selon
  l'escalade à 4 niveaux (§7). Pas de débit explicite à chaque iteration
  dans la boucle elle-même — le hook regarde `estimate_tokens(messages)`
  vs `state.working_budget` au moment où il s'exécute.
- **Persistence après chaque itération** : `messages.json` + `state.json`
  + `events.jsonl` sont sauvés sur disque, ce qui rend la conversation
  reprenable après crash ou redémarrage process.
- **Saturation extrême du `WORKING`** : si même l'autocompact (niveau 4)
  ne libère pas assez d'espace, la garantie de §7 prend le relais : un
  appel `chat()` minimal avec un `messages[]` réduit à
  `[system + last 2 turns + notice]` produit toujours une réponse,
  quitte à être dégradée.

### Tools exposés au main agent

| Catégorie     | Tools                                                                                         |
|---------------|-----------------------------------------------------------------------------------------------|
| Recherche     | `web_search`, `wikipedia_search`, `wikipedia_get_page`                                        |
| Workspace     | `workspace_create_file`, `workspace_append`, `workspace_str_replace`, `workspace_view`, `workspace_list` |
| Exécution     | `bash_sandbox` (selon `agent_sandbox_grants`)                                                 |
| Subagent      | `delegate_to`                                                                                 |
| Utilitaires   | `clock`, `conv_read_file`, `self_inspect_*`                                                   |
| Mémoire user  | `manage_user_memory` (cf. §10)                                                                |

### Tools retirés du payload (par rapport à l'existant)

- `set_task_class` : la classification est faite par le Tier 0, plus par le
  main agent.
- `manage_todo_list` : le main agent n'a pas de todo séparée — son
  `messages[]` est sa todo.
- `signal_convergence` (déjà déprécié) et `report_findings` (utilisé
  uniquement par les subagents, voir §5).
- `planner_done`, `gather_done`, `critic_done`, `build_done` : les
  "completion verbs" sans applicateur (cf. [04 §6](DevNotes/REVOLUCION/04_audit_complementaire.md))
  disparaissent.
- `return_to_user` : implicite, voir condition d'arrêt ci-dessus.

## 5. Tier 2 — Subagent isolé, délégation imbriquée (§C)

`delegate_to(agent_code, briefing, expected_summary_format?, support_files?)`
est un tool exposé à tous les agents non-finalizer qui le possèdent dans
`agent_tools` (main agent par défaut ; chaque subagent peut l'avoir ou
non, selon son grant). Quand il est appelé, l'orchestrateur :

1. Vérifie la whitelist : `agent_delegation_targets` (cf. [04 §5](DevNotes/REVOLUCION/04_audit_complementaire.md))
   est désormais seedée. Si `agent_code` n'est pas dans la whitelist du
   caller, refus côté hook `PreToolUse` avec erreur structurée.
2. Vérifie la profondeur : si `current_depth + 1 > MAX_DEPTH`, refus
   côté hook.
3. Construit un `messages[]` initial **vide d'historique du caller** :
   ```python
   sub_messages = [
       {"role": "system", "content": render_system_prompt(agent_code, depth=caller_depth+1, ...)},
       {"role": "user",   "content": briefing},
   ]
   ```
4. Lance la même boucle (`run_main_loop`-équivalent) avec ce `messages[]`,
   l'`agent_code` du subagent, et **son propre budget partitionné**
   calculé sur la fenêtre de contexte du modèle qu'il invoque
   (cf. §1.7). Modèle choisi dans l'ordre : `agents.model_override` du
   subagent si non-NULL, sinon `config.SUBAGENT_DEFAULT_MODEL` (défaut
   `gemma4:latest`). Pas d'héritage du caller : le subagent démarre
   frais.
5. Le subagent itère jusqu'à émettre un `tool_call` nommé `report_back`.
   Schéma :
   ```
   report_back(
     summary: str,                          # 1-3 phrases, ce qu'il a établi
     files_produced: list[str],             # chemins workspace
     confidence: "low" | "medium" | "high",
     low_confidence_reason: str | None,     # OBLIGATOIRE si confidence=="low"
                                            # une phrase synthétique
   )
   ```
   Ce tool a pour seul effet d'arrêter la boucle du subagent et de
   cristalliser le retour. Le hook `OnDelegateReturn` rejette le call si
   `confidence == "low"` et `low_confidence_reason` est vide ou absent —
   le subagent doit re-émettre.
6. L'orchestrateur retourne au **caller direct** (pas forcément le main
   agent — peut être un subagent intermédiaire) un dict structuré
   `{agent: agent_code, summary, files_produced, confidence}` qui est
   poussé comme `role=tool` dans le `messages[]` du caller.

Le `sub_messages[]` complet est persisté côté disque pour audit
(`conversations/<id>/subagent_<request_id>.json`) mais n'est jamais
réinjecté dans le `messages[]` du caller.

### Délégation imbriquée (sub-subagent)

C'est un principe central, pas une option. Un subagent qui rencontre une
sous-tâche qu'il ne peut pas résoudre seul appelle lui-même `delegate_to`,
sans repasser par le main agent. La récursion descend dans l'arbre,
elle ne remonte pas.

Mécanique concrète :

- Le subagent A est en cours d'exécution avec son propre `messages[]`,
  son budget alloué, et `current_depth = 1`.
- A émet un `delegate_to(agent_code="wikipedia-specialist", briefing="...")`.
- Le hook `PreToolUse` valide la whitelist et la profondeur
  (`current_depth + 1 = 2 ≤ MAX_DEPTH`), spawne le subagent B avec
  `current_depth = 2` et un nouveau budget alloué depuis ce qui reste à A.
- B exécute sa propre boucle. S'il a lui-même besoin de déléguer
  (par exemple à `web-search-specialist`), il appelle `delegate_to` et
  un subagent C avec `current_depth = 3` est spawné. Et ainsi de suite
  jusqu'à `MAX_DEPTH = 5`.
- B `report_back` à A. Le retour est poussé dans `A.messages[]`. A
  continue son travail avec cette information.
- Quand A décide de conclure, A `report_back` au main agent (ou à son
  propre caller si A a été spawné par un subagent).

Le main agent n'est jamais réveillé pendant les délégations imbriquées
sous son arbre. Il n'est notifié qu'au retour du subagent qu'il a
directement appelé.

### Garde-fous structurels sur l'arbre

Avec le modèle de **budget de contexte partitionné** (cf. §1.7 et §7),
chaque appel LLM — main ou subagent à n'importe quelle profondeur — a
son propre découpage `SYSTEM_RESERVE / WORKING / OUTPUT_RESERVE` calculé
sur la fenêtre de contexte du modèle qu'il invoque. Il n'y a pas de
"budget partagé descendant" entre niveaux : un subagent n'hérite pas du
budget restant de son caller. Il démarre frais.

Conséquence : le risque d'arbre runaway n'est pas régulé par raréfaction
de budget mais par trois garde-fous structurels indépendants :

- **Profondeur** : `MAX_DEPTH = 5` appliqué uniformément par le hook
  `PreToolUse(delegate_to)`. Au-delà, refus et l'appelant doit conclure
  avec ce qu'il a.
- **Recherche cumulée** : `MAX_SEARCH = 10` (web_search + wikipedia_*)
  comptabilisé **au niveau de la conversation pour le tour humain
  courant**, pas par subagent. Compteur incrémenté à chaque tool de
  recherche réussi, indépendamment de la profondeur où il a lieu.
  Empêche un arbre entier de transformer un tour en mode aspirateur.
- **Wall-clock 900 s** : filet ultime au niveau du tour humain (cf. §8).
  Si une cascade pathologique passe les deux gardes ci-dessus, le timer
  l'arrête.
- **Whitelist par agent** : `agent_delegation_targets` permet de stopper
  certaines chaînes structurellement (ex : un `weather-specialist` ne
  peut pas spawn `code-runner` — ça n'a aucun sens fonctionnel et serait
  pure explosion d'arbre).

### Traçabilité côté orchestrateur

La délégation imbriquée doit rester observable de bout en bout. Trois
mécanismes garantissent que l'orchestrateur Python connaît à chaque
instant l'état complet de l'arbre :

1. **Spawn centralisé** : chaque création de subagent passe par une
   fonction Python `spawn_subagent(caller, agent_code, briefing, depth, budget)`
   qui est la seule à instancier un nouveau `messages[]`. Aucun LLM ne
   spawne directement — il appelle `delegate_to`, hook `PreToolUse`
   valide, `spawn_subagent` exécute. L'orchestrateur a la main sur la
   création.
2. **Events `DelegationStarted` / `DelegationCompleted`** (§6 bis) émis
   à chaque transition. Le CLI les rend en temps réel, ils servent aussi
   à toute analyse ultérieure.
3. **Persistence du `sub_messages[]`** : chaque subagent persiste son
   propre array dans `subagent_<request_id>.json` à chaque itération.
   Si l'orchestrateur veut inspecter ce qui se passe au niveau 3 pendant
   que le niveau 0 attend, il peut.

### Esprit calls LLM courts

Le principe directeur derrière la cascade `delegate_to` n'est pas
d'autoriser n'importe quelle profondeur, c'est de **garder chaque appel
LLM individuel borné en contexte**. À la place d'un main agent qui
accumule 100 turns dans son `messages[]`, on a un main agent à 20 turns
qui délègue 4 fois, chaque subagent ayant lui-même un `messages[]` borné
par son propre `WORKING` partitionné.

Deux mécanismes appliquent cet esprit :

- **Compaction multi-niveaux du `messages[]`** (§7) — escalade à partir
  de 70 % du `WORKING` : Snip et Microcompact déterministes d'abord,
  puis Context Collapse et Autocompact LLM seulement si nécessaire. Le
  contexte d'un appel donné ne dépasse jamais le seuil sans réponse de
  l'orchestrateur.
- **Isolation entre appels** — chaque subagent démarre frais, ne porte
  pas le `messages[]` du caller. Un sous-arbre ne pollue pas son parent.

Un agent qui se sent contraint sur sa propre tâche peut toujours
`delegate_to` une sous-tâche à un specialist plus adapté — la délégation
ouvre un nouvel appel avec sa propre fenêtre de contexte propre, et le
caller voit ensuite uniquement le `report_back` structuré.

### Conséquence sur l'historique d'exécution

Le caller (qu'il soit le main agent ou un subagent intermédiaire) ne voit
jamais le raisonnement de son subagent. Il voit le retour structuré + les
fichiers workspace produits (qu'il peut lire avec `workspace_view` ou
`conv_read_file` s'il en a besoin). C'est le pattern Task tool de Claude
Code, généralisé à tous les niveaux.

## 6. État runtime persisté (§E)

Deux fichiers par conversation, dans le dossier existant `conversations/<id>/` :

### `messages.json`

Sérialisation directe du `messages[]` du main agent au format Ollama. Un
seul array, mis à jour après chaque itération de la boucle. Permet la
reprise après crash et le `--resume` existant. Pour les subagents, un
fichier séparé `subagent_<request_id>.json` par exécution.

### `state.json`

Snapshot de compteurs lisibles pour l'orchestrateur :

```json
{
  "system_reserve_tokens": 12400,
  "output_reserve_tokens": 19200,
  "working_budget": 96400,
  "working_tokens_used": 28500,
  "depth_current": 0,
  "search_calls_total": 4,
  "search_calls_since_last_persist": 2,
  "active_subagent": null,
  "last_iteration_at_utc": "2026-05-27T18:42:13Z"
}
```

Pas un état au sens "machine à états". Juste un snapshot de scalaires que
les hooks consultent et mutent. `working_tokens_used` est recalculé à
partir de `estimate_tokens(messages) − system_reserve_tokens` à chaque
itération, pas accumulé.

### Ce qui n'est PLUS source d'état runtime

- `plan.md` n'est plus écrit par l'orchestrateur en tant que source d'état.
  Il peut être régénéré à la demande comme rendu humain lisible à partir
  de `messages.json` (script séparé, hors chemin chaud).
- `todo.json` est supprimé en tant que **fichier d'état**. La fonction
  d'affichage CLI qu'il portait est remplacée par un flux d'events
  émis par l'orchestrateur (voir [§6 bis](#6-bis-affichage-cli-temps-réel)).

### Analyse : tables BDD — lesquelles garder, lesquelles supprimer

Le doc 03 a posé la question : on garde le système `requests` / `parent`
ou on le vire ? Avec `messages.json` et le filesystem qui portent toute
l'arborescence d'exécution, la BDD devient redondante pour la plupart
des tables runtime. Analyse table par table :

| Table                  | Usage actuel                                  | Décision v2 | Pourquoi                                                                 |
|------------------------|-----------------------------------------------|-------------|--------------------------------------------------------------------------|
| `conversations`        | métadonnées (id, title, mode, status, langue) | **garder**  | `--list-conv` et `--resume` doivent rester O(1), pas un scan filesystem  |
| `requests`             | arbre des requests parent/enfant              | **virer**   | `messages.json` + `subagent_<id>.json` portent cette arborescence        |
| `artifacts`            | index des fichiers produits par request       | **virer**   | le filesystem est l'index ; `glob` + frontmatter YAML suffit             |
| `sandbox_executions`   | audit cross-conv des exec sandbox             | **virer**   | remplacé par `~/.jean-michel/sandbox_audit.jsonl` global (cross-conv)    |
| `conversation_phases`  | tracker phases planner/gather/critic/build    | **virer**   | les phases disparaissent (cf. [04 §6](DevNotes/REVOLUCION/04_audit_complementaire.md)) |
| `paradigms` + dérivées | seed du système de prompts                    | **garder**  | source de vérité du composition prompt par agent                         |
| `agents` + dérivées    | définitions d'agents + grants                 | **garder**  | source de vérité de la configuration                                     |
| `user_memory` (nouveau)| mémoire long-terme cross-conv                 | **garder**  | besoin cross-conversation (cf. §10)                                      |

L'argument original pour `requests` / `artifacts` était d'éviter un scan
filesystem coûteux pour des conversations longues. En pratique, sur ce
projet :

- Un `messages.json` même volumineux (1-2 Mo après une conversation
  riche) se lit en une dizaine de millisecondes en Python — négligeable
  face à un appel LLM qui prend des secondes.
- Le filesystem per-conv (`conversations/<id>/*.md`) est déjà lisible
  humainement (cf. principe du README "fichiers sur disque = artefacts
  dérivés"). Le doubler en BDD ajoute une source de désynchronisation
  potentielle sans gain réel.
- Pour les métriques cross-conversation (combien de délégations en
  moyenne, profondeurs observées, etc.), un script ad-hoc qui scanne
  `conversations/*/messages.json` est largement assez rapide à l'échelle
  d'un user local (quelques centaines de conversations).
- L'audit sandbox cross-conv reste utile pour la sécurité — d'où le
  fichier global `~/.jean-michel/sandbox_audit.jsonl` qui reçoit chaque
  exec sandbox de toutes les conversations. Un seul fichier append-only,
  pas une table.

Bénéfice du nettoyage : moins de schéma à maintenir, plus de migrations
correctives à anticiper, une seule source de vérité par information
(filesystem pour les artefacts d'exécution, BDD pour la configuration
durable et la mémoire utilisateur).

### Multi-turn humain (mode chat)

Quand l'humain pose une seconde question dans la même conversation :

1. `messages.json` est rechargé (l'array complet du tour précédent).
2. Un nouveau `{"role":"user","content":<nouveau texte>}` est appendé.
3. La boucle `run_main_loop` redémarre.

L'`archivist` (qui maintenait `summary.md`) devient inutile dès lors que
`messages.json` porte toute l'histoire. La compaction (§7) s'occupe de
réduire la taille du contexte si nécessaire. `summary.md` peut survivre
comme rendu humain en lecture, généré à la demande comme `plan.md`.

## 6 bis. Affichage CLI temps réel

`todo.json` était utilisé par le CLI pour afficher l'arbre de
progression. La suppression du fichier ne doit pas dégrader cette
visibilité — au contraire, on peut en profiter pour la durcir.

### Modèle d'events émis par l'orchestrateur

L'orchestrateur émet un flux d'events typés que le CLI consomme pour le
rendu (le pattern existe déjà via le générateur `run()` actuel ; on le
formalise) :

| Event                      | Émis quand                                                | Données utiles au rendu                                            |
|----------------------------|-----------------------------------------------------------|--------------------------------------------------------------------|
| `RequestStarted`           | début d'un tour humain ou d'une délégation                | `agent`, `depth`, `briefing_summary`                               |
| `LLMCallStarted`           | avant chaque appel LLM                                    | `agent`, `model`, `messages_count`, `working_tokens_used`          |
| `LLMCallCompleted`         | après chaque appel LLM                                    | `tokens_used`, `tool_call_count`                                   |
| `ToolCallStarted`          | avant exécution d'un tool                                 | `agent`, `tool_name`, `args_summary`                               |
| `ToolCallCompleted`        | après exécution d'un tool                                 | `tool_name`, `result_summary`, `duration_ms`                       |
| `DelegationStarted`        | quand un `delegate_to` est validé et le subagent spawne   | `parent_agent`, `child_agent`, `depth`, `child_working_budget`     |
| `DelegationCompleted`      | quand le subagent fait `report_back`                      | `child_agent`, `summary`, `confidence`, `files_produced`           |
| `HookFired`                | quand un hook prend une action visible (refus, compaction)| `hook_name`, `action`, `reason`                                    |
| `WorkingBudgetUpdate`      | quand `working_tokens_used / working_budget` franchit un seuil (70/80/90/95 %) | `ratio`, `compaction_level_triggered`           |
| `MemoryNearCapacity`       | quand `user_memory` atteint 90 entrées (warning §10)      | `current_count`, `limit`                                           |
| `RequestCompleted`         | quand l'agent émet sa réponse finale                      | `agent`, `final_content_summary`                                   |

### Persistance du flux : `events.jsonl` per-conversation

Tous les events émis sont aussi append-ed à
`conversations/<id>/events.jsonl` au format JSON Lines (une ligne =
un event sérialisé avec son timestamp UTC, son type et son payload). Ce
fichier est :

- **Le journal d'événements canonique de la conversation.** Append-only,
  ordonné, jamais réécrit.
- **L'artefact qui permet de reconstruire l'arbre de délégation** sans
  passer par la BDD. Filtrer les lignes `DelegationStarted` /
  `DelegationCompleted`, les linker via `depth` et `parent_agent`, et
  l'arbre est reconstruit. Une dizaine de lignes Python ou un `jq`
  one-liner suffisent.
- **L'archive de progression** : un crash CLI (pas orchestrateur) peut
  être recouvert en replayant le `events.jsonl` depuis le début.
- **La source pour les métriques cross-conversation** : un script
  scanne `conversations/*/events.jsonl` et agrège (profondeur moyenne,
  taux de délégation, durée par tool, etc.).

C'est ce qui permet de virer la table `requests` sans perdre la capacité
de visualiser l'arborescence. La BDD aurait porté la même info ; le
filesystem la porte tout aussi bien, avec en plus l'avantage d'être
self-contained par conversation et lisible humainement.

### Reconstitution de l'affichage type todo

L'affichage CLI "voici les tâches en cours / faites / restantes" est
reconstitué uniquement depuis le flux d'events (live + `events.jsonl`) :

- Chaque `DelegationStarted` est un item de progression "en cours" avec
  son `child_agent` + son `briefing_summary` (1 ligne).
- Chaque `DelegationCompleted` marque cet item comme done, avec son
  `summary` + son `confidence`.
- L'arborescence parent/enfant est lue depuis `depth` et le `parent_agent`.
- Les `ToolCallStarted` / `ToolCallCompleted` sont rendus en sous-items
  sous leur `DelegationStarted` parent (ou sous le main agent).

### Sandbox audit cross-conversation

Le fichier `~/.jean-michel/sandbox_audit.jsonl` reçoit une ligne JSON par
exécution `bash_sandbox`, toutes conversations confondues. Format :

```json
{"utc":"2026-05-27T18:42:13Z","conv_id":"...","agent":"code-runner","command":"python3 script.py","exit_code":0,"duration_ms":1230}
```

Append-only, jamais lu par le runtime, accessible pour audit a posteriori
(grep + jq, ou un script dédié de stats).

## 7. Hooks Python (§D)

Quatre hooks. Aucun n'est dans la BDD. Aucun n'est dans un prompt. Tous
sont du code Python testable en isolation.

### `PreLLMCall(messages, state) -> None`

Mutate `messages` en place selon une **escalade de compaction à 4
niveaux** inspirée du modèle Copilot CLI. Chaque niveau est moins
coûteux que le suivant — on n'appelle un LLM qu'en dernier recours.

```python
def pre_llm_call(messages, state):
    used = estimate_tokens(messages) - state.system_reserve_tokens
    capacity = state.working_budget    # = total_context − system_reserve − output_reserve
    ratio = used / capacity

    if ratio < 0.70:
        return                          # rien à faire

    if ratio < 0.80:
        compact_snip(messages, state)        # niveau 1, déterministe, pas de LLM
        return

    if ratio < 0.90:
        compact_snip(messages, state)
        compact_microcompact(messages, state) # niveau 2, déterministe
        return

    if ratio < 0.95:
        compact_snip(messages, state)
        compact_microcompact(messages, state)
        compact_collapse(messages, state)    # niveau 3, appel LLM ciblé
        return

    # ratio >= 0.95 : dernier recours
    compact_autocompact(messages, state)     # niveau 4, appel LLM sur tout l'historique milieu
```

#### Niveau 1 — Snip (déterministe, pas de LLM)

Drop des messages dont la valeur informationnelle est nulle pour la
suite :

- Anciens messages `role=user` synthétiques injectés par l'orchestrateur
  (nudges "persiste tes findings", notice budget) qui ont déjà été
  honorés (un workspace_write a suivi).
- Anciens turns `role=assistant` qui n'avaient pas de `tool_calls` ET
  pas de `content` substantiel (les "trous" de réflexion vides).
- Premiers tool results d'une suite répétée si une compaction ultérieure
  les a rendus redondants.

Coût : négligeable. Gain typique : 5–15 % du `WORKING`.

#### Niveau 2 — Microcompact (déterministe, pas de LLM)

Remplace le `content` des tool results massifs et **recomputables** par
un stub :

```json
{
  "role": "tool",
  "tool_name": "web_search",
  "content": "[MICROCOMPACTED] previously returned 4200 tokens. fingerprint=ab12cd. fully persisted at workspace/web-search-specialist_rust-comparison.md"
}
```

Critères de microcompaction :

- Le tool est dans la liste `_MICROCOMPACTABLE = {"web_search",
  "wikipedia_get_page", "workspace_view"}`.
- Le `content` du tool message dépasse un seuil de **~1500 tokens**
  (≈ 6000 caractères, ≈ 1000 mots — l'équivalent grossier d'une page
  pleine). Concrètement, ce seuil attrape :
  - une réponse `web_search` qui ramène 5+ résultats avec snippets
  - un `wikipedia_get_page` sur une section de plus de ~600 mots
  - un `workspace_view` sur un fichier de plus de ~120 lignes de code
    ou ~80 lignes de prose
- Le résultat existe sur disque (workspace ou cache de tool) et est
  identifiable par fingerprint — sinon on ne peut pas remplacer le
  contenu par un stub sans perdre l'information.

Le LLM voit que le tool a été appelé, voit la trace, mais ne traîne plus
le contenu plein. S'il en a besoin, il peut `workspace_view` ou réémettre
le tool (dé-dup hook l'attrapera et lui rendra le cache).

Coût : négligeable. Gain typique : 20–40 % du `WORKING` quand
applicable.

#### Niveau 3 — Context Collapse (appel LLM ciblé)

Sélectionne une fenêtre de messages au milieu (par exemple turns 5–15
sur 25) et la remplace par un résumé synthétique. Garde intacts :

- Le `system` initial (idx 0).
- Les 5 derniers turns (qui portent le contexte courant).
- Les `report_back` returns des subagents complétés (informations
  cristallisées qu'on ne veut pas perdre).

L'appel est un `gemma4:latest` court (~800 tokens output), prompt
ciblé sur la zone à résumer. Le résumé est inséré en `role=user`
préfixé `[ORCHESTRATOR CONTEXT COLLAPSE]`.

Coût : 1 appel LLM. Gain typique : 30–50 % du `WORKING`.

#### Niveau 4 — Autocompact (dernier recours)

Quand l'historique a continué à grossir malgré les niveaux 1-3, on
résume tout le milieu en une seule passe. Garde uniquement :

- Le `system` initial.
- Les 2-3 derniers turns absolus.
- Un seul `role=user` synthétique `[ORCHESTRATOR AUTOCOMPACT]` qui
  résume tout ce qui se trouvait entre.

C'est destructeur — on perd la granularité — mais ça garantit toujours
un appel LLM possible. Le système produit une réponse, dégradée si
nécessaire, plutôt que de planter.

Coût : 1 appel LLM long (~1500 tokens output). Gain : ramène le
`WORKING` autour de 20–30 % d'occupation.

### Choix du modèle pour les niveaux 3 et 4

Compactor = `config.COMPACTOR_MODEL` (défaut : **`gemma4:latest`**).
Override par env `JEANMICHEL_COMPACTOR_MODEL`. Choix du défaut justifié :
la fidélité du résumé prime sur la latence — un résumé qui perd les
pointeurs vers les workspace files ou inverse une conclusion serait
pire qu'un résumé un peu plus lent.

### Décision sur le `role` du message compacté

Le résumé inséré aux niveaux 3 et 4 prend `role=user` préfixé par un
marqueur (`[ORCHESTRATOR CONTEXT COLLAPSE]` ou `[ORCHESTRATOR AUTOCOMPACT]`),
plutôt qu'un second `role=system`. Raison : avoir deux `role=system` est
non-standard et certains modèles le rejettent. Un `role=user` préfixé
est universellement accepté.

### Garantie de production de réponse

Même si l'autocompact échoue (timeout LLM, output corrompu), l'orchestrateur
force un dernier appel `chat()` avec un `messages[]` minimal :
`[system + last 2 turns + "[ORCHESTRATOR] Produis la meilleure réponse
possible avec ce contexte tronqué"]`. Le système rend toujours quelque
chose, quitte à signaler la dégradation à l'humain.

### `PreToolUse(call, state) -> Decision`

Retourne un objet `Decision(deny: bool, reason: str | None)`.

Vérifications :

- **Grant** : si `call.name` n'est pas dans `agent_tools` pour l'agent
  courant, deny.
- **Dédup contextualisée** : fingerprint = `(call.name, normalize(call.args), caller_agent_code, depth)`.
  Le scope du cache est **l'appel LLM courant** (le `messages[]` d'un
  agent donné à un niveau donné) — un sibling ou un sub-subagent à un
  autre niveau peut légitimement refaire le même call avec son propre
  contexte. Cette dédup contextualisée corrige le faux positif global
  actuel ([04 §3](DevNotes/REVOLUCION/04_audit_complementaire.md)).
- **Profondeur (pour `delegate_to`)** : deny si `state.depth_current + 1 > MAX_DEPTH`.
- **Budget recherche** : deny si `call.name in {"web_search", "wikipedia_*"}`
  et `state.search_calls_total >= MAX_SEARCH`. Le compteur est incrémenté
  dans `PostToolUse`.

### `PostToolUse(call, result, messages, state) -> None`

Effets de bord :

- Incrémente `state.search_calls_total` et `state.search_calls_since_last_persist`
  si pertinent.
- Si `call.name` est un workspace_write, reset `search_calls_since_last_persist = 0`.
- Si `state.search_calls_since_last_persist > 3`, append un message
  `role=user` synthétique avant le prochain LLM call : `[ORCHESTRATOR]
  Tu as fait N recherches sans écrire dans le workspace. Persiste tes
  findings via workspace_create_file ou workspace_append avant de
  continuer.` Ce nudge ne mute pas le system prompt et ne casse pas le
  protocole.
- Pour `bash_sandbox` : append d'une ligne dans
  `~/.jean-michel/sandbox_audit.jsonl` (cf. §6 bis). Pour les autres
  tools : émission d'un event `ToolCallCompleted` (cf. §6 bis), pas
  d'écriture BDD.

### `OnDelegateReturn(parent_messages, sub_result, state) -> None`

Effet : push le dict retourné par le subagent comme `role=tool` dans
`parent_messages`. Met à jour `state.active_subagent = None`. Persiste le
`sub_messages[]` complet dans le fichier `subagent_<request_id>.json`.

Ce hook n'est pas un "garde-fou" — c'est la mécanique propre du
`delegate_to`. Listé ici pour exhaustivité.

## 8. Garde-fous (§G)

| Garde-fou           | Implémentation                                       | Effet à dépassement                                                       |
|---------------------|------------------------------------------------------|---------------------------------------------------------------------------|
| Budget partitionné  | hook `PreLLMCall`, escalade Snip → Microcompact → Collapse → Autocompact | compaction au niveau adapté, garantie de réponse même dégradée            |
| `MAX_DEPTH = 5`     | hook `PreToolUse(delegate_to)`                       | refus de la délégation, erreur structurée                                 |
| `MAX_SEARCH = 10`   | hook `PreToolUse(web_search)`, compteur turn-wide    | refus des recherches additionnelles                                       |
| Wall-clock 900 s    | timer en tâche de fond, scope turn humain            | abort + sauvegarde `messages.json` + `events.jsonl` + état partiel        |

Le wall-clock est un filet de sécurité technique (Ollama hang, freeze
réseau), pas un levier fonctionnel. Les 4 autres budgets actuels
(`MAX_STEPS_PER_REQUEST`, `MAX_DELEGATIONS`, `MAX_SEARCH_CALLS_PER_REQUEST`,
`SOFT_DEADLINE_RATIO`) sont supprimés ou absorbés par le modèle de
budget partitionné + l'escalade de compaction.

### Précision sur le sens de "budget"

Le budget de contexte est **partitionné** par appel LLM en trois zones
fixes : `SYSTEM_RESERVE` (system prompt + tools, mesuré au démarrage) +
`WORKING` (le `messages[]` accumulé) + `OUTPUT_RESERVE` (15 % du
contexte total, garantit que la réponse finale ne soit jamais tronquée).
Quand le `WORKING` se sature (paliers 70/80/90/95 %), le hook
`PreLLMCall` déclenche l'escalade de compaction décrite en §7 jusqu'à
ramener l'usage sous le seuil. Si même l'autocompact ne suffit pas, la
garantie de réponse dégradée prend le relais.

Ce n'est PAS un budget de VRAM. La VRAM disponible sur la machine
détermine quels modèles peuvent être chargés en parallèle dans Ollama,
mais ne se "dépense" pas tour par tour — elle est constante tant que les
modèles sont en `keep_alive`. Sur la machine de référence du projet
(2x NVIDIA GV100, soit environ 64 Go VRAM cumulés), les configurations
viables incluent :

- `granite4.1:8b` (~5.3 Go) **plus** `gemma4:latest` (~9.6 Go) tous deux
  warm en parallèle, ~15 Go au total, le reste libre pour les KV caches
  et le contexte étendu. C'est la configuration recommandée pour la v2 :
  les deux modèles répondent sans rechargement et le dispatch est
  réellement < 1s.
- `gemma4:26b` (~17 Go) sharded sur les 2 cartes, mais alors granite ne
  rentre plus en parallèle. Configuration secondaire (à utiliser quand
  on accepte une latence dispatch plus haute en échange d'un main agent
  plus capable).

Le choix de configuration est un paramètre de déploiement, pas une
variable du runtime — il influence les modèles que `llm.chat(model=...)`
peut appeler instantanément mais pas la sémantique de la boucle.

## 9. Ce qui disparaît (§H)

Par catégorie.

**Fichiers d'état** :
- `plan.md` comme source d'état (devient rendu lisible à la demande).
- `todo.json` comme fichier d'état (la fonction d'affichage CLI est
  reprise par le flux d'events ; voir §6 bis).

**Tables BDD runtime** :
- `requests` — l'arborescence d'exécution vit dans `messages.json` +
  `subagent_<id>.json`.
- `artifacts` — le filesystem per-conv est l'index.
- `sandbox_executions` — remplacée par `~/.jean-michel/sandbox_audit.jsonl`
  global.
- `conversation_phases` — le concept de phase est retiré (cf.
  [04 §6](DevNotes/REVOLUCION/04_audit_complementaire.md)).

**Tools** :
- `set_task_class`, `manage_todo_list`, `signal_convergence`,
  `report_findings` (le main agent ne l'utilise plus ; les subagents
  utilisent `report_back` qui en est le successeur renommé),
  `planner_done`, `gather_done`, `critic_done`, `build_done`,
  `return_to_user` (implicite via arrêt de la boucle).

**Mécanique orchestrator.py** :
- `running_user_text` reconstruit à chaque itération.
- `render_plan_recap` injecté en user message.
- Les 5 gates inlines (`soft deadline`, `search budget`, `deep-research
  guard`, `classify_first`, `plan_first_required`) — remplacés par les
  4 hooks Python uniformes et les 4 garde-fous (budget partitionné,
  `MAX_DEPTH`, `MAX_SEARCH` turn-wide, wall-clock filet).

**Budgets** :
- `MAX_STEPS_PER_REQUEST` (+ bonus écriture workspace) — supprimé, fondu
  dans le budget tokens.
- `MAX_DELEGATIONS` — supprimé, contraint par budget + profondeur.
- `SOFT_DEADLINE_RATIO` — supprimé, c'était un proxy du budget tokens.

**Migrations BDD correctives** :
- 057 (write grant comparator + contrat sortie hardcodé), 058 (paradigm
  `planning_with_todos` MUST), 059 (MUST + <think>), 060, 061 (search
  budget gate). Une migration de réécriture remplacera la cascade.

**Paradigmes** :
- `convergence_gate` (id 100), `research_phase_routing` (id 102),
  `planning_with_todos` (id ~108), `classify_first_required`, et tout
  paradigme du même type "anti-loop incantatoire". À auditer un par un en
  phase d'implémentation.

**Agent `archivist`** :
- Devenait inutile dès lors que `messages.json` porte l'historique natif.
  À retirer en phase de migration, après vérification que `summary.md`
  généré à la demande couvre les besoins de lecture humaine.

## 10. Mémoire long-terme utilisateur (§I)

Évolution de l'actuel `user_profile.toml` statique vers une mémoire BDD
structurée, inspirée du système `MEMORY.md` de Claude Code.

### Schéma BDD

```sql
CREATE TABLE user_memory (
  id           INTEGER PRIMARY KEY,
  type         TEXT NOT NULL CHECK (type IN ('user','feedback','project','reference')),
  code         TEXT NOT NULL,
  title        TEXT NOT NULL,
  description  TEXT NOT NULL,    -- une ligne, injectée dans l'index du prompt
  content      TEXT NOT NULL,    -- corps markdown, chargé sur demande
  created_at   TEXT NOT NULL,
  modified_at  TEXT NOT NULL,
  UNIQUE (type, code)
);
CREATE INDEX idx_user_memory_type ON user_memory(type);
```

Le champ `code` est un slug kebab-case (par exemple `kiss-religieux`,
`unity-montreal`, `revolucion-branch`). La paire `(type, code)` est unique.

### Tool exposé : un seul, multi-action

```
manage_user_memory(
  action: "save" | "recall" | "list" | "update" | "delete",
  type:        str  | None,   # required for save / list / delete with type filter
  code:        str  | None,   # required for recall / update / delete
  title:       str  | None,   # required for save / update
  description: str  | None,   # required for save / update
  content:     str  | None,   # required for save / update
) -> JSON
```

Choix d'un tool unique avec un paramètre `action` plutôt que cinq tools
séparés : moins de surface API, paradigm `tool_discipline` plus simple,
et c'est le même pattern que `manage_todo_list` actuel donc consistance
interne. Un tool unique = un grant unique dans `agent_tools`.

### Grants

Une seule ligne `agent_tools(jean-michel, 'manage_user_memory')`. Aucun
autre agent ne touche la mémoire utilisateur dans la v2. (Si on ajoute un
agent dédié `memory-curator` plus tard, il viendra à ce moment-là —
out-of-scope ici.)

### Injection dans le prompt

Le rendu du `## Human` block dans [prompts.py L.518](src/jeanmichel/prompts.py#L518)
prepend l'**index** (type + code + description) de toutes les entrées de
`user_memory`, jamais le contenu complet :

```
## Known facts about the user (long-term memory)
- [user]     unity-montreal       : dev senior Unity Montréal, francophone
- [feedback] kiss-religieux       : pas de cache-misère, K.I.S.S, brutal truth
- [project]  revolucion-branch    : refonte arch Jean-Michel, branche revolucion

Use manage_user_memory(action="recall", code="<code>") to load the full
content of an entry. Use manage_user_memory(action="save", ...) to add a
new fact or manage_user_memory(action="update", ...) to refine one.
```

Le LLM voit l'index à chaque tour (système prompt re-rendu). Il appelle
`recall` quand il a besoin du contenu plein d'une entrée. Il appelle
`save` ou `update` quand l'humain révèle un fait durable.

Cet index est limité à `100` entrées affichées (les plus récemment
modifiées) pour éviter de gonfler le system prompt indéfiniment. Au-delà,
le LLM doit appeler `manage_user_memory(action="list", ...)` pour voir le
reste. Un warning est émis (event `MemoryNearCapacity`, rendu dans le CLI
et dans `## Known facts` du prompt) dès que le compteur atteint `90`
entrées, pour signaler qu'il est temps de purger les entrées obsolètes
via `delete` ou `update`.

### Discipline (paradigme dédié)

Un seul paradigme nouveau, attaché à jean-michel :

```
code: user_memory_discipline
content:
  - Save a user_memory entry when the human reveals a durable fact about
    themselves, their preferences, their projects, or their workflows.
  - Update an existing entry when a previously saved fact is contradicted
    or refined by the conversation.
  - Delete an entry that has become irrelevant (e.g. mention of an
    abandoned project, a corrected preference).
  - Recall the full content of an entry when the current conversation
    references something that might be in memory.
  - Keep entries concise: title under 60 chars, description under 150
    chars, content under 1000 chars.
```

Pas de cascade de MUST. Si le LLM s'en sert mal, on itère sur le paradigme
ou on ajoute un `PostToolUse` hook qui propose une sauvegarde à la fin
d'un tour humain ("the user just said X about themselves, save it?"). Pas
de hook préemptif tant qu'on n'a pas observé le besoin.

### Réponse à la question du doc 03 sur les tables `requests` / `parent`

**Supprimées** (cf. analyse §6 "Tables BDD — lesquelles garder, lesquelles
supprimer"). L'arborescence parent/enfant qu'elles portaient est
intégralement reconstituable depuis le filesystem per-conversation :

- `messages.json` (main agent) + `subagent_<request_id>.json` (un par
  subagent spawné) portent l'historique complet de chaque appel LLM
  dans l'arbre.
- `events.jsonl` (§6 bis) porte la séquence d'events typés —
  `DelegationStarted` / `DelegationCompleted` permettent de
  reconstruire l'arbre en quelques lignes Python ou avec `jq`.
- Les métriques cross-conversation (taux de délégation, distribution
  de profondeur, durée par tool, etc.) s'obtiennent via scan de
  `conversations/*/events.jsonl` — un script ad-hoc, pas une requête
  SQL.

Ce qui reste en BDD (cf. §11) : `conversations` (métadonnées + listing
rapide), les seeds (paradigms / agents / grants), et la nouvelle
`user_memory`. Tout le reste vit dans le filesystem.

## 11. Ce qui survit (§J)

### Tables BDD conservées

| Table                       | Rôle dans la v2                                          |
|-----------------------------|----------------------------------------------------------|
| `conversations`             | métadonnées + statut, indispensable pour `--list-conv` et `--resume` |
| `sections`, `categories`, `paradigms`, `paradigm_modes` | seed du système de prompts |
| `agents`, `agent_paradigms`, `agent_tools`, `agent_workspace_grants`, `agent_sandbox_grants`, `agent_delegation_targets` | configuration des agents et de leurs grants |
| `user_memory` (nouvelle)    | mémoire long-terme cross-conversation                    |

`agent_delegation_targets` est désormais effectivement seedée
(cf. [04 §5](DevNotes/REVOLUCION/04_audit_complementaire.md)).

### Code et infrastructure

- **Système de paradigmes** + composition par agent : la mécanique de
  rendu est bonne. On purge uniquement les paradigmes anti-loop devenus
  inutiles (la liste précise sera dans 07).
- **Workspace per-conversation** + quota 256 Mo + sandboxing path
  traversal : inchangé.
- **Sandbox Docker** + `bash_sandbox` + `agent_sandbox_grants` : inchangé
  côté grant. L'audit basculé du SQL vers `~/.jean-michel/sandbox_audit.jsonl`.
- **MockClient** + le **pattern** de test pytest : à adapter au nouveau
  format `messages[]`. Mais la suite des 274 tests existants n'est pas
  portée mécaniquement — voir stratégie ci-dessous.
- **Artefacts markdown** persistés sur disque + `inspect_conv` debug tool :
  conservés. `inspect_conv` doit être mis à jour pour lire `messages.json`
  au lieu d'interroger `requests` / `artifacts`.
- **`build_registry(conv_folder)`** comme pattern de DI pour les tools
  context-bound : inchangé.
- **`user_profile.toml`** : conservé en lecture seule, devient le bootstrap
  de la table `user_memory` au premier démarrage (script de seed qui le
  lit et crée une entrée `user / personal-profile`).

## 11 bis. Audit et nettoyage des paradigmes BDD

> Les paradigmes sont un pilier du système. Le meilleur orchestrateur du
> monde rendra des réponses inconsistantes si les paradigmes injectés
> dans les prompts contredisent le processus, référencent des outils
> supprimés, ou empilent des MUST en cascade pour compenser un défaut
> structurel qu'on vient de corriger ailleurs. Cette section pose le
> cadre du nettoyage. Le tableau exhaustif paradigme-par-paradigme sera
> produit en première phase de 07.

### Pourquoi un nettoyage est non-négociable

Environ 100+ paradigmes existent en BDD (117 codes distincts capturés
à travers `db/schema.sql` + 50 migrations, après déduplication par
update). Une fraction non négligeable de ces paradigmes a été ajoutée en
réaction à un défaut de l'orchestrateur actuel — pas pour exprimer une
discipline de pensée. La v2 supprime ces défauts ; les paradigmes qui en
découlent doivent disparaître ou être réécrits, sinon le LLM se
retrouve avec des consignes contradictoires (par exemple : un
paradigme `convergence_gate` qui parle de `signal_convergence` alors
que ce tool n'existe plus).

### Catégorisation des paradigmes existants

Six classes d'utilité dans la v2. Chaque paradigme actuel sera étiqueté
dans l'une d'entre elles :

| Classe                       | Comportement attendu en v2                                | Exemples actuels                                                                 |
|------------------------------|-----------------------------------------------------------|----------------------------------------------------------------------------------|
| **A. Métacognition métier**  | Garder tel quel ou affiner                                | `truth_over_comfort`, `intellectual_humility`, `metacognitive_pause`, `belief_provenance`, `assumption_surface`, `slogan_resistance`, `reject_intellectual_laziness` |
| **B. Épistémie + biais**     | Garder. Cœur de la qualité de pensée                      | `spot_traps`, `confirmation_bias_check`, `fast_vs_slow_arbitrage`, `narrative_immunity`, `who_benefits`, `binary_resistance`, `framing_awareness` |
| **C. Style + communication** | Garder, vérifier compatibilité multi-mode                 | `no_speculation`, `no_filler`, `no_decoration`, `brutal_truth`, `warm_constructive_pushback`, `minimal_formatting`, `concise_output` (vocal) |
| **D. Tool discipline**       | Garder si non couvert par un hook ; sinon supprimer       | `weather_api_required`, `wikipedia_source_only`, `wikipedia_extract_focus`, `wikipedia_search_strategy`, `prefer_tool_over_parametric_for_volatile`, `verify_execution_output` |
| **E. Format de sortie**      | Garder, **réécrire** pour aligner sur la v2               | `archivist_format` (à retirer si archivist disparaît), `critical_thinker_format`, `structured_verdict`, `improvement_proposals_format`, `document_workspace_output` |
| **F. Anti-loop incantatoire**| **Supprimer**, remplacé par un hook Python                | `convergence_gate`, `research_phase_routing`, `planning_with_todos`, `briefing_contract` (à réécrire), `comparison_routing`, `meta_analysis_routing`, `task_plan_file`, `classify_first` (s'il existe en paradigme), `code_execution_routing` |

Les classes A, B et C sont la valeur durable du système : elles
définissent comment l'agent raisonne et s'exprime, indépendamment de la
mécanique de l'orchestrateur. La v2 ne touche pas à ces paradigmes (sauf
ajustements ponctuels).

La classe F est la dette. Chaque paradigme F a été ajouté en réaction à
un comportement observé, en espérant que le LLM le respecte. La v2 le
garantit côté code (hooks Python) — les paradigmes F deviennent du
bruit qui dilue le system prompt et peut entrer en contradiction avec le
comportement effectif.

### Critères de qualité d'un paradigme dans la v2

Un paradigme conservé dans la v2 doit satisfaire **les cinq critères**
suivants. Chacun est un test binaire pass/fail.

1. **Pas de référence à un outil supprimé.** Si un paradigme nomme
   `set_task_class`, `manage_todo_list`, `signal_convergence`,
   `report_findings` (au sens main agent), `planner_done`,
   `gather_done`, `critic_done`, `build_done`, ou `return_to_user`,
   il est à supprimer ou réécrire.
2. **Pas de MUST en cascade.** Un paradigme doit énoncer un principe ou
   une discipline, pas une règle conditionnelle "IF X THEN Y" qu'on
   espère voir respectée. Les règles conditionnelles vont dans les hooks.
3. **Indépendant de la mécanique orchestrateur.** Un paradigme parle de
   *comment penser ou écrire*, pas de *comment naviguer le pipeline*.
   Conséquence : la mention de "phase GATHER / CRITIQUE / BUILD",
   "research_phase_routing", "planner_done", "convergence" disparaît.
4. **Concis.** 3 à 6 bullets maximum, chaque bullet une seule idée.
   Au-delà, le paradigme devient illisible pour le LLM et risque de
   contredire d'autres paradigmes voisins.
5. **Effet observable sur la qualité de la réponse.** Si retirer le
   paradigme ne change rien à la qualité de sortie d'un agent sur ses
   cas typiques, il n'a pas sa place. Critère testable indirectement
   via A/B sur cas représentatifs (cf. phase de validation en 07).

### Paradigmes nouveaux à introduire

| Code                              | Cible                          | Rôle                                                                                                             |
|-----------------------------------|--------------------------------|------------------------------------------------------------------------------------------------------------------|
| `user_memory_discipline`          | jean-michel (router)           | Quand save / update / delete / recall une entrée de mémoire utilisateur (cf. §10)                                |
| `nested_delegation_discipline`    | tous agents avec `delegate_to` | Préciser que la délégation peut descendre dans l'arbre, et **doit** descendre si la sous-tâche dépasse le scope. Ne PAS remonter au parent. |
| `report_back_format`              | tous specialists               | Comment remplir `summary`, `confidence`, `low_confidence_reason`. Privilégier la phrase synthétique.             |
| `workspace_progressive_write`     | tous specialists workspace-write | Reformulation positive du `workspace_as_shared_memory` (id 103) : écrire ses findings au fur et à mesure ; le hook `PostToolUse` reste filet. |
| `output_contract_no_inline_dump`  | jean-michel + finalizer        | La réponse à l'humain doit être prose construite, pas un dump des tool results.                                  |

Le paradigme `concise_output` (id 34) existant reste utilisé pour le
mode `vocal`. Il sert d'output contract concis indirectement réutilisé
par le TTS pipeline (cf. §12 mode vocal).

### Processus de nettoyage — sortie attendue

Production d'un **tableau exhaustif** (livrable du début de 07) :

| paradigm_id | code | classe (A-F) | décision | nouveau contenu si réécriture | justification |
|-------------|------|---------------|----------|-------------------------------|----------------|

Pour chaque paradigme actuel :

1. **Lecture** du `content` actuel + du `rationale` du seed.
2. **Classement** dans la grille A-F.
3. **Décision** parmi : `keep`, `keep-with-edits`, `rewrite`, `delete`, `merge-into-X`.
4. **Justification** en 1-2 lignes (référence aux 5 critères ci-dessus).
5. **Si réécriture** : proposition de nouveau content (3-6 bullets).

À cela s'ajoutent les paradigmes nouveaux (tableau précédent) avec leur
contenu rédigé.

### Migration BDD résultante

Une migration unique de nettoyage (proposition : `migrate_100_paradigm_realignment.sql`
ou similaire selon le numéro qui suit l'existant) qui :

- `DELETE` des paradigmes obsolètes (classe F + ceux qui échouent un des
  5 critères sans pouvoir être réécrits).
- `UPDATE` des paradigmes réécrits.
- `INSERT` des nouveaux paradigmes (classe v2 décrite ci-dessus).
- `DELETE FROM agent_paradigms` pour les bindings devenus orphelins.
- `INSERT INTO agent_paradigms` pour les nouveaux bindings.
- Vérification que chaque agent garde au moins ses paradigmes
  fondateurs (épistémie + communication + role-specific output contract).

Tests à ajouter :
- Un test par agent qui rend son system prompt complet et vérifie
  l'absence de mots-clés interdits (`set_task_class`, `manage_todo_list`,
  `signal_convergence`, etc.).
- Un test snapshot du nombre de paradigmes par agent (régression si on
  vide accidentellement un agent).
- Un test d'unicité : pas deux paradigmes actifs qui se contredisent
  pour le même agent dans le même mode.

### Place dans le phasage

Cette analyse + nettoyage est la **première phase concrète** du plan
d'implémentation (à formaliser en `07_plan_implementation.md`). Sans
elle, toute amélioration de l'orchestrateur Python sera diluée par des
paradigmes qui parlent de l'ancien monde.

L'ordre logique en 07 sera donc :

1. **Phase 0** : production du tableau exhaustif paradigme-par-paradigme
   + rédaction de la migration de nettoyage. **Sans exécution** — on
   reste en projet.
2. **Phase 1+** : implémentation côté Python (LLMClient multi-turn,
   hooks, dispatcher tier 0, etc.).
3. **Phase de bascule** : appliquer la migration BDD et bascule du code.

Détailler dans 07.

## 11 ter. Wrappers et CLI

Les deux points d'entrée utilisateur du système — le script bash
[jm.sh](jm.sh) et l'interface Python [src/jeanmichel/cli.py](src/jeanmichel/cli.py)
— doivent être révisés en cohérence avec la v2. Sans ça, les premiers
tours après bascule renverront des erreurs d'import ou afficheront des
events orphelins.

### 11 ter A. `jm.sh` — interventions nécessaires

`jm.sh` est un wrapper bash thin qui dispatche vers des modules Python.
Sa structure générale (venv, sous-commandes, pass-through CLI) reste
valide. Interventions ciblées :

| Sous-commande           | Statut v2  | Intervention                                                                                                       |
|-------------------------|------------|--------------------------------------------------------------------------------------------------------------------|
| `(défaut)` → CLI        | conservée  | Aucune modification du wrapper. La CLI Python en interne change (cf. §11 ter B).                                   |
| `--install`             | conservée  | Charge `db/schema.sql` consolidé — celui-ci doit refléter le schéma v2 (tables purgées + `user_memory` ajoutée).   |
| `--test`                | conservée  | Pointe sur la nouvelle suite (cf. décision tests en §12). Pas de changement du wrapper.                            |
| `--build-docker`        | conservée  | Aucun changement — les Dockerfiles sandbox sont inchangés.                                                         |
| `--export-db`           | conservée  | Plus rapide après v2 (DB plus petite). Aucun changement de signature.                                              |
| `--browse-db`           | conservée  | Idem.                                                                                                              |
| `--paradigm-matrix`     | conservée  | Outil de visualisation reste pertinent (paradigmes survivent en BDD).                                              |
| `--admin`               | conservée  | Le REPL gère agents/tools/paradigmes. Doit pouvoir éditer la nouvelle table `user_memory` aussi (extension).       |
| `--inspect-conv`        | **à refondre** | Lit actuellement les tables `requests` / `artifacts` qu'on supprime. Doit lire `messages.json` + `events.jsonl` + `subagent_*.json` du dossier conversation. |
| `--clean`               | conservée  | Devient plus simple (moins de tables à `DELETE`). Vérifier que `clean_convs.py` ne référence plus les tables virées.|
| `--meta-analysis`       | **à raffraichir** | Le prompt actuel mentionne `self_inspect(scope=...)` — or les tools split en `self_inspect_config`/`activity`/`architecture` depuis la migration 015. Bug pré-existant à corriger au passage. |

Pas de nouvelle sous-commande introduite par la v2 a priori. L'éventuel
`--rebuild-paradigms` (pour appliquer la migration de nettoyage) peut
rester une commande SQL ad-hoc lancée manuellement, pas une sous-commande
permanente.

### 11 ter B. `cli.py` — interventions nécessaires

`cli.py` consomme le flux d'events de l'orchestrateur via le générateur
`orch.run()`. Le pattern de rendu (rich Panels + Rules + spinner) est
bon et reste. Ce qui change : la liste des events importés en haut de
fichier, et le mapping event → rendu.

**Events à retirer des imports** (n'existent plus en v2) :

- `TodoListUpdated` — `todo.json` disparaît ; l'arbre des delegations
  remplace cette visualisation.
- `SignalConvergenceRedirected` — `signal_convergence` est déjà
  déprécié, devient mort.
- `SoftDeadlineReached` — soft deadline supprimée (un seul wall-clock
  reste comme safety net).
- `ForcedConvergence` — remplacé par la dégradation gracieuse de
  l'autocompact (§7 niveau 4).
- `ReportFindingsReceived` — l'évènement de complétion de subagent
  devient `DelegationCompleted` (cf. §6 bis).
- `SummaryUpdated` — l'archivist disparaît avec la persistence native
  `messages.json`.

**Events à ajouter** (déclarés en §6 bis) :

- `RequestStarted`, `RequestCompleted`
- `LLMCallStarted`, `LLMCallCompleted`
- `ToolCallStarted`, `ToolCallCompleted` (renomment l'actuel `ToolCallEmitted` + `ToolResponseRecorded`)
- `DelegationCompleted` (le pendant de `DelegationStarted`)
- `HookFired` (refus, compaction, force-persist)
- `WorkingBudgetUpdate` (franchissement d'un seuil de compaction 70/80/90/95 % du `WORKING`)
- `MemoryNearCapacity` (warning `user_memory` à 90 entrées, cf. §10)

**Refonte du `_render_todo_panel`** : la mécanique de Panel rich est
réutilisable. Renommer en `_render_delegation_tree` et l'alimenter
depuis le couple `DelegationStarted` / `DelegationCompleted` plutôt
que depuis `TodoListUpdated`. La structure visuelle (icônes selon
status, indent par profondeur, file count, confidence) est compatible
avec les besoins de l'arbre des delegations.

**Pré-warm `_prewarm`** : doit warmer **deux** modèles en v2 :
`granite4.1:8b` (Tier 0) et `gemma4:latest` (Tier 1+2). Si l'un échoue,
log un warning mais continuer — granite peut être absent, on fallback
sur DEEP direct.

**Argument `--model`** : sa sémantique change. Aujourd'hui, c'est le
modèle unique passé à `OllamaClient()`. En v2, il faut au moins deux
flags :

- `--main-model` (défaut : `gemma4:latest`) — modèle du Tier 1 / 2 / compactor.
- `--dispatch-model` (défaut : `granite4.1:8b`) — modèle du Tier 0.

`--model` peut être conservé en alias deprecated qui fixe `--main-model`.

**Argument `--mode vocal`** : sa sémantique évolue (cf. §12). Reste un
choix valide d'argument, mais l'effet est différent : injecte le
paradigme `concise_output` dans le prompt + active un branchement TTS
sur les ALEXA outputs. Le branchement TTS lui-même est une couche
indépendante (out of scope CLI).

**`--resume`** : `Orchestrator.resume_conversation()` doit recharger
`messages.json` + `state.json` du dossier au lieu de reconstituer
l'historique depuis les tables `requests` / `artifacts`.

**`--list-conv`** : utilise `db.list_active_conversations(conn)` qui
lit la table `conversations` — survit en v2 sans changement.

**`--once`** : conservé tel quel pour les usages programmatiques
(`jm.sh --meta-analysis` etc.).

### 11 ter C. Contrat orchestrateur ↔ CLI

Le contrat à formaliser entre `orchestrator.py` et `cli.py` est le
**catalogue des events typés** émis par `orch.run()`. Proposition :
extraire ces events dataclass dans un module dédié
`src/jeanmichel/events.py` (au lieu d'être déclarés dans `orchestrator.py`),
pour :

- découpler le générateur d'events de leur typage (imports plus propres
  côté CLI),
- permettre un test unitaire de chaque event,
- faciliter la version sérialisée (vers `events.jsonl` §6 bis).

Le CLI ne devrait jamais avoir à introspecter le `messages.json` —
toute l'information dont il a besoin lui arrive via le flux events.

### 11 ter D. Place dans le phasage

Ces interventions sont mécaniques mais étendent la surface des changements.
Elles arrivent **dans la phase d'écriture du nouvel orchestrateur** (pas
en phase 0 nettoyage paradigmes). Détail exact dans 07 — probablement :

- L'extraction `events.py` se fait en même temps que la réécriture de
  `orchestrator.py`.
- L'adaptation `cli.py` suit immédiatement (avant les premiers tests
  end-to-end).
- L'adaptation `jm.sh` (`--inspect-conv` notamment) peut être traitée en
  parallèle puisque c'est un script bash thin.

## 12. Décisions tranchées vs questions ouvertes (§K)

### Tranché dans ce doc

- **Pivot architectural** : Claude-Code-style avec `messages[]` multi-turn
  natif Ollama. Le LLM voit son histoire complète.
- **Modèles configurables, 4 slots nommés** dans `config.py` :
  - `DISPATCH_MODEL` (défaut `granite4.1:8b`) — Tier 0.
  - `MAIN_MODEL` (défaut `gemma4:latest`) — Tier 1 main agent.
  - `COMPACTOR_MODEL` (défaut `gemma4:latest`) — compactor niveaux 3 et 4.
  - `SUBAGENT_DEFAULT_MODEL` (défaut `gemma4:latest`) — subagents
    Tier 2 sans override.
  Chaîne d'override : CLI flag > env var (`JEANMICHEL_*_MODEL`) >
  `config.py` default > colonne BDD `agents.model_override` (per-agent,
  uniquement pour les subagents). Aucun modèle hardcoded dans le code.
- **4 hooks Python** : `PreLLMCall`, `PreToolUse`, `PostToolUse`,
  `OnDelegateReturn`. Aucun MUST en cascade dans les prompts.
- **Garde-fous unifiés** : budget de contexte partitionné par appel
  (SYSTEM/WORKING/OUTPUT, escalade compaction sur WORKING) + `MAX_DEPTH=5`
  + `MAX_SEARCH=10` turn-wide + wall-clock filet 900 s. Les 7 budgets
  actuels sont remplacés.
- **État canonique runtime** : `messages.json` + `state.json`
  per-conversation. La BDD ne porte plus d'état runtime.
- **Tables BDD virées** : `requests`, `artifacts`, `conversation_phases`,
  `sandbox_executions`. Remplacées par fichiers per-conv +
  `~/.jean-michel/sandbox_audit.jsonl` global.
- **Tables BDD gardées** : `conversations` + seeds (paradigms / agents /
  grants) + nouvelle `user_memory`.
- **Délégation imbriquée** : un subagent peut appeler `delegate_to` lui-même
  (un sub-subagent est spawné depuis le subagent, pas depuis le main).
  L'orchestrateur Python centralise les spawns donc reste informé de tout
  l'arbre (via `spawn_subagent()` + events). `MAX_DEPTH=5`, `MAX_SEARCH=10`
  turn-wide et wall-clock cadrent l'explosion ; pas de budget hérité entre
  niveaux.
- **`report_back` du subagent** : 4 champs, `low_confidence_reason`
  obligatoire si `confidence == "low"` (une phrase synthétique). Le parent
  est informé du pourquoi du faible indice de confiance, pas du
  raisonnement complet.
- **`user_memory`** : un tool unique `manage_user_memory(action, ...)`,
  index injecté dans system prompt (limite 100 entrées affichées, warning
  à 90), grant restreint à jean-michel.
- **CLI rendering + journal d'événements** : flux d'events typés émis
  par l'orchestrateur, consommés par le CLI live ET persistés en
  `conversations/<id>/events.jsonl` (append-only JSONL). C'est cet
  artefact qui permet la reconstruction de l'arbre de délégation, le
  replay post-crash CLI, et les métriques cross-conv.
- **Mode `vocal`** : pipeline TTS branché en sortie (principalement sur
  les réponses ALEXA du Tier 0). La verbosité réduite est appliquée par
  un paradigme dans le prompt (cf. paradigme existant `concise_output`
  id 34). `mode` reste un champ de la table `conversations`. Le
  branchement TTS lui-même est un détail d'output pipeline, hors scope
  orchestrateur.
- **Tools retirés** : `set_task_class`, `manage_todo_list`,
  `signal_convergence`, `report_findings`, `planner_done`, `gather_done`,
  `critic_done`, `build_done`, `return_to_user` (implicite via arrêt
  de boucle).
- **Agent `archivist`** : devient inutile, retrait en phase de migration.
- **Audit + nettoyage des paradigmes BDD** (§11 bis) : livrable
  prioritaire de la phase 0 du plan d'implémentation. Tableau exhaustif
  paradigme-par-paradigme (~100 entrées) avec décision keep / rewrite /
  delete, plus 5 nouveaux paradigmes à introduire. Migration unique
  `migrate_100_paradigm_realignment.sql`.
- **Stratégie de tests** : nouvelle suite construite from scratch contre
  la v2, qui sert de checkpoint de progression à chaque phase. Les 274
  tests legacy ne sont **pas** portés en bloc — ils sont cueillis
  individuellement (et adaptés) quand un test legacy couvre un
  comportement v2 qu'on veut garantir. La nouvelle suite couvre, à
  minima : Dispatcher Tier 0 (parse JSON, ALEXA / DEEP routing,
  fallback), boucle multi-turn avec messages[] natif, délégation
  imbriquée 1→2→3 niveaux, escalade de compaction 4 niveaux, hooks
  (deny grant, dédup, force-persist), tool `manage_user_memory`,
  `report_back` avec `low_confidence_reason`, et un snapshot de prompt
  par agent vérifiant l'absence de mots-clés interdits (cf. tests §11 bis).
- **Seuils de compaction** : 70 / 80 / 90 / 95 % du `WORKING` pour
  l'escalade Snip → Microcompact → Collapse → Autocompact (§7). Valeurs
  initiales calquées sur Copilot CLI, validées comme défauts de
  démarrage.
- **`OUTPUT_RESERVE` = 15 %** du contexte total. Délibérément bas par
  rapport à Copilot (25-30 %) parce que la v2 force l'écriture
  progressive des outputs longs vers le workspace via le paradigme
  `workspace_progressive_write` — la réponse finale est toujours un
  résumé court avec pointeurs vers les fichiers produits.
- **Seuil de microcompaction = 1500 tokens** (≈ 6000 chars, ≈ une page
  pleine). Tool result au-dessus de ce seuil = remplacé par stub si
  recomputable depuis disque.
- **Tous les paramètres tunables sont exposés dans `config.py`**. Aucun
  threshold ni modèle hardcoded ailleurs dans le code. Liste minimale :
  - Seuils numériques :
    `COMPACTION_THRESHOLDS = (0.70, 0.80, 0.90, 0.95)`,
    `OUTPUT_RESERVE_RATIO = 0.15`,
    `MICROCOMPACT_TOKEN_THRESHOLD = 1500`, `MAX_DEPTH = 5`,
    `MAX_SEARCH_CALLS_PER_TURN = 10`, `WALL_CLOCK_TURN_SECONDS = 900`,
    `USER_MEMORY_INDEX_LIMIT = 100`, `USER_MEMORY_WARN_AT = 90`.
  - Slots de modèles : `DISPATCH_MODEL`, `MAIN_MODEL`, `COMPACTOR_MODEL`,
    `SUBAGENT_DEFAULT_MODEL` (défauts détaillés dans la bullet "Modèles"
    ci-dessus). Override par env var et CLI flag.
  - Per-agent : colonne `agents.model_override` (NULL = utiliser
    `SUBAGENT_DEFAULT_MODEL`).
  Tunables sans redéploiement ni recompile.

### Reste à trancher en phase d'implémentation

Toutes les questions architecturales sont tranchées. Toutes les valeurs
numériques sont également posées avec leurs valeurs de démarrage —
chacune exposée dans `config.py` pour tuning a posteriori sans
recompilation (cf. liste Tranché ci-dessus).

L'architecture est figée pour le passage au plan d'implémentation 07.


## 13. Critères de validation de l'architecture

Avant de passer au plan d'implémentation (`07_*`), valider que cette
architecture répond à chacun des 10 défauts listés dans
[01_audit_orchestrateur.md](DevNotes/REVOLUCION/01_audit_orchestrateur.md) :

| Défaut 01                                          | Réponse v2                                              |
|----------------------------------------------------|---------------------------------------------------------|
| 1. "State machine" en nom seulement                | Boucle minimale + 4 hooks ; pas de state machine prétendue |
| 2. Un seul modèle thinking pour tout               | 3 modèles ; dispatch Python                             |
| 3. Pas de mémoire conversationnelle native         | `messages[]` multi-turn Ollama natif                    |
| 4. 7 budgets orthogonaux                           | Budget de contexte partitionné par appel (SYSTEM/WORKING/OUTPUT) + MAX_DEPTH + MAX_SEARCH turn-wide + wall-clock |
| 5. Récursion horizontale non contrainte            | MAX_SEARCH turn-wide + MAX_DEPTH + wall-clock + whitelist `agent_delegation_targets` ; pas de budget hérité entre niveaux |
| 6. plan.md vs todo.json                            | Les deux disparaissent comme sources d'état             |
| 7. System prompt immuable                          | Re-rendu en début de chaque tour humain, intègre l'index `user_memory` à jour (les saves d'un tour deviennent visibles au tour suivant) |
| 8. Méga-loop monolithique                          | `run_main_loop` ≤ 50 lignes ; hooks isolés et testés    |
| 9. Cycle de désespoir git                          | Réécriture nette, pas patch ; phasage en 07             |
| 10. Top 7 défauts                                  | Voir entrées 1-9 ci-dessus + dispatch tier 0            |

Si une case ne tient pas la critique en relecture, on itère ici avant de
toucher au plan d'implémentation.

## 14. Prochaine étape

Relecture commune de ce document. Itérations possibles sur chaque section.
Une fois l'architecture validée (au sens : on est d'accord qu'elle répond
aux défauts énumérés sans en introduire de nouveaux), passage à
`07_plan_implementation.md` qui phasera la migration depuis l'existant.
