# To Do


## NULACHIER: `conversations/2026-06-14_03-58_56e288f0599f4915bf6346171ad9f84a`

- ca ecrit plus d'artefacts dans le workspace
- ca fait des plans en mode analyse maos ca repond pas grand chose; il a fallur une reponse pour que le trigger d'acceptaion de pla apparaisse sur la GUI.
- les appels d'outils sont bloques
- le plan est vide, mais on peut le modifier

## c'est nuuuuul

- si une conversation est en court dans un onglet et qu'on check une autre convesation, quand on revient on voit pas que c'est en train de reflechir et on voit plus les chaines de pensees

## Un ouf malade

- plein de micro llm qui taffent sur le meme contexte a predire le prochain token et on en fait des triplets de precogs

## en cours

- VÉRIFIER EN LIVE le "plan mode" (livré, branche `ca_plan_pour_moi`, doc `docs/20260613_plan_mode/`) : sélecteur Plan/Edit (gauche du Envoyer, défaut Plan en code/analyse) → tour plan read-only (aucune mutation, todo_write forcé) → barre Approuver/Modifier → éditeur inline → exécution. Cf. étape 5 ci-dessous une fois rodé.

## Bugs

- les agents hallucinent sur des fichiers qui ne sont pas dans le workspace, probablement lie a une operation de compaction `conversations/2026-06-13_19-20_dfcafc75c589430f86fd9c2a82cf70ae`

## a faire

- on est definitivement en v2?? Checker si la v1 sert encore; sinon degager la v1 et les docs et consolider (orchestrateur, tests, ...)
- rafraichir le paradigm viewer/editor
- PLAN MODE — étape 5 (APRÈS rodage live côté code) : analyse écrite de généralisation aux autres modes (analyse/recherche : plan de recherche validé ? chat : marginal ? vocal : hors-sujet) + patterns d'orchestration transverses (vagues/dépendances/complexity-routing, cf. doc d'audit). Décider l'élargissement sur données réelles, pas spéculativement.
- ajouter un moyen de kill une operation en cours par un LLM (si on voit qu'il fait de la merde)



## a verifier

- plein de `PreToolUse: deny` 

- faire un bench sur le budget de token allouable, il me semble tout petit sachant que chaque LLM est independant avec un fresh start et que les LLM qu'on utilise on 100k tokens. De plus notre archi du serveur de dev (2x 32Go de VRAM) devrait nous mettre a l'aise. ex du `compaction (124 %)` de `conversations/2026-06-13_19-20_dfcafc75c589430f86fd9c2a82cf70ae`

- lister et analyser tous les paradigmes de tous les agents pour voir si c'est pas deconnanant; checker ce que dit le meta_analyst et si il sert encore a quelque chose
- pendant que ca tourne, le `graphify.sh` il sert encore ? si c'est juste un outil, on devrait le mettre dans un dossier `tools`; si c'est une base qui sert pour le jou ou on voudra le mettre dans un dockerfile, faut lui tourver une autre place. de memoire graphify c'etait pas ouf et on s'en servait pas
- le franglish : ```Looking back, the system prompt says: "Reply in the user's detected language." So the final answer should be in French. However, the report_back function's parameters are in English. The summary in report_back should be in English? Or in the user's language? The system prompt says to reply in the user's detected language, but the report_back is a structured object. The example in the tools shows the summary as a string, which should be in the user's language. So the summary should be in French, and the files_produced would be the markdown file created. Confidence is high, and no low confidence reason since the findings are well-documented.```: NORMALEMENT FIXED
