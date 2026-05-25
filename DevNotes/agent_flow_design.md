# Réflexion : flows d'enchainement agents — jean-michel

*Soliloque de conception — 24 mai 2026*

---

## Préambule — Ce qu'on a observé aujourd'hui

Le web-search-specialist a fait 12+ tours de recherche consécutifs en ciblant
le même repo GitHub (`awesome-free-research-apis`) sans jamais écrire le résultat,
parce qu'il n'avait pas le grant d'écriture workspace.

Mais même avec le grant, le problème aurait subsisté : **le specialist ne sait pas
quand s'arrêter**. Il cherche, trouve un lead, creuse, se heurte à une page vide,
reformule, repart. Personne ne l'interrompt, personne n'évalue la qualité de ce
qu'il a, personne ne dit "c'est assez, écris ton deliverable et remonte".

C'est le problème de fond que ce document cherche à adresser.

---

## 1. Les rôles actuels et leurs invariants

| Agent | Rôle | Écrit dans workspace | Lit workspace | Peut déléguer |
|-------|------|---------------------|---------------|---------------|
| jean-michel | routeur/orchestrateur | ✗ | ✅ plan.md | ✅ (tous) |
| planner | planificateur | ✅ plan.md uniquement | ✅ | ✗ |
| web-search-specialist | chercheur web | ✅ deliverables | ✗ | ✗ |
| wikipedia-specialist | chercheur encyclop. | ✅ deliverables | ✗ | ✗ |
| critical-thinker | analyste critique | ✅ evaluations | ✅ sources | ✗ |
| summarizer | synthèse/résumé | ✅ | ✅ | ✗ |
| document-builder | rédacteur final | ✅ output final | ✅ tous | ✗ |
| code-runner | exécution | ✅ résultats | ✅ scripts | ✗ |

**Contrainte fondamentale** : seul jean-michel délègue. Les specialists ne peuvent
pas se passer le relais entre eux. Tout remonte à jean-michel.

---

## 2. Flows par catégorie de requête

### 2.1 — `single_fact` : requête simple

Exemple : *"Quelle heure est-il à Tokyo ?"*

```
user
 └─▶ jean-michel (depth=0)
       ├─ [thought] single_fact, outil clock disponible
       ├─ clock(timezone='Asia/Tokyo')
       └─▶ return_to_user("Il est 14h32 à Tokyo.")
```

**Caractéristiques :**
- 0 délégation, 1 outil natif, 1 tour LLM
- Pas de workspace
- Pas de plan

---

### 2.2 — `medium_task` : recherche directe, 2-3 agents indépendants

Exemple : *"Explique-moi ce qu'est le modèle Actor-Critic en RL."*

```
user
 └─▶ jean-michel (depth=0)
       ├─ [thought] medium_task, plan mental dans le thought
       ├─ delegate_to(wikipedia-specialist, "Actor-Critic reinforcement learning")
       │     └─▶ wikipedia-specialist (depth=1)
       │           ├─ wikipedia_search(...)
       │           ├─ workspace_create_file("actor_critic.md", résumé structuré)
       │           └─▶ return_to_user("Voir actor_critic.md")
       ├─ [jean-michel reçoit l'answer + artifact]
       ├─ delegate_to(summarizer, briefing=contenu actor_critic.md, support=[artifact])
       │     └─▶ summarizer (depth=1)
       │           └─▶ return_to_user("Actor-Critic est...")
       └─▶ return_to_user(réponse finale en français)
```

**Caractéristiques :**
- 2 délégations séquentielles
- 1 fichier workspace intermédiaire
- Pas de planner (indépendant, pas de phases chaînées)

**Variante parallèle** : si la question a deux angles indépendants (encyclopédique + actualité récente), jean-michel délègue `wikipedia-specialist` ET `web-search-specialist` dans le même tour, puis synthesize les deux réponses.

```
jean-michel
 ├─ delegate_to(wikipedia-specialist, ...)  ─┐
 └─ delegate_to(web-search-specialist, ...)  ┘ (séquentiels dans l'orchestrateur,
                                                 mais logiquement parallèles)
 └─ synthesize → return_to_user
```

---

### 2.3 — `deep_research` : chaîne de phases dépendantes

Exemple : *"Dresse un tableau des sources de données programmatiques fiables par domaine."*

#### Flow nominal complet

```
user
 └─▶ jean-michel (depth=0)
       ├─ [thought] deep_research → planner en premier
       ├─ delegate_to(planner, "sources programmatiques fiables...")
       │     └─▶ planner (depth=1)
       │           ├─ workspace_create_file("plan.md", contenu du plan avec ## Status)
       │           └─▶ return_to_user("plan.md written.")
       │
       ├─ [jean-michel] workspace_view("plan.md")   ← lit le plan
       ├─ [jean-michel] trouve Step 1a ⬜ pending
       │
       ├─ delegate_to(wikipedia-specialist, step 1a)
       │     └─▶ wikipedia-specialist (depth=1)
       │           ├─ wikipedia_search("structured knowledge bases programmatic access")
       │           ├─ workspace_create_file("encyclopedic_sources.md", ...)
       │           └─▶ return_to_user("encyclopedic_sources.md written.")
       │
       ├─ [jean-michel] workspace_str_replace("plan.md", ⬜→✅ step 1a)
       ├─ [jean-michel] trouve Step 1b ⬜ pending
       │
       ├─ delegate_to(web-search-specialist, step 1b)
       │     └─▶ web-search-specialist (depth=1)
       │           ├─ web_search("open APIs science news geography programmatic")
       │           ├─ web_search("RSS feeds structured data domains")
       │           ├─ workspace_create_file("web_sources.md", ...)
       │           └─▶ return_to_user("web_sources.md written.")
       │
       ├─ [jean-michel] workspace_str_replace("plan.md", ⬜→✅ step 1b)
       ├─ [jean-michel] trouve Step 2 ⬜ pending (critical-thinker)
       │
       ├─ delegate_to(critical-thinker, step 2, support=[encyclopedic.md, web.md])
       │     └─▶ critical-thinker (depth=1)
       │           ├─ workspace_view("encyclopedic_sources.md")
       │           ├─ workspace_view("web_sources.md")
       │           ├─ [analyse, détecte lacunes, doublons, sources peu fiables]
       │           ├─ workspace_create_file("evaluation.md", ...)
       │           └─▶ return_to_user("evaluation.md written.")
       │
       ├─ [jean-michel] workspace_str_replace("plan.md", ⬜→✅ step 2)
       │
       │   ── BOUCLE RETOUR : le critical-thinker a détecté des lacunes ──
       │
       ├─ delegate_to(planner, "mise à jour plan : lacunes géographie identifiées")
       │     └─▶ planner (depth=1)
       │           ├─ workspace_view("plan.md")    ← lit plan existant
       │           ├─ workspace_str_replace("plan.md", ajoute Step 2b: web_search geo)
       │           └─▶ return_to_user("plan.md updated.")
       │
       ├─ [jean-michel] workspace_view("plan.md")  ← re-lit plan mis à jour
       ├─ [jean-michel] trouve Step 2b ⬜ pending (nouveau)
       │
       ├─ delegate_to(web-search-specialist, step 2b, cible=géographie)
       │     └─▶ web-search-specialist (depth=1)
       │           └─▶ ... → workspace_create_file("geo_sources.md") → return_to_user
       │
       ├─ [jean-michel] workspace_str_replace("plan.md", ⬜→✅ step 2b)
       ├─ [jean-michel] tous les steps ✅ sauf le Step 3 (document-builder)
       │
       ├─ delegate_to(document-builder, step 3, support=[evaluation.md, geo_sources.md, ...])
       │     └─▶ document-builder (depth=1)
       │           ├─ [lit les sources, construit le tableau markdown]
       │           ├─ workspace_create_file("sources_of_truth.md", tableau final)
       │           └─▶ return_to_user("sources_of_truth.md written.")
       │
       └─▶ return_to_user("Voici les sources — voir sources_of_truth.md")
```

---

## 3. Le problème du specialist qui ne sait pas s'arrêter

### Observation actuelle
Le web-search-specialist a un budget de N tours LLM (`MAX_STEPS_PER_REQUEST`).
Il n'a pas de critère d'arrêt qualitatif intégré à sa mission. Il cherche jusqu'à :
- Avoir assez de données pour écrire le fichier (cas idéal)
- Tourner en boucle sur des leads morts (cas observé aujourd'hui)
- Épuiser le budget

### La vraie question : qui décide que "c'est assez" ?

**Option A — le specialist décide** *(actuel)*  
Le briefing contient `expected: "un fichier sources_found.md"`. Le specialist cherche
jusqu'à se sentir satisfait et écrit. Problème : il n'a pas de critère explicite de
suffisance, et sans feedback, il s'emballe.

**Option B — jean-michel reçoit un rapport d'avancement** *(à implémenter)*  
Le specialist écrit un fichier partiel après chaque N recherches et remonte un signal
à jean-michel (`return_to_user("sources_found.md — partial, 8 sources, manque géographie.")`).
Jean-michel décide si c'est suffisant ou s'il re-délègue.

**Option C — budget de recherches explicite dans le briefing** *(simple, efficace)*  
Jean-michel écrit dans le briefing :
> "Run at most 5 web searches. If you cannot find more after that, write whatever you have."

Le specialist a un stop explicite. Évite les boucles infinies sans ajouter de complexité.

→ **Recommandation immédiate** : Option C dans `plan_before_complex_action` — ajouter
une règle sur la limite de recherches par délégation. Option B à étudier plus tard.

---

## 4. La boucle retour manquante

### Ce qu'il manque aujourd'hui

```
jean-michel → web-search-specialist → [12 recherches, aucun retour intermédiaire]
```

Il n'y a pas de feedback loop entre spécialiste et orchestrateur au sein d'une même
session de recherche. Un specialist qui tourne en rond ne peut pas signaler à
jean-michel "je suis bloqué, reformule la question".

### Flow avec boucle retour (cible)

```
jean-michel
 ├─ delegate_to(web-search-specialist, mission + budget_max=5)
 │     └─▶ web-search-specialist
 │           ├─ [recherches 1..5]
 │           ├─ workspace_create_file("sources_found.md", résultats partiels ou complets)
 │           └─▶ return_to_user("sources_found.md — 12 sources trouvées.
 │                               Domaines couverts: Science, News, Tech.
 │                               Lacune: Géographie (0 source).")
 │
 ├─ [jean-michel] évalue le rapport
 │     Si lacune significative → re-délègue avec focus géographie
 │     Sinon → passe à l'étape suivante
 │
 └─ (si re-délégation)
       delegate_to(web-search-specialist, "sources géo uniquement", budget=3)
```

**Ce que ça demande :**
- Rien côté code : le specialist peut déjà faire ça
- Côté paradigme : `return_to_user` doit inclure un résumé structuré (n sources,
  domaines couverts, lacunes) — pas juste `"sources_found.md written."`
- Côté jean-michel : après une délégation de recherche, lire le rapport et
  décider si on continue ou si on passe au step suivant

---

## 5. Scenarios avancés

### 5.1 — Recherche avec désaccord entre sources

```
web-search-specialist → "Python est plus rapide que Rust dans 3 benchmarks récents"
wikipedia-specialist  → "Rust surpasse Python en performance dans la plupart des cas"

jean-michel détecte divergence
 └─ delegate_to(critical-thinker, "évalue ces deux claims contradictoires")
       └─▶ critical-thinker
             ├─ workspace_view(web_search_result.md)
             ├─ workspace_view(wikipedia_result.md)
             ├─ [analyse de la qualité des sources, du contexte, des biais]
             ├─ workspace_create_file("verdict.md", analyse avec nuances)
             └─▶ return_to_user("Les deux claims sont vrais dans des contextes différents...")
```

### 5.2 — Recherche itérative avec comparaison

Exemple : *"Compare les frameworks Python pour le ML : PyTorch vs JAX vs TF"*

```
planner génère :
  Step 1a: web-search → PyTorch ecosystem (parallel)
  Step 1b: web-search → JAX ecosystem    (parallel)
  Step 1c: web-search → TF 2.x ecosystem (parallel)
  Step 2: critical-thinker → évalue chacun sur 5 critères
  Step 3: comparator-specialist → tableau comparatif
  Step 4: document-builder → rapport final

jean-michel exécute 1a, 1b, 1c séquentiellement (l'orchestrateur ne parallélise pas
encore — point d'évolution futur)
```

**Note :** l'instruction "parallel" dans le plan est actuellement un guide pour
l'ordre d'exécution (pas de dépendances), pas du vrai parallélisme. Jean-michel les
exécute l'un après l'autre. C'est correct fonctionnellement.

### 5.3 — Demande avec question sous-jacente non formulée

Exemple : *"C'est quoi les meilleurs modèles open-source en ce moment ?"*

```
jean-michel [thought]: ambiguïté — "meilleur" selon quoi ? taille/perf/license/usage ?

Option 1 : ask_human("Meilleur selon quel critère : performance, taille, license ?")
Option 2 : deep_research avec wikipedia + web-search sur plusieurs critères

→ Si la question est floue pour le planner aussi :
planner → ask_human("Contexte d'usage ? Fine-tuning, inférence locale, production ?")

Règle : ask_human avant de planifier vaut mieux que planifier dans le vide.
```

### 5.4 — Résultat qui invalide le plan

Exemple : le plan prévoit d'utiliser l'API OpenAlex pour les données académiques.
Le web-search-specialist découvre que l'API est obsolète (v2 supprimée en 2025).

```
web-search-specialist → return_to_user("openalexv2.md — ATTENTION: API v2 supprimée.
                                         Alternative: OpenAlex v3 (api.openalex.org).")

jean-michel → plan_before_complex_action: "Does this change enough to update the plan?"
  → Oui, le step "évaluation OpenAlex" est obsolète
  → delegate_to(planner, "update plan: OpenAlex v2 → v3, re-évaluer step 3")
    └─▶ planner
          ├─ workspace_view("plan.md")
          ├─ workspace_str_replace("plan.md", mise à jour step 3 + Revision log)
          └─▶ return_to_user("plan.md updated: step 3 now targets OpenAlex v3.")
```

---

## 6. Ce que le système fait bien aujourd'hui

- **Plan comme fil conducteur** : plan.md est le document partagé entre planner et jean-michel.
  L'état d'avancement est visible, persistant, et survivra à une reprise de session.
- **Isolation des specialists** : chaque specialist ne voit que sa mission.
  Pas de pollution de contexte entre agents.
- **Artifacts traçables** : chaque tool_call, thought, response est persisté.
  Débuggable post-mortem.
- **Planner comme second cerveau** : jean-michel délègue la pensée structurelle.
  Il n'a pas à inventer le plan — il l'exécute.

---

## 7. Ce qui manque / pistes d'évolution

| Lacune | Symptôme observé | Piste |
|--------|-----------------|-------|
| Budget de recherches explicite | web-search tourne 12+ fois sans conclure | Paradigme : "max N searches per delegation" |
| Rapport d'avancement structuré | return_to_user minimaliste | Paradigme : inclure n_sources, gaps, confidence |
| Évaluation intermédiaire par jean-michel | Passe à l'étape suivante sans valider la qualité | Paradigme : "assess if output is sufficient before marking ✅" |
| Résultats contradictoires → critical-thinker | Deux specialists peuvent remonter des infos opposées | Paradigme jean-michel : détecter divergences → critique |
| Critère de suffisance per-step dans le plan | Le plan dit "Deliverable: sources_found.md" mais pas ce que ça doit contenir | Format plan : ajouter "Acceptance criteria" par step |
| Parallelisme réel | 1a/1b exécutés séquentiellement | Évolution architecture (hors scope court terme) |

---

## 8. Proposition : "Acceptance criteria" dans le plan

Extension du format plan (Sprint D potentiel) :

```markdown
## Steps
| Step | Agent | Status | Deliverable | Acceptance criteria |
|------|-------|--------|-------------|---------------------|
| 1a | web-search-specialist | ⬜ pending | web_sources.md | ≥10 sources, ≥3 domaines couverts |
| 1b | wikipedia-specialist | ⬜ pending | encyclopedic.md | ≥3 structured KBs (Wikidata, DBpedia...) |
| 2  | critical-thinker | ⬜ pending | evaluation.md | chaque source évaluée sur fiabilité + format |
| 3  | document-builder | ⬜ pending | sources_of_truth.md | tableau markdown complet |
```

Jean-michel lit le critère, compare au livrable retourné, décide de valider ou de
re-déléguer avec un scope plus précis.

Le planner écrit ces critères au moment de la planification — il est le mieux placé
pour définir ce qui constitue un résultat satisfaisant pour chaque étape.

---

## 9. Checklist mentale pour jean-michel (deep_research)

```
1. delegate_to(planner) → attendre plan.md
2. workspace_view("plan.md") → lire le plan complet
3. Pour chaque step ⬜ pending dans l'ordre :
   a. delegate_to(agent, briefing avec acceptance criteria)
   b. Recevoir le résultat
   c. Évaluer : le deliverable répond-il aux acceptance criteria ?
      - Oui → workspace_str_replace(plan.md, ⬜→✅)
      - Non + gap mineur → re-déléguer avec scope précis
      - Non + gap majeur → delegate_to(planner, "update plan")
   d. Répéter
4. Tous les steps ✅ → delegate_to(synthesizer ou document-builder)
5. return_to_user avec la réponse finale
```

---

*Fin du soliloque.*
