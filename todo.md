# To Do

## Bugs

- n'importe quoi le ask_human; aucun composant dqns l'UI pour choisir la reponse; en plus la reponse est completement foireuse (je connais les animaniacs et il a tout melange, Pinky va de paire avec the Brain, aucun rapport avec les freres Warner et la soeur Warner) `conversations/2026-06-13_14-46_f002d17dac004bd0966da0fbd2d28d67`; Par principe, le LLM ne doit pas s'appuyer sur son illusion de connaissance, il doit chercher systematiquement sur internet, sauf si c'est un appel trivial a un outil (comme clock ou weather par exemple)

## en cours

- verifier qu'on a bien dans l'env les valeurs par defaut des routeurs "jean-michel" et code parametrables et definis

## a faire

- rafraichir le paradigm viewer/editor
- mode "code plan" (user-facing, IMPORTANT) : le router construit un plan (todo) et le PRÉSENTE pour validation humaine avant exécution (façon Claude Code plan mode) — front + flux WS + nouvel état
