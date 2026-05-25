Mise en perspective avant de plonger : tu vas déléguer ça à Claude 4.6 en mode agent. Donc le plan doit être **exécutable par un agent code-savvy mais sans le contexte de 30 tours de discussion qu'on a accumulé**. Il faut être autonome et autoporteur.

Ça veut dire :

- Référencer les artefacts existants (schema, orchestrator, etc.) avec leurs chemins.
- Donner des **critères d'acceptation testables** par phase, pas juste des checklists d'actions.
- Anticiper les pièges spécifiques (l'agent va découvrir le projet, pas y être déjà familier).
- Découper en tâches sub-agent suffisamment indépendantes pour parallélisation.

Quelques choix structurants que je prends, dis si tu veux modifier :

- **Migration SQL versionnée** (`migrate_005_workspace_and_sandbox.sql`) plutôt que d'éditer schema.sql directement. Plus traçable pour un agent qui itère.
- **Tests par phase**, exécutables en isolation, qui valident chaque garde-fou (path traversal, quota, etc.). Sans tests, l'agent ne peut pas confirmer qu'il a fini.
- **Pas d'agent code-runner créé dans ces phases** — on installe l'infrastructure, on ne crée pas encore l'agent qui en bénéficie. Tu le créeras toi-même quand tu voudras (via le HOWTO).