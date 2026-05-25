-- Migration 055: anti-hallucination paradigm for source/API listing tasks
--
-- Cause: qwen3:14b et gemma4:latest produisent un mode d'hallucination
-- spécifique sur les tâches de listing de sources/APIs : ils connaissent une
-- vraie marque (Britannica, BBC, NewsData.io, ScholarAPI) et inventent
-- l'existence d'une API publique stable et gratuite. Le paradigme générique
-- "never invent URLs" ne couvre pas ce cas — le modèle peut citer un URL
-- réel (la home de la marque) tout en inventant l'API derrière.
--
-- gemma4:latest pousse le défaut plus loin : il liste des MÉTA-CATÉGORIES
-- ("Web of Science / Scientific Databases", "Specialized Scientific APIs")
-- au lieu de sources concrètes nommées.
--
-- Cette migration ajoute un paradigme ciblé avec :
--   1. Critères d'admission positifs (ce qu'une source DOIT avoir)
--   2. Critères de rejet (marques connues sans API publique)
--   3. Interdiction explicite des méta-catégories
--   4. Défaut "en cas de doute, exclure"

BEGIN;

INSERT INTO paradigms (category_id, code, title, content, rationale, is_global, order_priority, active, created_at, modified_at)
VALUES (
  16,
  'source_admission_criteria',
  'Source admission criteria for listing tasks',
  '- When listing sources, APIs, datasets, or knowledge providers, every entry MUST be a concrete, named source — not a category. Forbidden: "Scientific Databases", "Public APIs", "Specialized X APIs", "Open Data Portals" used as the entry name itself. Use only specific products: "PubMed", "data.gov", "OpenAlex", "arXiv".
- For each source listed, you MUST be able to point to a tool_response (web_search or wikipedia output) from THIS research session where the source name appeared. If you cannot, omit it.
- Knowing a brand exists is NOT sufficient evidence that it offers a public, documented, accessible API. Common traps: news brands (BBC, Reuters, CNN — most retired their public APIs), encyclopedic brands behind paywalls (Britannica, Web of Science), generic-sounding names (ScholarAPI, NewsData, AcademicAPI — verify they are real products).
- In case of doubt about whether a source meets the brief (public, accessible, documented), EXCLUDE it. A shorter accurate list always beats a longer list padded with unverifiable entries.
- For each entry, the "value added" column must describe what is DIFFERENT or UNIQUE about this source (the angle it covers, its data format, its license). Generic descriptions that paraphrase the brand name ("comprehensive news", "structured data") are forbidden — they signal an entry the model cannot actually justify.',
  'Targets the brand-vs-API hallucination mode observed in qwen3:14b and the meta-category failure mode observed in gemma4:latest. Source: comparison of 3 model outputs on the same "sources of truth" research prompt, 2026-05-24.',
  0,
  55,
  1,
  strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
);

-- Attach to research-producing agents
INSERT INTO agent_paradigms (agent_id, paradigm_id)
SELECT a.id, p.id
FROM agents a, paradigms p
WHERE p.code = 'source_admission_criteria'
  AND a.code IN ('web-search-specialist', 'wikipedia-specialist', 'document-builder')
ON CONFLICT DO NOTHING;

COMMIT;
