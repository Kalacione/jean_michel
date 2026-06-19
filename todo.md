# To Do

> Backlog consolidé. Pour la connaissance acquise (design, audits), voir [docs/README.md](docs/README.md).

## Plan / todo — reste
- **Steering en cours de tour** : avec le multi-plan, pouvoir réorienter pendant l'exécution d'un plan.
  Prend un chemin d'analyse en cours de plan ; à creuser.
- **Enforcement plan-mode `todo_write`** : le garde mode-PLAN exige déjà `plan_write` ; reste le glissement où le
  modèle narre la todo en prose au lieu d'appeler l'outil.


## consolidation

- c'est con de prefixer `workspace` pour ``` "p3": {
      "plan_file": "workspace/plan_p3.md",
      "status": "pending",
      "approved": false
    }``` normalement les load/ write/read sont forcement limites au dossier workspace, j;ais peur qu'nu agent essaye de lire `workspace/workspace/fichier.md`

## Tooling / cleanup
- **Tool set / MCP par agent** : jean-michel ne doit pas avoir les outils github ni le MCP vuetify (réservés aux
  codeurs ; vuetify pas même lancé) → quel MCP pour quel agent. Suspicion : confond *outil* et *délégation*.
- Audit des paradigmes de tous les agents (incohérences ?).



## Idées
- Plein de micro-LLM prédisant le prochain token sur le même contexte → triplets de précogs.

## Modèles à tester
Sources : https://www.morphllm.com/best-ollama-models
- orchestrator : `deepseek-r1:32b` (`ollama run deepseek-r1:32b`)
- code : `qwen2.5-coder:32b` (22 Go VRAM @ Q4_K_M)
- math/STEM : Phi-4 14B
- via claude code : https://huggingface.co/collections/zai-org/glm-52 (MIT, pas de version quantifiée, 256 Go VRAM mini)
- `magistral:24b` (date un peu)
