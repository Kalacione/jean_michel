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

**Correctif — schema.sql et migration :**

```sql
-- À ajouter dans les seeds agent_tools
INSERT INTO agent_tools (agent_id, tool_code) VALUES (3, 'conv_read_file');
```

Et dans `db/migrate_NNN_synthesizer_conv_read_file.sql` :

```sql
INSERT OR IGNORE INTO agent_tools (agent_id, tool_code)
SELECT a.id, 'conv_read_file' FROM agents a WHERE a.code = 'synthesizer';
```

**Vérification secondaire :** document-builder a déjà `conv_read_file` ✓. Le comparator-specialist n'a aucun outil non-contrôle — il n'a pas besoin de `conv_read_file` car il passe les artifacts en `support_files` aux agents suivants sans lire lui-même. C'est correct.

---

### Bug 6 — Détection de boucle / réponse vide (demande nouvelle)

**Cause actuelle :** Aucune détection de répétition de tool_calls dans la boucle. Un agent peut appeler `wikipedia_search({'query': 'calecon underwear'})` 6 fois consécutives et l'orchestrateur s'en fiche jusqu'au budget exhausted.

**Correctif — orchestrateur, dans la boucle `for _step` :**

Deux mécanismes complémentaires :

**a) Déduplication des tool_calls identiques (anti-boucle) :**

```python
_seen_calls: set[str] = set()

# Dans la boucle, avant d'exécuter un outil :
call_fingerprint = f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
if call_fingerprint in _seen_calls:
    tool_responses.append(json.dumps({
        "tool": call.name,
        "error": (
            "Duplicate call detected. You already called this tool with identical "
            "arguments. The result will not change. Use the previous result or "
            "reformulate your query."
        ),
    }))
    continue
_seen_calls.add(call_fingerprint)
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

#### Problème 2 — Le wikipedia-specialist est explicitement instruit de "répondre en français" dans son prompt système

La ligne dans `render_system_prompt()` :

```python
f"Detected language — use for ALL human-facing output "
f"(return_to_user answer, ask_human question and why): {ctx.detected_language}\n\n"
```

La directive dit "ALL human-facing output". Pour un spécialiste intermédiaire, sa `return_to_user` va au parent (comparator ou orchestrateur), pas à l'humain. Pourtant il interprète cette directive comme "rédige en français" ce qui inclut ses requêtes Wikipedia.

**Correctif — prompts.py, `render_system_prompt()` :**

Différencier selon le `sender` :

```python
if ctx.sender == "human" or ctx.depth == 0:
    lang_note = (
        f"Detected language — use for ALL output "
        f"(return_to_user, ask_human): {ctx.detected_language}"
    )
else:
    lang_note = (
        f"Human language: {ctx.detected_language}. "
        f"Your return_to_user and ask_human must use this language. "
        f"All internal reasoning, tool queries, and briefings: English only."
    )
```

Ou plus directement, ajouter un paragraphe dédié au bloc `## Human` pour les agents à depth > 0 :

```
Working language: English (reasoning, tool queries, briefings to other agents).
Human-facing output only (return_to_user, ask_human): fr
```

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
| orchestrator.py | Retour structuré `{status, partial_clarifications}` au lieu de string brute en cas de step budget exhausted |
| orchestrator.py | Step budget compte uniquement les appels LLM — `ask_human` hors compteur |
| orchestrator.py | `_seen_calls: set[str]` — déduplication des tool calls identiques avec erreur explicite |
| orchestrator.py + prompts.py | `PromptContext.turn_clarifications` → bloc `## Prior clarifications` dans le prompt système |
| prompts.py | Directive langue différenciée `depth==0` vs `depth>0` : "internal reasoning in English" explicité |
| prompts.py — `_DELEGATE_TO` description | Ajouter "Do NOT include language instructions in briefings" |
| schema.sql — paradigme `briefing_contract` (id=14) | Interdire les directives de langue dans les briefings + propager `partial_clarifications` en cas de re-délégation |
| schema.sql — paradigme `wikipedia_search_strategy` (id=26) | "All queries MUST be in English, this takes precedence over any language directive" |
| schema.sql — `agent_tools` | `INSERT (synthesizer_id, 'conv_read_file')` |
| `db/migrate_NNN.sql` | Migration idempotente pour le grant synthesizer |