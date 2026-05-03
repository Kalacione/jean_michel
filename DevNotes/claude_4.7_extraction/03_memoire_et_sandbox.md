# Politique de mémoire et sandbox d'exécution — Jean-Michel

Position en perspective : ajouter à Jean-Michel la capacité de manipuler des fichiers et exécuter du code amène 3 risques majeurs :
1. **Corruption** des fichiers de conversation (artefacts, summary, journal).
2. **Évasion** vers le système hôte (path traversal, exécution de commandes système).
3. **Pollution** de la mémoire (insertions BDD non maîtrisées, paradigmes qui se mangent).

Ce document pose les règles d'une isolation **stricte par défaut**, libérée de manière mesurée selon le rôle de l'agent et l'outil mobilisé.

---

## 1. Architecture de la mémoire

### 1.1 Trois zones distinctes

```
conversations/{date}_{uuid}/
├── conversation.md                  # ZONE PROTÉGÉE — orchestrateur uniquement
├── summary.md                       # ZONE PROTÉGÉE — archivist uniquement
├── HHMMSS_{agent}_{kind}.md         # ZONE PROTÉGÉE — orchestrateur uniquement (artefacts)
└── workspace/                       # ZONE LIBRE — agents peuvent y écrire
    ├── notes.md
    ├── computed_data.json
    └── ...
```

**Convention** : tout ce qui est à la racine du dossier de conversation est **lecture seule** pour les agents LLM. Le sous-dossier `workspace/` est leur zone de jeu.

Pourquoi `workspace/` plutôt que `artifacts/` (ta proposition) : le mot "artifact" est déjà utilisé dans la BDD (`artifacts` table) pour désigner les fichiers de trace de l'orchestrateur. Réutiliser ce nom pour la zone libre des agents créerait de la confusion. Alternative équivalente : `scratch/` ou `agent_files/`. Je pars sur `workspace/` ci-dessous.

### 1.2 BDD — zone strictement protégée

**Aucun agent LLM ne doit pouvoir écrire dans la BDD.** Même un outil exposé "modifier_paradigm" est trop risqué : un LLM mal aligné peut neutraliser ses propres garde-fous.

L'écriture en BDD est exclusivement le fait de :
- L'orchestrateur (statuts de requête, artefacts, conversations)
- Les outils d'admin manuels (`debug/admin.py`, `paradigm_matrix`, etc.)
- Les migrations versionnées

Les agents ont accès en **lecture** aux données de leur conversation courante via des outils dédiés si nécessaire (ex : `get_request_history`, `get_summary`), jamais en accès direct SQL.

### 1.3 Conversation.md — append-only par l'orchestrateur

Le journal `conversation.md` est écrit **exclusivement par l'orchestrateur**. C'est la trace humaine-lisible de l'exchange. Aucun agent ne doit pouvoir y écrire — sinon un agent malveillant pourrait réécrire l'historique perçu par l'humain.

### 1.4 Summary.md — écrit par l'archivist via l'orchestrateur

Aujourd'hui le pattern est correct : l'archivist produit le contenu via `return_to_user`, l'orchestrateur écrit le fichier. Aucun agent (y compris l'archivist) n'a un outil "write_summary" direct. **Garder cette discipline**.

---

## 2. Sandbox d'exécution

### 2.1 Principe

Un agent peut bénéficier d'un **environnement Linux Ubuntu 24** isolé via Docker, avec :
- Le dossier `workspace/` de la conversation courante monté en `/workspace` (lecture-écriture).
- Le dossier de la conversation **lui-même** monté en `/conversation` en **lecture seule**.
- Aucun accès réseau par défaut (l'agent passe par les outils Python pour atteindre le web).
- Un set restreint de commandes autorisées.

### 2.2 Image Docker

Reco : `ubuntu:24.04` minimal + un set fixé d'outils standards. Pas de `docker run` ad hoc — un seul container long-vivant par conversation, créé au premier appel d'un outil sandbox, détruit à la clôture de la conversation.

**Critère de succès** : un agent compromis ne doit ni accéder au filesystem hôte, ni atteindre le réseau hors flux contrôlés, ni persister entre conversations.

### 2.3 Set de commandes autorisées (proposition)

Plutôt qu'un shell complet, exposer une liste blanche stricte :

| Commande | Usage |
|---|---|
| `ls`, `cat`, `head`, `tail`, `wc` | Inspection fichiers |
| `python3` | Exécution scripts (avec virtualenv pré-créé, pas de `pip install`) |
| `jq` | Manipulation JSON |
| `grep`, `sed`, `awk` | Traitement texte |
| `curl` | **Désactivé par défaut**, activable per-agent en BDD |
| `git` | **Désactivé**. Pas pertinent dans le scope. |

Le set est défini en BDD : table `agent_sandbox_grants` parallèle à `agent_tools`.

### 2.4 Garde-fous techniques

- **Resource limits** : CPU, mémoire, durée d'exécution (ex : 30 secondes max par commande).
- **Network** : `--network=none` par défaut. Si un agent a besoin du web, il passe par un outil Python qui implémente l'appel (pas `curl` brut).
- **Path mount** : `--read-only` sur le mount `/conversation`, lecture-écriture sur `/workspace`.
- **No privileges** : `--cap-drop=ALL`, user non-root.
- **Auto-destruction** : container nettoyé à la fin de la conversation, ou après 30 minutes d'inactivité.

### 2.5 Nouveau tool : `bash_sandbox`

Spec en `08_outil_bash_sandbox.md` ci-dessous.

---

## 3. Outils de manipulation de fichiers (workspace/)

Trois nouveaux outils proposés, tous bornés à `workspace/` :

- `workspace_create_file` — créer un fichier dans le workspace
- `workspace_read_file` — lire un fichier (workspace + lecture seule sur conversation root)
- `workspace_str_replace` — édition par remplacement de chaîne unique
- `workspace_list` — lister le contenu du workspace

Ces outils **n'ont pas accès** :
- Au dossier de conversation au-delà du workspace (sauf lecture explicite via `workspace_read_file` qui peut résoudre les fichiers de la racine, mais en lecture seule).
- Au filesystem hôte.
- Aux autres conversations.

### 3.1 Validation chemin

**Toute opération valide le chemin** par :
1. Résolution absolue (`Path.resolve()`).
2. Test de containment : `resolved.is_relative_to(workspace_root)` — sinon, erreur.
3. Refus des liens symboliques sortants (`is_symlink()` + résolution recursive).

L'implémentation existante de `conv_read_file` est un bon point de départ — étendre le pattern.

---

## 4. Politique d'accès par rôle

| Agent | Lecture conv root | Lecture workspace | Écriture workspace | Sandbox bash | Modification BDD |
|---|:-:|:-:|:-:|:-:|:-:|
| jean-michel | ✓ | ✓ | ✗ | ✗ | ✗ |
| summarizer | ✓ | ✓ | ✗ | ✗ | ✗ |
| weather-specialist | ✓ | ✓ | ✗ | ✗ | ✗ |
| wikipedia-specialist | ✓ | ✓ | ✗ | ✗ | ✗ |
| comparator-specialist | ✓ | ✓ | ✗ | ✗ | ✗ |
| critical-thinker | ✓ | ✓ | ✗ | ✗ | ✗ |
| synthesizer | ✓ | ✓ | ✗ | ✗ | ✗ |
| archivist | ✓ | ✓ | ✗ | ✗ | ✗ |
| **(futur)** code-runner | ✓ | ✓ | ✓ | ✓ | ✗ |
| **(futur)** data-analyst | ✓ | ✓ | ✓ | ✓ | ✗ |

**Aucun agent existant n'écrit dans le workspace ni n'a accès à la sandbox.** L'outillage est mis à disposition pour de futurs agents (code-runner, data-analyst, document-builder) — pas activé en rétro-compatible sur l'existant.

---

## 5. Garde-fous au niveau orchestrateur

L'orchestrateur applique ces vérifications :

1. **Au démarrage de chaque requête**, créer le sous-dossier `workspace/` si l'agent a accès en écriture (`agent_workspace_grants` table).
2. **Tool dispatch** : un agent qui appelle `workspace_create_file` ou `bash_sandbox` mais qui n'a pas le grant correspondant → tool_response d'erreur, pas d'exception silencieuse.
3. **Inventaire des fichiers créés** : chaque création dans `workspace/` est enregistrée comme `artifact` en BDD (`kind = 'workspace_file'`), pour traçabilité.
4. **Audit log** : chaque commande sandbox est loggée dans un artefact `kind = 'sandbox_command'` avec stdout/stderr/code retour.

---

## 6. Nouveau schéma BDD (additif)

```sql
-- Grants d'accès en écriture au workspace
CREATE TABLE agent_workspace_grants (
  agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  PRIMARY KEY (agent_id)
);

-- Grants de commandes sandbox autorisées par agent
CREATE TABLE agent_sandbox_grants (
  agent_id   INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  command    TEXT NOT NULL,           -- ex: 'python3', 'cat', 'jq'
  PRIMARY KEY (agent_id, command)
);

-- Étendre artifacts.kind
-- (pas un nouveau schéma — juste élargir le CHECK)
ALTER TABLE artifacts ... -- nouveau kind: 'workspace_file', 'sandbox_command'
```

À implémenter dans une **migration 006** quand les outils correspondants seront prêts.

---

## 7. Décisions à prendre

1. **Nom du sous-dossier** : `workspace/` (ma reco) ou `artifacts/` (ta proposition initiale, mais collision avec le nom DB) ou `scratch/` ?
2. **Persistance entre tours** : le `workspace/` est-il conservé entre les tours d'une même conversation (ma reco : oui), ou recréé à chaque tour ?
3. **Quota disque** : limite-t-on la taille du `workspace/` ? Reco : oui, soft-limit à ~100 Mo, alerte au-delà.
4. **Sandbox réseau** : entièrement off (ma reco), ou whitelist d'API publiques (ex : open-meteo, wikipedia) ?
5. **Modèle d'image Docker** : ubuntu:24.04 minimal + setup script, ou une image pré-construite versionnée dans le repo ?
6. **Ordre d'implémentation** :
   - Phase A : `workspace_*` outils (manipulation fichiers, sans Docker — direct sur filesystem hôte avec sandboxing par chemin).
   - Phase B : `bash_sandbox` avec Docker.
   - Phase C : agents qui en bénéficient (code-runner, etc.).
   - À valider.

---

## 8. Ce qui n'est PAS proposé

- **Pas d'outil "modifier la BDD"**. Trop risqué, jamais de raison légitime pour un agent LLM.
- **Pas d'outil "modifier conversation.md"**. L'historique n'est pas réécrit, c'est le seul moyen de garder une trace fiable.
- **Pas d'outil "supprimer un fichier au-delà du workspace"**. Le seul cas où un fichier serait supprimé est par l'orchestrateur en clôture de conversation.
- **Pas d'agent autonome avec sandbox networkée par défaut**. Si on ajoute un jour un agent qui doit faire de la recherche libre, c'est une décision architecturale séparée.
