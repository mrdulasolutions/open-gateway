import { useMemo, useState, type ReactNode } from "react";
import {
  Bookmark,
  ChevronDown,
  ChevronRight,
  FileStack,
  ListTodo,
  MessageSquare,
  Users,
} from "lucide-react";
import type { Artifact, Bookmark as BookmarkT, Participant, Task } from "@/lib/types";
import { cn } from "@/lib/utils";

type Props = {
  participants: Participant[];
  tasks: Task[];
  artifacts: Artifact[];
  bookmarks: BookmarkT[];
  events: string[];
  meId?: string | null;
  activeDmPeerId?: string | null;
  onJumpMessage?: (messageId: string) => void;
  onOpenDm?: (peerId: string) => void;
};

function AccordionSection({
  title,
  icon,
  count,
  defaultOpen = false,
  children,
}: {
  title: string;
  icon: ReactNode;
  count?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-white/[0.06] dark:bg-zinc-900/70">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
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
          <span className="rounded-full bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-500 dark:bg-zinc-800">
            {count}
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-1.5 border-t border-zinc-100 px-2.5 py-2.5 dark:border-white/5">
          {children}
        </div>
      )}
    </div>
  );
}

function Row({
  children,
  onClick,
  active,
}: {
  children: ReactNode;
  onClick?: () => void;
  active?: boolean;
}) {
  const Comp = onClick ? "button" : "div";
  return (
    <Comp
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={cn(
        "w-full rounded-lg border px-2.5 py-2 text-left text-sm",
        active
          ? "border-violet-500/35 bg-violet-500/10"
          : "border-zinc-100 bg-zinc-50 dark:border-white/[0.05] dark:bg-zinc-950/80",
        onClick &&
          !active &&
          "hover:border-orange-500/30 hover:bg-orange-500/5"
      )}
    >
      {children}
    </Comp>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <div className="px-1 py-2 text-xs text-zinc-500">{children}</div>;
}

function relativeSeen(iso?: string) {
  if (!iso) return "";
  try {
    const t = new Date(iso).getTime();
    const d = Date.now() - t;
    if (d < 60_000) return "just now";
    if (d < 3_600_000) return `${Math.floor(d / 60_000)}m ago`;
    if (d < 86_400_000) return `${Math.floor(d / 3_600_000)}h ago`;
    return new Date(iso).toLocaleDateString();
  } catch {
    return "";
  }
}

export function SideRail({
  participants,
  tasks,
  artifacts,
  bookmarks,
  events,
  meId,
  activeDmPeerId,
  onJumpMessage,
  onOpenDm,
}: Props) {
  const sorted = useMemo(() => {
    const rank = (p: Participant) => {
      const pr = p.presence || (p.status === "online" ? "joined" : "offline");
      if (pr === "listening") return 0;
      if (pr === "joined" || p.status === "online") return 1;
      return 2;
    };
    return [...participants].sort((a, b) => {
      const ar = rank(a);
      const br = rank(b);
      if (ar !== br) return ar - br;
      const at = a.last_poll_at || a.last_seen_at
        ? new Date(a.last_poll_at || a.last_seen_at).getTime()
        : 0;
      const bt = b.last_poll_at || b.last_seen_at
        ? new Date(b.last_poll_at || b.last_seen_at).getTime()
        : 0;
      return bt - at;
    });
  }, [participants]);

  const listeningCount = sorted.filter((p) => p.presence === "listening").length;
  const joinedCount = sorted.filter(
    (p) => p.presence === "joined" || (p.status === "online" && p.presence !== "listening")
  ).length;

  return (
    <aside className="flex h-full min-h-0 w-full shrink-0 flex-col gap-2.5 overflow-y-auto border-l border-zinc-200 bg-zinc-50/90 p-3 dark:border-white/[0.06] dark:bg-zinc-950/50 md:w-[320px]">
      <AccordionSection
        title="Participants"
        icon={<Users className="h-4 w-4" />}
        count={participants.length}
        defaultOpen={false}
      >
        {sorted.length === 0 && <Empty>None</Empty>}
        {sorted.length > 0 && (
          <div className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-400">
            {listeningCount} listening · {joinedCount} joined · radio vs present
          </div>
        )}
        {sorted.map((p) => {
          const isMe = meId && p.id === meId;
          const presence =
            p.presence ||
            (p.status === "online" ? "joined" : "offline");
          const isListening = presence === "listening";
          const isJoined = presence === "joined";
          return (
            <Row
              key={p.id}
              active={activeDmPeerId === p.id}
              onClick={
                !isMe && onOpenDm
                  ? () => onOpenDm(p.id)
                  : undefined
              }
            >
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    "h-2 w-2 shrink-0 rounded-full",
                    isListening &&
                      "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.85)] animate-pulse",
                    isJoined &&
                      !isListening &&
                      "bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.6)]",
                    !isListening &&
                      !isJoined &&
                      "bg-zinc-400 dark:bg-zinc-600"
                  )}
                  title={
                    isListening
                      ? "Listening (long-poll / radio on)"
                      : isJoined
                        ? "Joined but not listening"
                        : "Offline"
                  }
                />
                <div className="min-w-0 flex-1 font-medium text-zinc-900 dark:text-zinc-100">
                  {p.name}
                  {isMe && (
                    <span className="ml-1 text-[10px] font-normal text-zinc-400">
                      (you)
                    </span>
                  )}
                </div>
                <span
                  className={cn(
                    "shrink-0 rounded-full px-1.5 py-px text-[9px] font-semibold uppercase tracking-wide",
                    isListening &&
                      "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
                    isJoined &&
                      !isListening &&
                      "bg-amber-500/15 text-amber-700 dark:text-amber-400",
                    !isListening &&
                      !isJoined &&
                      "bg-zinc-500/10 text-zinc-500"
                  )}
                >
                  {isListening ? "listening" : isJoined ? "joined" : "offline"}
                </span>
                {!isMe && onOpenDm && (
                  <MessageSquare className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
                )}
              </div>
              <div className="mt-0.5 pl-4 text-xs text-zinc-500">
                {p.harness} · {p.role}
                {(p.last_poll_at || p.last_seen_at) && (
                  <span className="text-zinc-400">
                    {" · "}
                    {relativeSeen(p.last_poll_at || p.last_seen_at)}
                  </span>
                )}
              </div>
              {p.capabilities?.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1 pl-4">
                  {p.capabilities.slice(0, 4).map((c) => (
                    <span
                      key={c}
                      className="rounded bg-zinc-200/80 px-1 py-px text-[10px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
                    >
                      {c}
                    </span>
                  ))}
                </div>
              )}
            </Row>
          );
        })}
      </AccordionSection>

      <AccordionSection
        title="Tasks"
        icon={<ListTodo className="h-4 w-4" />}
        count={tasks.length}
        defaultOpen={false}
      >
        {tasks.length === 0 && <Empty>None</Empty>}
        {tasks.map((t) => {
          const assignee =
            (t.metadata?.assignee_name as string) ||
            (t.claimed_by ? t.claimed_by.slice(0, 8) : "unassigned");
          return (
            <Row key={t.id}>
              <div className="font-medium text-zinc-900 dark:text-zinc-100">
                {t.title}
              </div>
              <div className="mt-1 flex flex-wrap gap-1.5 text-[11px]">
                <span className="rounded-full border border-zinc-200 px-1.5 py-px dark:border-white/10">
                  {t.status}
                </span>
                <span className="text-zinc-500">→ {assignee}</span>
              </div>
              {t.description && (
                <div className="mt-1.5 line-clamp-3 whitespace-pre-wrap text-xs text-zinc-500">
                  {t.description}
                </div>
              )}
              {t.result && (
                <div className="mt-1 text-xs text-orange-700 dark:text-orange-300">
                  Result: {t.result}
                </div>
              )}
              {t.updated_at && (
                <div className="mt-1 text-[10px] text-zinc-400">
                  Updated {new Date(t.updated_at).toLocaleString()}
                </div>
              )}
            </Row>
          );
        })}
      </AccordionSection>

      <AccordionSection
        title="Bookmarks"
        icon={<Bookmark className="h-4 w-4" />}
        count={bookmarks.length}
        defaultOpen={false}
      >
        {bookmarks.length === 0 && <Empty>Bookmark messages from chat</Empty>}
        {bookmarks.map((b, i) => (
          <Row key={b.id} onClick={() => onJumpMessage?.(b.message_id)}>
            <div className="flex items-center gap-2 text-xs text-zinc-500">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-zinc-200 text-[10px] font-bold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                {i + 1}
              </span>
              {new Date(b.created_at).toLocaleString()}
            </div>
            <div className="mt-1 font-medium text-zinc-900 dark:text-zinc-100">
              {b.title}
            </div>
            {b.excerpt && (
              <div className="mt-0.5 line-clamp-2 text-xs text-zinc-500">
                {b.excerpt}
              </div>
            )}
          </Row>
        ))}
      </AccordionSection>

      <AccordionSection
        title="Artifacts"
        icon={<FileStack className="h-4 w-4" />}
        count={artifacts.length}
        defaultOpen={false}
      >
        {artifacts.length === 0 && <Empty>None</Empty>}
        {artifacts.map((a) => (
          <Row key={a.id}>
            <div className="font-medium text-zinc-900 dark:text-zinc-100">
              {a.name}
            </div>
            <div className="mt-0.5 text-xs text-zinc-500">
              {a.content_type}
              {a.description ? ` · ${a.description}` : ""}
            </div>
          </Row>
        ))}
      </AccordionSection>

      <AccordionSection
        title="Event log"
        icon={<ListTodo className="h-4 w-4" />}
        count={events.length}
        defaultOpen={false}
      >
        <div className="max-h-36 space-y-1 overflow-y-auto font-mono text-[11px] text-zinc-500">
          {events.length === 0 && <Empty>Quiet</Empty>}
          {events.map((e, i) => (
            <div
              key={i}
              className="border-b border-zinc-100 py-1 dark:border-white/[0.04]"
            >
              {e}
            </div>
          ))}
        </div>
      </AccordionSection>
    </aside>
  );
}
