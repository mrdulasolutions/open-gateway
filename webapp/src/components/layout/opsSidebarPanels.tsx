/**
 * Panels shared by settings sheet and legacy sidebar.
 */
import { useEffect, useState } from "react";
import QRCode from "qrcode";
import {
  Bot,
  Copy,
  Globe2,
  KeyRound,
  Lock,
  Network,
  Plus,
  QrCode,
  Shield,
  Trash2,
  UserPlus,
  X,
} from "lucide-react";
import { AgentInstallPanel } from "@/components/AgentInstallPanel";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { AuthInvite, Ping } from "@/lib/types";
import type { GatewayCard } from "@/components/layout/OpsSidebar";

export function networkLabel(network: string): string {
  switch (network) {
    case "loopback":
      return "Internal (loopback)";
    case "lan":
      return "LAN";
    case "tailscale":
      return "Tailnet (Serve)";
    case "funnel":
      return "Internet (Funnel)";
    case "public":
      return "Internet (open bind)";
    default:
      return network;
  }
}

export function networkBadgeClass(network: string): string {
  switch (network) {
    case "loopback":
      return "border-emerald-500/30 text-emerald-800 dark:text-emerald-200";
    case "lan":
      return "border-sky-500/40 bg-sky-500/10 text-sky-800 dark:text-sky-200";
    case "tailscale":
      return "border-violet-500/40 bg-violet-500/10 text-violet-800 dark:text-violet-200";
    case "funnel":
      return "border-rose-500/40 bg-rose-500/10 text-rose-800 dark:text-rose-200";
    case "public":
      return "border-amber-500/40 bg-amber-500/10 text-amber-900 dark:text-amber-200";
    default:
      return "border-zinc-200 dark:border-white/10";
  }
}

export function GatewayCardView({
  g,
  onClick,
}: {
  g: GatewayCard;
  onClick?: () => void;
}) {
  const isTailnet = g.network === "tailscale";
  const isFunnel = g.network === "funnel";
  const isLan = g.network === "lan";
  const isLoopback = g.network === "loopback" || g.mode === "internal";
  const label = networkLabel(g.network);
  return (
    <button
      type="button"
      onClick={onClick}
      title="Click for phone pair QR"
      className={cn(
        "mb-1.5 w-full rounded-lg border px-2.5 py-2 text-left transition",
        g.is_self
          ? "border-orange-500/30 bg-orange-500/5 hover:bg-orange-500/10"
          : "border-zinc-200 hover:border-orange-500/30 hover:bg-zinc-50 dark:border-white/10 dark:hover:bg-white/5"
      )}
    >
      <div className="flex items-center gap-1.5 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
        {isFunnel ? (
          <Globe2 className="h-3.5 w-3.5 text-rose-500" />
        ) : isTailnet ? (
          <Shield className="h-3.5 w-3.5 text-violet-500" />
        ) : isLan ? (
          <Network className="h-3.5 w-3.5 text-sky-500" />
        ) : isLoopback ? (
          <Network className="h-3.5 w-3.5 text-emerald-500" />
        ) : (
          <Globe2 className="h-3.5 w-3.5 text-amber-500" />
        )}
        <span className="min-w-0 flex-1 truncate">{g.name}</span>
        {g.is_self && (
          <span className="rounded-full bg-orange-500/15 px-1.5 py-px text-[10px] font-medium text-orange-800 dark:text-orange-200">
            this
          </span>
        )}
        <QrCode className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
      </div>
      <div className="mt-1 font-mono text-[10px] text-zinc-500 break-all">
        {g.base_url}
      </div>
      <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-zinc-500">
        <span
          className={cn(
            "rounded border px-1.5 py-px font-semibold",
            networkBadgeClass(g.network)
          )}
        >
          {label}
        </span>
        {g.require_auth && (
          <span className="rounded border border-amber-500/30 px-1 text-amber-700 dark:text-amber-300">
            auth
          </span>
        )}
        <span className="rounded border border-zinc-200 px-1 dark:border-white/10">
          tap to pair
        </span>
      </div>
      {g.notes && (
        <div className="mt-1 text-[10px] text-zinc-500 line-clamp-2">{g.notes}</div>
      )}
    </button>
  );
}

export function PairQrModal({
  gateway,
  roomId,
  onClose,
}: {
  gateway: GatewayCard;
  roomId: string | null;
  onClose: () => void;
}) {
  const [url, setUrl] = useState("");
  const [code, setCode] = useState("");
  const [qrDataUrl, setQrDataUrl] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(true);
  const [copied, setCopied] = useState(false);
  const [expiresIn, setExpiresIn] = useState(900);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy(true);
      setErr("");
      try {
        const res = await api.createPair({
          room_id: roomId || undefined,
          label: "mobile",
          ttl_seconds: 900,
          // QR origin must match the card: LAN = Wi‑Fi only; Tailnet = cellular OK
          base_url: gateway.base_url,
          network: gateway.network,
        });
        if (cancelled) return;
        // Always force QR origin to this gateway card (API may advertise LAN by default)
        let pairUrl = res.url || "";
        try {
          const u = new URL(pairUrl);
          const gw = new URL(
            gateway.base_url.includes("://")
              ? gateway.base_url
              : `https://${gateway.base_url}`
          );
          if (gw.hostname) {
            u.protocol = gw.protocol || u.protocol;
            u.hostname = gw.hostname;
            // Empty port on https MagicDNS → default 443 (do not keep LAN :8765)
            u.port = gw.port;
            pairUrl = u.toString();
          }
        } catch {
          /* keep res.url */
        }
        // Prefer server-built URL for this network when provided
        if (gateway.network === "tailscale" && res.urls?.tailscale) {
          pairUrl = res.urls.tailscale;
        } else if (gateway.network === "lan" && res.urls?.lan) {
          pairUrl = res.urls.lan;
        }
        setUrl(pairUrl);
        setCode(res.code || "");
        setExpiresIn(res.ttl_seconds || 900);
        const dataUrl = await QRCode.toDataURL(pairUrl, {
          width: 280,
          margin: 2,
          color: { dark: "#18181b", light: "#ffffff" },
        });
        if (!cancelled) setQrDataUrl(dataUrl);
      } catch (e) {
        if (!cancelled) {
          const msg = e instanceof Error ? e.message : String(e);
          // Friendlier hints for common failures
          if (/not found/i.test(msg) || /404/.test(msg)) {
            setErr(
              "Pair API not found — restart the gateway with the latest OpenGateway (needs /v1/pair)."
            );
          } else if (/unauthoriz/i.test(msg) || /401/.test(msg)) {
            setErr(
              "Unauthorized — paste the gateway auth token in Settings, then try again."
            );
          } else {
            setErr(msg);
          }
        }
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [gateway.base_url, roomId]);

  const networkHint = (() => {
    switch (gateway.network) {
      case "lan":
        return {
          title: "Same Wi‑Fi required",
          body: `LAN path only — phone must be on the same Wi‑Fi as this computer. For cellular, close this and tap the Tailnet (Serve) card instead. Target: ${gateway.base_url}`,
        };
      case "tailscale":
        return {
          title: "Cellular OK · Tailscale must be ON",
          body: `Works on cellular or any Wi‑Fi as long as the Tailscale app is connected to the same tailnet. Turn on Tailscale VPN on the phone, then scan. No same-Wi‑Fi needed. Target: ${gateway.base_url}`,
        };
      case "funnel":
        return {
          title: "Public Funnel URL",
          body: "Funnel is internet-reachable. Phone can use any network (including cellular), but you still need a strong auth token.",
        };
      case "loopback":
        return {
          title: "This gateway is loopback-only",
          body: "Internal (127.0.0.1) cannot be opened from a phone. Use the LAN card (same Wi‑Fi) or Tailnet Serve card (cellular + Tailscale).",
        };
      default:
        return {
          title: "Phone must reach this host",
          body: `Phone needs network path to ${gateway.base_url}. For cellular, use the Tailnet (Serve) card with Tailscale ON.`,
        };
    }
  })();

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div
        role="dialog"
        aria-label="Pair phone"
        className="w-[min(360px,94vw)] rounded-2xl border border-zinc-200 bg-white p-5 shadow-2xl dark:border-white/10 dark:bg-zinc-900"
      >
        <div className="mb-3 flex items-start justify-between gap-2">
          <div>
            <div className="flex items-center gap-2 text-base font-semibold text-zinc-900 dark:text-zinc-50">
              <QrCode className="h-4 w-4 text-orange-500" />
              Pair phone
            </div>
            <div className="mt-0.5 text-xs text-zinc-500">
              {gateway.name} · {networkLabel(gateway.network)}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/10"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mb-3 rounded-xl border border-amber-500/35 bg-amber-50 px-3 py-2.5 text-left dark:border-amber-500/30 dark:bg-amber-500/10">
          <div className="text-xs font-semibold text-amber-950 dark:text-amber-100">
            ⚠ {networkHint.title}
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-amber-900/90 dark:text-amber-100/85">
            {networkHint.body}
          </p>
        </div>

        {busy && (
          <div className="py-12 text-center text-sm text-zinc-500">
            Generating pair link…
          </div>
        )}
        {err && (
          <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-800 dark:text-rose-200">
            {err}
            <p className="mt-1 text-xs opacity-80">
              Public gateways need an auth token in Settings.
            </p>
          </div>
        )}
        {!busy && !err && qrDataUrl && (
          <>
            <div className="mx-auto flex w-fit flex-col items-center rounded-xl border border-zinc-200 bg-white p-3 dark:border-white/10">
              <img
                src={qrDataUrl}
                alt="Pair QR code"
                className="h-[240px] w-[240px]"
              />
            </div>
            <div className="mt-3 text-center font-mono text-lg font-bold tracking-[0.2em] text-zinc-900 dark:text-zinc-100">
              {code}
            </div>
            <p className="mt-1 text-center text-[11px] text-zinc-500">
              Scan with your phone · expires in {Math.round(expiresIn / 60)} min
              {roomId ? " · opens current room" : ""}
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                className="flex flex-1 items-center justify-center gap-1.5 rounded-xl border border-zinc-200 px-3 py-2.5 text-sm font-semibold text-zinc-700 hover:bg-zinc-50 dark:border-white/10 dark:text-zinc-200 dark:hover:bg-white/5"
                onClick={async () => {
                  await navigator.clipboard.writeText(url);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                }}
              >
                <Copy className="h-3.5 w-3.5" />
                {copied ? "Copied" : "Copy link"}
              </button>
              <button
                type="button"
                className="flex-1 rounded-xl bg-gradient-to-b from-orange-400 to-orange-600 px-3 py-2.5 text-sm font-semibold text-orange-950 shadow-sm"
                onClick={onClose}
              >
                Done
              </button>
            </div>
            <p className="mt-2 break-all text-center font-mono text-[10px] text-zinc-400">
              {url}
            </p>
          </>
        )}
      </div>
    </div>
  );
}

export function TeamInvitesPanel({
  hasAuthToken,
  requireAuth,
  onOpenSettings,
}: {
  hasAuthToken: boolean;
  requireAuth: boolean;
  onOpenSettings: () => void;
}) {
  const [invites, setInvites] = useState<AuthInvite[]>([]);
  const [openRegistration, setOpenRegistration] = useState(false);
  const [canManage, setCanManage] = useState(false);
  const [role, setRole] = useState<"member" | "admin">("member");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [freshCode, setFreshCode] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [hasUsers, setHasUsers] = useState(false);

  const load = async () => {
    try {
      const [status, me] = await Promise.all([
        api.authStatus(),
        api.authMe(),
      ]);
      setHasUsers(Boolean(status.has_users));
      if (!hasAuthToken) {
        setCanManage(false);
        setInvites([]);
        setErr("");
        return;
      }
      const admin =
        me.user?.role === "admin" || me.auth_kind === "master";
      setCanManage(Boolean(status.has_users) && admin);
      if (!admin || !status.has_users) {
        setInvites([]);
        return;
      }
      const listed = await api.listAuthInvites();
      setInvites(listed.invites);
      setOpenRegistration(listed.open_registration);
      setErr("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setCanManage(false);
    }
  };

  useEffect(() => {
    void load();
  }, [hasAuthToken, requireAuth]);

  const create = async () => {
    setBusy(true);
    setErr("");
    setFreshCode(null);
    try {
      const inv = await api.authInvite({ role });
      setFreshCode(inv.code);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const copyCode = async (code: string) => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  const hubUrl = `${window.location.origin}${import.meta.env.BASE_URL}`;

  if (!hasUsers) {
    return (
      <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-white/10 dark:bg-zinc-950/40">
        <div className="flex items-center gap-2 text-sm font-semibold text-zinc-800 dark:text-zinc-100">
          <UserPlus className="h-4 w-4 text-zinc-500" />
          People
        </div>
        <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">
          Create your admin account on the login page first. Then you can
          invite other humans here. Add Agent starts Claude, Grok, or Hermes,
          not a person.
        </p>
      </div>
    );
  }

  if (!canManage) {
    return (
      <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-white/10 dark:bg-zinc-950/40">
        <div className="flex items-center gap-2 text-sm font-semibold text-zinc-800 dark:text-zinc-100">
          <UserPlus className="h-4 w-4 text-zinc-500" />
          People
        </div>
        <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">
          {requireAuth && !hasAuthToken
            ? "Sign in or paste the hub token in Settings to invite people."
            : "Only an admin can invite people. They open this hub and choose Join organization."}
        </p>
        {requireAuth && !hasAuthToken ? (
          <button
            type="button"
            onClick={onOpenSettings}
            className="mt-2 text-[10px] font-semibold text-orange-700 underline dark:text-orange-300"
          >
            Open Settings
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-zinc-200 bg-white p-3 dark:border-white/10 dark:bg-zinc-950/40">
      <div className="flex items-center gap-2 text-sm font-semibold text-zinc-800 dark:text-zinc-100">
        <UserPlus className="h-4 w-4 text-zinc-500" />
        People
      </div>
      <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">
        {openRegistration
          ? "Open registration is on — invite codes are optional."
          : "Registration is invite-only after the first admin."}
        Teammates open{" "}
        <span className="font-mono text-[9px]">{hubUrl}</span> and choose{" "}
        <strong>Join organization</strong>.
      </p>
      {err && (
        <p className="mt-2 text-[10px] text-rose-600 dark:text-rose-300">
          {err}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-end gap-2">
        <label className="text-[10px] font-medium text-zinc-500">
          Role
          <select
            value={role}
            onChange={(e) =>
              setRole(e.target.value === "admin" ? "admin" : "member")
            }
            className="field-input mt-0.5 py-1 text-xs"
            disabled={busy}
          >
            <option value="member">Member</option>
            <option value="admin">Admin</option>
          </select>
        </label>
        <button
          type="button"
          onClick={() => void create()}
          disabled={busy}
          className="rounded-lg bg-orange-500/15 px-2.5 py-1.5 text-[10px] font-semibold text-orange-900 disabled:opacity-50 dark:text-orange-100"
        >
          {busy ? "Creating…" : "Create invite"}
        </button>
        <button
          type="button"
          onClick={() => void load()}
          disabled={busy}
          className="rounded-lg border border-zinc-200 px-2 py-1.5 text-[10px] font-semibold dark:border-white/10"
        >
          Refresh
        </button>
      </div>
      {freshCode && (
        <div className="mt-2 flex items-center justify-between gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2 py-1.5">
          <span className="font-mono text-xs font-bold tracking-wider">
            {freshCode}
          </span>
          <button
            type="button"
            onClick={() => void copyCode(freshCode)}
            className="inline-flex items-center gap-1 text-[10px] font-semibold"
          >
            <Copy className="h-3 w-3" />
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      )}
      {invites.length > 0 && (
        <ul className="mt-2 max-h-36 space-y-1 overflow-y-auto text-[10px]">
          {invites.map((inv) => {
            const exhausted = inv.uses >= inv.max_uses;
            return (
              <li
                key={inv.code}
                className={cn(
                  "flex items-center justify-between gap-2 rounded-md px-1.5 py-1",
                  exhausted
                    ? "bg-zinc-100 text-zinc-400 dark:bg-white/5"
                    : "bg-zinc-50 dark:bg-white/5"
                )}
              >
                <span className="font-mono font-semibold">{inv.code}</span>
                <span className="shrink-0 text-zinc-500">
                  {inv.role} · {inv.uses}/{inv.max_uses}
                </span>
                {!exhausted && (
                  <button
                    type="button"
                    onClick={() => void copyCode(inv.code)}
                    className="shrink-0 text-orange-700 dark:text-orange-200"
                    aria-label={`Copy invite ${inv.code}`}
                  >
                    <Copy className="h-3 w-3" />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * Create / list / revoke agent & device API keys (production auth).
 * Master token in Settings can mint keys; scoped keys cannot.
 */
export function AgentTokensPanel({
  gatewayBase,
  hasAuthToken,
  requireAuth,
  roomHint,
}: {
  gatewayBase: string;
  hasAuthToken: boolean;
  requireAuth: boolean;
  roomHint: string;
}) {
  const [keys, setKeys] = useState<
    {
      id: string;
      name: string;
      key_prefix: string;
      scopes: string[];
      role: string;
      device_label: string;
      metadata?: Record<string, unknown>;
    }[]
  >([]);
  const [name, setName] = useState("grok-agent");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [freshToken, setFreshToken] = useState<string | null>(null);

  const load = async () => {
    if (requireAuth && !hasAuthToken) {
      setErr("");
      setKeys([]);
      return;
    }
    try {
      const res = await api.listKeys();
      setKeys(
        (res.keys || []).filter(
          (key) =>
            !["runner", "managed_agent"].includes(
              String(key.metadata?.identity_type || "")
            )
        )
      );
      setErr("");
    } catch (e) {
      setErr(
        e instanceof Error
          ? e.message
          : "Cannot list keys — paste master token in Settings (admin)"
      );
    }
  };

  useEffect(() => {
    void load();
  }, [hasAuthToken, requireAuth]);

  const create = async () => {
    setBusy(true);
    setErr("");
    setFreshToken(null);
    try {
      const res = await api.createKey({
        name: name.trim() || "agent",
        device_label: label.trim() || name.trim() || "agent",
        scopes: ["write", "read", "pair", "push", "tools"],
        role: "contributor",
      });
      if (res.token) setFreshToken(res.token);
      await load();
    } catch (e) {
      setErr(
        e instanceof Error
          ? e.message
          : "Create failed — need master token (admin scope)"
      );
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: string) => {
    if (!confirm("Revoke this agent/device token?")) return;
    try {
      await api.deleteKey(id, true);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  if (requireAuth && !hasAuthToken) {
    return (
      <div className="space-y-2 rounded-xl border border-amber-500/35 bg-amber-50 p-3 dark:border-amber-500/30 dark:bg-amber-500/10">
        <p className="text-[11px] font-semibold text-amber-950 dark:text-amber-100">
          Authorize this browser first
        </p>
        <p className="text-[10px] leading-relaxed text-amber-900/90 dark:text-amber-100/85">
          OSS on-device: you run the hub yourself (
          <code className="text-[9px]">opengateway serve --token …</code>
          ). Open <strong>Settings</strong> →{" "}
          <strong>This browser&apos;s auth token</strong> and paste that same
          token. Then return here to mint one key per agent.
        </p>
        <p className="text-[10px] leading-relaxed text-amber-900/80 dark:text-amber-100/70">
          Railway is optional. Only paste a remote host&apos;s{" "}
          <code className="text-[9px]">OPENGATEWAY_AUTH_TOKEN</code> if this
          UI is that host — not for a hub on this machine.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-xl border border-zinc-200 p-3 dark:border-white/10">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-zinc-700 dark:text-zinc-200">
        <Shield className="h-3.5 w-3.5 text-orange-500" />
        Compatibility API keys
      </div>
      <p className="text-[10px] leading-relaxed text-zinc-500">
        {requireAuth
          ? "For older manual MCP and device setups. New managed agents do not need keys copied through the browser."
          : "For older manual MCP and device setups. Prefer Add Agent for runner-managed launches."}{" "}
        Raw keys are never rendered; a ready-to-use snippet can be copied once.
      </p>

      <div className="grid grid-cols-2 gap-2">
        <label className="block text-[10px] font-medium text-zinc-500">
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="field-input mt-0.5 text-xs"
            placeholder="grok-agent"
            maxLength={48}
          />
        </label>
        <label className="block text-[10px] font-medium text-zinc-500">
          Device / host
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="field-input mt-0.5 text-xs"
            placeholder="MacBook · CI · phone"
            maxLength={48}
          />
        </label>
      </div>

      <button
        type="button"
        disabled={busy}
        onClick={() => void create()}
        className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-gradient-to-b from-orange-400 to-orange-600 px-3 py-2 text-xs font-semibold text-orange-950 shadow-sm disabled:opacity-50"
      >
        <Plus className="h-3.5 w-3.5" />
        {busy ? "Creating…" : "Create agent token"}
      </button>

      {err && (
        <p className="text-[10px] text-rose-600 dark:text-rose-300">{err}</p>
      )}

      {freshToken && (
        <AgentInstallPanel
          hubUrl={gatewayBase}
          token={freshToken}
          defaultName={name.trim() || undefined}
          roomHint={roomHint}
        />
      )}

      <ul className="space-y-1.5">
        {keys.length === 0 && !err && (
          <li className="text-[10px] text-zinc-400">No agent tokens yet</li>
        )}
        {keys.map((k) => (
          <li
            key={k.id}
            className="flex items-start justify-between gap-2 rounded-lg border border-zinc-100 px-2 py-1.5 dark:border-white/[0.06]"
          >
            <div className="min-w-0">
              <div className="truncate text-[11px] font-semibold text-zinc-800 dark:text-zinc-100">
                {k.name}
                {k.device_label ? (
                  <span className="font-normal text-zinc-400">
                    {" "}
                    · {k.device_label}
                  </span>
                ) : null}
              </div>
              <div className="font-mono text-[9px] text-zinc-500">
                {k.key_prefix}… · {(k.scopes || []).join(", ")} · {k.role}
              </div>
            </div>
            <button
              type="button"
              onClick={() => void revoke(k.id)}
              className="shrink-0 text-[10px] font-semibold text-rose-600 hover:underline dark:text-rose-300"
            >
              Revoke
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Admin tool credential vault — secrets never listed; agents use tool_proxy. */
export function ToolVaultPanel({
  hasAuthToken,
  requireAuth,
  onOpenSettings,
}: {
  hasAuthToken: boolean;
  requireAuth: boolean;
  onOpenSettings: () => void;
}) {
  const [rows, setRows] = useState<
    {
      name: string;
      description?: string;
      inject?: string;
      allowed_hosts?: string[];
      has_value?: boolean;
    }[]
  >([]);
  const [name, setName] = useState("github");
  const [value, setValue] = useState("");
  const [hosts, setHosts] = useState("api.github.com");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");

  const unlocked = hasAuthToken || !requireAuth;

  const load = async () => {
    if (!unlocked) return;
    try {
      const r = await api.listToolCredentials();
      setRows(r.credentials || []);
      setErr("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void load();
  }, [unlocked]);

  if (!unlocked) {
    return (
      <p className="text-[10px] leading-relaxed text-zinc-500">
        API keys agents can use without seeing the secret. Sign in or paste
        the hub token in Settings.{" "}
        <button
          type="button"
          onClick={onOpenSettings}
          className="font-semibold text-orange-700 underline dark:text-orange-300"
        >
          Open Settings
        </button>
      </p>
    );
  }

  return (
    <div className="space-y-2 rounded-xl border border-zinc-200 p-3 dark:border-white/10">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-zinc-700 dark:text-zinc-200">
        <KeyRound className="h-3.5 w-3.5 text-orange-500" />
        Tool credentials
      </div>
      <p className="text-[10px] leading-relaxed text-zinc-500">
        API keys agents can use without seeing the secret. Agents call{" "}
        <code className="text-[9px]">tool_proxy</code> by name. The secret
        stays on the hub.
      </p>
      <label className="block text-[10px] font-medium text-zinc-500">
        Name
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="field-input mt-0.5 font-mono text-[10px]"
          placeholder="github"
        />
      </label>
      <label className="block text-[10px] font-medium text-zinc-500">
        Secret value
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="field-input mt-0.5 font-mono text-[10px]"
          placeholder="ghp_… (shown once never again)"
          autoComplete="off"
        />
      </label>
      <label className="block text-[10px] font-medium text-zinc-500">
        Allowed hosts (comma)
        <input
          value={hosts}
          onChange={(e) => setHosts(e.target.value)}
          className="field-input mt-0.5 font-mono text-[10px]"
          placeholder="api.github.com"
        />
      </label>
      <button
        type="button"
        disabled={busy || !name.trim() || !value.trim()}
        onClick={() => {
          void (async () => {
            setBusy(true);
            setErr("");
            setOk("");
            try {
              const allowed = hosts
                .split(",")
                .map((h) => h.trim())
                .filter(Boolean);
              await api.putToolCredential(name.trim(), {
                value: value.trim(),
                inject: "bearer",
                allowed_hosts: allowed,
              });
              setValue("");
              setOk(`Saved “${name.trim()}” (value never re-shown).`);
              await load();
            } catch (e) {
              setErr(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          })();
        }}
        className="w-full rounded-xl bg-zinc-900 px-3 py-2 text-[11px] font-semibold text-white dark:bg-zinc-100 dark:text-zinc-900 disabled:opacity-50"
      >
        {busy ? "Saving…" : "Save credential"}
      </button>
      {err ? (
        <p className="text-[10px] text-rose-600 dark:text-rose-300">{err}</p>
      ) : null}
      {ok ? (
        <p className="text-[10px] text-emerald-700 dark:text-emerald-300">{ok}</p>
      ) : null}
      <ul className="space-y-1">
        {rows.length === 0 ? (
          <li className="text-[10px] text-zinc-400">No credentials yet</li>
        ) : (
          rows.map((c) => (
            <li
              key={c.name}
              className="flex items-center justify-between gap-2 rounded-lg border border-zinc-100 px-2 py-1 dark:border-white/[0.06]"
            >
              <div className="min-w-0">
                <div className="truncate font-mono text-[11px] font-semibold">
                  {c.name}
                </div>
                <div className="truncate text-[9px] text-zinc-500">
                  {(c.allowed_hosts || []).join(", ") || "any host"} ·{" "}
                  {c.inject || "bearer"}
                </div>
              </div>
              <button
                type="button"
                className="text-zinc-400 hover:text-rose-500"
                title="Delete"
                onClick={() => {
                  if (!confirm(`Delete vault credential “${c.name}”?`)) return;
                  void api
                    .deleteToolCredential(c.name)
                    .then(load)
                    .catch((e) =>
                      setErr(e instanceof Error ? e.message : String(e))
                    );
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}

/** Path-addressed room workspace (shared collab FS). */
export function WorkspacePanel({
  hasAuthToken,
  requireAuth,
  onOpenSettings,
  roomId,
  updatedBy,
}: {
  hasAuthToken: boolean;
  requireAuth: boolean;
  onOpenSettings: () => void;
  roomId: string | null;
  updatedBy: string;
}) {
  const [files, setFiles] = useState<
    { path: string; bytes?: number; content_type?: string }[]
  >([]);
  const [path, setPath] = useState("docs/notes.md");
  const [content, setContent] = useState("# Notes\n");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const unlocked = hasAuthToken || !requireAuth;

  const load = async () => {
    if (!unlocked || !roomId) return;
    try {
      const r = await api.listWorkspace(roomId);
      setFiles(r.files || []);
      setErr("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    void load();
  }, [unlocked, roomId]);

  if (!unlocked) {
    return (
      <p className="text-[10px] leading-relaxed text-zinc-500">
        Shared files for this room. Sign in or paste the hub token in
        Settings.{" "}
        <button
          type="button"
          onClick={onOpenSettings}
          className="font-semibold text-orange-700 underline dark:text-orange-300"
        >
          Open Settings
        </button>
      </p>
    );
  }
  if (!roomId) {
    return (
      <p className="text-[10px] text-zinc-500">
        Open a room to list/write workspace files.
      </p>
    );
  }

  return (
    <div className="space-y-2 rounded-xl border border-zinc-200 p-3 dark:border-white/10">
      <p className="text-[10px] leading-relaxed text-zinc-500">
        Shared files for this room. Prefer paths over dumping files into chat.
        Agents: <code className="text-[9px]">workspace_write</code> /{" "}
        <code className="text-[9px]">workspace_read</code>.
      </p>
      <label className="block text-[10px] font-medium text-zinc-500">
        Path
        <input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          className="field-input mt-0.5 font-mono text-[10px]"
          placeholder="docs/plan.md"
        />
      </label>
      <label className="block text-[10px] font-medium text-zinc-500">
        Content
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={4}
          className="field-input mt-0.5 font-mono text-[10px]"
        />
      </label>
      <button
        type="button"
        disabled={busy || !path.trim()}
        onClick={() => {
          void (async () => {
            setBusy(true);
            setErr("");
            try {
              await api.writeWorkspace(roomId, path.trim().replace(/^\//, ""), {
                content,
                content_type: "text/plain",
                updated_by: updatedBy,
              });
              await load();
            } catch (e) {
              setErr(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          })();
        }}
        className="w-full rounded-xl border border-orange-500/40 bg-orange-500/10 px-3 py-1.5 text-[11px] font-semibold text-orange-950 dark:text-orange-100 disabled:opacity-50"
      >
        {busy ? "Writing…" : "Write file"}
      </button>
      {err ? (
        <p className="text-[10px] text-rose-600 dark:text-rose-300">{err}</p>
      ) : null}
      <ul className="max-h-36 space-y-1 overflow-y-auto">
        {files.length === 0 ? (
          <li className="text-[10px] text-zinc-400">Empty workspace</li>
        ) : (
          files.map((f) => (
            <li
              key={f.path}
              className="flex items-center justify-between gap-2 font-mono text-[10px] text-zinc-600 dark:text-zinc-300"
            >
              <span className="truncate">{f.path}</span>
              <span className="shrink-0 text-zinc-400">{f.bytes ?? "—"} B</span>
            </li>
          ))
        )}
      </ul>
    </div>
  );
}

/** Enable Web Push when VAPID is configured on the gateway. */
export function PushEnableButton({
  participantId,
}: {
  participantId: string | null;
}) {
  const [status, setStatus] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const enable = async () => {
    setBusy(true);
    setStatus("");
    try {
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
        setStatus("Push not supported in this browser");
        return;
      }
      const vapid = await api.pushVapid();
      if (!vapid.configured || !vapid.public_key) {
        setStatus(vapid.hint || "VAPID not configured on gateway");
        return;
      }
      const reg = await navigator.serviceWorker.register("/ui/sw.js", {
        scope: "/ui/",
      });
      await navigator.serviceWorker.ready;
      const perm = await Notification.requestPermission();
      if (perm !== "granted") {
        setStatus("Notification permission denied");
        return;
      }
      const key = urlBase64ToUint8Array(vapid.public_key);
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: key as BufferSource,
      });
      await api.pushSubscribe({
        subscription: sub.toJSON(),
        participant_id: participantId || undefined,
        device_label: "mobile-web",
      });
      setStatus("Push enabled for this device");
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1">
      <button
        type="button"
        disabled={busy}
        onClick={() => void enable()}
        className="w-full rounded-xl border border-zinc-200 px-3 py-2 text-xs font-semibold text-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-white/10 dark:text-zinc-200 dark:hover:bg-white/5"
      >
        {busy ? "Enabling…" : "Enable mobile push"}
      </button>
      {status && (
        <p className="text-[10px] leading-snug text-zinc-500">{status}</p>
      )}
    </div>
  );
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}
