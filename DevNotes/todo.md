# Todo

## Inputs

Etudier ces modeles

 encodeurs à tête de classification → non servables par Ollama ; 2/3 = sentiment, 1 = sûreté de contenu → hors-sujet

https://huggingface.co/knowledgator/opir-multitask-multilang-v1.0
https://huggingface.co/nlptown/bert-base-multilingual-uncased-sentiment
https://huggingface.co/yangheng/deberta-v3-base-absa-v1.1

+ prompt degueu 

- injection date/heure et info user systematiques ?

## Connectivite

- compatibilite du systeme avec mcp

## recherches searXNG

- verifier comment sont gerees les langues de recherche; le processus de pensees de nos agent est en anglais, mais certaines requetes peuvent cibler du contenu d'autre langues.

## Memory

- L'analyse automatique des échanges pour extraire des faits reste hors scope (trop complexe pour le gain).
- comment choisir les memory qu'on inject pour un sujet en particulier

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

Upmc	Medium	https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/fr/fr_FR/upmc/medium/fr_FR-upmc-medium.onnx?download=true	https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/fr/fr_FR/upmc/medium/fr_FR-upmc-medium.onnx.json?download=true	French	France

## Dette technique (repéré 2026-06-02)

- ~~**Vestige `requests`/`artifacts`**~~ → ✅ soldé 2026-06-02. Confirmé : en v1 `requests`
  était l'arbre d'orchestration multi-agent persisté en SQL (parent_request_id = la pile,
  status = la machine à états) ; la revolucion v2 l'a déplacé vers la récursion Python +
  `messages[]` + `events.jsonl` (migrate_102 a droppé la table). Restaient des morts :
  `db.create_request`/`update_request_status` (0 appelant) + dataclass `Request` → supprimés ;
  `conv_status` (jamais granté) → supprimé ; `self_inspect._activity_snapshot` (granté à
  meta-analyst via `self_inspect_activity`, **crashait** sur `SELECT FROM requests/artifacts`)
  → recâblé sur `events.jsonl` (RequestStarted = volume, DelegationCompleted low = santé,
  HookFired deny, top agents). migrate_102 avait recâblé `_sandbox_snapshot` vers le JSONL
  mais oublié `_activity_snapshot` juste au-dessus.

## Futur

### Sources de MCP

https://glama.ai/mcp/servers
https://mcpservers.org/

### A voir MCP server

https://glama.ai/mcp/servers/brave/brave-search-mcp-server

### Pour piece detachees

https://github.com/mikelewis1971/ai_workspace_mcp/blob/main/ai_workspace_mcp.py

### Dinguerie de MCP server (necessite chrome + desktop)

https://github.com/opentabs-dev/opentabs

### re-etudier E2B

pour la sandbox propre, vient avec son serveur MCP https://mcpservers.org/servers/e2b-dev/mcp-server