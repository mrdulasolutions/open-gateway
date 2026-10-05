import { cn } from "@/lib/utils";

function initials(name?: string) {
  const parts = (name || "?").trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() || "").join("") || "?";
}

export function TypingBubble({
  id,
  name,
  harness,
  harnessClass,
}: {
  id: string;
  name?: string;
  harness?: string;
  harnessClass: string;
}) {
  return (
    <div id={`msg-${id}`} className="flex justify-start gap-3">
      <style>{`
        @keyframes og-typing-dot {
          0%, 60%, 100% { transform: translateY(0); opacity: 0.35; }
          30% { transform: translateY(-4px); opacity: 1; }
        }
        .og-typing-dot {
          width: 7px;
          height: 7px;
          border-radius: 999px;
          background: currentColor;
          animation: og-typing-dot 1.4s infinite ease-in-out;
        }
        .og-typing-dot:nth-child(2) { animation-delay: 0.16s; }
        .og-typing-dot:nth-child(3) { animation-delay: 0.32s; }
        @media (prefers-reduced-motion: reduce) {
          .og-typing-dot { animation: none; opacity: 0.85; }
        }
      `}</style>
      <div
        className={cn(
          "mt-6 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border text-[11px] font-bold",
          harnessClass
        )}
      >
        {initials(name)}
      </div>
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-semibold text-orange-950 dark:text-orange-50">
            {name || "agent"}
          </span>
          {harness && (
            <span className="rounded-full border border-orange-500/30 bg-orange-500/15 px-1.5 py-px text-[10px] text-orange-800 dark:text-orange-100">
              {harness}
            </span>
          )}
        </div>
        <div
          className="og-user-bubble inline-flex h-[34px] items-center gap-[5px] rounded-full px-3.5"
          role="status"
          aria-label={`${name || "Agent"} is typing`}
        >
          <span className="og-typing-dot" />
          <span className="og-typing-dot" />
          <span className="og-typing-dot" />
        </div>
      </div>
    </div>
  );
}
