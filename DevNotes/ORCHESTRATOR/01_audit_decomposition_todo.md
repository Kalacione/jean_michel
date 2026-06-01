# Audit & design — Décomposition méthodique + TODO persistant (orchestrateur « Claude-Code-like »)

> **Statut : design à valider.** État des lieux (base LIVE + code), recherche externe, et
> logique d'implémentation K.I.S.S. adaptée à notre infra modeste. Aucune implémentation encore.
> Date : 2026-06-01.
>
> ⚠️ **Révision (retour d'expérience utilisateur)** : la première version proposait *un seul
> agent codeur en boucle*. **Écarté** — un agent unique hallucine dès que la tâche ou la
> codebase devient grande. Le design retenu ci-dessous est **multi-agents** : analyse →
> découpage méthodique → délégation séquentielle à des **workers frais**, chacun avec un
> **contexte crafté** pour son action précise.

## 0. Décisions

| # | Décision | Choix |
|---|---|---|
| D1 | Forme générale | **Orchestrator-workers** : le router décompose + délègue ; il n'écrit pas le code lui-même |
| D2 | Anti-hallucination | **Contexte frais + crafté par worker** (1 sous-tâche = 1 délégation = `messages:[system, briefing]`) |
| D3 | Boucle | **PDCA au cœur** (Plan-Do-Check-Act) ; le `todo.json` est l'**artefact vivant** de la boucle |
| D4 | Levier #1 | **Qualité du découpage** = capacité à résoudre ; **révision du TODO à chaque retour worker = clef de la qualité de sortie** |
| D5 | Suivi | **`todo.json` VIVANT possédé par l'orchestrateur**, **réinjecté à chaque tour au router seul** |
| D6 | Décomposition | Router **plan-first** (`planner` dormant laissé tel quel — le router robuste suffit) |
| D7 | Briefing | but précis + `support_files` (fichiers workspace) + **mémoire partagée** (`manage_user_memory`) |
| D8 | Modèle orchestrateur | **`qwen3:14b`** sur le router (robuste pour codebases + révision du plan ; **deepseek v4** prendra le relais) |
| D9 | Modèle codeur | **`qwen3-coder:latest`** sur `code-runner` (worker code) |
| D10 | Parallélisme | **Non** — séquentiel (1 GPU). Le gain est l'hygiène de contexte, pas le parallélisme |
| D11 | Remontée worker | Le worker **propose** des MAJ de plan via `report_back.suggested_todo_updates` ; l'orchestrateur reste **seul writer** du `todo.json` (propose / dispose) |

## 1. Contexte & exigence
On veut un « Claude Code local » sur nos briques (Ollama, sandbox Docker, qwen-coder). Exigence
issue de l'expérience : **l'analyse, le découpage méthodique, et la délégation (même
séquentielle) à plusieurs LLM avec un contexte soigneusement crafté par action sont cruciaux** ;
un agent unique dérive sur les grosses tâches. Les workers ont accès aux **fichiers du
workspace** et à la **mémoire partagée**. Contrainte : infra modeste (1 GPU, modèles servis
séquentiellement, petits modèles).

## 2. État des lieux (vérifié sur la base LIVE + le code — pas les fichiers de migration)
- **Boucle** `orchestrator_v2._run_agent_loop` : tours natifs Ollama ; `delegate_to` /
  `report_back` / `ask_human` interceptés. Délégation récursive mais **strictement séquentielle**
  (`spawn_subagent`, 1 enfant à la fois ; `MAX_DEPTH=5`). **Chaque enfant démarre sur un contexte
  FRAIS `messages=[system, briefing]`** et renvoie un `SubResult` structuré (summary / files /
  confidence). **`delegate_to` prend déjà `briefing` (requis) + `support_files` (option) ;
  `report_back` renvoie `summary` + `files_produced` + `confidence` (low/med/high).** ⇒ **les
  primitives du PDCA existent déjà** (DO = briefing crafté + support_files ; CHECK =
  summary/confidence) ; elles sont juste sous-exploitées, sans plan ni suivi pour les piloter.
- **Décomposition aujourd'hui** = paradigme `plan_before_complex_action` (**plan dans le canal
  *thinking*, aucun artefact durable**) + `strategist` (actif) qui découpe les briefs
  **exploratoires de RECHERCHE** en 3-7 axes (paradigmes `strategist_first` /
  `strategist_decomposition_discipline` ; promet une parallélisation que l'orchestrateur ne fait
  jamais). ⚠️ **Domaine distinct** du coding : on ne surcharge PAS `strategist` ; la décompo coding
  passe par `todo_write` + PDCA (séquentiel, pas de fausse promesse de parallélisme).
- **Embryons TODO morts (vérifié : bancals, NON récupérables → à NETTOYER, pas à raviver)** :
  `db.py` définit `set_task_class` / `update_conversation_phase` / `get_task_class` + un
  `INSERT INTO conversation_phases` — mais **la table `conversation_phases` est DROPPÉE** (ce code
  planterait) et **aucun n'est appelé** (un seul commentaire mort en `turn_runner.py:158`). Colonnes
  `task_class`/`current_phase` (table `conversations`) **jamais écrites**. `workspace_create_file.py`
  / `workspace_append.py` **réservent `plan.md` « voir `plan_writer.py` »** → `plan_writer.py`
  **n'existe pas**, et le message d'erreur cite **`manage_todo_list`** (outil supprimé). Agent
  **`planner` (id 14, INACTIF)** = même lignée bancale (mission « écrire plan.md, ne pas exécuter »).
  → On part **propre** (`todo.json` à la racine conv, hors workspace ⇒ zéro collision avec `plan.md`)
  et on **purge** ces fantômes (cf. §7).
- **Substrat coding** : `code-runner` (gemma4:26b) édite via `workspace_str_replace` (édition
  ciblée à la Claude Code) + teste dans `bash_sandbox` Docker (network=none ; whitelist
  bash/cat/echo/jq/ls/python3 ; conteneur par conversation) ; capé à 3 itérations internes.
  `code-fetcher` (lookup : github/stackoverflow/pypi/web_fetch). 4 stratégies de compaction
  (snip/microcompact/collapse/autocompact) déjà en place.
- **Mémoire partagée** : `manage_user_memory` (par-utilisateur, isolée) — actuellement grantée à
  jean-michel, code-fetcher, news-specialist, strategist. **PAS** à code-runner.
- **Modèles LIVE** (`ollama list`) : **`qwen3-coder:latest` (18 Go) installé mais NON câblé** ;
  aussi `qwen3:14b`, `gemma4:26b`, `gemma4:latest`, `granite4.1:8b`. Modèle/agent via
  `agents.model_override` (résolu dans `load_agent_spec_v2`).
- **Roster live (16 agents) & rôles dans le PDCA** : orchestrateur = **`jean-michel`** (role
  `router` ; délègue déjà à 13 agents → **possède le TODO**, → qwen3:14b) ; worker code =
  **`code-runner`** (gemma4:26b→**qwen3-coder** ; a `bash_sandbox` + workspace ; **peut déjà déléguer
  à `code-fetcher`** ; pas encore `manage_user_memory`) ; lookup = **`code-fetcher`** (github / SO /
  pypi / web_fetch + mémoire partagée) ; synthèse possible = **`synthesizer`** (role `finalizer`).
  `strategist` (actif) → décompo RECHERCHE seulement ; `planner` (inactif) → **laissé dormant**.
  Convention outils = snake_case verbe (`workspace_create_file`, `delegate_to`, `report_back`…) ⇒
  **`todo_write` s'y intègre naturellement**.

## 3. Diagnostic
Le pivot v2 a **retiré la structure** (todo, planner, phases) en pariant sur « le LLM planifie
dans son thinking ». OK pour un modèle frontière. Mais : (a) **nos petits modèles locaux
dérivent** sans plan durable relu ni suivi ; (b) surtout, **on laisse trop de travail à un seul
contexte** — le router improvise ses délégations, les briefings sont pauvres, et un worker (ou
le router) qui porte une grosse tâche + une grosse codebase **hallucine**. La brique
anti-hallucination (`spawn_subagent` → contexte frais) existe mais n'est ni pilotée par une
décomposition explicite, ni nourrie de briefings craftés, ni suivie par un TODO.

## 4. Meilleures pratiques (recherche + sources fournies)
- **Anthropic « Building Effective Agents »** : le coding = **orchestrator-workers**
  (décomposition *dynamique*, on ne sait pas à l'avance combien de fichiers/étapes). Mantra :
  commencer simple, structurer seulement si le simple échoue. → conforte le multi-agents.
- **Plan-and-Execute > ReAct** sur le multi-étapes : planifier d'abord puis exécuter pas-à-pas →
  moins d'appels LLM, moins de dérive. Décisif pour petits modèles.
- **Fuite du code source Claude Code** ([ccleaks.com/architecture] ; article « 11 layers ») :
  - Le **system prompt (instructions + tools + context + memory) est reconstruit À CHAQUE TOUR**
    → valide **réinjecter le TODO à chaque tour** (un petit modèle ne le relit pas seul).
  - **Hooks = l'API d'extension** → on se branche sur le hook `PreLLMCall` existant.
  - ⚠️ **Divergence assumée** : « subagents partagent le prompt-cache → parallélisme ~gratuit ».
    **Ne s'applique PAS** chez nous (Ollama 1 GPU = séquentiel, pas de cache partagé). On délègue
    quand même à **plusieurs LLM** — mais pour l'**hygiène de contexte**, pas pour la vitesse.

## 5. Design retenu — « Analyser → Décomposer → Déléguer (frais, crafté) → Suivre → Synthétiser »

**Principe directeur (anti-hallucination)** : *aucun agent ne porte la grosse tâche + la grosse
codebase à la fois.* L'orchestrateur découpe ; chaque sous-tâche part vers un **worker frais**
avec **uniquement le contexte crafté pour elle**.

### La boucle au cœur : PDCA (Plan-Do-Check-Act)
L'orchestrateur (router, **qwen3:14b**) n'écrit pas le code : il fait tourner une boucle PDCA dont
le **`todo.json` est l'artefact vivant**. **La qualité du découpage est le levier #1** sur la
capacité à résoudre ; **la révision du TODO à chaque retour de worker (revise / append / modify /
reorder / retry) est la clef de la qualité de sortie** — ce n'est pas la liste initiale qui compte,
c'est sa réécriture continue.

- **PLAN** — analyser la demande, *regarder les sources* (lire le workspace / la codebase, déléguer
  un lookup à `code-fetcher` si besoin), puis **décomposer** en sous-tâches scopées via
  `todo_write(goal, items)` (3-7 items, un seul `in_progress`). Lit aussi la mémoire partagée pour
  cadrer.
- **DO** — déléguer **la** sous-tâche courante à un **worker frais** : `delegate_to(worker,
  briefing, support_files)`. Briefing crafté = but précis + contraintes + sortie attendue +
  résultats amont utiles ; `support_files` = fichiers workspace pertinents ; le worker lit la
  mémoire partagée si besoin. Contexte frais `[system, briefing]` → focalisé, peu d'hallucination.
- **CHECK** — le worker **vérifie son travail** (écrit + **teste dans le sandbox**) et renvoie un
  `SubResult` (résumé + fichiers + confiance ; pas le travail brut → le router reste léger).
  L'orchestrateur **évalue** : succès ? régressions ? le retour révèle-t-il une contrainte ou du
  travail non prévu ? Le worker peut **remonter un besoin de plan** via le champ optionnel
  `report_back.suggested_todo_updates` (prérequis / découpe / étape manquante / blocage), exprimé en
  termes de *travail* — **il ne voit pas le TODO** ; c'est l'orchestrateur qui le traduit en items.
- **ACT** — **réécrire le TODO vivant** selon le retour **et les `suggested_todo_updates` remontées** :
  marquer *done*, **ou** insérer de nouveaux items, **ou** re-scoper / réordonner les items restants,
  **ou** re-déléguer (retry) en cas d'échec. **L'orchestrateur reste le seul writer du `todo.json`**
  (worker = propose, orchestrateur = dispose). Puis reboucler sur **DO** (prochain item). Le TODO
  révisé est **réinjecté à chaque tour** dans le contexte du router (recap) pour rester méthodique.

Quand tout est *done* **et vérifié** → **synthèse** (router, ou délégation à `synthesizer`).

### Les briques (minimales, réutilisent l'existant)
1. **`todo.json`** (racine conv, hors quota workspace) : `{goal, items:[{id, text, status:
   pending|in_progress|done}]}`, **au plus un `in_progress`**. **Plan VIVANT** de l'orchestrateur :
   réécrit à chaque **ACT** (revise / append / modify / reorder), pas une checklist figée.
2. **Outil `todo_write(goal, items)`** (`tools/todo_write.py`) — **remplace toute la liste**
   (idempotent → revise/append/modify/reorder en un appel) ; refuse >1 `in_progress` (`tool_error`
   correctif) ; écriture atomique. **Nom ≠ `manage_todo_list`** (un test interdit ce littéral).
   **Granté au router** (jean-michel).
3. **Recap réinjecté à chaque tour — AU ROUTER SEULEMENT** (pas aux workers, qui doivent rester
   focalisés sur leur briefing) : hook **`PreLLMCall`** (qui mute déjà `messages`) →
   `prompts.render_todo_recap(todo)` → message `[TODO-RECAP]` (`[x]/[>]/[ ]` + « Next action »),
   **rafraîchi** (on retire l'ancien → pas d'accumulation), injecté **seulement si `todo.json`
   existe ET boucle principale** (`is_main_agent`). *Confirmé par la source CC* (cf.
   [02_claude_code_patterns.md](02_claude_code_patterns.md) §3) : CC réinjecte aussi en
   `<system-reminder>` *user* `isMeta` — mais **throttlé** (~10 tours sans `TodoWrite`) car modèle
   frontière. **Nous (petits modèles qui dérivent) : chaque tour, rafraîchi** (<150 tokens) ;
   throttle envisageable plus tard si le coût gêne.
4. **Paradigme PDCA bindé au router** (anglais) : « **PLAN**: look at the sources, then
   `todo_write` a 3-7 step plan. **DO**: delegate ONE step to a fresh worker with a precise briefing
   + `support_files`. **CHECK**: require the worker to test/verify; evaluate its report (it may
   surface `suggested_todo_updates`). **ACT**: *rewrite the TODO from the feedback + those
   suggestions* (mark done, or add / modify / reorder items, or retry on
   failure) before the next step. Never write code yourself. » **Pas de hard-gate** (pas de
   résurrection de `set_task_class` détesté → dégradation gracieuse). *CC fait pareil* : le
   « un seul `in_progress` » y est **prompt-only, zéro garde code** (cf. [02] §3) → on valide.
5. **Workers + accès** : `code-runner` (→ **qwen3-coder**) pour le code ; `code-fetcher` pour le
   lookup ; spécialistes existants. **Granter la mémoire partagée** (`manage_user_memory`, lecture)
   aux workers coding (code-runner). Workspace : déjà granté. *Idée CC* (cf. [02] §4) : par worker,
   **filtrer le contexte** (ne donner que le pertinent) et **restreindre son pool d'outils** →
   renforce le briefing crafté (« donner peu, mais juste »).
6. **Modèles** : **orchestrateur (router jean-michel) `model_override='qwen3:14b'`** — robuste pour
   intervenir sur des codebases entières et pour la **révision du plan** (deepseek v4 prendra le
   relais plus tard) ; **worker code `code-runner.model_override='qwen3-coder:latest'`**. ctx-window
   via env `JEANMICHEL_CTX_WINDOW_qwen3_14b` et `..._qwen3_coder_latest` (depuis `ollama show`). Les
   petites réflexions tournent très bien sur qwen3:14b ; le coût est le **swap de modèles** sur
   1 GPU (granite dispatch → qwen3:14b orchestrateur → qwen3-coder worker), cf. §8.
7. **(Option, non retenue)** `planner` réveillé en décomposeur dédié : superflu maintenant que
   l'orchestrateur est robuste (qwen3:14b fait PLAN **et** ACT lui-même). Laissé dormant ; à
   reconsidérer seulement si le router-plan-first sature sur de très gros chantiers.

## 6. Ce qu'on écarte (et pourquoi)
- **Agent codeur unique en boucle** : hallucine sur grosse tâche/codebase (retour d'expérience).
- **Essaim parallèle** : le « 5 agents = coût de 1 » de Claude Code repose sur le cache partagé,
  **inexistant** sur Ollama 1 GPU. On délègue à plusieurs LLM mais en séquentiel.
- **Hard-gates / résurrection de `set_task_class`** : MUST en cascade, détestés.
- **Régénération de `plan.md` par l'orchestrateur** (le chemin baroque supprimé) ; machine à états
  sur `task_class`/`current_phase` (laissés dormants).

## 7. Points d'intégration (fichiers / fonctions)
- **Nouveau** : `tools/todo_write.py` ; `prompts.render_todo_recap` ; `tests/v2/test_todo.py`.
- **Réutilisé** : `delegate_to(agent_code, briefing, support_files)` (DO, tel quel) ; `spawn_subagent`
  (contexte frais, tel quel) ; **`report_back` étendu** d'un champ optionnel `suggested_todo_updates:
  list[str]` (CHECK + remontée worker — cf. D11 ; la consigne worker vit dans sa *description*).
- **Édités** : `tools/__init__.py` (registry) ; `hooks.py` (`PreLLMCall` + `build_hook_registry`
  gagnent `conv_folder` + un flag `is_main_agent` ; injection du recap après compaction, **main
  agent only**) ; `orchestrator_v2.py` (les 2 sites de construction des hooks passent
  `conv_folder` + `is_main_agent` — déjà en scope) ; `tools/report_back.py` (champ optionnel
  `suggested_todo_updates` + `validate_report_back_args` + consigne worker dans la description) ;
  `config.py` (slot `CODER_MODEL` optionnel).
- **Nettoyage des fantômes (S1, ménage propre — cf. §2)** : supprimer de `db.py` les helpers morts
  `set_task_class` / `update_conversation_phase` / `get_task_class` + le `INSERT INTO
  conversation_phases` (table droppée → code mort/cassé, zéro appelant) ; **retirer la réservation
  `plan.md`** dans `workspace_create_file.py` / `workspace_append.py` (réf. au `plan_writer.py`
  inexistant + message citant le défunt `manage_todo_list`) ; virer le commentaire mort
  `turn_runner.py:158`. *(Colonnes `task_class`/`current_phase` : laissées — drop de colonne SQLite
  coûteux/risqué ; simplement ignorées. `planner` inactif : laissé dormant.)*
- **BDD** : `migrate_120_coding_decomposition.sql` — **`jean-michel.model_override='qwen3:14b'`** +
  grant `todo_write` à jean-michel ; paradigme `pdca_decompose_delegate_revise` bindé à
  jean-michel ; grant `manage_user_memory` à `code-runner` ;
  `code-runner.model_override='qwen3-coder:latest'`. **Mirroir dans `db/schema.sql` + appliqué à la
  base live** (convention). MAJ des assertions de compte (paradigmes 117→…) + ajout de migrate_120
  aux chaînes de test.

## 8. Risques / vérité crue
- **Plus de délégations = plus d'appels LLM séquentiels** (latence sur 1 GPU). Assumé : on troque
  de la vitesse contre de la fiabilité. Lever : granularité de décomposition raisonnable (pas
  trop fine).
- **Swap de modèles sur 1 GPU** : un tour coding peut charger granite (dispatch) → qwen3:14b
  (orchestrateur) → qwen3-coder 18 Go (worker), **séquentiellement**. Surcoût de chargement assumé
  (fiabilité > vitesse). Lever : garder le dispatch léger, ne pas multiplier les modèles workers.
- **Qualité du découpage = levier #1 (= qualité de l'orchestrateur).** Si la décomposition ou sa
  **révision** est mauvaise, le tour patine. Levers : modèle robuste (qwen3:14b) + texte du
  paradigme PDCA insistant sur l'**ACT** (réécriture du plan). Pas de gate dur : dégradation
  gracieuse, pas de blocage.
- **Contexte du router** : grandit avec le TODO + les `SubResult` (résumés, pas le travail brut) ;
  compaction + recap rafraîchi le gardent léger.
- **Recap = coût tokens/tour** : ~1 ligne/item (<150 tokens), rafraîchi, jamais accumulé.
- **ctx-window qwen3-coder** : ne pas annoncer plus qu'Ollama ne sert (sur-allocation WORKING →
  troncature silencieuse) — régler depuis `ollama show`.
- **Discipline dual-write** `schema.sql` ↔ migration (le test d'idempotence compare les deux).
- Les ~521 tests doivent rester verts (unitaires sous `MockClient` → le changement de modèle
  n'impacte que l'E2E).

## 9. Sprints (chacun livrable + testable)
- **S1 — Infrastructure TODO + recap + ménage (code pur, sans BDD ni modèle).** `todo_write` +
  registry + `render_todo_recap` + hook `PreLLMCall` (recap **main-agent only**, no-op sans
  `todo.json`) + threading `conv_folder`/`is_main_agent` ; **`report_back` étendu** (`suggested_todo_updates`
  optionnel + validation + description — D11) ; **+ purge des fantômes** (db.py morts, réservation
  `plan.md`, commentaire mort — cf. §7). Tests : écriture / refus >1 in_progress / format recap /
  injection + rafraîchissement / **no-op sans todo.json** / **pas de recap pour un sous-agent** /
  `report_back` accepte+valide `suggested_todo_updates`. *DoD : suite verte, tours non-coding
  inchangés, fantômes supprimés.*
- **S2 — Orchestration + workers (migration, comportemental).** migrate_120 :
  `jean-michel`→**qwen3:14b** + grant `todo_write` ; paradigme PDCA `pdca_decompose_delegate_revise`
  (router) ; grant `manage_user_memory` à code-runner ; `code-runner`→**qwen3-coder** ; ctx-window
  env (les deux). Mirror schema + counts + apply live. Tests : grants/override/paradigme résolus
  (chaîne ET schema), dead-literal guard. *DoD : `load_agent_spec_v2('jean-michel')`→qwen3:14b +
  `todo_write` ; `('code-runner')`→qwen3-coder.*
- **S3 — E2E coding multi-étapes + réglage.** Tâche réelle non triviale (petite feature
  multi-fichiers), observer : décomposition (todo), délégations à contexte frais, **révision du
  todo après les retours**, artefacts vérifiés au sandbox. Régler par le seul texte du paradigme
  PDCA (modèle déjà fixé à qwen3:14b). *DoD : tâche multi-étapes complétée méthodiquement, todo
  vivant correct (un seul in_progress), révisions visibles.*

## 10. Vérification
`pytest tests/v2` vert (+ `test_todo.py`, assertions migrate_120). E2E manuel sur le daemon
(orchestrateur qwen3:14b, worker qwen3-coder) : le router émet un `todo.json` (décomposition),
délègue séquentiellement avec des briefings + `support_files`, **révise le todo après les retours**
(append/modify visibles, un seul in_progress), `[TODO-RECAP]` ne s'accumule pas et **n'apparaît PAS
dans les contextes workers**, et un **tour trivial ne crée NI todo NI recap** (rayon de souffle
nul).

## 11. Sources
- Anthropic, *Building Effective Agents* — <https://www.anthropic.com/research/building-effective-agents>
- Claude Code architecture (leak) — <https://ccleaks.com/architecture> + article « 11 layers »
  (fourni par l'utilisateur) : system prompt reconstruit chaque tour, hooks, compaction, cache de
  subagents (divergence assumée).
- Plan-and-Execute vs ReAct — synthèse de pratiques (decomposition, moins d'appels LLM).
- Méthodo Claude Code (TodoWrite/Task/plan mode), adaptée petits modèles : **réinjecter l'état**,
  invariants par **hooks**, un seul `in_progress`, marquer fait immédiatement.
