#!/usr/bin/env python3
"""Live-test an Ollama model for the ORCHESTRATOR role (tool-calling + delegation).

The orchestrator's whole job is emitting valid tool calls and decomposing work —
so we probe exactly that, the way `jeanmichel.llm.chat_messages` calls Ollama
(options={num_ctx, temperature}, tools=[...]). Catches broken GGUFs that emit
garbage ('!!!!') or crash the runner (status 500) before they ever hit the chain.

Usage:
    .venv/bin/python debug/eval_model.py cogito:14b cogito:32b qwen3:14b [--ctx 40960]

Per model it runs and scores:
  A. basic generation     — no garbage, no crash
  B. simple tool call     — emits a valid delegate_to(agent, task)
  C. multi-step decompose — picks the right tool among several, sane args
  D. temperature robustness — re-runs B at temp 0.2 AND 0.6 (some GGUFs crash hot)
"""

from __future__ import annotations

import argparse
import time

import ollama

_DELEGATE_TOOL = {
    "type": "function",
    "function": {
        "name": "delegate_to",
        "description": "Delegate a task to a specialist agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "specialist code"},
                "task": {"type": "string", "description": "the task for the specialist"},
            },
            "required": ["agent", "task"],
        },
    },
}
_WEB_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for fresh information.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}

_GARBAGE = ("!!!!", "????", "....")


def _looks_like_garbage(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and any(g in t for g in _GARBAGE) and len(set(t)) <= 3


def _chat(client, model, messages, ctx, temp, tools=None):
    opts = {"num_ctx": ctx, "temperature": temp}
    return client.chat(
        model=model, messages=messages, tools=tools or [],
        options=opts, stream=False, keep_alive="30s",
    )


def _tool_calls(resp):
    return resp.get("message", {}).get("tool_calls") or []


def _run_one(client, model, ctx):
    rows = []

    def record(name, ok, detail, dt):
        rows.append((name, ok, detail, dt))

    # A. basic generation
    t = time.time()
    try:
        r = _chat(client, model, [{"role": "user", "content": "Réponds en un mot: bonjour."}], ctx, 0.2)
        content = r.get("message", {}).get("content") or ""
        thinking = r.get("message", {}).get("thinking") or ""
        garbage = _looks_like_garbage(content) or _looks_like_garbage(thinking)
        ok = not garbage
        record("A.basic", ok, "garbage output" if garbage else repr(content[:40]), time.time() - t)
    except Exception as e:  # noqa: BLE001
        record("A.basic", False, f"{type(e).__name__}: {str(e)[:80]}", time.time() - t)

    # B. simple tool call
    t = time.time()
    try:
        r = _chat(client, model,
                  [{"role": "user", "content": "Quelle météo à Paris ? Délègue au weather-specialist."}],
                  ctx, 0.2, tools=[_DELEGATE_TOOL])
        tc = _tool_calls(r)
        ok = bool(tc) and tc[0]["function"]["name"] == "delegate_to"
        record("B.tool_call", ok, str(tc[0]["function"]["arguments"]) if ok else f"no/bad tool_calls ({len(tc)})", time.time() - t)
    except Exception as e:  # noqa: BLE001
        record("B.tool_call", False, f"{type(e).__name__}: {str(e)[:80]}", time.time() - t)

    # C. multi-step decomposition (pick the right tool among several)
    t = time.time()
    try:
        r = _chat(client, model,
                  [{"role": "user", "content": "Trouve les news récentes sur l'IA, puis délègue la synthèse à un specialist."}],
                  ctx, 0.2, tools=[_DELEGATE_TOOL, _WEB_TOOL])
        tc = _tool_calls(r)
        ok = bool(tc) and tc[0]["function"]["name"] in ("web_search", "delegate_to")
        record("C.decompose", ok, (tc[0]["function"]["name"] if ok else f"no/bad tool_calls ({len(tc)})"), time.time() - t)
    except Exception as e:  # noqa: BLE001
        record("C.decompose", False, f"{type(e).__name__}: {str(e)[:80]}", time.time() - t)

    # D. temperature robustness (some GGUFs only crash hot)
    for temp in (0.2, 0.6):
        t = time.time()
        try:
            r = _chat(client, model,
                      [{"role": "user", "content": "Délègue 'analyse X' au code-analyst."}],
                      ctx, temp, tools=[_DELEGATE_TOOL])
            tc = _tool_calls(r)
            content = r.get("message", {}).get("content") or ""
            ok = bool(tc) or not _looks_like_garbage(content)
            record(f"D.temp{temp}", ok, (tc[0]["function"]["name"] if tc else "no crash"), time.time() - t)
        except Exception as e:  # noqa: BLE001
            record(f"D.temp{temp}", False, f"{type(e).__name__}: {str(e)[:80]}", time.time() - t)

    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Live-test Ollama models for the orchestrator role.")
    ap.add_argument("models", nargs="+", help="Ollama model tags to evaluate")
    ap.add_argument("--ctx", type=int, default=40960, help="num_ctx to pin (default 40960)")
    args = ap.parse_args()

    client = ollama.Client()
    summary = []
    for model in args.models:
        print(f"\n{'=' * 64}\n  {model}  (num_ctx={args.ctx})\n{'=' * 64}")
        rows = _run_one(client, model, args.ctx)
        passed = sum(1 for _, ok, _, _ in rows if ok)
        for name, ok, detail, dt in rows:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name:14s} {dt:5.1f}s  {detail}")
        print(f"  -> {passed}/{len(rows)} passed")
        summary.append((model, passed, len(rows)))

    print(f"\n{'=' * 64}\n  SUMMARY\n{'=' * 64}")
    for model, passed, total in sorted(summary, key=lambda x: -x[1]):
        print(f"  {passed}/{total}  {model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
