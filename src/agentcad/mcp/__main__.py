"""Entry point for `python -m agentcad.mcp`."""

try:
    from agentcad.mcp.server import isolate_protocol_stdout, mcp
except ImportError as e:
    import sys
    print(
        f"Error: {e}\n\n"
        "The MCP server requires the 'mcp' package.\n"
        "Install it with: pip install agentcad[mcp]",
        file=sys.stderr,
    )
    sys.exit(1)

isolate_protocol_stdout()
mcp.run(transport="stdio")
