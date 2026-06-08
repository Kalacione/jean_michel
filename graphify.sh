#!/usr/bin/env bash
# graphify.sh — lance graphify sur CE repo avec nos paramètres locaux (Ollama, no cloud key).
#
# graphify n'est PAS dans le .venv 3.14 du projet : il est installé en outil isolé via uv
#   uv tool install "graphifyy[openai]" --with mcp --python 3.12
# (l'extra [openai] = backend Ollama via API OpenAI-compatible ; --with mcp = serveur MCP).
#
# Usage :
#   ./graphify.sh build              # graphe code (tree-sitter, déterministe) + naming communautés (Ollama local)
#   ./graphify.sh update             # graphe code seul (no LLM)
#   ./graphify.sh serve [PORT]       # sert graph.json en MCP HTTP (auth via $GRAPHIFY_API_KEY)
#   ./graphify.sh explain "run_main_loop()"   # passthrough : explain/path/affected/query/god_nodes/...
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

usage() {
  cat <<'USAGE'
graphify.sh — pilote graphify sur ce repo avec nos paramètres locaux (Ollama, sans clé cloud).

Usage : ./graphify.sh <commande> [args]

Commandes :
  build               graphe code (tree-sitter, déterministe) + naming des communautés (Ollama local)
  update              graphe code seul (no LLM) — rapide, à relancer après des changements
  serve [PORT]        sert graphify-out/graph.json en MCP HTTP sur 127.0.0.1:PORT (défaut 8080)
                      auth : $GRAPHIFY_API_KEY si défini, sinon un token éphémère est généré + affiché
  <autre> [...]       passthrough graphify : explain "X()" | path "A()" "B()" | affected "X()" | query "..."
  -h | --help         cette aide

Pré-requis (installation isolée, hors du .venv 3.14) :
  uv tool install "graphifyy[openai]" --with mcp --python 3.12

Env (optionnel) :
  OLLAMA_BASE_URL     défaut http://localhost:11434/v1   (sa présence => backend ollama)
  OLLAMA_MODEL        défaut qwen2.5-coder:7b            (override, ex. granite4.1:8b)
  GRAPHIFY_API_KEY    token d'auth du serveur MCP (sinon généré à la volée pour `serve`)
USAGE
}

# --- env local-first : piloter Ollama, jamais le cloud ---------------------
export PATH="$HOME/.local/bin:$PATH"
export OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434/v1}"  # présence => backend ollama
# Modèle de naming : défaut graphify = qwen2.5-coder:7b (installé). Override possible :
#   export OLLAMA_MODEL="granite4.1:8b"
# Neutralise toute clé cloud héritée du shell (sinon graphify la préfère à Ollama).
unset OPENAI_API_KEY ANTHROPIC_API_KEY GEMINI_API_KEY GOOGLE_API_KEY MOONSHOT_API_KEY DEEPSEEK_API_KEY 2>/dev/null || true

case "${1:-}" in
  -h|--help|help|"")
    usage
    exit 0
    ;;
esac

# Vérifie l'install seulement pour les commandes qui touchent graphify.
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
    port="${2:-8080}"
    [ -f graphify-out/graph.json ] || { echo "✖ pas de graphe : lance ./graphify.sh build avant" >&2; exit 1; }
    # Auth : réutilise $GRAPHIFY_API_KEY si défini, sinon génère un token éphémère.
    if [ -z "${GRAPHIFY_API_KEY:-}" ]; then
      GRAPHIFY_API_KEY="$(openssl rand -hex 16 2>/dev/null || python3 -c 'import secrets;print(secrets.token_hex(16))')"
      echo "ℹ GRAPHIFY_API_KEY non défini → token éphémère généré (exporte-le pour le figer)."
    fi
    cat <<EOF
▶ Serveur MCP graphify : http://127.0.0.1:${port}/mcp   (auth bearer)
  Token : ${GRAPHIFY_API_KEY}
  Pour jean-michel — mcp_servers.toml :
    [servers.graphify]
    url      = "http://127.0.0.1:${port}/mcp"
    category = "code"
    auth_env = "GRAPHIFY_MCP_TOKEN"     # export GRAPHIFY_MCP_TOKEN=${GRAPHIFY_API_KEY}
  (Ctrl-C pour arrêter)
EOF
    # serve n est pas une sous-commande CLI -> module python (a besoin du paquet mcp).
    # --no-project : ignore le pyproject 3.14 du repo (graphify tourne en 3.12 isole).
    exec uv run --no-project --with graphifyy --with mcp --python 3.12 -m graphify.serve \
      graphify-out/graph.json --transport http --host 127.0.0.1 --port "$port" --path /mcp \
      --api-key "$GRAPHIFY_API_KEY"
    ;;
  *)
    exec graphify "$@"   # passthrough : query / path / explain / affected / god_nodes / ...
    ;;
esac
