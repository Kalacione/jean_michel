# Audit architectural — où l'orchestrateur se prend les pieds

J'ai cartographié le flux complet (delegate_to → child → report_findings → plan → tool_response parent). Voici ce que je vois, **par ordre d'importance structurelle**.

## La carte mentale réelle

```
Jean-Michel (router, depth=0)
  │
  ├── delegate_to(web-search-specialist, briefing, expected)
  │     │
  │     ↓  Orchestrateur intercepte (orchestrator.py:709)
  │     │  ├── valide depth, budget, support_files
  │     │  ├── plan_writer.write(step S1, status=in_progress)  ← écrit plan.md
  │     │  ├── _run_request(child) récursif
  │     │  │     │
  │     │  │     ↓ specialist tourne en boucle tool_call/tool_response
  │     │  │     │  Outils dispos: web_search, workspace_create_file,
  │     │  │     │                  workspace_view, plan_update (mort)
  │     │  │     │
  │     │  │     ↓ Sortie possible :
  │     │  │     │  (a) report_findings(summary, files_produced, ...)
  │     │  │     │      → _format_report_for_parent → markdown structuré
  │     │  │     │  (b) step_budget_exhausted → JSON aveugle
  │     │  │     │  (c) loop_detected (3 duplicates) → JSON aveugle
  │     │  │     │  (d) wall_clock_exceeded → JSON aveugle
  │     │  │
  │     │  ├── extrait "### Summary" du markdown, tronque à 120 chars
  │     │  ├── plan_writer.write(step S1, status=done, summary=...)
  │     │  └── tool_response = {tool, agent, artifact, answer, converged?}
  │     │
  │     ↓ injecté au tour LLM suivant de Jean-Michel
  │
  ├── decision: re-déléguer ou return_to_user ?
  └── ...
```

## Les vrais trous (par criticité décroissante)

### 🔴 Trou #1 — Contrat de sortie incohérent dans les prompts specialists

**Le prompt rendu pour un specialist contient `return_to_user` dans `# OUTPUT CONTRACT`** (prompts.py)**, mais l'orchestrateur ne l'accepte plus pour les specialists** (orchestrator.py) **— il exige `report_findings`.**

Conséquence : le specialist tente `return_to_user` à la fin de sa recherche → orchestrateur redirige → événement `SignalConvergenceRedirected` → le LLM ne comprend pas → recommence à chercher → budget claque.

**C'est très probablement la cause racine du comportement observé.** Le specialist ne sait littéralement pas comment finir.

### 🔴 Trou #2 — Plan.md est invisible à tout le monde

L'orchestrateur écrit fidèlement plan.md à chaque delegate_to, mais **personne ne le lit**.
- Jean-Michel : pas d'injection automatique dans son prompt système (`prompts.py` ne le lit nulle part)
- Les specialists : idem

**Résultat** : Jean-Michel re-délègue 3 fois la même chose parce qu'il n'a aucune mémoire structurée de ce qu'il a déjà fait. Le plan.md est un journal mort.

### 🔴 Trou #3 — Échec child = aveuglement total du parent

Quand le child fait `step_budget_exhausted` / `loop_detected` / `wall_clock_exceeded`, le parent reçoit :
```json
{"status": "step_budget_exhausted", "agent": "...", "partial_clarifications": null, "error": "..."}
```

**Aucune trace** des 18 web_searches faites, des résultats obtenus, des fichiers éventuellement créés. Le travail est jeté à la poubelle. Jean-Michel ne peut que re-déléguer la même demande ⇒ rebelote.

### 🟠 Trou #4 — Le workspace n'est pas exploité, et c'est pas (que) une question de prompt

Les specialists **ont les grants** (`workspace_create_file` listé dans `agent_tools`). Les paradigmes DB le leur disent (`search_then_synthesize`, `wikipedia_persist_before_delegate`, `workspace_as_shared_memory`).

Mais dans la session : **zéro `workspace_create_file`**. 18 web_searches, point.

Hypothèses (pas mutuellement exclusives) :
- **(a) MAX_STEPS_PER_REQUEST=15 est mortel pour ce flow.** Avec 15 steps, le specialist peut faire 5-6 web_searches puis doit synthétiser + écrire + report. Il faut compter "5 search + 1 read accumulé + 1 write + 1 report" = 8 steps minimum dans le meilleur cas. Sans accumulation, il fait `search → read mental → search → read mental → ... → budget claque`.
- **(b) Trou #1 fait que le LLM ne sait pas qu'il doit finir.** Sans verb de sortie clair, pas de discipline "je résume et j'écris".
- **(c) Pas de mécanique orchestrateur** qui pousserait le specialist à écrire. Tout repose sur la bonne volonté du LLM (option A que t'as rejeté à raison).

### 🟠 Trou #5 — Plan.md flat ≠ réalité hiérarchique

Le plan ne capture QUE les `delegate_to`. Toutes les tool_calls internes du child (les 18 web_searches, les éventuelles écritures workspace) sont invisibles. Plan.md ment par omission : il dit "S1 done" alors que S1 a explosé.

### 🟠 Trou #6 — files_produced perdu dans la traduction

Quand un specialist converge avec `files_produced=["report.md"]`, ce champ est **encapsulé dans le markdown** que reçoit le parent (`_format_report_for_parent`). Ce n'est pas un champ structuré du tool_response. Donc :
- Pas de colonne `Files` dans plan.md
- Pas de moyen mécanique pour Jean-Michel de passer ce fichier en `support_files` à la délégation suivante autrement qu'en re-parsant le markdown
- Le workspace devient un cimetière de fichiers non référencés

### 🟡 Trou #7 — DuplicateCallBlocked silencieux

Le code est correct (orchestrator.py), `_seen_calls` est scopé à la requête entière (orchestrator.py), le fingerprint normalise correctement les args. Donc `Wikidata API access method` × 2 **a probablement** déclenché DuplicateCallBlocked.

Mais : **pas d'artefact persisté**, **événement yield au CLI seulement**. Aucune trace sur disque. Impossible de confirmer dans une session terminée. C'est un bug d'observabilité, pas de logique.

### 🟡 Trou #8 — `plan_update` zombie en DB

`agent_tools` contient encore `plan_update` pour 5 agents (migration 050 a oublié de purger ? Non, on l'a fait). À vérifier : le subagent voit l'export `db_20260524_143609.sql` qui est **antérieur** à notre migration. Sur la DB live actuelle ça doit être propre. Mais à confirmer.

## Architecture proposée — orchestrateur-first, pas LLM-first

Le principe : **l'orchestrateur tient l'état, le LLM ne fait que produire des verbes**. Le workspace + plan.md sont une mémoire externalisée que l'orchestrateur maintient activement, pas un fichier que le LLM doit penser à lire.

### Chantier 1 — Fixer le contrat (Trou #1) [BLOQUANT]

- Aligner `prompts.py` : OUTPUT CONTRACT pour `specialist` doit lister `report_findings`, pas `return_to_user`. Le control tools mapping est déjà bon dans `_CONTROL_TOOLS_BY_ROLE`, mais la section OUTPUT CONTRACT en fin de prompt doit aussi être rôle-aware.
- Vérifier en lisant un prompt rendu.

### Chantier 2 — Plan.md vivant dans le prompt (Trou #2)

L'orchestrateur lit `plan.md` (s'il existe) et **l'injecte dans le bloc CONTEXT du prompt système de Jean-Michel** à chaque tour. Quelque chose comme :

```
## Research plan so far
[contenu de plan.md inlined ici, max 1500 chars]
```

Jean-Michel voit littéralement ce qu'il a déjà délégué. Plus de re-délégation aveugle.

### Chantier 3 — Récupération des partial findings (Trou #3)

Quand un child crash (`step_budget`, `loop_detected`, `wall_clock`), avant de retourner au parent, l'orchestrateur :

1. Liste les fichiers que le child a créés dans workspace pendant la requête
2. Extrait depuis les `tool_call` artefacts de cette requête les N dernières requêtes effectuées (queries, URLs lus)
3. Construit un `partial_report` markdown structuré : "Aborted (reason). Files created: ... . Searches tried: ... ."
4. Le retourne au parent comme answer du delegate_to, avec `converged=False, partial=True`

Plan.md gagne aussi un statut `partial` (en plus de `done`/`blocked`).

### Chantier 4 — Step budget contextualisé (Trou #4)

Deux options :
- **(a)** Augmenter `MAX_STEPS_PER_REQUEST` pour les agents de recherche à 25-30 (configurable par agent en DB).
- **(b)** Mécanique orchestrateur "écrire ou crever" : après N web_searches consécutifs sans `workspace_create_file`, l'orchestrateur injecte un tool_response synthétique : *"Tu as fait N recherches sans rien écrire. Avant ta prochaine recherche, appelle workspace_create_file pour sauvegarder ce que tu as appris."* Ce n'est pas du prompt-engineering, c'est une **règle de flux orchestrateur**.

Je préfère (a) + (b) combinés.

### Chantier 5 — files_produced first-class (Trou #6)

- Ajouter `files_produced: list[str]` au dict step de plan.md
- Le rendre dans une colonne dédiée
- L'orchestrateur peut alors auto-passer ces fichiers en `support_files` lors de la prochaine délégation si Jean-Michel ne les mentionne pas explicitement — ou au moins les exposer clairement dans le `Files` du plan injecté au prompt

### Chantier 6 — Observabilité duplicate (Trou #7)

- Persister un artefact `tool_response` quand DuplicateCallBlocked déclenche (avec le fingerprint et la query)
- L'event est déjà yielded au CLI, mais ajoute aussi une ligne dans `conversation.md`

### Chantier 7 — Plan.md sous-niveaux (Trou #5)

Plus tard. Permettre des sous-étapes générées par les workspace_create_file du child : `S1.1` = fichier `notes.md` créé. Visualisation enrichie. Pas critique tant que les chantiers 1-4 ne sont pas faits.

## Ordre d'attaque recommandé

| # | Chantier | Effort | Impact |
|---|---|---|---|
| 1 | Fixer contrat OUTPUT pour specialist (Trou #1) | S | 🔥 Énorme — cause racine probable |
| 2 | Injecter plan.md dans le prompt de Jean-Michel (Trou #2) | M | 🔥 Énorme — fin de la re-délégation aveugle |
| 3 | Récupération partial findings on crash (Trou #3) | M | 🔥 Énorme — fin de l'aveuglement parent |
| 4a | Step budget configurable par agent (Trou #4) | S | Moyen |
| 4b | Règle "écrire après N searches" (Trou #4) | M | Élevé |
| 5 | files_produced first-class (Trou #6) | S | Moyen |
| 6 | Persister DuplicateCallBlocked (Trou #7) | XS | Faible mais utile |
| 7 | Sous-étapes plan.md (Trou #5) | L | Moyen, à reporter |

**Mon conseil : 1 + 2 + 3 d'abord, on teste, on voit ce qui reste comme dysfonctionnement. Les chantiers 4-7 viennent après.**

Tu veux qu'on attaque dans cet ordre ? Et avant de toucher au code je voudrais **confirmer** sur un prompt rendu que le Trou #1 est bien réel (que `return_to_user` apparaît bien dans l'OUTPUT CONTRACT du specialist) — c'est trivial à vérifier.