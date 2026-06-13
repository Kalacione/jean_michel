# To Do

## Bugs

## en cours

- verifier qu'on a bien dans l'env les valeurs par defaut des routeurs "jean-michel" et code parametrables et definis
- VÉRIFIER EN LIVE le fix ask_human/hallucination (shippé, changements de prompt/doctrine non testables hors run) : (1) une demande de choix déclenche bien la picker `ask_human(choices)` au lieu d'une réponse texte ; (2) le routeur cherche/délègue les faits au lieu de répondre de mémoire (paradigme `ground_every_fact` + paradigme 79 corrigé). Conv de réf : `2026-06-13_14-46_f002d17d`

## a faire

- rafraichir le paradigm viewer/editor
- mode "code plan" (user-facing, IMPORTANT) : le router construit un plan (todo) et le PRÉSENTE pour validation humaine avant exécution (façon Claude Code plan mode) — front + flux WS + nouvel état
