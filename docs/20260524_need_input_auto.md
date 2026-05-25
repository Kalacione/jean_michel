Analyse complète. Voici le bilan.

---

## Injections supplémentaires — propositions

### 1. `## Plan` — contenu de `workspace/plan.md` ★★★ priorité haute

**Condition :** `turn_index > 0` et le fichier existe dans le workspace de la conversation.

**Justification :** C'est le symétrique exact du problème conv_status. Actuellement, le paradigme `task_plan_file` dit "at the start of each new turn, read workspace/plan.md with workspace_view **before** deciding what to do next" — donc le premier tool call de jean-michel dans chaque turn de deep_research est systématiquement un `workspace_view`. L'orchestrateur Python peut lire ce fichier avant de construire le prompt ; jean-michel le verrait dès l'ouverture du turn, sans appel.

---

### 2. Déplacer le summary dans le system prompt ★★ priorité moyenne

**Condition :** `turn_index > 0` et `summary.md` existe.

**Justification :** Le summary est déjà injecté via `_prefix_summary()`, mais dans le **message user**, ce qui crée une ambiguïté sémantique (le LLM voit du texte system-like au début du message humain). Le mettre dans une section `## Summary` du system prompt clarifierait la frontière system/user. Deux paradigmes (`memory_without_narration`, `no_context_recap`) disent déjà "a running summary is provided" comme si c'était dans le system — ce serait enfin vrai.

---

### 3. `## Workspace` — liste des fichiers dans `workspace/` ★ priorité faible

**Condition :** `turn_index > 0` et le dossier `workspace/` n'est pas vide.

**Justification :** Évite un `workspace_list` préliminaire quand jean-michel cherche à savoir si `sources.md`, `report.md`, etc. existent déjà. Valeur marginale car jean-michel a rarement besoin de cette info en dehors de deep_research où `plan.md` donne déjà les fichiers produits.

---

### Non recommandé : injection des tool grants, agents disponibles, etc.

L'orchestrateur connaît les grants mais jean-michel n'en a pas besoin — il n'a pas de logique conditionnelle sur ses propres capacités. Polluerait le contexte pour rien.

---

## Déchets dans les paradigmes

### Déchet lié à `## Budget` (déjà injecté)

`metacog_live_monitor` → ✅ déjà correct, synchronisé lors de la migration 043.

---

### Déchet lié à une future injection `## Plan`

Si la proposition 1 est implémentée, trois paradigmes ont des instructions à changer :

**`task_plan_file`** — la dernière règle devient fausse :
> "At the start of each new turn in the same conversation, read workspace/plan.md with workspace_view before deciding what to do next."

→ À remplacer par : "At the start of each new turn, the current plan.md is already visible in the ## Plan section of your context — read it before deciding what to do next."

**`orchestration_plan_maintenance`** :
> "read workspace/plan.md via workspace_view"

→ À remplacer par : "check the ## Plan section in your context"

**`plan_before_complex_action`** :
> "After the planner returns: call workspace_view('plan.md') to read the current plan."

→ À remplacer par : "After the planner returns, the updated plan.md will be visible in ## Plan at the next turn."

---

### Déchet interne (indépendant des injections)

**`followup_proposals`** conflicte avec **`concise_output`** :
- `followup_proposals` : "propose 2 to 3 specific angles the user might want to explore further"
- `concise_output` : "Keep the user-facing answer under 4 short sentences"

Ces deux paradigmes sont mutuellement exclusifs sur des réponses courtes. Le LLM ne peut pas respecter les deux à la fois. Lequel prime ? À clarifier — mais c'est un problème indépendant des injections de contexte.

---

**Verdict :** La proposition 1 (injection `## Plan`) est la plus solide — même bénéfice que conv_budget, même pattern, trois paradigmes à mettre à jour en même temps. Je l'implémente quand tu valides l'approche ?    