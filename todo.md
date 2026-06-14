# To Do

## Tool set

- jean-michel ne doit pas avoit les trucs de github, reserves aux agents codeurs
```{"type": "HookFired", "hook_name": "PreToolUse", "action": "deny", "reason": "Tool 'web-search-specialist' not granted to agent 'jean-michel'. Available: ['analyze_image', 'ask_human', 'clock', 'delegate_to', 'image_fetch', 'image_search', 'manage_memory', 'mcp__github__add_comment_to_pending_review', 'mcp__github__add_issue_comment', 'mcp__github__add_reply_to_pull_request_comment', 'mcp__github__assign_copilot_to_issue', 'mcp__github__create_branch', 'mcp__github__create_or_update_file', 'mcp__github__create_pull_request', 'mcp__github__create_pull_request_with_copilot', 'mcp__github__create_repository', 'mcp__github__delete_file', 'mcp__github__fork_repository', 'mcp__github__get_commit', 'mcp__github__get_copilot_job_status', 'mcp__github__get_file_contents', 'mcp__github__get_label', 'mcp__github__get_latest_release', 'mcp__github__get_me', 'mcp__github__get_release_by_tag', 'mcp__github__get_tag', 'mcp__github__get_team_members', 'mcp__github__get_teams', 'mcp__github__issue_read', 'mcp__github__issue_write', 'mcp__github__list_branches', 'mcp__github__list_commits', 'mcp__github__list_issue_fields', 'mcp__github__list_issue_types', 'mcp__github__list_issues', 'mcp__github__list_pull_requests', 'mcp__github__list_releases', 'mcp__vuetify__create_bug_report', 'mcp__vuetify__create_vuetify_bin', 'mcp__vuetify__create_vuetify_link', 'mcp__vuetify__create_vuetify_playground', 'mcp__vuetify__get_all_bins', 'mcp__vuetify__get_all_links', 'mcp__vuetify__get_all_playgrounds', 'mcp__vuetify__get_bin', 'mcp__vuetify__get_component_api_by_version', 'mcp__vuetify__get_directive_api_by_version', 'mcp__vuetify__get_exposed_exports', 'mcp__vuetify__get_feature_guide', 'mcp__vuetify__get_feature_guides', 'mcp__vuetify__get_frequently_asked_questions', 'mcp__vuetify__get_installation_guide', 'mcp__vuetify__get_release_notes_by_version', 'mcp__vuetify__get_upgrade_guide', 'mcp__vuetify__get_v4_breaking_changes', 'mcp__vuetify__get_vuetify0_component_guide', 'mcp__vuetify__get_vuetify0_component_list', 'mcp__vuetify__get_vuetify0_composable_guide', 'mcp__vuetify__get_vuetify0_composable_list', 'mcp__vuetify__get_vuetify0_exports_list', 'mcp__vuetify__get_vuetify0_installation_guide', 'mcp__vuetify__get_vuetify0_package_guide', 'mcp__vuetify__get_vuetify0_skill', 'mcp__vuetify__get_vuetify_api_by_version', 'mcp__vuetify__get_vuetify_one_installation_guide', 'mcp__vuetify__update_vuetify_bin', 'mcp__vuetify__update_vuetify_playground', 'report_back', 'todo_update', 'todo_write', 'web_search', 'workspace_view']", "utc": "2026-06-14T03:59:39.217175Z"}``` pareil pour le mcp vuetify, ils ne sont meme pas lances en plus (ca pose la question de quel mcp pour quel agent); et je pense egalement qu'il a confondu outil et delegation


## NULACHIER: `conversations/2026-06-14_03-58_56e288f0599f4915bf6346171ad9f84a`

- ca ecrit plus d'artefacts dans le workspace
- ca fait des plans en mode analyse maos ca repond pas grand chose; il a fallur une reponse pour que le trigger d'acceptaion de pla apparaisse sur la GUI.
- les appels d'outils sont bloques
- le plan est vide, mais on peut le modifier

- et les memories, on les voit plus alors ??

## c'est nuuuuul

- si une conversation est en court dans un onglet et qu'on check une autre convesation, quand on revient on voit pas que c'est en train de reflechir et on voit plus les chaines de pensees

## Un ouf malade

- plein de micro llm qui taffent sur le meme contexte a predire le prochain token et on en fait des triplets de precogs

## en cours

- VÉRIFIER EN LIVE le "plan mode" (livré, branche `ca_plan_pour_moi`, doc `docs/20260613_plan_mode/`) : sélecteur Plan/Edit (gauche du Envoyer, défaut Plan en code/analyse) → tour plan read-only (aucune mutation, todo_write forcé) → barre Approuver/Modifier → éditeur inline → exécution. Cf. étape 5 ci-dessous une fois rodé.

## Bugs

- les agents hallucinent sur des fichiers qui ne sont pas dans le workspace, probablement lie a une operation de compaction `conversations/2026-06-13_19-20_dfcafc75c589430f86fd9c2a82cf70ae`

## a faire

- on est definitivement en v2?? Checker si la v1 sert encore; sinon degager la v1 et les docs et consolider (orchestrateur, tests, ...)
- rafraichir le paradigm viewer/editor
- PLAN MODE — étape 5 (APRÈS rodage live côté code) : analyse écrite de généralisation aux autres modes (analyse/recherche : plan de recherche validé ? chat : marginal ? vocal : hors-sujet) + patterns d'orchestration transverses (vagues/dépendances/complexity-routing, cf. doc d'audit). Décider l'élargissement sur données réelles, pas spéculativement.
- ajouter un moyen de kill une operation en cours par un LLM (si on voit qu'il fait de la merde)



## a verifier

- plein de `PreToolUse: deny` 

- faire un bench sur le budget de token allouable, il me semble tout petit sachant que chaque LLM est independant avec un fresh start et que les LLM qu'on utilise on 100k tokens. De plus notre archi du serveur de dev (2x 32Go de VRAM) devrait nous mettre a l'aise. ex du `compaction (124 %)` de `conversations/2026-06-13_19-20_dfcafc75c589430f86fd9c2a82cf70ae`

- lister et analyser tous les paradigmes de tous les agents pour voir si c'est pas deconnanant; checker ce que dit le meta_analyst et si il sert encore a quelque chose
- pendant que ca tourne, le `graphify.sh` il sert encore ? si c'est juste un outil, on devrait le mettre dans un dossier `tools`; si c'est une base qui sert pour le jou ou on voudra le mettre dans un dockerfile, faut lui tourver une autre place. de memoire graphify c'etait pas ouf et on s'en servait pas
- le franglish : ```Looking back, the system prompt says: "Reply in the user's detected language." So the final answer should be in French. However, the report_back function's parameters are in English. The summary in report_back should be in English? Or in the user's language? The system prompt says to reply in the user's detected language, but the report_back is a structured object. The example in the tools shows the summary as a string, which should be in the user's language. So the summary should be in French, and the files_produced would be the markdown file created. Confidence is high, and no low confidence reason since the findings are well-documented.```: NORMALEMENT FIXED
