Cette vision nous plait enormement! Enfin un orchestrateur deterministe et des calls LLM courts pour plus d'agilite.

Voici quelques points en reponse:
- pour la phase "GATHER" on a 2 outils, web_search et wikipedia; on pourrait utiliser les deux (on rajoute `-wikipedia` dans le web_search) en evitant un overlap; Il faut aussi "aider web search a bien chercher" en blindant la synthese de mots clefs pour la recherche.
- pour le run task, on est clairement des admirateurs de la puissance de Claude et de sa logique. On aimerait vraiment eviter les choses complexes a border (on vient de passer 50 sprints a essayer sans succes); 
- faut pas oublier qu'une web_search commence par ramasser jusqu'a 10 resultats si pertinent et non redondant, qu'on va aller lire apres; chacun doit etre un call court, bien trace

Nous avons a notre disposition gemma4 qui tourne pas si mal et granite en tiny qui est specialiste des entrees/dispatch.

```
$ ollama list
NAME                  ID              SIZE      MODIFIED 
qwen3:14b             bdbd181c33f2    9.3 GB    2 days ago     
granite4.1:8b         444af1c4b2fe    5.3 GB    3 weeks ago    
gemma4:26b            5571076f3d70    17 GB     4 weeks ago    
qwen3-coder:latest    06c1097efce0    18 GB     5 weeks ago    
gemma4:latest         c6eb396dbd59    9.6 GB    5 weeks ago   
```

J'aimerai raffiner la reflexion pour voir si on peut tirer partie de ce qui a ete mis en place dans notre base de donnees; En plus des paradigmes des agents, il y a tout un systeme de gestion de request / response avec les parents et enfants. On est pas obliges de le reutiliser, on peut aussi le virer si c'est plus efficace.
