/**
 * Left nav shell — Slack-style list via LiveOpsNav.
 */
import { useMemo } from "react";
import type { AppNotification } from "@/components/NotificationBell";
import type { DmThread, Fork, ManagedAgent, Participant, Ping, Room } from "@/lib/types";
import { LiveOpsNav, type DmRow } from "@/components/layout/LiveOpsNav";

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

export type { DmRow };

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
  managedAgents: ManagedAgent[];
  notifications: AppNotification[];
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
  showArchivedRooms: boolean;
  onShowArchivedRoomsChange: (value: boolean) => void;
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
};

export function OpsSidebar({
  collapsed,
  dmThreads,
  participants,
  participantId,
  ...rest
}: Props) {
  const dmRows: DmRow[] = useMemo(() => {
    const byId = new Map<string, DmRow>();
    for (const t of dmThreads) {
      if (!t.peer_id) continue;
      const p = participants.find((x) => x.id === t.peer_id);
      const presence =
        p?.presence ||
        (t.peer_status === "online" || p?.status === "online"
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
      return (b.last_at || "").localeCompare(a.last_at || "");
    });
  }, [dmThreads, participants, participantId]);

  if (collapsed) return null;

  return (
    <LiveOpsNav
      {...rest}
      dmRows={dmRows}
      participants={participants}
      participantId={participantId}
    />
  );
}
