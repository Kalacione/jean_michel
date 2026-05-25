## Comparaison qualitative

Tu as deux variantes en local : `gemma4:26b` (17GB) et `gemma4:latest` (9.6GB). En croisant les caractéristiques des deux sorties :

### Conv 1 — `2026-05-25_00-19_397d0826ad98` (très probablement `gemma4:26b`)

- **Spécialistes appelés** : web-search-specialist (8 itérations de recherche) + document-builder (3 steps) + archivist
- **Sortie** : tableau de **11 candidats nommés et concrets** : Wikidata, OpenStreetMap, arXiv, PubMed, NASA, GDELT, GitHub, Stack Exchange, PyPI, Copernicus, USGS
- **Couverture** : 8 domaines distincts (encyclopédique, géo, scientifique, news, code, technique Q&A, écosystème langage, observation terrestre)
- **Réponse utilisateur** : 2 phrases sèches, pointe le fichier
- **Qualité** : ciblée, actionnable — chaque ligne est une source réelle interrogeable

### Conv 2 — `2026-05-25_00-33_ec4068d2b514` (très probablement `gemma4:latest` / le plus petit)

- **Spécialistes appelés** : wikipedia-specialist (2 steps) + web-search-specialist (3 steps) + document-builder (3 steps) — plus de diversité d'outils
- **Sortie** : tableau de **5 candidats, dont 4 sont des catégories** : "Government Open Data APIs", "Scientific Databases", "Open Knowledge Graphs", "Specialized APIs". Seul Wikidata est nommé concrètement.
- **Couverture** : 5 buckets génériques
- **Réponse utilisateur** : ré-inline tout le tableau en français + commentaire — plus verbeux
- **Qualité** : structure correcte mais **abstraction excessive** — "Scientific Databases" n'est pas une source, c'est une famille. L'utilisateur devra refaire le travail d'identification.

### Verdict

| Critère | Conv 1 (26b) | Conv 2 (latest) |
|---|---|---|
| Spécificité des entrées | ✅ entités nommées | ❌ catégories |
| Couverture | 11 sources / 8 domaines | 5 buckets |
| Effort de recherche | 8 web searches | 5 calls mixtes |
| Verbosité finale | minimale | redondante |
| Utilisabilité directe | élevée | faible |

**Conv 1 est nettement plus utile.** Le petit modèle compense la moindre profondeur par plus de structure et de verbosité dans la réponse, mais le contenu est plus pauvre. Intéressant à noter : le petit modèle a *choisi* d'utiliser Wikipedia + web (bonne décision méthodologique) là où le gros est resté sur web search en boucle. C'est un signal qu'**il y a peut-être un paradigme à durcir côté `web-search-specialist` du 26b** pour qu'il pivote sur Wikipedia plus tôt — il a la capacité, il n'a juste pas l'instinct.