# Audit & réflexion — Capacités image pour Jean-Michel

> **Statut : décisions verrouillées (2026-05-30).** Cet audit a servi à trancher ; les
> réponses de l'utilisateur sont intégrées ci-dessous. Reste à dérouler les sprints §6 une
> fois ce plan validé. Convention : suite de `01_audit_api_async_webui.md` /
> `02_audit_user_memory_isolation.md`.

---

## 0. TL;DR — décisions

| # | Décision | Choix verrouillé |
|---|---|---|
| D1 | Servir une image | **Endpoint authed `GET …/workspace/image?path=`** (vrai MIME, blob→objectURL, comme le TTS) |
| D2 | Vignettes | **Génération auto, fit 1024×1024, Pillow → WebP, cache `.thumbs/`** — **une seule dérivée pour affichage ET vision** (dès la v1) |
| D3 | Clic sur une vignette | **Lightbox `v-dialog`** in-app (reste authentifié) |
| D4 | Listing workspace | **Masquer les dotfiles** (cache `.thumbs/`) |
| D5 | Recherche d'images | **`image_search` dédié** (catégorie SearXNG) |
| D6 | `image_search` télécharge ? | **Recherche = URLs/miniatures distantes seules** ; un **`image_fetch(url)` borné 22 Mo** assure le cas « grab → workspace → analyse » |
| D7 | Vision : où vit l'image | **Fichier workspace** ; base64 **transitoire**, jamais dans `messages.json` |
| D8 | Vision : comment le modèle voit | **Les deux, complémentaires** : (A) outil `analyze_image` puis (B) image jointe en contexte (détail §3.3) |
| D9 | Vision : quel modèle | **gemma4 only ; image ⇒ DEEP forcé** (granite, le dispatcher, est texte-only) |

**Principes directeurs (cadrés par l'utilisateur) :**

1. **Le workspace est la source de vérité.** On ne met **jamais** d'image base64 dans les
   messages LLM persistés ; le base64 n'existe qu'au moment précis d'un appel vision, encodé
   à la volée depuis le fichier, puis jeté.
2. **La normalisation Pillow a un double bénéfice.** Le même pipeline qui produit la vignette
   d'affichage **réduit la bande passante** *et* **garantit un format que Gemma4/Ollama
   acceptent** (JPEG/PNG). Conséquence clé : **la vision ne mange jamais l'original brut**
   (qui peut être un TIFF/BMP/exotique de 22 Mo) mais une **copie normalisée**. **Une seule
   taille (~1024 px) et une seule dérivée** servent à la fois l'affichage et la vision (K.I.S.S.) :
   la vision marche mieux ~1024, et l'affichage se contente de la même image contrainte en CSS.
3. **Réutiliser l'existant.** Sécurité = `require_conversation_owner` + `safe_resolve` (rien à
   réinventer). Sources d'images = le flux *pièces jointes* déjà en place + `image_search`.
   Vision côté transport = `chat_messages` forwarde les messages **verbatim** → un champ
   `images:[base64]` atteint Ollama sans toucher la couche LLM.

---

## 1. État des lieux (vérifié dans le code)

### 1.1 Service fichiers + sécurité
- Endpoints (owner-scopés) dans [api/app.py](../../src/jeanmichel/api/app.py) :
  `…/workspace` (arbre `list_tree`), `…/workspace/file` (texte UTF-8 tronqué), `…/download`
  (`FileResponse`, `application/octet-stream` — **pas de vrai MIME**), `…/upload`, `…/zip`.
- **Garde unique** : `auth.require_conversation_owner`
  ([api/auth.py](../../src/jeanmichel/api/auth.py)) → 403 si l'utilisateur ne possède pas la
  conversation. Anti-traversal : `safe_resolve`
  ([tools/_workspace.py](../../src/jeanmichel/tools/_workspace.py)). **C'est ce qui isole
  Alice de Bob.** Tout endpoint image qui réutilise ces deux gardes hérite de l'isolation.
- **Auth ≠ `<img src>`** : l'auth est par header Bearer ; une balise `<img>` ne peut pas le
  porter. Le **pattern TTS existant** (`api.tts` → blob → `URL.createObjectURL`) est le
  modèle : `fetch` authentifié → `blob()` → objectURL → `<img>`. Pas de token en URL.

### 1.2 Listing — dotfiles non filtrés
`list_tree`/`_entry` ([service/workspace.py](../../src/jeanmichel/service/workspace.py))
itèrent `sorted(iterdir())` **sans** filtrer les `.` → un `.thumbs/` apparaîtrait. Masquer =
`if not name.startswith(".")` aux **2 points** d'itération.

### 1.3 Pas de lib image
Ni Pillow ni autre dans [pyproject.toml](../../pyproject.toml)/venv. **À ajouter** (`Pillow`,
extra `web`). C'est le socle de D2 et de la normalisation vision.

### 1.4 LLM / Ollama / Gemma4 — multimodal prêt côté transport
- `chat_messages` passe `messages` **verbatim** à Ollama
  ([llm.py:156-168](../../src/jeanmichel/llm.py#L156)) → `{"role":"user","content":"…",
  "images":["<b64>"]}` part tel quel. **Zéro modif de la couche LLM.**
- API Ollama `/api/chat` : images = **liste base64** par message ; pas d'API par chemin → le
  base64 est obligatoire **à l'appel** (donc transitoire). Ollama **redimensionne en interne**
  (~896 px) et accepte JPEG/PNG/WebP (BMP/TIFF aussi, mais JPEG/PNG = sûr).
  [(Ollama Vision)](https://docs.ollama.com/capabilities/vision)
- Gemma4 : **toutes les variantes multimodales** (Texte+Image) ; **budget visuel
  configurable** 70/140/280/560/1120 tokens → ~64/121/**256**/529 tokens/image ; **multi-images**
  OK ; **placer l'image avant le texte**. ⚠️ [docs/GEMMA4.md](../../docs/GEMMA4.md) n'a pas de
  section vision → à compléter.
- Modèles ([config.py](../../src/jeanmichel/config.py)) : `MAIN_MODEL=gemma4:latest`,
  **`DISPATCH_MODEL=granite4.1:8b` (texte-only)**. Message user forgé en
  [orchestrator_v2.py:748](../../src/jeanmichel/orchestrator_v2.py#L748).
  `estimate_messages_tokens` ([tokens.py](../../src/jeanmichel/tokens.py)) ne compte que
  `content` → **n'estime pas** le coût image (à corriger pour le design B).

### 1.5 Recherche — SearXNG, catégories non exploitées
`web_search._do_search` ([tools/web_search.py:86](../../src/jeanmichel/tools/web_search.py#L86))
n'envoie **aucune** `categories`. SearXNG gère `categories=images` nativement → par résultat :
`title, url, img_src, thumbnail_src, source`. Ajout d'outil = pattern établi (fichier outil +
`build_registry` + migration grant + régén `schema.sql`, cf. `migrate_106/107`).

---

## 2. Partie 1 — Afficher des images (dérivée 1024 px dès la v1)

### 2.1 (D1) Endpoint image
`GET …/workspace/image?path=…[&thumb=1]` (owner-scopé) : `safe_resolve` → si `thumb=1`,
sert/produit la vignette `.thumbs/` ; sinon sert l'original avec le **bon MIME**
(`mimetypes.guess_type`). Front : `fetch` authentifié → blob → objectURL → `<img>`.

### 2.2 (D2) Vignettes — pipeline Pillow (recherche intégrée)
Helper unique `normalize_image(src, max_px, fmt)` :
- `ImageOps.exif_transpose(img)` **d'abord** (corrige l'orientation des photos téléphone).
  [(Pillow ImageOps)](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html)
- `img.thumbnail((max_px, max_px))` : **préserve le ratio** et **n'agrandit jamais** (fit dans
  1024×1024, un panorama devient 1024×N).
- Sauvegarde **WebP** (acceptée par Ollama → sert l'affichage *et* la vision ; bascule JPEG
  triviale si un jour un format pose souci).
- **GIF animé** : on prend la **1ʳᵉ frame** (vignette statique — Pillow gère mal l'animé).
- **SVG** : **non supporté par Pillow** → **servi tel quel** (le navigateur le rend/scale ;
  pas de vignette raster). [(formats Pillow)](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html)
- Cache : `workspace/.thumbs/<hash(path)>.webp`, régénéré si l'original est plus récent (mtime).
  Génération **à la demande** (au 1ᵉʳ `?thumb=1`), pas à l'upload (évite de bloquer l'upload).

Taille **unique** : `IMAGE_MAX_PX=1024` → **une seule dérivée** `.thumbs/<hash>.webp`,
réutilisée pour l'affichage (contrainte CSS dans la bulle) **et** la vision (base64). C'est le
« double bénéfice » : moins de bande passante **et** format normalisé pour Gemma4, sans gérer
deux tailles.

### 2.3 (D3) Clic → grand format
Lightbox `v-dialog` plein écran via le même objectURL (reste authentifié, rien ne fuit).
*(Le nouvel onglet via `window.open(objectURL)` marcherait mais sort de l'app et ne peut pas
re-fetcher — écarté.)*

### 2.4 (D4) Masquer `.thumbs`
Filtre `not name.startswith(".")` dans `list_tree` + `_entry`.

### 2.5 (1c) Rendu dans la conversation
Réutiliser `conv.wsFiles` + `messageFiles`
([ChatPane.vue](../../web/src/components/ChatPane.vue)) : si un fichier référencé a une
extension image, le rendre en **`<img>` vignette** (cliquable → lightbox) au lieu d'une chip.

### 2.6 (D5/D6) `image_search` + `image_fetch`
- **`image_search(query, …)`** = `_do_search` + `categories=images` → renvoie
  `[{title, url, img_src, thumbnail_src, source}]`. **URLs seules** : le front affiche les
  `thumbnail_src` **distantes** (URLs publiques, chargées par le navigateur — **aucun fetch
  serveur**, donc zéro SSRF).
- **`image_fetch(url)`** (pour le cas « analyser une image trouvée sur le web ») : télécharge
  l'URL **dans le workspace** sous gardes (§4), **plafond 22 Mo** (= `WORKSPACE_UPLOAD_MAX_BYTES`,
  comme l'upload), puis l'image devient un fichier workspace normal → vignette + `analyze_image`.
  C'est le « grab in workspace / send to gemma4 » demandé.

---

## 3. Partie 2 — Vision Gemma4 (centrée workspace)

### 3.1 Préambule commun aux deux options
L'image **vit dans le workspace**. Pour la vision, on encode **la dérivée normalisée déjà
produite pour l'affichage** (Pillow, ~1024 px WebP, cache `.thumbs/`) — **jamais l'original
brut** → bande passante bornée **et** format garanti pour Gemma4 (Ollama redimensionne ensuite
à ~896 px en interne). Même fichier, double usage. Le base64 est **transitoire**,
**jamais** écrit dans `messages.json`/`events.jsonl`. Image ⇒ **gemma4 + DEEP** (granite =
texte-only).

### 3.2 Capacités Gemma4 à exploiter
Budget visuel configurable (on visera ~256 tokens/image, budget 280 — bon compromis),
multi-images, image-avant-texte. À documenter dans `docs/GEMMA4.md`.

### 3.3 (D8) Les deux façons de faire « voir » gemma4 — en détail

#### Option A — Outil `analyze_image(path, question)` (image → texte, hors-bande)
**Déroulé :** l'agent décide qu'il doit voir `diagramme.png` → appelle
`analyze_image("diagramme.png", "quelle tendance ?")` → le handler : `safe_resolve` → dérivée
normalisée 1024 px (`.thumbs/`) → base64 → **un appel gemma4 vision isolé**
(`chat_messages(messages=[{role:"user", content:question, images:[b64]}])`) → récupère une
**réponse texte** → la renvoie comme `tool_response`. Ce texte entre dans la conversation comme
un message `tool`. **Le `messages[]` principal ne contient jamais de base64.**

**À construire :** 1 outil workspace-bound (`make_spec(conv_folder)`), 1 grant BDD, (option) un
paradigme de routage. **Aucune** modif de la boucle orchestrateur, **aucun** threading
d'images, **aucun** comptage tokens spécial dans la boucle principale.

**Avantages :** zéro base64 persisté ; contexte principal léger (seul le texte de description y
entre — le coût ~256 tokens est payé dans l'appel isolé, pas réinjecté à chaque tour) ; isolé
et **testable** (MockClient scripte la réponse vision) ; **modèle principal agnostique** (c'est
l'outil qui parle à gemma4) ; marche pour **toutes** les sources, dont `image_fetch` → **c'est
LE chemin du cas « analyser une image trouvée sur le web »**.

**Inconvénients (vérité crue) :** le modèle principal voit une **description**, pas les pixels →
il ne peut pas remarquer un détail hors de la `question` posée ; **deux sauts** (l'agent doit
poser la bonne question) ; re-questionner la même image = nouvel appel (sauf cache) ; pas de
raisonnement « image + conversation » simultané.

**Idéal pour :** OCR/lecture, décrire/extraire, inspection agentique d'un fichier, analyse
d'une image fraîchement récupérée du web.

#### Option B — base64 éphémère attaché au tour (image dans le contexte)
**Déroulé :** l'utilisateur **joint** `photo.jpg` (déjà dans le workspace via les pièces
jointes) et écrit « c'est quoi ce composant ? ». À l'envoi, l'orchestrateur encode la copie
normalisée et l'attache au message user **de ce tour** : `{role:"user", content:"…",
images:[b64]}` (image avant texte). gemma4 reçoit **les pixels + toute la conversation**.
**Avant `save_messages`, on retire `images`** : on ne persiste que le texte + la note de
référence (« Image jointe : `photo.jpg` »).

**À construire :** threader `images` (`run_turn_streaming`→`run_turn`→`_run_deep_turn`→
`run_main_loop`→msg user L748) ; **non-persistance** (strip avant save) ; **comptage tokens**
image dans `estimate_messages_tokens` (sinon dépassement de contexte) ; **DEEP forcé** ;
encodage à la volée depuis le workspace.

**Avantages :** **vrai multimodal en contexte** (le modèle voit l'image ET la conversation →
raisonnement holistique, suivi de questions sur des détails qu'il a lui-même remarqués) ; UX
« naturelle » : je dépose une image et on en discute.

**Inconvénients (vérité crue) :** **plus invasif** (plomberie sur tout le tour + non-persistance
+ comptage tokens) ; chaque tour qui ré-inclut l'image **re-paie** ~256 tokens et **re-transmet**
le base64 (latence) ; **multimodal obligatoire** (exclut granite) ; **resume/replay** : l'image
n'étant pas persistée, un tour repris ne « revoit » pas l'image sauf à la ré-encoder depuis la
référence workspace (logique à prévoir).

**Idéal pour :** compréhension conversationnelle, « regarde ça et discutons », Q&R itératif sur
une image déposée dans le chat.

#### Pourquoi les deux (complémentaires, pas redondantes)
- **A** = inspection **agentique** + cas web-search→fetch→analyse. **Isolé, faible risque.**
- **B** = UX **chat** « dépose une image et parles-en ». **Plus riche, plus invasif.**
- Séquencement conseillé : **A d'abord** (brique isolée, sert déjà le cas web), **B ensuite**.
  Si l'usage premier est « déposer une image dans le chat », on peut prioriser B.

---

## 4. Audit sécurité
- **Isolation** : tout endpoint image/thumbnail **dépend de** `require_conversation_owner` +
  passe `path` par `safe_resolve` → Alice ne lit pas les images de Bob, rien ne sort du
  workspace. **Déjà éprouvé, rien de neuf.**
- **Pas d'URL publique** : service via endpoints authed + objectURL (blob de session, non
  partageable, non devinable). `.thumbs` masqué du listing.
- **`image_fetch` (download serveur) — les gardes** : plafond **22 Mo**
  (`WORKSPACE_UPLOAD_MAX_BYTES`) ; **http(s) seulement** ; **bloquer IP privées/loopback**
  (anti-SSRF) ; timeout ; `Content-Type: image/*` exigé ; écriture via chemin workspace sûr ;
  idéalement restreindre aux hôtes issus des résultats `image_search`. Les miniatures de
  recherche (`thumbnail_src`) sont chargées **par le navigateur** → pas de fetch serveur, pas
  de SSRF.
- **Base64 transitoire** : jamais dans `messages.json`/`events.jsonl`.

## 5. Coûts & limites (vérité crue)
- **Tokens/image** ~256 (budget 280). **A** confine ce coût à l'appel isolé ; **B** le
  réinjecte à chaque tour qui ré-inclut l'image → **B doit** mettre à jour
  `estimate_messages_tokens`.
- **Bande passante** : la dérivée Pillow unique (~1024 px) **réduit fortement** le poids vs
  l'original, pour l'affichage comme pour la vision ; +33 % base64 reste, mais sur image bornée.
- **Format** : la copie normalisée (JPEG/PNG) **élimine** les « format not supported » d'Ollama
  sur des entrées exotiques (TIFF/BMP…).
- **granite text-only** : règle « image ⇒ gemma4 + DEEP » non négociable.
- **Pillow** : nouvelle dépendance (extra `web`).

## 6. Sprints (tous greenlit ; séquencés, chacun livrable seul)
- **I1 — Affichage + dérivée image 1024 px.** `Pillow` (extra web) ; helper `normalize_image`
  (fit 1024, exif_transpose, WebP) ; endpoint `…/workspace/image?path=&thumb=1` (MIME + dérivée
  `.thumbs/` WebP réutilisée par la vision, exif_transpose,
  GIF→frame0, SVG servi tel quel) ; masquage dotfiles ; rendu `<img>` + lightbox dans
  `ChatPane`. *Pur UX, zéro risque LLM.*
- **I2 — `image_search`.** `_do_search`+`categories=images` (URLs + `thumbnail_src`) ; registry ;
  migration grant (+ paradigme routage) ; régén `schema.sql` ; tests + suite verte.
- **I3 — Vision A : `analyze_image` + `image_fetch`.** `analyze_image(path, question)`
  (workspace-bound, réutilise la dérivée 1024 px, appel gemma4 isolé → texte) ; `image_fetch(url)` (grab →
  workspace, cap 22 Mo, gardes SSRF §4) → cas « analyser une image du web » ; grants ; règle
  image⇒gemma4 ; tests MockClient ; **compléter `docs/GEMMA4.md`** (section vision).
- **I4 — Vision B : image jointe en contexte.** Threader `images` éphémères (non persistés,
  strip avant save) ; comptage tokens ; image jointe ⇒ DEEP ; encodage à la volée depuis le
  workspace ; tests.

## 7. Décisions verrouillées (récap) & résidu
**Verrouillé :** dérivée image **unique 1024 px** Pillow→WebP (affichage = vision) dès v1 (D2) ;
lightbox in-app (D3) ; `image_search`
URLs-only + `image_fetch` borné 22 Mo (D5/D6) ; vision workspace-centric, base64 transitoire,
copie normalisée pour la bande passante + le format (D7) ; gemma4 + DEEP (D9) ; **tous les
sprints sont à enclencher** (aucun bloqueur).
**Seul résidu à confirmer :** l'ordre A→B (recommandé) ou prioriser B (chat « dépose une
image ») — décidable après lecture du §3.3.
