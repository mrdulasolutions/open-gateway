# Mobile Web Push

Get notified on your phone when you receive a DM or room activity — works with the Live Ops PWA over Tailscale or HTTPS.

## Requirements

1. Gateway served over **HTTPS** (Tailscale Serve, Fly, Railway) or `localhost`
2. Optional extra: `uv sync --extra push` (installs `pywebpush`)
3. VAPID key pair

## Generate VAPID keys

```bash
# with pywebpush installed
python - <<'PY'
from py_vapid import Vapid01
v = Vapid01()
v.generate_keys()
print("OPENGATEWAY_VAPID_PRIVATE=" + v.private_pem().decode().replace("\n", "\\n"))
# Or use application server keys:
from cryptography.hazmat.primitives import serialization
print(v)
PY
```

Easier (Node one-shot, if you have `web-push`):

```bash
npx web-push generate-vapid-keys
# → set public + private below
```

```bash
export OPENGATEWAY_VAPID_PUBLIC="BNxxx..."
export OPENGATEWAY_VAPID_PRIVATE="xxxxx"   # or PEM
export OPENGATEWAY_VAPID_CONTACT="mailto:you@example.com"
```

## Client flow

1. Open Live Ops → **Settings** → **Enable mobile push**
2. Accept notification permission
3. Phone stays subscribed; DMs to your `participant_id` trigger a push

API:

```http
GET  /v1/push/vapid
POST /v1/push/subscribe   { "subscription": {…}, "participant_id": "…", "device_label": "iphone" }
GET  /v1/push/subscriptions
DELETE /v1/push/subscriptions/{id}
```

Service worker: `/ui/sw.js` (shipped with the UI).

## Tips

- Pair the phone on **Tailnet** so HTTPS MagicDNS works off Wi‑Fi
- Push is best-effort; delivery depends on OS + browser power saving
- Set `participant_id` when subscribing so only your DMs wake you (not every room message)
