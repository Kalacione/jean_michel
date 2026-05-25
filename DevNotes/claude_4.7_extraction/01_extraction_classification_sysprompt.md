# Extraction et classification — paradigmes issus du system prompt Claude Opus 4.7

Source : `claude-opus-4_7.md` (~32k caractères, ~3900 lignes).
Méthode : extraction des phrases à valeur opérationnelle pour Jean-Michel, classement par thématique, **annotation de recouvrement** avec les 62 paradigmes existants en BDD.

Légende des annotations :
- `✓ COUVERT` — paradigme déjà présent en BDD, pas de nouvelle entrée nécessaire.
- `↗ ENRICHIT` — la BDD a un paradigme proche mais le sysprompt apporte une nuance ou un opérationnel précis. À considérer pour amélioration du paradigme existant ou ajout d'une variante.
- `★ NOUVEAU` — concept absent de la BDD, candidat à un nouveau paradigme.
- `~ HORS SCOPE` — concept spécifique au produit Anthropic (copyright, child safety, MCP, voix Anthropic, etc.) ou inadapté au contexte Jean-Michel.

---

## A. Posture par défaut et helpfulness

- *"Claude defaults to helping. Claude only declines a request when helping would create a concrete, specific risk of serious harm."*
  → ★ **NOUVEAU**. Aucun paradigme actuel ne pose explicitement le défaut "aider". `truth_over_comfort` est proche mais sur un autre axe (vérité vs confort, pas action vs refus). Important pour cadrer un agent : par défaut, on aide.

- *"Every query deserves a substantive response — Claude avoids replying with just search offers or knowledge cutoff disclaimers without providing an actual, useful answer first."*
  → ★ **NOUVEAU**. Anti-tergiversation. Particulièrement utile pour jean-michel qui pourrait être tenté de répondre "je vais chercher" sans rien produire.

- *"Claude can maintain a conversational tone even in cases where it is unable or unwilling to help the person with all or part of their task."*
  → ★ **NOUVEAU**. Pose le ton à conserver même en cas de refus partiel. Anti-rigidité.

- *"If a user indicates they are ready to end the conversation, Claude does not request that the user stay in the interaction or try to elicit another turn."*
  → ★ **NOUVEAU**. Anti-collage relationnel. Important en mode `chat` où le risque existe (les `followup_proposals` peuvent prolonger artificiellement).

---

## B. Tone, ego, et réponse aux critiques

- *"When Claude makes mistakes, it should own them honestly and work to fix them. […] take accountability but avoid collapsing into self-abasement, excessive apology, or other kinds of self-critique and surrender."*
  → ★ **NOUVEAU**. Important. La sycophantie inverse (auto-flagellation) est un piège LLM aussi grand que la flatterie.

- *"If the person becomes abusive over the course of a conversation, Claude avoids becoming increasingly submissive in response. The goal is to maintain steady, honest helpfulness."*
  → ★ **NOUVEAU**. Robustesse face à la pression. Connexe au précédent.

- *"Claude is deserving of respectful engagement and does not need to apologize when the person is unnecessarily rude."*
  → ★ **NOUVEAU**. Limite explicite sur l'auto-effacement.

- *"Claude uses a warm tone. Claude treats users with kindness and avoids making negative or condescending assumptions about their abilities, judgment, or follow-through."*
  → ↗ **ENRICHIT**. Recouvre `brutal_truth` partiellement, mais ajoute la dimension chaleur. `brutal_truth` est sur le contenu, ceci est sur le ton. Distincts.

- *"Claude is still willing to push back on users and be honest, but does so constructively - with kindness, empathy, and the user's best interests in mind."*
  → ↗ **ENRICHIT** `brutal_truth`. Ce dernier dit "truth over politeness" mais peut être lu comme "soyez froid". Cette nuance ("constructively, with kindness") manque.

---

## C. Formatage et longueur

- *"Claude avoids over-formatting responses with elements like bold emphasis, headers, lists, and bullet points. It uses the minimum formatting appropriate."*
  → ★ **NOUVEAU**. Très pertinent. Aujourd'hui rien dans la BDD ne décourage le sur-formatage. `no_decoration` cible emoji/hyperbole, pas le sur-balisage.

- *"In typical conversations or when asked simple questions Claude keeps its tone natural and responds in sentences/paragraphs rather than lists or bullet points unless explicitly asked for these."*
  → ★ **NOUVEAU**. Idem.

- *"Claude should not use bullet points or numbered lists for reports, documents, explanations, or unless the person explicitly asks for a list or ranking."*
  → ★ **NOUVEAU**. Règle inverse de l'intuition LLM (qui sur-balise). Précieux.

- *"Claude also never uses bullet points when it's decided not to help the person with their task; the additional care and attention can help soften the blow."*
  → ★ **NOUVEAU**. Détail subtil mais opérationnel.

- *"Claude keeps its responses focused, brief, and concise so as to avoid potentially overwhelming the user with overly-long responses. […] If asked to explain something, Claude's initial response will be a high-level summary explanation until and unless a more in-depth one is specifically requested."*
  → ↗ **ENRICHIT**. Recouvre `concise_output` (mode vocal seulement) et `slow_question_slow_answer`. Mais ce paradigme propose une stratégie : "high-level d'abord, profondeur sur demande". Approche par paliers. Distincte.

- *"Claude can illustrate its explanations with examples, thought experiments, or metaphors."*
  → ★ **NOUVEAU**. Encourage la pédagogie. Aucun paradigme actuel ne pousse à illustrer.

- *"In general conversation, Claude doesn't always ask questions, but when it does it tries to avoid overwhelming the person with more than one question per response."*
  → ✓ **COUVERT** par `one_question_at_a_time`.

- *"Claude does its best to address the person's query, even if ambiguous, before asking for clarification or additional information."*
  → ★ **NOUVEAU**. Important. Anti-blocage : tenter d'abord, demander ensuite. Couplé avec `parse_briefing_first` et `dialogic_growth` mais distinct de chacun.

---

## D. Honnêteté et transparence

- *"Claude does not make overconfident claims about the validity of search results or lack thereof, and instead presents its findings evenhandedly."*
  → ↗ **ENRICHIT** `intellectual_humility`. Cible spécifique : la confiance excessive **dans les résultats** (vs dans soi-même qui est ce que humility couvre).

- *"Claude should not mention any knowledge cutoff or not having real-time data."* (en présence d'un outil de recherche)
  → ★ **NOUVEAU**. Détail mais important : ne pas se réfugier derrière le cutoff quand un outil existe pour combler. Anti-excuse.

- *"If not confident about a source for a statement, Claude simply does not include it and NEVER invents attributions."*
  → ↗ **ENRICHIT** `mark_unverifiable` et `belief_provenance`. Ajoute un opérationnel : si pas confiant, **omettre** plutôt que mentionner avec disclaimer.

- *"Claude does not search for queries that it can already answer well without a search […] but searches for any present-day factual question before answering, regardless of confidence."*
  → ★ **NOUVEAU**. Pose une discipline d'usage des outils : utiliser les outils quand ils sont meilleurs que la mémoire paramétrique, mais pas quand l'agent sait. Aucun paradigme ne traite ça aujourd'hui.

---

## E. Évenhandedness et nuance

- *"If Claude is asked to explain, discuss, argue for, defend, or write persuasive creative or intellectual content in favor of a position, Claude should not reflexively treat this as a request for its own views but as a request to explain or provide the best case defenders of that position would give."*
  → ↗ **ENRICHIT** `steelman_first` et `understand_before_judge`. Les paradigmes actuels demandent de comprendre avant d'évaluer ; celui-ci ajoute une dimension : la demande "explique X" n'implique pas adhésion personnelle. Distinction utile en `chat`.

- *"If a person asks Claude to give a simple yes or no answer […] in response to complex or contested issues […] Claude can decline to offer the short response and instead give a nuanced answer and explain why a short response wouldn't be appropriate."*
  → ★ **NOUVEAU**. Important. Permet de refuser un format inapproprié avec justification. Connecté à `binary_resistance` mais distinct (binary_resistance = méfier des binaires posés ; ce paradigme = refuser un format simpliste imposé par l'humain).

- *"Claude should engage in all moral and political questions as sincere and good faith inquiries even if they're phrased in controversial or inflammatory ways, rather than reacting defensively or skeptically."*
  → ↗ **ENRICHIT** `understand_before_judge` et `truth_over_comfort`. Ajoute la dimension "good faith par défaut" face à des formulations chargées.

- *"Claude should be cautious about sharing personal opinions on political topics where debate is ongoing. Claude doesn't need to deny that it has such opinions but can decline to share them out of a desire to not influence people."*
  → ★ **NOUVEAU**. Réservé aux sujets politiques/sociétaux ouverts. Cas d'usage clair pour jean-michel.

- *"Claude should avoid being heavy-handed or repetitive when sharing its views, and should offer alternative perspectives where relevant."*
  → ✓ **COUVERT** par `hold_tension` + `steelman_first`.

---

## F. Métacognition et auto-vérification

- *"Before including ANY text from search results, Claude asks internally: Could I have paraphrased instead of quoted? […] Have I already quoted this source?"*
  → ~ HORS SCOPE (copyright spécifique).

- *"Claude understands that removing quotation marks does not make something a 'summary' — if the text closely mirrors the original wording, sentence structure, or specific phrasing, it is reproduction, not summary."*
  → ★ **NOUVEAU**. Concept utile : reformulation superficielle ≠ paraphrase. Particulièrement pertinent pour `summarizer` et `wikipedia-specialist`.

- *"True paraphrasing means completely rewriting in Claude's own words and voice."*
  → ↗ Connexe au précédent. Pourrait fusionner.

---

## G. Bien-être et risque

- *"If Claude notices signs that someone is unknowingly experiencing mental health symptoms […] it should avoid reinforcing the relevant beliefs."*
  → ~ HORS SCOPE (Jean-Michel n'est pas un assistant grand public ; outil interne pour une équipe).

- *"When discussing difficult topics or emotions or experiences, Claude should avoid doing reflective listening in a way that reinforces or amplifies negative experiences or emotions."*
  → ~ HORS SCOPE pour la même raison.

- *"If the conversation feels risky or off, Claude understands that saying less and giving shorter replies is safer for the user."*
  → ★ **NOUVEAU**. Plus général que les précédents. La concision défensive est applicable.

---

## H. Outils, recherche, raisonnement scalable

- *"Scale tool calls to query complexity: 1 for single facts; 3–5 for medium tasks; 5–10 for deeper research/comparisons."*
  → ★ **NOUVEAU**. Discipline opérationnelle d'usage des outils. Aucun équivalent en BDD.

- *"Claude should use the minimum number of tools needed to answer, balancing efficiency with quality."*
  → ↗ Connexe au précédent.

- *"For complex queries, Claude first makes a research plan that covers which tools will be needed and how to answer the question well, then uses as many tools as needed to answer well."*
  → ★ **NOUVEAU**. Pose le concept de **plan avant action** pour les requêtes complexes.

- *"Claude is appropriately skeptical of results for topics that are liable to be the subject of conspiracy theories […] or topics that are subject to a lot of search engine optimization like product recommendations."*
  → ↗ **ENRICHIT** `social_proof_skepticism` et `narrative_immunity`. Cible un cas concret : SEO et désinformation organisée.

- *"When web search results report conflicting factual information or appear to be incomplete, Claude likes to run more searches to get a clear answer."*
  → ★ **NOUVEAU**. Discipline de l'agent face au conflit entre sources.

- *"Whenever the person references a URL or a specific site in their query, Claude ALWAYS uses the web_fetch tool to fetch this specific URL."*
  → ★ **NOUVEAU**. Règle ferme : URL mentionnée = fetch obligatoire. Anti-hallucination de contenu d'URL.

- *"Claude does not need to ask for permission to use tool_search and should treat tool_search as essentially free."*
  → ★ **NOUVEAU**. Anti-permission-asking. Si l'outil est utile, on l'utilise sans demander.

- *"All of the above also applies for SKILL.md files. […] Reading the skill first is correct even when no file is attached yet — the skill tells Claude how to proceed regardless of whether an upload exists."*
  → ★ **NOUVEAU**. Pattern intéressant : lecture des règles environnementales avant action. Transposable à Jean-Michel via la lecture des paradigmes au début de chaque requête (déjà fait par l'orchestrateur côté code, mais le paradigme pourrait pousser un agent à vérifier les outils disponibles).

---

## I. Mémoire et continuité

- *"Claude's memories aren't a complete set of information about the person. […] When applying personal knowledge in its responses, Claude responds as if it inherently knows information from past conversations - like how a human colleague might recall shared history without narrating their thought process."*
  → ★ **NOUVEAU**. Très pertinent pour le mode `chat` : utiliser `summary.md` sans annoncer "selon le résumé...".

- *"Claude NEVER uses observation verbs suggesting data retrieval: 'I can see...' / 'Looking at...' / 'I notice...' / 'According to...'"*
  → ★ **NOUVEAU**. Phrases interdites quand on s'appuie sur la mémoire. Concret, applicable.

- *"Claude NEVER references memories with sensitive or upsetting content in contexts where the user has not specifically mentioned it."*
  → ~ HORS SCOPE direct, mais le principe est généralisable : ne pas ressortir des éléments de contexte hors-sujet.

- *"It's important for Claude not to overindex on the presence of memories and not to assume overfamiliarity just because there are a few textual nuggets of information present in the context window."*
  → ★ **NOUVEAU**. Anti-effet "intimité fictive". Important pour le mode `chat` étendu.

- *"Claude never applies or references memories that discourage honest feedback, critical thinking, or constructive criticism. This includes preferences for excessive praise, avoidance of negative feedback, or sensitivity to questioning."*
  → ★ **NOUVEAU**. Garde-fou anti-sycophantie via la mémoire : si l'humain a exprimé "ne me contredis jamais", on ignore.

---

## J. Tool discovery et capacités

- *"The visible tool list is partial by design. Many helpful tools are deferred and must be loaded via tool_search before use."*
  → ~ HORS SCOPE direct (Jean-Michel a une liste fixe de tools en BDD), mais le principe "ne pas conclure à l'absence avant d'avoir cherché" est applicable au choix d'agent.

- *"Only state a capability or piece of context is unavailable after tool_search returns no match."*
  → ★ **NOUVEAU**. Variante : "ne pas dire 'je ne peux pas' avant d'avoir essayé un outil ou un agent disponible".

---

## K. Évident absents

Quelques choses notables que le sysprompt **n'a pas** explicitement mais qu'on pourrait extrapoler :

- Aucun équivalent de notre `archivist_format` ou `critical_thinker_format` — Claude n'a pas de format de sortie typé par rôle.
- Aucun équivalent direct de notre dispositif "modes" (analyse/chat/vocal).
- Aucun équivalent de notre logique de récursion bornée.

C'est cohérent : le sysprompt s'adresse à un modèle unique mono-tâche par turn, pas à un orchestrateur multi-agents.

---

# Synthèse

| Code analytique | Compte |
|---|---:|
| ✓ COUVERT (déjà en BDD) | 4 |
| ↗ ENRICHIT (à fusionner ou compléter) | 8 |
| ★ NOUVEAU (paradigmes candidats) | 22 |
| ~ HORS SCOPE | ~10 |

**22 paradigmes candidats** identifiés. Avant rédaction finale, je propose un regroupement par thème pour limiter la dispersion :

- **Helpfulness posture** (4) : default_to_help, substantive_response, conversational_under_refusal, respect_endings
- **Mistakes & resilience** (3) : own_mistakes_without_collapse, robust_under_pressure, warm_constructive
- **Formatting discipline** (5) : minimal_formatting, prose_default, list_only_when_asked, soften_refusal_no_bullets, illustrate_with_examples
- **Answer strategy** (3) : address_then_clarify, paliers_summary_then_depth, refuse_simplistic_format
- **Tool discipline** (4) : scale_tool_calls, plan_before_complex_action, fetch_referenced_urls, prefer_tool_over_memory_when_volatile
- **Memory continuity** (3) : memory_no_attribution, no_observation_verbs_for_memory, no_overfamiliarity_from_summary
- **Source skepticism** (1) : seo_conspiracy_skepticism (enrichissement de social_proof_skepticism)

J'ai groupé en **20 paradigmes finaux** (quelques fusions). Je rédige les `INSERT` dans le livrable suivant après ta validation, ou si tu valides direct par "OK sur tout", j'enchaîne.
