# La stack d'un agent autonome local (GGML → orchestration)

> Digest local de l'article SitePoint « *The Complete Stack for Local Autonomous Agents: From GGML to
> Orchestration* » (2026-02-23,
> [source](https://www.sitepoint.com/the-complete-stack-for-local-autonomous-agents--from-ggml-to-orchestration/)).
> Référence pour **situer nos choix** (jean-michel EST une telle stack, sur Ollama) et **repérer ce qu'on
> pourrait piocher**. Chaque couche : le digest fidèle + une note **chez nous**.

L'article découpe une stack d'agent 100% local en **5 couches** empilées (du moteur d'inférence en bas à
l'orchestration en haut). Argument du « tout local » : zéro coût API, souveraineté des données, pas de
rate-limit sur les boucles agentiques (des dizaines d'aller-retours LLM par tâche), fonctionne air-gapped.

## Couche 1 — Moteur d'inférence (GGML / GGUF / llama.cpp)
- **GGML** : lib de tenseurs (Georgi Gerganov) pour l'inférence CPU/GPU sur matériel grand public.
- **GGUF** : conteneur **auto-décrit** (archi + tokenizer + quant + poids) ; standard de facto sur Hugging Face.
- **llama.cpp** : runtime au-dessus de GGML (Metal / CUDA / Vulkan / AVX). ~40 tok/s sur un 7-8B Q4_K_M (M2
  Ultra) ; 8-15 tok/s CPU-only — utilisable en dev.
- Alternatives : **vLLM** (haut débit GPU, PagedAttention + continuous batching, multi-users), **Ollama**
  (wrappe llama.cpp, CLI + gestion de modèles, moins de contrôle fin), **ExLlamaV2** (GPTQ/GPU), **MLX** (Apple).
- **Chez nous** : on est sur **Ollama** (= llama.cpp + gestion de modèles). Compromis assumé de l'article :
  moins de contrôle fin (ctx / quant / **grammaire**) qu'un `llama-server` brut.

## Couche 2 — Sélection de modèle + quantization
- Modèles agentic (mi-2025) : Llama 3.1 8B/70B (tool tokens natifs), Mistral Nemo 12B, Qwen2.5-Coder (code),
  Phi-3, DeepSeek-V2-Lite (MoE).
- **Piège majeur** : mismatch de **chat template / tokens de tool-call** (Llama `<|python_tag|>`, Hermes
  `<tool_call>`, Mistral son schéma) → tool-call malformé si le template du modèle ≠ ce que l'orchestrateur formate.
- **Quantization** : famille k-quant block-wise (Q4_K_M, Q5_K_M, Q8_0) ; **imatrix** préserve les poids
  importants (calibration). Q4_K_M = sweet spot 16 Go (perplexité <1 % vs full). Q5_K_M+ pour le raisonnement.
  Q8_0 ≈ 2× la RAM. 70B → Q4_K_M souvent seul à tenir (+ le KV cache du contexte en plus des poids).
- Source : HF (bartowski, TheBloke) ; sinon `llama-quantize <in> <out> Q4_K_M` ; **vérifier les SHA256** + la licence.
- **Chez nous** : modèles pinnés en Q4-ish (cogito:32b ~19 Go, gemma4:26b…), `num_ctx` épinglé **par modèle**
  avec plafond 128k (= le conseil « ctx au minimum, gare au KV cache »). Tool-calling natif Ollama (capacités
  vérifiées via `ollama show`). Cf. [models_eval.md](models_eval.md).

## Couche 3 — API locale OpenAI-compatible
- **llama-server** expose `/v1/chat/completions` (drop-in OpenAI). **Décodage contraint par grammaire (GBNF)**
  via `--grammar-file` / `response_format` → force du JSON valide = tue **le mode d'échec n°1** (tool-call malformé ;
  la grammaire garantit la structure, pas la validité des valeurs → checks applicatifs en plus).
- Sécurité : `--host 0.0.0.0` **seulement** derrière un reverse-proxy authentifié (sinon API LLM ouverte).
- **Plusieurs modèles** : un petit (3-4B) pour parser/formatter les tool-calls, un gros (70B) pour le raisonnement ;
  **speculative decoding** (`--model-draft`) accélère (le petit propose, le gros vérifie).
- **Chez nous** : Ollama fournit l'API OpenAI-compat (client `ollama`). **Gap à noter** : on n'utilise **pas** de
  décodage GBNF (Ollama l'abstrait) → nos tool-calls malformés (cf. firefights) pourraient en bénéficier via un
  `llama-server`. Le pattern « petit modèle pour le tri + gros pour le raisonnement » = notre dispatcher
  granite + orchestrateur, mais **pas** de speculative decoding.

## Couche 4 — Mémoire, tools, function calling, sandbox
- **Mémoire = RAG** : vector store local — **ChromaDB** (zero-config), **LanceDB** (gros volumes, Rust),
  **Qdrant** (prod, Docker, réplication/filtrage), **FAISS** (le plus rapide en brut, pas de persistance/metadata
  intégrée). Embeddings locaux : `nomic-embed-text`, `all-MiniLM-L6-v2` (sentence-transformers).
- **Function calling** : tokens spéciaux du template + **GBNF** pour fiabiliser.
- **Sandbox** : exécuter du code généré SANS isolation = incident de sécurité. **Conteneurs Docker = minimum
  viable** ; `restrictedpython` insuffisant seul (contournable via traversée d'attributs `().__class__…`).
- **Chez nous** : mémoire = **DB scopée + FTS5/BM25** (PAS un vector store / RAG embeddings — approche
  *lexicale*, à connaître si on veut un jour du sémantique). Tool-calling natif Ollama. Sandbox =
  **`bash_sandbox` Docker `--network=none`** = pile le « conteneur = minimum viable » de l'article.

## Couche 5 — Orchestration (la boucle d'agent)
- Boucle : **Perceive → Plan → Act → Observe → Repeat** jusqu'à complétion/terminaison. Sans framework = des
  scripts de prompt-chaining fragiles ; un orchestrateur gère état, branchements conditionnels, reprise d'erreur.
- Frameworks : **LangGraph** (machine à états en graphe, accepte tout endpoint OpenAI-compat, branchements/backtrack
  complexes — recommandé local-first), **CrewAI** (multi-agents par rôles via LiteLLM, mais pas de re-routage
  dynamique), **AutoGen/AG2** (agents conversants, bon pour le code, API instable entre versions), **smolagents**
  (HF, léger, code-agent, pas de multi-agent/persistance natifs).
- **Chez nous** : on a notre **propre boucle Python déterministe + hooks** (PAS de framework tiers). On a
  reconstruit indépendamment ce que l'article prescrit — la boucle perceive/plan/act/observe ET surtout le
  hardening ci-dessous.

## Tuning & hardening (le plus actionnable)
- **Débit** : `--ctx-size` au strict minimum (attention quadratique) ; **Flash Attention** (`--flash-attn`,
  CUDA/Metal seulement) ; **KV cache quantisé** (`--cache-type-k q8_0`) pour des contextes plus longs à VRAM
  égale ; continuous batching pour les requêtes concurrentes.
- **Fiabilité** (un agent échoue autrement qu'un chatbot) :
  - retry + backoff exponentiel ; fallback sur un modèle plus petit / autre quant si la sortie est inparsable.
  - **valider les sorties** (Pydantic + grammaire) avant qu'elles ne se propagent dans la boucle.
  - **logger chaque étape** (prompt, sortie brute, tool-calls, résultats) dans **SQLite** = observabilité type
    LangSmith, en local.
  - **garde-fous** : itérations max, budget de tokens par tâche, breakpoints human-in-the-loop sur les actions à risque.
- **Chez nous** : déjà en place — garde-fou sans-progrès + `MAX_DEPTH`, budget de contexte partitionné +
  compaction 4 niveaux, journal `events.jsonl`, `ask_human` (humain-dans-la-boucle), `num_ctx` épinglé.
  **À piocher** : décodage **GBNF** pour les tool-calls (le mode d'échec n°1), Flash Attention / KV-quant (selon
  ce qu'Ollama expose), **validation Pydantic systématique des args de tool**.
