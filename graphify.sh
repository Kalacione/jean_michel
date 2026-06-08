#!/usr/bin/env bash
# graphify.sh — pilote graphify sur CE repo avec nos paramètres locaux (Ollama, no cloud key).
#
# graphify n'est PAS dans le .venv 3.14 du projet : il est installé en outil isolé via uv
#   uv tool install "graphifyy[openai]" --with mcp --python 3.12
# (extra [openai] = backend Ollama via API OpenAI-compatible ; --with mcp = serveur MCP).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# Défauts (alignés avec .env.example) — surchargeables par l'env ou par .env.
GRAPHIFY_MCP_PORT_DEFAULT=8765
GRAPHIFY_MCP_TOKEN_DEFAULT="graphify-local-dev"

# envval KEY DEFAULT : valeur depuis l'env, sinon depuis .env (KEY=value), sinon DEFAULT.
envval() {
  local key="$1" def="$2" v="${!1:-}"
  if [ -z "$v" ] && [ -f .env ]; then
    v="$(sed -n "s/^${key}=//p" .env | tail -1 | tr -d '"')"
  fi
  printf '%s' "${v:-$def}"
}

usage() {
  cat <<USAGE
graphify.sh — pilote graphify sur ce repo avec nos paramètres locaux (Ollama, sans clé cloud).

Usage : ./graphify.sh <commande> [args]

Commandes :
  build               graphe code (tree-sitter, déterministe) + naming des communautés (Ollama local)
  update              graphe code seul (no LLM) — rapide, à relancer après des changements
  serve [PORT]        sert graphify-out/graph.json en MCP HTTP sur 127.0.0.1 (défaut $GRAPHIFY_MCP_PORT_DEFAULT)
  <autre> [...]       passthrough graphify : explain "X()" | path "A()" "B()" | affected "X()" | query "..."
  -h | --help         cette aide

Pré-requis (installation isolée, hors du .venv 3.14) :
  uv tool install "graphifyy[openai]" --with mcp --python 3.12

Env (surchargeable ; valeurs lues dans .env sinon défauts) :
  GRAPHIFY_MCP_PORT   port du serveur MCP            (défaut $GRAPHIFY_MCP_PORT_DEFAULT)
  GRAPHIFY_MCP_TOKEN  token d'auth bearer du serveur (défaut "$GRAPHIFY_MCP_TOKEN_DEFAULT")
  OLLAMA_BASE_URL     défaut http://localhost:11434/v1  (sa présence => backend ollama)
  OLLAMA_MODEL        défaut qwen2.5-coder:7b           (override, ex. granite4.1:8b)
USAGE
}

# --- env local-first : piloter Ollama, jamais le cloud ---------------------
export PATH="$HOME/.local/bin:$PATH"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434/v1}"  # présence => backend ollama
# Neutralise toute clé cloud héritée du shell (sinon graphify la préfère à Ollama).
unset OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY GOOGLE_API_KEY MOONSHOT_API_KEY DEEPSEEK_API_KEY 2>/dev/null || true

case "${1:-}" in
  -h|--help|help|"") usage; exit 0 ;;
esac

if ! command -v graphify >/dev/null 2>&1; then
  echo "✖ graphify introuvable. Installe-le (isolé) :" >&2
  echo "    uv tool install \"graphifyy[openai]\" --with mcp --python 3.12" >&2
  exit 1
fi

cmd="$1"
case "$cmd" in
  build)
    graphify update .   # graphe code-only déterministe (tree-sitter, aucun LLM)
    graphify label .    # nomme les communautés via Ollama local (qwen2.5-coder:7b)
    echo "→ graphify-out/{graph.html,graph.json,GRAPH_REPORT.md}"
    ;;
  update)
    graphify update .
    ;;
  serve)
    port="${2:-$(envval GRAPHIFY_MCP_PORT "$GRAPHIFY_MCP_PORT_DEFAULT")}"
    token="$(envval GRAPHIFY_MCP_TOKEN "$GRAPHIFY_MCP_TOKEN_DEFAULT")"
    [ -f graphify-out/graph.json ] || { echo "✖ pas de graphe : lance ./graphify.sh build avant" >&2; exit 1; }
    echo "▶ MCP graphify → http://127.0.0.1:${port}/mcp   (auth bearer, token: ${token})"
    echo "  jean-michel y accède via [servers.graphify] dans mcp_servers.toml + GRAPHIFY_MCP_TOKEN dans .env"
    # serve n'est pas une sous-commande CLI -> module python (a besoin du paquet mcp).
    # --no-project : ignore le pyproject 3.14 du repo (graphify tourne en 3.12 isolé).
    exec uv run --no-project --with graphifyy --with mcp --python 3.12 -m graphify.serve \
      graphify-out/graph.json --transport http --host 127.0.0.1 --port "$port" --path /mcp \
      --api-key "$token"
    ;;
  *)
    exec graphify "$@"   # passthrough : query / path / explain / affected / god_nodes / ...
    ;;
esac
