# Rapport — Pièces détachées : ollamacode (boucle + tools) & E2B (sandbox)

> Mining ciblé de deux repos publics pour améliorer notre coding-agent (cf.
> [03_code_mode_and_sandbox_strategy.md](03_code_mode_and_sandbox_strategy.md)). Sources
> systématiquement citées. OpenHands : testé plus tard (noté §4). Date : 2026-06-01.

## 0. TL;DR — à prendre / à éviter
| Source | À PRENDRE | À ÉVITER |
|---|---|---|
| **ollamacode** | ergonomie des tools fichier (read/write/list/delete/mkdir granulaire) ; loop minimaliste comme référence de lisibilité | son **archi mono-agent SANS sandbox** (shell arbitraire + FS complet) = exactement le monolithe que tu rejettes |
| **E2B** | **un template/Dockerfile par langage** (confirmé) ; **pré-chauffage** (process déjà lancé → démarrage zéro) ; **auto-kill par timeout** | dépendre du cloud E2B (microVM Firecracker) — pas notre infra |
| **Notre code** (constat) | — | **fuite de conteneurs** : `docker run -d --rm` mais **aucun `stop`/reaper** → 1 conteneur/conv tourne indéfiniment |

## 1. ollamacode — boucle d'itération & tools
Repo : <https://github.com/128bytes8/ollamacode> · loop : <https://github.com/128bytes8/ollamacode/blob/master/main.py>

**La boucle (`run_agentic_loop`, ≤ 50 itérations)** :
- **Tool-calling natif Ollama** : `for chunk in client.chat(prompt, tools=tools, stream=True)`, tools via
  `tool_registry.get_tool_definitions()`. (≈ notre `chat_messages(tools=…)`.)
- **Historique cumulatif** : 1ʳᵉ itération = prompt user ; itérations suivantes = prompt **vide**
  (« the conversation history already contains everything »). **On fait pareil** (messages[] qui s'accumulent).
- **Parsing en streaming + dédup par tool-call ID** ; exécution via
  `tool_registry.execute_tool(name, args)` → résultat réinjecté par `client.add_tool_message(id, result, name)`.
- **Terminaison** : aucun tool call → « Task completed! ». Garde `max_iterations = 50`.
- **Erreurs** : exception/KeyboardInterrupt → break, **aucun retry** (terminal).

**Tools (`ToolRegistry` dans `tools.py`)** : fichiers (`read_file`, `write_file`, `create_directory`,
`list_directory`, `delete_file`, `delete_directory`), terminal (`run_command` — shell brut), web
(`web_search` DuckDuckGo + automation navigateur). Ajouter un tool = l'implémenter + l'ajouter à
`get_tool_definitions()`.

**Sécurité : AUCUNE.** README : *"This tool can execute arbitrary shell commands and modify your file
system. Only use with trusted models and tasks."* Accès FS + shell complet (dans un base dir).

**Comparaison à notre `_run_agent_loop`** : la nôtre est **plus riche** — délégation multi-agents
(`delegate_to`), sandbox Docker verrouillé, compaction (`PreLLMCall`), hooks, `report_back` structuré,
budgets. ollamacode = **mono-agent, sans sandbox, sans retry** : simple mais c'est le **monolithe qui
hallucine** sur les grosses tâches (ton point de départ). ⇒ **on ne copie pas son archi** ; on pioche
l'**ergonomie des tools** et on note que notre loop n'a rien à lui envier.

→ **À adopter** : (a) granularité des tools fichier — il nous manque `workspace_delete` et un
`workspace_mkdir` explicite (on auto-crée les sous-dossiers, mais pas de delete) ; (b) le **streaming**
des tokens/tool-calls (on est en `stream=False`) améliorerait le ressenti live, non-critique.
→ **À éviter** : mono-agent, shell non-sandboxé, absence de retry.

## 2. E2B — templates & cycle de vie
Repo : <https://github.com/e2b-dev/e2b> · templates : <https://e2b.dev/docs/sandbox-template>

- **Template = base image + env + fichiers + commandes + start command (snapshot)**, défini par CLI
  (`e2b template init`) ou SDK. **Un template par langage/usage** (exemples JS/TS ET Python, fichiers
  indépendants) → **confirme ta préférence : une Dockerfile par type de code.**
- **Pré-chauffage (clé réactivité)** : *"the process is **already running** when you create a sandbox
  from that template … zero wait time."* Le runtime est snapshotté **démarré**.
- **Ressources par template** (`cpuCount`, `memoryMB`) → tuning par type.
- **Cleanup** : microVM **auto-tuée par timeout** (idle) — pas de conteneurs zombies.

**Comparaison à notre `bash_sandbox`** (`src/jeanmichel/tools/bash_sandbox.py`) :
- ✅ **Par-langage déjà là** : `py-alpine` / `node-alpine`, image choisie via `agents.sandbox_image`.
- ✅ **Conteneur par conversation réutilisé** (`jm-sandbox-{conv_id}`, `docker exec` par commande — pas
  de cold start à chaque appel, contrairement à un `docker run` par commande).
- ❌ **Pas de teardown** : `docker run -d --rm` lance en détaché ; `--rm` ne nettoie **qu'à l'arrêt**, et
  **rien n'arrête le conteneur**. ⇒ chaque conversation laisse un conteneur **tourner indéfiniment**
  (RAM réservée). C'est le « meilleur cleanup » d'E2B qui nous manque.
- ⚠️ **Pas de pré-chauffage** : le 1ᵉʳ appel d'une conv paie le `docker run`.

## 3. Recommandations pour Jean-Michel (priorisées)
**R1 — Cleanup des sandboxes (le vrai trou, prioritaire).** Implémenter un teardown :
- **idle-TTL + reaper** : une commande `./jm.sh --reap-sandboxes` (et/ou au shutdown du daemon) qui
  `docker stop` les `jm-sandbox-*` inactifs depuis N min (le `--rm` fait le `rm` au stop). À la
  E2B (auto-kill par timeout). KISS : reaper périodique + stop sur fermeture de conversation.
- Effort : faible. Impact : élimine la fuite de conteneurs.

**R2 — Une Dockerfile par type (ta préférence).** On garde des images **alpine minimales par langage**
(une Dockerfile chacune), sélectionnées par worker via `agents.sandbox_image`. Minimiser les deps par
image = démarrage plus rapide. C'est déjà le pattern (py/node) ; le formaliser (un worker = une image)
quand un besoin node/go réel arrive. (Confirmé par E2B : templates indépendants par langage.)

**R3 — Pré-chauffage (optionnel, réactivité).** Démarrer le conteneur de la conv **à l'ouverture d'une
conv en mode `code`** (ou au 1ᵉʳ tour) pour cacher la latence du `docker run`. Inspiré du snapshot E2B.
Effort : moyen. Impact : ressenti.

**R4 — Ergonomie tools (depuis ollamacode).** Ajouter `workspace_delete` (et éventuellement un
`workspace_mkdir` explicite). Faible priorité — utile pour les tâches d'édition multi-fichiers.

**R5 — Robustesse « thinking » du client LLM (issu du test E2E).** Le bug observé (`qwen3-coder ne
supporte pas thinking → 400 → délégation avortée → fallback monolithique`) a été corrigé pour
code-runner (`thinking_mode=0`), MAIS la cause générale demeure : si on câble un futur worker sur un
modèle sans canal *think*, ça repétera. **Recommandé** : dans `OllamaClient.chat_messages`, capter le
400 « does not support thinking » et **réessayer une fois sans `think`** (au lieu d'avorter). Filet de
sécurité général pour le câblage par-mode/par-agent. Effort : faible. Impact : évite une classe d'échecs
silencieux. *(Non implémenté : touche le chemin LLM critique — à faire en suivant si tu valides.)*

**R6 — À NE PAS faire.** Copier l'archi ollamacode (mono-agent + shell non sandboxé). On garde
multi-agents + sandbox verrouillé.

## 4. OpenHands — différé
À tester plus tard (tu l'as dit). Réf. la plus aboutie (sandbox Docker par session, image runtime
curatée, montage workspace, délégation multi-agents, tourne sur Ollama). Source :
<https://docs.openhands.dev/openhands/usage/architecture/runtime>. Quand on s'y mettra : comparer son
runtime client (serveur d'exécution dans le conteneur, accès SSH) à notre `docker exec`, et sa gestion
de session/teardown.

## 5. Sources
- ollamacode — <https://github.com/128bytes8/ollamacode> ; boucle
  <https://github.com/128bytes8/ollamacode/blob/master/main.py>
- E2B — <https://github.com/e2b-dev/e2b> ; templates <https://e2b.dev/docs/sandbox-template> ;
  <https://e2b.dev/>
- OpenHands runtime — <https://docs.openhands.dev/openhands/usage/architecture/runtime>
- Notre code — `src/jeanmichel/tools/bash_sandbox.py` (cycle de vie conteneur), `src/jeanmichel/llm.py`
  (`chat_messages`, point d'insertion R5).
