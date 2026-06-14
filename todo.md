# To Do

## En cours

- **Rôle code-router / code-analyst** : remplacer qwen3:14b par mieux (tient sur 1 GPU 32 Go).
  Candidats + plan d'éval + point de vigilance câblage → [docs/20260614_model_selection.md](docs/20260614_model_selection.md).
- VÉRIFIER EN LIVE le « plan mode » (livré, doc `docs/20260613_plan_mode/`) : sélecteur Plan/Edit → tour plan
  read-only (todo_write forcé) → barre Approuver/Modifier → éditeur inline → exécution. Cf. étape 5 plus bas.

## À faire

- **Tool set / MCP par agent** : jean-michel ne doit PAS avoir les outils github ni le MCP vuetify (réservés aux
  agents codeurs) — et le vuetify n'est même pas lancé → question : quel MCP pour quel agent ? Suspicion qu'il
  confond *outil* et *délégation* (cf. deny `web-search-specialist` traité comme un outil non accordé).
- On est définitivement en v2 ? Checker si la v1 sert encore ; sinon dégager v1 + docs et consolider
  (orchestrateur, tests…).
- Rafraîchir le paradigm viewer/editor.
- PLAN MODE — étape 5 (APRÈS rodage live) : analyse écrite de généralisation aux autres modes (analyse/recherche :
  plan de recherche validé ? chat : marginal ? vocal : hors-sujet) + patterns d'orchestration transverses
  (vagues/dépendances/complexity-routing). Décider sur données réelles, pas spéculativement.
- Ajouter un moyen de **kill une opération LLM en cours** (si on voit qu'il part en vrille).

## Bugs

- Les agents hallucinent sur des fichiers absents du workspace, probablement lié à une compaction
  (`conversations/2026-06-13_19-20_dfcafc75c589430f86fd9c2a82cf70ae`).
- Si une conversation tourne dans un onglet et qu'on en consulte une autre, au retour on ne voit plus qu'elle
  réfléchit ni les chaînes de pensée (état de streaming perdu au switch d'onglet).
- À revérifier en live (conv `2026-06-14_03-58_56e288f0…`, possiblement adressé par commits récents
  models.toml/todo/plan) : artefacts workspace écrits ? plans en mode analyse qui répondent ? appels d'outils
  non bloqués ? plan non vide ? mémoires visibles ?

## À vérifier

- Bench du budget de tokens allouable (semblait tout petit ; chaque LLM repart fresh, fenêtres ~40-128k, serveur
  2×32 Go). Ex. `compaction (124 %)` sur `conversations/2026-06-13_19-20_dfcafc75…`. NB : lié au travail récent
  `num_ctx` par modèle + plafond (commits 2bcffc8/a00122b).
- Lister/analyser les paradigmes de tous les agents (incohérences ?) ; le meta_analyst sert-il encore ?

## Idées

- Plein de micro-LLM qui prédisent le prochain token sur le même contexte → triplets de précogs.
