# Changelog — recalibrage Jean-Michel

Cinq fichiers à mettre en place. Tout est rétrocompatible avec l'existant
(pas de signature publique cassée, pas de schéma renommé).

## Fichiers livrés

| Fichier | Action |
|---|---|
| `db/migrate_002_recalibrate_paradigms.sql` | À appliquer **une fois** sur `jeanmichel.db` (idempotent). |
| `src/jeanmichel/prompts.py` | **Remplace** la version actuelle. |
| `src/jeanmichel/orchestrator.py` | **Remplace** la version actuelle. |
| `src/jeanmichel/config.py` | **Remplace** la version actuelle (juste 2 lignes ajoutées). |

## Ce qui change — DB (migration 002)

- `brutal_truth` → non-global, lié à `jean-michel` uniquement (était diffusé partout, inutile pour archivist/summarizer/specialists qui ne s'adressent pas directement à l'humain).
- `depth_over_speed` → non-global, lié à `jean-michel`/`summarizer`/`synthesizer`/`comparator-specialist`, restreint aux modes `analyse`+`chat` (était diffusé partout, en conflit avec `concise_output` en vocal).
- `briefing_contract` → non-global, lié à `jean-michel`+`comparator-specialist` (les deux seuls qui émettent `delegate_to`).
- `one_question_at_a_time` → non-global, lié aux agents qui peuvent appeler `ask_human` (exclut archivist/synthesizer).
- Nouveau paradigme `parse_briefing_first` (catégorie `process/execution`), lié à `weather-specialist`/`wikipedia-specialist`/`comparator-specialist`. Remplace `audit_phase` qui était inadapté (parle de "call stacks", "code", "technical debt").
- `audit_phase` débrancé de `weather-specialist` et `wikipedia-specialist`.
- `concise_output` débrancé de `comparator-specialist` (conflit irréconciliable avec `structured_verdict`).

**Effet mesuré** sur la matrice (n paradigmes par agent × mode) :

| Agent | Avant (analyse) | Après (analyse) |
|---|---:|---:|
| jean-michel | 15 | 15 |
| summarizer | 13 | 11 |
| synthesizer | 13 | 10 |
| weather-specialist | 16 | 13 |
| wikipedia-specialist | 17 | 14 |
| comparator-specialist | 16 | 16 |
| archivist | 15 | 11 |

## Ce qui change — code

### `prompts.py`

- **Outils de contrôle filtrés par rôle** : un `finalizer` (synthesizer, archivist) ne voit plus que `return_to_user`. Plus de `delegate_to`/`ask_human` injectés inutilement.
- **`OUTPUT CONTRACT` adapté au rôle** : version courte pour les finalizers.
- **Description de `delegate_to` clarifiée** : annonce le format structuré du tool result.
- **`tools_payload_for_agent(agent_role, tool_grants, registry)`** : nouvelle signature, prend le rôle de l'agent en premier argument.
- **`archivist` exclu** de la liste des `## Available specialists` rendus dans le prompt — il ne doit pas être dispatchable.

### `orchestrator.py`

- **`_run_request` retourne `(answer, artifact_filename)`** au lieu d'une string seule. Plus de variable d'instance `_last_response_artifact` (qui était un piège pour le futur parallélisme).
- **`tool_response` du `delegate_to` est un JSON structuré** : `{"tool":"delegate_to","agent":..., "artifact":..., "answer":...}`. Le LLM lit la clé `artifact` directement, plus de parsing texte fragile `(artifact: FILENAME)`.
- **Tous les `tool_response` synthétiques** (`ask_human`, erreurs, rejet de récursion) sont aussi des JSON. Format homogène.
- **`MAX_STEPS_PER_REQUEST`** sorti dans `config.py` (était hardcodé à 8 dans le source).
- **`summary.md` enregistré comme artifact en BDD** quand l'archivist a fini.
- **Appel `tools_payload_for_agent`** mis à jour pour passer `agent.role`.

### `config.py`

- Ajout de `MAX_STEPS_PER_REQUEST = 8`.

## Procédure d'installation

```bash
# 1. Backup (au cas où)
cp jeanmichel.db jeanmichel.db.bak.$(date +%Y%m%d)

# 2. Appliquer la migration DB
sqlite3 jeanmichel.db < db/migrate_002_recalibrate_paradigms.sql

# 3. Remplacer les 3 fichiers Python
cp prompts.py src/jeanmichel/prompts.py
cp orchestrator.py src/jeanmichel/orchestrator.py
cp config.py src/jeanmichel/config.py

# 4. Vérifier la matrice via paradigm_matrix
./jm.sh --paradigm-matrix
```

## Validation rapide

Smoke test minimum à passer :
- `./jm.sh "quelle heure est-il ?"` → réponse directe (jean-michel, pas de délégation).
- `./jm.sh "résume ce texte: ..."` → délégation summarizer, retour OK.
- `./jm.sh "quelle est la météo à Paris ?"` → délégation weather-specialist, retour OK.
- `./jm.sh "compare les chats et les chiens"` → délégation comparator → wikipedia ×2, verdict structuré.
- `./jm.sh --mode chat "salut"` puis suite → archivist tourne, `summary.md` apparaît dans le dossier.

Si un de ces 5 cas régresse, la cause la plus probable est dans la nouvelle signature `tools_payload_for_agent` (3 args au lieu de 2). Vérifier qu'aucun appel résiduel n'utilise l'ancienne signature.

## Ce qui n'est PAS dans ce livrable

Conformément au plan d'audit, sont reportés :
- Délégation parallèle réelle (refonte async, 1-2 jours).
- Sous-dossiers par tour dans le dossier de conversation.
- Tests de régression automatisés.
- Migration vers fichiers d'artefacts pour la table `requests` (sujet abordé séparément ci-après dans le rapport).
