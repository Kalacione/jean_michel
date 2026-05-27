Comme tu l'auras devine, nous sommes en train de creer un systeme de harness local, avec plusieurs agents specialistes et des outils a leur disposition. Le but est d'assurer une autonomie et une securite totale en faisant rouler les LLM locallement.
Le systeme doit etre capable de gerer globalement 2 types de requetes:
1. requetes simples (Alexa like): quelle heure est-il, quel temps fait-il, quelle est la definition de xxx, ...; pour ce mode, la reflexion ne devrait meme pas etre enclenchee et on pourrait limiter tourner avec un LLM ultra petit (comme ministral); il prend la reauete, lance un outil et donne la reponse
2. analyse en profondeur (claude like): compare des elements, fait des recherches sur un projet, regarde ma codebase; cela demande relfexion, creation d'un workspace et d'une todolist, recherches, analyses, mise a jour de la todo, creation de sous taches, comparaison, ...; necessite forcement un thinking mode et l'orchestrateur python doit gerer les LLM comme un chef d'orchestre

On deja mis en place des outils pas mauvais:
- le systeme de paradigmes en BDD est une excellente base
- le workspace comme espace de memoire partagee est super utile
- l'outil de sandbox de code avec docker est super cool
- les outils web_search et wikipedia fonctionnent
- les prompts dynamiques sont une bonne base

Mais on est forces de constater que le systeme est une illusion; on dirait qu'il fonctionne; mais il a de gros defaut de conceptions.
Le coeur du systeme n'est pas assez robuste.

Voici ce qu'on peut deja remonter comme problemes:
- on demarre toujours avec le meme LLM, thinking mode actif, meme si l'utilisateur nous demande l'heure ou la meteo; ressources gaspillees, lenteur d'executions
- l'orchestrateur n'est pas assez dirigiste et on s'appuie trop sur la bonne volonte des LLM de respecter les consignes des prompts au lieu de les "tenir fermement en laisse"; les processus doivent etre decrits plus precisements et assurer une predictivite des comportements;
- on a tente de mettre des garde fous de convergence, mais on heurte toujours la limite avec les agents de recherche; on dirait qu'ils veulent aspirer internet et finissent par s'embourber
- les LLMs hallucinent trop pour piloter la resolution d'une problematique ou l'organisation de recherches; Il faut les utiliser pour ce qu'ils sont, sans les croire doues d'intelligence. Ils sont doues pour identifier et reproduire des motifs, a nous de leur fournir le contenu pour diriger les motifs.
- on a pas assez souvent l'effet "contexte frais et construit dynamiquement" pour chaque agent specialiste; ils finissent par tourner en rond dans leur coin avant d'etre arretes par l'orchestrateur
- les limites de tour et de temps sont des cache misere; on a beau essayer de regler les valeurs dans tous les sens on a jamais quelque chose de stable et consistant dans les reponses
- il y a une confusion et une redondance entre le `plan.md` et le `todo.json`
- on souhaite avoir une li;ite de recursivite en profondeur, pas a l'horizontale; une recherche peut mener a des suggestions de recherches, qui donnent lieu a d'autres recherche, .. ; pas juste un enchainement lineaire qui meme a des doublons de recherche


Ce que l'on souhaite:
- demarrer par defaut avec un LLM ridicule, capable de gerer les outils pour prendre la premiere requete
- si c'est une requete simple: on appelle l'outil et on repond direct
- si c'est une requete complexe, on lance une phae d'analyse avec une reflexion en premier lieu, creation d'une todo et traitement des taches
- la todo list doit etre dynamique et permettre l'ajout de sous elements (de sous recherches, des analyses croisees)
- les LLM ne doivent pas enclencher les etapes suivantes eux meme; ils font des retours a l'orchestrateur python qui met a jour le state
- on a pas vraiment de state machine et on aimerait se passer de RAG pour rester simple
- tirer pleinement parti du workspace comme espace de memoire partage par les LLM; les echanges entre les agents sont hasardeux, alors qu'on a un espace pour ecrire et partager.
- Ne pas laisser un LLM orchestrer le systeme; se baser sur des logiques de code evolutives;
- 

Ce que l'on attend:
- une analyse complete de l'orchestrateur et de ses defauts de conception
- une analyse des solutions utilisees par Claude et Copilot pour assurer la bonne gestion du processus d'orhestration
- une analyse rapide du git pour voir les evolutions et les mauvaises
- des appels unitaires a des LLM pour des actions precises et bien definies
- un processus d'orchestration clair, deterministe et evolutif
- une logique implacable, limpide, evolutive, K.I.S.S.

Nous sommes sur une branche a part, tu as carte blanche pour analyser et proposer une solution viable et durable pour notre ambition. Nous sommes conscient que notre systeme est plein de bonne intentions, mais peche dans sa conception; on a herite de ca et on pense qu'il y a un potentiel a liberer pas tres loin.
N'hesite pas a remettre en question ceratins choix de logique ou d'architecture si ils sont debiles et si il existe de meilleures solutions. On a beaucoup fait d'aller/retours et de mise au point pour arriver au constat actuel: c'est pas mal presente, ca a l'air de marcher, on a de bons composants, mais ca tient pas ensemble, ca tombe dans des boucles sans fin et ca ne tient pas ses promesses.

Tu peux faire toutes les recherches que tu souhaite et ecrire tous les documents supports necessaires dans le dossier `DevNotes/REVOLUCION`; Tu es un expert harness, tu sait jongler avec les tools, les skills et les agents. N'hesite pas a analyser ta propre memoire pour nous faire des recommandations.

On commence par une phase d'audit et d'analyse, on itere ensemble sur les pistes de solutions et ensuite on ecrira les documents necessaires a la mise en oeuvre de ces solutions.

Sent toi a l'aise dans notre "bac de pieces de Lego" pour assembler notre vision.