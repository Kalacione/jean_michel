# 10 — Découplage du rendu d'événements (suivi)

> **Statut** : à faire, **pas urgent**. Document de suivi pour quand on s'attaquera
> à l'amélioration de l'UX terminal + la préparation d'un futur frontal web.
> Daté du 2026-05-28.

## Contexte

Phase 7/8 ont livré un CLI v2 fonctionnel mais minimaliste côté affichage. Le
besoin remonté par l'utilisateur :

> En v1, on avait un affichage dans le CLI qui permettait de suivre ce qu'il se
> passait en interne (délégation, …), et de petits séparateurs plus agréables à
> lire. On voudrait que ce soit exposé comme un événement, pour le jour où on
> voudra faire un frontal web.

Trois modes d'usage envisagés :

- **CLI direct utilisateur** — joli, dynamique, séparateurs, icônes, couleurs.
- **CLI piped depuis une autre appli** — texte brut, synthétique, sans ANSI.
- **CLI exposé à un frontal web** — stream d'événements consommable.

## Ce qu'on a déjà (à garder tel quel)

La fondation est en place depuis la Phase 1 :

- [events.py](../../src/jeanmichel/events.py) — 11 dataclasses immutables,
  sérialisables JSON via `event_to_jsonl_line` :
  `RequestStarted`, `LLMCallStarted/Completed`, `ToolCallStarted/Completed`,
  `DelegationStarted/Completed`, `HookFired`, `WorkingBudgetUpdate`,
  `MemoryNearCapacity`, `RequestCompleted`.
- Orchestrateur émet via callback `event_emitter` (cf.
  [orchestrator_v2.py](../../src/jeanmichel/orchestrator_v2.py)).
- Persistance JSONL append-only par conv-folder (`events.jsonl`,
  `fcntl.flock` pour la concurrence — cf.
  [persistence.py](../../src/jeanmichel/persistence.py) `append_event`).

C'est **déjà** le contrat dont un frontal web aurait besoin. Pas de rework
côté émission. Le travail est exclusivement côté **rendu**.

## Ce qui pèche aujourd'hui

Un seul renderer, codé en dur dans [cli.py:106](../../src/jeanmichel/cli.py#L106)
(fonction `render_event`). Limites concrètes :

1. **`LLMCallStarted` / `LLMCallCompleted` sont muets** — l'utilisateur ne sait
   pas combien le LLM consomme. Le spinner "thinking…" comble partiellement
   mais ne montre rien sur les tokens.
2. **Pas de séparateur entre phases** — dispatch / deep / délégation
   s'enchaînent sans rupture visuelle.
3. **Pas d'indentation hiérarchique au-delà de la racine** — `MAX_DEPTH=5`
   permet des délégations imbriquées profondes mais elles sont indistinguables
   à l'œil (juste `depth=N` en dim).
4. **Aucun mode non-TTY** — un `jean-michel --once "..." | grep ...` crache
   des codes ANSI, illisible en sortie pipée.
5. **Pas d'override mode rendu** — pas de `--output {rich,plain,jsonl}`.

## Reco — 3 axes, **n'attaquer que 1 + 2** pour l'instant

### Axe 1 : extraire le renderer dans `src/jeanmichel/renderers/`

Protocole minimal, **pas de framework** :

```python
# renderers/_base.py
from typing import Protocol

class EventRenderer(Protocol):
    def handle(self, event: Any) -> None: ...
    def close(self) -> None: ...  # final panel, summary, etc.
```

Le CLI choisit l'impl. en fonction de `--output` + `sys.stdout.isatty()`.
**K.I.S.S.** : pas de registry, pas de plugins, juste 2 fichiers concrets.

### Axe 2 : deux implémentations concrètes

#### `renderers/rich_renderer.py` — mode interactif (défaut TTY)

- `Rule("turn N", style="dim")` au début de chaque turn humain.
- `Rule("─ tier 0 alexa ─" / "─ tier 1 deep ─")` selon le routage.
- `RequestStarted` → header agent avec indentation par `depth` (deux espaces
  par niveau, icône ↳ pour les enfants).
- `LLMCallCompleted` → ligne discrète `· LLM · 1.2k tokens · 2 tool calls`
  (dé-muté, mais en couleur dim pour ne pas saturer).
- `ToolCallStarted/Completed` → groupé visuellement (icône 🔧 en démarrage,
  ↳ en fin avec résultat tronqué).
- `DelegationStarted` → header de sous-agent avec **règle horizontale indentée**
  (séparateur de phase).
- `DelegationCompleted` → footer du sous-agent (icône ✓, confidence colorée).
- `HookFired` (action != "ok") → ligne ⚠ rouge.
- `WorkingBudgetUpdate` → ligne `⏱ compaction · {level}` (seuils 70/80/90/95 %).
- `RequestCompleted` → `Rule("answer from {agent}")`.
- Panel Markdown final pour la réponse user-facing (déjà fait dans
  `run_one_turn`, on le déplace dans `close()`).

#### `renderers/plain_renderer.py` — mode pipe / non-TTY

- Pas d'ANSI, pas de Rule Rich, juste des lignes texte.
- Format synthétique compatible `grep` / `awk` :
  ```
  [turn-1] dispatch alexa tool=clock conf=high
  [turn-1] llm completed tokens=1234 tools=2
  [turn-1] tool web_search args="paris weather"
  [turn-1] tool web_search ok 845ms
  [turn-1] delegate to=specialist_X depth=1 budget=8192
  [turn-1] delegate.return child=specialist_X conf=high files=2
  [turn-1] answer:
  <texte complet de la réponse, indenté>
  ```
- Détection auto : si `sys.stdout.isatty()` est faux → bascule plain.
  Override : `--output {rich,plain}` (flag explicite).

### Axe 3 : (différé) `jsonl_renderer.py` — streaming machine-readable

- Dump chaque event en JSONL sur stdout (un event par ligne).
- Utile pour un futur frontal web qui pipe le CLI dans un consumer.
- **Mais** : `events.jsonl` sur disque est **déjà** consommable par tail —
  un futur backend web peut juste `tail -F` ce fichier ou exposer
  `GET /conv/{id}/events.jsonl`. **Pas besoin d'un mode `jsonl` dans le CLI**
  tant qu'on n'a pas un cas d'usage concret. C'est de l'archi spéculative.

Le jour où on s'y attaque vraiment, on aura probablement plus envie d'un
**petit serveur HTTP autonome** (`jm-server` ou équivalent) qui lit
`conv_folder/events.jsonl` et expose un endpoint SSE, plutôt que d'alourdir
le CLI avec un mode supplémentaire.

## Tradeoffs à arbitrer le jour J

- **Hauteur de terminal** : les Rule horizontales bouffent des lignes.
  Sur écran 30 lignes, ça devient agressif. → Mode `--compact` ?
  Ou laisser les Rule uniquement pour les phases majeures (turn, deep, final),
  pas pour chaque délégation. **À trancher en testant.**
- **Couleurs configurables** : pour l'instant on hard-code la palette dans
  cli.py (`C_USER`, `C_TOOL`, …). Si on extrait, on copie tel quel — on ne
  fait pas un thème system maintenant.
- **`--show-thoughts`** : aujourd'hui le flag existe mais l'orchestrateur v2
  ne dump pas encore le contenu `thinking` du LLM dans les événements.
  Si on veut le surfacer, il faudra ajouter `LLMCallCompleted.thinking_summary`
  ou un nouvel event `ThinkingEmitted`. Hors scope de ce doc.

## Ce qu'on **ne** fait **pas** ici

- Pas de framework de plugins de rendu.
- Pas d'abstraction `Channel` / `Sink` / `Pipeline` — on a 2 cas d'usage,
  on fait 2 fichiers.
- Pas de mode API HTTP dans le CLI maintenant.
- Pas de refactor de `events.py` (déjà bon).
- Pas de toucher à l'orchestrateur ni aux hooks (déjà bons aussi).

## Plan d'attaque le jour J (~2-3 h estimées)

1. Créer `src/jeanmichel/renderers/__init__.py` + `_base.py` (protocole).
2. Couper `cli.render_event` → `renderers/rich_renderer.py`, enrichir avec
   les Rule de phase + indentation par depth + `LLMCallCompleted` dé-muté.
3. Écrire `renderers/plain_renderer.py` (parallèle, sans ANSI).
4. Ajouter `--output {rich,plain}` à argparse + détection auto via `isatty`.
5. Adapter `run_one_turn` / `_run_deep_turn` pour passer le renderer choisi
   au lieu d'appeler `render_event` directement. Le `close()` du renderer
   gère le panneau final / la ligne `answer:`.
6. Tests : `tests/v2/test_renderers.py` — vérifie que les deux renderers
   absorbent les 11 types d'événements sans crash + golden outputs courts.
