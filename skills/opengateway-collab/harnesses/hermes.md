# OpenGateway IM — Hermes

## Goal

Live Ops message → Hermes COO replies without a human opening desktop Hermes.

## Always-on seat

```bash
export OPENGATEWAY_URL=http://127.0.0.1:8765
export OPENGATEWAY_AUTH_TOKEN=ogk_…   # device key

opengateway im <room> \
  --name "Hermes COO" \
  --harness hermes \
  --wake hermes \
  --hermes-skills opengateway-collab \
  --hermes-max-turns 20
```

Or `--wake harness` (same when `--harness hermes`).

## What the wake does

Spawns:

```text
hermes chat -Q -q <prompt> -s opengateway-collab --max-turns 20 --accept-hooks
```

The prompt tells Hermes to `drain_inbox` / `post_message` (MCP) and **not** loop `wait_for_messages`.

## Desktop Hermes (coding chat)

Still: `begin_im_mode` → radio → work → `drain_inbox` ≤1/turn.  
Do **not** wait-loop. IM auto-reply is the external `opengateway im` process.

## Optional: Hermes gateway webhook

```bash
# Enable webhook platform in ~/.hermes/config.yaml, then:
hermes webhook subscribe opengateway-im \
  --prompt 'OpenGateway IM wake. {prompt}' \
  --description 'Room events from opengateway im'

opengateway im <room> --name "Hermes COO" --harness hermes \
  --wake webhook \
  --wake-webhook http://127.0.0.1:8644/webhooks/opengateway-im
```

## Smoke without LLM

```bash
opengateway im <room> --name "Hermes COO" --harness hermes --wake auto
# Live Ops: ping → pong
```
