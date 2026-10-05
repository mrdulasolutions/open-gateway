import { useEffect, useRef, useState } from "react";
import { Search, Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

export type SearchHit = {
  type: string;
  type_label?: string;
  score: number;
  id: string;
  title: string;
  subtitle: string;
  room_id?: string;
  path?: string;
  meta?: Record<string, unknown>;
};

type Props = {
  onNavigate: (hit: SearchHit) => void;
  className?: string;
  forParticipant?: string | null;
  /** When set, ⌘K is handled by the parent (palette). */
  registerShortcut?: boolean;
  paletteOpen?: boolean;
  onPaletteOpenChange?: (open: boolean) => void;
  /** Inline bar only, or palette overlay (no inline field). */
  variant?: "inline" | "palette";
};

const TYPE_STYLES: Record<string, string> = {
  dm: "bg-violet-500/15 text-violet-800 border-violet-500/35 dark:text-violet-200",
  message:
    "bg-orange-500/12 text-orange-900 border-orange-500/30 dark:text-orange-100",
  room:
    "bg-emerald-500/12 text-emerald-800 border-emerald-500/30 dark:text-emerald-200",
  participant:
    "bg-sky-500/12 text-sky-900 border-sky-500/30 dark:text-sky-100",
  task:
    "bg-amber-500/12 text-amber-900 border-amber-500/30 dark:text-amber-100",
  artifact:
    "bg-zinc-200/80 text-zinc-700 border-zinc-300 dark:bg-zinc-800 dark:text-zinc-300 dark:border-white/10",
  bookmark:
    "bg-yellow-500/12 text-yellow-900 border-yellow-500/30 dark:text-yellow-100",
  fork:
    "bg-fuchsia-500/12 text-fuchsia-900 border-fuchsia-500/30 dark:text-fuchsia-100",
};

const TYPE_LABELS: Record<string, string> = {
  dm: "DM",
  message: "Room msg",
  room: "Room",
  participant: "Agent",
  task: "Task",
  artifact: "File",
  bookmark: "Bookmark",
  fork: "Fork",
};

function typeLabel(h: SearchHit) {
  return h.type_label || TYPE_LABELS[h.type] || h.type;
}

function typeStyle(type: string) {
  return TYPE_STYLES[type] || TYPE_STYLES.artifact;
}

export function GlobalSearch({
  onNavigate,
  className,
  forParticipant,
  registerShortcut = false,
  paletteOpen = false,
  onPaletteOpenChange,
  variant = "inline",
}: Props) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [suggestions, setSuggestions] = useState<
    { label: string; query: string; type?: string }[]
  >([]);
  const [active, setActive] = useState(0);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [searchError, setSearchError] = useState("");
  const timer = useRef<number | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const panelOpen = variant === "palette" ? paletteOpen : open;

  const setPanelOpen = (next: boolean) => {
    if (variant === "palette") {
      onPaletteOpenChange?.(next);
    } else {
      setOpen(next);
    }
    if (!next) setSearchError("");
  };

  useEffect(() => {
    if (variant !== "inline") return;
    const onDoc = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [variant]);

  useEffect(() => {
    if (!registerShortcut) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onPaletteOpenChange?.(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [registerShortcut, onPaletteOpenChange]);

  useEffect(() => {
    if (paletteOpen) {
      window.setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [paletteOpen]);

  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    if (!q.trim()) {
      setHits([]);
      setSuggestions([]);
      setLoading(false);
      setSearchError("");
      return;
    }
    setLoading(true);
    setSearchError("");
    timer.current = window.setTimeout(async () => {
      try {
        const res = await api.search(q.trim(), 40, forParticipant);
        setHits(res.hits || []);
        setSuggestions(res.suggestions || []);
        setActive(0);
        if (variant === "inline") setOpen(true);
        const m = q.match(/\b(?:type|t):([a-zA-Z_-]+)/i);
        setTypeFilter(m ? m[1].toLowerCase() : null);
      } catch (err) {
        setHits([]);
        setSuggestions([]);
        setSearchError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    }, 140);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [q, forParticipant, variant]);

  const pick = (hit: SearchHit) => {
    onNavigate(hit);
    setPanelOpen(false);
    setQ("");
  };

  const visible = typeFilter
    ? hits.filter(
        (h) =>
          h.type === typeFilter ||
          h.type === typeFilter.replace(/s$/, "") ||
          (typeFilter === "agent" && h.type === "participant") ||
          (typeFilter === "msg" && h.type === "message")
      )
    : hits;

  const counts = hits.reduce<Record<string, number>>((acc, h) => {
    acc[h.type] = (acc[h.type] || 0) + 1;
    return acc;
  }, {});

  const resultsPanel = (
    <div
      className={cn(
        variant === "palette"
          ? "max-h-[min(480px,55vh)] overflow-auto rounded-xl border border-zinc-200 bg-white shadow-2xl dark:border-white/10 dark:bg-zinc-900"
          : "absolute left-0 right-0 top-full z-40 mt-1.5 max-h-[min(480px,65vh)] overflow-auto rounded-xl border border-zinc-200 bg-white shadow-2xl dark:border-white/10 dark:bg-zinc-900"
      )}
    >
      <div className="flex flex-wrap gap-1.5 border-b border-zinc-100 px-3 py-2 dark:border-white/5">
        <button
          type="button"
          className={cn(
            "rounded-full border px-2 py-0.5 text-[11px] font-medium",
            !typeFilter
              ? "border-orange-500/40 bg-orange-500/10 text-orange-900 dark:text-orange-100"
              : "border-zinc-200 text-zinc-600 dark:border-white/10 dark:text-zinc-300"
          )}
          onMouseDown={(e) => {
            e.preventDefault();
            setTypeFilter(null);
            setQ((prev) =>
              prev.replace(/\b(?:type|t):[a-zA-Z_-]+\s*/gi, "").trim()
            );
          }}
        >
          All
        </button>
        {(
          [
            ["dm", "DM"],
            ["message", "Room msg"],
            ["participant", "Agent"],
            ["room", "Room"],
            ["task", "Task"],
            ["fork", "Fork"],
          ] as const
        ).map(([t, label]) => (
          <button
            key={t}
            type="button"
            className={cn(
              "rounded-full border px-2 py-0.5 text-[11px] font-medium",
              typeFilter === t
                ? typeStyle(t)
                : "border-zinc-200 text-zinc-600 dark:border-white/10 dark:text-zinc-300"
            )}
            onMouseDown={(e) => {
              e.preventDefault();
              setTypeFilter(t);
              const base = q
                .replace(/\b(?:type|t):[a-zA-Z_-]+\s*/gi, "")
                .trim();
              setQ(`type:${t} ${base}`.trim());
            }}
          >
            {label}
            {counts[t] ? (
              <span className="ml-1 opacity-70">{counts[t]}</span>
            ) : null}
          </button>
        ))}
      </div>

      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-b border-zinc-100 px-3 py-2 dark:border-white/5">
          {suggestions.slice(0, 8).map((s) => (
            <button
              key={s.label + (s.type || "")}
              type="button"
              className="rounded-full border border-zinc-200 px-2 py-0.5 text-[11px] text-zinc-600 hover:bg-zinc-50 dark:border-white/10 dark:text-zinc-300 dark:hover:bg-white/5"
              onMouseDown={(e) => {
                e.preventDefault();
                setQ(s.query);
              }}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}

      {searchError && (
        <div className="border-b border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs text-rose-800 dark:text-rose-200">
          Search failed: {searchError}
        </div>
      )}

      {visible.map((h, i) => (
        <button
          key={`${h.type}-${h.id}`}
          type="button"
          onMouseDown={(e) => {
            e.preventDefault();
            pick(h);
          }}
          className={cn(
            "flex w-full flex-col gap-0.5 px-3 py-2.5 text-left text-sm transition",
            i === active
              ? "bg-orange-500/10"
              : "hover:bg-zinc-50 dark:hover:bg-white/5"
          )}
        >
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "shrink-0 rounded border px-1.5 py-px text-[10px] font-bold uppercase tracking-wide",
                typeStyle(h.type)
              )}
            >
              {typeLabel(h)}
            </span>
            <span className="font-medium text-zinc-900 dark:text-zinc-100 line-clamp-1">
              {h.title}
            </span>
          </div>
          <div className="pl-[3.25rem] text-xs text-zinc-500 line-clamp-1">
            {h.subtitle}
          </div>
        </button>
      ))}
      {visible.length === 0 && !searchError && (
        <div className="px-3 py-4 text-center text-xs text-zinc-500">
          {q.trim()
            ? "No matches — try type:agent or a room name"
            : "Type to search rooms, agents, and messages"}
        </div>
      )}
    </div>
  );

  const inputRow = (
    <div className="flex items-center gap-2 rounded-xl border border-zinc-200 bg-white px-3 py-2 shadow-sm dark:border-white/10 dark:bg-zinc-900">
      {loading ? (
        <Loader2 className="h-4 w-4 animate-spin text-zinc-400" />
      ) : (
        <Search className="h-4 w-4 text-zinc-400" />
      )}
      <input
        ref={inputRef}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onFocus={() => {
          if (variant === "inline" && q.trim()) setOpen(true);
        }}
        onKeyDown={(e) => {
          const items = visible;
          if (e.key === "ArrowDown" && items.length) {
            e.preventDefault();
            setActive((i) => (i + 1) % items.length);
          } else if (e.key === "ArrowUp" && items.length) {
            e.preventDefault();
            setActive((i) => (i - 1 + items.length) % items.length);
          } else if (e.key === "Enter" && items[active]) {
            e.preventDefault();
            pick(items[active]);
          } else if (e.key === "Escape") {
            setPanelOpen(false);
            inputRef.current?.blur();
          }
        }}
        placeholder="Search…  type:dm  type:message  type:agent  type:task"
        className="w-full bg-transparent text-sm text-zinc-900 outline-none placeholder:text-zinc-400 dark:text-zinc-100"
      />
      <kbd className="hidden rounded border border-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-400 sm:inline dark:border-white/10">
        ⌘K
      </kbd>
    </div>
  );

  if (variant === "palette") {
    if (!paletteOpen) return null;
    return (
      <div
        className="fixed inset-0 z-[60] flex items-start justify-center bg-black/40 px-3 pt-[12vh] backdrop-blur-sm"
        role="dialog"
        aria-modal="true"
        aria-label="Search"
        onMouseDown={(e) => {
          if (e.target === e.currentTarget) setPanelOpen(false);
        }}
      >
        <div className="w-full max-w-xl">
          <div className="mb-2 flex items-center justify-between px-1">
            <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
              Search
            </span>
            <button
              type="button"
              className="rounded-lg p-1 text-zinc-500 hover:bg-white/10"
              onClick={() => setPanelOpen(false)}
              aria-label="Close search"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          {inputRow}
          {(panelOpen && (hits.length > 0 || suggestions.length > 0 || q.trim())) &&
            resultsPanel}
        </div>
      </div>
    );
  }

  return (
    <div ref={boxRef} className={cn("relative w-full min-w-[12rem] max-w-xl", className)}>
      {inputRow}
      {open && (hits.length > 0 || suggestions.length > 0 || q.trim() || searchError) &&
        resultsPanel}
    </div>
  );
}
