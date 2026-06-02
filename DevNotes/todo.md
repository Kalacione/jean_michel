# Todo

- injection date/heure et info user systematiques ?

## recherches searXNG

- verifier comment sont gerees les langues de recherche; le processus de pensees de nos agent est en anglais, mais certaines requetes peuvent cibler du contenu d'autre langues.

## Memory

- L'analyse automatique des échanges pour extraire des faits reste hors scope (trop complexe pour le gain).
- comment coisir les memory qu'on inject pour un sujet en particulier

## Files 

- creation d'un lien symbolique dans le workspace pour acceder a un repository externe a l'outil ?
- normalisation de noms de fichier pour eviter les injections degueulasses ou problematiques
- gestion historique workspace git + auto commit par tour + revert + branch
- ~~suppression fichier workspace~~ → ✅ fait (`workspace_delete_file` / `workspace_delete_dir` + `workspace_create_dir`, grantés code-runner + workspace-manager)

## dialog

- incitation a continuer la conversation en anglais (non respect de la langue user)
- les demandes "repond simplement ..." ne devraient pas etre routees vers jeanmichel

## vocal personna

- personna + vocal associe
  - https://github.com/tjiho/French-tts-model-piper
  - https://rhasspy.github.io/piper-samples/#fr_FR-upmc-medium
  - creation de personna assistant (ex glados, ton desinvolte, ironique)
  - association d'un model vocal

Voices:
Gilles (quebec)
Gilles	Low	https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/fr/fr_FR/gilles/low/fr_FR-gilles-low.onnx?download=true	https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/fr/fr_FR/gilles/low/fr_FR-gilles-low.onnx.json?download=true	

Voix feminine jessica

## Dette technique (repéré 2026-06-02)

- **Vestige `requests`** : la table `requests` a été droppée en v2 (migrate_102) mais reste
  référencée par `db.update_request_status` / `db.create_request` + `tools/self_inspect.py`
  + `tools/conv_status.py` (SELECT/COUNT/UPDATE `requests`). ⇒ ces outils planteraient s'ils
  sont invoqués (self_inspect_activity est granté à meta-analyst). À traiter : soit retirer le
  tracking v1 de ces tools, soit recréer une table `requests`. (record_artifact, même lignée, a
  été purgé.)

Upmc	Medium	https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/fr/fr_FR/upmc/medium/fr_FR-upmc-medium.onnx?download=true	https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/fr/fr_FR/upmc/medium/fr_FR-upmc-medium.onnx.json?download=true	French	France