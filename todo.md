# To Do

> Backlog consolidé. Pour la connaissance acquise (design, audits), voir [docs/README.md](docs/README.md).

## Plan / todo — reste
- **Steering en cours de tour** : avec le multi-plan, pouvoir réorienter pendant l'exécution d'un plan.
  Prend un chemin d'analyse en cours de plan ; à creuser.
- **Todo multiple** (par-id, comme les plans) : aujourd'hui les **plans sont multiples** (`workspace/plan_<id>.md`,
  supersede déterministe au re-plan, historique en lecture seule dans l'UI) mais le **todo reste unique**
  (`todo.json` à la racine, le courant écrase). Cible : un todo par demande **et** le todo comme outil de
  **suivi de progression des subagents** sur les opérations longues. (Déféré le 2026-06-17 — « on a plus urgent ».)
- **Enforcement plan-mode `todo_write`** : le garde mode-PLAN exige déjà `plan_write` ; reste le glissement où le
  modèle narre la todo en prose au lieu d'appeler l'outil.

## Bugs / à revérifier en live
- **Plan mode : plan narré en « message »** — en mode plan, le modèle streame parfois le plan dans le canal
  content (visible) avant `plan_write` ; jeté à l'appel d'outil (non persisté), donc cosmétique. Piste : ne pas
  rendre le stream content en mode plan (le front connaît `isPlan` ; ou le backend ne l'émet pas).
- **Stop + garde-fou boucle** (livrés R5 : Stop ferme la connexion Ollama + annule pendant les tool calls ;
  garde-fou sans-progrès conclut seul) → à **valider en live** (Stop d'une action longue pas encore testé).
- Hallucination d'agents sur des fichiers hors workspace (probable compaction) — `conversations/2026-06-13_19-20_dfcafc75…`.
- Re-vérifier en live : artefacts écrits dans le workspace, plans en mode analyse qui répondent, appels d'outils
  non bloqués, mémoires visibles.
  - erreurs frontend LaTex : ```LaTeX-incompatible input and strict mode is set to 'warn': Unrecognized Unicode character "ệ" (7879) [unknownSymbol] katex.mjs:316:49
No character metrics for 'ệ' in style 'Main-Regular' and mode 'text' katex.mjs:4738:47
“mathvariant='bold'” on MathML elements is deprecated and will be removed at a future date.```

## consolidation

- c'est con de prefixer `workspace` pour ``` "p3": {
      "plan_file": "workspace/plan_p3.md",
      "status": "pending",
      "approved": false
    }``` normalement les load/ write/read sont forcement limites au dossier workspace, j;ais peur qu'nu agent essaye de lire `workspace/workspace/fichier.md`

## Mémoire
- **Sidecars mémoire → DB** (R6, backlog) : `consolidation_state.json` + `pending_memory.json` traînent dans le
  dossier conv (le user les a repérés et râlé : « c'est quoi ce fichier de merde »). Cible :
  `conversations.consolidation_studied_msgs` (colonne) + table `memory_pending`. Migration manuelle + schema.sql.
- **Shadow-consolidation — reste** : (a) **persistance reload** — `GET /conversations/{id}/pending-memory` +
  chargement au `select()` (sinon candidats perdus si on quitte la conv dans les ~15 s) ; (b) éventuel **gating**
  de fréquence (ne pas consolider chaque tour deep). [Recouvre en partie le point sidecars→DB ci-dessus.]

## Perf / qualité orchestration
- **Nudging tours vides de cogito** (tour assistant vide juste après un résultat d'outil → relancé par le garde,
  ~1 appel LLM en plus) : creuser la cause (prompt ? quirk modèle reasoning ?).
- **Fluidité du streaming sous famine GIL** : le worker tient le GIL pendant les sections CPU lourdes → le live
  saccade (la WS ne meurt plus, keepalive désactivé, mais l'UX peut figer un instant).

## Tooling / cleanup
- **Tool set / MCP par agent** : jean-michel ne doit pas avoir les outils github ni le MCP vuetify (réservés aux
  codeurs ; vuetify pas même lancé) → quel MCP pour quel agent. Suspicion : confond *outil* et *délégation*.
- On est définitivement en v2 ? Checker si v1 sert encore ; sinon dégager v1 + docs et consolider (orchestrateur, tests).
- **MAJ `docs/PROMPT_SKELETON.md`** : remettre à jour la structure du prompt avec l'état v2 actuel (noté au ménage docs).
- Rafraîchir le paradigm viewer/editor.
- Audit des paradigmes de tous les agents (incohérences ?).
- **meta_analyst — qualité** : `--meta-analysis` remarche (commit 78055ce), MAIS le meta-analyst **hallucine** des
  noms d'agents/outils inexistants (ex. `analyst`/`researcher`, `sandbox_execute`) alors qu'il a
  `self_inspect_config`. Durcir le grounding (ne référencer que des agents/outils RÉELS du roster ; noms exacts).
  Idée : le lancer **périodiquement** (auto-trigger sur seuil d'échecs/ask_human) — mais sortie **filtrée par un
  humain**, jamais auto-appliquée.


## Idées
- Plein de micro-LLM prédisant le prochain token sur le même contexte → triplets de précogs.

## Modèles à tester
Sources : https://www.morphllm.com/best-ollama-models
- orchestrator : `deepseek-r1:32b` (`ollama run deepseek-r1:32b`)
- code : `qwen2.5-coder:32b` (22 Go VRAM @ Q4_K_M)
- math/STEM : Phi-4 14B
- via claude code : https://huggingface.co/collections/zai-org/glm-52 (MIT, pas de version quantifiée, 256 Go VRAM mini)
- `magistral:24b` (date un peu)
