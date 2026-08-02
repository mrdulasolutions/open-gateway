import { useEffect, useRef, useState } from "react";
import { Search, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

export type SearchHit = {
  type: string;
  score: number;
  id: string;
  title: string;
  subtitle: string;
  room_id?: string;
  path?: string;
};

type Props = {
  onNavigate: (hit: SearchHit) => void;
  className?: string;
};

export function GlobalSearch({ onNavigate, className }: Props) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [suggestions, setSuggestions] = useState<
    { label: string; query: string; type?: string }[]
  >([]);
  const [active, setActive] = useState(0);
  const timer = useRef<number | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
        setOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    if (!q.trim()) {
      setHits([]);
      setSuggestions([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    timer.current = window.setTimeout(async () => {
      try {
        const res = await api.search(q.trim(), 30);
        setHits(res.hits || []);
        setSuggestions(res.suggestions || []);
        setActive(0);
        setOpen(true);
      } catch {
        setHits([]);
      } finally {
        setLoading(false);
      }
    }, 140);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [q]);

  const pick = (hit: SearchHit) => {
    onNavigate(hit);
    setOpen(false);
    setQ("");
  };

  return (
    <div ref={boxRef} className={cn("relative w-full max-w-xl", className)}>
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
          onFocus={() => q.trim() && setOpen(true)}
          onKeyDown={(e) => {
            const items = hits;
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
              setOpen(false);
              inputRef.current?.blur();
            }
          }}
          placeholder="Search rooms, agents, messages, tasks…  type:room"
          className="w-full bg-transparent text-sm text-zinc-900 outline-none placeholder:text-zinc-400 dark:text-zinc-100"
        />
        <kbd className="hidden rounded border border-zinc-200 px-1.5 py-0.5 text-[10px] text-zinc-400 sm:inline dark:border-white/10">
          ⌘K
        </kbd>
      </div>

      {open && (hits.length > 0 || suggestions.length > 0) && (
        <div className="absolute left-0 right-0 top-full z-40 mt-1.5 max-h-[min(420px,60vh)] overflow-auto rounded-xl border border-zinc-200 bg-white shadow-2xl dark:border-white/10 dark:bg-zinc-900">
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
          {hits.map((h, i) => (
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
                <span className="rounded bg-zinc-100 px-1.5 py-px text-[10px] font-semibold uppercase text-zinc-500 dark:bg-zinc-800">
                  {h.type}
                </span>
                <span className="font-medium text-zinc-900 dark:text-zinc-100 line-clamp-1">
                  {h.title}
                </span>
              </div>
              <div className="text-xs text-zinc-500 line-clamp-1">{h.subtitle}</div>
            </button>
          ))}
          {hits.length === 0 && (
            <div className="px-3 py-4 text-center text-xs text-zinc-500">
              No matches — try a room name, agent, or phrase
            </div>
          )}
        </div>
      )}
    </div>
  );
}
