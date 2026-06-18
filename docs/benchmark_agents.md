> **Bench live des modèles** (recherche, chiffré) — base des verdicts de [models_eval.md](models_eval.md). Relocalisé depuis `DevNotes/benchmark_agents.md`.

# run de bench

```
Tu es Jean-Michel, un assistant IA local Python 3.14 tournant sur Ollama (modèle `gemma4:26b`). Tu es piloté par un orchestrateurqui te délègue des tâches via des agents spécialisés (summarizer, wikipedia-specialist, workspace-manager, etc.). Les agents peuvent utiliser des outils (recherche web, lecture fichiers, sandbox Docker…).
 Ton utilisateur développe ce système et te demande d'**explorer des sources d'information fiables et structurées** qui pourraientservir de "source of truth" dans les réponses que tu produis — au même titre que Wikipedia. Il cherche des sources interrogeables programmatiquement (API, dumps, RSS…), couvrant idéalement plusieurs domaines (encyclopédique, scientifique, actualité, technique, géographique…).
 Recherche sur internet et wikipedia des sources de confiances qu'on pourrait référencer en tant qu'agent spécialiste ou simple outil pyhton.La sortie attendue est un fichier markdown dans le workspace de notre conversation; Le fcichier contiendra un tableau qui liste les candidats potentiels, leurs valeur ajoutée ainsi que le domaine de connaissance.
 ```


 # Résultats

 voir `DevNotes/comparo_gemma_latest_26b.md`

 ## gemma4:26b - la référence

 ai.google.dev/gemma

 Lent mais produit un résultat de qualité

 ## gemma4:latest

 Plus rapide, un seul GPU chargé, mais résultats moins qualitatits et remplissage verbal

 ## qwen3.6:27b

 Trop lent, heurte la limite des 120 secondes par requetes pour la réflexion

 ## deepseek-r1:14b

 Ne peut pas utiliser les outils

## qwen3:14b 

Etonnant, equivalent au gros gemma et 2 fois plus rapide, mais hallucine un peu




--- 


### Classement quantifié

| | gemma4:26b | qwen3:14b | gemma4:latest |
|---|---|---|---|
| Sources utilisables | 11/11 | ~22/26 | **2/5** |
| Hallucinations / non-sources | 0 | 3-4 | 3 |
| Densité d'info par ligne | haute | moyenne | nulle |
| Vitesse | lente | rapide | rapide |
| Couverture | étroite | large | minuscule |

### Ce que ça dit du bench

Le pattern qui se dégage :

1. **gemma4:latest est éliminé**. Il est rapide mais produit du contenu qui force l'humain à refaire le travail. Aucun gain réel.
2. **qwen3:14b ≈ gemma4:26b en qualité utile**, mais qwen3 est **rapide**. À couverture équivalente (post-filtrage des hallucinations), qwen3 est **Pareto-dominant** sur le couple (qualité, latence).
3. **gemma4:26b garde un edge sur la finesse rédactionnelle** (justifications denses, 0 bullshit) mais à coût latence/contexte qui ne se justifie peut-être pas pour un usage research-and-list.

### Implication pour le projet

Ça commence à faire un argument sérieux pour **switcher la baseline sur qwen3:14b** pour les research specialists, et garder gemma4:26b uniquement pour les rôles à forte exigence éditoriale (synthesizer, archivist, critical-thinker).

Ça rejoint d'ailleurs naturellement ta spec Ralph : un specialist qui fait peu par itération mais beaucoup d'itérations → on veut **un modèle rapide et compétent**, pas un modèle lent et brillant. qwen3:14b coche les deux cases.

Tu veux que je note ces observations dans comparo_gemma_latest_26b.md (en l'étendant à qwen3), ou tu préfères continuer à benchmarker à la main et on consolide à la fin ?

