# Rapport d'audit Jean-Michel — état logique, paradigmes, et avenir LangGraph

**Auteur** : audit externe
**Date** : 2026-05-01
**Version analysée** : codebase post-modes (jm.sh, paradigm_matrix, archivist en place)

---

## 0. Synthèse exécutive

Trois constats à retenir avant les détails.

**Constat 1 — l'architecture actuelle est saine, ce qui est bancal est ailleurs.**
L'orchestrateur en générateur d'events, la séparation des préoccupations (CLI / orchestrator / db / prompts / llm), le pattern paradigmes-en-DB : tout cela tient debout. Le problème n'est pas l'architecture ; il est dans **la manière dont les paradigmes ont été distribués au fil du temps** et dans **deux ou trois mécaniques de l'orchestrateur qui méritent d'être resserrées**. Pas une refonte.

**Constat 2 — la fragilité ressentie vient principalement des paradigmes globaux mal pensés.**
La cartographie réelle (calculée par injection de la requête `load_paradigms_for_agent` sur la BDD installée) montre que des agents reçoivent des directives qui n'ont aucun sens dans leur contexte. C'est ce qui produit le sentiment "le modèle répond bizarrement, à côté de la plaque". Ce n'est pas un défaut du LLM ni de l'orchestrateur — c'est un problème de configuration BDD que la nouvelle dimension `mode` a aggravé.

**Constat 3 — LangGraph est un excellent outil dont vous n'avez pas besoin maintenant.**
Vous n'avez pas le problème que LangGraph résout (cycles complexes, observabilité, multi-tenant, persistance distribuée). Vous avez le problème que **votre propre orchestrateur** résout déjà. Migrer maintenant, c'est jeter 6 mois de connaissance fine pour adopter une abstraction qui va vous coûter plus de temps qu'elle ne vous en fait gagner. Recommandation détaillée en partie 4.

Le reste du rapport documente précisément ces trois points et propose un plan de remédiation graduel.

---

## 1. Audit logique de l'orchestrateur

### 1.1 Ce qui fonctionne bien

- **Pattern event generator** : l'orchestrateur `yield`-e des events typés, la CLI les rend. Excellent découpage. Une API web future les consommerait sans modifier l'orchestrateur. C'est un acquis majeur.
- **`_run_request` est récursif** : les `delegate_to` réutilisent la même fonction avec `depth+1`. La logique est lisible et testable.
- **Garde-fous codés dur** :
  - Profondeur ≤ 5 enforced côté orchestrateur (rejet `delegate_to` au-delà avec message explicite).
  - `archivist` whitelisté (rejet de `delegate_to(archivist)` venant des LLM).
  - `seen_ask` au niveau de la requête (et non du tour LLM) — bloque réellement les `ask_human` en boucle.
  - `max_steps = 8` : filet anti-tool-loop par requête.
- **Persistance complète** : chaque artefact (prompt, thought, tool_call, tool_response, briefing, response) est écrit en fichier ET enregistré en BDD. Traçabilité parfaite.
- **Continuité de conversation** correctement implémentée : `conv_folder is None` distingue premier tour vs suivants ; `turn_index` incrémenté ; `summary.md` injecté en préfixe du `user` message au tour 2+.
- **Échec archivist non-bloquant** : `_run_archivist` a un try/except qui préserve le summary précédent en cas d'échec. Bon réflexe défensif.

### 1.2 Anomalies repérées

#### A1. `seen_ask` est strictement par requête mais pas conservé entre les *steps* de la même requête

Dans `_run_request`, `seen_ask` est initialisé à `False` au début de la fonction. Il bloque correctement deux `ask_human` dans le **même** tour LLM, mais si l'agent appelle `ask_human` au step 1, reçoit la réponse, puis ré-appelle `ask_human` au step 2 (toujours dans la même requête racine), c'est autorisé.

**Question** : c'est voulu ? Le paradigme `one_question_at_a_time` parle de "Une seule question par appel" donc oui, en toute rigueur, deux questions peuvent être posées si l'agent en a besoin entre-temps. Mais tu avais initialement décidé "une seule par tour modèle" — il y a divergence entre l'intention et l'implémentation. À clarifier.

**Sévérité** : faible. Mais à statuer pour ne pas pourrir un futur debug.

#### A2. `_last_response_artifact` est un état mutable côté instance, pas par requête

```python
# orchestrator.py:148
self._last_response_artifact: str | None = None
# orchestrator.py:481
self._last_response_artifact = filename
```

Cette variable est partagée entre **tous les `_run_request` simultanés** (en théorie). Aujourd'hui ce n'est pas un problème car les délégations sont strictement séquentielles. Mais c'est un piège si tu actives la délégation parallèle (cf. A6 plus bas) : deux delegations parallèles écraseraient leur `_last_response_artifact` mutuellement, et le parent récupérerait le mauvais filename pour `support_files`.

**Sévérité** : moyenne aujourd'hui, critique le jour où tu actives le parallèle. Solution : retourner le filename comme valeur de retour de `_run_request` (qui retournerait alors `tuple[str, str | None]` pour `(answer, artifact)`).

#### A3. Le briefing reçu par un agent enfant échappe à `support_files`

Le parent émet :
```python
delegate_to(briefing="...", support_files=["123_specialist_response.md"])
```

L'enfant reçoit un prompt avec `support_files` listés. Mais dans `_run_request` ligne 238, on fait :
```python
running_user_text = inbound_text  # = briefing seulement
```

Donc l'enfant doit explicitement appeler `conv_read_file(filename)` pour lire le contenu. Bien.

**Mais** : la convention `(artifact: FILENAME)` ajoutée dans `tool_responses` (ligne 352) force le PARENT à voir le filename dans la réponse texte — c'est le seul moyen pour le parent de savoir quel filename passer en `support_files` à l'agent suivant.

C'est acrobatique. Le LLM doit lire un format texte parenthésé pour récupérer un filename qu'il va devoir ressortir dans un argument structuré. Le passage est fragile : si Gemma 4 décide de paraphraser au lieu de copier-coller, le filename se perd.

**Pourquoi ce détour ?** Parce qu'il n'y a pas de retour structuré du tool `delegate_to` côté LLM. L'orchestrateur le contournement en bricolant le texte du `tool_response`.

**Sévérité** : critique conceptuellement. C'est une source réelle de bugs LLM ("j'ai essayé de transmettre le résultat à synthesizer mais il n'a rien reçu").

**Solution proposée** : faire en sorte que le `tool_response` du `delegate_to` soit structuré, par exemple :
```json
{"agent": "wikipedia-specialist", "artifact": "150033_wikipedia-specialist_response.md", "answer": "..."}
```
Ainsi quand le LLM le passe au prochain agent, il a toute l'info. Et l'instruction "pass FILENAME in support_files" devient triviale à suivre.

#### A4. Le step budget `max_steps = 8` est un nombre magique non documenté

Hardcodé en `orchestrator.py:239`. Aucune ligne d'explication, aucun lien BDD. Pour un système qui revendique "DB = source of truth", c'est une dette.

**Sévérité** : faible. Mais c'est un drapeau d'odeur de code.

#### A5. Le mode `analyse` ne propage pas la fermeture de session côté orchestrateur

Dans `cli.py:265-266`, on a `if args.mode == "analyse": break`. Mais l'instance `Orchestrator` reste vivante sans avoir de signal de "fin". Pas de fuite (le programme se termine), mais sémantiquement c'est asymétrique avec `chat`/`vocal`.

**Sévérité** : nulle aujourd'hui. À noter pour le jour où la CLI pourra changer de mode à chaud.

#### A6. La délégation parallèle est annoncée mais non implémentée

Le paradigme `comparison_research_first` dit : *"These calls may be issued in the same turn — they run in parallel."* Et le `delegate_to` schema dit : *"Multiple delegate_to calls in the same turn run in parallel."*

**Mais** dans `_run_request` ligne 292, on a :
```python
for call in response.tool_calls:
    ...
    if call.name == "delegate_to":
        ...
        child_answer = yield from self._run_request(...)  # SÉRIEL
```

C'est strictement séquentiel. Le `comparator-specialist`, qui est censé lancer N spécialistes "en parallèle" pour gagner en latence, les enchaîne en réalité l'un après l'autre.

**Sévérité** : moyenne. Pas de bug fonctionnel, mais une promesse non tenue dans les paradigmes — qui peut induire le LLM en erreur sur le coût d'une délégation.

**Note technique** : Ollama supporte le batching de requêtes via `OLLAMA_NUM_PARALLEL`. Le passage en parallèle est faisable techniquement, mais demande de basculer l'orchestrateur en async (cf. partie 4 sur LangGraph qui aborde ce point).

#### A7. Le summary.md n'est pas indexé en BDD

`_run_archivist` écrit le fichier mais n'enregistre pas d'`artifact` row pour celui-ci. Conséquence : le summary échappe à l'inventaire des artefacts (tu ne le retrouverais pas via une query SQL). Petite incohérence avec le reste.

**Sévérité** : faible. Une ligne à ajouter (`db.record_artifact(conn, ..., "summary.md", "summary")`).

#### A8. `archivist` ne devrait pas émettre `delegate_to` ni `ask_human`

Sa mission est mécanique : prendre un input, produire un output structuré. Il n'a aucun cas d'usage légitime pour les outils de contrôle. Pourtant le squelette injecte tous les contrôles à tous les agents (`prompts.py:CONTROL_TOOLS_SCHEMA`).

**Sévérité** : faible-moyenne. Un archivist mal aligné pourrait techniquement émettre `delegate_to(jean-michel, ...)` et créer une boucle infinie. Aujourd'hui ça ne se produit pas par pure chance / paradigme `archivist_format`.

**Solution** : permettre à `prompts.tools_payload_for_agent` de filtrer les contrôles eux-mêmes selon le rôle. Un `finalizer` n'a besoin que de `return_to_user`. Un `router` a besoin des trois.

---

## 2. Audit de la matrice paradigmes × agents × modes

### 2.1 Méthode

J'ai exécuté la requête `load_paradigms_for_agent` (db.py:63) pour chaque combinaison (agent, mode), à partir de la BDD installée par `db/schema.sql`. Résultats bruts :

| Agent | analyse | chat | vocal |
|---|---:|---:|---:|
| jean-michel | 15 | 17 | 17 |
| summarizer | 13 | 13 | 14 |
| synthesizer | 13 | 13 | 14 |
| weather-specialist | 16 | 16 | 17 |
| wikipedia-specialist | 17 | 17 | 18 |
| comparator-specialist | 16 | 16 | 17 |
| archivist | 15 | 15 | 15 |

### 2.2 Anomalies identifiées

#### P1. Paradigmes "code-tier" injectés à des spécialistes de domaine

**`audit_phase`** (catégorie `process/audit`) parle d'architecture, naming, helpers, call stacks, dette technique. Conçu pour des agents qui touchent du code. Or il est lié explicitement à :
- `weather-specialist` (mission : appeler une API météo et reformater le JSON)
- `wikipedia-specialist` (mission : chercher un article et extraire l'extrait pertinent)

Ces deux agents reçoivent donc une directive demandant de "tracer les piles d'appel" et "flag side effects, edge cases, technical debt" alors qu'ils n'ont strictement aucune raison de manipuler ces concepts.

**Hypothèse de l'origine** : tu voulais `audit_phase` pour forcer ces spécialistes à parser le briefing avant d'agir. Mais le contenu du paradigme parle de tout autre chose — c'est le mauvais paradigme pour le bon objectif.

**Recommandation** : créer un paradigme `parse_briefing_first` (catégorie `process/execution`) générique qui dit "Read and interpret the briefing fully before any tool call. Identify the concrete deliverable expected." Et débrancher `audit_phase` des deux spécialistes.

#### P2. `brutal_truth` est global mais inadapté à plusieurs rôles

`brutal_truth` est un paradigme **stylistique** ("treat the human as someone whose progress depends on hearing the truth, not on being coddled"). Il est marqué `is_global=1` donc injecté à tous les agents.

Conséquences :
- **archivist** reçoit l'instruction d'être brutalement honnête… alors qu'il produit un fichier structuré sans interlocuteur humain direct.
- **summarizer** reçoit cette instruction… alors qu'il doit rester strictement fidèle à la source, pas commenter.
- **weather-specialist** reçoit cette instruction… pour un rapport météo.
- **wikipedia-specialist** : encyclopedic, neutralité de ton requise — l'inverse de "brutal".

Le paradigme est utile **pour jean-michel** quand il s'adresse à l'humain. Pas ailleurs.

**Recommandation** : `is_global=0` pour `brutal_truth`, lier explicitement à `jean-michel`.

#### P3. `depth_over_speed` global vs `concise_output` (vocal) : conflit explicite

`depth_over_speed` (global) : *"Full structural analysis before any decision. Depth over speed."*
`concise_output` (vocal) : *"Keep the user-facing answer under 4 short sentences."*

En mode vocal, jean-michel reçoit les deux. C'est le LLM qui doit arbitrer. Conflictuel, et le résultat dépendra du modèle.

**Recommandation** : marquer `depth_over_speed` avec `paradigm_modes` = `analyse` (ou `analyse + chat`), pour qu'il disparaisse en `vocal`. La conversation rapide n'a pas besoin de "depth over speed" — c'est exactement le contraire qui est attendu.

#### P4. `concise_output` lié à `comparator-specialist` en vocal — conflit avec `structured_verdict`

`structured_verdict` (lié à comparator) demande **3 sections numérotées** (data per entity / side-by-side / verdict).
`concise_output` (lié à comparator en vocal) demande **moins de 4 phrases courtes**.

Impossible de respecter les deux. Conflit franc.

**Recommandation** : retirer `concise_output` de `comparator-specialist`. Le mode vocal sur une comparaison demanderait un sous-paradigme dédié `comparison_concise` (ou laisser tel quel : la concision finale est le job du synthesizer).

#### P5. `one_question_at_a_time` injecté à `archivist`

L'archivist n'utilise pas `ask_human` (whitelisté côté orchestrateur — il est appelé directement, pas via delegate_to). Le paradigme est inutile dans son contexte. Pas grave, mais c'est du bruit dans le prompt.

#### P6. Couverture des modes inégale

Aucun paradigme `chat`-only n'est lié à autre chose que jean-michel (`followup_proposals`).
Aucun paradigme `vocal`-only n'est lié à `archivist`.

C'est probablement OK (intention design : seul jean-michel pilote la conversation, l'archivist est invariant), mais à valider explicitement plutôt que par omission.

#### P7. La table `paradigm_modes` est sous-exploitée

3 paradigmes seulement ont des restrictions de mode (`followup_proposals`, `concise_output`, `no_context_recap`). Tous les autres sont "tous modes" par défaut.

C'est cohérent avec la convention "absence de ligne = tous modes", mais ça veut dire que le système de modes n'a aujourd'hui un effet que sur **3 paradigmes**. La complexité ajoutée (table, requête SQL plus longue, UI matrice) sert peu. Symétriquement, ça veut dire qu'il y a probablement plus de paradigmes qui devraient être mode-restreints (cf. P3 sur `depth_over_speed`).

### 2.3 Le ressenti "associations bizarres" est confirmé

La perception de bancalité est **réelle**. Elle n'est pas due à un bug du moteur de filtrage (la requête SQL est correcte), mais à un **biais d'accumulation** : à chaque ajout d'agent, des paradigmes ont été liés à la va-vite, et l'effet cumulé sur les agents anciens (qui se sont vus injecter de nouveaux paradigmes globaux) n'a jamais été audité.

**C'est le moment de faire ce ménage**. Voir partie 3.

### 2.4 Une question d'architecture qui se pose maintenant

Tu as 3 dimensions de paradigme :
1. **Globalité** (`is_global` colonne)
2. **Liaison explicite par agent** (`agent_paradigms` table)
3. **Restriction de mode** (`paradigm_modes` table)

Chaque dimension a sa propre convention de "vide = tout". C'est gérable mais ça commence à devenir non-trivial à raisonner. La matrice d'éditeur web doit gérer 3 dimensions simultanément, et l'œil humain a du mal.

**Piste de simplification possible (à débattre)** : remplacer `is_global` par une convention "un agent virtuel `*` qui reçoit les globaux", de sorte qu'il n'y ait plus qu'**un seul mécanisme** : la liaison explicite. Tu gardes la sémantique mais tu unifies l'arborescence mentale.

Pas urgent. À évaluer si le système prend de l'ampleur.

---

## 3. Audit de l'utilisation du dossier de conversation

### 3.1 Ce qui marche

- Format `[yyyy-MM-dd_HH-mm]_[uuid]/` : tri lexicographique = tri chronologique. ✓
- Nommage `HHMMSSmmm_<agent>_<kind>.md` (millisecondes ajoutées récemment) : règle les collisions des 2 modèles parallèles. ✓
- Frontmatter YAML systématique sur chaque fichier. ✓
- `conversation.md` comme journal append-only humain-lisible. ✓
- `summary.md` au niveau racine du dossier — une seule source vérité, écrasée à chaque tour. ✓
- Path traversal bloqué dans `conv_read_file`. ✓

### 3.2 Points faibles

#### F1. Le journal `conversation.md` peut être pollué par les contradictions

C'est le risque que tu avais identifié toi-même : sur une conversation longue, le journal contient des questions/réponses qui ont été révisées plus tard. Il sert de trace humaine, c'est OK, mais son utilisation comme source d'info structurée serait biaisée.

**Aujourd'hui** : seul le `summary.md` (mis à jour par l'archivist) est ré-injecté dans le prompt. Le journal n'est jamais lu par le LLM. Donc OK. Mais à formaliser explicitement dans la doc (sinon un futur dev sera tenté de l'utiliser).

#### F2. Les artefacts en dossier plat deviennent illisibles vite

Sur une conversation de 5 tours avec 3 délégations chacune, on atteint vite **30-50 fichiers à plat**. Le tri chronologique aide mais la lecture humaine se complique.

**Pistes** (par ordre de coût croissant) :
- Préfixer le nom de fichier par `T{turn_index}_` → `T2_153011_summarizer_response.md`. Trivial à faire.
- Sous-dossier `turn_{N}/` par tour humain. Casserait le tri chronologique global en lexicographique, mais améliorerait la navigation.
- Une vue dédiée dans `inspect_conv` (tu l'as déjà côté debug, c'est probablement la voie la plus simple).

#### F3. Pas de mécanisme de purge automatique au-delà d'un seuil

`debug/clean_convs.py` purge par âge en jours, c'est manuel. Sur une utilisation intensive, le dossier `conversations/` grossit indéfiniment. Pas critique mais à surveiller.

#### F4. `support_files` n'est jamais validé contre l'inventaire DB

Si jean-michel passe `support_files=["fichier_inexistant.md"]` à `summarizer`, le `conv_read_file` retournera une erreur côté LLM. C'est correct. Mais l'orchestrateur pourrait faire un check préalable plus utile : *"agent X demande à lire FILENAME, ce filename n'existe pas dans `artifacts`, on rejette en amont avec un message plus parlant"*. Mineur.

### 3.3 Cartographie logique des enchaînements agents

J'ai vérifié la cohérence du graph de routing implicite tel que défini par les paradigmes :

```
                         humain
                           ↓
                      jean-michel
                           ↓
              ┌────────────┼────────────┐
              ↓            ↓            ↓
           summa-      compara-    weather-/
           rizer        tor       wikipedia-
              │            ↓            ↓
              │       ┌────┴────┐       │
              │       ↓         ↓       │
              │   weather-  wikipedia-  │
              │   specialist  specialist│
              │            │            │
              └────────────┼────────────┘
                           ↓
                    synthesizer (si N≥2)
                           ↓
                         humain
                           ↓
                      archivist (si chat/vocal)
                           ↓
                       summary.md
```

**Constat** : le graph est cohérent. Pas de cycle non maîtrisé, pas de dead-end. La récursion comparator → wikipedia/weather est bien limitée par la profondeur (depth=1 → 2, capé à 5).

**Nuance** : `wikipedia-specialist` pourrait théoriquement faire un `delegate_to(comparator-specialist)` (rien ne l'interdit en BDD ni en code). Aucune raison qu'il le fasse, mais le système ne l'empêche pas non plus.

**Recommandation** (faible priorité) : ajouter un paradigme `no_self_referential_delegation` aux specialists, ou un check côté orchestrateur "un specialist ne peut déléguer qu'à un autre specialist plus spécifique". Ce dernier est complexe à formaliser ; je penche pour la première option (paradigme).

---

## 4. Faut-il migrer vers LangGraph ?

### 4.1 Ce qu'est LangGraph

LangGraph est un framework d'orchestration en **graphe d'états**, conçu spécifiquement pour les workflows LLM. On définit :
- des **nodes** (fonctions Python qui prennent un state et retournent un state modifié)
- des **edges** (transitions, conditionnelles ou non)
- un **state schema** (TypedDict ou dataclass)
- un **checkpointer** optionnel (Postgres, SQLite, MemorySaver) pour la reprise sur crash

Concrètement, sur un projet comme le tien, ça donnerait :

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

class State(TypedDict):
    user_input: str
    briefings: list
    final_answer: str
    ...

graph = StateGraph(State)
graph.add_node("jean_michel", run_jean_michel)
graph.add_node("summarizer", run_summarizer)
graph.add_node("synthesizer", run_synthesizer)

graph.add_conditional_edges("jean_michel", route_after_jean_michel,
    {"summarizer": "summarizer", "synthesizer": "synthesizer", "end": END})
graph.add_edge("summarizer", "synthesizer")
...

app = graph.compile(checkpointer=SqliteSaver.from_conn_string(":memory:"))
```

### 4.2 Ce que LangGraph t'apporterait

| Aspect | Apport |
|---|---|
| **Persistance et reprise** | Checkpointing natif. Si Ollama plante au milieu d'une délégation, redémarrer reprend où on en était. |
| **Streaming par node** | Tu peux streamer les events agent par agent côté CLI sans coder ton générateur à la main. |
| **Visualisation du graph** | `app.get_graph().draw_png()` génère une image du flow. Utile pour la doc et le debug. |
| **Human-in-the-loop natif** | `interrupt_before` sur un node = pause naturelle pour `ask_human`. Plus propre que ton bricolage actuel. |
| **Communauté + écosystème** | LangGraph 1.0 et 2.0 sortis fin 2025 / début 2026, recommandé par LangChain pour tout workflow agentique. Beaucoup de tutos, beaucoup d'exemples. |
| **Retry / reflection patterns** | Boucles "self-critique → refine" disponibles out of the box (le `reflection pattern`). |
| **Observability** | Intégration LangSmith pour tracer chaque appel. |

### 4.3 Ce que LangGraph te coûterait

#### 4.3.1 Coût technique

**Le minimum à abandonner ou refondre** :

1. **Ton orchestrateur** (~500 lignes). À jeter ou à transformer en wrapper sur StateGraph.
2. **Ton modèle de paradigmes en BDD**. LangGraph n'a pas de notion équivalente. Tu peux la garder, mais elle deviendra une couche au-dessus, plus une partie intégrée du framework.
3. **Ton system de tools** (`tools/__init__.py`, `tools/_base.py`). LangGraph a son propre wrapper de tools (via `langchain-core` ou MCP). Adaptation nécessaire.
4. **Ton client Ollama custom** (`llm.py`). LangGraph attend du `langchain_ollama.ChatOllama` ou équivalent. Le tien est plus simple, plus direct.
5. **Ta gestion d'event generator** côté CLI. À reconcevoir avec le streaming LangGraph.

**Estimation honnête** : 5-15 jours de travail de migration sérieuse, pas 2-3. Et tout ce temps c'est du temps qui ne va pas à améliorer le produit.

#### 4.3.2 Coût conceptuel

LangGraph impose son **modèle mental** :
- **State centralisé partagé** : tous les nodes lisent et écrivent dans un même state. C'est puissant mais c'est aussi un piège (les nodes deviennent couplés via le state).
- **Edges déclarés statiquement** : tu déclares `jean_michel → summarizer` au build-time du graph. C'est l'inverse de ton modèle actuel où jean-michel **décide dynamiquement** par tool_call. Tu pourrais simuler ça avec des edges conditionnels mais c'est moins naturel.
- **Tu codes le plomberie graph plus que la logique métier**.

Ton orchestrateur actuel a un avantage majeur : il est **lisible en une heure** par n'importe quel dev Python. LangGraph ajoute une couche d'abstraction qui masque la simplicité de "voici un humain → voici un agent qui décide → voilà des outils → on persiste".

#### 4.3.3 Coût d'ergonomie

Un point qui revient dans les retours d'expérience production (cf. recherche menée) :

> *"LangGraph's debugging story is still worse than a custom loop. If you need full control over observability and error handling, consider building the loop yourself."* — Kalvium Labs, retour de production sur 12 projets agentiques.

Quand quelque chose plante dans LangGraph, comprendre pourquoi nécessite de tracer le state à travers les nodes, comprendre les checkpoints, démêler le streaming. Avec ton orchestrateur, tu ouvres un fichier, tu lis 500 lignes, tu trouves.

### 4.4 Quand LangGraph deviendrait pertinent

Si demain tu rencontres un de ces cas, l'équation s'inverse :

1. **Tu veux un mode "self-critique"** où un agent évalue la sortie d'un autre et déclenche un retry. Ce pattern (reflection) est natif dans LangGraph, à coder à la main chez toi.
2. **Tu veux multi-utilisateur concurrent** sur la même instance. Le state management devient un cauchemar à coder. LangGraph + checkpointer Postgres = solution.
3. **Tu veux un graph dynamique éditable visuellement** (du genre "un workflow par projet, pas le même partout"). LangGraph + LangSmith Studio.
4. **Tu veux une forme de DAG complexe** où le routing dépend de plusieurs résultats agrégés. Aujourd'hui, jean-michel fait ça via tool_call, mais ça peut devenir compliqué à 10+ agents.
5. **Ton équipe grossit** et tu veux que les nouveaux n'aient pas à comprendre 500 lignes d'orchestrateur custom — préférer un framework standard avec doc.

### 4.5 Recommandation

**Ne migre pas maintenant. Continue à investir dans ton orchestrateur.**

Raisons :
1. Le système actuel **fonctionne**. Les anomalies relevées sont **toutes corrigeables en quelques heures**, sans framework.
2. Le ressenti de bancalité vient majoritairement de la matrice paradigmes (cf. partie 2), pas du moteur d'orchestration. Migrer ne corrigerait rien à ça — le problème est en BDD, pas en code.
3. Ton orchestrateur est **plus simple à raisonner** que LangGraph pour ton cas d'usage actuel (quelques agents, une conversation à la fois, local-first).
4. Tu as **investi** une compréhension fine de ton système. C'est précieux. Le jeter pour redémarrer avec une abstraction tierce, c'est jeter ce capital.
5. LangGraph ajoute des **dépendances lourdes** (LangChain core, langchain-ollama, etc.). Pour un projet "100% local, KISS", c'est un déménagement.

**Mais** garde LangGraph en réserve mentale. Si dans 6 mois tu ajoutes un mécanisme de retry-on-quality, ou si tu passes en multi-utilisateur, c'est probablement le moment.

**Position intermédiaire honnête** : tu peux **t'inspirer** des patterns LangGraph sans l'adopter. En particulier :
- Formaliser un `State` (tu en as déjà un implicitement : c'est `Orchestrator.self.*` + le summary + les artefacts).
- Le `reflection pattern` (un agent critique qui re-déclenche un agent). Implémentable comme un agent finalizer dans ton modèle.
- Le **checkpointing** : si une requête échoue, pouvoir reprendre. Aujourd'hui tu marques `failed` en BDD ; tu pourrais marquer le step exact et reprendre.

---

## 5. Plan de remédiation proposé

Par ordre de priorité, du plus rentable au plus cher.

### Phase A — Nettoyage paradigmes (½ journée)

1. **Débrancher `audit_phase`** de `weather-specialist` et `wikipedia-specialist`. Créer un paradigme `parse_briefing_first` à la place (catégorie `process/execution`).
2. **Marquer `brutal_truth` comme non-global** (`is_global=0`). Lier explicitement à `jean-michel` uniquement.
3. **Restreindre `depth_over_speed` aux modes `analyse` et `chat`** (entrées dans `paradigm_modes`).
4. **Retirer `concise_output`** de `comparator-specialist` (conflit avec `structured_verdict`).
5. **Auditer `archivist`** : retirer les paradigmes non pertinents (`brutal_truth`, `one_question_at_a_time`, `briefing_contract`).
6. **Re-générer la matrice** via `paradigm_matrix`, vérifier que chaque agent reçoit ce qu'il devrait.

**Outillage** : tu as déjà `debug/paradigm_matrix.py` qui gère ça en UI web. Suffit de bien l'utiliser.

### Phase B — Resserrage orchestrateur (1 journée)

1. **A3 — Tool response structuré pour `delegate_to`** : retourner `{"agent": ..., "artifact": ..., "answer": ...}` au lieu d'une string parsable. Mettre à jour la description du tool dans `prompts.py`.
2. **A2 — Retourner `(answer, artifact_filename)` depuis `_run_request`**, supprimer `_last_response_artifact` au niveau instance.
3. **A7 — Enregistrer `summary.md` comme artifact en BDD** dans `_run_archivist`.
4. **A8 — Filtrer les contrôles par rôle** : `finalizer` n'a que `return_to_user`, `specialist` a `return_to_user` + `delegate_to`, `router` a les trois. Ajouter un argument `agent.role` à `tools_payload_for_agent`.
5. **A4 — Sortir `max_steps` de l'orchestrateur** : soit en config (`config.MAX_STEPS_PER_REQUEST`), soit en colonne agent (`agents.max_steps`). Probablement la première.

### Phase C — Tests de régression (½ journée)

1. Ajouter un test de smoke par mode (`analyse`, `chat`, `vocal`) avec MockClient.
2. Ajouter un test sur la matrice paradigmes : "pour chaque agent et chaque mode, le set de paradigmes injecté est exactement celui attendu". Verrouille la régression de paradigmes.
3. Ajouter un test sur le flux `delegate_to` → support_files → conv_read_file.

### Phase D — Optionnel selon priorités

- **Délégation parallèle réelle** (A6) : passer l'orchestrateur en `async`, utiliser `asyncio.gather` pour les multiples `delegate_to` du même tour. Réécriture non triviale (1-2 jours) mais ça débloque le `comparator-specialist`.
- **Sous-dossier par tour** dans le dossier de conversation (F2) : amélioration de lisibilité.
- **Validation `support_files` côté orchestrateur** (F4) : message d'erreur plus parlant.

### Phase E — À ne PAS faire maintenant

- Migration LangGraph (cf. partie 4.5).
- Refonte du modèle de paradigmes (la convention 3-dimensions est gérable, ne pas la casser).
- Multi-utilisateur / persistance distribuée (pas un besoin actuel).

---

## 6. Conclusion

Le système Jean-Michel n'est pas bancal — il est **mal calibré**. La fondation (orchestrateur en générateur, paradigmes en BDD, tools modulaires, persistance) est solide et adaptée au scope. Le sentiment d'enchaînement logique faiblissant vient quasi exclusivement d'un **biais d'accumulation** dans la BDD : trop de paradigmes globaux, des liaisons paradigmes-agents qui n'ont pas été auditées depuis l'ajout des modes.

LangGraph répond à des problèmes que tu n'as pas. L'adopter maintenant, c'est ré-apprendre une abstraction pour résoudre un problème de configuration BDD. Le ratio coût/bénéfice est mauvais.

L'investissement le plus rentable, par ordre :
1. **½ journée** de ménage paradigmes (Phase A) — gain qualité immédiat, ratio explosif.
2. **1 journée** de resserrage orchestrateur (Phase B) — élimine les pièges latents.
3. **½ journée** de tests de régression (Phase C) — évite que ça se reproduise.

Total : **2 jours** de travail pour un système nettement plus solide. Bien moins coûteux qu'une migration framework, et infiniment plus utile.

Garde LangGraph dans la liste des outils que tu pourrais adopter le jour où un cas d'usage l'imposera vraiment (reflection patterns, multi-tenant, checkpointing distribué). Pas avant.
