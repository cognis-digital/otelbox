"""OTELBOX MCP server — exposes lint() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
import json
from otelbox.core import validate_config, load_config_text


def _lint_to_json(config_text: str) -> str:
    """Validate *config_text* (OTel YAML) and return a JSON findings string."""
    try:
        cfg = load_config_text(config_text)
    except ValueError as exc:
        return json.dumps({"ok": False, "error": f"parse error: {exc}"})
    result = validate_config(cfg)
    return json.dumps(result.as_dict(), indent=2)


def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-otelbox[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print("Install the MCP extra: pip install 'cognis-otelbox[mcp]'")
        return 1
    app = FastMCP("otelbox")

    @app.tool()
    def otelbox_lint(config_text: str) -> str:
        """Validate an OTel collector config YAML. Returns JSON findings."""
        return _lint_to_json(config_text)

    app.run()
    return 0
