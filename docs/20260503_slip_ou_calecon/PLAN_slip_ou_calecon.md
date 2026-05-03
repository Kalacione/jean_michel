Voici l'analyse complète avec les correctifs proposés.

---

## Rapport de correctifs — 5 bugs + langue + boucles

---

### Bug 1 — Retour opaque en cas de `step budget exhausted`

**Cause :** `_run_request()` retourne `("[orchestrator] step budget exhausted", artifact)`. Le parent reçoit ce texte tel quel dans son `tool_response` et ne distingue pas un échec d'une réponse substantielle. Il peut choisir de réutiliser cet artifact comme `support_file` — ce que le comparator-specialist a fait, passant deux fichiers d'erreur au synthesizer.

**Correctif — orchestrateur Python :**

Au lieu de :
```python
return msg, artifact
```

Retourner un objet d'erreur structuré incluant les échanges humains survenus dans ce scope :

```python
exchanges_summary = ""
if self._turn_exchanges:
    exchanges_summary = "; ".join(
        f"Q: {q} → A: {a}" for q, a in self._turn_exchanges
    )

error_payload = json.dumps({
    "tool": "delegate_to",
    "agent": agent_code,
    "status": "step_budget_exhausted",
    "partial_clarifications": exchanges_summary or None,
    "error": (
        "The agent exhausted its step budget without producing a result. "
        + (f"Human clarified during this request: {exchanges_summary}" if exchanges_summary else "")
    ),
})
# Ne pas passer l'artifact d'erreur — forcer None
return error_payload, None
```

Le parent reçoit maintenant un objet structuré avec `status: step_budget_exhausted`, différenciable d'une réponse réelle. Un `artifact: None` interdit de le passer en `support_file`.

---

### Bug 2 — `_turn_exchanges` non injecté dans les sous-requêtes

**Cause :** `_turn_exchanges` est une liste d'instance (`self._turn_exchanges`) alimentée par `_handle_ask_human()`, mais `PromptContext` n'a pas de champ pour ça. Chaque appel à `_run_request()` construit un contexte aveugle à ce qui a été clarifié pendant ce tour.

**Correctif — orchestrateur + prompts.py :**

**a)** Ajouter un champ `turn_clarifications` à `PromptContext` :

```python
@dataclass
class PromptContext:
    ...
    turn_clarifications: list[tuple[str, str]]  # (question, answer)
```

**b)** Le passer dans `_run_request()` :

```python
ctx = PromptContext(
    ...
    turn_clarifications=list(self._turn_exchanges),  # snapshot au moment du call
)
```

**c)** L'injecter dans `render_system_prompt()` après le bloc `## Inbound briefing` :

```
## Prior clarifications this turn
- Q: "..." → A: "..."
(none)  # si liste vide
```

Résultat : tout agent invoqué après un `ask_human` dans le même tour voit la clarification sans qu'aucun agent parent ait à la propager manuellement dans son briefing.

---

### Bug 3 — `ask_human` consomme une itération du step budget

**Cause :** La boucle `for _step in range(MAX_STEPS_PER_REQUEST)` incrémente `_step` après chaque cycle, y compris quand le cycle entier était `ask_human` + attente humaine (~30 secondes d'I/O). L'agent perd une slot sur une opération non-LLM.

**Correctif — orchestrateur :**

Compter séparément les appels LLM (le coût réel) et les `ask_human` (I/O) :

```python
llm_steps = 0
ask_steps = 0

while llm_steps < MAX_STEPS_PER_REQUEST:
    response = self.llm.chat(...)
    llm_steps += 1
    ...
    if call.name == "ask_human":
        ask_steps += 1
        # ne pas incrémenter llm_steps pour ce cycle
        # (la réponse humaine arrive dans le même step)
```

Alternative plus simple : augmenter `MAX_STEPS_PER_REQUEST` à 20 (comme demandé) **et** ne décompter `ask_human` que comme 0.5 step, en ajoutant un `llm_steps` séparé incrémenté uniquement sur les appels LLM réels.

La solution propre : séparer `_llm_step_count` de `_ask_human_count`. La limite `MAX_STEPS_PER_REQUEST` ne s'applique qu'aux LLM steps. `ask_human` est hors compteur.

---

### Bug 4 — Aucune directive forçant un agent parent à inclure les clarifications dans un re-briefing

**Cause :** Le paradigme `briefing_contract` dit "briefings in English" mais ne mentionne pas la propagation des clarifications humaines en cas d'échec de délégation.

**Correctif — schema.sql, paradigme `briefing_contract` (id=14) :**

Ajouter à son contenu :

```
- If a delegation returns status "step_budget_exhausted" with "partial_clarifications",
  include those clarifications verbatim in the re-delegation briefing under the key
  "Known clarifications from human:".
- Never re-delegate with the exact same briefing after a failed delegation — 
  incorporate what was learned (even partial) before retrying.
```

Cela s'applique au comparator-specialist (lié au paradigme 14) et à jean-michel (aussi lié).

---

### Bug 5 — Le synthesizer n'a pas le grant `conv_read_file`

**Cause :** Table `agent_tools` : le synthesizer (id=3) n'a aucune ligne. Il reçoit des `support_files` mais ne peut pas les lire. Il "simule" la lecture en pensée → hallucination garantie.

**Correctif — `schema.sql` directement (on repartira de zéro en BDD) :**

```sql
-- Dans les seeds agent_tools, ajouter :
INSERT INTO agent_tools (agent_id, tool_code) VALUES (3, 'conv_read_file');
```

Pas de migration — modifier `schema.sql` et réinitialiser la BDD (`rm jeanmichel.db && sqlite3 jeanmichel.db < db/schema.sql`).

**Vérification secondaire :** document-builder a déjà `conv_read_file` ✓. Le comparator-specialist n'a aucun outil non-contrôle — il n'a pas besoin de `conv_read_file` car il passe les artifacts en `support_files` aux agents suivants sans lire lui-même. C'est correct.

---

### Bug 6 — Détection de boucle / réponse vide (demande nouvelle)

**Cause actuelle :** Aucune détection de répétition de tool_calls dans la boucle. Un agent peut appeler `wikipedia_search({'query': 'calecon underwear'})` 6 fois consécutives et l'orchestrateur s'en fiche jusqu'au budget exhausted.

**Correctif — orchestrateur, dans la boucle `for _step` :**

Deux mécanismes complémentaires :

**a) Déduplication des tool_calls identiques (anti-boucle) :**

On enregistre uniquement les appels ayant produit un résultat **non-erreur**. Un appel qui a échoué (erreur réseau, page introuvable) peut être légitimement retenté ; un appel qui a réussi ne doit jamais être répété à l'identique.

```python
_successful_calls: set[str] = set()  # appels ayant produit un résultat utilisable

# Après exécution d'un outil natif, avant d'ajouter à tool_responses :
call_fingerprint = f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
if call_fingerprint in _successful_calls:
    tool_responses.append(json.dumps({
        "tool": call.name,
        "error": (
            "Duplicate call detected. This exact call already produced a result earlier "
            "in this request. Re-running it will not change the output. "
            "Use the previous result or reformulate your query with different arguments."
        ),
    }))
    continue

# result = spec.handler(...) — exécution normale
if not (isinstance(result, str) and '"error"' in result):
    _successful_calls.add(call_fingerprint)  # enregistrer seulement si succès
```

**b) Escalade orchestrateur si step budget épuisé ET au moins un `ask_human` a eu lieu :**

Si à la sortie de la boucle il y a eu ≥1 `ask_human` **et** budget épuisé (sans `return_to_user`), c'est signe d'un agent qui tourne en rond malgré une clarification. L'orchestrateur peut déclencher un `ask_human` synthétique de l'orchestrateur lui-même :

```python
# Fin de boucle (budget épuisé)
if seen_ask and self._turn_exchanges:
    # L'agent a reçu une clarification mais n'a pas su en tirer parti
    # → escalade vers l'humain avec un résumé du blocage
    escalation_q = (
        f"The {agent_code} agent was unable to complete the task even after "
        f"your clarification. It searched exhaustively but found no usable result. "
        f"Can you rephrase or provide an alternative?"
    )
    answer = self.ask_human_callback(question=escalation_q, why="step budget exhausted after clarification")
    ...
```

Ou plus simplement : retourner un `tool_response` d'erreur structuré `status: step_budget_exhausted` au parent (Bug 1 corrigé), et laisser le parent décider s'il escalade via `ask_human` — ce qui est plus propre car c'est le LLM parent qui formule la question plutôt que l'orchestrateur.

---

### Bug 7 — Incohérence de langue dans les briefings inter-agents

**Localisation :** Plusieurs endroits.

#### Problème 1 — Les briefings contiennent "The final answer must be presented in French"

Exemples observés :
- jean-michel → comparator : `"The final answer must be presented in French"`
- comparator → wikipedia : `"The final answer must be presented in French"`
- comparator → wikipedia (2ème appel) : `"Gather factual information on the differences... The final answer must be presented in French"`

C'est une **fuite de directive langue dans un canal inter-agent**. Le wikipedia-specialist reçoit la consigne de répondre en français et l'applique au mauvais niveau : il cherche `"calecon sous-vêtements homme"` sur Wikipedia (en français) au lieu de `"boxer underwear"` en anglais.

La règle est déjà dans l'OUTPUT CONTRACT hardcodé dans prompts.py :
```
Inter-agent briefings: English. Human-facing output: see ## Human detected language.
```

Mais elle n'est pas respectée par les LLMs qui génèrent les briefings.

**Correctif — paradigme `briefing_contract` (id=14) :**

Ajouter explicitement :

```
- NEVER include language instructions ("reply in French", "the answer must be in French")
  in a briefing. The receiving agent knows which language to use from its own system prompt.
  Including language instructions in briefings contaminates inter-agent searches
  (e.g. causes Wikipedia searches in the wrong language).
```

Et dans la description de l'outil `delegate_to` (dans prompts.py, `_DELEGATE_TO`) :

```
"briefing": {
    "type": "string",
    "description": (
        "Mission text in English. Do NOT include language instructions — "
        "the receiving agent handles output language automatically."
    ),
}
```

#### Problème 2 — La directive "detected language" ne distingue pas le travail interne de la sortie humaine

La ligne dans `render_system_prompt()` :

```python
f"Detected language — use for ALL human-facing output "
f"(return_to_user answer, ask_human question and why): {ctx.detected_language}\n\n"
```

La directive est correcte dans son intention mais insuffisamment précise. Elle dit "human-facing output" mais ne précise pas explicitement que **tout le reste** (raisonnement, requêtes d'outils, briefings émis vers d'autres agents) doit rester en anglais. Le LLM, voyant la langue détectée en haut du prompt, l'applique trop largement — y compris à ses requêtes Wikipedia.

**⚠️ À ne PAS modifier :** La directive de langue détectée reste intacte et s'applique à tous les agents à toutes les profondeurs. Elle est nécessaire pour que les spécialistes puissent poser leurs `ask_human` dans la langue de l'humain, quel que soit leur niveau de récursion.

**Correctif — `prompts.py`, `render_system_prompt()` :**

Ajouter une ligne **immédiatement après** la directive de langue détectée pour expliciter la règle de travail interne :

```python
f"Detected language — use for ALL human-facing output "
f"(return_to_user answer, ask_human question and why): {ctx.detected_language}\n"
f"Working language for everything else (reasoning, tool queries, "
f"briefings to other agents): English only.\n\n"
```

Le contrat devient explicite :
- `ask_human` → langue humaine détectée ✓
- `return_to_user` → langue humaine détectée ✓  
- Raisonnement interne (thinking) → anglais
- Requêtes d'outils (`wikipedia_search`, etc.) → anglais
- Briefings `delegate_to` → anglais (déjà dans l'OUTPUT CONTRACT, maintenant répété ici au plus près de la directive)

#### Problème 3 — `wikipedia_search_strategy` (paradigme 26) dit de traduire en anglais, mais la directive "detected language" la contredit

Le paradigme dit :
> *"If the entity name is not in English, translate it to its English equivalent before forming the search query"*

Mais le prompt système dit :
> *"Detected language — use for ALL human-facing output"*

L'agent est tiraillé entre deux règles contradictoires. La première dit "traduis en anglais", la seconde dit "utilise le français". Le LLM résout l'ambiguïté en faveur de la directive la plus récente et la plus saillante dans le prompt (le bloc `## Human` est haut dans le prompt, le paradigme `wikipedia_search_strategy` est plus bas).

**Correctif — renforcer le paradigme `wikipedia_search_strategy` (id=26) :**

Ajouter en tête de son contenu :

```
- Wikipedia uses English as its default edition. All search queries MUST be in English,
  regardless of the detected human language. This takes precedence over any language
  directive in this prompt.
```

---

### Résumé des correctifs par composant

| Composant | Changement |
|---|---|
| `orchestrator.py` | Retour structuré `{status, partial_clarifications}` au lieu de string brute en cas de step budget exhausted |
| `orchestrator.py` | Step budget compte uniquement les appels LLM — `ask_human` hors compteur |
| `orchestrator.py` | `_successful_calls: set[str]` — déduplication des tool calls ayant déjà produit un résultat non-erreur |
| `orchestrator.py` + `prompts.py` | `PromptContext.turn_clarifications` → bloc `## Prior clarifications` dans le prompt système |
| `prompts.py` | Ajouter ligne "Working language for everything else: English only" immédiatement après la directive de langue détectée (la directive de langue elle-même reste inchangée) |
| `prompts.py` — `_DELEGATE_TO` description | Ajouter "Do NOT include language instructions in briefings" |
| `schema.sql` — paradigme `briefing_contract` (id=14) | Interdire les directives de langue dans les briefings + propager `partial_clarifications` en cas de re-délégation |
| `schema.sql` — paradigme `wikipedia_search_strategy` (id=26) | Ajouter en tête : "All queries MUST be in English, this takes precedence over any language directive" |
| `schema.sql` — seeds `agent_tools` | `INSERT INTO agent_tools VALUES (3, 'conv_read_file')` — synthesizer |
| `schema.sql` + `rm jeanmichel.db` | Pas de migration — reconstruire la BDD depuis le schéma modifié |