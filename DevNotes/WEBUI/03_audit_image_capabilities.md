# Audit & réflexion — Capacités image pour Jean-Michel

> **Statut : à trancher.** Ce document fait l'état des lieux, pose les options et
> recommande une voie K.I.S.S. pour chaque décision. Rien n'est implémenté. Objectif :
> que tu choisisses *quoi* (et *si*) on construit avant d'écrire une ligne de code.
> Date : 2026-05-30. Convention : suite de `01_audit_api_async_webui.md` /
> `02_audit_user_memory_isolation.md`.

---

## 0. TL;DR — décisions & recommandations

| # | Décision | Options | Reco K.I.S.S. |
|---|---|---|---|
| D1 | Servir une image au front | endpoint authed dédié vs réutiliser `/download` | **Endpoint `GET …/workspace/image?path=`** (vrai MIME, blob→objectURL) |
| D2 | Vignettes | (A) original + taille CSS · (B) `.thumbs` + Pillow | **(A) en v1** ; (B) si images lourdes/nombreuses |
| D3 | Clic sur une vignette | lightbox in-app vs nouvel onglet | **Lightbox `v-dialog`** (reste authentifié) |
| D4 | Listing du workspace | exposer vs masquer les dotfiles | **Masquer `.`** (cache `.thumbs/`, propre) |
| D5 | Recherche d'images | enrichir web/wikipedia vs `image_search` dédié | **`image_search`** (catégorie SearXNG) |
| D6 | `image_search` télécharge ? | URLs seules vs download workspace | **URLs+miniatures seules en v1** (zéro SSRF/quota) |
| D7 | Vision : où vit l'image | base64 dans `messages.json` vs **fichier workspace** | **Fichier workspace, base64 transitoire** |
| D8 | Vision : comment le modèle voit | outil `analyze_image` vs base64 éphémère sur le tour | **Outil `analyze_image(path, question)`** |
| D9 | Vision : quel modèle | gemma4 (multimodal) vs granite (dispatcher) | **gemma4 only ; image ⇒ DEEP forcé** |

**Principe directeur (cadré par toi) :** *on ne met jamais d'image en base64 dans les
messages LLM persistés ; le base64 n'apparaît qu'au moment précis où gemma4 doit analyser,
encodé depuis le workspace, puis jeté.* Le workspace est la source de vérité.

**Deux constats qui rendent tout ça peu coûteux :**

1. **La vision est quasi-gratuite côté plomberie LLM.** `OllamaClient.chat_messages`
   forwarde les messages **verbatim** à Ollama ([llm.py:158](../../src/jeanmichel/llm.py#L158))
   — un champ `images:[base64]` sur un message atteint le modèle **sans aucune modification
   de la couche LLM**. Gemma4 est multimodal sur **toutes** ses variantes.
2. **La sécurité est déjà résolue.** Tout `/workspace/*` est derrière
   `auth.require_conversation_owner` + `safe_resolve`. Un endpoint image qui réutilise cette
   garde est **automatiquement** isolé par utilisateur. Rien de neuf à inventer.

---

## 1. État des lieux (vérifié dans le code)

### 1.1 Service fichiers du workspace + modèle de sécurité

- Endpoints (tous owner-scopés) dans [api/app.py](../../src/jeanmichel/api/app.py) :
  `GET …/workspace` (arbre, `list_tree`), `…/workspace/file` (lecture texte UTF-8 tronquée
  100 Ko), `…/workspace/download` (octets via `FileResponse`,
  `media_type="application/octet-stream"` — **pas de vrai MIME**), `…/workspace/upload`,
  `…/workspace/zip`.
- **Garde unique** : `auth.require_conversation_owner`
  ([api/auth.py](../../src/jeanmichel/api/auth.py)) résout le Bearer → user, vérifie en BDD
  `user_owns_conversation`, renvoie 403 sinon. **C'est ce qui empêche Alice de lire les
  fichiers de Bob.** Anti-traversal : `safe_resolve`
  ([tools/_workspace.py](../../src/jeanmichel/tools/_workspace.py)) — refuse `..`, chemins
  absolus, et tout ce qui sort de la racine workspace.
- **Conséquence forte** : n'importe quel endpoint sous `…/workspace/*` qui dépend de
  `require_conversation_owner` + passe son `path` par `safe_resolve` hérite gratuitement de
  l'isolation. Un endpoint image **n'a rien de nouveau à sécuriser**.

### 1.2 Auth ≠ `<img src>` → le pattern « blob »

L'auth est par **header `Authorization: Bearer`**. Une balise `<img src="/api/…">` ne peut
**pas** porter de header. Le contournement existe déjà pour le TTS
([api.js](../../web/src/api.js) `tts`, [stores/conversations.js](../../web/src/stores/conversations.js)
`playBlob`) : `fetch` avec le header → `blob()` → `URL.createObjectURL(blob)` → `<audio>`.
**Le même pattern donne `<img :src="objectUrl">`.** Aucun token dans une URL, aucun fichier
public devinable.

### 1.3 Listing — les dotfiles ne sont pas filtrés

`list_tree` / `_entry` ([service/workspace.py](../../src/jeanmichel/service/workspace.py))
itèrent `sorted(iterdir())` **sans** filtrer les noms commençant par `.`. Un dossier
`.thumbs/` (ou des images d'entrée cachées) **apparaîtrait** dans l'arbre. Masquer = ajouter
`if not name.startswith(".")` aux **2 points** d'itération (racine + enfants récursifs).

### 1.4 Vignettes — pas de lib image

Ni `Pillow` ni autre lib image dans [pyproject.toml](../../pyproject.toml) ni dans le venv.
De vraies vignettes nécessitent d'ajouter `Pillow` (extra `web`). Alternative sans
dépendance : servir l'original et le contraindre en CSS (`max-width`).

### 1.5 LLM / Ollama / Gemma4 — multimodal prêt côté transport

- `OllamaClient.chat_messages(*, messages, tools, …)` passe `messages` **verbatim**
  ([llm.py:156-168](../../src/jeanmichel/llm.py#L156)). Un message
  `{"role":"user","content":"…","images":["<b64>"]}` part tel quel vers le client `ollama`.
  **Zéro changement de la couche LLM** pour activer la vision.
- API Ollama `/api/chat` : images = **liste de chaînes base64** par message
  (`message.images`). **Contrainte** : pas d'API « par chemin de fichier » — le base64 est
  obligatoire **au moment de l'appel**. Donc base64 = **transitoire**, jamais à persister.
- Modèles ([config.py](../../src/jeanmichel/config.py)) : `MAIN_MODEL=gemma4:latest`
  (routeur jean-michel), `SUBAGENT_DEFAULT_MODEL=gemma4:latest`,
  **`DISPATCH_MODEL=granite4.1:8b`** (Tier 0). ⚠️ **granite n'est pas multimodal** → on
  n'envoie **jamais** d'image au dispatcher.
- Le message user est forgé dans `run_main_loop`
  ([orchestrator_v2.py:748](../../src/jeanmichel/orchestrator_v2.py#L748)) : aujourd'hui
  `{"role":"user","content":user_text}`. `persistence.save_messages` fait un dump JSON → un
  champ `images` y survivrait **mais on ne veut pas l'y mettre** (cf. D7).
- `estimate_messages_tokens` ([tokens.py](../../src/jeanmichel/tokens.py)) ne compte que
  `content` → **n'estime pas** le coût d'une image (~256 tokens, cf. §5).

### 1.6 Recherche — SearXNG, catégories non exploitées

`web_search._do_search` ([tools/web_search.py:86-100](../../src/jeanmichel/tools/web_search.py#L86))
envoie `q, format=json, language, safesearch=0` — **aucune `categories`**. Or SearXNG gère
`categories=images` nativement et renvoie par résultat : `title`, `url`, `img_src`,
`thumbnail_src`, `source`, `engine`. Un `image_search` est donc une **variante fine** de
`_do_search`. Ajout d'un outil = pattern établi : fichier outil + enregistrement dans
`build_registry` ([tools/__init__.py](../../src/jeanmichel/tools/__init__.py)) + migration de
grant (`agent_tools`) + régénération `db/schema.sql` (cf. `migrate_106/107`).

---

## 2. Partie 1 — Afficher des images dans le flot (la partie « simple »)

### 2.1 (D1) Endpoint de service image

Nouveau `GET /api/conversations/{id}/workspace/image?path=…` (owner-scopé) qui :
`safe_resolve(path)` → vérifie que c'est un fichier → renvoie `FileResponse` avec le **bon
MIME** (`mimetypes.guess_type`, défaut `application/octet-stream`). Front : `api.workspaceImage(id, path)`
→ blob → `objectURL` → `<img>`. *(On pourrait étendre `/download` au lieu d'un nouvel
endpoint, mais un endpoint dédié au MIME image reste plus lisible et borne la surface.)*

### 2.2 (D2) Vignettes — deux options

- **Option A — original + CSS (reco v1, zéro dépendance).** On sert l'image entière, le
  front l'affiche en `max-width: 240px` dans la bulle. Simple, immédiat, pas de Pillow, pas
  de cache, pas de dossier caché. **Limite honnête** : une photo de 5 Mo est transférée en
  entier pour une vignette → lourd si beaucoup d'images / grosses images.
- **Option B — `.thumbs` + Pillow (quand A coince).** À la 1ʳᵉ demande, générer
  `workspace/.thumbs/<hash(path)>.webp` (ex. 320 px max), servir ça ; régénérer si l'original
  est plus récent. Nécessite `Pillow`, le masquage `.thumbs` (D4) et une petite invalidation
  de cache. **C'est ton idée initiale et c'est la bonne dès que les images sont lourdes** —
  mais inutile tant qu'on bricole avec de petites images.

> **Reco** : démarrer en A (KISS), garder B comme optimisation câblable derrière le **même**
> endpoint (param `?thumb=1`) — l'UI ne change pas le jour où on l'active.

### 2.3 (D3) Clic → grand format

- **Lightbox in-app** (reco) : un `v-dialog` plein écran affiche l'image via le même
  `objectURL` (déjà authentifié). Pas de token qui fuit, reste dans l'app.
- **Nouvel onglet** : `window.open(objectURL)` fonctionne (l'objectURL est valide pour la
  session), mais l'onglet ne peut pas re-fetcher (pas de header) et c'est moins intégré.

### 2.4 (D4) Masquer `.thumbs` (et les images d'entrée cachées)

Filtre `not name.startswith(".")` dans `list_tree` + `_entry`. Effet de bord **voulu** :
tous les dotfiles disparaissent du listing (cohérent avec un workspace « propre »).

### 2.5 (1c) Rendu dans la conversation

Le store tient déjà la liste des fichiers du workspace
(`conv.wsFiles`, [stores/conversations.js](../../web/src/stores/conversations.js)) et
[ChatPane.vue](../../web/src/components/ChatPane.vue) affiche déjà des **chips fichiers** sous
chaque message (`messageFiles`). Évolution minimale : si un fichier référencé a une extension
image (`png/jpg/jpeg/gif/webp/svg`), le rendre en **`<img>` vignette** (cliquable → lightbox)
au lieu d'une chip. Réutilise toute la mécanique existante.

### 2.6 (D5/D6) `image_search`

Outil `image_search(query, language?, count?)` = `_do_search` + `categories=images`,
renvoyant `[{title, url, img_src, thumbnail_src, source}]`. **En v1 : renvoie les URLs +
miniatures**, ne télécharge rien (cf. §4 SSRF). Le front peut afficher les miniatures
(distantes, via `<img>` direct — ce sont des URLs publiques, pas du workspace). Si plus tard
on veut nourrir la vision avec un résultat, on ajoute un `image_fetch(url)` borné qui
l'enregistre dans le workspace (décision séparée).

---

## 3. Partie 2 — Vision Gemma4 (la partie « profonde »)

### 3.1 Capacités Gemma4 à exploiter (recherche)

- **Toutes les variantes gemma4 sont multimodales** (Texte+Image ; E2B/E4B aussi audio).
  Encodeur vision ~150M (E2B/E4B) à ~550M (26B/31B).
- **Budget visuel configurable** : 70 / 140 / 280 / 560 / 1120 tokens par image →
  ~64 / 121 / **256** / 529 / … tokens réellement consommés. Compromis vitesse ↔ détail.
- **Multi-images** par prompt supporté. **Placement** : mettre l'image **avant** le texte.
- ⚠️ Le `docs/GEMMA4.md` du repo liste `<|image|>` comme placeholder mais **n'a pas de
  section vision** — à compléter si on implémente.

### 3.2 Principe : workspace-centric, base64 transitoire (cadré par toi)

L'image **vit dans le workspace** (uploadée, jointe, ou produite par un outil). La
conversation la **référence par chemin** (texte), comme aujourd'hui pour les pièces jointes.
Le base64 n'existe **que** le temps d'un appel vision, encodé à la volée depuis le fichier,
puis jeté. **`messages.json` reste 100 % texte** → replay sain, pas de contexte qui explose,
pas de stockage dupliqué.

### 3.3 (D8) Comment le modèle « voit » — deux designs

- **(A) Outil `analyze_image(path, question)` — reco.**
  Un nouvel outil lit le fichier workspace (`safe_resolve`), encode en base64, fait **un
  appel gemma4 vision transitoire** (`chat_messages` avec un message
  `{role:user, content:question, images:[b64]}`), et **renvoie une description texte** dans la
  conversation. Le `messages[]` principal ne porte **jamais** de base64. L'agent « regarde »
  une image exactement comme il lit un fichier texte avec `workspace_view` — l'outil masque
  juste qu'il y a de la vision derrière. *Aligné au maximum sur le workspace + ton contrainte
  base64.* Marche aussi pour une image issue d'`image_search` (une fois enregistrée).
  Coût : 1 outil + 1 grant + (option) routage.
- **(B) base64 éphémère sur le tour.**
  Quand une image est jointe au message, l'orchestrateur encode le fichier workspace en base64
  et l'attache au message user **du seul tour concerné** (non persisté ; on retire/zappe le
  champ avant `save_messages`, on ne garde que la note de référence texte). Utile si le modèle
  doit raisonner **image + conversation dans le même contexte** (ce que (A) ne permet pas,
  puisque (A) résume l'image en texte). Plus intrusif : il faut threader `images`
  (`run_turn_streaming`→`run_turn`→`run_main_loop`), gérer la non-persistance, le budget
  tokens, et le routage.

> **Reco** : **(A)** comme première brique (KISS, isolé, testable seul, zéro base64 persisté,
> réutilise le workspace). Passer à **(B)** seulement si l'usage montre un besoin de
> raisonnement multimodal en contexte. (A) et (B) ne s'excluent pas.

### 3.4 (D9) Routage & modèle

- Vision = **gemma4 uniquement**. Le **dispatcher granite ne reçoit jamais d'image**.
- Conséquence : si l'utilisateur joint une image, **forcer DEEP** (court-circuiter le
  raccourci Tier 0 ALEXA) — l'image implique de toute façon un traitement par l'agent
  principal.
- Sous-agents : ne donner `analyze_image` qu'à des agents dont le modèle est multimodal
  (gemma4). Un agent sur granite ne doit pas l'appeler.

### 3.5 Source des images pour la vision

Réutiliser **le flux pièces jointes déjà en place** (l'utilisateur joint un fichier du
workspace au message — cf. feature précédente) + les uploads + (plus tard) `image_search` +
`image_fetch`. Aucune nouvelle voie d'entrée à inventer.

---

## 4. Audit sécurité

- **Isolation par utilisateur** : tout endpoint image/thumbnail **doit** dépendre de
  `require_conversation_owner` et passer son `path` par `safe_resolve`. C'est suffisant et
  déjà éprouvé — Alice ne peut pas lire les images de Bob (403), ni sortir du workspace.
- **Pas d'URL publique** : on sert via endpoints authed + `objectURL` (blob de session, non
  partageable, non devinable). Jamais de chemin statique exposé.
- **`.thumbs` masqué** : évite d'exposer des artefacts internes dans le listing.
- **⚠️ SSRF / abus si on télécharge des images web** (le jour où `image_fetch`/download
  arrive) : un `image_search` qui *renvoie des URLs* est sans risque côté serveur (le
  navigateur charge les miniatures distantes). Mais **télécharger** une URL côté serveur =
  SSRF potentiel (accès réseau interne), bombe de décompression, dépassement de quota. Si on
  l'implémente : restreindre aux hôtes des résultats SearXNG, **plafonner la taille**
  (réutiliser `WORKSPACE_UPLOAD_MAX_BYTES` + quota workspace), timeouts, refuser les schémas
  non-http(s) et les IP privées. → **raison de garder D6 = URLs seules en v1.**
- **Base64 transitoire** : ne jamais écrire le base64 d'une image dans `messages.json` /
  `events.jsonl` (fuite + poids). Encoder en mémoire, passer à Ollama, libérer.

---

## 5. Coûts & limites (vérité crue)

- **Tokens/image** : ~256 au budget par défaut (280). Multi-images = multiplie d'autant.
  `analyze_image` (design A) **confine** ce coût à un appel isolé ; le design B le réinjecte
  potentiellement à chaque tour si mal géré (raison de plus pour A).
- **`estimate_messages_tokens` ne compte pas les images** → en design B, sous-estimation du
  budget ⇒ risque de dépasser le contexte. À corriger si on fait B.
- **Poids base64** : +33 % vs binaire ; encoder à la volée évite de le stocker, mais l'appel
  vision reste plus lourd qu'un appel texte (latence GPU).
- **Vignettes A** : transfert de l'original → coûteux en bande passante sur grosses images
  (bascule en B le moment venu).
- **granite text-only** : toute fuite d'image vers le Tier 0 = erreur/perte → la règle
  « image ⇒ DEEP, gemma4 only » est non négociable.

---

## 6. Reco K.I.S.S. + sprints (séquencés, chacun livrable seul)

> Chaque sprint est indépendant et utile seul. Tu greenlights à la carte.

- **I1 — Afficher les images (v1).** Endpoint `…/workspace/image` (MIME) ; masquage des
  dotfiles ; rendu `<img>` vignette (original + CSS) dans `ChatPane` à partir de `wsFiles` ;
  lightbox `v-dialog` au clic. *Aucune dépendance nouvelle.* → couvre « vignettes dans le
  flot » + « clic → grand format » + l'audit sécu (réutilise la garde).
- **I2 — `image_search`.** Outil = `_do_search` + `categories=images` (URLs+miniatures, pas
  de download) ; enregistrement registry ; migration de grant (+ paradigme de routage
  optionnel) ; régén `schema.sql`. Tests + suite verte.
- **I3 — Vignettes `.thumbs` + Pillow (optionnel).** Seulement si I1 montre que les images
  sont trop lourdes. Ajoute `Pillow`, génération/cache `.thumbs`, param `?thumb=1` sur
  l'endpoint I1. UI inchangée.
- **I4 — Vision `analyze_image` (design A).** Outil workspace-bound qui encode à la volée +
  appel gemma4 vision transitoire + retour texte ; grant aux agents multimodaux ; règle
  « image jointe ⇒ DEEP ». Tests via MockClient (script d'une réponse vision). **Compléter
  `docs/GEMMA4.md`** (section vision : budget visuel, multi-images, placement, base64).
- **I5 — Vision en contexte (design B, optionnel).** Seulement si un besoin de raisonnement
  image+conversation émerge : threader `images` éphémères + non-persistance + comptage tokens
  + `image_fetch` borné (avec les gardes SSRF de §4).

---

## 7. Questions ouvertes (à trancher par toi)

1. **D2/I3** : on démarre en « original + CSS » (A) et on n'ajoute Pillow/`.thumbs` que si
   nécessaire — OK ? Ou tu veux les vraies vignettes dès le départ ?
2. **D8** : la vision démarre par l'**outil `analyze_image`** (A, image→texte) — suffisant
   pour ton usage, ou tu veux d'emblée l'image **dans le contexte** du modèle (B) ?
3. **Périmètre immédiat** : on n'implémente probablement pas tout. Quels sprints (I1 ? I1+I2 ?
   I4 ?) veux-tu réellement enclencher après ce doc — ou aucun pour l'instant ?
4. **`image_search`** : URLs seules en v1 confirmé (pas de download serveur tant que les
   gardes SSRF ne sont pas en place) ?
