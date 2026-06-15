# To Do

> Backlog consolidé. Pour la connaissance acquise (design, audits), voir [docs/README.md](docs/README.md).

## En cours
- **Plan mode — vérif live + étape 5** : roder le cycle Plan→Approuver→Edit en code/analyse, puis analyse
  écrite de généralisation aux autres modes + patterns d'orchestration transverses (doc `docs/20260613_plan_mode/`).

## Bugs / à revérifier en live
- **Front : contexte perdu au reload / changement de conversation.** L'état `planPending` (barre d'approbation)
  et le streaming sont éphémères (armés seulement par l'event live `final`), non restaurés depuis le persistant
  → au reload/switch, un plan en attente devient inacceptable. Dériver l'état du disque au chargement
  (`todo.json` présent + dernier tour = plan non exécuté).
- **Vision / images avec cogito comme `main`** : cogito:32b n'est pas multimodal. Avant, le routeur (gemma)
  voyait les images nativement. Vérifier que le flux image marche (le main délègue bien au tool `analyze_image`
  → gemma4 ; pas d'image base64 injectée directement dans les messages de cogito qui la chokerait).
- Hallucination d'agents sur des fichiers hors workspace (probable compaction) — `conversations/2026-06-13_19-20_dfcafc75…`.
- Re-vérifier en live post-fixes (ex-« NULACHIER ») : artefacts écrits dans le workspace, plans en mode analyse
  qui répondent, appels d'outils non bloqués, mémoires visibles.

## Perf / qualité orchestration
- **Shadow-consolidation** : découplée du tour (tâche de fond post-tour + push notif) + grounding anti-GIGO
  (user/tool only) ✅ (c48cfc1). **Reste** : (a) **persistance reload** — `GET /conversations/{id}/pending-memory`
  + chargement au `select()` (sinon candidats perdus si on quitte la conv dans les ~15s) ; (b) éventuel
  **gating** de fréquence (ne pas consolider chaque tour deep).
- **Nudging tours vides de cogito** (tour assistant vide juste après un résultat d'outil → relancé par le garde,
  ~1 appel LLM en plus) : creuser la cause (prompt ? quirk modèle reasoning ?).
- **Fluidité du streaming sous famine GIL** : le worker tient le GIL pendant les sections CPU lourdes → le live
  saccade (la WS ne meurt plus, keepalive désactivé, mais l'UX peut figer un instant).
- Enforcement plan-mode `todo_write` : le modèle narre parfois le plan en prose au lieu d'appeler l'outil.

## Sélection de modèles
- **Rôle code-router / code-analyst** : remplacer qwen3:14b par mieux tenant sur 1 GPU (32 Go). Candidats, plan
  d'éval (harness `debug/eval_model.py`) et point de vigilance câblage (migration model_override) →
  [docs/20260614_model_selection.md](docs/20260614_model_selection.md).
- **Point E — rôle orchestrateur DÉDIÉ vs jean-michel chat** : déféré. Préférence = garder l'orchestrateur le
  plus déterministe possible ; re-trancher sur données réelles, après le réglage du rôle code.

## Tooling / cleanup
- **Tool set / MCP par agent** : jean-michel ne doit pas avoir les outils github ni le MCP vuetify (réservés aux
  codeurs ; vuetify pas même lancé) → quel MCP pour quel agent. Suspicion : confond *outil* et *délégation*.
- On est définitivement en v2 ? Checker si v1 sert encore ; sinon dégager v1 + docs et consolider (orchestrateur,
  tests).
- Rafraîchir le paradigm viewer/editor.
- Audit des paradigmes de tous les agents (incohérences ?).
- **meta_analyst — qualité** : `--meta-analysis` remarche (prompt → délégation explicite, commit 78055ce), MAIS
  le meta-analyst **hallucine** des noms d'agents/outils inexistants (ex. `analyst`/`researcher`, `sandbox_execute`)
  alors qu'il a `self_inspect_config`. Durcir une discipline grounding (ne référencer que des agents/outils
  RÉELS du roster ; citer les noms exacts). Idée : le lancer **périodiquement** (le vieux doc proposait un
  auto-trigger sur seuil d'échecs/ask_human) — mais sortie à **filtrer par un humain**, jamais auto-appliquer.

## Idées
- Plein de micro-LLM prédisant le prochain token sur le même contexte → triplets de précogs.


## Modeles a tester

Sources: https://www.morphllm.com/best-ollama-models

- orchestrator: `deepseek-r1:32b`   => `ollama run deepseek-r1:32b`
- code: `qwen2.5-coder:32b` (22Go VRAM at Q4_K_M) => `ollama run qwen2.5-coder:32b`
- new math and stem specialist: Phi-4 14B

## Source a recuperer pour ecriture doc

https://www.sitepoint.com/the-complete-stack-for-local-autonomous-agents--from-ggml-to-orchestration/