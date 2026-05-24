# Deep Introspection — Plan de mise en œuvre

Documents de remédiation issus de l'audit [audit_2026-05-24_04-08_ca049ab8be2a.md](../audit_2026-05-24_04-08_ca049ab8be2a.md).

Chaque document est **auto-suffisant**, destiné à un agent de mise en œuvre (Claude 4.6) qui traitera les points dans l'ordre. Convention :

- Toute modification de paradigme doit être appliquée **à la BDD live** (`jeanmichel.db`) **ET** répercutée dans [db/schema.sql](../../db/schema.sql).
- Pour cela : créer une migration `db/migrate_NNN_<slug>.sql` (avec le numéro suivant disponible — actuellement 043), l'appliquer via `sqlite3 jeanmichel.db < db/migrate_NNN_*.sql`, puis ajouter le bloc équivalent à la fin de `db/schema.sql`.
- Backup automatique avant toute migration : `./jm.sh --export-db`.

## Décisions d'arbitrage (verrouillées avant rédaction)

| Réf | Décision |
|-----|---------|
| Q1 — Control verbs | **Option B nommée** : `planner_done`, `gather_done`, `critic_done`, `build_done` |
| Q2 — Depth ≥ 2 | **Hybride** : jean-michel orchestre par défaut ; `critic` peut creuser ; web/wiki peuvent lancer des sous-recherches (page d'homonymie, lien à suivre) |
| Q3 — Planner | **Tool mécanique** : agent `planner` **supprimé**, remplacé par outil `plan_update` |
| Q4 — Loop detection | **Strict + normalisation fingerprint** (lowercase, strip, sort args) |
| Q5 — Wall-clock | `LLM_CALL_TIMEOUT_SECONDS=120`, `REQUEST_WALL_CLOCK_SECONDS=900`, `TURN_WALL_CLOCK_SECONDS=1800` |
| Q6 — Path norm | **Strip + warning loggué** |
| Q7 — Output corrompu | **Retry (1×) puis escalate** |
| Q8 — Ordre | Doc 01 → 10 (criticité × dépendance) |

## Ordre d'exécution

1. [01_wall_clock_timeouts.md](01_wall_clock_timeouts.md) — fondation : sans ça, tout peut hanguer
2. [02_workspace_path_normalization.md](02_workspace_path_normalization.md) — bug nested, isolé
3. [03_loop_detection.md](03_loop_detection.md) — boucles intra-specialist
4. [04_corrupted_output_validation.md](04_corrupted_output_validation.md) — résilience LLM
5. [05_remove_planner_agent.md](05_remove_planner_agent.md) — décision structurante (DB + paradigmes)
6. [06_plan_update_tool.md](06_plan_update_tool.md) — remplaçant mécanique du planner
7. [07_research_pipeline_enforcement.md](07_research_pipeline_enforcement.md) — GATHER→CRITIC→BUILD imposé
8. [08_depth_promotion.md](08_depth_promotion.md) — empowerment cible pour depth ≥ 2
9. [09_grant_briefing_validation.md](09_grant_briefing_validation.md) — orchestrateur vérifie cohérence
10. [10_filesystem_failfast.md](10_filesystem_failfast.md) — remontée propre des erreurs FS

## Conventions communes

- **Code** : Python 3.14, suivre les patterns existants (dataclass events, ToolSpec, `db.connect()`).
- **Tests** : `MockClient(script=[...])` + `os.environ["JEANMICHEL_HOME"]=tmpdir`. Voir `.github/skills/testing/SKILL.md`.
- **Pas de docstrings/commentaires** sur du code non touché.
- **Pas de feature flags** : les changements sont des évolutions de comportement, pas des options.
- Numéro de migration suivant à utiliser : **044** (incrémenter pour chaque doc qui touche la DB).
