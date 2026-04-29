# Plan — `comparator-specialist`

## Contexte

Lors d'une question comparative ("Qui est le plus fort : l'hippo ou le rhino ?"), jean-michel
délègue au `wikipedia-specialist` avec une tâche double : chercher ET comparer. Le budget de
8 étapes s'épuise sur les recherches (2-3 essais avant le bon article). Jean-Michel reçoit
`step budget exhausted`, ne sait pas récupérer, et re-délègue avec la même tâche → boucle infinie.

Cause structurelle : aucun agent n'est responsable de la synthèse comparative.

---

## Solution

Nouvel agent `comparator-specialist` qui orchestre lui-même la collecte via `delegate_to`
parallèles, puis synthétise. Jean-Michel ne fait que router.

### Flux cible

```
human → jean-michel (depth=0)
          [comparison_routing paradigm → delegate_to comparator-specialist]

          comparator-specialist (depth=1)
            [comparison_research_first → delegate_to en parallèle]
            → wikipedia-specialist  "hippo facts"   (depth=2)
            → wikipedia-specialist  "rhino facts"   (depth=2)
            ← données reçues
            [comparison_data_only + structured_verdict → return_to_user]
```

---

## Changements — DB uniquement, aucun Python

### Agent

| champ          | valeur |
|----------------|--------|
| `code`         | `comparator-specialist` |
| `name`         | `Comparator Specialist` |
| `role`         | `specialist` |
| `mission`      | `Given a comparative question and the entities to compare, gather factual data for each entity via parallel delegations to domain specialists, then synthesize a structured, evidence-based comparative verdict.` |
| `thinking_mode`| `1` |
| `temperature`  | `0.2` |

### Catégorie

Section `process` → catégorie `comparison` / *Comparison* (order_priority 40)

### Paradigmes (4, tous non-globaux)

**`comparison_routing`** — lié à `jean-michel`
```
- When the human asks to compare, rank, or choose between two or more entities,
  do not delegate to a domain specialist directly.
- Delegate exclusively to `comparator-specialist`, passing the comparison question
  and the list of entities to compare.
- The comparator is solely responsible for sourcing the data.
```

**`comparison_research_first`** — lié à `comparator-specialist`
```
- Before any comparative reasoning, emit one delegate_to per entity to the
  appropriate domain specialist (e.g. wikipedia-specialist for encyclopedic
  facts, weather-specialist for meteorological data).
- These calls may be issued in the same turn — they run in parallel.
- Do not attempt any comparative reasoning before all delegations have returned.
```

**`comparison_data_only`** — lié à `comparator-specialist`
```
- All factual claims in the verdict must come from the briefings returned by
  the delegated specialists. Never use training knowledge about the entities.
- If a delegation returned no usable data, state it explicitly — do not fill
  the gap with inferred or approximate information.
```

**`structured_verdict`** — lié à `comparator-specialist`
```
- Structure the final answer as:
  1. Summary of gathered data per entity.
  2. Side-by-side analysis of each relevant criterion.
  3. Explicit verdict with justification.
- If data is insufficient for a definitive verdict, say so with the reason.
```

### Bindings `agent_paradigms`

- `comparator-specialist` ← `comparison_research_first`, `comparison_data_only`, `structured_verdict`
- `jean-michel` ← `comparison_routing` (s'ajoute à `trust_context_defaults`)

### Grants `agent_tools`

Aucun — le comparator n'utilise que les outils de contrôle (`delegate_to`, `return_to_user`),
toujours disponibles à tous les agents sans grant.

---

## Ordre d'implémentation

1. `db/schema.sql` — ajout des INSERTs (source de vérité, section finale du fichier)
2. Live `jeanmichel.db` — mêmes INSERTs exécutés directement en SQLite
3. Tests — routing jean-michel → comparator, flow parallèle, verdict structuré
