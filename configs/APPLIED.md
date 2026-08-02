# Applied harness configs

OpenGateway MCP was merged into live user configs on this machine. Backups live in `configs/applied-backups/`.

| Harness | Config file | Server name | `OPENGATEWAY_HARNESS` |
|---------|-------------|-------------|------------------------|
| Claude Code | `~/.claude/.mcp.json` + `~/.claude.json` | `opengateway` | `claude-code` |
| Cursor | `~/.cursor/mcp.json` | `opengateway` | `cursor` |
| Grok CLI | `~/.grok/config.toml` | `opengateway` | `grok` |
| Codex CLI | `~/.codex/config.toml` | `opengateway` | `codex` |
| Hermes | `~/.hermes/config.yaml` | `opengateway` | `hermes` |
| Project | `Code/OpenGateway/.mcp.json` | `opengateway` | `mcp` |

## Requirements

1. Gateway running: `uv run opengateway serve` (default `http://127.0.0.1:8765`)
2. Restart each harness after config change so MCP reconnects

## Skills installed

- `~/.claude/skills/opengateway-collab/SKILL.md`
- `~/.cursor/skills/opengateway-collab/SKILL.md`
- `~/.grok/skills/opengateway-collab/SKILL.md`

## Quick verify

```bash
# gateway
curl -s http://127.0.0.1:8765/ping

# MCP stdio smoke (should start without error)
OPENGATEWAY_URL=http://127.0.0.1:8765 \
  /Users/mrdulasolutions/.local/bin/uv run --directory /Users/mrdulasolutions/Code/OpenGateway \
  opengateway mcp
# (stdio — Ctrl-C after process starts cleanly)
```
