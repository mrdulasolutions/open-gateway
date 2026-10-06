/**
 * Hub settings sheet (identity, People, gateways, vault, API keys).
 */
import { useMemo, useState } from "react";
import { Check, Copy, Eye, EyeOff, Network, Settings2, Shield, X } from "lucide-react";
import type { Ping } from "@/lib/types";
import { cn } from "@/lib/utils";
import type { GatewayCard } from "@/components/layout/OpsSidebar";
import {
  AgentTokensPanel,
  GatewayCardView,
  PairQrModal,
  PushEnableButton,
  TeamInvitesPanel,
  ToolVaultPanel,
  networkBadgeClass,
  networkLabel,
} from "@/components/layout/opsSidebarPanels";

type Props = {
  open: boolean;
  onClose: () => void;
  ping: Ping | null;
  online: boolean;
  displayName: string;
  displayRole: string;
  participantId: string | null;
  authToken: string;
  gateways: GatewayCard[];
  activeRoomId: string | null;
  onNameChange: (name: string) => void;
  onNameCommit: (name: string) => void;
  onRoleChange: (role: string) => void;
  onRoleCommit: (role: string) => void;
  onAuthTokenChange: (token: string) => void;
  roomHint: string;
};

export function OpsSettingsSheet({
  open,
  onClose,
  ping,
  online,
  displayName,
  displayRole,
  participantId,
  authToken,
  gateways,
  activeRoomId,
  onNameChange,
  onNameCommit,
  onRoleChange,
  onRoleCommit,
  onAuthTokenChange,
  roomHint,
}: Props) {
  const [pairGateway, setPairGateway] = useState<GatewayCard | null>(null);
  const [tab, setTab] = useState<
    "general" | "people" | "gateways" | "vault" | "keys"
  >("general");
  const [tokenVisible, setTokenVisible] = useState(false);
  const [tokenCopied, setTokenCopied] = useState(false);

  const internalGws = useMemo(
    () => gateways.filter((g) => g.mode === "internal" || g.network === "loopback"),
    [gateways]
  );
  const lanGws = useMemo(
    () => gateways.filter((g) => g.network === "lan"),
    [gateways]
  );
  const publicGws = useMemo(
    () =>
      gateways.filter(
        (g) =>
          g.network !== "loopback" &&
          g.network !== "lan" &&
          (g.mode === "public" ||
            g.network === "tailscale" ||
            g.network === "funnel" ||
            g.network === "public")
      ),
    [gateways]
  );

  if (!open) return null;

  const tabs: { id: typeof tab; label: string }[] = [
    { id: "general", label: "General" },
    { id: "people", label: "People" },
    { id: "gateways", label: "Gateways" },
    { id: "vault", label: "Vault" },
    { id: "keys", label: "API keys" },
  ];

  return (
    <>
      <div
        className="fixed inset-0 z-[70] bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden
      />
      <div
        role="dialog"
        aria-label="Settings"
        className="fixed inset-y-0 left-0 z-[71] flex w-full max-w-md flex-col border-r border-zinc-200 bg-white shadow-2xl dark:border-white/10 dark:bg-zinc-950"
      >
        <div className="flex items-center gap-2 border-b border-zinc-200 px-4 py-3 dark:border-white/10">
          <Settings2 className="h-4 w-4 text-orange-600" />
          <h2 className="flex-1 text-base font-semibold">Settings</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-white/10"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex gap-1 overflow-x-auto border-b border-zinc-100 px-2 py-2 dark:border-white/5">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={cn(
                "shrink-0 rounded-lg px-2.5 py-1.5 text-xs font-semibold transition",
                tab === t.id
                  ? "bg-orange-500/15 text-orange-950 dark:text-orange-50"
                  : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-white/5"
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {tab === "general" && (
            <div className="space-y-3">
              <div className="rounded-lg border border-zinc-200 bg-zinc-50 px-3 py-2.5 dark:border-white/10 dark:bg-zinc-950">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <span
                    className={cn(
                      "h-1.5 w-1.5 rounded-full",
                      online ? "bg-emerald-500" : "bg-rose-400"
                    )}
                  />
                  {online ? "Gateway online" : "Gateway offline"}
                </div>
                <div className="mt-1 font-mono text-[11px] text-zinc-500 break-all">
                  {ping
                    ? `v${ping.version} · ${networkLabel(ping.network || "loopback")} · ${ping.rooms} rooms · ${ping.persistent ? "sqlite" : "memory"}`
                    : "—"}
                </div>
              </div>
              <label className="block text-xs font-medium text-orange-950 dark:text-orange-50">
                Identity
                <input
                  value={displayName}
                  onChange={(e) => onNameChange(e.target.value)}
                  onBlur={(e) => onNameCommit(e.target.value)}
                  className="field-input mt-1"
                  maxLength={64}
                  autoComplete="nickname"
                />
              </label>
              <label className="block text-xs font-medium text-orange-950 dark:text-orange-50">
                Role
                <input
                  value={displayRole}
                  onChange={(e) => onRoleChange(e.target.value)}
                  onBlur={(e) => onRoleCommit(e.target.value)}
                  className="field-input mt-1"
                  maxLength={48}
                  list="og-roles-sheet"
                />
                <datalist id="og-roles-sheet">
                  <option value="observer" />
                  <option value="coordinator" />
                  <option value="contributor" />
                </datalist>
              </label>
              <div className="font-mono text-[11px] text-zinc-500 break-all">
                {participantId ? `participant ${participantId}` : "not joined"}
              </div>
              <label className="block text-xs font-medium text-orange-950 dark:text-orange-50">
                Hub auth token
                <div className="relative mt-1">
                  <input
                    type={tokenVisible ? "text" : "password"}
                    value={authToken}
                    onChange={(e) => onAuthTokenChange(e.target.value)}
                    className="field-input w-full pr-[4.5rem] font-mono text-xs"
                    autoComplete="off"
                    spellCheck={false}
                  />
                  <div className="absolute inset-y-0 right-1 flex items-center gap-0.5">
                    <button
                      type="button"
                      onClick={() => setTokenVisible((v) => !v)}
                      className="rounded-md p-1.5 text-zinc-500 transition hover:bg-zinc-200/80 hover:text-zinc-800 dark:hover:bg-white/10 dark:hover:text-zinc-200"
                      title={tokenVisible ? "Hide token" : "Show token"}
                      aria-label={tokenVisible ? "Hide token" : "Show token"}
                    >
                      {tokenVisible ? (
                        <EyeOff className="h-4 w-4" />
                      ) : (
                        <Eye className="h-4 w-4" />
                      )}
                    </button>
                    <button
                      type="button"
                      disabled={!authToken.trim()}
                      onClick={async () => {
                        if (!authToken.trim()) return;
                        await navigator.clipboard.writeText(authToken);
                        setTokenCopied(true);
                        window.setTimeout(() => setTokenCopied(false), 1500);
                      }}
                      className="rounded-md p-1.5 text-zinc-500 transition hover:bg-zinc-200/80 hover:text-zinc-800 disabled:opacity-40 dark:hover:bg-white/10 dark:hover:text-zinc-200"
                      title="Copy token"
                      aria-label="Copy token"
                    >
                      {tokenCopied ? (
                        <Check className="h-4 w-4 text-emerald-600" />
                      ) : (
                        <Copy className="h-4 w-4" />
                      )}
                    </button>
                  </div>
                </div>
                <span className="mt-1 block text-[10px] font-normal text-zinc-500">
                  Same value as{" "}
                  <code className="text-[9px]">OPENGATEWAY_AUTH_TOKEN</code> when
                  you started the hub.
                </span>
              </label>
              <PushEnableButton participantId={participantId} />
            </div>
          )}
          {tab === "people" && (
            <TeamInvitesPanel
              hasAuthToken={Boolean(authToken?.trim())}
              requireAuth={Boolean(ping?.require_auth)}
              onOpenSettings={() => setTab("general")}
            />
          )}
          {tab === "gateways" && (
            <div className="space-y-3">
              <p className="text-[11px] text-zinc-500">
                Tap a card for phone pair QR.{" "}
                <span className={cn("rounded border px-1", networkBadgeClass("lan"))}>
                  LAN
                </span>{" "}
                same Wi‑Fi ·{" "}
                <span className={cn("rounded border px-1", networkBadgeClass("tailscale"))}>
                  Tailnet
                </span>{" "}
                cellular + Tailscale.
              </p>
              <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-400">
                Internal
              </div>
              {internalGws.map((g) => (
                <GatewayCardView key={g.id} g={g} onClick={() => setPairGateway(g)} />
              ))}
              <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-400">
                LAN
              </div>
              {lanGws.map((g) => (
                <GatewayCardView key={g.id} g={g} onClick={() => setPairGateway(g)} />
              ))}
              <div className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-400">
                <Shield className="h-3 w-3" />
                Public / Tailscale
              </div>
              {publicGws.map((g) => (
                <GatewayCardView key={g.id} g={g} onClick={() => setPairGateway(g)} />
              ))}
              {gateways.length === 0 && (
                <div className="flex items-center gap-2 text-xs text-zinc-500">
                  <Network className="h-4 w-4" />
                  No gateway cards yet.
                </div>
              )}
            </div>
          )}
          {tab === "vault" && (
            <ToolVaultPanel
              hasAuthToken={Boolean(authToken?.trim())}
              requireAuth={Boolean(ping?.require_auth)}
              onOpenSettings={() => setTab("general")}
            />
          )}
          {tab === "keys" && (
            <div id="og-api-keys">
              <AgentTokensPanel
                gatewayBase={ping?.base_url || window.location.origin}
                hasAuthToken={Boolean(authToken?.trim())}
                requireAuth={Boolean(ping?.require_auth)}
                roomHint={roomHint}
              />
            </div>
          )}
        </div>
      </div>
      {pairGateway && (
        <PairQrModal
          gateway={pairGateway}
          roomId={activeRoomId}
          onClose={() => setPairGateway(null)}
        />
      )}
    </>
  );
}
