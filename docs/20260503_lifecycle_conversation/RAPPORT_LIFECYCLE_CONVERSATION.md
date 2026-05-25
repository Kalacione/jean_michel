# Rapport — bugs des modes et refonte de la gestion conversation/artifacts

**Contexte** : tests réels post-recalibrage révèlent deux bugs distincts en mode `analyse`. L'utilisateur propose une refonte du cycle de vie des conversations. Ce rapport analyse les deux bugs séparément, valide la refonte proposée, et fournit un plan d'implémentation prêt à exécuter en local.

---

## 1. Symptômes observés (depuis `convers.log`)

**Symptôme 1** — *coupure après réponse en mode analyse*
Lancement 1 : "philosophie des stoïciens" → réponse complète → CLI se ferme → user doit relancer `./jm.sh` pour la conversation suivante.

**Symptôme 2** — *questions multiples non passées par `ask_human`*
Conversation "slip ou caleçon", 2e tour après réponse humaine sur "sous-vêtements masculins" :
> Pour vous aider à déterminer si un slip ou un caleçon est le meilleur choix, j'aurais besoin de savoir ce qui est le plus important pour vous. Cherchez-vous :
> 1. Le confort maximal pour une utilisation quotidienne ?
> 2. Un soutien accru ou une compression ?
> 3. Un style particulier ?
> 4. Un vêtement adapté à une activité spécifique ?

Ces 4 questions sont passées via `return_to_user` au lieu de `ask_human`. Le user n'a aucun moyen de répondre dans le flux : la CLI attend un nouvel input racine, pas une réponse.

---

## 2. Diagnostic

### 2.1 Bug 1 — coupure en mode analyse

**Cause directe** : `cli.py:265-266`

```python
if args.mode == "analyse":
    break  # single-shot in analyse mode
```

La boucle CLI sort dès qu'une requête est complète en mode `analyse`. C'était le design initial *"analyse = one-shot"*. **Avec l'ajout d'`ask_human`, ce design devient incohérent** : si jean-michel pose une question, reçoit une réponse, et finit avec `return_to_user`, la CLI se coupe juste après — l'utilisateur ne peut pas faire de tour suivant sans relancer `./jm.sh`. C'est exactement ce que montre le log entre lancement 1 et lancement 2.

**Sévérité** : élevée. Casse le cas d'usage le plus fréquent (conversation iterative), même en mode "one-shot intentionnel" l'humain veut souvent enchaîner.

### 2.2 Bug 2 — questions multiples non routées vers `ask_human`

**Cause directe** : désynchronisation entre la BDD et le code Python.

Au tour-22, on a modifié le paradigme `one_question_at_a_time` (id 4) pour clarifier la tension avec `address_then_clarify` :

> Avant : *"One question per ask_human call. Never a list of questions."*
> Après : *"One ask_human call per request, with a focused scope. If multiple clarifications are genuinely needed and share the same blocker, group them into a coherent set within a single call."*

**Mais on n'a pas mis à jour `prompts.py`** qui contient en dur deux endroits avec l'ancien message :

```python
# Description du tool ask_human
"description": "Pause the request and ask the human a single question. ..."

# OUTPUT CONTRACT
"- If you must clarify with the user: call ask_human(question, why). "
"One question only. `why` is mandatory."
```

Le LLM voit donc **trois signaux contradictoires** dans son prompt système :
- Section `# DIRECTIVES` (paradigme injecté de la BDD) : "regroupe si même blocage"
- Bloc `# OUTPUT CONTRACT` : "une seule question, c'est tout"
- Description du tool `ask_human` : "a single question"

Face à cette confusion, Gemma a fait le choix qu'on lui a implicitement appris (**2 sources sur 3 disent "single"**) : ne pas appeler `ask_human` avec 4 questions, et basculer sur `return_to_user` avec le questionnaire en texte. C'est un comportement défensif logique du modèle, pas une hallucination.

**Sévérité** : élevée. Casse le pattern de clarification multi-question qu'on a explicitement créé.

### 2.3 Diagnostic global

Les deux bugs ne sont **pas liés** entre eux mais ils sont tombés ensemble lors des tests parce qu'ils touchent tous deux la couche d'interaction utilisateur en mode `analyse`. Bug 1 = comportement CLI. Bug 2 = désynchro BDD/Python.

La proposition de l'utilisateur ("créer le dossier au lancement, itérer dedans, mode analyse comme mode chat") **ne résout que Bug 1**. Bug 2 doit être réglé indépendamment, par mise à jour de `prompts.py`.

---

## 3. Analyse de la refonte proposée

### 3.1 La proposition

> *"On crée un dossier de conversation au lancement de jm.sh et puis c'est tout, on itère dedans (le mode analyse fait comme le mode chat). On pourrait même rajouter une option resume à notre jm.sh pour reprendre une conversation dans son contexte."*

### 3.2 Ce qui est juste

**Le découpage actuel est artificiel.** Aujourd'hui :
- Le dossier est créé au premier `run()` (lazy)
- En `analyse`, la CLI se coupe après 1 réponse → 1 dossier = 1 paire question/réponse
- En `chat`/`vocal`, la CLI itère → 1 dossier = N tours, archivist tient un summary

Cette asymétrie a **3 conséquences indésirables** :
1. Bug 1 (coupure)
2. Pas de continuité possible en analyse même quand l'utilisateur le souhaite
3. Pas de moyen de reprendre une conversation interrompue (crash, EOF, machine éteinte)

**La refonte unifie le cycle de vie** : 1 lancement = 1 dossier, peu importe le mode. Le mode reste utile, mais devient une **question de comportement** (concision pour vocal, follow-ups pour chat) et plus une question de **lifecycle**.

### 3.3 Ce qui doit être tranché explicitement

#### Q1 — L'archivist en mode analyse ?

L'archivist tourne aujourd'hui uniquement en `chat`/`vocal`. Il maintient `summary.md` qui est ré-injecté au tour suivant.

Si `analyse` devient itératif, deux options :

**Option A** — *L'archivist tourne aussi en analyse.*
Cohérent avec "analyse fait comme chat". Mais coût supplémentaire d'un appel LLM par tour (l'archivist est un agent à part entière).

**Option B** — *L'archivist reste hors `analyse`.*
En analyse, pas de summary, pas de continuité narrative entre tours. Chaque tour repart "à blanc" mais réutilise le même dossier.

**Reco** : **Option B**, pour préserver la sémantique des modes. La distinction reste : `analyse` = tours indépendants stockés dans le même dossier, `chat`/`vocal` = tours liés via summary. Sinon les modes deviennent indistincts.

#### Q2 — Quel comportement reste spécifique au mode analyse ?

Si on supprime la coupure CLI, **qu'est-ce qui définit encore "analyse"** ?

Aujourd'hui après la refonte :
- Pas de followup_proposals (paradigme `chat`-only)
- Pas de concise_output (paradigme `vocal`-only)
- Pas de no_context_recap (paradigme `chat`+`vocal`)
- Plein des paradigmes de réflexion profonde (`depth_over_speed`, `metacognitive_pause`, `assumption_surface`, `steelman_first`, `hold_tension`, `slow_question_slow_answer`)
- Pas d'archivist → pas de summary → pas de prefix_summary entre tours

→ **Le mode analyse reste cohérent** : c'est le mode "réflexion sans mémoire conversationnelle". Chaque tour est traité avec profondeur sur sa propre demande, sans ré-injection des tours précédents.

C'est un cas d'usage légitime : "j'ai 3 questions indépendantes profondes à poser dans la même session, je ne veux pas que la 3e soit polluée par le contexte des 2 premières".

#### Q3 — Que faire du `summary.md` en `analyse` ?

Pas créé du tout. Cohérent avec Q1+Q2.

#### Q4 — Comportement de `--resume`

Quelle conversation reprendre ?
- **Reco** : si `--resume <conv_id>` est passé : reprend exactement celle-là. Si `--resume` seul (sans argument) : reprend la dernière conversation active de la BDD.
- Le mode est récupéré depuis la BDD (`conversations.mode`). Pas d'override possible.
- Si conversation introuvable ou clos : message d'erreur, pas de fallback silencieux vers une nouvelle conversation.

#### Q5 — Statut "active" / "closed"

Aujourd'hui le statut existe (`conversations.status`) mais n'est jamais passé à `closed`. Pour `--resume`, il faut un signal qui distingue "conversation reprenable" de "conversation finie".

**Reco** :
- À l'EOF/quit en CLI, la conversation passe `closed` automatiquement (sauf s'il y a un `ask_human` en suspens, dans lequel cas elle reste `awaiting_human`).
- `--resume` ne reprend que les conversations `active` ou `awaiting_human`.
- Une commande dédiée `--list-conv` permettrait de lister les conversations existantes (utile pour le user qui ne se souvient plus du conv_id).

---

## 4. Décision recommandée

**Mes 5 réponses condensées** (à valider avant le plan) :

| Question | Réponse |
|---|---|
| Q1 archivist en analyse | Non, reste hors `analyse` (préserve la sémantique des modes) |
| Q2 spécificité analyse | "Réflexion sans mémoire" = pas d'archivist, pas de summary, paradigmes de profondeur quand même |
| Q3 summary.md en analyse | Pas créé |
| Q4 `--resume` | `--resume <conv_id>` ou `--resume` seul (= dernière conversation active) ; mode hérité de la BDD |
| Q5 statut conversation | Passage auto à `closed` à l'EOF (ou `awaiting_human` si question en suspens) ; resume sur active/awaiting_human seulement |

Si tu valides, on passe au plan. Sinon dis-moi laquelle modifier.

---

## 5. Plan d'implémentation

Pour exécution en local sans agent autonome — donc plan détaillé mais sans découpage sub-agents.

### Phase 0 — Pré-requis (5 min)

- [ ] Backup BDD courante : `cp jeanmichel.db jeanmichel.db.bak.$(date +%Y%m%d-%H%M)`
- [ ] Branche git dédiée : `git checkout -b fix/conversation-lifecycle`
- [ ] Smoke test avant modif : `./jm.sh` en analyse, poser une question simple, vérifier le bug 1 reproduit (coupure post-réponse)

### Phase 1 — Fix Bug 2 (désynchro `prompts.py`) — 15 min

C'est isolé du reste, à régler en premier pour pouvoir tester le bug 2 indépendamment.

**Tâche 1.A** — Mettre à jour la description du tool `ask_human` dans `prompts.py` :

```python
# Remplacer
"description": "Pause the request and ask the human a single question. "
               "`why` is mandatory and must explain what is blocked without it.",

# Par
"description": ("Pause the request and ask the human for clarification. "
                "Use a single, focused call per request. If multiple "
                "questions are genuinely needed and share the same "
                "blocker, group them in `question` as a coherent list "
                "with one shared `why`."),
```

**Tâche 1.B** — Mettre à jour le bloc OUTPUT CONTRACT dans `prompts.py` :

```python
# Remplacer
"- If you must clarify with the user: call ask_human(question, why). "
"One question only. `why` is mandatory.\n"

# Par
"- If you must clarify with the user: call ask_human(question, why). "
"Only one ask_human call per request; group related questions "
"sharing the same blocker. `why` is mandatory.\n"
```

**Tâche 1.C** — Test : relancer le scénario "slip ou caleçon", vérifier que le 2e tour appelle bien `ask_human` avec les 4 questions au lieu de `return_to_user` avec un texte.

**Critère d'acceptation Phase 1** : 4 questions liées sont passées en 1 seul `ask_human`.

### Phase 2 — Refonte cycle de vie conversation — 1h30

#### 2.A — `cli.py` : suppression du break analyse, création du dossier au lancement

Modifier la fonction `main()` :

```python
# Avant la boucle while True
orch = Orchestrator(llm=llm, profile=profile, mode=args.mode,
                    ask_human_callback=make_ask_human(console, session))
orch.bootstrap_conversation()  # NEW: crée le dossier maintenant, pas au 1er run()

# Dans la boucle, supprimer
if args.mode == "analyse":
    break  # single-shot in analyse mode
```

#### 2.B — `orchestrator.py` : extraction de la logique d'init

Refactor : sortir la création du dossier de `run()` vers une nouvelle méthode `bootstrap_conversation()` :

```python
def bootstrap_conversation(self) -> None:
    """Create the conversation folder and DB row. Idempotent.
    
    Called once at CLI startup, before any user input. After this call,
    self.conv_folder is set and the conversation row exists in DB with
    status='active'.
    """
    if self.conv_folder is not None:
        return  # already bootstrapped
    started = datetime.now(UTC)
    folder_name = conversation_folder_name(self.conv_id, started)
    self.conv_folder = config.CONVERSATIONS_DIR / folder_name
    self.conv_folder.mkdir(parents=True, exist_ok=True)
    with db.connect() as conn:
        db.create_conversation(
            conn, self.conv_id, str(self.conv_folder),
            user_language="und",  # détecté au 1er tour
            mode=self.mode,
        )
    self.turn_index = -1  # premier run() incrémentera à 0

def run(self, user_input: str) -> Generator[object]:
    self.user_language = _detect_language(user_input)
    self._turn_exchanges = []
    self.turn_index += 1  # 0 au premier tour, 1+ ensuite
    
    # Mise à jour de la langue détectée si elle change (rare mais possible)
    if self.turn_index == 0:
        with db.connect() as conn:
            db.update_conversation_language(conn, self.conv_id, self.user_language)
    
    if self.turn_index == 0:
        yield ConversationStarted(...)
    else:
        yield TurnStarted(turn_index=self.turn_index)
    
    # ... le reste reste pareil
```

**Note** : `_prefix_summary` reste inchangé. En `analyse`, il retourne user_input tel quel (le mode est testé). En `chat`/`vocal`, il préfixe le summary si dispo. Donc la logique itérative en `analyse` repart "à blanc" à chaque tour, ce qui est cohérent avec la décision Q2.

#### 2.C — Ajout de `db.update_conversation_language`

Petite fonction utilitaire :

```python
def update_conversation_language(conn, conv_id: str, language: str) -> None:
    conn.execute(
        "UPDATE conversations SET user_language=?, modified_at=datetime('now') "
        "WHERE id=?",
        (language, conv_id),
    )
    conn.commit()
```

#### 2.D — Statut conversation à la fermeture

Modifier `cli.py:main()` à la sortie (try/except qui catche EOFError/KeyboardInterrupt) :

```python
except (EOFError, KeyboardInterrupt):
    console.print("\n[dim]bye.[/]")
    orch.close_conversation()  # NEW: marque la conversation comme closed
    return 0
```

Et dans `orchestrator.py` :

```python
def close_conversation(self) -> None:
    """Mark the conversation as closed in DB. Safe to call multiple times."""
    if self.conv_folder is None:
        return
    with db.connect() as conn:
        # Si une requête est en awaiting_human, on garde le statut tel quel.
        # Sinon on passe en closed.
        row = conn.execute(
            "SELECT 1 FROM requests WHERE conversation_id=? "
            "AND status='awaiting_human' LIMIT 1",
            (self.conv_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                "UPDATE conversations SET status='closed', "
                "modified_at=datetime('now') WHERE id=?",
                (self.conv_id,),
            )
            conn.commit()
```

**Critère d'acceptation Phase 2** :
- En mode analyse, après une réponse, la CLI invite à un nouveau prompt (pas de coupure).
- Tous les artefacts du 2e tour (et suivants) sont dans le **même dossier** que le 1er.
- À l'EOF (Ctrl-D), la conversation passe `status='closed'` en BDD.
- En mode chat, summary.md est créé entre les tours (comportement inchangé).
- En mode analyse, summary.md n'est **pas** créé (comportement inchangé).

### Phase 3 — `--resume` et `--list-conv` — 1h

#### 3.A — Ajout des arguments CLI

Dans `cli.py`, étendre l'argparser :

```python
parser.add_argument("--resume", nargs="?", const="__last__", default=None,
    metavar="CONV_ID",
    help="Resume a conversation. With CONV_ID: that conversation. "
         "Without: the most recent active or awaiting_human conversation.")
parser.add_argument("--list-conv", action="store_true",
    help="List recent conversations (active and awaiting_human) and exit.")
```

#### 3.B — Implémentation `--list-conv`

```python
if args.list_conv:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, mode, status, user_language, created_at, modified_at "
            "FROM conversations "
            "WHERE status IN ('active','awaiting_human') "
            "ORDER BY modified_at DESC LIMIT 20"
        ).fetchall()
    if not rows:
        console.print("[dim]No active conversation.[/]")
        return 0
    # Render table via rich
    from rich.table import Table
    table = Table(title="Active conversations")
    for col in ("conv_id", "mode", "status", "lang", "created", "last activity"):
        table.add_column(col)
    for r in rows:
        table.add_row(r["id"][:12], r["mode"], r["status"], 
                      r["user_language"], r["created_at"], r["modified_at"])
    console.print(table)
    return 0
```

#### 3.C — Implémentation `--resume`

```python
if args.resume is not None:
    with db.connect() as conn:
        if args.resume == "__last__":
            row = conn.execute(
                "SELECT id, folder_path, mode, user_language FROM conversations "
                "WHERE status IN ('active','awaiting_human') "
                "ORDER BY modified_at DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, folder_path, mode, user_language FROM conversations "
                "WHERE id=? OR id LIKE ?",
                (args.resume, args.resume + '%'),  # accepte préfixes
            ).fetchone()
    if row is None:
        console.print(f"[red]Conversation not found or already closed.[/]")
        return 1
    
    # Bootstrap orchestrator from existing conversation
    orch = Orchestrator(llm=llm, profile=profile, mode=row["mode"],
                        conv_id=row["id"],
                        ask_human_callback=make_ask_human(console, session))
    orch.resume_conversation(folder_path=row["folder_path"],
                             user_language=row["user_language"])
    
    # Override args.mode pour cohérence affichage
    args.mode = row["mode"]
```

#### 3.D — Méthode `resume_conversation` dans Orchestrator

```python
def resume_conversation(self, folder_path: str, user_language: str) -> None:
    """Reattach to an existing conversation folder.
    
    Sets self.conv_folder to the existing path, restores turn_index from
    the highest turn_index in DB requests, and re-activates the row if it
    was 'closed'.
    """
    self.conv_folder = Path(folder_path)
    if not self.conv_folder.exists():
        raise FileNotFoundError(f"Conversation folder missing: {folder_path}")
    self.user_language = user_language
    with db.connect() as conn:
        row = conn.execute(
            "SELECT MAX(turn_index) AS max_turn FROM requests "
            "WHERE conversation_id=? AND parent_request_id IS NULL",
            (self.conv_id,),
        ).fetchone()
        self.turn_index = (row["max_turn"] if row["max_turn"] is not None else -1)
        # Reactivate if closed
        conn.execute(
            "UPDATE conversations SET status='active', "
            "modified_at=datetime('now') WHERE id=? AND status='closed'",
            (self.conv_id,),
        )
        conn.commit()
```

**Critère d'acceptation Phase 3** :
- `./jm.sh --list-conv` affiche les conversations actives.
- `./jm.sh --resume <conv_id>` reprend la conversation, le summary.md (si existant) est ré-injecté au prochain tour.
- `./jm.sh --resume` (sans arg) reprend la dernière active.
- `./jm.sh --resume <id_inexistant>` → erreur claire, exit code 1.

### Phase 4 — Tests et documentation — 30 min

**Tests manuels minimum** :

1. **Bug 1 régression** : analyse, 3 questions enchaînées dans la même session → 1 seul dossier, 3 turns en BDD.
2. **Bug 2 régression** : "slip ou caleçon" + 2e tour → `ask_human` avec 4 questions groupées.
3. **Resume** : conversation analyse, Ctrl-D, `./jm.sh --resume`, poser une 4e question → toujours dans le même dossier, turn_index continue.
4. **List-conv** : créer 3 conversations, en clore 1 explicitement (Ctrl-D), `--list-conv` n'affiche que les 2 actives.
5. **ask_human + close** : poser une question, ne pas y répondre, Ctrl-D → conversation reste `awaiting_human` en BDD, `--list-conv` la montre, `--resume` la reprend et présente la question.

**Documentation** :
- `README.md` : section CLI mise à jour avec les nouvelles options et la suppression du comportement one-shot en analyse.
- `docs/PROMPT_SKELETON.md` : confirmer que `ask_human` accepte un groupe de questions (mettre à jour la phrase qui mentionne "single question" si elle existe encore).

---

## 6. Estimation totale

| Phase | Durée |
|---|---|
| 0 — Backup + branche | 5 min |
| 1 — Fix bug 2 (`prompts.py`) | 15 min |
| 2 — Refonte cycle de vie | 1h30 |
| 3 — `--resume` et `--list-conv` | 1h |
| 4 — Tests + doc | 30 min |
| **Total** | **~3h30** |

Phases 1 et 2 indépendantes — peuvent être merged séparément si tu veux fixer les bugs sans attendre `--resume`. Phase 3 dépend de Phase 2.

---

## 7. Risques et points d'attention

**R1 — Migration des conversations existantes**
Les conversations actuelles en BDD ont un `status='active'` qui ne signifie rien (elles sont en réalité finies). Au premier `--list-conv`, elles vont apparaître. Reco : avant la première utilisation après merge, exécuter manuellement :
```sql
UPDATE conversations SET status='closed' WHERE created_at < datetime('now');
```
Pas critique, juste cosmétique.

**R2 — `turn_index` après resume**
Le calcul `MAX(turn_index)` ne compte que les requêtes racines (parent_request_id IS NULL). C'est volontaire — les sous-délégations n'incrémentent pas le turn_index. Vérifier qu'aucune autre partie du code ne suppose une numérotation continue.

**R3 — Race entre Ctrl-D et ask_human en cours**
Si l'utilisateur fait Ctrl-D pendant qu'une requête est en cours d'exécution (LLM en train de générer), le hook `close_conversation()` peut s'exécuter avant que le statut de la requête soit finalisé. Le check sur `awaiting_human` couvre le cas le plus important, mais une requête en `running` à ce moment-là restera "running" en BDD. Mineur, mais à connaître.

**R4 — Interaction avec les modifications futures**
La phase d'implémentation workspace+sandbox (planifiée dans `PLAN_WORKSPACE_SANDBOX.md`) suppose des conversations qui durent. La refonte ici **renforce** ce besoin (puisqu'on aura des sessions plus longues), donc compatible. Le hook de cleanup Docker prévu en Phase 2.D du plan workspace devra être appelé depuis `close_conversation()`. À ne pas oublier au moment d'intégrer les deux.
