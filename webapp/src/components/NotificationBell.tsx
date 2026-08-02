/**
 * Top-nav notification bell — DMs, mentions, room pings while away.
 */
import { useEffect, useRef, useState } from "react";
import { Bell, MessageSquare, AtSign, ListTodo, Hash, CheckCheck } from "lucide-react";
import { cn } from "@/lib/utils";

export type AppNotification = {
  id: string;
  kind: "dm" | "mention" | "task" | "room";
  title: string;
  body: string;
  createdAt: string;
  roomId?: string;
  peerId?: string;
  messageId?: string;
  read: boolean;
};

type Props = {
  items: AppNotification[];
  onSelect: (n: AppNotification) => void;
  onMarkAllRead: () => void;
  onMarkRead: (id: string) => void;
};

function kindIcon(kind: AppNotification["kind"]) {
  switch (kind) {
    case "dm":
      return <MessageSquare className="h-3.5 w-3.5" />;
    case "mention":
      return <AtSign className="h-3.5 w-3.5" />;
    case "task":
      return <ListTodo className="h-3.5 w-3.5" />;
    default:
      return <Hash className="h-3.5 w-3.5" />;
  }
}

function kindBadge(kind: AppNotification["kind"]) {
  switch (kind) {
    case "dm":
      return "bg-violet-500/15 text-violet-800 border-violet-500/30 dark:text-violet-200";
    case "mention":
      return "bg-orange-500/15 text-orange-900 border-orange-500/30 dark:text-orange-100";
    case "task":
      return "bg-sky-500/15 text-sky-900 border-sky-500/30 dark:text-sky-100";
    default:
      return "bg-zinc-100 text-zinc-600 border-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:border-white/10";
  }
}

function kindLabel(kind: AppNotification["kind"]) {
  switch (kind) {
    case "dm":
      return "DM";
    case "mention":
      return "Mention";
    case "task":
      return "Task";
    default:
      return "Room";
  }
}

function relativeTime(iso: string) {
  try {
    const d = Date.now() - new Date(iso).getTime();
    if (d < 60_000) return "just now";
    if (d < 3_600_000) return `${Math.floor(d / 60_000)}m`;
    if (d < 86_400_000) return `${Math.floor(d / 3_600_000)}h`;
    return new Date(iso).toLocaleDateString();
  } catch {
    return "";
  }
}

export function NotificationBell({
  items,
  onSelect,
  onMarkAllRead,
  onMarkRead,
}: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const unread = items.filter((n) => !n.read).length;

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "relative inline-flex h-9 w-9 items-center justify-center rounded-full border bg-white text-zinc-600 transition dark:border-white/10 dark:bg-zinc-900 dark:text-zinc-300",
          unread > 0
            ? "border-orange-400/50 text-orange-700 dark:text-orange-200"
            : "border-zinc-200"
        )}
        title={unread ? `${unread} unread` : "Notifications"}
        aria-label="Notifications"
      >
        <Bell className={cn("h-4 w-4", unread > 0 && "fill-orange-500/20")} />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-gradient-to-br from-orange-500 to-violet-600 px-1 text-[10px] font-bold text-white shadow">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full z-50 mt-2 w-[min(360px,92vw)] overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-2xl dark:border-white/10 dark:bg-zinc-900">
          <div className="flex items-center justify-between border-b border-zinc-100 px-3 py-2.5 dark:border-white/5">
            <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              Notifications
              {unread > 0 && (
                <span className="ml-1.5 text-xs font-normal text-zinc-500">
                  {unread} new
                </span>
              )}
            </div>
            {items.length > 0 && (
              <button
                type="button"
                onClick={() => {
                  onMarkAllRead();
                }}
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:hover:bg-white/5 dark:hover:text-zinc-200"
              >
                <CheckCheck className="h-3.5 w-3.5" />
                Mark all read
              </button>
            )}
          </div>

          <div className="max-h-[min(420px,60vh)] overflow-y-auto">
            {items.length === 0 && (
              <div className="px-4 py-8 text-center text-xs text-zinc-500">
                Quiet for now. DMs, @mentions, and tasks show up here.
              </div>
            )}
            {items.map((n) => (
              <button
                key={n.id}
                type="button"
                onClick={() => {
                  onMarkRead(n.id);
                  onSelect(n);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full gap-2.5 border-b border-zinc-50 px-3 py-2.5 text-left transition last:border-0 dark:border-white/[0.04]",
                  n.read
                    ? "hover:bg-zinc-50 dark:hover:bg-white/5"
                    : "bg-orange-500/[0.06] hover:bg-orange-500/10 dark:bg-violet-500/10"
                )}
              >
                <span
                  className={cn(
                    "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border",
                    kindBadge(n.kind)
                  )}
                >
                  {kindIcon(n.kind)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span
                      className={cn(
                        "rounded border px-1 py-px text-[9px] font-bold uppercase tracking-wide",
                        kindBadge(n.kind)
                      )}
                    >
                      {kindLabel(n.kind)}
                    </span>
                    <span className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                      {n.title}
                    </span>
                    {!n.read && (
                      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-orange-500" />
                    )}
                  </span>
                  <span className="mt-0.5 line-clamp-2 text-xs text-zinc-500">
                    {n.body}
                  </span>
                  <span className="mt-0.5 block text-[10px] text-zinc-400">
                    {relativeTime(n.createdAt)}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
