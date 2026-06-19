"""Native-tool dispatch : argument validation (`_arg_mismatch_error`).

A local model that fumbles a parameter name (e.g. blends `relative_path` + `content`
into `content_relative_path`) must get a clean, instructive error — not a leaky Python
TypeError — and recover in one step. Generic to every registry tool ; valid calls are
unchanged.
"""

from __future__ import annotations

from jeanmichel.orchestrator_v2 import _arg_mismatch_error, _execute_native_tool

from ._orchestrator_helpers import make_tool_spec, tool_call


def _file_tool():
    # Mirrors workspace_create_file : two required params + one optional.
    def handler(relative_path, content, description=""):
        return '{"ok": true, "summary": "ran"}'

    return make_tool_spec("make_file", handler)


def test_valid_args_run_unchanged():
    reg = {"make_file": _file_tool()}
    out = _execute_native_tool(tool_call("make_file", relative_path="a.md", content="x"), reg)
    assert out == {"ok": True, "summary": "ran"}  # handler ran, no error injected


def test_optional_arg_present_is_fine():
    reg = {"make_file": _file_tool()}
    out = _execute_native_tool(
        tool_call("make_file", relative_path="a.md", content="x", description="d"), reg
    )
    assert out == {"ok": True, "summary": "ran"}


def test_unknown_arg_gives_clean_error_not_typeerror():
    """The reported couac : `content_relative_path` instead of `relative_path`."""
    reg = {"make_file": _file_tool()}
    out = _execute_native_tool(
        tool_call("make_file", content_relative_path="a.md", content="x"), reg
    )
    assert out["error"] == "bad_arguments"
    assert "content_relative_path" in out["summary"]  # names the offender
    assert "relative_path" in out["summary"]           # names the valid arg
    assert "raised" not in out["summary"]              # not the leaky TypeError path


def test_missing_required_arg_reported():
    reg = {"make_file": _file_tool()}
    out = _execute_native_tool(tool_call("make_file", relative_path="a.md"), reg)
    assert out["error"] == "bad_arguments"
    assert "content" in out["summary"]


def test_var_keyword_handler_accepts_anything():
    """MCP-style handlers take **kwargs → never flagged (they accept any argument)."""
    def handler(**kwargs):
        return '{"ok": true}'

    reg = {"mcp_tool": make_tool_spec("mcp_tool", handler)}
    out = _execute_native_tool(tool_call("mcp_tool", whatever="x", more="y"), reg)
    assert out.get("error") != "bad_arguments"


def test_helper_none_on_match_error_on_mismatch():
    def handler(a, b=2):
        return "x"

    assert _arg_mismatch_error("t", handler, {"a": 1}) is None              # required present, optional omitted
    assert _arg_mismatch_error("t", handler, {"a": 1, "b": 3}) is None      # all present
    assert _arg_mismatch_error("t", handler, {"a": 1, "c": 9})["error"] == "bad_arguments"  # unknown 'c'
    assert _arg_mismatch_error("t", handler, {})["error"] == "bad_arguments"  # missing 'a'
