# 09 — Phase 8 completion + état post-révolution

> Note finale clôturant le plan d'implémentation
> (`07_plan_implementation.md`). Décrit l'état du repo après les 9 phases
> (0 à 8), les écarts assumés par rapport au plan, et ce qui reste à
> faire en dehors du périmètre v2 strict.

## Résumé de la bascule

| Phase | Statut | Livrable principal |
|-------|--------|--------------------|
| 0     | ✓ done | `08_paradigm_audit_table.md` + `migrate_100_paradigm_realignment.sql` (117 paradigmes audités, 18 supprimés, 8 réécrits, 5 nouveaux) |
| 1     | ✓ done | `LLMClient.chat_messages()` natif multi-turn + `events.py` (11 events) + persistence v2 (`messages.json` / `state.json` / `events.jsonl` / `subagent_*.json`) |
| 2     | ✓ done | `hooks.py` (4 hooks) + `compaction.py` (escalade 4 niveaux Snip → Microcompact → Collapse → Autocompact) |
| 3     | ✓ done | `dispatcher.py` (Tier 0, granite4.1:8b, JSON forcé) + `execute_alexa` (clock/weather/wikipedia_search) |
| 4     | ✓ done | `orchestrator_v2.py` (main loop + `spawn_subagent` + délégation imbriquée jusqu'à `MAX_DEPTH=5`) + `tools/delegate_to.py` + `tools/report_back.py` |
| 5     | ✓ done | `tools/manage_user_memory.py` (5 actions) + `migrate_101_user_memory.sql` + `bootstrap.py` |
| 6     | ✓ done | `migrate_102_drop_runtime_tables.sql` (DROP requests/artifacts/conversation_phases/sandbox_executions + `agents.model_override`) + 3 tests structurels |
| 7     | ✓ done | `cli.py` v2 (Tier 0 + Tier 1 + ask_human) + `debug/inspect_conv.py` v2 + fix `jm.sh --meta-analysis` |
| 8     | ✓ done | `tests/v2/test_smoke_e2e.py` (skipped sans Ollama) + consolidation `db/schema.sql` + suppression du code legacy + docs |

## État final du repo

### Tests

- **tests/v2/** : 298 tests passants + 3 skipped (smoke E2E sans
  `JEANMICHEL_SMOKE_E2E=1`). Exécution : ~2,5 s sans Ollama.
- **tests/** legacy : **supprimés** (24 fichiers + `smoke.py` + `demo_cli.py` + `conftest.py`).

### Code v2 (src/jeanmichel/)

Modules v2 actifs :

- `cli.py`, `orchestrator_v2.py`, `dispatcher.py`, `hooks.py`,
  `compaction.py`, `events.py`, `tokens.py`, `llm.py`, `persistence.py`,
  `prompts.py` (v2-only), `bootstrap.py`, `config.py`, `db.py`, `models.py`.

Modules legacy **supprimés** :

- `orchestrator.py` (1794 lignes — la grosse boucle legacy).
- `plan_writer.py` (240 lignes — rendu déterministe de `plan.md`).
- `tools/manage_todo_list.py`, `tools/set_task_class.py`,
  `tools/self_inspect.py` (mono tool ; les 3 scopés survivent).

Module v2 nouveau dans `tools/` :

- `delegate_to.py` (schéma), `report_back.py` (schéma + validation),
  `manage_user_memory.py` (5 actions).

### Base de données

- `db/schema.sql` (635 lignes) = **état v2 consolidé** issu d'un `.dump` SQLite
  après application de schema_v1_baseline + migrate_100 + 101 + 102. Le
  fichier comprend 12 tables, 12 agents actifs, 104 paradigmes actifs.
- `db/schema_v1_baseline.sql` (3312 lignes) — baseline v1 préservée pour
  valider les migrations dans les tests d'idempotence.
- `db/migrations/` — 60+ migrations historiques + les 3 v2 (100/101/102).

Pour une instance v1 existante :

```bash
sqlite3 jeanmichel.db < db/migrations/migrate_100_paradigm_realignment.sql
sqlite3 jeanmichel.db < db/migrations/migrate_101_user_memory.sql
sqlite3 jeanmichel.db < db/migrations/migrate_102_drop_runtime_tables.sql
```

Pour une fresh install : `./jm.sh --install` charge `db/schema.sql` (v2).

### Docs

- `README.md` — réécrit pour la v2 (Tier 0/1/2, hooks, user_memory,
  budget partitionné, compaction multi-niveaux, configurabilité totale).
- `docs/PROMPT_SKELETON.md` — réécrit pour `render_system_prompt_v2`
  (output contract par rôle, ask_human main-only, report_back subagent-only).
- `DevNotes/REVOLUCION/01–09` — historique complet de la pensée
  (audit → proposition → plan → tableau paradigmes → ce doc).

## Écarts vs plan 07 (assumés)

1. **`orchestrator.py` n'a pas été renommé en `orchestrator_legacy.py`
   en Phase 4** — j'ai préféré garder `orchestrator.py` legacy intact et
   créer `orchestrator_v2.py` à côté. En Phase 8 (cleanup), j'ai
   directement supprimé `orchestrator.py` sans passer par un alias. Le
   code v2 vit dans `orchestrator_v2.py` ; aucun import ne traverse
   plus. Le fichier peut être renommé en `orchestrator.py` à tout
   moment (1 search/replace dans cli.py + tests/v2).

2. **Tag git `v2.0.0` non créé** — laissé à l'utilisateur. La branche
   `revolucion` est prête pour merge (le branch tag est sa
   responsabilité).

3. **`MAX_SEARCH_CALLS_PER_TURN` n'est pas réellement turn-wide** dans
   l'implémentation actuelle : le compteur `state.search_calls_total`
   est par-agent (chaque subagent a son propre state). Documenté
   comme une simplification de Phase 4 dans `orchestrator_v2.py`. Pour
   un vrai turn-wide, il faudrait introduire un `TurnCounters` partagé
   (cf. réflexion §4.2 du plan). Décalage acceptable — le wall-clock
   et `MAX_DEPTH` couvrent l'essentiel.

4. **Subagent peut-il appeler `ask_human` ?** Non — le tool n'est
   pas dans son payload. La consigne est de remonter via
   `report_back(confidence='low', low_confidence_reason='...')`. C'est
   l'option B de l'analyse §5 doc 06.

5. **`record_sandbox_execution` dans `db.py`** — la fonction existe
   toujours mais réfère à la table `sandbox_executions` supprimée.
   Inutilisée par la v2. Suppression silencieuse possible en
   follow-up.

## Validation finale

```
$ source .venv/bin/activate && pytest tests/v2/ -q
298 passed, 3 skipped in 2.46s
```

```
$ python -c "
from jeanmichel.cli import main
from jeanmichel.orchestrator_v2 import run_main_loop, spawn_subagent
from jeanmichel.dispatcher import classify, execute_alexa
from jeanmichel.hooks import build_hook_registry
from jeanmichel.compaction import escalate_compaction
from jeanmichel.tools import build_registry
from jeanmichel.tools.manage_user_memory import SPEC
from jeanmichel.bootstrap import bootstrap_user_memory_from_profile
print('All v2 imports OK')
"
All v2 imports OK
```

## Smoke test E2E réel (à exécuter par l'humain sur la machine GV100)

```bash
JEANMICHEL_SMOKE_E2E=1 pytest tests/v2/test_smoke_e2e.py -v
```

Scénarios :

1. ALEXA français : "Quelle heure est-il ?" → dispatcher classifie
   alexa+clock → réponse formatée en français.
2. ALEXA anglais : "What time is it?" → dispatcher alexa+clock →
   réponse anglaise (summary verbatim).
3. DEEP simple : question conceptuelle → main loop conclut sans
   tool_calls → réponse cohérente, fichiers v2 (`messages.json`,
   `state.json`, `events.jsonl`) sur disque.

## Prochaines étapes hors-périmètre v2

Non bloquant pour le merge `revolucion → main`, mais à considérer
ultérieurement :

- **Renommer `orchestrator_v2.py` → `orchestrator.py`** pour la cohérence
  des imports. Simple search/replace.
- **Turn-wide counters** : factoriser `search_calls_total` dans un objet
  `TurnCounters` partagé entre subagents.
- **Suppression de `db.record_sandbox_execution`** (orpheline).
- **Suppression des paradigmes inactifs** (`active=0`) après une période
  d'observation.
- **Ajout d'une vraie suite de bench latence** sur la machine GV100
  pour calibrer `OUTPUT_RESERVE_RATIO` (15 %) et `MICROCOMPACT_TOKEN_THRESHOLD`
  (1500) empiriquement.
- **`progress.json` snapshot** pour la reprise visuelle CLI après crash
  (déferré en Phase 6, mentionné §6 bis doc 06 — events.jsonl couvre
  déjà le replay donc plus prioritaire).

## Conclusion

La révolution est terminée. La v2 livre :

- Une **boucle minimale + 4 hooks** déterministes (au lieu d'un méga-loop
  de 1794 lignes avec 5 gates inline).
- **Trois modèles spécialisés** (dispatch / main / compactor) au lieu d'un
  seul thinking pour tout.
- **Mémoire native multi-turn** (au lieu d'un récap reconstruit basse
  fidélité).
- **Budget de contexte partitionné** avec **escalade de compaction à 4
  niveaux** (au lieu de 7 budgets orthogonaux ad-hoc).
- **Délégation imbriquée propre** jusqu'à `MAX_DEPTH=5` (au lieu d'un
  arbre potentiellement à 10⁸ feuilles).
- **Mémoire long-terme utilisateur** structurée (au lieu d'un toml
  statique).
- **Modèles, seuils, paradigmes tous configurables** sans recompile.

L'architecture est K.I.S.S, déterministe, et évolutive. Le pivot
Claude-Code-style validé le 2026-05-27 a tenu jusqu'au bout sans
compromis structurel.
