/**
 * Left nav: logo, refresh/new room, rooms, DMs, forks, gateways, settings
 */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  ChevronDown,
  ChevronRight,
  Copy,
  GitFork,
  Globe2,
  Hash,
  KeyRound,
  Lock,
  MessageSquare,
  Network,
  Plus,
  QrCode,
  RefreshCw,
  Settings2,
  Shield,
  Trash2,
  Users,
  X,
} from "lucide-react";
import QRCode from "qrcode";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { DmThread, Fork, Participant, Ping, Room } from "@/lib/types";
import { AgentInstallPanel } from "@/components/AgentInstallPanel";

export type GatewayCard = {
  id: string;
  name: string;
  mode: string;
  network: string;
  base_url: string;
  require_auth: boolean;
  is_self: boolean;
  notes?: string;
  metadata?: Record<string, unknown>;
};

export type LeftSection = "rooms" | "dms" | "forks" | "settings";

export type DmRow = {
  peer_id: string;
  peer_name: string;
  peer_harness: string;
  peer_status: string;
  /** listening | joined | offline — radio vs present */
  peer_presence?: string;
  peer_role?: string;
  last_at?: string | null;
  has_thread: boolean;
};

type Props = {
  rooms: Room[];
  activeRoomId: string | null;
  ping: Ping | null;
  displayName: string;
  displayRole: string;
  participantId: string | null;
  participants: Participant[];
  dmThreads: DmThread[];
  forks: Fork[];
  gateways: GatewayCard[];
  activeDmPeerId: string | null;
  section: LeftSection;
  collapsed: boolean;
  authToken: string;
  onToggleCollapsed: () => void;
  onSection: (s: LeftSection) => void;
  onSelectRoom: (id: string) => void;
  onSelectDm: (peerId: string | null) => void;
  onSelectFork: (forkId: string) => void;
  onRefresh: () => void;
  onNewRoom: () => void;
  onNameChange: (name: string) => void;
  onNameCommit: (name: string) => void;
  onRoleChange: (role: string) => void;
  onRoleCommit: (role: string) => void;
  onAuthTokenChange: (token: string) => void;
};

function Accordion({
  title,
  icon,
  open,
  onToggle,
  count,
  children,
}: {
  title: string;
  icon: ReactNode;
  open: boolean;
  onToggle: () => void;
  count?: number;
  children: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white dark:border-white/[0.06] dark:bg-zinc-900/50">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm font-semibold text-zinc-800 dark:text-zinc-100"
      >
        {open ? (
          <ChevronDown className="h-4 w-4 text-zinc-400" />
        ) : (
          <ChevronRight className="h-4 w-4 text-zinc-400" />
        )}
        <span className="text-zinc-500">{icon}</span>
        <span className="flex-1">{title}</span>
        {typeof count === "number" && (
          <span className="rounded-full bg-zinc-100 px-1.5 py-0.5 text-[11px] font-medium text-zinc-500 dark:bg-zinc-800">
            {count}
          </span>
        )}
      </button>
      {open && (
        <div className="border-t border-zinc-100 px-2 py-2 dark:border-white/5">
          {children}
        </div>
      )}
    </div>
  );
}

function OnlineDot({
  online,
  presence,
  className,
}: {
  online: boolean;
  presence?: string;
  className?: string;
}) {
  const mode =
    presence || (online ? "joined" : "offline");
  const listening = mode === "listening";
  const joined = mode === "joined" || (online && !listening && mode !== "offline");
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 shrink-0 rounded-full",
        listening &&
          "animate-pulse bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.85)]",
        joined &&
          !listening &&
          "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.6)]",
        !listening && !joined && "bg-zinc-400 dark:bg-zinc-600",
        className
      )}
      title={
        listening
          ? "Listening (radio on)"
          : joined
            ? "Joined but not listening"
            : "Offline"
      }
    />
  );
}

function networkLabel(network: string): string {
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

function networkBadgeClass(network: string): string {
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

export function OpsSidebar({
  rooms,
  activeRoomId,
  ping,
  displayName,
  displayRole,
  participantId,
  participants,
  dmThreads,
  forks,
  gateways,
  activeDmPeerId,
  section,
  collapsed,
  authToken,
  onToggleCollapsed,
  onSection,
  onSelectRoom,
  onSelectDm,
  onSelectFork,
  onRefresh,
  onNewRoom,
  onNameChange,
  onNameCommit,
  onRoleChange,
  onRoleCommit,
  onAuthTokenChange,
}: Props) {
  const online = ping?.status === "ok";
  const [openRooms, setOpenRooms] = useState(false);
  const [openDms, setOpenDms] = useState(false);
  const [openForks, setOpenForks] = useState(false);
  const [openGateways, setOpenGateways] = useState(false);
  // Public hubs: open Settings by default so auth + token minting is visible
  const [openSettings, setOpenSettings] = useState(
    () => Boolean(authToken) || Boolean(ping?.require_auth)
  );
  const [openTokens, setOpenTokens] = useState(true);
  const [openVault, setOpenVault] = useState(false);
  const [openWorkspace, setOpenWorkspace] = useState(false);
  const [pairGateway, setPairGateway] = useState<GatewayCard | null>(null);

  const internalGws = gateways.filter(
    (g) => g.mode === "internal" || g.network === "loopback"
  );
  const lanGws = gateways.filter((g) => g.network === "lan");
  const publicGws = gateways.filter(
    (g) =>
      g.network !== "loopback" &&
      g.network !== "lan" &&
      (g.mode === "public" || g.network === "tailscale" || g.network === "funnel" || g.network === "public")
  );

  /** All peers as DM rows: existing threads + online participants without a thread. */
  const dmRows: DmRow[] = useMemo(() => {
    const byId = new Map<string, DmRow>();
    for (const t of dmThreads) {
      if (!t.peer_id) continue;
      const p = participants.find((x) => x.id === t.peer_id);
      const presence =
        p?.presence ||
        ((t.peer_status === "online" || p?.status === "online")
          ? "joined"
          : "offline");
      byId.set(t.peer_id, {
        peer_id: t.peer_id,
        peer_name: t.peer_name || p?.name || t.peer_id.slice(0, 8),
        peer_harness: t.peer_harness || p?.harness || "other",
        peer_status: t.peer_status || p?.status || "offline",
        peer_presence: presence,
        peer_role: t.peer_role || p?.role,
        last_at: t.last_at,
        has_thread: true,
      });
    }
    for (const p of participants) {
      if (participantId && p.id === participantId) continue;
      if (byId.has(p.id)) {
        const row = byId.get(p.id)!;
        row.peer_status = p.status;
        row.peer_presence =
          p.presence || (p.status === "online" ? "joined" : "offline");
        row.peer_role = p.role;
        row.peer_name = p.name;
        row.peer_harness = p.harness;
        continue;
      }
      byId.set(p.id, {
        peer_id: p.id,
        peer_name: p.name,
        peer_harness: p.harness,
        peer_status: p.status,
        peer_presence:
          p.presence || (p.status === "online" ? "joined" : "offline"),
        peer_role: p.role,
        last_at: p.last_seen_at,
        has_thread: false,
      });
    }
    const rank = (pr?: string) =>
      pr === "listening" ? 0 : pr === "joined" ? 1 : 2;
    return Array.from(byId.values()).sort((a, b) => {
      const ar = rank(a.peer_presence);
      const br = rank(b.peer_presence);
      if (ar !== br) return ar - br;
      const at = a.last_at || "";
      const bt = b.last_at || "";
      return bt.localeCompare(at);
    });
  }, [dmThreads, participants, participantId]);

  if (collapsed) {
    return (
      <aside className="flex w-12 shrink-0 flex-col items-center gap-3 border-r border-zinc-200 bg-white py-3 dark:border-white/[0.06] dark:bg-zinc-950">
        <img
          src={`${import.meta.env.BASE_URL}og-logo.png`}
          alt="OG"
          className="og-logo h-9 w-9 rounded-lg"
        />
        <button
          type="button"
          onClick={onToggleCollapsed}
          className="rounded-lg border border-zinc-200 p-2 text-zinc-500 dark:border-white/10"
          title="Expand sidebar"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        <button type="button" onClick={onRefresh} className="p-2 text-zinc-500" title="Refresh">
          <RefreshCw className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={onNewRoom}
          className="p-2 text-orange-600"
          title="New room"
        >
          <Plus className="h-4 w-4" />
        </button>
      </aside>
    );
  }

  return (
    <aside className="flex h-full min-h-0 w-full shrink-0 flex-col gap-3 border-r border-zinc-200 bg-gradient-to-b from-white to-zinc-50 px-3 py-3 dark:border-white/[0.06] dark:from-zinc-950 dark:to-zinc-950/95 md:w-[300px]">
      <div className="flex items-center gap-2.5 px-1">
        <img
          src={`${import.meta.env.BASE_URL}og-logo.png`}
          alt="OpenGateway"
          className="og-logo h-10 w-10 rounded-xl"
        />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[15px] font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            OpenGateway
          </div>
          <div className="text-xs text-zinc-500">Multi-agent live ops</div>
        </div>
        <button
          type="button"
          onClick={onToggleCollapsed}
          className="rounded-lg border border-zinc-200 p-1.5 text-zinc-500 dark:border-white/10"
          title="Collapse sidebar"
        >
          <ChevronDown className="h-4 w-4 -rotate-90" />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-zinc-200 bg-white px-2.5 py-2 text-sm font-semibold text-zinc-700 transition hover:bg-zinc-50 dark:border-white/10 dark:bg-zinc-900 dark:text-zinc-200"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </button>
        <button
          type="button"
          onClick={onNewRoom}
          className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-gradient-to-b from-orange-400 to-orange-600 px-2.5 py-2 text-sm font-semibold text-orange-950 shadow-sm shadow-orange-500/20"
        >
          <Plus className="h-3.5 w-3.5" />
          New room
        </button>
      </div>

      {/* Always-visible entry: mint agent tokens (was easy to miss under collapsed Settings) */}
      <button
        type="button"
        onClick={() => {
          setOpenTokens(true);
          setOpenSettings(true);
          onSection("settings");
          document.getElementById("og-agent-tokens")?.scrollIntoView({
            behavior: "smooth",
            block: "nearest",
          });
        }}
        className="flex w-full items-center justify-center gap-2 rounded-xl border border-orange-500/40 bg-orange-500/10 px-3 py-2.5 text-sm font-semibold text-orange-950 dark:text-orange-100"
      >
        <Shield className="h-4 w-4" />
        Mint agent token
      </button>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-0.5">
        <Accordion
          title="Agent tokens"
          icon={<Shield className="h-4 w-4" />}
          open={openTokens}
          onToggle={() => setOpenTokens((v) => !v)}
        >
          <div id="og-agent-tokens" className="px-1 pb-1">
            <AgentTokensPanel
              gatewayBase={ping?.base_url || window.location.origin}
              hasAuthToken={Boolean(authToken?.trim())}
              requireAuth={Boolean(ping?.require_auth)}
            />
          </div>
        </Accordion>

        <Accordion
          title="Tool vault"
          icon={<Lock className="h-4 w-4" />}
          open={openVault}
          onToggle={() => setOpenVault((v) => !v)}
        >
          <div className="px-1 pb-1">
            <ToolVaultPanel hasAuthToken={Boolean(authToken?.trim())} />
          </div>
        </Accordion>

        <Accordion
          title="Room workspace"
          icon={<Globe2 className="h-4 w-4" />}
          open={openWorkspace}
          onToggle={() => setOpenWorkspace((v) => !v)}
        >
          <div className="px-1 pb-1">
            <WorkspacePanel
              hasAuthToken={Boolean(authToken?.trim())}
              roomId={activeRoomId}
              updatedBy={participantId || displayName}
            />
          </div>
        </Accordion>

        <Accordion
          title="Rooms"
          icon={<Hash className="h-4 w-4" />}
          open={openRooms}
          onToggle={() => setOpenRooms((v) => !v)}
          count={rooms.length}
        >
          {rooms.length === 0 && (
            <div className="px-2 py-3 text-center text-xs text-zinc-500">
              No rooms yet
            </div>
          )}
          {rooms.map((r) => {
            const active =
              r.id === activeRoomId && section === "rooms" && !activeDmPeerId;
            const n = r.participant_ids?.length ?? 0;
            return (
              <button
                key={r.id}
                type="button"
                onClick={() => {
                  onSection("rooms");
                  onSelectDm(null);
                  onSelectRoom(r.id);
                }}
                className={cn(
                  "mb-1 w-full rounded-lg border px-2.5 py-2 text-left transition",
                  active
                    ? "border-orange-500/35 bg-orange-500/10"
                    : "border-transparent hover:bg-zinc-100 dark:hover:bg-white/5"
                )}
              >
                <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                  {r.name}
                </div>
                <div className="mt-0.5 line-clamp-1 text-xs text-zinc-500">
                  {r.goal || "No goal"} · {n} agents
                </div>
              </button>
            );
          })}
        </Accordion>

        <Accordion
          title="Direct messages"
          icon={<MessageSquare className="h-4 w-4" />}
          open={openDms}
          onToggle={() => setOpenDms((v) => !v)}
          count={dmRows.length}
        >
          {!participantId && (
            <div className="px-2 py-2 text-xs text-zinc-500">
              Join a room to DM
            </div>
          )}
          {participantId && dmRows.length === 0 && (
            <div className="px-2 py-2 text-xs text-zinc-500">
              No peers yet. Agents appear here when they join.
            </div>
          )}
          {dmRows.map((t) => {
            const isOn = t.peer_status === "online" || t.peer_presence === "listening";
            const label =
              t.peer_presence === "listening"
                ? "listening"
                : t.peer_presence === "joined" || isOn
                  ? "joined"
                  : "offline";
            return (
              <button
                key={t.peer_id}
                type="button"
                onClick={() => {
                  onSection("dms");
                  onSelectDm(t.peer_id);
                }}
                className={cn(
                  "mb-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition",
                  activeDmPeerId === t.peer_id
                    ? "bg-violet-500/12 text-violet-900 dark:text-violet-100"
                    : "hover:bg-zinc-100 dark:hover:bg-white/5"
                )}
              >
                <OnlineDot online={isOn} presence={t.peer_presence} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{t.peer_name}</span>
                  <span className="block truncate text-[10px] text-zinc-500">
                    {label}
                    {t.peer_role ? ` · ${t.peer_role}` : ""}
                    {t.has_thread ? " · thread" : " · start chat"}
                  </span>
                </span>
                <span className="shrink-0 text-[10px] text-zinc-400">
                  {t.peer_harness}
                </span>
              </button>
            );
          })}
        </Accordion>

        <Accordion
          title="Forks"
          icon={<GitFork className="h-4 w-4" />}
          open={openForks}
          onToggle={() => setOpenForks((v) => !v)}
          count={forks.length}
        >
          {forks.length === 0 && (
            <div className="px-2 py-2 text-xs text-zinc-500">
              Fork a message from the chat to start a branch.
            </div>
          )}
          {forks.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => {
                onSection("forks");
                onSelectFork(f.id);
              }}
              className="mb-1 w-full rounded-lg px-2.5 py-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-white/5"
            >
              <div className="font-medium text-zinc-900 dark:text-zinc-100 line-clamp-2">
                {f.title}
              </div>
              <div className="mt-0.5 text-[11px] text-zinc-500">
                {f.created_by_name || "you"} ·{" "}
                {new Date(f.created_at).toLocaleString()}
              </div>
            </button>
          ))}
        </Accordion>

        <Accordion
          title="Gateways"
          icon={<Network className="h-4 w-4" />}
          open={openGateways}
          onToggle={() => setOpenGateways((v) => !v)}
          count={gateways.length}
        >
          <div className="mb-2 px-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-400">
            Internal
          </div>
          {internalGws.length === 0 && (
            <div className="px-2 py-1 text-xs text-zinc-500">
              No loopback gateway in this process.
            </div>
          )}
          {internalGws.map((g) => (
            <GatewayCardView
              key={g.id}
              g={g}
              onClick={() => setPairGateway(g)}
            />
          ))}
          <div className="mb-2 mt-3 px-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-400">
            LAN
          </div>
          {lanGws.length === 0 && (
            <div className="px-2 py-1 text-xs text-zinc-500">
              Open bind on LAN:{" "}
              <code className="text-[11px]">
                serve --mode public --via open
              </code>
            </div>
          )}
          {lanGws.map((g) => (
            <GatewayCardView
              key={g.id}
              g={g}
              onClick={() => setPairGateway(g)}
            />
          ))}
          <div className="mb-2 mt-3 px-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-400">
            Public / Tailscale
          </div>
          {publicGws.length === 0 && (
            <div className="px-2 py-2 text-xs text-zinc-500">
              <code className="text-[11px]">opengateway serve --mode serve</code>
              {" · "}
              Tailnet (Serve) or Funnel.
            </div>
          )}
          {publicGws.map((g) => (
            <GatewayCardView
              key={g.id}
              g={g}
              onClick={() => setPairGateway(g)}
            />
          ))}
          <p className="mt-2 px-1 text-[10px] leading-relaxed text-zinc-500">
            Tap a gateway card for phone pair QR:{" "}
            <strong>LAN</strong> = same Wi‑Fi · <strong>Tailnet</strong> = cellular
            OK (Tailscale app on).
          </p>
        </Accordion>

        <Accordion
          title="Settings"
          icon={<Settings2 className="h-4 w-4" />}
          open={openSettings}
          onToggle={() => setOpenSettings((v) => !v)}
        >
          <div className="space-y-3 px-1 pb-1">
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
                  ? `v${ping.version} · ${networkLabel(ping.network || "loopback")} · ${
                      ping.rooms
                    } rooms · ${ping.persistent ? "sqlite" : "memory"}`
                  : "—"}
              </div>
            </div>

            <label className="block text-xs font-medium text-zinc-500">
              Identity
              <input
                value={displayName}
                onChange={(e) => onNameChange(e.target.value)}
                onBlur={(e) => onNameCommit(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.currentTarget.blur();
                  }
                }}
                className="field-input mt-1"
                maxLength={64}
                placeholder="Your name (spaces OK)"
                autoComplete="nickname"
                spellCheck={false}
              />
              <span className="mt-1 block text-[10px] font-normal text-zinc-400">
                Spaces allowed · saved when you leave the field or press Enter
              </span>
            </label>

            <label className="block text-xs font-medium text-zinc-500">
              Role
              <input
                value={displayRole}
                onChange={(e) => onRoleChange(e.target.value)}
                onBlur={(e) => onRoleCommit(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.currentTarget.blur();
                  }
                }}
                placeholder="observer · coordinator · reviewer…"
                className="field-input mt-1"
                maxLength={48}
                list="og-roles"
              />
              <datalist id="og-roles">
                <option value="observer" />
                <option value="coordinator" />
                <option value="contributor" />
                <option value="reviewer" />
                <option value="facilitator" />
              </datalist>
            </label>

            <div className="font-mono text-[11px] text-zinc-500 break-all">
              {participantId ? `participant ${participantId}` : "not joined"}
            </div>

            <label className="block text-xs font-medium text-zinc-500">
              This browser&apos;s auth token
              <input
                type="password"
                value={authToken}
                onChange={(e) => onAuthTokenChange(e.target.value)}
                placeholder="Master or device token (Bearer)"
                className="field-input mt-1 font-mono text-xs"
              />
              <span className="mt-1 block text-[10px] font-normal text-zinc-400">
                This is your hub. If you started it with{" "}
                <code className="text-[9px]">--token</code> /{" "}
                <code className="text-[9px]">OPENGATEWAY_AUTH_TOKEN</code>, paste
                that same value so this browser can mint agent keys. Loopback
                with no token: leave empty. Railway / other cloud is optional —
                only paste a remote host&apos;s master token if this UI is that
                host.
              </span>
            </label>

            <PushEnableButton participantId={participantId} />
            <p className="text-[11px] leading-relaxed text-zinc-500">
              Use <strong>@all</strong> for everyone. DMs stay private — only
              you and the peer see them.
            </p>
          </div>
        </Accordion>
      </div>

      {pairGateway && (
        <PairQrModal
          gateway={pairGateway}
          roomId={activeRoomId}
          onClose={() => setPairGateway(null)}
        />
      )}
    </aside>
  );
}

function GatewayCardView({
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

function PairQrModal({
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

/**
 * Create / list / revoke agent & device API keys (production auth).
 * Master token in Settings can mint keys; scoped keys cannot.
 */
function AgentTokensPanel({
  gatewayBase,
  hasAuthToken,
  requireAuth,
}: {
  gatewayBase: string;
  hasAuthToken: boolean;
  requireAuth: boolean;
}) {
  const [keys, setKeys] = useState<
    {
      id: string;
      name: string;
      key_prefix: string;
      scopes: string[];
      role: string;
      device_label: string;
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
      setKeys(res.keys || []);
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
        Agent &amp; device tokens
      </div>
      <p className="text-[10px] leading-relaxed text-zinc-500">
        {requireAuth
          ? "Mint a scoped token per agent or phone. Paste it into MCP as OPENGATEWAY_AUTH_TOKEN. Shown once."
          : "This hub is yours on this machine — mint a key per agent here. No Railway token. Shown once."}{" "}
        Agents use the minted key, not the hub master token.
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
function ToolVaultPanel({ hasAuthToken }: { hasAuthToken: boolean }) {
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

  const load = async () => {
    if (!hasAuthToken) return;
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
  }, [hasAuthToken]);

  if (!hasAuthToken) {
    return (
      <p className="text-[10px] text-zinc-500">Connect hub to manage vault.</p>
    );
  }

  return (
    <div className="space-y-2 rounded-xl border border-zinc-200 p-3 dark:border-white/10">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-zinc-700 dark:text-zinc-200">
        <KeyRound className="h-3.5 w-3.5 text-orange-500" />
        Tool credentials
      </div>
      <p className="text-[10px] leading-relaxed text-zinc-500">
        Store third-party API keys on the hub. Agents call{" "}
        <code className="text-[9px]">tool_proxy</code> by name — secrets never
        enter agent env.
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
function WorkspacePanel({
  hasAuthToken,
  roomId,
  updatedBy,
}: {
  hasAuthToken: boolean;
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

  const load = async () => {
    if (!hasAuthToken || !roomId) return;
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
  }, [hasAuthToken, roomId]);

  if (!hasAuthToken) {
    return (
      <p className="text-[10px] text-zinc-500">Connect hub to use workspace.</p>
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
        Shared path FS for this room. Prefer paths over dumping files into chat.
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
function PushEnableButton({
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
