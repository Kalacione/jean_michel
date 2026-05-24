# Sprint 3 — Corrélation plan.md × activité réelle

> **Statut** : Réflexion ouverte — pas encore implémenté. Ce document explore pourquoi l'idée est
> séduisante, où elle se complique, et ce qu'il faudrait résoudre avant de la coder.

---

## L'idée initiale

`conv_status` sait combien de tool_calls chaque agent a fait.  
`plan.md` sait quels steps sont ⬜/✅/🔄.  
**La corrélation** : croiser les deux pour détecter l'écart entre ce qui était prévu et ce qui se passe réellement.

Exemple utile :

```
Step 1 (web-search-specialist → web_sources.md) : ⬜ pending
Mais web-search-specialist a déjà 7 tool_calls dans cette conversation.
→ Signal : ce step est probablement bloqué ou terminé sans avoir été marqué.
```

## Pourquoi c'est plus compliqué qu'il n'y paraît

### 1. La BDD ne sait pas à quel step appartient chaque tool_call

`requests` a un `inbound_briefing` qui mentionne le step, mais c'est du texte libre.
Il n'y a pas de `plan_step_id` ou de lien structurel entre un artifact et un step du plan.

Pour corréler, il faudrait soit :
- Extraire le step depuis le briefing (parsing fragile, LLM-dépendant)
- Ou introduire un champ `step_ref` dans `requests` — changement de schéma non trivial

### 2. plan.md est un fichier Markdown, pas une table DB

Le `## Status` est une table Markdown formatée à la main par le LLM.
Parser `| 1a | web-search-specialist | ⬜ pending | web_sources.md |` depuis Python est faisable,
mais fragile : format instable, le LLM peut légèrement varier la syntax.

Alternative : stocker le plan comme JSON dans un artifact `plan_state` en parallèle du plan.md
visible. Mais ça duplique l'état et crée une source de vérité floue.

### 3. Les steps parallèles cassent la corrélation naïve

Si le plan a `1a`, `1b`, `1c` en parallèle, tous les trois font des tool_calls en même temps.
Un compteur par agent global ne distingue pas les tool_calls de `1a` de ceux de `1b`.
Il faudrait une corrélation par `dispatch_group_id` — ce qui existe en DB mais n'est
pas encore exposé dans `conv_status`.

### 4. Le signal de "step bloqué sans être marqué" est déjà partiellement couvert

`metacog_live_monitor` + `conv_status` donnent déjà le signal clé :
"web-search-specialist a 7 tool_calls". Jean-michel doit décider quoi faire.
La corrélation avec le step exact ajoute de la précision mais pas un signal fondamentalement nouveau.

---

## Ce qu'il faudrait pour que ça tienne

### Option A — Parsing Markdown défensif
Lire `plan.md` depuis Python, extraire la table `## Status` par regex,
retourner un dict `{step_label: status}`.

Problèmes : fragile, maintenance élevée, bloque sur les variations de format du LLM.

### Option B — Table `plan_steps` en DB
Lors de la création du plan, le planner insère aussi des rows dans une table `plan_steps` :

```sql
CREATE TABLE plan_steps (
    id INTEGER PRIMARY KEY,
    conversation_id TEXT,
    step_label TEXT,    -- "1a", "1b", etc.
    agent_code TEXT,
    status TEXT,        -- pending / in_progress / done / blocked
    deliverable TEXT,
    dispatch_group_id TEXT   -- lien avec les requests parallèles
);
```

Avantages : requêtable, typé, corrélable avec `requests` via `dispatch_group_id`.  
Problèmes : le planner doit écrire dans la DB (actuellement il ne fait que du workspace).
Il faut un nouvel outil `plan_step_register` et un `plan_step_update`, et s'assurer
que le LLM s'en sert de manière fiable (un autre surface d'erreur).

### Option C — Tag dans le briefing
Lors de la délégation, jean-michel injecte un tag structuré :
`[PLAN_STEP:1a]` dans l'`inbound_briefing`. `conv_status` parse ce tag pour corréler.

Plus simple que l'Option B, mais ça transforme le briefing en vecteur de données structurées
mélangé avec du texte libre — hybridation fragile.

---

## Ce que ça révèle sur notre système

L'idée de corrélation pointe vers un problème plus profond :
**notre plan existe dans deux espaces simultanément** — un fichier Markdown pour le LLM,
et une DB SQLite pour nous. Ces deux représentations ne sont pas synchronisées en temps réel.

On a optimisé le fichier pour que le LLM le lise bien. On a optimisé la DB pour que Python
le requête bien. Mais on n'a pas de **pont bidirectionnel vivant** entre les deux.

La corrélation plan.md × activité réelle nécessite ce pont. Sans lui, toute corrélation
est une approximation qui peut tromper autant qu'éclairer.

---

## Piste sérieuse — version minimaliste

Avant de faire une table `plan_steps`, tester d'abord :

1. **`conv_status` expose `dispatch_group_id`** dans ses `active_requests`
   (déjà dans la DB, pas encore retourné — modification mineure)
2. **Jean-michel tague ses délégations** : quand il délègue un step du plan,
   il ajoute dans le briefing une ligne `<!-- step:1a -->` que `conv_status`
   peut parser sans ambiguité
3. **`conv_status` retourne un dict `steps_activity`** :
   `{"1a": {"agent": "web-search", "tool_calls": 4, "status": "running"}}`

C'est le minimum viable pour que jean-michel voie "step 1a a déjà 4 tool_calls"
sans introduire de nouvelle table ni changer le schéma de `requests`.

---

## Décision

Ne pas implémenter maintenant. Les raisons :

1. Sprint 1+2 (`conv_status` + `metacog_live_monitor`) couvrent déjà le problème principal.
2. La corrélation exacte nécessite un choix d'architecture (Option A/B/C) qui mérite un test live.
3. Le bon moment pour l'implémenter : après avoir observé des conversations avec `conv_status` actif,
   pour voir si le signal "agent X a N tool_calls" est suffisant ou si jean-michel a vraiment besoin
   de savoir à quel step ça correspond.

**Trigger pour rouvrir ce document** : jean-michel appelle `conv_status`, voit un WARNING,
mais ne peut pas décider quoi faire parce qu'il ne sait pas quel step est concerné.
