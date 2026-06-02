# Claude Code — Catalogue des pratiques & astuces (source leakée)

> **Source** : fork TS de Claude Code CLI cloné dans `~/lab/clawde/claude-code-fork` (leak npm
> `.map` du 2026-03-31 ; ~1900 fichiers, ~512k lignes ; runtime Bun, UI React/Ink). Les refs
> `chemin:ligne` pointent dans CE fork local ; les citations en anglais sont **verbatim** de la
> source. Doc de référence pour nourrir l'orchestrateur Jean-Michel (cf.
> [01_audit_decomposition_todo.md](01_audit_decomposition_todo.md)). Date : 2026-06-01.

---

## 0. Lecture rapide — les 12 astuces qui comptent pour nous
1. **TODO réinjecté** en `<system-reminder>` (message *user* `isMeta`), **avec throttle** (toutes
   les ~10 itérations sans `TodoWrite`), pas à chaque tour aveuglément.
2. **« Un seul `in_progress` » n'est PAS gardé par le code** — c'est **prompt-only**. (Valide notre
   « pas de hard-gate ».)
3. **Sous-agents = contexte FRAIS**, system prompt propre, **pool d'outils propre**, mode de
   permission propre, contexte **filtrable** (on peut omettre gitStatus/CLAUDE.md).
4. **Coordinateur multi-agents** en phases **Research (//) → Synthèse → Implémentation →
   Vérification** = exactement notre PDCA.
5. **Prompt système coupé en statique (cacheable) / dynamique** via un marqueur de frontière.
6. **`<system-reminder>` = bus de métadonnées** hors prompt statique (injecté par tour, atomique).
7. **Outils : règles de comportement gravées dans la *description*** (read-before-edit,
   `old_string` unique, format `cat -n`, « préfère l'outil dédié à Bash »).
8. **Dispatch : lectures en parallèle / écritures en série** (partition par `isConcurrencySafe`).
9. **Compaction multi-paliers** avec seuils calés sur la fenêtre *effective* (− réserve de sortie).
10. **Budget par résultat d'outil** (~4 Ko) : on tronque + on marque, pour ne pas exploser le ctx.
11. **Mémoire** : index `MEMORY.md` + fichiers à frontmatter, **taxonomie 4 types** (= la nôtre),
    **auto-extraction par agent forké** post-tour.
12. **Robustesse** : retry + **fallback de modèle**, interruption *cheap* par `AbortController`.

---

## 1. Architecture d'ensemble
- Entrée `main.tsx` (Commander + Ink) → `QueryEngine.ts` (~46k lignes : appels API, streaming,
  boucle tool-call, thinking, retry) → boucle `query.ts`.
- Registres : `tools.ts` (≈40 outils), `commands.ts` (≈50 slash-commands), `Tool.ts` (types).
- Sous-systèmes notables : `coordinator/` (multi-agents), `tasks/`, `memdir/` (mémoire),
  `services/compact/` (compaction), `hooks/toolPermission/` (permissions), `skills/`, `bridge/`
  (IDE), `remote/`, `server/`.

## 2. Boucle agent & assemblage du prompt système
**Le prompt système est coupé en deux** par un marqueur (`prompts.ts:573`) :
```
SYSTEM_PROMPT_DYNAMIC_BOUNDARY = '__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__'
```
- **Statique (préfixe cacheable, `scope:'global'`)** : identité + cadrage cyber ; règle « contexte
  illimité via auto-résumé » ; *Doing tasks* (qualité, minimalisme, vérification) ; *Actions*
  (réversibilité, confirmation des opérations destructrices) ; *Using your tools* (préférer les
  outils dédiés à Bash) ; *Tone & style*.
- **Dynamique (après le marqueur)** : guidage de session (outils, instructions d'agent, découverte
  de skills), prompt mémoire, infos d'environnement (cwd, git, date, modèle, cutoff), langue,
  output style, instructions MCP, *microcompact*, nudges de budget de tokens.

→ **Astuce cache** : tout ce qui précède le marqueur est identique entre des millions de sessions
(même modèle) ⇒ réutilisation du hash de préfixe. Le dynamique change sans invalider le statique.

Le contexte d'environnement est **réinjecté par tour** (`api.ts:437 appendSystemContext`), pas figé
dans le statique. Bloc env (`prompts.ts:640-648`) :
```
<env>
Working directory: …
Is directory a git repo: Yes/No
Platform: … / Shell: … / OS Version: …
</env>
```

## 3. Système de TODO (`TodoWriteTool`)
**Schéma** (`tools/TodoWriteTool/TodoWriteTool.ts`) : `todos: [{ content (impératif), status:
pending|in_progress|completed, activeForm (présent continu) }]`.
**Description** (`…/prompt.ts:183`) :
> *"Update the todo list for the current session. To be used proactively and often to track
> progress and pending tasks. Make sure that at least one task is in_progress at all times. Always
> provide both content (imperative) and activeForm (present continuous) for each task."*

- **Stockage** : `AppState.todos[todoKey]`, `todoKey = agentId ?? sessionId` → **chaque agent a sa
  propre liste**.
- **Validation code** : la seule logique est `allDone → liste vidée` (`TodoWriteTool.ts:70`). Le
  « **exactement un `in_progress`** » est **PROMPT-ONLY** (prompt.ts:158/184), **zéro garde code**.
- **Réinjection (clé)** : `getTodoReminderAttachments()` (`utils/attachments.ts:3266-3317`) se
  déclenche quand `turnsSinceLastTodoWrite >= 10 && turnsSinceLastReminder >= 10`, rend la liste
  vivante de `AppState` en **message *user* enveloppé `<system-reminder>` (`isMeta`)**
  (`utils/messages.ts:3663-3678`) :
> *"The TodoWrite tool hasn't been used recently. … consider using the TodoWrite tool … Here are
> the existing contents of your todo list:"* + `[ N. [status] content … ]`

→ **Donc** : Claude Code **ne réinjecte pas à chaque tour** — il *throttle* (≈10 tours sans usage),
et c'est un *nudge* (« tu n'as pas mis à jour ta liste »), pas un push systématique.

## 4. Sous-agents, Task & multi-agents
**`AgentTool`** (`tools/AgentTool/…`) : `AgentTool({ description, prompt, subagent_type?, model?,
isolation: "worktree"|"remote", cwd?, run_in_background }) → agentId`.
- **Contexte FRAIS** (`runAgent.ts:347-518`) : nouvel `agentId` ; `contextMessages` filtrés +
  `promptMessages` ; **system prompt propre** (`getAgentSystemPrompt`) ; user/system context
  **filtrables** (on peut **omettre gitStatus / CLAUDE.md**) ; **mode de permission propre** ;
  **pool d'outils propre** assemblé par l'appelant. Pas d'héritage d'historique implicite.
- **Résultats** renvoyés en XML `<task-notification>` (status / summary / result / usage).
- **Messagerie** : `SendMessage({ to: agentId | "*" | "bridge:sessionId", message, summary })` —
  continue un agent ou broadcast ; messages structurés (shutdown_request/response,
  plan_approval_response).
- **Mode coordinateur** (`coordinator/coordinatorMode.ts:111-370`) : system prompt dédié ; phases
  **Research (parallèle) → tu synthétises → Implementation → Verification** ; consigne forte :
  *prouver la compréhension* (ne pas se contenter de « based on findings »).
- **Concurrence** : recherche/lecture **en parallèle**, écritures **sérialisées** ; plafond
  `getMaxToolUseConcurrency()` (défaut **10**, env `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY`).
- **Isolation** : `worktree` = branche git isolée ; `remote` = toujours en background.

## 5. Conception des outils (les règles sont dans la *description*)
| Outil | Params clés | Règle gravée (verbatim courte) |
|---|---|---|
| **Read** | `file_path`(abs), `offset`, `limit`, `pages`(PDF) | *"Results are returned using cat -n format, line numbers starting at 1"* ; 2000 lignes/défaut ; PDF >10p → `pages` requis (max 20) ; images rendues visuellement |
| **Write** | `file_path`(abs), `content` | *"you MUST use the Read tool first … This tool will fail if you did not read the file first"* ; *"Prefer the Edit tool … Only use this tool to create new files or for complete rewrites"* |
| **Edit** | `file_path`, `old_string`(unique), `new_string`, `replace_all` | *"The edit will FAIL if old_string is not unique"* ; *"preserve the exact indentation … AFTER the line number prefix … Never include any part of the line number prefix in the old_string"* ; pré-read obligatoire, rejet si modifié depuis |
| **Glob** | `pattern`, `path` | 100 résultats max |
| **Grep** | `pattern`, `path`, `glob`, `output_mode`, `-A/-B/-C`, `-i`, `-n`, `head_limit`(250), `multiline` | *"ALWAYS use Grep for search tasks. NEVER invoke grep or rg as a Bash command"* |
| **Bash** | `command`, `timeout`, `run_in_background` | *"Avoid using this tool to run find, grep, cat, head, tail, sed, awk, echo … use the appropriate dedicated tool"* ; *"File search: Use Glob (NOT find or ls)"* ; *"maintain your current working directory … using absolute paths and avoiding cd"* ; git : *"Always create NEW commits rather than amending"* |
| **WebFetch** | `url`, `prompt` | *"WILL FAIL for authenticated or private URLs"* |
| **WebSearch** | `query`, `allowed/blocked_domains` | 8 recherches max |

**Astuces de sécurité/UX en code** :
- **Dédup de Read** (`FileReadTool.ts:524-572`) : même fichier+offset+mtime → `FILE_UNCHANGED_STUB`
  *"refer to that instead of re-reading"* (WeakMap par tour ; ~18 % de Read en moins).
- **Budget de tokens image** : lire **une fois** → resize standard → compression agressive si
  dépassement (jamais de re-read).
- **Chemins device bloqués** (`/dev/zero`, `/dev/stdin`, `/dev/tty`) par check de chemin (zéro I/O).
- **Pré-read enforcement** Write/Edit (rejet sur contenu périmé).
- **`head_limit` avec échappatoire** : défaut 250 (signale la troncature au modèle), `0` = illimité.
- **Rappel anti-malware** ajouté à chaque lecture de fichier (cf. §10).

**Dispatch (`services/tools/toolOrchestration.ts:19-115`)** : `runTools` partitionne les appels —
**suite d'outils read-only → exécutés en parallèle** ; **outil non-read-only → exclusif/série**.
`isConcurrencySafe(input)` par outil (parse du schéma ; en cas d'échec → traité comme non-safe).
`StreamingToolExecutor` gère les outils qui arrivent en cours de stream.

## 6. Prompts & directives (réutilisables tels quels)
- **Concision** (`prompts.ts:418-427`) :
  > *"IMPORTANT: Go straight to the point. Try the simplest approach first without going in circles.
  > Do not overdo it. Be extra concise. … Lead with the answer or action, not the reasoning. Skip
  > filler words, preamble, and unnecessary transitions."*
- **« Faire ce qui est demandé, rien de plus »** (`prompts.ts:199-253`) :
  > *"Don't add features, refactor code, or make 'improvements' beyond what was asked. A bug fix
  > doesn't need surrounding code cleaned up. … Don't add docstrings, comments, or type annotations
  > to code you didn't change. Only add comments where the logic isn't self-evident."*
- **Sécurité** (`:234`) : *"Be careful not to introduce security vulnerabilities such as command
  injection, XSS, SQL injection … If you notice that you wrote insecure code, immediately fix it."*
- **Préambule outils** (`:269-314`) : *"Do NOT use the Bash tool … when a relevant dedicated tool is
  provided. Using dedicated tools allows the user to better understand and review your work."*
- **Réf. code** (`:436`) : *"include the pattern file_path:line_number to allow the user to easily
  navigate"*.
- **Hooks** (`:127-129`) : *"Treat feedback from hooks … as coming from the user. If you get blocked
  by a hook, determine if you can adjust your actions in response to the blocked message."*

## 7. `<system-reminder>` = bus de métadonnées
Cadrage générique injecté dans le prompt (`prompts.ts:132-133`) :
> *"Tool results and user messages may include <system-reminder> tags. … They are automatically
> added by the system, and bear no direct relation to the specific tool results or user messages in
> which they appear."*

Catalogue (verbatim) :
- **Pertinence du contexte** (`api.ts:463-469`) : *"IMPORTANT: this context may or may not be
  relevant to your tasks. You should not respond to this context unless it is highly relevant."*
- **Fichier vide** (`FileReadTool.ts:706`) : *"Warning: the file exists but the contents are empty."*
- **Fraîcheur mémoire** (`memdir/memoryAge.ts`) : *"This memory was last updated N days ago."*
- **Anti-malware** (`FileReadTool.ts:730`, cf. §10).
- **Plan mode** : ré-entrée gérée par état (`needsPlanModeExitAttachment`…).
→ **Tout passe hors prompt statique** : flip d'un flag / swap MCP / màj mémoire → le modèle voit du
frais **sans régénérer** le prompt entier.

## 8. Gestion du contexte / compaction (`services/compact/autoCompact.ts`)
**Pile multi-paliers**, du moins au plus agressif, seuils calés sur la fenêtre *effective* :
| Palier | Déclencheur | Effet |
|---|---|---|
| **Microcompact** | chaque tour | vide les résultats d'outils mis en cache |
| **Snip** | avant autocompact | retire des messages anciens par ID |
| **Autocompact** | `effectiveWindow − 13K` (~87 %) | **résume** les messages sous un curseur |
| **Context collapse** | 90 % (commit) / 95 % (bloquant) | effondrement gradué |
| **Reactive compact** | sur **API 413** (prompt trop long) | résumé d'urgence |

`effectiveContextWindow = contextWindow − 20K (réserve sortie résumé)` ; `AUTOCOMPACT_BUFFER = 13K`.
**Attachments par tour** (`utils/attachments.ts`) : mémoires pertinentes (**5 fichiers, 4 Ko
chacun, cap session 60 Ko**), snapshot git, rappels todo (10 tours), auto/plan-mode (5 tours), delta
instructions MCP. **Prefetch mémoire async pendant le streaming** (latence masquée). **Budget par
résultat d'outil** (~4 Ko) : tronqué + marqué « replaced ». **Normalisation/réordonnancement** des
messages avant envoi (UUID stables, attachments remontés jusqu'à une frontière tool-use/result).
**Sessions** persistées en **JSONL** + fichiers d'état ; *resume* = messages **après la dernière
frontière de compaction** + re-préfixe de contexte.

> **→ applicabilité Jean-Michel (mode `code`) : DÉJÀ COUVERT.** `compaction.py` (4 paliers, seuils
> 0.70/0.80/0.90/0.95), budget `ctx − reserve − 0.15·ctx`, recap `[TODO-RECAP]`, resume JSONL. Les
> manques sont **inapplicables au runtime** (réactif-413 : Ollama tronque `num_ctx`, pas de 413 ;
> prefetch-pendant-streaming : on ne stream pas), **déjà mitigés** (budget par résultat ← microcompact
> + les workers renvoient des *résumés* via `report_back`, pas le brut) ou **volontaires** (mémoire =
> index statique + pull `manage_user_memory` ; l'auto-injection par tour coûterait le ctx 40960 de
> qwen3:14b). ⇒ rien à implémenter.

## 9. Permissions & hooks (`hooks/toolPermission/`, `entrypoints/sdk/coreSchemas.ts`)
- **Modes** (`coreSchemas.ts:337`) : `default` (prompte le dangereux), `acceptEdits` (auto-accepte
  les éditions), `bypassPermissions` (saute tout — exige un flag), `plan` (**aucune exécution**),
  `dontAsk` (ne prompte pas, refuse si non pré-approuvé). **Comportements** : `allow | deny | ask`.
- **Flux `canUseTool`** : **hooks d'abord** → si décision, on l'applique → sinon classifieur (Bash si
  `BASH_CLASSIFIER`) → sinon dialogue. Un `deny` **abort** l'agent (`abortController.abort()`).
- **Événements de hook** (`coreSchemas.ts:355`) : `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
  `Notification`, `UserPromptSubmit`, `SessionStart`, `SessionEnd`, `Stop`, `StopFailure`,
  `SubagentStart`, `SubagentStop`, `PreCompact`, `PostCompact`, `PermissionRequest`,
  `PermissionDenied`, `Setup`, `TeammateIdle`, `TaskCreated`/`TaskCompleted`,
  `Elicitation`/`ElicitationResult`, `ConfigChange`, `WorktreeCreate`/`WorktreeRemove`,
  `InstructionsLoaded`, `CwdChanged`, `FileChanged`.
- **Sorties de hook** : `async` (timeout) vs `sync` (`continue`, `suppressOutput`, `stopReason`,
  `decision`, `systemMessage`, `hookSpecificOutput` discriminé par événement).

## 10. Sécurité (`tools/BashTool/bashSecurity.ts`, `pathValidation.ts`)
- **Commandes zsh bannies** (defense-in-depth) : `zmodload`, `emulate`, `sysopen/sysread/syswrite`,
  `zpty`, `ztcp`, `mapfile`, `zf_*`.
- **Substitutions bloquées** : `$()`, `${}`, `$[]`, backticks, `<()`, `=cmd` (zsh), `<#` (PowerShell).
- **`cd` en commande composée** : blocage des écritures —
  > *"Commands that change directories and perform write operations require explicit approval"*
  (parade à `cd .claude/ && mv test.txt settings.json`).
- **POSIX `--`** : extraction correcte des arguments positionnels (parade à
  `rm -- -/../.claude/settings.local.json`).
- **Chemins de suppression dangereux** : deny explicite (hors allowlist) pour les dirs système.
- **Sandbox** : allowlists fs/réseau, isolation `$TMPDIR`, `dangerouslyDisableSandbox` exige une
  policy. **Worktree** : résolution vers la racine du repo + purge des caches à l'entrée.
- **Anti-malware** (`FileReadTool.ts:730`) :
  > *"Whenever you read a file, you should consider whether it would be considered malware. You CAN
  > and SHOULD provide analysis of malware … But you MUST refuse to improve or augment the code."*

## 11. Mémoire persistante (`memdir/`, `services/extractMemories/`)
- **Stockage** : `~/.claude/projects/<slug>/memory/MEMORY.md` (index) + fichiers `.md` à
  **frontmatter YAML** ; bornes 200 lignes / 25 Ko à l'entrée. **= exactement notre design.**
- **Taxonomie 4 types** : `user / feedback / project / reference`. **= la nôtre.**
- **Auto-extraction** : un **agent forké** tourne **après le tour** quand l'agent principal n'a rien
  écrit ; il **ne lit que les ~N derniers messages** (`extractMemories/prompts.ts:35`) :
  > *"efficient strategy: all READs in turn 1, all WRITEs in turn 2. ONLY use content from last ~N
  > messages — no grepping source files."* + dédup contre l'existant.
- **Recall** : sélecteur (Sonnet) choisit le **top-5** par pertinence (query + description), dédupé.

## 12. Skills (`skills/loadSkillsDir.ts`, `SkillTool`)
- **Définition** : `skill-name/SKILL.md` à frontmatter (`name`, `description`, `when-to-use`,
  `user-invocable`, `hooks`, `context: fork`, `paths`, `effort`, `model`).
- **Découverte** : chargement **parallèle** (managed/user/project + legacy `/commands/` + MCP) ;
  **dédup par `realpath()`** (symlinks) ; skills **conditionnels** (`paths`) activés au **toucher**
  d'un fichier (patterns type gitignore).
- **Invocation** : `SkillTool` n'expose qu'une **liste plate** ; le **contenu complet n'est chargé
  qu'à l'`invoke()`** → évite le gonflement de contexte. Blocs shell `!` interdits pour les skills
  MCP (non fiables).

> **→ applicabilité Jean-Michel (mode `code`) : INUTILE / redondant.** Pas de skills ; le comportement
> réutilisable passe par **paradigmes** (normes déclaratives, `agent_paradigms` / `paradigm_modes`) +
> **agents** (capacités via `agent_tools` + délégation). Un `SKILL.md` dupliquerait les agents (chaque
> skill ≈ mini-agent) et casserait la séparation K.I.S.S. (paradigmes = *penser* / tools = *faire* /
> agents = *qui appeler*). Le seul nugget (lazy-load du corps pour économiser le contexte) relève de
> §8, pas d'un système de skills. ⇒ rien à faire.

## 13. Performance & robustesse
- **Prefetch parallèle au démarrage** (`main.tsx`) : MDM + keychain lancés avant les imports lourds.
- **Lazy `import()`** des modules lourds (OTel ~400 Ko, gRPC ~700 Ko).
- **DCE par `feature('bun:bundle')`** : le code des features inactives est **strippé au build**.
- **Retry + fallback de modèle** (`services/api/withRetry.ts:52`) : `FallbackTriggeredError` → bascule
  de modèle ; 529 retenté en *foreground* ; *unattended* (background) → backoff jusqu'à 5 min,
  heartbeats 30 s.
- **Interruption *cheap*** : `AbortController` + propagation `WeakRef` à travers QueryEngine → outils
  → scans mémoire ; `interrupt()` coupe tout le pipeline (event-driven, pas de busy-wait).

---

## 14. Ce qu'on adopte pour Jean-Michel (mapping actionnable)
**Confirme notre design (doc 01)** :
- ✅ **Réinjection du TODO en `<system-reminder>`** : correct — mais ajouter **un throttle** (ne pas
  pousser à chaque tour si rien n'a bougé ; CC attend ~10 tours) et le **marquer méta** (message
  *user* `isMeta`). Notre recap « rafraîchi » va dans le même sens.
- ✅ **« Un seul `in_progress` » sans garde code** = notre « pas de hard-gate » (CC le fait
  prompt-only). On valide.
- ✅ **Sous-agents à contexte frais** (= `spawn_subagent`). **À ajouter** : CC **filtre** le contexte
  par worker (omettre git/CLAUDE.md) et lui donne un **pool d'outils propre** → renforce notre
  « briefing crafté + `support_files` » : *donner peu, mais juste*.
- ✅ **Phases coordinateur Research→Synthèse→Implémentation→Vérification** = notre **PDCA**. Reprendre
  la consigne *« prouver la compréhension, pas “based on findings” »* dans le paradigme.

**À piquer (petits gains, K.I.S.S.)** :
- **Directives de prompt** (concision, « do what's asked nothing more », `file_path:line`, sécurité)
  → excellent contenu pour un paradigme **global** (en anglais).
- **Règles d'outils workspace** : on a déjà *create-no-overwrite* + `old_string` unique
  (`workspace_str_replace`) ; **ajouter** le rejet si fichier modifié depuis lecture, et le **rappel
  de troncature** quand on cape une sortie (`head_limit` style).
- **Budget par résultat d'outil** (~4 Ko, tronquer+marquer) : utile pour nos sorties sandbox
  (déjà 50 Ko cap — on peut affiner le marquage « replaced »).
- **Seuils de compaction** calés sur **fenêtre effective = ctx − réserve sortie − buffer** : à
  mirrorer dans notre `compaction.py` (on a déjà 4 stratégies).
- **Hardening bash** : nos garde-fous (whitelist + `--network=none` + cap-drop) sont **déjà plus
  stricts** que la blacklist de CC ; rien d'urgent, mais la parade *cd-en-commande-composée* et le
  POSIX `--` sont des idées si un jour on élargit la whitelist.

**Non applicable chez nous** :
- **Parallélisme** (lectures //) : on reste séquentiel (1 GPU). On garde juste la **distinction
  read/write** comme invariant de sûreté si on batche un jour.
- **Cache de préfixe statique/dynamique** : pas de prompt-cache partagé sur Ollama → le marqueur de
  frontière n'apporte rien ici (mais garder le prompt système **stable** reste bon).

> **TL;DR** : notre architecture (orchestrator-workers, contexte frais, TODO réinjecté, PDCA, mémoire
> 4-types) est **alignée** avec Claude Code. Les écarts utiles à combler : **throttle + méta** sur la
> réinjection, **filtrage/outillage par worker**, **budget par résultat**, et le **contenu de prompt**
> (concision / minimalisme / sécurité).
