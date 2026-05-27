# 05 — Inspiration Claude Code et Copilot

> On n'invente rien. Claude Code et Copilot CLI sont deux harness publics
> qui tournent en production sur des millions de sessions. Ce doc extrait
> les patterns qu'on peut reprendre sans complexité ajoutée, et identifie
> ce qu'on ne veut PAS reprendre.

## 1. Le harness Claude Code est une dumb loop

> "The 'agentic' behavior emerges from a loop running until the model
> decides it's done, with no complex planning system, no separate
> 'reasoning engine' — just repeated calls to the same LLM with an
> accumulating context. The heart of the harness is a very simple while loop."
> — [Inside the Agent Harness](https://medium.com/jonathans-musings/inside-the-agent-harness-how-codex-and-claude-code-actually-work-63593e26c176)

C'est l'observation centrale. Le harness Claude Code n'a PAS de state machine
de pipeline, PAS de phases gather/critic/build hardcodées, PAS de classifier
en amont. Il a :

- Un `messages: list[dict]` accumulé.
- Une liste de tools déclarés.
- Une boucle `while llm_wants_more_tools: call(llm); execute_tools(); append_results()`.

Quand l'agent veut une sous-recherche, il appelle un tool. Le tool retourne
en `messages[]` comme un message `role=tool`. Le LLM voit son propre tool
call et son propre résultat au tour suivant. Pas de récap dégradé,
pas de "running user text" reconstruit.

**Implication pour Jean-Michel** : Ollama supporte les rôles `assistant` et
`tool` nativement (`{"role":"tool","tool_call_id":"...","content":"..."}`).
On n'utilise pas ça aujourd'hui dans [llm.py](src/jeanmichel/llm.py#L74).
Le changer débloque tout.

## 2. Hooks déterministes hors-prompt

Claude Code fait une distinction explicite :

> "Hooks fire at fixed lifecycle points — unlike skills, they are
> deterministic, not model-chosen. Anything that 'should be handled
> deterministically rather than asked of the LLM every time' belongs in
> a hook."
> — [Claude Code Harness Architecture](https://pasqualepillitteri.it/en/news/1892/claude-code-harness-runtime-architecture-2026-guide)

Les hooks Claude Code : `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop`,
`UserPromptSubmit`, `Notification`, etc. Configurés en JSON, exécutés par
le harness, jamais par le LLM. Exemples concrets : bloquer un tool si la
permission n'est pas accordée, logger les tool calls dans un fichier, valider
le format du diff avant un git commit, etc.

**Symétrie pour nous** : tous les paradigmes Jean-Michel actuels du type
`paradigm 100 convergence_gate` ("si depth>=2 et pas de nouvelle info, call
signal_convergence") sont des **hooks déguisés en prompt**. Le LLM ne suit
pas une consigne molle ("if X then call Y") aussi bien qu'un Python qui
intercepte un tool_call et le rejette.

**À transformer en hook** :
- Dédup tool_call avec contexte (remplace `paradigm 100 convergence_gate`)
- Force workspace_write après N research calls (remplace
  `paradigm 103 workspace_as_shared_memory`)
- Reject delegate_to si depth >= MAX_DEPTH (existe déjà côté code, à
  garder en hook propre)
- Compaction du `messages[]` quand approche du budget tokens

## 3. Subagents (Task tool) : contexte frais + retour unique

> "Subagents are isolated Claude Code sessions spawned by a parent agent.
> Each one gets a fresh context window, runs a specific task, and returns
> the result to the parent. Subagents use their own isolated context
> windows, and only send relevant information back to the orchestrator,
> rather than their full context."
> — [Orchestrate teams of Claude Code sessions](https://code.claude.com/docs/en/agent-teams)

Le `delegate_to` actuel de Jean-Michel est conceptuellement proche, mais
problématique : il appelle récursivement `_run_request` dans le même
process Python, partage le même client LLM, et le parent attend en bloquant.

Le pattern Claude Code est plus propre :
- Subagent = nouveau process / nouveau context window (les `messages[]` du
  parent ne sont pas visibles).
- Spawn synchrone (le parent attend) mais le subagent travaille en isolation.
- Le retour est UNE STRUCTURE (summary + workspace_files), pas un dump.
- Le parent intègre ce retour comme un `tool` message dans son `messages[]`.

**Pour Jean-Michel** : le subagent doit avoir son propre `messages[]` array
créé from scratch avec son system prompt + l'inbound briefing. Il accumule
ses tool calls dedans. Au retour, on extrait `{summary, files, confidence}`
et on injecte juste ça dans le `messages[]` du parent. Pas de partage
de context.

## 4. Sessions et compaction comme objet externe

> "If messages are transformed by a compaction step, the harness removes
> compacted messages from Claude's context window, and these are
> recoverable only if they are stored. The session provides this benefit,
> serving as a context object that lives outside Claude's context window,
> with context durably stored in the session log."
> — [Claude Code Harness 2026 Guide](https://pasqualepillitteri.it/en/news/1892/claude-code-harness-runtime-architecture-2026-guide)

Compaction = quand le `messages[]` devient trop gros, le harness le résume
(via un appel LLM dédié, souvent un modèle plus petit) et remplace les
anciens messages par leur résumé. Le tout indexé dans la session pour
recovery.

**Pour nous** : exactement ce qu'on veut quand un spécialiste accumule 20
tool_responses (web_search × 5, wikipedia × 3, etc.). Au lieu de tout
garder en messages, on compacte les anciens en "Findings so far: ..." et
on garde les derniers turns en clair. Le tiny LLM (granite4.1) est parfait
pour la compaction — appel court, pas de thinking.

## 5. Auto-memory (à intégrer côté Jean-Michel)

Claude Code (et Claude.ai en général) maintient une **mémoire long-terme
utilisateur** distincte de la session courante. Pattern :

- Index plat (`MEMORY.md`) listant les entrées par titre + description courte.
- Une entrée = un fichier markdown avec frontmatter YAML
  (`type: user|feedback|project|reference`, `name`, `description`).
- Cross-references via `[[name]]` (style wiki).
- Inject automatique de l'index dans le system prompt à chaque tour.
- Tool dédié pour le LLM : ajouter, lire, mettre à jour, supprimer une entrée.
- Discipline : update OR remove memories qui deviennent obsolètes.

**Pour Jean-Michel** : on a aujourd'hui un `user_profile.toml` statique
([README.md](README.md) §Profil utilisateur). C'est l'embryon. Évolution
naturelle vers une mémoire structurée en BDD :

- Table `user_memory(id, type, code, title, description, content, created_at, modified_at)`.
- Tool `manage_user_memory(action: save|recall|list|delete, type?, code?, ...)`.
- Au render du `## Human` block dans le system prompt, prepend l'index
  (type + code + description, sans le content complet) pour que tout agent
  sache ce qui existe.
- Le main agent (router) peut décider quand sauvegarder une nouvelle
  info ou en oublier une dépassée. Discipline pilotée par paradigme dédié,
  pas par hardcode.

C'est un petit composant K.I.S.S — une table, un tool, un rendu prompt.
Détails d'implémentation dans
[06_proposition_v2.md](DevNotes/REVOLUCION/06_proposition_v2.md) §I et
[07_plan_implementation.md](DevNotes/REVOLUCION/07_plan_implementation.md)
Phase 4.

## 6. Copilot CLI : ce qu'on prend (et ce qu'on laisse)

> "Fleet mode orchestrates multiple subagents working in parallel to execute
> decomposed subtasks efficiently, with the fleet orchestrator decomposing
> tasks into parallelizable subtasks that execute concurrently."
> — [Agent Orchestration System](https://deepwiki.com/github/copilot-cli/6.3-agent-orchestration-system)

**On prend** : custom agents avec scoped tools + scoped system prompt — c'est
ce que Jean-Michel fait déjà via `agent_tools` + `agent_paradigms`. Bon
système. Conserver.

**On laisse** : fleet mode (parallélisme multi-agents simultanés). Sur du
local mono-GPU, paralléliser n'apporte rien — Ollama sérialise au niveau
GPU. Notre seul "parallélisme" utile est de **batcher plusieurs `delegate_to`
dans le même tour LLM** (déjà supporté). Pas besoin de fleet.

## 7. Ce qu'on ne reprend PAS (volontairement)

- **MCP servers** comme primitive de tools. C'est over-kill pour des tools
  locaux qui peuvent être de simples fonctions Python. On garde notre
  `build_registry(conv_folder)` qui est déjà propre.
- **Skills à la Claude Code** (capacités chargées sur demande). Pas
  pertinent à notre échelle — on a 10 outils, pas 100.
- **Plan mode** comme état Claude Code (où le LLM ne peut que lire/proposer).
  C'est utile pour un dev tool généraliste, mais notre router est déjà en
  position de planificateur dans son flux normal.
- **Permissions interactives** ("autoriser cette commande ?"). En local
  trusted, sans intérêt. Notre sandbox Docker est suffisante.

## Synthèse — ce qu'on inscrit dans la v2

Trois choses prises chez Claude Code, une chez Copilot, une qu'on garde de
nous, le reste qu'on jette.

1. **Boucle dumb avec `messages[]` natif** (Claude) — fix la fracture mémoire.
2. **Hooks déterministes hors-prompt** (Claude) — remplace les paradigmes
   anti-loop et les MUST en cascade.
3. **Subagent = contexte frais + retour structuré** (Claude) — remplace
   notre `delegate_to` qui partage le contexte.
4. **Auto-memory utilisateur** (Claude) — évolution de notre
   `user_profile.toml` statique.
5. **Custom agents scopés** (nous + Copilot) — conserver tel quel.
6. **Compaction par tiny LLM** (Claude) — remplace nos heuristiques de
   troncature.

## Prochaine étape

[06_proposition_v2.md](DevNotes/REVOLUCION/06_proposition_v2.md) —
l'architecture refondue qui assemble ces blocs.

## Sources

- [How Claude Code works — Claude Code Docs](https://code.claude.com/docs/en/how-claude-code-works)
- [Building agents with the Claude Agent SDK — Anthropic](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk)
- [Inside the Agent Harness: How Codex and Claude Code Actually Work](https://medium.com/jonathans-musings/inside-the-agent-harness-how-codex-and-claude-code-actually-work-63593e26c176)
- [Claude Code Harness Architecture 2026 Guide](https://pasqualepillitteri.it/en/news/1892/claude-code-harness-runtime-architecture-2026-guide)
- [Orchestrate teams of Claude Code sessions](https://code.claude.com/docs/en/agent-teams)
- [GitHub Copilot CLI Agent Orchestration System](https://deepwiki.com/github/copilot-cli/6.3-agent-orchestration-system)
- [Agent mode 101 — GitHub Blog](https://github.blog/ai-and-ml/github-copilot/agent-mode-101-all-about-github-copilots-powerful-mode/)
