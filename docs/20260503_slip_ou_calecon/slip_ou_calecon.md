---

## Rapport d'analyse — Perte de clarification humaine entre agents

### Ce qui s'est passé (timeline)

| Timestamp | Agent | Événement |
|---|---|---|
| 18:08:44 | jean-michel | `ask_human` : "slip" et "calecon", trop ambigus |
| 18:10:30 | jean-michel | `human_answer` : "de sous-vêtements masculins" |
| 18:11:10 | comparator-specialist | `delegate_to` → wikipedia-specialist pour "calecon" (request `d5e166d11b38`) |
| 18:11:25 | wikipedia-specialist | `ask_human` : "calecon, faute de frappe ?" |
| 18:11:57 | wikipedia-specialist | `human_answer` : "oui, il manque une cédille, l'équivalent anglais c'est le boxer" |
| 18:12:03 | wikipedia-specialist | Pensée correcte — cherche "boxer briefs underwear" ✅ |
| 18:12:32 | wikipedia-specialist | `[orchestrator] step budget exhausted` — response renvoyée au comparator |
| 18:12:43 | comparator-specialist | `delegate_to` → wikipedia-specialist pour **"calecon"** à nouveau (request `e72ed054aecf`) |
| 18:13:04 | wikipedia-specialist | `ask_human` : "calecon, faute de frappe ?" **→ boucle** |

L'agent a bien intégré la réponse humaine (`181203346_thought.md` le confirme), mais le système a ensuite remis les compteurs à zéro.

---

### Bug 1 — L'épuisement du step budget efface silencieusement toute clarification

**Localisation :** orchestrator.py, fin de `_run_request()`, ligne `msg = "[orchestrator] step budget exhausted..."`.

Quand une requête enfant (wikipedia-specialist, `d5e166d11b38`) consomme toutes ses 8 itérations, l'orchestrateur retourne au parent :

```python
return msg, artifact
# msg = "[orchestrator] step budget exhausted within a single request."
```

Ce `msg` est ensuite empaqueté dans le `tool_response` que voit le comparator :

```json
{"tool": "delegate_to", "agent": "wikipedia-specialist", "answer": "[orchestrator] step budget exhausted..."}
```

La réponse humaine `"oui, il manque une cédille, l'équivalent anglais c'est le boxer"` est enregistrée dans `_turn_exchanges` sur l'instance Orchestrator, mais **elle n'apparaît nulle part dans le message de retour au parent**. Le comparator-specialist ne sait pas ce que l'humain a dit.

---

### Bug 2 — `_turn_exchanges` existe mais n'est jamais injecté dans les sous-requêtes

**Localisation :** orchestrator.py → `_handle_ask_human()` vs `_run_request()` ; prompts.py → `PromptContext`.

Dans `_handle_ask_human()` :
```python
self._turn_exchanges.append((question, answer))
```

Cette liste est un champ de l'instance Orchestrator, mais `_run_request()` construit un `PromptContext` **sans aucun champ pour les clarifications de tour en cours** :

```python
ctx = PromptContext(
    agent=agent, paradigms=paradigms, ...
    # ← pas de turn_exchanges ici
)
```

Et `render_system_prompt()` ne rend pas non plus ce contexte. `_turn_exchanges` n'est consommé qu'une seule fois : dans `_run_archivist()` (modes `chat`/`vocal` uniquement), pour le résumé. 

Conséquence directe : lorsque le comparator-specialist re-délègue à un nouveau wikipedia-specialist (`e72ed054aecf`), ce dernier reçoit un prompt système **identique** au premier — sans la moindre trace de ce que l'humain a déjà précisé pendant ce tour.

---

### Bug 3 — Le step budget est compté de façon uniforme, peu importe l'ask_human

**Localisation :** orchestrator.py, boucle `for _step in range(MAX_STEPS_PER_REQUEST)`.

Dans request `d5e166d11b38`, les 8 itérations se sont déroulées ainsi (approximatif) :

| Step | Action |
|---|---|
| 0 | Premier search Wikipedia (résultat vide) |
| 1 | `ask_human` → bloqué en attente humaine (~30 secondes) → réponse reçue |
| 2–7 | 6 searches successives sur "boxer briefs", jamais de `return_to_user` |

L'`ask_human` consomme une itération entière. La pause humaine n'est pas "offerte" en dehors du budget. L'agent avait donc **6 itérations restantes** pour : chercher, lire une page, extraire, rédiger et retourner. C'est trop juste pour l'agent actuel (wikipedia-specialist enchaîne search + get_page + éventuellement plusieurs articles).

Ce n'est pas un bug à proprement parler — c'est une contrainte de design — mais il amplifie les autres bugs en forçant un timeout prématuré.

---

### Bug 4 — Le comparator-specialist re-délègue avec le terme d'origine inchangé

**Localisation :** comportement LLM du comparator-specialist, amplifié par Bug 1.

Après avoir reçu `"step budget exhausted"`, le comparator-specialist (request `6ec7f7aa4089`) choisit de re-déléguer, mais **avec exactement le même briefing** ("calecon"), car :
1. Il n'a aucune information sur ce que le sous-agent a découvert (Bug 1)
2. Il n'a aucune directive lui indiquant de propager les clarifications au prochain appel

C'est à la fois une conséquence du Bug 1 (le retour d'erreur est opaque) et un manque de directive système pour ce cas de reprise.

---

### Résumé des causes racines

| # | Cause | Composant |
|---|---|---|
| 1 | Le `tool_response` renvoyé au parent en cas d'exhaustion de step budget n'inclut pas les échanges `ask_human` accumulés dans ce sous-scope | orchestrator.py → fin de `_run_request` |
| 2 | `_turn_exchanges` est collecté mais jamais injecté dans le prompt des sous-requêtes lancées par le même tour | orchestrator.py + prompts.py → `PromptContext` manque un champ |
| 3 | L'`ask_human` coûte une itération dans le step budget, réduisant la fenêtre de travail post-clarification | orchestrator.py → boucle `for _step in range(MAX_STEPS_PER_REQUEST)` |
| 4 | Aucune directive ne guide les agents parents à inclure les clarifications connues dans un re-briefing | prompts.py → OUTPUT CONTRACT / schema.sql paradigmes |

---

### Pistes de correction (sans code)

**Court terme / hauts impacts :**

1. **Injecter `_turn_exchanges` dans `PromptContext`** et les rendre dans un bloc `## Prior clarifications this turn` du prompt système pour tout appel à `_run_request` après le premier `ask_human` du tour. C'est le fix direct qui résout le Bug 2 et atténue le Bug 4.

2. **Enrichir le message d'erreur "step budget exhausted"** : inclure une mention des échanges `ask_human` survenus dans ce scope, par exemple `"[...] The human clarified: Q: [...] A: [...]"`. Le parent peut alors propager cette info dans son re-briefing.

**Architectural :**

3. **Ne pas compter `ask_human` dans le step budget** (ou le déduire d'un sous-compteur séparé) — l'attente humaine est un événement I/O externe, pas une itération LLM. Un `ask_human_step_credit` pourrait refactoriser l'idée.

4. **Ajouter un paradigme de directive** sur le comportement des agents parents en cas de délégation échouée : "Si tu re-délègues après un échec, inclus dans le briefing toute clarification connue."