Voilà l'analyse complète.

---

## Thinking mode : pour ou contre par agent

### Ce que confirment les sources

**Ollama blog + Qwen3 docs** :
- Thinking fonctionne avec les tool calls (Qwen génère d'abord le `reasoning_content`, puis les `tool_calls` structurés). Pas de casse.
- Désactiver thinking = réponse directe, plus rapide, mêmes capacités pour les tâches simples.
- Qwen recommande explicitement `enable_thinking=False` pour les étapes tool-call pures quand la latence compte.

**Notre code** : le commit `5881e4c` d'aujourd'hui désactive déjà l'injection `<think>` si `tools` est fourni — donc pour les steps de tool-calling sur notre Gemma uncensored, on ne paye déjà plus le coût du thinking.

---

### Analyse par agent

| Agent | Tâche dominante | Thinking utile ? |
|-------|----------------|-----------------|
| `jean-michel` | Routing complexe, décomposition | **Oui — critique** |
| `critical-thinker` | Analyse multi-angle, thèse/antithèse | **Oui — critique** |
| `meta-analyst` | Introspection, patterns sur des données | **Oui** |
| `comparator-specialist` | Comparaison nuancée | **Oui** |
| `wikipedia-specialist` | Choisir quoi extraire d'un long article | Oui, marginal |
| `synthesizer` | Fusionner des réponses existantes | Oui, marginal |
| `summarizer` | Formater un brief déjà structuré | **Non — overhead pur** |
| `document-builder` | Mise en forme, structure Markdown | **Non — overhead pur** |
| `workspace-manager` | Tool calls fichiers, exécution | **Non — inutile** |
| `archivist` | Store/retrieve, aucun raisonnement | **Non — inutile** |
| `weather-specialist` | Fetch + format | **Non — inutile** |
| `code-runner` | Écrire/exécuter du code | Oui marginal (debugging) |

---

### Verdict

Ton intuition est **juste sur le fond**, avec une nuance importante :

- **Le gain réel est sur les modèles à thinking natif** (Qwen3-think, DeepSeek-R1, etc.). Pour eux, thinking = tokens budget supplémentaire hors contexte, donc coût non-négligeable sur des réponses simples.
- **Sur notre Gemma Q4_K_M** : le thinking est prompt-injecté. On injecte `<think>…</think>` uniquement sur les calls sans tools (déjà optimisé). Le coût est réel mais limité — le modèle va juste pondre quelques lignes avant de répondre.

**Ce qui vaut vraiment la peine** : désactiver `thinking_mode` dans la DB pour les 4 agents "exécution pure" — ça ne changera rien sur le Gemma actuel (non-natif), mais prépare proprement l'archi pour le jour où tu bascules sur un vrai modèle thinking (Qwen3, R2, etc.) :

```sql
UPDATE agents SET thinking_mode=0 
WHERE code IN ('summarizer','document-builder','workspace-manager','archivist','weather-specialist');
```

Veux-tu que j'applique ça maintenant (+ migration DB + schema.sql) ?