#!/usr/bin/env bash
# seed-defaults.sh — Seed default tools, skills, workflows and agents.
#
# Idempotent: HTTP 409 = already exists → skip.
# Requires registry-api to be reachable at REGISTRY_URL (default: http://localhost:8000).
# Called by deploy-cpe2e.sh as step 8.
set -euo pipefail

REGISTRY_URL="${REGISTRY_URL:-http://localhost:8000}"
TEAM="platform"

log() { echo "  $*"; }
ok()  { echo "  [OK] $*"; }
skip(){ echo "  [--] $*"; }
warn(){ echo "  [!!] $*"; }

# ---------------------------------------------------------------------------
# Helper: POST with 409-safe response; echoes record JSON (created or existing).
# On 409 (already exists), queries the list endpoint to find the existing record
# by name so callers always get the ID for linking.
# ---------------------------------------------------------------------------
post_idempotent() {
  local path="$1"
  local body="$2"
  local label="$3"

  local http_code resp
  resp=$(curl -s -w "\n%{http_code}" -X POST "${REGISTRY_URL}${path}" \
    -H "Content-Type: application/json" \
    -d "$body")
  http_code=$(echo "$resp" | tail -1)
  body_out=$(echo "$resp" | sed '$d')

  if [ "$http_code" = "201" ]; then
    ok "$label"
    echo "$body_out"
  elif [ "$http_code" = "409" ]; then
    skip "$label (already exists)"
    local existing
    existing=$(curl -s "${REGISTRY_URL}${path}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
items = data.get('items', data) if isinstance(data, dict) else data
for item in items:
    if item.get('name') == '$label':
        print(json.dumps(item))
        break
" 2>/dev/null || echo "")
    echo "$existing"
  else
    warn "$label — unexpected HTTP $http_code: $body_out"
    echo ""
  fi
}

echo ""
echo "==> Seeding default resources into ${REGISTRY_URL} ..."
echo ""

# ===========================================================================
# TOOLS
# ===========================================================================
echo "--- Tools ---"

# --- web-search (HTTP, Serper.dev) ---
WEB_SEARCH=$(post_idempotent "/api/v1/tools/" \
  "{\"name\":\"web_search\",\"display_name\":\"Web Search\",\"description\":\"Search the web using Serper.dev. Pass X-API-KEY header via agent auth config.\",\"type\":\"http\",\"risk_level\":\"high\",\"owner_team\":\"${TEAM}\",\"http_method\":\"POST\",\"http_url\":\"https://google.serper.dev/search\",\"http_headers\":{\"Content-Type\":\"application/json\",\"X-API-KEY\":\"{{serper_api_key}}\"},\"http_body_template\":\"{\\\"q\\\":\\\"{{query}}\\\",\\\"num\\\":5}\"}" \
  "web_search")
WEB_SEARCH_ID=$(echo "$WEB_SEARCH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

# --- weather-lookup (HTTP, Open-Meteo — free, no key) ---
WEATHER=$(post_idempotent "/api/v1/tools/" \
  "{\"name\":\"weather_lookup\",\"display_name\":\"Weather Lookup\",\"description\":\"Get current weather for a location using Open-Meteo (free, no API key required).\",\"type\":\"http\",\"risk_level\":\"low\",\"owner_team\":\"${TEAM}\",\"http_method\":\"GET\",\"http_url\":\"https://api.open-meteo.com/v1/forecast?latitude={{latitude}}&longitude={{longitude}}&current_weather=true\"}" \
  "weather_lookup")
WEATHER_ID=$(echo "$WEATHER" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

# --- ip-geolocation (HTTP, ip-api.com — free) ---
GEO=$(post_idempotent "/api/v1/tools/" \
  "{\"name\":\"ip_geolocation\",\"display_name\":\"IP Geolocation\",\"description\":\"Geolocate an IP address using ip-api.com (free, no key required).\",\"type\":\"http\",\"risk_level\":\"low\",\"owner_team\":\"${TEAM}\",\"http_method\":\"GET\",\"http_url\":\"http://ip-api.com/json/{{ip}}\"}" \
  "ip_geolocation")
GEO_ID=$(echo "$GEO" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

# --- slack-notify (HTTP, Slack webhook) ---
SLACK=$(post_idempotent "/api/v1/tools/" \
  "{\"name\":\"slack_notify\",\"display_name\":\"Slack Notify\",\"description\":\"Send a message to a Slack channel via webhook. Pass webhook_url and message at call time.\",\"type\":\"http\",\"risk_level\":\"medium\",\"owner_team\":\"${TEAM}\",\"http_method\":\"POST\",\"http_url\":\"{{webhook_url}}\",\"http_headers\":{\"Content-Type\":\"application/json\"},\"http_body_template\":\"{\\\"text\\\":\\\"{{message}}\\\"}\"}" \
  "slack_notify")
SLACK_ID=$(echo "$SLACK" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

# --- http-echo (HTTP, in-cluster registry-api /echo — for testing) ---
ECHO=$(post_idempotent "/api/v1/tools/" \
  "{\"name\":\"http_echo\",\"display_name\":\"HTTP Echo\",\"description\":\"Echo HTTP requests back via the in-cluster registry-api /echo endpoint (no external dependency). Useful for testing tool integration.\",\"type\":\"http\",\"risk_level\":\"low\",\"owner_team\":\"${TEAM}\",\"http_method\":\"GET\",\"http_url\":\"http://agentshield-registry-api.agentshield-platform.svc.cluster.local:8000/echo/{{path}}\"}" \
  "http_echo")
ECHO_ID=$(echo "$ECHO" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

# --- calculator (Python, AST-safe arithmetic) ---
CALC_CODE='def run_tool(args: dict) -> str:
    """Evaluate a math expression. args: {"expression": "(5+3)*2"}"""
    import ast, operator
    ops = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod,
    }
    def _eval(node):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            return ops[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -_eval(node.operand)
        raise ValueError(f"Unsupported operation: {ast.dump(node)}")
    tree = ast.parse(args["expression"], mode="eval")
    result = _eval(tree.body)
    return str(result)'

CALC_CODE_JSON=$(python3 -c "import json; print(json.dumps('''${CALC_CODE}'''))")

CALC=$(post_idempotent "/api/v1/tools/" \
  "{\"name\":\"calculator\",\"display_name\":\"Calculator\",\"description\":\"Evaluate math expressions safely using Python AST. No eval(), no imports.\",\"type\":\"python\",\"risk_level\":\"low\",\"owner_team\":\"${TEAM}\",\"python_code\":${CALC_CODE_JSON}}" \
  "calculator")
CALC_ID=$(echo "$CALC" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

# --- knowledge_search (HTTP, cluster-internal RAG backend — POC-4) ---
# The team + agent name in the headers are substituted server-side by HttpToolExecutor
# from the agent pod's env (AGENTSHIELD_AGENT_TEAM / AGENT_NAME) — unspoofable by the
# model. {{query}} is the ONLY model-controlled input. kb_id is NEVER in the tool: the
# internal /knowledge/search endpoint resolves it from agent_knowledge_bindings by
# (agent_name, team), then PgVectorStore re-enforces (team, kb_id) as required
# predicates (S5). Body is built with json.dumps so the nested input_schema is exact.
KNOWLEDGE_SEARCH_BODY=$(python3 <<'PY'
import json
print(json.dumps({
    "name": "knowledge_search",
    "display_name": "Knowledge Search",
    "description": "Search the team's knowledge base for passages relevant to a question. Returns the most relevant document chunks with their source. Use this to ground answers in the team's own documents and cite them.",
    "type": "http",
    "risk_level": "low",
    "owner_team": "platform",
    "side_effecting": False,
    "http_method": "POST",
    "http_url": "http://agentshield-registry-api.agentshield-platform.svc.cluster.local:8000/api/v1/internal/knowledge/search",
    "http_headers": {
        "Content-Type": "application/json",
        "X-Agent-Team": "{{AGENTSHIELD_AGENT_TEAM}}",
        "X-Agent-Name": "{{AGENT_NAME}}",
    },
    "http_body_template": "{\"query\": \"{{query}}\", \"k\": 5}",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question or search phrase to look up in the knowledge base.",
            },
        },
        "required": ["query"],
    },
}))
PY
)
KNOWLEDGE_SEARCH=$(post_idempotent "/api/v1/tools/" "$KNOWLEDGE_SEARCH_BODY" "knowledge_search")
KNOWLEDGE_SEARCH_ID=$(echo "$KNOWLEDGE_SEARCH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

echo ""

# ===========================================================================
# AUTH CONFIGS (tool credentials)
# ===========================================================================
echo "--- Auth Configs ---"

SERPER_KEY="${SERPER_API_KEY:-demo-key-replace-me}"
SERPER_AC=$(post_idempotent "/api/v1/auth-configs/" \
  "{\"name\":\"serper-dev\",\"type\":\"api_key\",\"credentials\":{\"serper_api_key\":\"${SERPER_KEY}\"},\"owner_team\":\"${TEAM}\"}" \
  "serper-dev")
SERPER_AC_ID=$(echo "$SERPER_AC" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

# Link auth config to web_search tool (idempotent — runs every deploy)
if [ -n "$WEB_SEARCH_ID" ] && [ -n "$SERPER_AC_ID" ]; then
  curl -s -X PUT "${REGISTRY_URL}/api/v1/tools/${WEB_SEARCH_ID}" \
    -H "Content-Type: application/json" \
    -d "{\"auth_config_id\":\"${SERPER_AC_ID}\"}" > /dev/null 2>&1
  ok "web_search linked to serper-dev auth config"
else
  warn "web_search/serper-dev link skipped (WEB_SEARCH_ID='${WEB_SEARCH_ID}' SERPER_AC_ID='${SERPER_AC_ID}')"
fi

echo ""

# ===========================================================================
# SKILLS
# ===========================================================================
echo "--- Skills ---"

# Build tool_ids arrays — only include non-empty IDs
build_tool_ids() {
  python3 -c "
import json, sys
ids = [i for i in sys.argv[1:] if i]
print(json.dumps(ids))
" "$@"
}

WEB_RESEARCH_TOOL_IDS=$(build_tool_ids "${WEB_SEARCH_ID:-}" "${WEATHER_ID:-}" "${GEO_ID:-}")
post_idempotent "/api/v1/skills/" \
  "{\"name\":\"web_research_skill\",\"team\":\"${TEAM}\",\"description\":\"Bundled search + weather + geolocation tools for research agents.\",\"tool_ids\":${WEB_RESEARCH_TOOL_IDS}}" \
  "web_research_skill" > /tmp/skill_research.json

NOTIFY_TOOL_IDS=$(build_tool_ids "${SLACK_ID:-}")
post_idempotent "/api/v1/skills/" \
  "{\"name\":\"notification_skill\",\"team\":\"${TEAM}\",\"description\":\"Slack notification tool bundle.\",\"tool_ids\":${NOTIFY_TOOL_IDS}}" \
  "notification_skill" > /tmp/skill_notify.json

WEB_RESEARCH_SKILL_ID=$(python3 -c "import json; d=json.load(open('/tmp/skill_research.json')); print(d.get('id',''))" 2>/dev/null || echo "")
NOTIFY_SKILL_ID=$(python3 -c "import json; d=json.load(open('/tmp/skill_notify.json')); print(d.get('id',''))" 2>/dev/null || echo "")

echo ""

# ===========================================================================
# AGENTS
# ===========================================================================
echo "--- Agents ---"

post_idempotent "/api/v1/agents/" \
  "{\"name\":\"research-assistant\",\"team\":\"${TEAM}\",\"description\":\"Searches the web, looks up weather, geolocates IPs. Uses web_research_skill.\",\"agent_type\":\"declarative\"}" \
  "research-assistant" > /dev/null

post_idempotent "/api/v1/agents/" \
  "{\"name\":\"calculator-bot\",\"team\":\"${TEAM}\",\"description\":\"Evaluates math expressions using the AST-safe calculator tool.\",\"agent_type\":\"declarative\"}" \
  "calculator-bot" > /dev/null

post_idempotent "/api/v1/agents/" \
  "{\"name\":\"slack-notifier\",\"team\":\"${TEAM}\",\"description\":\"Sends Slack notifications via webhook. Uses notification_skill.\",\"agent_type\":\"declarative\"}" \
  "slack-notifier" > /dev/null

post_idempotent "/api/v1/agents/" \
  "{\"name\":\"echo-agent\",\"team\":\"${TEAM}\",\"description\":\"Minimal reference SDK agent. Source: services/echo-agent/. Responds to /health and /ready.\",\"agent_type\":\"sdk\"}" \
  "echo-agent" > /dev/null

post_idempotent "/api/v1/agents/" \
  "{\"name\":\"order-agent\",\"team\":\"${TEAM}\",\"description\":\"Example order-processing SDK agent with HITL approval gate. Source: examples/order-agent/.\",\"agent_type\":\"sdk\"}" \
  "order-agent" > /dev/null

echo ""

# ===========================================================================
# WORKFLOWS (starter canvases for declarative agents)
# ===========================================================================
echo "--- Workflows ---"

build_workflow() {
  local agent_name="$1"
  local agent_instructions="$2"
  local tool_ids_json="$3"
  local skill_ids_json="$4"

  python3 - <<EOF
import json
definition = {
  "nodes": [
    {
      "id": "${agent_name}-node",
      "type": "agent",
      "position": {"x": 100, "y": 200},
      "config": {
        "name": "${agent_name}-node",
        "instructions": """${agent_instructions}""",
        "model": "claude-sonnet-4-6",
        "risk": "low",
        "tool_ids": ${tool_ids_json},
        "skill_ids": ${skill_ids_json}
      }
    },
    {"id": "end", "type": "end", "position": {"x": 500, "y": 200}, "config": {"output_mapping": {}}}
  ],
  "edges": [
    {"id": "e1", "source": "${agent_name}-node", "target": "end", "condition": "default"}
  ]
}
print(json.dumps(definition))
EOF
}

RESEARCH_DEF=$(build_workflow \
  "research-assistant" \
  "You are a research assistant. Search the web for current information to answer the user question. When a location or city is mentioned, look up current weather. When an IP address is mentioned, geolocate it. Summarize all findings clearly and concisely." \
  "[]" \
  "$(python3 -c "import json; ids=[i for i in ['${WEB_RESEARCH_SKILL_ID:-}'] if i]; print(json.dumps(ids))")")

post_idempotent "/api/v1/workflows/" \
  "{\"name\":\"research-workflow\",\"team\":\"${TEAM}\",\"description\":\"Starter workflow for the research-assistant declarative agent.\",\"definition\":${RESEARCH_DEF}}" \
  "research-workflow" > /dev/null

CALC_TOOL_IDS=$(build_tool_ids "${CALC_ID:-}")
CALC_DEF=$(build_workflow \
  "calculator-bot" \
  "You are a calculator assistant. Use the calculator tool to evaluate any math expressions the user provides. Never calculate in your head — always use the tool and show the result." \
  "${CALC_TOOL_IDS}" \
  "[]")

post_idempotent "/api/v1/workflows/" \
  "{\"name\":\"calculator-workflow\",\"team\":\"${TEAM}\",\"description\":\"Starter workflow for the calculator-bot declarative agent.\",\"definition\":${CALC_DEF}}" \
  "calculator-workflow" > /dev/null

NOTIF_DEF=$(build_workflow \
  "slack-notifier" \
  "You are a notification dispatcher. When asked to send a message, use the slack_notify tool. Confirm what was sent after the tool call completes. If no webhook_url is provided, ask the user for it." \
  "[]" \
  "$(python3 -c "import json; ids=[i for i in ['${NOTIFY_SKILL_ID:-}'] if i]; print(json.dumps(ids))")")

post_idempotent "/api/v1/workflows/" \
  "{\"name\":\"notification-workflow\",\"team\":\"${TEAM}\",\"description\":\"Starter workflow for the slack-notifier declarative agent.\",\"definition\":${NOTIF_DEF}}" \
  "notification-workflow" > /dev/null

echo ""
echo "==> Default resource seeding complete."
echo ""
