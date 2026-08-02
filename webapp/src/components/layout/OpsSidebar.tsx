/**
 * Left nav: logo, refresh/new room, rooms, DMs, forks, gateways, settings
 */
import { useMemo, useState, type ReactNode } from "react";
import {
  ChevronDown,
  ChevronRight,
  GitFork,
  Globe2,
  Hash,
  MessageSquare,
  Network,
  Plus,
  RefreshCw,
  Settings2,
  Shield,
  Users,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { DmThread, Fork, Participant, Ping, Room } from "@/lib/types";

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
  onRoleChange: (role: string) => void;
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

function OnlineDot({ online, className }: { online: boolean; className?: string }) {
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 shrink-0 rounded-full",
        online
          ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]"
          : "bg-zinc-400 dark:bg-zinc-600",
        className
      )}
      title={online ? "Online" : "Offline"}
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
  onRoleChange,
  onAuthTokenChange,
}: Props) {
  const online = ping?.status === "ok";
  const [openRooms, setOpenRooms] = useState(true);
  const [openDms, setOpenDms] = useState(true);
  const [openForks, setOpenForks] = useState(true);
  const [openGateways, setOpenGateways] = useState(true);
  const [openSettings, setOpenSettings] = useState(true);

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
      byId.set(t.peer_id, {
        peer_id: t.peer_id,
        peer_name: t.peer_name || p?.name || t.peer_id.slice(0, 8),
        peer_harness: t.peer_harness || p?.harness || "other",
        peer_status: t.peer_status || p?.status || "offline",
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
        peer_role: p.role,
        last_at: p.last_seen_at,
        has_thread: false,
      });
    }
    return Array.from(byId.values()).sort((a, b) => {
      const ao = a.peer_status === "online" ? 0 : 1;
      const bo = b.peer_status === "online" ? 0 : 1;
      if (ao !== bo) return ao - bo;
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
    <aside className="flex h-full min-h-0 w-[300px] shrink-0 flex-col gap-3 border-r border-zinc-200 bg-gradient-to-b from-white to-zinc-50 px-3 py-3 dark:border-white/[0.06] dark:from-zinc-950 dark:to-zinc-950/95">
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

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-0.5">
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
            const isOn = t.peer_status === "online";
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
                <OnlineDot online={isOn} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{t.peer_name}</span>
                  <span className="block truncate text-[10px] text-zinc-500">
                    {isOn ? "online" : "offline"}
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
            <GatewayCardView key={g.id} g={g} />
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
            <GatewayCardView key={g.id} g={g} />
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
            <GatewayCardView key={g.id} g={g} />
          ))}
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
                onBlur={(e) => onNameChange(e.target.value.trim() || "human")}
                className="field-input mt-1"
                maxLength={40}
              />
            </label>

            <label className="block text-xs font-medium text-zinc-500">
              Role
              <input
                value={displayRole}
                onChange={(e) => onRoleChange(e.target.value)}
                onBlur={(e) =>
                  onRoleChange(e.target.value.trim() || "observer")
                }
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
              Auth token (public gateways)
              <input
                type="password"
                value={authToken}
                onChange={(e) => onAuthTokenChange(e.target.value)}
                placeholder="Bearer token if required"
                className="field-input mt-1 font-mono text-xs"
              />
            </label>
            <p className="text-[11px] leading-relaxed text-zinc-500">
              Use <strong>@all</strong> for everyone. DMs stay private — only
              you and the peer see them. Click a participant or DM row to open
              a private thread.
            </p>
          </div>
        </Accordion>
      </div>
    </aside>
  );
}

function GatewayCardView({ g }: { g: GatewayCard }) {
  const isTailnet = g.network === "tailscale";
  const isFunnel = g.network === "funnel";
  const isLan = g.network === "lan";
  const isLoopback = g.network === "loopback" || g.mode === "internal";
  const label = networkLabel(g.network);
  return (
    <div
      className={cn(
        "mb-1.5 rounded-lg border px-2.5 py-2",
        g.is_self
          ? "border-orange-500/30 bg-orange-500/5"
          : "border-zinc-200 dark:border-white/10"
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
        {g.name}
        {g.is_self && (
          <span className="rounded-full bg-orange-500/15 px-1.5 py-px text-[10px] font-medium text-orange-800 dark:text-orange-200">
            this
          </span>
        )}
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
      </div>
      {g.notes && (
        <div className="mt-1 text-[10px] text-zinc-500 line-clamp-2">{g.notes}</div>
      )}
    </div>
  );
}
