# To Do

## Bugs

- regression effet de bord sur les conversations, quand on reprend une conversation existante, dans l'interface web; en plus du message utilisateur, on voit dans la fenetre les `[ORCHESTRATOR]...`, `[TODO_RECAP]...` du processus de pensee comme des message envoyes par l'utilisateur ( alors qu'il font partie du processus de thinking ); normalement ces elements sont affiches en direct lors de la reflexion, mais ne sont pas repris apres (surtout quand on reouvre une ancienne couversation ou qu'on recharge la page); On a change quelquechose a ce niveau ? on peut afficher l'historique des pensees maintenant (risque de surcharge visuelle) ou c'est juste un effet de bord d'une de nos recentes interventions ?

## A verifier

- graphify vraiment utilise / utile (sinon ca degage) ?
- on monte bien le workspace et le repo dans la sandbox (genre pour ecrire un script python d'action et le faire tourner sur le repo, en respectant le bon point de montage des 2; exemple un scrip python qui liste des elements du repo doit etre execute avec les bons chemins)

## a faire

- raffraichir le paradigm viewer/editor
- determiner la meilleure maniere de pouvoir "brancher un repo" (donner acces au code, analyser les sources, lancer des commandes) dynamiquement a une conversation.
Par exemple dans l'interface web, on rempli un parametre "code repo" (local ou ssh) et le systeme jean michel devient capable d'y acceder et de le faire vivre.

## bonus:

- booster les capacites offertes par git pour le mode repo (lecture historique, dif, ...)
