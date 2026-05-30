# Todo

- envoi de fichier
  - joindre fichiers a une conversation
- outil image
  - recherche image + save workspace
  - analyse image
  - edition image
- titre page web



- **Web UI** 
  - permettre de donner un titre a la conversation (le LLM pourrait en deduire un par defaut et le user pourrait l'editer)
  - possibilite de supprimer des conversations (bouton edit au survol > popup "details conversation" > bouton delete)
  - ranger les conversation par ordre chronologique inverse (normelement la derniere interraction devrait faire "remonter" la conversation en haut de la liste; abandonner si on a pas les infos cote backend sur la "derniere interraction")
- injection date/heure et info user systematiques ?
- L'analyse automatique des échanges pour extraire des faits reste hors scope (trop complexe pour le gain).
- creation d'un lien symbolique dans le workspace pour acceder a un repository externe a l'outil
- gestion historique workspace git + auto commit par tour + revert + branch
- suppression fichier workspace ?
- joindre fichier workspace a conversation
  - petites chips avec le nom du fichier et une croix pour l'enlever du contexte (exactement comme les chips in select de vuetify 4 https://vuetifyjs.com/en/components/chips/#in-selects)
  - choix dans la liste des fichiers du workspace: analyser quelle est la maniere la plus simple et ergonomique de selectionner un fichier a ajouter au message; necessite analyse (peut etre le bouton attachement du message devrait ouvrir une liste de choix de fichier existant et avoir en derniere option "envoyer un fichier" avec une icone `(+)` https://vuetifyjs.com/en/components/combobox/#usage)
  - lors d'un upload depuis  la zone message, ajouter directement le fichier en reference au mesage
  - les fichiers "joints" au message doivent etre automatiquement references dans le payload envoye au LLM
  - ajouter des liens vers preview/download d'un (ou plusieurs) fichiers du workspace dans la reponse
  - bonus: possibilite de telecharger tous les fichiers du workspace en zip ?
- personna + vocal associe
  - creation de personna assistant (ex glados, ton desinvolte, ironique)
  - association d'un model vocal
- incitation a continuer la conversation en amglais (non respect de la langue user)
- supprimer formatage markdow avant lecture vocale
- les demandes "repond simplement ..." ne devraient pas etre routees vers jeanmichel