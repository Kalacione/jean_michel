# Plan d'implémentation — modes Jean-Michel

## 1. Schéma DB — choix multi-mode

**Décision : table de jointure `paradigm_modes`.** Cohérent avec `agent_paradigms` qui existe déjà. Convention : **absence de ligne = applicable à tous les modes** (par parallélisme avec `is_global` côté agents).

```sql
CREATE TABLE paradigm_modes (
  paradigm_id INTEGER NOT NULL REFERENCES paradigms(id) ON DELETE CASCADE,
  mode        TEXT    NOT NULL CHECK (mode IN ('analyse','chat','vocal')),
  PRIMARY KEY (paradigm_id, mode)
);
```

**Rejeté** : colonne CSV (`applies_to_modes='chat,vocal'`). Filtre SQL sale (`LIKE`), pas d'intégrité référentielle, pénible à éditer en UI.

**Filtre paradigmes** (mise à jour de `db.load_paradigms_for_agent`) :

```sql
SELECT s.code AS section_code, c.code AS category_code, c.title AS category_title,
       p.code, p.title, p.content
FROM paradigms p
JOIN categories c ON c.id = p.category_id
JOIN sections   s ON s.id = c.section_id
WHERE p.active = 1 AND c.active = 1 AND s.active = 1
  AND (p.is_global = 1
       OR p.id IN (SELECT paradigm_id FROM agent_paradigms WHERE agent_id = :agent_id))
  AND (NOT EXISTS (SELECT 1 FROM paradigm_modes pm WHERE pm.paradigm_id = p.id)
       OR EXISTS (SELECT 1 FROM paradigm_modes pm WHERE pm.paradigm_id = p.id AND pm.mode = :mode))
ORDER BY s.order_priority, c.order_priority, p.order_priority, p.id
```

Le sous-`NOT EXISTS` exprime exactement la convention "aucune ligne = tous modes".

**Autres altérations :**

```sql
ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'analyse'
  CHECK (mode IN ('analyse','chat','vocal'));

ALTER TABLE requests ADD COLUMN turn_index INTEGER NOT NULL DEFAULT 0;
-- turn_index = 0 pour la première requête racine d'une conversation,
-- incrémenté à chaque nouveau tour humain. Sous-requêtes héritent du turn_index parent.

-- artifacts.kind accepte 'summary' (déjà dans la liste actuelle, ✓)
```

## 2. Nouvel agent `archivist`

```sql
INSERT INTO agents (code, name, role, mission, thinking_mode, temperature, ...)
VALUES ('archivist', 'Archivist', 'finalizer',
        'Maintain a structured running summary of the conversation. '
        'Resolve contradictions, surface evolving threads, in a direct factual tone.',
        1, 0.1, ...);
```

Ses paradigmes :
- Globaux usuels (no_filler, no_speculation, etc.)
- Bound spécifique : `archivist_format` qui définit le schéma `summary.md` (Established facts / Open threads / Resolved contradictions / User preferences observed)
- Bound spécifique : `archivist_tone` — "direct, factual, no narration, no transitions". Important pour ne pas avoir de prose.

**Important** : l'archivist n'est pas dispatchable par jean-michel via `delegate_to`. Il est invoqué uniquement par l'orchestrateur en post-loop. Pour éviter qu'un autre agent l'appelle par erreur, soit on filtre au niveau du paradigme de routing (jean-michel ne sait pas qu'il existe), soit on whiteliste dans le code de `delegate_to`. Reco : whiteliste code (KISS).

## 3. Paradigmes mode-spécifiques

À insérer en seed. Exemples concrets :

```sql
-- chat: relancer la conversation
INSERT INTO paradigms (category_id, code, title, content, is_global, ...) VALUES
((SELECT id FROM categories WHERE code='style' ...),
 'followup_proposals', 'Follow-up proposals',
 '- After delivering the answer, propose 2 to 3 specific angles the user might want to explore further.
- Format them as a short list, no preamble.
- If the answer is fully self-contained and no useful angle remains, do not force proposals.',
 0, ...);

-- vocal: concision
INSERT INTO paradigms (category_id, code, title, content, is_global, ...) VALUES
((SELECT id FROM categories WHERE code='style' ...),
 'concise_output', 'Concise output',
 '- Keep the user-facing answer under 4 short sentences.
- Headline first, details on demand.
- Offer to expand specific points: "Want me to detail X?".',
 0, ...);

-- chat + vocal: pas de répétition contextuelle
INSERT INTO paradigms (category_id, code, title, content, is_global, ...) VALUES
((SELECT id FROM categories WHERE code='style' ...),
 'no_context_recap', 'No context recap',
 '- A running summary is provided. Do not paraphrase or repeat what the user already knows.
- Address the new turn directly.',
 0, ...);
```

Bindings :

```sql
-- followup_proposals → jean-michel, chat only
INSERT INTO agent_paradigms ... 'jean-michel', 'followup_proposals';
INSERT INTO paradigm_modes (paradigm_id, mode) VALUES (..., 'chat');

-- concise_output → jean-michel + tous les specialists, vocal only
-- (tous les specialists pour que la rép. brute soit déjà courte, pas seulement le format final)
INSERT INTO agent_paradigms ... ('jean-michel','concise_output'), ('summarizer','concise_output'), ...;
INSERT INTO paradigm_modes (paradigm_id, mode) VALUES (..., 'vocal');

-- no_context_recap → jean-michel, chat ET vocal
INSERT INTO agent_paradigms ... 'jean-michel', 'no_context_recap';
INSERT INTO paradigm_modes (paradigm_id, mode) VALUES (..., 'chat'), (..., 'vocal');
```

Ce dernier illustre l'intérêt de la table de jointure : **un paradigme, deux modes, sans duplication**.

## 4. Propagation du mode

Le mode est porté par l'`Orchestrator` et lu depuis la conversation. Plus précisément :

- `Orchestrator.__init__` reçoit `mode: str`
- À la création de la conversation, `mode` est persisté dans la colonne `conversations.mode`
- À chaque construction de prompt, le `PromptContext` reçoit le mode et le filtre des paradigmes l'utilise
- Le bloc `## Conversation` du prompt contient `- mode: {mode}` (l'agent le voit dans son contexte mais ne décide rien — les paradigmes font le boulot)

```python
# orchestrator.py — signature
def __init__(self, llm, profile, mode: str = "analyse",
             conv_id: str | None = None, ask_human_callback=None) -> None:
    assert mode in {"analyse", "chat", "vocal"}
    self.mode = mode
    ...

# db.py
def load_paradigms_for_agent(conn, agent_id: int, mode: str) -> list[Paradigm]:
    # nouvelle signature, mode obligatoire
    ...
```

## 5. Continuité de la conversation

Le changement structurel principal. Aujourd'hui chaque `run()` crée une nouvelle conversation. Désormais :

- **Premier `run()`** : crée la conversation + le dossier
- **`run()` suivants** sur la même instance : ajoute une requête racine (parent=NULL) à la conversation existante, `turn_index += 1`

Refactor de `Orchestrator.run()` :

```python
def run(self, user_input: str) -> Generator[object, None, None]:
    self.user_language = _detect_language(user_input)

    if self.conv_folder is None:
        # Premier tour
        started = datetime.now(UTC)
        folder_name = conversation_folder_name(self.conv_id, started)
        self.conv_folder = config.CONVERSATIONS_DIR / folder_name
        self.conv_folder.mkdir(parents=True, exist_ok=True)
        with db.connect() as conn:
            db.create_conversation(conn, self.conv_id, str(self.conv_folder),
                                   self.user_language, self.mode)
        self.turn_index = 0
        yield ConversationStarted(...)
    else:
        # Tour suivant
        self.turn_index += 1
        yield TurnStarted(turn_index=self.turn_index)

    # Préfixer le summary courant à l'input humain (si présent)
    enriched_input = self._prefix_summary(user_input)

    answer = yield from self._run_request(
        agent_code="jean-michel",
        inbound_text=enriched_input,
        ...,
        depth=0,
        sender="human",
    )

    yield FinalAnswer(text=answer)

    # Archivist post-loop (chat/vocal uniquement)
    if self.mode in {"chat", "vocal"}:
        yield from self._run_archivist(user_input, answer)
```

Le `parent_request_id` reste NULL pour les requêtes racines, mais `conversation_id` les rattache. Si tu veux un lien explicite entre tours, ajoute `previous_root_request_id` plus tard — KISS d'abord.

## 6. Injection du `summary.md`

```python
def _prefix_summary(self, user_input: str) -> str:
    if self.mode == "analyse" or self.turn_index == 0:
        return user_input
    summary_path = self.conv_folder / "summary.md"
    if not summary_path.exists():
        return user_input
    summary = summary_path.read_text(encoding="utf-8").strip()
    return (
        "## Conversation summary so far\n"
        f"{summary}\n\n"
        "## New user turn\n"
        f"{user_input}"
    )
```

Préfixé au `user` message, pas dans le `system`. Raison : le summary est dynamique, le system reste structurel et stable. Cohérent avec la règle Gemma 4 sur les multi-tours.

## 7. Archivist post-loop

```python
def _run_archivist(self, last_user: str, last_answer: str):
    # Pas de delegate_to — l'archivist est invoqué directement.
    # Mêmes paradigmes/squelette qu'un agent normal, mais l'orchestrateur
    # crée la requête lui-même.
    previous_summary = ""
    summary_path = self.conv_folder / "summary.md"
    if summary_path.exists():
        previous_summary = summary_path.read_text(encoding="utf-8")

    briefing = (
        "Update the running summary.\n\n"
        f"## Previous summary\n{previous_summary or '(none)'}\n\n"
        f"## Latest user turn\n{last_user}\n\n"
        f"## Latest assistant answer\n{last_answer}\n\n"
        "Produce the new summary as the value of return_to_user. "
        "Follow the archivist_format paradigm strictly."
    )

    new_summary = yield from self._run_request(
        agent_code="archivist",
        inbound_text=briefing,
        expected_outcome="Updated running summary, structured per archivist_format.",
        support_files=[],
        parent_request_id=None,
        depth=0,                # n'incrémente pas la récursion (par doctrine)
        sender="orchestrator",
    )

    summary_path.write_text(new_summary, encoding="utf-8")
    with db.connect() as conn:
        db.record_artifact(conn, ..., "summary.md", "summary")
    yield SummaryUpdated(path=str(summary_path))
```

Note : `_run_request` retourne déjà un string (la valeur de `return_to_user`). Pas de modif côté orchestrateur core.

## 8. CLI

Changements ciblés dans `cli.py` :

**Flag `--mode`** :

```python
parser.add_argument("--mode", choices=["analyse","chat","vocal"], default="analyse")
```

**Boucle adaptée** :

```python
mode = args.mode
profile = UserProfile.load()
llm = OllamaClient(model=args.model)

if mode in {"chat", "vocal"}:
    _prewarm(llm, args.model, console)

# Une seule instance d'Orchestrator pour la session si interactif
orch = Orchestrator(llm=llm, profile=profile, mode=mode,
                    ask_human_callback=make_ask_human(console))

while True:
    user_input = Prompt.ask(f"[{C_USER}]you[/]")
    if user_input.strip().lower() in {"exit", "quit"}:
        break
    if not user_input.strip():
        continue
    render_events(console, orch.run(user_input), show_thoughts=args.show_thoughts)
    if mode == "analyse":
        break
```

**Indicateur visuel du mode** dans le splash : `model: gemma4:e4b · mode: chat`.

## 9. Pre-warm Ollama

Implémentation minimale :

```python
# llm.py — passer keep_alive à chaque appel
def chat(self, *, system, user, tools, temperature, thinking) -> LLMResponse:
    kwargs = {
        ...
        "options": {"temperature": temperature},
        "keep_alive": "30m",   # garde en RAM
    }
    ...

# cli.py — pre-warm
def _prewarm(llm, model: str, console: Console) -> None:
    console.print(f"[dim]warming up {model}…[/]", end="")
    try:
        llm.chat(system="You are a warmup probe.", user="ok",
                 tools=[], temperature=0.0, thinking=False)
        console.print(" [dim]ready.[/]")
    except Exception as e:
        console.print(f" [yellow]warmup failed: {e}[/]")
```

L'appel chat consomme un peu plus que `/api/show` mais charge réellement les poids comme un vrai run, donc le premier vrai tour est froid-zéro. `keep_alive=30m` couvre largement les réflexions humaines entre deux tours.

## 10. Impact sur l'outil web d'édition de paradigmes

Vu que tu as un éditeur web :

- Nouvelle UI : pour chaque paradigme, **3 cases à cocher** (analyse / chat / vocal) + un raccourci "tous modes" (= 0 case cochée, persisté comme aucune ligne dans `paradigm_modes`).
- À l'enregistrement : `DELETE FROM paradigm_modes WHERE paradigm_id = ?` puis `INSERT` des modes cochés. Atomique, simple.
- Filtre/recherche : pouvoir filtrer la liste des paradigmes par mode pour vérifier rapidement quel jeu est actif dans tel ou tel mode.
- Affichage : dans la liste, un petit badge `analyse|chat|vocal` ou `all` (= aucune ligne).

L'éditeur d'agents : pas d'impact direct. Le mode n'est pas une propriété d'agent.

## 11. Récap des changements de fichiers

| Fichier | Changement |
|---|---|
| `db/schema.sql` | nouvelles tables/colonnes, agent archivist, paradigmes mode, bindings |
| `src/jeanmichel/db.py` | signature `load_paradigms_for_agent(conn, agent_id, mode)`, `create_conversation` accepte `mode` |
| `src/jeanmichel/config.py` | constante `MODES = ('analyse','chat','vocal')` |
| `src/jeanmichel/models.py` | éventuellement `mode` sur la `Conversation` dataclass |
| `src/jeanmichel/orchestrator.py` | mode persistant, `turn_index`, `_prefix_summary`, `_run_archivist`, nouvel event `SummaryUpdated` + `TurnStarted` |
| `src/jeanmichel/prompts.py` | `mode` et `turn_index` dans le bloc `## Conversation` |
| `src/jeanmichel/llm.py` | `keep_alive` sur les appels |
| `src/jeanmichel/cli.py` | `--mode`, boucle adaptée, pre-warm, badge mode au splash |
| `tests/smoke.py` | scénario chat avec 2 tours + vérif summary.md |

Aucun nouveau module Python. Pas de tool nouveau.

## 12. Ordre d'implémentation suggéré

1. **DB** — schéma, agent archivist, paradigmes seed. Vérifier qu'un install à blanc charge tout.
2. **db.py** — filtre par mode. Tests directs en SQL.
3. **Orchestrator + prompts** — propagation du mode, mode `analyse` doit continuer à marcher exactement comme avant (régression zéro).
4. **CLI** — `--mode` et boucle.
5. **Continuité de conversation** — `turn_index`, summary lu/préfixé.
6. **Archivist** — appel post-loop, écriture du fichier.
7. **Pre-warm + keep_alive**.
8. **Éditeur web** — case multi-mode (en parallèle, indépendant).

Chaque étape est testable et déployable seule. Les 4 premières ne touchent pas le comportement utilisateur en mode `analyse` (sécurité).

## Points résiduels à valider

- **Volume du summary** : pas de cap dur dans le plan. Si la conversation s'éternise, le summary peut grossir. Mon avis : un paradigme `archivist_max_length` qui demande "keep under 1500 words" suffit, ajustable plus tard. Pas de troncation automatique côté code.
- **Fallback si archivist plante** : si l'archivist échoue (LLM ne renvoie rien d'exploitable), on garde le summary précédent et on logue. La conversation continue. Pas de blocage utilisateur.
- **Affichage CLI du `SummaryUpdated`** : discret (`[dim]· summary updated[/]`) pour ne pas polluer la lecture.