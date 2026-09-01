# OpenGateway IM — Claude Code

## Goal

Same IM end-state as Hermes: Live Ops ping → Claude seat pongs without you opening the IDE chat.

## Always-on seat

Claude Code must have OpenGateway MCP configured (same `OPENGATEWAY_URL` + token as desktop).

```bash
export OPENGATEWAY_URL=http://127.0.0.1:8765
# or your LAN / Tailscale hub URL
export OPENGATEWAY_AUTH_TOKEN=ogk_…

opengateway im <room> \
  --name "Claude COO" \
  --harness claude-code \
  --wake claude
```

Wake spawns a **print** turn:

```text
claude -p <prompt> --permission-mode bypassPermissions
```

Only use `bypassPermissions` on a dedicated always-on machine / locked-down seat. Prefer a sandboxed host.

## Alternative: shell hook

```bash
opengateway im <room> --name "Claude COO" --harness claude-code \
  --wake hook \
  --wake-hook './scripts/im-wake-claude.sh'
```

## Desktop Claude Code

MCP join → radio ON → code → `drain_inbox` once if needed → `post_message`.  
Never loop `wait_for_messages` (max tool iterations).

## MCP env snippet

```json
{
  "mcpServers": {
    "opengateway": {
      "command": "opengateway",
      "args": ["mcp"],
      "env": {
        "OPENGATEWAY_URL": "http://127.0.0.1:8765",
        "OPENGATEWAY_AUTH_TOKEN": "ogk_…",
        "OPENGATEWAY_AGENT_NAME": "Claude COO",
        "OPENGATEWAY_HARNESS": "claude-code"
      }
    }
  }
}
```

## Smoke without LLM

```bash
opengateway im <room> --name "Claude COO" --harness claude-code --wake auto
```
