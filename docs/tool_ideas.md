# Idées d'outils — candidats

> Tri (2026-06-17) des rapports `DevNotes/the_toolbox/` (depuis supprimés ; en historique git). On a déjà ~40
> outils natifs (web_search/web_fetch, wikipedia, news, github, pypi, stackoverflow, weather, image_search/fetch,
> analyze_image, workspace_*, repo_*, bash_sandbox, todo_write/update, plan_write, manage_memory, self_inspect_*,
> conv_history_scan, delegate_to, report_back, clock). La quasi-totalité des « sources » listées (APIs news /
> scientifiques / gouvernementales / Wikidata / GitHub / PyPI / SO) est **déjà couverte** par `web_search` +
> `web_fetch` ou un outil dédié. Ne restent que les **vrais manques** ci-dessous.

## Candidats pertinents

| Candidat | Usage | Source / techno | Pourquoi utile chez nous |
|---|---|---|---|
| **Outils géo** (`geo_lookup`, `geo_features`, `geo_stats`) | geocoding (adresse↔coord), POI dans une bbox, données démographiques | OpenStreetMap **Nominatim** / **Overpass** ; Geoapify ; US Census / World Bank (clients **HTTP**, pas de lib GIS lourde type GDAL) | **Aucun outil géo aujourd'hui** (vérifié : rien dans `src/jeanmichel/tools/`). `web_search` répond au factuel, pas au géospatial précis (coordonnées, POI, cartes). |
| **Extraction de contenu** (`extract_article`, `extract_table`) | HTML/PDF brut → texte propre + métadonnées ; tables → JSON/CSV | `trafilatura` / `readability-lxml` ; `pandas.read_html` (+ `pdfplumber` pour PDF) | Dégrossit `web_fetch` (qui rend du HTML brut) → moins de tokens gaspillés en boilerplate, meilleure extraction sur sites complexes / papiers académiques. |

## Déjà couvert / écarté (pour mémoire)
- **APIs news / scientifiques / gov / Wikidata / GitHub / PyPI / StackOverflow** → déjà via outils dédiés ou `web_search`/`web_fetch`.
- **OpenWebUI** (UI), **Cognee** / **Neo4j** (knowledge graph / graph DB), **skill brainstorming** → hors scope du harness.
- **Todo enrichi** (le `todo_tool_spec` de the_toolbox : statuts/dépendances/scoping par-requête) → **ne pas dupliquer** : on a déjà `todo_write`/`todo_update`/`plan_write` ; l'enrichissement (todo par-id + suivi subagent) est déjà tracé dans [../todo.md](../todo.md) (« Todo multiple par-id »).
