# To Do

## Bugs

- reload apres modif: ecran blanc + erreur WS `ws://localhost:3000/?token=w1r-muA8VllM`. (fonctionne si on ouvre avec nu autre navigateur en mode prive, forcement nu problemet de storage ou  de token, mais on devrai etre rediriges vers l'auth, pas rester sur un truc vide)

## A verifier

- graphify vraiment utilise / utile (sinon ca degage) ?
- on monte bien le workspace et le repo dans la sandbox (genre pour ecrire un script python d'action et le faire tourner sur le repo, en respectant le bon point de montage des 2; exemple un scrip python qui liste des elements du repo doit etre execute avec les bons chemins)

## a faire

- rafraichir le paradigm viewer/editor
- un agent code plan qui sache faire de la todo
- fix ruff
- B7 (Étage C) : faire tourner `repo_test` DANS le conteneur du projet (image projet, deps présentes) au lieu de l'hôte, une fois la sandbox projet rodée
- F4 : garde anti ré-délégation verbatim du router — changer d'approche / escalader après 2× `confidence=low` sur une tâche quasi identique (à faire si ré-observé)
