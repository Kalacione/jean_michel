# Audit orchestration — « plan mode » & bonnes pratiques

> Doc de capitalisation (2026-06-13). Recherche + lecture de deux repos partagés par le user
> ([KARIMO](https://github.com/opensesh/KARIMO),
> [claude-code-workflow-orchestration](https://github.com/barkain/claude-code-workflow-orchestration))
> en vue d'implémenter un **plan mode user-facing** (le router PRÉSENTE un plan pour validation humaine avant
> exécution). On note ici l'essentiel + les bonnes pratiques, et ce qu'on adopte / écarte.

## TL;DR (décisions)

- **Plan mode = un workflow, PAS un système de permissions.** On n'a pas (et on ne construit pas) d'auto-approve
  par-commande. Une **seule gate grossière** : approuver le plan entier.
- **Notre `todo.json` EST l'artefact de plan** ; nos **nudges déterministes `[ORCHESTRATOR]`** sont déjà le
  levier d'enforcement (cf. barkain « nudges, never blocks »).
- **Mécanisme retenu : turn-boundary** — le tour « plan » produit le todo puis s'arrête ; l'exécution est un
  tour séparé. Choix de fin (**Approuver & exécuter / Modifier / Affiner**) = boutons front.
- **Intensité de réflexion : ÉCARTÉE** — ne mappe pas sur des modèles chez nous (compute limité) ; au mieux
  jouerait sur les limites de récursion/itération ; probablement « pas équipés ». On garde uniquement Plan/Edit.
- **Périmètre : code (+ analyse)** ; pas chat/vocal. **Plan = défaut** pour code & analyse (discipline
  plan-first, cohérent avec la thèse : border le petit modèle à réfléchir avant d'agir).

---

## 1. Claude Code « plan mode » — l'essence

- C'est un **workflow + du prompting**, **orthogonal aux permission modes** (default / auto-accept / …). On
  l'active (Shift+Tab ×2), l'agent passe en **exploration read-only**, recherche, puis **présente un plan**
  (artefact markdown). L'humain revoit/édite, puis **approuve** (ExitPlanMode) → l'agent exécute.
- L'enforcement « read-only » est **par prompt**, pas architectural (Ronacher : plan mode est largement
  reproductible au prompt seul ; la valeur est l'**orchestration**, pas la restriction technique).
- **À retenir pour nous** : on peut faire **mieux que le prompt** pour pas cher → une **gate déterministe**
  (`PreToolUse` refuse les outils mutants quand `plan_mode`). C'est notre thèse (border la logique).

## 2. Intensité de réflexion — le pattern commun (documenté, mais écarté chez nous)

- Tous les gros fournisseurs exposent un **petit enum** : `low / medium / high / max`.
  - Claude : `effort` (adaptive thinking) ; OpenAI : `reasoning_effort` ; qwen3 : `/think`·`/no_think` +
    `thinking_budget` (soft-switch au prompt, pas de param API).
- Coût ~1× → ~10×+ tokens selon le niveau.
- **Pourquoi écarté ici** : nos modèles locaux n'ont pas la capacité de Claude ; un « niveau d'effort » ne
  mappe pas proprement sur un choix de modèle chez nous. Au mieux il jouerait sur `MAX_DEPTH` /
  `max_iterations` — mais c'est probablement une fausse bonne idée tant qu'on n'a pas le besoin avéré ni
  l'équipement. **Le choix Plan/Edit suffit.**

## 3. Plan-vs-act ailleurs (le plus simple gagne)

| Outil | Planif. | Exéc. | Approbation |
|---|---|---|---|
| **Aider `--architect`** | modèle « architecte » (raisonneur) | modèle « editor » (diffs) | implicite, par plan entier |
| **Cline / Roo « Plan & Act »** | mode Plan (read-only, discussion) | mode Act (read/write) | bascule manuelle de mode |
| **Cursor plan mode** | génère un plan markdown | exécute | **1 bouton « build the plan »** |

- **Consensus : approbation GROSSIÈRE (go/no-go sur le plan entier), jamais par-étape.** Cursor est le plus
  simple : artefact + un bouton.
- Le split deux-modèles d'Aider (planifie fort / exécute précis) est élégant mais **pas nécessaire** pour nous
  (un seul modèle par tour).

## 4. KARIMO (opensesh)

- Ajoute la **discipline plan-then-execute** par-dessus Claude Code (qui « ne coordonne pas les dépendances,
  ne détecte pas les boucles, ne récupère pas des crashes »).
- **Pipeline 3 boucles** : Research→Plan, Tasks→Review, Execute en **vagues**→Inspect. **Artifact-driven
  gates** (PRD, briefs valident avant la phase suivante).
- **Complexity routing** (Sonnet rapide / Opus dur) + détection de boucle sémantique + réconciliation git
  (checksums de branche).
- Principe : **« You are the architect, agents are the builders »** — l'humain garde revue/merge **sans
  dialogues par-commande**. ⇒ valide notre approbation grossière.
- **À piquer (backlog)** : artifact-driven gates (on a déjà todo.json) ; vagues parallèles ordonnées par
  dépendances ; détection de boucle.

## 5. barkain (claude-code-workflow-orchestration)

- Force la **délégation aux spécialistes** (on a déjà router + specialists), décompose en **phases atomiques**
  avec dépendances, exécute en **vagues** (parallèle si indépendant, séquentiel si dépendances), passe le
  contexte entre phases.
- Utilise le **plan mode natif** (EnterPlanMode/ExitPlanMode) : analyse complexité → décompose → **rend un
  graphe de dépendances** → l'utilisateur approuve → exécute (sinon fallback subagent).
- **Escalade de nudge** (au lieu de bloquer) : 1ʳᵉ violation silencieuse, 2ᵉ hint (~12 tokens), 3-4ᵉ warning
  (~25), 5ᵉ+ « strong reminder » (~55). **Compteur reset par-tour ; subagents exemptés.**
- ⇒ **C'est exactement notre mécanisme de nudge déterministe** (P1/F4). Validation forte de la direction.
- **À piquer (backlog)** : escalade graduée du nudge ; métadonnées de phases (wave/phase id/deps).

## 6. Anti-patterns (à éviter)

- **Approval fatigue** : l'approbation par-étape casse le flow → tout est approuvé sans revue. → **grossier**.
- **Plan staleness** : plan généré tôt, contexte dérive → re-planifier si trop vieux.
- **Cascade** : une étape échoue, l'agent improvise → **pause + escalade** (cf. notre F4 + `ask_human`).
- **Enforcement par prompt seul** : fragile sous pression → on ajoute la **gate déterministe**.

## 7. Ce qu'on a DÉJÀ qui mappe

- `todo.json` (goal + items, statuts) = **l'artefact de plan**. `todo_write` / `render_recap`.
- Nudges `[ORCHESTRATOR]` (PreLLMCall) + strip transitoire (persistence) = **levier d'enforcement soft**.
- `PreToolUse` (grant/dedup/depth) = **point d'ancrage de la gate déterministe** (no-mutation en plan mode).
- Worktree git isolé par conversation = bac à sable d'exécution.
- Délibération `critical-coder` / `sergent-kiss` (P5) ; dispatcher deep/trivial ; `ask_human` (+ choix).

## 8. Bonnes pratiques — à adopter MAINTENANT vs BACKLOG

**Maintenant (ce chantier)** :
- Plan mode turn-boundary + **gate déterministe no-mutation** (mieux que le prompt seul).
- Approbation **grossière** (Approuver & exécuter) + boucle de raffinement (Modifier / Affiner).
- **Édition inline du plan** dans l'UI (todo.json éditable) — supprime le cycle download/edit/upload.
- **Plan par défaut** pour code & analyse (plan-first).

**Backlog (post-analyse de généralisation)** :
- Escalade graduée du nudge (barkain).
- Vagues parallèles + métadonnées de dépendances (KARIMO/barkain).
- Détection de boucle sémantique ; re-plan auto sur staleness ; recovery git.
- Complexity-routing modèle (si un jour on a le compute).
- Intensité de réflexion (écartée — cf. §2).

---

## Sources

- Claude Code plan mode : <https://code.claude.com/docs/en/permission-modes> ;
  <https://lucumr.pocoo.org/2025/12/17/what-is-plan-mode/>
- Thinking/effort : <https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking> ;
  <https://developers.openai.com/api/docs/guides/reasoning> ; <https://arxiv.org/abs/2505.09388> (Qwen3)
- Plan-vs-act : <https://aider.chat/docs/usage/modes.html> ; <https://docs.cline.bot/features/plan-and-act> ;
  <https://cursor.com/docs/agent/plan-mode>
- Repos : <https://github.com/opensesh/KARIMO> ;
  <https://github.com/barkain/claude-code-workflow-orchestration>
- Plan-then-execute (sécurité/anti-patterns) : <https://arxiv.org/pdf/2509.08646>
