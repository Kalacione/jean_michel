Confirmation. **Le synthesizer n'a pas le grant `conv_read_file`.**

---

### Bug supplémentaire identifié — Bug 5

**Le synthesizer n'a pas `conv_read_file` dans ses grants.**

Le système lui passe des `support_files` en briefing, mais il n'a aucun outil pour les lire. Face à des fichiers qu'il ne peut pas lire, il tombe dans sa mémoire paramétrique en pensée (`"Simulated Content Extraction"`) sans déclencher d'erreur visible. C'est silencieux et catastrophique.

Le `conv_read_file` doit être granted à tous les agents qui reçoivent des `support_files` — ici : synthesizer et document-builder a minima.

---

### Récapitulatif complet des 5 bugs

| # | Bug | Conséquence observée |
|---|-----|---------------------|
| 1 | Retour "step budget exhausted" opaque au parent — pas d'erreur structurée | Le comparator ne sait pas que la sous-requête a échoué |
| 2 | `_turn_exchanges` (ask_human) non injecté dans les sous-requêtes du même tour | La clarification humaine est perdue à chaque re-délégation |
| 3 | L'`ask_human` consomme une itération du step budget | Fenêtre de travail post-clarification réduite à 7 puis 6 steps |
| 4 | Pas de directive forçant les agents parents à inclure les clarifications dans un re-briefing | Le comparator re-briefait "calecon" sans tenir compte de ce que l'humain avait dit |
| 5 | Le synthesizer n'a pas le grant `conv_read_file` | Il hallucine le contenu des support_files depuis sa mémoire paramétrique — et produit une réponse inversée |