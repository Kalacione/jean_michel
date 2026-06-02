# Rapport — Mode `code`, sandboxes multi-langages & autonomie Docker

> Analyse des retours utilisateur (2026-06-01) + revue du prior art public. Pèse le pour/contre de
> trois idées : (1) un mode d'interaction `code`, (2) des sandboxes par langage, (3) l'« autonomie
> Docker ». Fait suite à [01_audit_decomposition_todo.md](01_audit_decomposition_todo.md) (S1+S2
> livrés). **Aucune implémentation ici** — on tranche la direction.

## 0. Verdicts en un coup d'œil
| Idée | Verdict | En une ligne |
|---|---|---|
| **1. Mode `code`** | ✅ **À faire** (raffinement de S2) | Plus propre que mon override global + garde vision : le mode = signal explicite « codebase → modèle robuste », gemma4 redevient le défaut (vision native), et on scope le paradigme PDCA au seul mode `code`. |
| **2. Sandboxes par langage** | ✅ **Déjà 80% là** | `agents.sandbox_image` choisit l'image par agent (`py-alpine`/`node-alpine` existent). Pas de nouveaux outils : un worker = un agent + son image. |
| **3a. Templates Docker curatés** | ✅ **Oui** | 3-4 images pré-buildées (py, node, +go/rust si besoin) + montage `/workspace`. C'est ce que font OpenHands & E2B. |
| **3b. « Clés de Docker » (socket, image choisie par web-search)** | ❌ **Non** | Socket Docker = root host trivial (évasion de conteneur documentée) ; web-search-to-image = latence + non-déterminisme pour ~zéro gain. |

## 1. Idée 1 — un mode `code` (à côté de `analyse` / `chat` / `vocal`)
**Pas une idée à la con — c'est même la bonne abstraction.** Aujourd'hui (S2) j'ai mis jean-michel
**globalement** sur qwen3:14b + une **garde** « si image → gemma4 ». Ça marche, mais c'est un
contournement. Le mode `code` fait mieux :

- **Sélection de modèle par mode** : jean-michel **gemma4 par défaut** (chat/analyse/vocal —
  vision native, plus léger pour les petites réflexions), **qwen3:14b uniquement en mode `code`**.
  ⇒ **ma garde vision devient inutile** (gemma4 est déjà le défaut pour tout ce qui n'est pas code).
- **Scope du paradigme PDCA** via `paradigm_modes` → le gros bloc PDCA n'est injecté **qu'en mode
  `code`**, zéro pollution des prompts chat/vocal/analyse.
- **Signal explicite** : « intervenir sur une codebase » = l'utilisateur choisit `code`, pas une
  heuristique fragile. C'est exactement ta phrase d'origine (« le router suffit pour des petites
  réflexions, mais pour les codebases il faut du robuste »).

**Coûts / contre** :
- **Opt-in** : si l'utilisateur tape une demande de code en mode `chat`, il reste sur gemma4
  (décomposition plus faible). Mitigation possible (plus tard) : le dispatcher Tier-0 (granite)
  détecte l'intention « code » et suggère/bascule. KISS d'abord : opt-in manuel.
- **Câblage** : ajouter `code` à l'énumération des modes partout (dispatcher, `turn_runner`,
  sélecteur d'UI web, CLI `--mode`, `paradigm_modes`). C'est mécanique mais transverse.
- **Cohérence** : le mode `code` force DEEP (jamais ALEXA) et active la stack PDCA + workers coding.

**Reco** : le faire en **S2.5** — revenir jean-michel à gemma4 par défaut, sélection qwen3:14b
quand `mode == 'code'`, scoper le paradigme PDCA à `code` (+ `analyse` éventuellement), retirer la
garde vision (devenue inutile). Migration `migrate_121` + logique mode→modèle dans `_run_deep_turn`.

## 2. Idée 2 — sandboxes par langage
**Déjà supporté à 80%.** Le HOWTO le documente : la colonne **`agents.sandbox_image`** choisit
l'image Docker par agent, et deux images existent déjà :
- `jeanmichel-sandbox:py-alpine` (Python 3.13, jq, requests/numpy/tabulate) — défaut.
- `jeanmichel-sandbox:node-alpine` (Node 22, TS, ts-node).

⇒ Un « node_sandbox » **n'est pas un nouvel outil** : c'est un **worker** (ex. `code-runner-node`)
= agent avec `sandbox_image='jeanmichel-sandbox:node-alpine'` + grants `bash_sandbox`/workspace +
sandbox_grants (`node`, `npm`…). L'orchestrateur délègue au worker dont l'image matche le langage.

**Alternative** (plus tard, si on veut éviter de multiplier les agents) : un paramètre `runtime`
sur `bash_sandbox` (`python|node|…`) mappé à une image curatée, sélectionné par le worker. Mais le
**1 worker = 1 image** est plus KISS et colle au modèle de grants actuel (DB-driven, rien en dur).

**Reco** : pas d'urgence. Quand un besoin node réel arrive → créer `code-runner-node` (1 migration,
l'image existe déjà). Le `bash_sandbox` reste un seul outil.

## 3. Idée 3 — « autonomie Docker »
Ta vision : l'agent web-search l'image à lancer, part d'un `docker-compose` template avec montage
workspace, et hop. **Le repli que tu proposes toi-même (« quelques templates prémâchés ») est la
bonne réponse** — et c'est exactement ce que fait le prior art sérieux.

| Variante | Verdict | Raison |
|---|---|---|
| Quelques **images/templates curatés** (py, node, go, rust) + montage `/workspace` | ✅ | OpenHands construit son runtime depuis une **image de base fournie** ; E2B = **templates `e2b.Dockerfile`** pré-buildés. Couvre ~tous les cas réels. |
| Agent **choisit son image via web-search** | ⚠️→❌ | Latence + non-déterminisme + surface d'attaque (pull d'images arbitraires) pour un gain marginal vs 3-4 templates. |
| Agent reçoit le **socket Docker** / `docker compose` libre | ❌ | Le socket Docker = **root sur l'hôte**. L'évasion de conteneur par un LLM est un risque documenté (arXiv 2603.02277). Casse tout notre modèle verrouillé (`--network=none`, `--cap-drop=ALL`, whitelist). |

**Notre posture sécurité est déjà au niveau de l'état de l'art** (cf. §5) : `--network=none`
(le « easiest win » anti-exfiltration), montage du seul `/workspace` (jamais `$HOME`), conteneur
par conversation, cap-drop, whitelist de binaires, limite mémoire. **Ne pas la sacrifier** pour de
l'autonomie. La seule limite non couverte (et non couvrable par un sandbox) = l'**injection de
contexte** : ne JAMAIS injecter de secret dans le sandbox (on ne le fait pas).

**Reco** : rester sur des **templates curatés sélectionnés par worker** (idée 2). Pas de socket,
pas de web-search-to-image. Si un jour on veut « n'importe quel stack » : intégrer **E2B**
(microVM Firecracker, templates Dockerfile) ou **alibaba/OpenSandbox** plutôt que réinventer un
provisioner Docker maison.

## 4. Prior art — ce qu'un « petit génie de GitHub » a déjà poussé
| Projet | Ce que c'est | À voler |
|---|---|---|
| **OpenHands** (ex-OpenDevin, MIT) | Agent SWE autonome, **sandbox Docker par session** (image construite depuis une base fournie, SSH + Jupyter, **montage workspace seul**, détruit en fin de session), délégation multi-agents, tourne sur **Ollama (Qwen/DeepSeek/Llama)**. | Le modèle « runtime = image curatée par session + workspace mount ». La délégation multi-agents (on l'a). |
| **E2B** (open-source) | Sandboxes cloud pour agents, **microVM Firecracker**, **templates `e2b.Dockerfile`** (build → microVM). | Le pattern « template pré-buildé sélectionnable ». Option d'intégration si on veut du multi-stack sérieux. |
| **alibaba/OpenSandbox** | Runtime de sandbox généraliste pour agents, **multi-langages**, Docker/K8s, SDK Py/JS/Go/… | Référence pour un `bash_sandbox` multi-runtime si on étend. |
| **ollamacode** (128bytes8) | Assistant agentique **100% Ollama** (tool-calling natif, file ops + terminal), « comme Claude Code mais local ». | Le concurrent le plus proche de notre cible — à étudier pour les prompts/loop. |
| **OpenCode**, **CUA**, **agent-sandbox** (E2B-compatible), **restyler/awesome-sandbox** | Écosystème local-agent + liste curée de sandboxes. | Veille. |

**Constat** : personne n'a poussé *exactement* notre combo (harness LLM local maison + orchestrateur
PDCA + sandbox Docker verrouillé + mémoire partagée), mais **chaque brique a une référence mûre**.
On est sur la bonne voie ; pas besoin de tout réinventer côté sandbox (OpenHands/E2B/OpenSandbox
sont là si on veut accélérer le multi-stack).

## 5. Sécurité — notre sandbox vs les best practices (validation)
Les recommandations 2026 (Sandgarden, Bunnyshell, LangChain, smolagents, Northflank) :
- **Couper le réseau** (anti-exfiltration) → ✅ `--network=none`.
- **Monter le seul dossier projet, jamais `$HOME`** → ✅ `/workspace` only.
- **Auto-destruction + limites ressources + moindre privilège** → ✅ conteneur/conv, mémoire cap,
  `--cap-drop=ALL`, whitelist.
- **L'injection de contexte n'est pas sandboxable** : ne pas mettre de secret dans le sandbox → ✅
  (aucun secret injecté).
⇒ **Rien à durcir d'urgent.** Le risque #1 si on ouvrait Docker à l'agent = évasion de conteneur
(le socket) — précisément ce qu'on refuse.

## 6. Reco séquencée (KISS)
1. **D'abord** : finir **S3** (E2E coding avec ce qu'on a — qwen3:14b + qwen3-coder + py-sandbox)
   et régler le paradigme PDCA. Ne rien ajouter avant d'avoir vu la boucle tourner.
2. **Puis (si S3 valide la boucle)** : **mode `code`** (S2.5) — la meilleure des trois idées,
   nettoie l'archi modèle/vision et le scoping du paradigme.
3. **À la demande** : worker `code-runner-node` (idée 2) quand un besoin JS/TS réel arrive.
4. **Plus tard / si ambition multi-stack** : évaluer **E2B** ou **OpenSandbox** plutôt qu'un
   provisioner Docker maison. **Jamais** le socket Docker à l'agent.

## 7. Sources
- OpenHands runtime — <https://docs.openhands.dev/openhands/usage/architecture/runtime> ; SDK
  (arXiv 2511.03690) — <https://arxiv.org/html/2511.03690v1>
- E2B — <https://e2b.dev/> ; templates — <https://e2b.dev/docs/sandbox-template> ;
  <https://github.com/e2b-dev/e2b>
- alibaba/OpenSandbox — <https://github.com/alibaba/OpenSandbox>
- ollamacode — <https://github.com/128bytes8/ollamacode>
- awesome-sandbox — <https://github.com/restyler/awesome-sandbox>
- Sécurité : Sandgarden <https://www.sandgarden.com/learn/llm-sandbox> ; smolagents secure exec
  <https://huggingface.co/docs/smolagents/tutorials/secure_code_execution> ; LangChain sandboxes
  <https://docs.langchain.com/oss/python/deepagents/sandboxes> ; container escape (arXiv 2603.02277)
  <https://arxiv.org/pdf/2603.02277> ; Northflank <https://northflank.com/blog/best-code-execution-sandbox-for-ai-agents>
