# 04 — Audit complémentaire de l'orchestrateur

> Le doc `01_audit_orchestrateur.md` a posé les 10 fractures structurelles
> majeures. Ce doc complète avec 6 anti-patterns sortis d'une lecture
> ligne-à-ligne d'[orchestrator.py](src/jeanmichel/orchestrator.py),
> [llm.py](src/jeanmichel/llm.py), et [prompts.py](src/jeanmichel/prompts.py).
> Ce sont les "petites" dettes qui multiplient les grandes. Aucune redite
> avec 01 ici.

## 1. Cinq gates inline dans `_run_request`, sans ordre déclaré

La boucle principale de [orchestrator.py](src/jeanmichel/orchestrator.py)
(L.818-1595) ne lit pas une table de transitions. Elle traverse 5 `if`
empilés dans un ordre qui n'est nulle part formalisé :

| Ordre runtime | Gate                  | Localisation        | Condition                                                   |
|---------------|-----------------------|---------------------|-------------------------------------------------------------|
| 1             | Soft deadline         | L.833-864           | `not _triggered AND _conclusion_tool AND deadline_ratio<1`  |
| 2             | Search budget         | L.871-891           | `not _triggered AND _conclusion_tool AND search_count>=MAX` |
| 3             | Deep-research guard   | L.901-912           | `agent.role=router AND (plan.md exists OR class=deep_*)`    |
| 4             | classify_first        | L.1133-1149         | `mode in PLANNING AND router AND class is None`             |
| 5             | plan_first_required   | L.1153-1171         | `mode in PLANNING AND router AND class=deep AND !todo.json` |

Conséquence : ajouter un nouveau garde-fou demande de choisir empiriquement
où insérer le `if`. Chaque gate dépend de l'état laissé par les précédentes
(un side-effect file `plan.md` posé par la gate 3 active la gate 5). Pas de
test possible en isolation. Pas de visualisation possible non plus —
contrairement à une state machine.

C'est ça qui produit le pattern git "encore un MUST, encore une migration,
encore un gate". Chaque correctif s'ajoute à la pile, jamais en remplacement.

## 2. Triple état désynchronisé pour `task_class`

`task_class` (la classification "single_fact / medium_task / deep_research"
posée par le tool `set_task_class`) vit dans trois endroits :

- **DB** : table `conversations.task_class`, persisted via `db.set_pipeline_state()`
- **Mémoire locale** : `_current_task_class` dans `_run_request`, lue UNE SEULE FOIS
  à L.765 (`pipeline = db.get_pipeline_state(...)`)
- **Arguments tool** : `call.arguments.get("task_class")` à L.1506 lors d'un
  `set_task_class` appel

Si un spécialiste appelle `set_task_class` en milieu de request, l'orchestrateur
met à jour `_current_task_class` localement (L.1506) ET la DB (via le handler
du tool). Mais le router parent qui reprend la main à la prochaine itération
**ne re-lit pas la DB** — il continue avec son `_current_task_class` cached.

Pire : si le user pose une seconde question dans la conversation, la
nouvelle request lit la DB une fois (L.765) puis recommence le même bug
localement. Trois caches, aucun invalidator, drift garanti.

## 3. Fingerprint de dédup aveugle au contexte

[orchestrator.py L.1379-1423](src/jeanmichel/orchestrator.py#L1379) construit
le fingerprint d'un tool_call en normalisant : whitespace, casing, valeurs
par défaut égalisées. Pour les outils de lecture, seule l'identité du chemin
compte (un `view_range` différent ne crée pas un nouveau fingerprint).

**Ce qui manque** : `turn_index`, `depth`, `parent_step_id`. Un agent ne
peut pas refaire `web_search("paris")` au turn 2 (avec un contexte
totalement différent du turn 1) parce que le fingerprint matche le cache du
turn 1. La dédup l'attrape — et le force à conclure. Faux positif déguisé
en sécurité.

Le commentaire de tête du fingerprint dit "1ère fois en payload complet,
puis simple notice avec compteur. 3 duplicates consécutifs → force-stop".
Ce qui est cassé : un duplicate "consécutif" est défini comme "même
fingerprint", pas "même contexte". 3 web_search légitimes consécutifs sur
le même mot-clé dans 3 sous-requêtes différentes = force-stop injuste.

## 4. `render_plan_recap()` à chaque itération, sans cache

[prompts.py L.430](src/jeanmichel/prompts.py#L430) `render_plan_recap()`
lit `plan.md` du disque, le parse pour extraire la section du step courant,
le tronque, et le retourne. Cette fonction est appelée à
[orchestrator.py L.1586](src/jeanmichel/orchestrator.py#L1586) à chaque
itération de la boucle principale.

Cardinalité : sur une request typique en mode `deep_research` avec ~10 steps
× 30 itérations LLM internes, c'est 300 lectures `plan.md` du disque.
Chacune relit un fichier qui peut atteindre 100 KB. Aucun cache mémoire,
aucun stat(2) pour vérifier si le fichier a changé, aucun memoization.

Le coût en latence est faible (lecture de fichier local), mais l'absurdité
architecturale est totale : le LLM lit son propre récap qui contient son
propre tool_response du tour précédent, parce qu'on lui a refusé de le voir
nativement dans `messages[]`.

## 5. `agent_delegation_targets` : verrou sans clé

La table `agent_delegation_targets` (whitelist par agent des cibles
autorisées en délégation) existe en BDD et est lue dans
[prompts.py L.496](src/jeanmichel/prompts.py#L496). Si elle est vide pour
un agent → comportement legacy "tout le monde est visible". Si elle a des
lignes → ces lignes filtrent la section `## Delegation targets` du prompt.

Mais : aucun agent **n'a de lignes**. Vérifié dans le seed
[db/schema.sql](db/schema.sql) — pas un seul `INSERT INTO agent_delegation_targets`.
Le verrou existe, personne ne s'en sert.

C'est dommage : c'est le mécanisme déterministe parfait pour empêcher
l'explosion combinatoire de la délégation. Le router devrait avoir le
droit de déléguer à tous les spécialistes, mais un spécialiste comme
`weather-specialist` ne devrait pas pouvoir déléguer à `code-runner` —
ça n'a aucun sens fonctionnel et augmente la profondeur de l'arbre pour
rien. La whitelist par agent applique ça structurellement. Personne ne l'a
seedée. À récupérer dans la v2.

## 6. "Completion verbs" sans applicateur

[prompts.py L.227](src/jeanmichel/prompts.py#L227) déclare 4 outils :
`planner_done`, `gather_done`, `critic_done`, `build_done`. Ils sont
exposés au LLM via la signature `expected.completion_verb`. Le LLM est
censé les appeler quand il atteint la fin d'une phase.

**Vérifié dans orchestrator.py** : aucun handler ne lit le `completion_verb`
émis par un spécialiste. Les outils sont déclarés, le LLM peut les appeler,
mais le résultat est traité comme n'importe quel tool_response — pas de
transition de phase enregistrée en DB, pas de gate qui change l'état.

Le `conversation_phases` table (migration 044) est créée mais aucun INSERT
dans le code de l'orchestrateur. C'est de la doc qui prétend être de la
mécanique.

## 7 (bonus). Le rebuild de `running_user_text` est l'épitaphe du système

[orchestrator.py L.1582-1595](src/jeanmichel/orchestrator.py#L1582) :

```python
running_user_text = (
    _recap                                          # plan + tool calls résumés
    + "[ORCHESTRATOR] Tool results below ...\n\n"
    + "\n".join(tool_responses)
)
```

Cette concaténation à chaque itération est la signature de la fracture
mémoire. Le LLM voit toujours `[system, user]` — jamais
`[system, user, assistant, tool, assistant, tool, ...]`.
Ollama supporte les rôles `assistant` et `tool` en multi-turn nativement
depuis 0.9 (avec thinking depuis le même release).
[llm.py L.74-79](src/jeanmichel/llm.py#L74) :

```python
messages = [
    {"role": "system", "content": system},
    {"role": "user", "content": user},
]
```

C'est le sabotage initial. Le reste — 7 budgets, 5 gates, 50 migrations,
le récap, les MUST — est compensation. Si on retire ce sabotage, beaucoup
de paradigmes anti-loop deviennent inutiles.

## Synthèse

Les défauts du doc 01 décrivent l'**architecture** cassée. Ceux-ci décrivent
l'**implémentation** qui en découle :

1. Gates sans table — empilement.
2. `task_class` triple-state — drift par construction.
3. Fingerprint aveugle au contexte — faux positifs.
4. Récap relu sans cache — absurdité structurelle.
5. Whitelist délégation jamais seedée — verrou sans clé.
6. Completion verbs déclarés sans applicateur — promesse non tenue.
7. `running_user_text` reconstruit — sabotage initial qui explique tout le reste.

## Prochaine étape

[05_inspiration_claude_copilot.md](DevNotes/REVOLUCION/05_inspiration_claude_copilot.md)
— ce qu'on prend de Claude Code et Copilot pour réparer ça sans réinventer.
