
## Analyse : injection du summary en mode `analyse`

### Ce que fait le mécanisme aujourd'hui

`_prefix_summary()` a un court-circuit explicite :
```python
if self.mode == "analyse" or self.turn_index == 0:
    return user_input
```

En `chat`/`vocal`, à partir du tour 1, le contenu de `summary.md` est préfixé au `inbound_text` passé à `jean-michel`, dans un bloc structuré :
```
## Conversation summary so far
{contenu de summary.md}

## New user turn
{question de l'utilisateur}
```

Ce texte enrichi arrive dans `ctx.inbound_text` → rendu dans le bloc `## Inbound briefing` du prompt système. C'est le seul vecteur de mémoire entre tours.

---

### Ce qui se passe en `analyse` actuellement

Chaque tour, `jean-michel` voit :
- Son identité, son mode, le `turn_index`
- Le `inbound_text` = la question brute, sans aucun contexte des tours précédents
- `turn_index` dans `# CONTEXT > Conversation` (le LLM *sait* qu'on est au tour 2, 3, etc. mais n'a pas accès au contenu)

Il n'y a **pas de `summary.md`** en `analyse` parce que `_run_archivist` n'est jamais appelé. Donc même si on retirait le court-circuit, il n'y aurait rien à injecter.

---

### Scénario de la proposition

Activer le summary en `analyse` = les deux sous-mécanismes ensemble :
1. Appeler `_run_archivist` après chaque tour en `analyse` (pour écrire le `summary.md`)
2. Retirer le `or self.mode == "analyse"` dans `_prefix_summary`

---

### Bénéfices réels

**1. Continuité contextuelle légère sans le overhead narratif de `chat`**
L'archivist produit des bullet points compressés (< 1500 mots, paradigme `archivist_tone`). Le LLM au tour 3 sait ce qui a été établi aux tours 1 et 2 sans que l'humain doive le rappeler. Cas d'usage concret : "explique-moi les stoïciens" → "compare avec les épicuriens" → "et les cyniques ?" — aujourd'hui le 3e tour n'a aucun contexte.

**2. `--resume` devient vraiment utile en `analyse`**
Actuellement, reprendre une conversation `analyse` ne restaure que le `turn_index`. Avec un `summary.md`, le tour suivant a de la substance. Sans ça, `--resume` en `analyse` = juste reprendre la numérotation.

**3. Cohérence de la sémantique de "conversation"**
On a décidé qu'un lancement = un dossier. Un dossier sans mémoire inter-tours est une collection de transactions indépendantes. Le summary complète la sémantique.

---

### Risques réels

**R1 — Coût d'un appel LLM supplémentaire par tour**
L'archivist est un agent à part entière : 1 appel Ollama de plus après chaque réponse. Sur Gemma 4 en local, ça représente ~10-30 secondes supplémentaires selon la machine. En `analyse`, le user ne s'y attend pas. En `chat`, il est déjà dans une dynamique de conversation et l'attente est acceptable.

**R2 — Le summary peut polluer des tours intentionnellement indépendants**
Le cas d'usage *"3 questions indépendantes profondes dans la même session"* (mentionné explicitement dans le rapport lifecycle) devient moins propre. Si les questions sont vraiment sans rapport, le summary injecte du contexte non pertinent que le LLM doit ignorer. C'est du bruit dans le prompt, pas de l'aide.

**R3 — L'archivist peut mal résumer des exchanges techniques**
Le format `archivist_format` est 4 headings + bullet points. Il fonctionne bien pour des conversations naturelles. Pour des sessions `analyse` avec du code, des formules, des listes de fichiers — la compression peut perdre des détails critiques. Le tour suivant part d'un résumé dégradé plutôt que d'une ardoise vierge.

---

### Effets de bord

**1. `ask_human` + archivist : les échanges sont capturés correctement**
`_turn_exchanges` est déjà alimenté lors des `ask_human` et passé à l'archivist dans `_run_archivist`. Aucun changement requis là.

**2. `turn_index == 0` : le court-circuit pour le premier tour est déjà correct**
`_prefix_summary` teste aussi `self.turn_index == 0` indépendamment du mode. Tour 0 → pas de summary injecté quoi qu'il arrive. C'est juste.

**3. Le paradigme `followup_proposals` est restreint à `chat` en BDD**
L'archivist en mode `analyse` ne verrait pas ce paradigme (`paradigm_modes` le filtre). Pas d'effet de bord là.

**4. Tokens supplémentaires dans le prompt**
Le summary injecté augmente la taille du contexte de jean-michel à chaque tour. Sur une session longue (10+ tours), le summary peut atteindre 1500 mots (limite `archivist_tone`). Pour Gemma 4 avec une fenêtre de contexte large, ce n'est pas bloquant mais c'est à connaître.

**5. `workspace_manager`/`document-builder` en `analyse`**
Ces agents reçoivent leur `inbound_text` via `delegate_to`, pas via `_prefix_summary`. Ils ne verraient jamais le summary directement. Pas d'effet de bord.

---

### Verdict

La proposition est **architecturalement saine** : le code est déjà câblé pour ça, un seul `if` à retirer et un appel `_run_archivist` à déplacer. Mais le choix se résume à une décision de sémantique :

| | `analyse` avec summary | `analyse` sans summary |
|---|---|---|
| Sessions thématiques (questions liées) | Meilleure expérience | Contexte perdu |
| Sessions de questions indépendantes | Bruit inutile | Propre |
| `--resume` utile | Oui | Turn index seulement |
| Coût par tour | +1 appel LLM | Inchangé |

La question clé est : **qui utilise `analyse` ?** Si c'est pour de l'exploration thématique progressive (le cas le plus courant à priori), le summary est un gain net. Si c'est pour des requêtes one-shot ponctuelles dans la même session, c'est du bruit payant.

Une option intermédiaire serait de rendre le comportement configurable (flag `--with-memory` ou paradigme `session_memory` opt-in), mais ça ajoute de la complexité sans qu'aucun besoin opérationnel ne le justifie aujourd'hui.