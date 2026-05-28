-- =============================================================================
-- migrate_103_search_quality.sql
-- =============================================================================
-- Suite à l'analyse comparative v1/v2 sur la requête "sources of truth" :
--   - v1 sources_discovery : 6 domaines couverts, propre
--   - v1 more_sources      : niches spécialisées (IMGT, ProteomeXchange)
--   - v2 actuel            : 3 domaines seulement, fort biais EU/FR, pas de
--                            météo, pas de tech, pas de niche scientifique
--
-- Diagnostic (events.jsonl conv 2026-05-28_13-15) :
--   1. web-search-specialist : 4 queries trop similaires, 3 angles distincts
--   2. wikipedia-specialist  : 2 search + 2 get_page, utilisé comme dictionnaire
--   3. document-builder      : passif, réduit la liste au lieu de l'élargir
--   4. jean-michel           : pas de validation de couverture avant doc-builder
--
-- Cette migration ajoute 4 paradigmes ciblés (P1-P4) et passe jean-michel sur
-- gemma4:26b (bonus) pour pousser la décomposition thématique côté router.
--
-- Idempotente : INSERT OR IGNORE + UPDATE conditionnel sur model_override.
-- =============================================================================

PRAGMA foreign_keys = ON;

BEGIN TRANSACTION;

-- =============================================================================
-- P1 — breadth_before_depth (web-search-specialist)
-- =============================================================================
-- Adresse : 4 queries reformulant les mêmes 3 angles (gov / academic / tech).
-- Force une décomposition explicite en 5-7 angles distincts avant de lancer
-- les recherches, avec terme racine différent par query.

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'web_search'),
    'breadth_before_depth',
    'Breadth before depth in exploratory search',
'- For an open exploration brief (inventory, listing, "find sources/tools/X"),
  enumerate 5–7 distinct thematic angles in your thought-channel BEFORE
  executing any search.
- Each angle must use a different root term. Reformulations of the same
  concept (e.g. "open data APIs" then "data APIs open") count as ONE angle.
- Diversify deliberately across at least 3 of: generic English term, niche
  technical term, named product/service, acronym, non-English term, adjacent
  domain. List the chosen angles before searching so the reasoning trace is
  auditable.
- Cover at minimum these domain families when relevant to the brief:
  encyclopedic, scientific (incl. niche : biomedical, materials, ecology),
  geospatial, news/current events, technical (code, web standards, OS),
  weather/environment, governmental/civic, specialised reference.
- One search per angle is the floor. Re-querying the same angle is only
  warranted when the first result was empty or off-topic.',
    'P1 du diagnostic 2026-05-28 : sans cette contrainte, l''agent produit
4 queries reformulant 3 angles seulement, ratant les niches.',
    0, 50, 1, datetime('now'), datetime('now')
);

-- =============================================================================
-- P2 — wikipedia_lateral_exploration (wikipedia-specialist)
-- =============================================================================
-- Adresse : Wikipedia utilisé comme dictionnaire (2 lookups sur termes connus
-- du brief, pas d'exploration latérale).

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'encyclopedic'),
    'wikipedia_lateral_exploration',
    'Wikipedia is a graph, not a dictionary',
'- A `wikipedia_search` with a broad term returns a candidate list. Treat
  each candidate as a potential entry point — explore at least 3 distinct
  articles before concluding, NOT three variations of the same subject.
- After reading an article, surface its outbound links (categories,
  "See also", references) as new exploration candidates. The goal is to
  walk the knowledge graph, not to confirm a known term.
- For inventory/listing briefs, mine the lateral articles for the SPECIFIC
  named entities the user could use (tools, databases, APIs, organisations)
  — not just the abstract concepts that frame them.
- 3 articles is the floor for an exploratory brief. A single get_page
  followed by report_back signals you treated Wikipedia as a dictionary
  lookup — re-open it.',
    'P2 du diagnostic 2026-05-28 : 2 search + 2 get_page = comportement
"dictionnaire" qui rate OpenStreetMap, PubMed, arXiv, GeoNames, etc.',
    0, 50, 1, datetime('now'), datetime('now')
);

-- =============================================================================
-- P3 — coverage_check (document-builder)
-- =============================================================================
-- Adresse : document-builder passif, fusionne sans critiquer la couverture,
-- a même réduit 16+ entrées → 14 dans le test.

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'document_authoring'),
    'coverage_check',
    'Coverage check before report_back on inventory deliverables',
'- Before producing an inventory/listing document (table of sources, tools,
  references, candidates…), run this check on the assembled material :
    (a) at least 5 distinct thematic categories represented,
    (b) at least 2 entries per category,
    (c) no single domain accounts for more than 50% of total entries.
- If ANY of (a)(b)(c) fails, do NOT silently produce a truncated table.
  Conclude with `report_back(confidence="low", low_confidence_reason="…")`
  naming the specific coverage gap (e.g. "73% government data, no
  weather/technical/encyclopedic entries — request a second sweep").
- Never reduce the input set on aesthetic grounds. If two upstream specialists
  produced 18 distinct entries, the final table must have ≥ 18 (deduped),
  not 14. Aesthetic curation is the caller''s decision, not yours.
- Cite the source workspace file for each entry (the upstream report you
  drew it from), so the router can audit provenance.',
    'P3 du diagnostic 2026-05-28 : document-builder a fusionné 2 reports
en perdant des entrées, sans signaler la pauvreté thématique.',
    0, 60, 1, datetime('now'), datetime('now')
);

-- =============================================================================
-- P4 — parallel_specialists_for_inventory (jean-michel)
-- =============================================================================
-- Adresse : jean-michel a délégué à 1 web-search-specialist + 1 wikipedia-
-- specialist en parallèle, mais ne s'est pas demandé si UN web-search-spec
-- pouvait couvrir TOUS les domaines (il ne peut pas, cf. P1).

INSERT OR IGNORE INTO paradigms (
    category_id, code, title, content, rationale,
    is_global, order_priority, active, created_at, modified_at
) VALUES (
    (SELECT id FROM categories WHERE code = 'planning'),
    'parallel_specialists_for_inventory',
    'Parallel specialists for open inventory briefs',
'- For an open inventory/exploration brief ("find me X sources of truth",
  "list relevant tools for Y", "candidates for Z"), do not assume a single
  specialist can cover the breadth. In your first round, spawn 2–3
  delegations in parallel, each with a **disjoint thematic scope**.
- When briefing each specialist, name the specific domain family they are
  responsible for (e.g. "you cover weather + niche scientific + cartographic;
  the other specialist handles encyclopedic + government + technical").
  Without explicit scoping, two parallel specialists tend to converge on
  the same low-hanging fruit.
- After the first round, before delegating to a synthesizer/document-builder,
  audit the aggregate coverage. If a domain family is empty or sparse, spawn
  a third targeted delegation rather than passing the gap downstream.
- This applies specifically to **broad exploration**. For a narrow lookup
  ("what is the capital of Estonia") a single specialist is correct.',
    'P4 du diagnostic 2026-05-28 : le router a délégué une seule fois à
web-search-specialist qui a couvert 3 domaines sur 7 souhaités.',
    0, 40, 1, datetime('now'), datetime('now')
);

-- =============================================================================
-- BINDINGS — relier les 4 paradigmes à leurs agents respectifs
-- =============================================================================

-- P1 → web-search-specialist
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'web-search-specialist'),
    (SELECT id FROM paradigms WHERE code = 'breadth_before_depth');

-- P2 → wikipedia-specialist
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'wikipedia-specialist'),
    (SELECT id FROM paradigms WHERE code = 'wikipedia_lateral_exploration');

-- P3 → document-builder
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'document-builder'),
    (SELECT id FROM paradigms WHERE code = 'coverage_check');

-- P4 → jean-michel (router)
INSERT OR IGNORE INTO agent_paradigms (agent_id, paradigm_id)
SELECT
    (SELECT id FROM agents WHERE code = 'jean-michel'),
    (SELECT id FROM paradigms WHERE code = 'parallel_specialists_for_inventory');

-- =============================================================================
-- MODE BINDINGS — actifs sur le mode "analyse" (le seul utilisé en v2 sur
-- les requêtes exploratoires). En "chat" ils restent disponibles aussi, le
-- router décide.
-- =============================================================================
-- Les paradigmes ci-dessus sont actifs par défaut sur tous les modes (pas
-- de filtrage paradigm_modes) tant qu'aucune ligne ne les restreint.
-- Cohérent avec le pattern des paradigmes ajoutés en migration 100.

-- =============================================================================
-- BONUS — pousser jean-michel sur gemma4:26b
-- =============================================================================
-- Justification : pour la décomposition thématique (P4) le router doit
-- générer 5-7 angles thématiques dans son thought-channel. gemma4:latest
-- (variante 9b) produit des angles génériques et redondants ; le 26b
-- propose une diversité plus large, indispensable au pattern d'inventaire.

UPDATE agents
SET model_override = 'gemma4:26b', modified_at = datetime('now')
WHERE code = 'jean-michel' AND (model_override IS NULL OR model_override <> 'gemma4:26b');

COMMIT;

-- =============================================================================
-- VALIDATION post-migration (à exécuter manuellement après application)
-- =============================================================================
-- SELECT code FROM paradigms WHERE code IN (
--   'breadth_before_depth',
--   'wikipedia_lateral_exploration',
--   'coverage_check',
--   'parallel_specialists_for_inventory'
-- ) ORDER BY code;   -- attendu : 4 lignes
--
-- SELECT a.code, p.code FROM agent_paradigms ap
-- JOIN agents a ON a.id = ap.agent_id
-- JOIN paradigms p ON p.id = ap.paradigm_id
-- WHERE p.code IN (
--   'breadth_before_depth', 'wikipedia_lateral_exploration',
--   'coverage_check', 'parallel_specialists_for_inventory'
-- ) ORDER BY p.code;
--
-- SELECT code, model_override FROM agents WHERE code = 'jean-michel';
--   -- attendu : jean-michel | gemma4:26b
