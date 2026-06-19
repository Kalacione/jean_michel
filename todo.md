# To Do

> Backlog consolidé. Pour la connaissance acquise (design, audits), voir [docs/README.md](docs/README.md).

## Plan / todo — reste
- **Steering en cours de tour** : avec le multi-plan, pouvoir réorienter pendant l'exécution d'un plan.
  Prend un chemin d'analyse en cours de plan ; à creuser.
- **Enforcement plan-mode `todo_write`** : le garde mode-PLAN exige déjà `plan_write` ; reste le glissement où le
  modèle narre la todo en prose au lieu d'appeler l'outil.

## Bugs / à revérifier en live
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

## Tooling / cleanup
- **Tool set / MCP par agent** : jean-michel ne doit pas avoir les outils github ni le MCP vuetify (réservés aux
  codeurs ; vuetify pas même lancé) → quel MCP pour quel agent. Suspicion : confond *outil* et *délégation*.
- On est définitivement en v2 ? Checker si v1 sert encore ; sinon dégager v1 + docs et consolider (orchestrateur, tests).
- **MAJ `docs/PROMPT_SKELETON.md`** : remettre à jour la structure du prompt avec l'état v2 actuel (noté au ménage docs).
- **Paradigm viewer/éditeur web** (chantier séparé) : l'intégrer dans l'app web (façon `AgentsDialog`) — voir/éditer
  content + rationale + bindings/modes + un onglet « Promotions » (`pending_consolidation kind='rule'`). Le CLI
  (`admin.py` : `paradigm <code>`, `promotions`) couvre déjà la lecture + la revue des promotions.
- Audit des paradigmes de tous les agents (incohérences ?).
- **meta_analyst — auto-trigger périodique** : le lancer automatiquement sur un seuil (échecs récurrents /
  ask_human) plutôt qu'à la main ; sortie toujours filtrée par l'humain, jamais auto-appliquée. (Le grounding +
  la promotion en règles ancrées sont en place.)


## Idées
- Plein de micro-LLM prédisant le prochain token sur le même contexte → triplets de précogs.

## Modèles à tester
Sources : https://www.morphllm.com/best-ollama-models
- orchestrator : `deepseek-r1:32b` (`ollama run deepseek-r1:32b`)
- code : `qwen2.5-coder:32b` (22 Go VRAM @ Q4_K_M)
- math/STEM : Phi-4 14B
- via claude code : https://huggingface.co/collections/zai-org/glm-52 (MIT, pas de version quantifiée, 256 Go VRAM mini)
- `magistral:24b` (date un peu)
