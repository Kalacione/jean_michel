# run de bench

```
Tu es Jean-Michel, un assistant IA local Python 3.14 tournant sur Ollama (modèle `gemma4:26b`). Tu es piloté par un orchestrateurqui te délègue des tâches via des agents spécialisés (summarizer, wikipedia-specialist, workspace-manager, etc.). Les agents peuvent utiliser des outils (recherche web, lecture fichiers, sandbox Docker…).
 Ton utilisateur développe ce système et te demande d'**explorer des sources d'information fiables et structurées** qui pourraientservir de "source of truth" dans les réponses que tu produis — au même titre que Wikipedia. Il cherche des sources interrogeables programmatiquement (API, dumps, RSS…), couvrant idéalement plusieurs domaines (encyclopédique, scientifique, actualité, technique, géographique…).
 Recherche sur internet et wikipedia des sources de confiances qu'on pourrait référencer en tant qu'agent spécialiste ou simple outil pyhton.La sortie attendue est un fichier markdown dans le workspace de notre conversation; Le fcichier contiendra un tableau qui liste les candidats potentiels, leurs valeur ajoutée ainsi que le domaine de connaissance.
 ```


 # Résultats

 voir `DevNotes/comparo_gemma_latest_26b.md`

 ## gemma4:26b - la référence

 Lent mais produit un résultat de qualité

 ## gemma4:latest

 Plus rapide, un seul GPU chargé, mais résultats moins qualitatits et remplissage verbal

 ## qwen3.6:27b

 Trop lent, heurte la limite des 120 secondes par requetes pour la réflexion

 ## deepseek-r1:14b

 Ne peut pas utiliser les outils

