"""OTELBOX MCP server — exposes scan() as an MCP tool for Cognis.Studio."""
from __future__ import annotations
from otelbox.core import scan, to_json

def serve() -> int:
    """Start an MCP stdio server. Requires the optional 'mcp' extra:
        pip install "cognis-otelbox[mcp]"
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception:
        print("Install the MCP extra: pip install 'cognis-otelbox[mcp]'")
        return 1
    app = FastMCP("otelbox")

    @app.tool()
    def otelbox_scan(target: str) -> str:
        """One-command OpenTelemetry collector + dashboards bundle. Returns JSON findings."""
        return to_json(scan(target))

    app.run()
    return 0
