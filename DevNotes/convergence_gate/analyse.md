# Convergence Gate — Analyse avant implémentation

## Contexte

La conversation `74e885c9de41` a produit 308 artifacts. 5 agents actifs :

| Agent             | Artifacts |
|-------------------|-----------|
| critical-thinker  | 83        |
| meta-analyst      | 55        |
| document-builder  | 52        |
| synthesizer       | 44        |
| workspace-manager | 38        |

La boucle est due à la combinaison de deux limites indépendantes qui se multiplient :

- `MAX_RECURSION_DEPTH = 5` — profondeur de délégation (`delegate_to` imbriqués)
- `MAX_STEPS_PER_REQUEST = 8` — rounds de tool calls par agent par requête

Un agent à depth 4 peut déléguer 8 fois vers depth 5, chacun faisant 8 rounds → **64 appels LLM** pour deux niveaux. Aucune condition de sortie basée sur le contenu.

---

## Le problème central

La boucle s'arrête uniquement sur des compteurs, pas sur une mesure de progression réelle.
Il n'y a pas de mécanisme pour qu'un agent dise "j'ai atteint ce que je peux atteindre, inutile de continuer".

---

## Approches rejetées

**Score de confiance (approche 3)** — le LLM hallucine les scores. Déjà testé, rejeté.

**Fingerprint du texte** — une virgule crée une fausse nouveauté. Inutilisable.

---

## Approches retenues : 1 + 2

### Approche 1 — OPEN_QUESTIONS tracking

Chaque réponse d'agent se termine par un bloc structuré :
```
OPEN_QUESTIONS:
- question A encore non résolue
- question B à vérifier
```

L'orchestrateur extrait ces listes. Si `open_questions[round N] == open_questions[round N-1]` → l'agent tourne en rond, on force la conclusion.

**Avantage** : mesure réelle de progression (on retire des questions au fil des rounds).

**Risques** :
- Émission inconsistante (l'agent oublie le bloc) → il faut un paradigme fort + fallback
- Faux vide (l'agent dit "aucune question ouverte" trop tôt) → même problème que le score de confiance ?
- Parsing fragile si le LLM reformate le bloc différemment à chaque round

### Approche 2 — Signal CONVERGED explicite

Plutôt que du texte libre parsé, un **outil de contrôle** `signal_convergence` — exactement comme `return_to_user` mais pour signaler au parent que la profondeur a atteint sa limite utile.

```json
{
  "synthesis": "résumé de la conclusion",
  "open_questions": ["ce qui reste ouvert pour le parent"]
}
```

L'orchestrateur l'intercepte comme il intercepte `return_to_user` — **déterministe, pas de parsing de texte**.

**Avantage** : fiable à 100% si l'agent l'émet.

**Risques** :
- L'agent ne l'émet pas et préfère continuer (il faut que le paradigme soit très directif)
- Convergence prématurée si le paradigme est trop agressif

---

## Architecture proposée

### Option A — Parsing texte dans la boucle

Après chaque `response.content`, scanne `OPEN_QUESTIONS:` et `CONVERGED`.

- Pro : fonctionne à tous les niveaux
- Con : parsing fragile, couplage fort entre orchestrateur et format texte

### Option B — Nouveau control tool `signal_convergence`

Comme `return_to_user`, mais avec sémantique "j'ai convergé, voici ma synthèse + ce qui reste ouvert".

- Pro : déterministe, fiable, trace claire dans les artifacts (`tool_call` visible)
- Pro : le parent reçoit `open_questions` comme donnée structurée, peut décider de continuer ou non
- Con : nécessite un paradigme fort pour que l'agent l'appelle

### Option C — Exposer `signal_convergence` uniquement à depth ≥ 2

Le tool n'est injecté dans `tools_payload` que si `depth >= 2`. Les agents peu profonds ne le voient pas, donc ne peuvent pas converger prématurément sur des tâches simples.

**→ Option B + C combinées est la plus propre.**

---

## Questions à résoudre avant d'implémenter

1. **Condition d'activation** : `depth >= 2` est-il le bon seuil ? Ou `depth >= 1` ?
   - depth 0 = jean-michel (router), depth 1 = premier spécialiste, depth 2 = sous-spécialiste
   - La boucle problématique commence à depth 2+, donc `>= 2` semble correct

2. **Que fait le parent quand le child émet `signal_convergence` ?**
   - Actuellement, `delegate_to` retourne `child_answer` (string). Il faudrait aussi retourner `open_questions` pour que le parent puisse les inclure dans sa propre convergence.
   - Cela change la structure de retour de `_run_request` — **impact fort**.

3. **Que faire si `signal_convergence` n'est jamais émis ?**
   - Fallback : `MAX_STEPS_PER_REQUEST` reste le filet de sécurité.
   - Mais sans le signal, on ne sait pas si c'est un vrai travail en cours ou une boucle.

4. **Interaction avec `archivist`** :
   - L'archivist est appelé après chaque réponse finale. Si `signal_convergence` court-circuite, l'archivist est-il toujours appelé ?

5. **Effets sur les tests existants** :
   - `test_orchestrator.py` mocke les `LLMResponse` avec des scripts. Les tests existants n'émettent pas `signal_convergence` → comportement inchangé si on rend le tool optionnel.
   - Il faut ajouter des tests spécifiques pour les chemins de convergence.

6. **Le paradigme `convergence_gate`** :
   - Doit-il être bindé à *tous* les agents ? Ou seulement aux agents analytiques (critical-thinker, meta-analyst, synthesizer) ?
   - Un agent de type `specialist` simple (wikipedia, workspace-manager) ne devrait pas avoir ce paradigme — il répond une fois et s'arrête.

---

## Plan d'implémentation envisagé

1. Ajouter `signal_convergence` dans `orchestrator.py` comme control tool (intercept dans la boucle tool_calls, même pattern que `return_to_user`)
2. Modifier `tools_payload_for_agent` pour injecter `signal_convergence` seulement si `depth >= 2`
3. Ajouter paradigme `convergence_gate` en DB (category = analyse ou meta)
4. Binder ce paradigme aux agents analytiques uniquement (critical-thinker, meta-analyst, synthesizer)
5. Tests : mock LLMResponse qui émet `signal_convergence` à depth 2

---

## Ce qu'on ne touche pas

- Les valeurs de `MAX_RECURSION_DEPTH` et `MAX_STEPS_PER_REQUEST` — ce sont les filets, pas la solution
- La logique de `delegate_to` pour les agents non-analytiques
- L'archivist
