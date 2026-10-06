/**
 * Slack-style left nav: Now strip, rooms, DMs, agents, forks, identity bar.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArchiveRestore,
  Bot,
  GitFork,
  Hash,
  MessageSquare,
  MoreHorizontal,
  Plus,
  Settings2,
  Star,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { AppNotification } from "@/components/NotificationBell";
import type { Fork, ManagedAgent, Participant, Ping, Room } from "@/lib/types";
import { loadPinnedRoomIds, togglePinnedRoom } from "@/lib/pinnedRooms";
import { AddAgentWizard } from "@/components/AddAgentWizard";
import { ManagedAgentsPanel } from "@/components/ManagedAgentsPanel";
import { OpsSettingsSheet } from "@/components/layout/OpsSettingsSheet";
import type { GatewayCard, LeftSection } from "@/components/layout/OpsSidebar";
import { networkLabel } from "@/components/layout/opsSidebarPanels";

export type DmRow = {
  peer_id: string;
  peer_name: string;
  peer_harness: string;
  peer_status: string;
  peer_presence?: string;
  peer_role?: string;
  last_at?: string | null;
  has_thread: boolean;
};

type NavFocusId =
  | { kind: "now"; agentId: string; roomId: string }
  | { kind: "room"; roomId: string }
  | { kind: "dm"; peerId: string };

function OnlineDot({
  online,
  presence,
}: {
  online: boolean;
  presence?: string;
}) {
  const mode = presence || (online ? "joined" : "offline");
  const listening = mode === "listening";
  const joined = mode === "joined" || (online && !listening && mode !== "offline");
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 shrink-0 rounded-full",
        listening && "animate-pulse bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.85)]",
        joined && !listening && "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.6)]",
        !listening && !joined && "bg-zinc-400 dark:bg-zinc-600"
      )}
    />
  );
}

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-[3px]" aria-hidden>
      <style>{`
        @keyframes og-nav-dot {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.35; }
          30% { transform: translateY(-3px); opacity: 1; }
        }
        .og-nav-dot { width: 4px; height: 4px; border-radius: 999px; background: currentColor;
          animation: og-nav-dot 1.4s infinite ease-in-out; display: inline-block; }
        .og-nav-dot:nth-child(2) { animation-delay: 0.16s; }
        .og-nav-dot:nth-child(3) { animation-delay: 0.32s; }
      `}</style>
      <span className="og-nav-dot" />
      <span className="og-nav-dot" />
      <span className="og-nav-dot" />
    </span>
  );
}

function activityByRoom(agent: ManagedAgent): Record<string, string> {
  const raw = agent.metadata?.activity_by_room;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(raw)) {
      if (typeof v === "string" && v.trim()) out[k] = v;
    }
    if (Object.keys(out).length) return out;
  }
  if (
    String(agent.metadata?.activity || "").toLowerCase() === "thinking" &&
    agent.room_id
  ) {
    return { [agent.room_id]: "thinking" };
  }
  return {};
}

export type LiveOpsNavProps = {
  rooms: Room[];
  activeRoomId: string | null;
  ping: Ping | null;
  displayName: string;
  displayRole: string;
  participantId: string | null;
  participants: Participant[];
  dmRows: DmRow[];
  forks: Fork[];
  gateways: GatewayCard[];
  managedAgents: ManagedAgent[];
  notifications: AppNotification[];
  activeDmPeerId: string | null;
  section: LeftSection;
  authToken: string;
  showArchivedRooms: boolean;
  onShowArchivedRoomsChange: (value: boolean) => void;
  onSection: (s: LeftSection) => void;
  onSelectRoom: (id: string) => void;
  onSelectDm: (peerId: string | null) => void;
  onSelectFork: (forkId: string) => void;
  onNewRoom: () => void;
  onArchiveRoom: (roomId: string) => void;
  onUnarchiveRoom: (roomId: string) => void;
  onNameChange: (name: string) => void;
  onNameCommit: (name: string) => void;
  onRoleChange: (role: string) => void;
  onRoleCommit: (role: string) => void;
  onAuthTokenChange: (token: string) => void;
  onMarkRoomRead?: (roomId: string) => void;
  addAgentOpen?: boolean;
  onAddAgentOpenChange?: (open: boolean) => void;
  onRefresh: () => void;
};

export function LiveOpsNav({
  rooms,
  activeRoomId,
  ping,
  displayName,
  displayRole,
  participantId,
  participants,
  dmRows,
  forks,
  gateways,
  managedAgents,
  notifications,
  activeDmPeerId,
  section,
  authToken,
  showArchivedRooms,
  onShowArchivedRoomsChange,
  onSection,
  onSelectRoom,
  onSelectDm,
  onSelectFork,
  onNewRoom,
  onArchiveRoom,
  onUnarchiveRoom,
  onNameChange,
  onNameCommit,
  onRoleChange,
  onRoleCommit,
  onAuthTokenChange,
  onMarkRoomRead,
  addAgentOpen,
  onAddAgentOpenChange,
  onRefresh,
}: LiveOpsNavProps) {
  const [focusIndex, setFocusIndex] = useState(-1);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [manageAgentsOpen, setManageAgentsOpen] = useState(false);
  const [roomMenuOpen, setRoomMenuOpen] = useState(false);
  const [pinnedIds, setPinnedIds] = useState<string[]>(() => loadPinnedRoomIds());
  const [showAddAgentInternal, setShowAddAgentInternal] = useState(false);
  const showAddAgent = addAgentOpen ?? showAddAgentInternal;
  const setShowAddAgent = onAddAgentOpenChange ?? setShowAddAgentInternal;
  const [managedRefreshKey, setManagedRefreshKey] = useState(0);

  const managedChanged = () => {
    setManagedRefreshKey((v) => v + 1);
    onRefresh();
  };

  const roomNameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of rooms) m.set(r.id, r.name);
    return m;
  }, [rooms]);

  const nowItems = useMemo(() => {
    const items: {
      agentId: string;
      agentName: string;
      roomId: string;
      roomName: string;
    }[] = [];
    for (const agent of managedAgents) {
      const byRoom = activityByRoom(agent);
      for (const [rid, act] of Object.entries(byRoom)) {
        if (act.toLowerCase() !== "thinking") continue;
        items.push({
          agentId: agent.id,
          agentName: agent.name,
          roomId: rid,
          roomName: roomNameById.get(rid) || rid.slice(0, 8),
        });
      }
    }
    return items;
  }, [managedAgents, roomNameById]);

  const mentionCountByRoom = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of notifications) {
      if (n.read || n.kind !== "mention" || !n.roomId) continue;
      m.set(n.roomId, (m.get(n.roomId) || 0) + 1);
    }
    return m;
  }, [notifications]);

  const dmCountByPeer = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of notifications) {
      if (n.read || n.kind !== "dm" || !n.peerId) continue;
      m.set(n.peerId, (m.get(n.peerId) || 0) + 1);
    }
    return m;
  }, [notifications]);

  const sortedRooms = useMemo(() => {
    const pinSet = new Set(pinnedIds);
    const list = [...rooms];
    list.sort((a, b) => {
      const ap = pinSet.has(a.id) ? 0 : 1;
      const bp = pinSet.has(b.id) ? 0 : 1;
      if (ap !== bp) return ap - bp;
      return a.name.localeCompare(b.name);
    });
    return list;
  }, [rooms, pinnedIds]);

  const dmThreadRows = useMemo(
    () => dmRows.filter((r) => r.has_thread),
    [dmRows]
  );
  const dmStartPeers = useMemo(
    () => dmRows.filter((r) => !r.has_thread),
    [dmRows]
  );

  const focusables: NavFocusId[] = useMemo(() => {
    const out: NavFocusId[] = [];
    for (const item of nowItems) {
      out.push({ kind: "now", agentId: item.agentId, roomId: item.roomId });
    }
    for (const r of sortedRooms) {
      if (r.status === "archived" && !showArchivedRooms) continue;
      out.push({ kind: "room", roomId: r.id });
    }
    for (const d of dmThreadRows) {
      out.push({ kind: "dm", peerId: d.peer_id });
    }
    return out;
  }, [nowItems, sortedRooms, showArchivedRooms, dmThreadRows]);

  const activateFocus = useCallback(
    (item: NavFocusId) => {
      if (item.kind === "now" || item.kind === "room") {
        onSection("rooms");
        onSelectDm(null);
        onSelectRoom(item.roomId);
        onMarkRoomRead?.(item.roomId);
      } else {
        onSection("dms");
        onSelectDm(item.peerId);
      }
    },
    [onMarkRoomRead, onSection, onSelectDm, onSelectRoom]
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement;
      if (
        el instanceof HTMLTextAreaElement ||
        el instanceof HTMLInputElement ||
        (el instanceof HTMLElement && el.isContentEditable)
      ) {
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (!focusables.length) return;
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFocusIndex((i) =>
          i < 0 ? 0 : Math.min(focusables.length - 1, i + 1)
        );
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setFocusIndex((i) => (i <= 0 ? 0 : i - 1));
      } else if (e.key === "Enter" && focusIndex >= 0) {
        e.preventDefault();
        const item = focusables[focusIndex];
        if (item) activateFocus(item);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focusables, focusIndex, activateFocus]);

  const focusIdxFor = (match: (f: NavFocusId) => boolean) =>
    focusables.findIndex(match);

  const initials =
    (displayName || "?").trim().split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase() || "").join("") ||
    "?";

  const openRoom = (id: string) => {
    onSection("rooms");
    onSelectDm(null);
    onSelectRoom(id);
    onMarkRoomRead?.(id);
  };

  return (
    <>
      <aside className="flex h-full min-h-0 w-full shrink-0 flex-col border-r border-zinc-200/80 bg-zinc-100/90 dark:border-white/[0.06] dark:bg-zinc-950 md:w-[260px]">
        <div className="border-b border-zinc-200/80 px-3 py-3 dark:border-white/[0.06]">
          <div className="flex items-center gap-2.5">
            <img
              src={`${import.meta.env.BASE_URL}og-logo.png`}
              alt=""
              className="og-logo h-9 w-9 rounded-lg"
            />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-50">
                OpenGateway
              </div>
              <div className="truncate text-[11px] text-zinc-500">
                {ping ? networkLabel(ping.network || "loopback") : "—"}
                {ping?.version ? ` · v${ping.version}` : ""}
              </div>
            </div>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
          {nowItems.length > 0 && (
            <div className="mb-3">
              <div className="px-2 pb-1 text-[10px] font-bold uppercase tracking-wide text-orange-800/80 dark:text-orange-200/80">
                Now
              </div>
              {nowItems.map((item) => {
                const fi = focusIdxFor(
                  (f) =>
                    f.kind === "now" &&
                    f.agentId === item.agentId &&
                    f.roomId === item.roomId
                );
                return (
                  <button
                    key={`${item.agentId}-${item.roomId}`}
                    type="button"
                    onClick={() =>
                      openRoom(item.roomId)
                    }
                    className={cn(
                      "mb-0.5 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition",
                      fi === focusIndex
                        ? "bg-orange-500/20 ring-1 ring-orange-500/40"
                        : "hover:bg-orange-500/10"
                    )}
                  >
                    <span className="text-orange-800 dark:text-orange-100">
                      <TypingDots />
                    </span>
                    <span className="min-w-0 flex-1 truncate font-medium text-zinc-900 dark:text-zinc-100">
                      {item.agentName}
                      <span className="font-normal text-zinc-500"> · thinking · </span>
                      #{item.roomName}
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          <div className="mb-3">
            <div className="flex items-center justify-between px-2 pb-1">
              <span className="text-[10px] font-bold uppercase tracking-wide text-zinc-500">
                Rooms
              </span>
              <div className="flex items-center gap-0.5">
                <button
                  type="button"
                  title="Room options"
                  onClick={() => setRoomMenuOpen((v) => !v)}
                  className="rounded p-1 text-zinc-500 hover:bg-zinc-200/80 dark:hover:bg-white/10"
                >
                  <MoreHorizontal className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  title="New room"
                  onClick={onNewRoom}
                  className="rounded p-1 text-orange-700 hover:bg-orange-500/15 dark:text-orange-200"
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            {roomMenuOpen && (
              <label className="mb-1 flex cursor-pointer items-center gap-2 px-2 text-[11px] text-zinc-600 dark:text-zinc-400">
                <input
                  type="checkbox"
                  checked={showArchivedRooms}
                  onChange={(e) => onShowArchivedRoomsChange(e.target.checked)}
                />
                Show archived
              </label>
            )}
            {sortedRooms.map((r) => {
              const archived = r.status === "archived";
              if (archived && !showArchivedRooms) return null;
              const active =
                r.id === activeRoomId && section === "rooms" && !activeDmPeerId;
              const pinned = pinnedIds.includes(r.id);
              const mentions = mentionCountByRoom.get(r.id) || 0;
              const fi = focusIdxFor(
                (f) => f.kind === "room" && f.roomId === r.id
              );
              return (
                <div
                  key={r.id}
                  className={cn(
                    "group relative mb-0.5 flex items-stretch rounded-md transition",
                    active && "bg-orange-500/12",
                    fi === focusIndex && "ring-1 ring-orange-500/35",
                    !active && "hover:bg-zinc-200/60 dark:hover:bg-white/5"
                  )}
                >
                  {active && (
                    <span className="absolute left-0 top-1 bottom-1 w-0.5 rounded-full bg-orange-500" />
                  )}
                  <button
                    type="button"
                    disabled={archived}
                    onClick={() => openRoom(r.id)}
                    className="min-w-0 flex-1 py-1.5 pl-3 pr-1 text-left"
                  >
                    <div className="flex items-center gap-1.5 text-sm font-medium text-zinc-900 dark:text-zinc-100">
                      <Hash className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
                      <span className="truncate">{r.name}</span>
                      {pinned && (
                        <Star className="h-3 w-3 shrink-0 fill-amber-400 text-amber-500" />
                      )}
                      {mentions > 0 && (
                        <span className="ml-auto shrink-0 rounded-full bg-orange-500 px-1.5 py-px text-[10px] font-bold text-white">
                          {mentions}
                        </span>
                      )}
                    </div>
                    <div className="pl-5 text-[10px] text-zinc-500">
                      {r.participant_ids?.length ?? 0} in room
                    </div>
                  </button>
                  <button
                    type="button"
                    title={pinned ? "Unpin" : "Pin"}
                    onClick={() => {
                      const next = togglePinnedRoom(r.id);
                      setPinnedIds(next);
                    }}
                    className="self-center rounded p-1 opacity-0 group-hover:opacity-100 text-zinc-400 hover:text-amber-600"
                  >
                    <Star className={cn("h-3.5 w-3.5", pinned && "fill-amber-400 text-amber-500 opacity-100")} />
                  </button>
                  <button
                    type="button"
                    title={archived ? "Restore" : "Archive"}
                    className="self-center rounded p-1 opacity-0 group-hover:opacity-100 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
                    onClick={() =>
                      archived ? onUnarchiveRoom(r.id) : onArchiveRoom(r.id)
                    }
                  >
                    {archived ? (
                      <ArchiveRestore className="h-3.5 w-3.5" />
                    ) : (
                      <Archive className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
              );
            })}
          </div>

          <div className="mb-3">
            <div className="px-2 pb-1 text-[10px] font-bold uppercase tracking-wide text-zinc-500">
              Direct messages
            </div>
            {!participantId && (
              <p className="px-2 text-[11px] text-zinc-500">Join a room to DM</p>
            )}
            {dmThreadRows.map((t) => {
              const dmCount = dmCountByPeer.get(t.peer_id) || 0;
              const fi = focusIdxFor(
                (f) => f.kind === "dm" && f.peerId === t.peer_id
              );
              return (
                <button
                  key={t.peer_id}
                  type="button"
                  onClick={() => {
                    onSection("dms");
                    onSelectDm(t.peer_id);
                  }}
                  className={cn(
                    "mb-0.5 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition",
                    activeDmPeerId === t.peer_id
                      ? "bg-violet-500/12"
                      : "hover:bg-zinc-200/60 dark:hover:bg-white/5",
                    fi === focusIndex && "ring-1 ring-violet-500/35"
                  )}
                >
                  <OnlineDot
                    online={
                      t.peer_status === "online" ||
                      t.peer_presence === "listening"
                    }
                    presence={t.peer_presence}
                  />
                  <span className="min-w-0 flex-1 truncate font-medium">
                    {t.peer_name}
                  </span>
                  {dmCount > 0 && (
                    <span className="rounded-full bg-orange-500 px-1.5 py-px text-[10px] font-bold text-white">
                      {dmCount}
                    </span>
                  )}
                </button>
              );
            })}
            {dmStartPeers.length > 0 && (
              <div className="mt-1 border-t border-zinc-200/80 pt-1 dark:border-white/5">
                <p className="px-2 pb-0.5 text-[9px] font-semibold uppercase text-zinc-400">
                  Start
                </p>
                {dmStartPeers.slice(0, 6).map((t) => (
                  <button
                    key={t.peer_id}
                    type="button"
                    onClick={() => {
                      onSection("dms");
                      onSelectDm(t.peer_id);
                    }}
                    className="mb-0.5 flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs text-zinc-600 hover:bg-zinc-200/60 dark:text-zinc-400 dark:hover:bg-white/5"
                  >
                    <OnlineDot online={t.peer_status === "online"} presence={t.peer_presence} />
                    <span className="truncate">{t.peer_name}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="mb-3">
            <div className="flex items-center justify-between px-2 pb-1">
              <span className="text-[10px] font-bold uppercase tracking-wide text-zinc-500">
                Agents
              </span>
              <div className="flex gap-0.5">
                <button
                  type="button"
                  title="Manage agents"
                  onClick={() => setManageAgentsOpen(true)}
                  className="rounded px-1.5 py-0.5 text-[10px] font-semibold text-zinc-500 hover:bg-zinc-200/80 dark:hover:bg-white/10"
                >
                  Manage
                </button>
                <button
                  type="button"
                  title="Add agent"
                  onClick={() => setShowAddAgent(true)}
                  className="rounded p-1 text-orange-700 hover:bg-orange-500/15 dark:text-orange-200"
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
            {managedAgents.length === 0 && (
              <p className="px-2 text-[11px] text-zinc-500">No managed agents yet</p>
            )}
            {managedAgents.map((agent) => {
              const roomIds =
                agent.room_ids?.length ? agent.room_ids : [agent.room_id];
              const byRoom = activityByRoom(agent);
              const status = String(agent.status || agent.state || "unknown");
              return (
                <div
                  key={agent.id}
                  className="mb-2 rounded-md px-2 py-1.5 hover:bg-zinc-200/50 dark:hover:bg-white/[0.04]"
                >
                  <div className="flex items-center gap-2">
                    <Bot className="h-3.5 w-3.5 text-orange-600" />
                    <span className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                      {agent.name}
                    </span>
                    <span className="ml-auto text-[10px] text-zinc-500">{status}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1 pl-5">
                    {roomIds.map((rid) => {
                      const thinking =
                        byRoom[rid]?.toLowerCase() === "thinking";
                      const rname = roomNameById.get(rid) || rid.slice(0, 6);
                      return (
                        <button
                          key={rid}
                          type="button"
                          onClick={() => openRoom(rid)}
                          className={cn(
                            "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium transition",
                            thinking
                              ? "border-orange-500/40 bg-orange-500/15 text-orange-900 dark:text-orange-100"
                              : "border-zinc-200 bg-white text-zinc-700 hover:border-orange-500/30 dark:border-white/10 dark:bg-zinc-900 dark:text-zinc-300"
                          )}
                        >
                          {thinking && (
                            <span className="text-orange-800 dark:text-orange-100">
                              <TypingDots />
                            </span>
                          )}
                          #{rname}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>

          {forks.length > 0 && (
            <div className="mb-3">
              <div className="flex items-center gap-1 px-2 pb-1 text-[10px] font-bold uppercase tracking-wide text-zinc-500">
                <GitFork className="h-3 w-3" />
                Forks
              </div>
              {forks.map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => {
                    onSection("forks");
                    onSelectFork(f.id);
                  }}
                  className="mb-0.5 w-full rounded-md px-2 py-1.5 text-left text-xs hover:bg-zinc-200/60 dark:hover:bg-white/5"
                >
                  <div className="line-clamp-2 font-medium text-zinc-900 dark:text-zinc-100">
                    {f.title}
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="border-t border-zinc-200/80 p-2 dark:border-white/[0.06]">
          <div className="flex items-center gap-2 rounded-lg px-2 py-1.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-orange-400 to-violet-600 text-[11px] font-bold text-white">
              {initials}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">{displayName || "You"}</div>
              <div className="truncate text-[10px] text-zinc-500">{displayRole || "observer"}</div>
            </div>
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-200/80 dark:hover:bg-white/10"
              title="Settings"
            >
              <Settings2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      <OpsSettingsSheet
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        ping={ping}
        online={ping?.status === "ok"}
        displayName={displayName}
        displayRole={displayRole}
        participantId={participantId}
        authToken={authToken}
        gateways={gateways}
        activeRoomId={activeRoomId}
        onNameChange={onNameChange}
        onNameCommit={onNameCommit}
        onRoleChange={onRoleChange}
        onRoleCommit={onRoleCommit}
        onAuthTokenChange={onAuthTokenChange}
        roomHint={rooms.find((r) => r.id === activeRoomId)?.name || "main"}
      />

      {manageAgentsOpen && (
        <div className="fixed inset-0 z-[65] flex items-center justify-center bg-black/50 p-4">
          <div className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-zinc-200 bg-white p-4 shadow-xl dark:border-white/10 dark:bg-zinc-900">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">Manage agents</h3>
              <button
                type="button"
                className="text-xs font-semibold text-orange-800 dark:text-orange-200"
                onClick={() => setManageAgentsOpen(false)}
              >
                Close
              </button>
            </div>
            <ManagedAgentsPanel
              rooms={rooms}
              currentRoomId={activeRoomId}
              participants={participants}
              refreshKey={managedRefreshKey}
              onAddAgent={() => {
                setManageAgentsOpen(false);
                setShowAddAgent(true);
              }}
              onChanged={managedChanged}
              onSelectRoom={(id) => {
                openRoom(id);
                setManageAgentsOpen(false);
              }}
            />
          </div>
        </div>
      )}

      {showAddAgent && (
        <AddAgentWizard
          rooms={rooms}
          currentRoomId={activeRoomId}
          participants={participants}
          onClose={() => setShowAddAgent(false)}
          onChanged={managedChanged}
          onSelectRoom={onSelectRoom}
        />
      )}
    </>
  );
}
