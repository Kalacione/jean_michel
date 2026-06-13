# To Do

## Bugs

## a verifier

- faire un bench sur le budget de token allouable, il me semble tout petit sachant que chaque LLM est independant avec un fresh start et que les LLM qu'on utilise on 100k tokens. De plus notre archi du serveur de dev (2x 32Go de VRAM) devrait nous mettre a l'aise.
- VÉRIFIER EN LIVE le "plan mode" (livré, branche `ca_plan_pour_moi`, doc `docs/20260613_plan_mode/`) : sélecteur Plan/Edit (gauche du Envoyer, défaut Plan en code/analyse) → tour plan read-only (aucune mutation, todo_write forcé) → barre Approuver/Modifier → éditeur inline → exécution. Cf. étape 5 ci-dessous une fois rodé.
- lister et analyser tous les paradigmes de tous les agents pour voir si c'est pas deconnanant; checker ce que dit le meta_analyst et si il sert encore a quelque chose

## a faire

- on est definitivement en v2. Checker si la v1 sert encore; sinon degager la v1 et consolider (orchestrateur, tests, ...)
- rafraichir le paradigm viewer/editor
- PLAN MODE — étape 5 (APRÈS rodage live côté code) : analyse écrite de généralisation aux autres modes (analyse/recherche : plan de recherche validé ? chat : marginal ? vocal : hors-sujet) + patterns d'orchestration transverses (vagues/dépendances/complexity-routing, cf. doc d'audit). Décider l'élargissement sur données réelles, pas spéculativement.
