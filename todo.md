# To Do

## Bugs

## a verifier

- faire un bench sur le budget de token allouable, il me semble tout petit sachant que chaque LLM est independant avec un fresh start et que les LLM qu'on utilise on 100k tokens. De plus notre archi du serveur de dev (2x 32Go de VRAM) devrait nous mettre a l'aise.

## a faire

- on est definitivement en v2. Checker si la v1 sert encore; sinon degager la v1 et consolider (orchestrateur, tests, ...)
- rafraichir le paradigm viewer/editor
- mode "code plan" (user-facing, IMPORTANT) : le router construit un plan (todo) et le PRÉSENTE pour validation humaine avant exécution (façon Claude Code plan mode) — front + flux WS + nouvel état
