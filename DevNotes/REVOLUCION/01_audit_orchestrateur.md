# 01 — Audit de l'orchestrateur actuel

> Phase d'analyse. Aucune décision n'est prise ici. Ce document décrit ce
> qui existe, pourquoi ça pèche, et où sont les vraies fractures.
> La proposition d'architecture cible est dans `02_architecture_cible.md`.

## TL;DR

Le système se présente comme une **state machine déterministe** mais c'est en
réalité **une boucle générator de ~700 lignes** qui dispatche des tool calls
LLM avec une dizaine de garde-fous empilés a posteriori. Les vrais leviers de
qualité (choix du modèle, routage par complexité, vrai état de pipeline) ne
sont pas dans le code — ils sont délégués au goodwill d'un LLM unique qui
tourne en thinking mode quelle que soit la question. Le résultat est un
système qui *paraît* fonctionner sur les cas heureux et casse en silence sur
tout le reste.

---

## 1. La fracture conceptuelle : "state machine" en nom seulement

Le commentaire de tête de [orchestrator.py](src/jeanmichel/orchestrator.py)
parle de *state machine*. Dans les faits :

- Pas de table d'états ni de transitions explicites. Pas de `current_state`.
- Le "phase" supposé du pipeline (`GATHER → CRITIC → BUILD`) n'existe pas
  en variable Python. Ce qui se rapproche le plus est `current_task_class`
  (`single_fact` / `medium_task` / `deep_research`), une **étiquette** posée
  par le LLM via `set_task_class()` et lue ponctuellement pour gater
  `delegate_to`.
- Pas de pipeline `gather_done` / `critic_done` / `build_done` non plus.
  Le commit `97b7eb0 07_research_pipeline_enforcement` y a touché, puis
  les concepts ont été dilués dans des "completion_verbs" textuels
  (`gather_done` etc.) que la documentation interne mentionne mais que
  l'orchestrateur ne vérifie pas mécaniquement.

**Conséquence** : la "logique de pipeline" vit dans le system prompt
([prompts.py](src/jeanmichel/prompts.py)) sous forme de phrases imploranantes.
Quand le LLM ne respecte pas l'ordre attendu, l'orchestrateur ne sait pas le
détecter — il subit. Migration 045, 049, 057, 060, 061 sont toutes des
correctifs textuels ou des refus tardifs (`classify_first`, `plan_first_required`)
qui rééditent le même pattern : on attend que le LLM fasse une erreur visible
pour l'invalider à coup de message d'erreur structuré.

C'est pas une state machine, c'est un **modérateur**.

## 2. Un seul LLM, en thinking mode, pour tout

[config.py](src/jeanmichel/config.py#L81-L86) :

```python
DEFAULT_OLLAMA_MODEL = os.environ.get("JEANMICHEL_MODEL", "gemma4:26b")
```

[llm.py](src/jeanmichel/llm.py#L48-L57) : `OllamaClient` reçoit **un** model
au constructeur. Le `chat()` ne prend pas de paramètre `model`. Le champ
`AgentDef.model` n'existe pas dans le schema. **Tous les agents parlent au
même modèle**, en thinking activé (`agent.thinking_mode` est un booléen
généralement à `True`).

Conséquence directe du manifeste : "Quelle heure est-il ?" déclenche un
chargement de Gemma4 26B + un cycle de pensée. La latence est rédhibitoire
pour les usages Alexa-like, et la VRAM s'effondre dès qu'on enchaîne deux
spécialistes en parallèle.

Aucune infrastructure de **routing par modèle** n'existe. Aucun fallback non
plus : si Gemma4 hallucine, on encaisse.

## 3. Le LLM n'a pas de mémoire de turn — il a un "récap"

[llm.py](src/jeanmichel/llm.py#L75-L82) construit à chaque appel :

```python
messages = [
    {"role": "system", "content": system},
    {"role": "user",   "content": user},
]
```

**Pas d'historique multi-turn.** Pas de `role: "assistant"`, pas de
`role: "tool"`. À chaque itération du loop, l'orchestrateur réécrit la valeur
de `running_user_text`
([orchestrator.py L1582-L1595](src/jeanmichel/orchestrator.py#L1582)) :

```python
running_user_text = (
    _recap                                          # plan + tool calls résumés
    + "[ORCHESTRATOR] Tool results below ...\n\n"
    + "\n".join(tool_responses)                     # JSON des tools
)
```

Le modèle ne voit **ni ses anciennes pensées**, ni ses anciens tool_calls,
ni les anciennes réponses, **sauf via un récap basse fidélité** rendu par
`plan_writer.render_plan_recap()`. Concrètement :

- Une recherche `web_search(q="rust vs go performance")` produira un JSON
  de 80kB en réalité, mais dans le récap il devient un bullet *"web_search
  → 5 hits"*.
- Le tour suivant, le LLM "voit" ces 5 hits via le tool_response brut
  injecté en user, mais **ne se souvient pas pourquoi il a posé cette
  question** — son thinking précédent est perdu.
- Au tour N+2, plus aucune trace du tool_response. Juste le récap dégradé.

C'est l'**explication structurelle des spécialistes qui tournent en rond** :
ils n'ont pas accès à leur propre raisonnement passé, leur seul lien avec
l'histoire est un markdown compressé. Sans surprise, ils refont les mêmes
recherches sous d'autres angles. Le `_fingerprint` dedup les attrape parfois,
mais c'est cosmétique : la cause est en amont.

## 4. Empilement de garde-fous, zéro modèle de coût

`config.py` déclare **7 budgets indépendants** :

| Budget                                | Portée                       | Effet à dépassement              |
|---------------------------------------|------------------------------|----------------------------------|
| `MAX_RECURSION_DEPTH = 10`            | chaîne de `delegate_to`      | refus de déléguer                |
| `MAX_STEPS_PER_REQUEST = 20` (+bonus) | itérations LLM dans 1 req    | abort de la requête              |
| `MAX_DELEGATIONS = 8`                 | délégations par tour         | refus de déléguer                |
| `MAX_SEARCH_CALLS_PER_REQUEST = 10`   | recherches dans 1 req        | tools restreints à `report_findings` |
| `LLM_CALL_TIMEOUT = 120s`             | un appel `chat()`            | retry + concluding hint          |
| `REQUEST_WALL_CLOCK = 900s`           | une requête entière          | abort + rapport partiel          |
| `TURN_WALL_CLOCK = 1800s`             | tour humain complet          | abort dur                        |
| + `SOFT_DEADLINE_RATIO = 0.75`        | dérivé des deux derniers     | restriction du payload tools     |
| + `WRITE_STEP_BONUS / MAX_STEP_BONUS` | bonus si écriture workspace  | étend `MAX_STEPS`                |

Le manifeste appelle ça "cache-misère". C'est exactement ça. Chaque garde-fou
a été ajouté en réaction à un échec observé (les commits le confirment :
`01_wall_clock_timeouts`, `03_loop_detection`, `07_research_pipeline_enforcement`,
"search budget gate" #061…). Aucun **modèle global** : il n'y a pas de
coût agrégé (tokens, temps cumulé, profondeur) qu'on consomme à chaque
action — il y a 7 compteurs indépendants, et le système meurt par celui qui
arrive le premier au plafond. Les valeurs sont des incantations.

Une vraie solution déterministe nécessite **un budget unique** (ex. budget
de coût par tour, dérivé du `task_class`) que l'orchestrateur dépense
explicitement à chaque action — pas 7 disjoncteurs.

## 5. Récursivité horizontale > profondeur

Le manifeste demande explicitement *"une limite en profondeur, pas à
l'horizontale"*. Le code actuel fait l'inverse :

- `delegate_to` appelle récursivement `_run_request`, donc **profondeur** =
  `MAX_RECURSION_DEPTH=10`.
- Mais à chaque niveau, **le même tour peut déléguer 8 fois en parallèle**
  (`MAX_DELEGATIONS=8`).
- L'arbre théorique : 10⁸ feuilles. Le wall-clock global est le seul rempart.
- Pire : le `delegate_to` ne renvoie pas une *file* mais un appel récursif.
  Le parent attend que l'enfant ait conclu pour reprendre. Pas de
  scheduling, pas de priorisation, pas de coupure si une branche prend trop.

La vraie demande utilisateur est : *"chaque sous-tâche peut faire émerger
des sous-tâches enfants"*. C'est un **arbre orienté avec budget partagé
décroissant en profondeur**. Aujourd'hui c'est juste une pile d'appels
Python qui peut exploser en largeur.

## 6. plan.md vs todo.json — l'analyse a déjà été faite

[DevNotes/plan_vs_todo.md](DevNotes/plan_vs_todo.md) couvre la question avec
précision. Résumé pour la REVOLUCION :

| Fichier      | Écrit par      | Quand                      | Granularité           |
|--------------|----------------|----------------------------|-----------------------|
| `plan.md`    | orchestrateur  | post-délégation            | `S1`, `S2`, `S1.1`    |
| `todo.json`  | LLM (`manage_todo_list`) | pré-délégation       | sous-tâches libres    |

Le constat de plan_vs_todo.md est : *ils ne sont pas redondants, ils sont
complémentaires*. Sauf que :

1. Le LLM ne le voit pas comme ça : il a deux interfaces pour "planifier",
   doit choisir, se trompe régulièrement (migration 059 a dû ajouter un
   `MUST` pour forcer `manage_todo_list`).
2. Le **deep-research guard** est déclenché par l'existence de `plan.md`
   ([orchestrator.py L908-L917](src/jeanmichel/orchestrator.py#L908)), et
   `manage_todo_list` (qui crée `todo.json`) appelle aussi `plan_writer.write()`
   qui crée `plan.md` vide. Donc le simple fait de poser une todo grille
   le droit du router à `web_search` — même en mode chat trivial.
3. La todo a une notion de `depends_on` (DAG), le plan a une notion de
   parent/enfant (arbre). Deux structures incompatibles pour le même but :
   "ce qui reste à faire".

**Verdict** : ce sont deux artefacts qui devraient être **une seule
structure de données canonique** — un arbre de tâches typées vivant en DB
ou en JSON unique. Le markdown est un *rendu* de cet arbre, pas la source.

## 7. Le system prompt est immuable, le contexte ne l'est pas

`prompts.py` rend un system prompt **une fois par requête** :

- Identité de l'agent, paradigmes attachés, profil utilisateur, plan injecté
  (tronqué à 3000 chars), liste de tools, contrat de sortie.

Une fois rendu, ce bloc ne bouge plus de la requête. Or **toute l'évolution
de l'état du monde** (ce que le spécialiste vient de trouver, ce que le
parent a planifié entre-temps) est censée arriver via le `running_user_text`,
qui n'est pas un message persistant mais une chaîne qu'on écrase à chaque
itération.

Résultat : le system prompt **n'évolue jamais** au cours d'une requête, alors
qu'il devrait être le canal naturel pour injecter "tu as déjà fait X, focus
sur Y". Le hack actuel — `render_plan_recap()` prepended au user — est une
fuite architecturale.

## 8. Tool dispatch : un méga-loop de 700 lignes

`_run_request` (≈ L678-L1620) fait :

1. Boucle pendant que `llm_steps < MAX_STEPS + bonus`.
2. Vérifie 4 budgets de temps.
3. Filtre le `tools_payload` selon le soft deadline et le rôle.
4. Appelle `llm.chat()`.
5. Écrit l'artefact `thought` si thinking.
6. Si pas de tool_calls → return implicite.
7. Pour chaque `tool_call` dans la réponse :
   - Si **control verb** (delegate_to, ask_human, return_to_user, report_findings,
     set_task_class, manage_todo_list, signal_convergence) → handler dédié
     inline avec gating spécifique.
   - Sinon : fingerprint dedup → exécute le tool natif → log dans plan.
8. Recompose `running_user_text` = recap + tool_responses → retour 1.

Tout est **inlined**. Les "control verbs" sont du dispatch hardcodé dans la
boucle, pas des transitions de machine d'état. Les gates (`classify_first`,
`plan_first_required`) sont des `if` éparpillés sur 100 lignes. Toute
nouvelle règle = nouveau `if` à insérer au bon endroit. C'est la définition
même d'une dette structurelle.

## 9. Ce que la git history raconte

Extrait significatif (`git log --oneline -100`) :

```
3491595 bon, il est con
9a50403 compadre
61758e4 l'eternel recommencement
7fed060 qwen est un cwon
f97eb48 l'amère en slop
303b715 feat: search budget gate + support_files workspace fix (#061)
972fc6d fix: nudge on empty LLM turn + auto-update todo.json on delegation
799fcc2 fix: strip research tools from router as soon as task_class=deep_research
b9a39c9 feat: set_task_class tool + classify-before-delegate structural gates
64cdd85 fix(db): migration 059 — enforce manage_todo_list via MUST + <think> ≠ persist
```

Patterns visibles :

- **Cycle de désespoir** : "battle plan" → "amère en slop" → "eternel
  recommencement" → "bon il est con". Les noms parlent.
- **Cascade de migrations correctives** (057 → 058 → 059 → 060 → 061) : chacune
  ajoute un MUST, un gate, une restriction. C'est exactement le pattern
  *"on patche les symptômes"*.
- Le passage `planner` → `dispatcher` → `router` montre qu'on cherche encore
  qui est responsable de quoi.
- Plusieurs revirements modèle (`d4c5d3a default to qwen3:14b` puis retour à
  Gemma) prouvent que le choix du modèle global est un cache-misère lui aussi.

## 10. Top 7 défauts de conception (synthèse)

1. **Pas de modèle d'état explicite.** "State machine" est un mot, pas une
   structure. Les "phases" vivent dans des chaînes de caractères.
2. **Un seul modèle, thinking forcé, pour tout.** Aucun routage par
   complexité.
3. **Aucune mémoire conversationnelle native.** Tout est reconstruit à
   chaque tour via un récap compressé → spécialistes amnésiques.
4. **7 budgets orthogonaux** au lieu d'un coût agrégé.
5. **Récursion horizontale non contrainte.** L'arbre de délégation peut
   exploser ; seul le wall-clock l'arrête.
6. **plan.md ↔ todo.json** : deux artefacts pour une même intention, qui se
   collisionnent (le guard deep-research se déclenche à tort).
7. **Méga-loop monolithique de 700 lignes**, gates inlines, control verbs
   hardcodés. Toute évolution = nouvelle dette.

## 11. Ce qui marche et qu'il faut sanctuariser

Pour ne pas jeter le bébé :

- **Le système de paradigmes en DB** est solide : composition par
  agent/rôle, attache via `agent_paradigms`, render dans le prompt. À garder.
- **Le workspace per-conv** ([_workspace.py](src/jeanmichel/tools/_workspace.py))
  avec quota et sandboxing. C'est la bonne primitive de "mémoire partagée".
- **La sandbox Docker** (`bash_sandbox`) — exécution sûre, audit en DB.
- **`build_registry(conv_folder)`** comme pattern de DI pour les tools
  context-bound. Propre, testable.
- **Les artefacts markdown** persistés par tool_call. Excellente
  traçabilité, base à exploiter bien plus.
- **`MockClient`** + tests pytest sans Ollama. Cette discipline doit
  survivre à toute réécriture.

## Prochaine étape

Voir [02_architecture_cible.md](DevNotes/REVOLUCION/02_architecture_cible.md)
pour la proposition à itérer ensemble.
