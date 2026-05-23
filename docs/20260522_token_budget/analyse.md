Voici l'analyse complète en croisant les deux :

---

## État actuel : aucune context window governance

**Ce qu'on a comme "budgets" :**
- `MAX_STEPS_PER_REQUEST = 8` — anti-boucle d'appels outils, rien à voir avec les tokens
- `MAX_RECURSION_DEPTH = 10` — anti-délégation infinie
- Limites par outil individuellement (100k bytes pour `conv_read_file`, 50k pour bash, 12k pour Wikipedia…)

**Ce qu'on n'a pas :**
- Zéro token counting
- `num_ctx` jamais passé à Ollama → le modèle fait ce qu'il veut avec sa fenêtre par défaut
- Les stats Ollama (`prompt_eval_count`, `eval_count`) sont dans la réponse HTTP mais llm.py ne les lit pas
- Aucune partition system / history / tools / output

---

## Le design actuel a un truc intelligent (involontaire)

L'orchestrateur orchestrator.py ne concatène **pas** l'historique de toutes les itérations. À chaque step, `running_user_text` est **remplacé** par un bloc ORCHESTRATOR + les résultats des outils du dernier step. Donc une conversation n'explose pas linéairement en tokens à chaque itération.

Ce qui peut exploser à l'intérieur d'une seule requête : si 8 tool calls retournent chacun un blob, ils s'accumulent dans `tool_responses` et le bloc devient potentiellement énorme. Avec les 8 steps max, une seule requête pourrait atteindre 8 × 100k = 800k chars dans le pire cas (lectures de fichiers successives).

---

## Zones à risque concret

| Zone | Risque | Cause |
|------|--------|-------|
| Prompt système | Élevé | identity + directives DB + briefing complet entier, sans plafond |
| Tool results cumulés dans un step | Élevé | 8 steps × taille outil illimitée globalement |
| `self_inspect` scope `architecture` | Moyen | README + schema.sql entiers, non tronqués |
| Délégations en cascade avec briefings riches | Moyen | chaque niveau reconstruit un nouveau system prompt avec de la matière |
| `summary.md` inter-tours | Faible | grandit sans borne mais reste textuel compressé |

---

## Ce que ferait l'approche Copilot chez nous

Le principe est simple : avant chaque `self.llm.chat(...)` ([orchestrator.py L347](src/jeanmichel/orchestrator.py#L347)), on calcule un budget :

```
context_window (ex: 32k pour gemma4:26b MoE)
├── 25% → system prompt (identité + directives + profil)  
├── 40% → inbound briefing + tool results cumulés  
├── 15% → output réservé (num_predict)  
├── 10% → marge de sécurité  
└── 10% → résumé historique inter-tours
→ déclencher compression si > 80% du total
```

Les 3 pièces à implémenter :

1. **Estimateur de tokens** dans llm.py — une approche chars/4 suffit pour commencer, à calibrer avec `prompt_eval_count` qu'Ollama retourne déjà (on lit juste pas le champ)

2. **Context budget checker** appelé dans l'orchestrateur juste avant `self.llm.chat()` — compare les segments mesurés à leurs enveloppes, applique une politique de compression en cascade (tronquer les tool results en premier, puis compresser le briefing, jamais toucher au system core)

3. **`num_ctx` + `num_predict`** passés dans les options Ollama — actuellement on envoie uniquement `temperature`, on peut ajouter ces deux-là sans changer l'interface

---

## Est-ce qu'on devrait le faire ?

**Maintenant : non.** On n'a pas encore observé de dépassement réel en production — le modèle 26B MoE avec 128k de contexte natif est très tolérant, et le pattern `running_user_text` remplacé à chaque step nous protège de l'essentiel.

**Quand ça devient prioritaire :**
- dès qu'un agent commence à `conv_read_file` plusieurs gros fichiers dans le même step
- si on active `self_inspect` sur des gros repos
- si on branche un agent sur un pipeline de RAG avec beaucoup de chunks

**L'implémentation minimale à fort ROI** : juste lire `prompt_eval_count` dans la réponse Ollama et le logger dans l'artefact DB. Ça nous donnerait des données empiriques réelles sans rien changer au comportement — et en 2-3 sessions on saurait exactement où on en est.