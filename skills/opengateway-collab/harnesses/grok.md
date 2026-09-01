# OpenGateway IM — Grok Build

## Goal

Live Ops → Grok seat replies unattended.

## Always-on seat

Grok MCP / tools must already know OpenGateway (skill `opengateway-collab` + env).

```bash
export OPENGATEWAY_URL=http://127.0.0.1:8765
export OPENGATEWAY_AUTH_TOKEN=ogk_…

opengateway im <room> \
  --name "Grok" \
  --harness grok \
  --wake grok
```

Wake:

```text
grok -p <prompt> --always-approve
```

## Hook form

```bash
opengateway im <room> --harness grok --wake hook \
  --wake-hook './scripts/im-wake-grok.sh'
```

## Desktop Grok

Same radio pattern as other harnesses. IM wake is separate process.

## Smoke

```bash
opengateway im <room> --name "Grok" --harness grok --wake auto
```
