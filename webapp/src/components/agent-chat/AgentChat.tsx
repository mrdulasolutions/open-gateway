/**
 * Adapted from 21st.dev Agent Chat (serafimcloud/agent-chat)
 * Full-frame multi-agent chat + @mentions + file attach + theme-aware.
 */
import {
  memo,
  useState,
  useRef,
  useEffect,
  useMemo,
  type ReactNode,
  type KeyboardEvent,
  type ChangeEvent,
} from "react";
import {
  Paperclip,
  Send,
  X,
  FileIcon,
  AtSign,
  Bookmark,
  GitFork,
  Copy,
  Check,
} from "lucide-react";
import { Markdown } from "@/components/Markdown";
import { cn } from "@/lib/utils";

export type ChatStatus = "ready" | "streaming" | "submitted" | "idle";

export type MessagePart =
  | { type: "text"; text: string }
  | { type: "file"; name: string; url?: string; contentType?: string }
  | { type: "error"; title?: string; message: string };

export type AgentMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  parts: MessagePart[];
  name?: string;
  harness?: string;
  createdAt?: string;
};

export type Mentionable = { id: string; name: string; harness?: string };

export type PendingFile = {
  id: string;
  file: File;
};

export type AgentChatProps = {
  messages: AgentMessage[];
  onSend?: (payload: {
    role: "user";
    content: string;
    files: File[];
    mentionIds: string[];
  }) => void | Promise<void>;
  status?: ChatStatus;
  error?: { message: string; title?: string };
  emptyState?: ReactNode;
  placeholder?: string;
  disabled?: boolean;
  footerExtra?: ReactNode;
  className?: string;
  mentionables?: Mentionable[];
  bookmarkedIds?: Set<string>;
  onBookmark?: (messageId: string) => void;
  onFork?: (messageId: string) => void;
  onCopy?: (text: string) => void;
  highlightMessageId?: string | null;
  /** Shown in composer toolbar, e.g. "Room" or "DM · alice" */
  modeLabel?: string;
};

function initials(name?: string) {
  return (name || "?").slice(0, 2).toUpperCase();
}

function harnessTone(h?: string) {
  const k = (h || "").toLowerCase();
  if (k.includes("grok"))
    return "bg-orange-500/15 text-orange-800 dark:text-orange-200 border-orange-500/30";
  if (k.includes("claude"))
    return "bg-amber-500/15 text-amber-800 dark:text-amber-200 border-amber-500/30";
  if (k.includes("cursor"))
    return "bg-sky-500/15 text-sky-800 dark:text-sky-200 border-sky-500/30";
  if (k.includes("human"))
    return "bg-violet-500/15 text-violet-800 dark:text-violet-200 border-violet-500/30";
  if (k.includes("broadcast") || k === "all")
    return "bg-fuchsia-500/15 text-fuchsia-800 dark:text-fuchsia-200 border-fuchsia-500/30";
  return "bg-black/5 dark:bg-white/5 text-zinc-600 dark:text-zinc-300 border-black/10 dark:border-white/10";
}

function fmtTime(iso?: string) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso.slice(11, 19);
  }
}

function MessageActions({
  messageId,
  text,
  bookmarked,
  onBookmark,
  onFork,
  onCopy,
}: {
  messageId: string;
  text: string;
  bookmarked?: boolean;
  onBookmark?: (id: string) => void;
  onFork?: (id: string) => void;
  onCopy?: (text: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-0.5 opacity-0 transition group-hover:opacity-100">
      <button
        type="button"
        className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/10 dark:hover:text-zinc-200"
        title="Copy"
        onClick={async () => {
          await navigator.clipboard.writeText(text);
          onCopy?.(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1000);
        }}
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
      <button
        type="button"
        className={cn(
          "rounded-md p-1.5 hover:bg-zinc-100 dark:hover:bg-white/10",
          bookmarked
            ? "text-amber-500"
            : "text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
        )}
        title="Bookmark"
        onClick={() => onBookmark?.(messageId)}
      >
        <Bookmark className={cn("h-3.5 w-3.5", bookmarked && "fill-current")} />
      </button>
      <button
        type="button"
        className="rounded-md p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/10 dark:hover:text-zinc-200"
        title="Fork conversation from here"
        onClick={() => onFork?.(messageId)}
      >
        <GitFork className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function renderParts(parts: MessagePart[], rich: boolean) {
  return parts.map((p, i) => {
    if (p.type === "text") {
      return rich ? (
        <Markdown key={i} content={p.text} className="text-[15px]" />
      ) : (
        <span key={i} className="whitespace-pre-wrap">
          {p.text}
        </span>
      );
    }
    if (p.type === "file") {
      return (
        <a
          key={i}
          href={p.url || "#"}
          target="_blank"
          rel="noreferrer"
          className="mt-2 flex items-center gap-2 rounded-lg border border-zinc-200 bg-zinc-50 px-2.5 py-1.5 text-sm font-medium text-zinc-800 dark:border-white/10 dark:bg-black/30 dark:text-zinc-100"
        >
          <FileIcon className="h-4 w-4 shrink-0" />
          {p.name}
        </a>
      );
    }
    return null;
  });
}

function plainText(parts: MessagePart[]) {
  return parts
    .filter((p): p is { type: "text"; text: string } => p.type === "text")
    .map((p) => p.text)
    .join("\n");
}

function UserBubble({
  id,
  parts,
  name,
  createdAt,
  bookmarked,
  onBookmark,
  onFork,
  onCopy,
  highlight,
}: {
  id: string;
  parts: MessagePart[];
  name?: string;
  createdAt?: string;
  bookmarked?: boolean;
  onBookmark?: (id: string) => void;
  onFork?: (id: string) => void;
  onCopy?: (text: string) => void;
  highlight?: boolean;
}) {
  return (
    <div
      id={`msg-${id}`}
      className={cn(
        "group flex justify-end gap-3",
        highlight && "rounded-xl ring-2 ring-amber-400/50"
      )}
    >
      <div className="max-w-[min(960px,88%)] space-y-1">
        <div className="flex items-center justify-end gap-2 text-xs text-zinc-500">
          <MessageActions
            messageId={id}
            text={plainText(parts)}
            bookmarked={bookmarked}
            onBookmark={onBookmark}
            onFork={onFork}
            onCopy={onCopy}
          />
          <span>{fmtTime(createdAt)}</span>
          <span className="font-medium text-zinc-600 dark:text-zinc-400">
            {name || "you"}
          </span>
        </div>
        <div className="og-user-bubble rounded-2xl px-4 py-3 text-[15px] leading-relaxed shadow-sm">
          {renderParts(parts, true)}
        </div>
      </div>
      <div
        className={cn(
          "mt-6 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border text-[11px] font-bold",
          harnessTone("human")
        )}
      >
        {initials(name || "you")}
      </div>
    </div>
  );
}

function AssistantBubble({
  id,
  parts,
  name,
  harness,
  createdAt,
  bookmarked,
  onBookmark,
  onFork,
  onCopy,
  highlight,
}: {
  id: string;
  parts: MessagePart[];
  name?: string;
  harness?: string;
  createdAt?: string;
  bookmarked?: boolean;
  onBookmark?: (id: string) => void;
  onFork?: (id: string) => void;
  onCopy?: (text: string) => void;
  highlight?: boolean;
}) {
  return (
    <div
      id={`msg-${id}`}
      className={cn(
        "group flex justify-start gap-3",
        highlight && "rounded-xl ring-2 ring-amber-400/50"
      )}
    >
      <div
        className={cn(
          "mt-6 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border text-[11px] font-bold",
          harnessTone(harness)
        )}
      >
        {initials(name)}
      </div>
      <div className="max-w-[min(980px,92%)] space-y-1">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="font-semibold text-zinc-800 dark:text-zinc-200">
            {name || "agent"}
          </span>
          {harness && (
            <span
              className={cn(
                "rounded-full border px-1.5 py-px text-[10px]",
                harnessTone(harness)
              )}
            >
              {harness}
            </span>
          )}
          <span className="text-zinc-500">{fmtTime(createdAt)}</span>
          <MessageActions
            messageId={id}
            text={plainText(parts)}
            bookmarked={bookmarked}
            onBookmark={onBookmark}
            onFork={onFork}
            onCopy={onCopy}
          />
        </div>
        <div className="rounded-2xl border border-zinc-200 bg-white px-4 py-3 text-[15px] leading-relaxed text-zinc-800 shadow-sm dark:border-white/10 dark:bg-zinc-900/80 dark:text-zinc-200">
          {renderParts(parts, true)}
        </div>
      </div>
    </div>
  );
}

function SystemLine({ text }: { text: string }) {
  return (
    <div className="flex justify-center py-1">
      <div className="max-w-[90%] rounded-full border border-dashed border-zinc-300 px-3 py-1 text-center text-xs text-zinc-500 dark:border-white/10">
        {text}
      </div>
    </div>
  );
}

function MessageList({
  messages,
  bookmarkedIds,
  onBookmark,
  onFork,
  onCopy,
  highlightMessageId,
}: {
  messages: AgentMessage[];
  bookmarkedIds?: Set<string>;
  onBookmark?: (id: string) => void;
  onFork?: (id: string) => void;
  onCopy?: (text: string) => void;
  highlightMessageId?: string | null;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (highlightMessageId) {
      document
        .getElementById(`msg-${highlightMessageId}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, messages[messages.length - 1]?.id, highlightMessageId]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-none flex-col gap-5">
        {messages.map((m) => {
          if (m.role === "system") {
            const text = m.parts
              .filter((p): p is { type: "text"; text: string } => p.type === "text")
              .map((p) => p.text)
              .join("\n");
            return <SystemLine key={m.id} text={text} />;
          }
          if (m.role === "user") {
            return (
              <UserBubble
                key={m.id}
                id={m.id}
                parts={m.parts}
                name={m.name}
                createdAt={m.createdAt}
                bookmarked={bookmarkedIds?.has(m.id)}
                onBookmark={onBookmark}
                onFork={onFork}
                onCopy={onCopy}
                highlight={highlightMessageId === m.id}
              />
            );
          }
          return (
            <AssistantBubble
              key={m.id}
              id={m.id}
              parts={m.parts}
              name={m.name}
              harness={m.harness}
              createdAt={m.createdAt}
              bookmarked={bookmarkedIds?.has(m.id)}
              onBookmark={onBookmark}
              onFork={onFork}
              onCopy={onCopy}
              highlight={highlightMessageId === m.id}
            />
          );
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function InputBar({
  onSend,
  status = "ready",
  value,
  onChange,
  placeholder,
  disabled,
  footerExtra,
  mentionables = [],
  pendingFiles,
  onAddFiles,
  onRemoveFile,
  modeLabel,
}: {
  onSend?: (payload: {
    role: "user";
    content: string;
    files: File[];
    mentionIds: string[];
  }) => void | Promise<void>;
  status?: ChatStatus;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
  footerExtra?: ReactNode;
  mentionables?: Mentionable[];
  pendingFiles: PendingFile[];
  onAddFiles: (files: FileList | null) => void;
  onRemoveFile: (id: string) => void;
  modeLabel?: string;
}) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionIndex, setMentionIndex] = useState(0);
  const busy = status === "streaming" || status === "submitted" || disabled;

  const filteredMentions = useMemo(() => {
    const q = mentionQuery.toLowerCase();
    return mentionables
      .filter((m) => m.name.toLowerCase().includes(q))
      .slice(0, 8);
  }, [mentionables, mentionQuery]);

  const extractMentionIds = (text: string) => {
    const ids: string[] = [];
    for (const m of mentionables) {
      const re = new RegExp(
        `@${m.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b`,
        "i"
      );
      if (re.test(text)) ids.push(m.id);
    }
    return ids;
  };

  const insertMention = (m: Mentionable) => {
    const ta = taRef.current;
    if (!ta) {
      onChange(`${value.replace(/@[\w.-]*$/, "")}@${m.name} `);
      setMentionOpen(false);
      return;
    }
    const pos = ta.selectionStart ?? value.length;
    const before = value.slice(0, pos);
    const after = value.slice(pos);
    const replaced = before.replace(/@[\w.-]*$/, `@${m.name} `);
    onChange(replaced + after);
    setMentionOpen(false);
    setMentionQuery("");
    requestAnimationFrame(() => {
      ta.focus();
      const caret = replaced.length;
      ta.setSelectionRange(caret, caret);
    });
  };

  const insertAtCursor = (snippet: string) => {
    const ta = taRef.current;
    if (!ta) {
      onChange(value + snippet);
      return;
    }
    const pos = ta.selectionStart ?? value.length;
    const next = value.slice(0, pos) + snippet + value.slice(pos);
    onChange(next);
    requestAnimationFrame(() => {
      ta.focus();
      const caret = pos + snippet.length;
      ta.setSelectionRange(caret, caret);
    });
  };

  const onInput = (e: ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value;
    onChange(v);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;

    const pos = el.selectionStart ?? v.length;
    const upto = v.slice(0, pos);
    const match = upto.match(/@([\w.-]*)$/);
    if (match) {
      setMentionOpen(true);
      setMentionQuery(match[1] || "");
      setMentionIndex(0);
    } else {
      setMentionOpen(false);
      setMentionQuery("");
    }
  };

  const submit = async () => {
    const text = value.trim();
    if ((!text && pendingFiles.length === 0) || busy) return;
    const mentionIds = extractMentionIds(text);
    await onSend?.({
      role: "user",
      content: text || (pendingFiles.length ? "(attachment)" : ""),
      files: pendingFiles.map((p) => p.file),
      mentionIds,
    });
    onChange("");
    if (taRef.current) taRef.current.style.height = "auto";
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionOpen && filteredMentions.length) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionIndex((i) => (i + 1) % filteredMentions.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionIndex(
          (i) => (i - 1 + filteredMentions.length) % filteredMentions.length
        );
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        insertMention(filteredMentions[mentionIndex]);
        return;
      }
      if (e.key === "Escape") {
        setMentionOpen(false);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  };

  const canSend = !busy && (!!value.trim() || pendingFiles.length > 0);
  const chars = value.length;

  return (
    <div className="border-t border-zinc-200 bg-white/90 px-4 pb-4 pt-3 backdrop-blur-md dark:border-white/10 dark:bg-zinc-950/85 sm:px-6 lg:px-8">
      {footerExtra && <div className="mb-2 w-full">{footerExtra}</div>}

      {pendingFiles.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {pendingFiles.map((pf) => (
            <span
              key={pf.id}
              className="inline-flex items-center gap-1.5 rounded-lg border border-orange-500/20 bg-orange-500/5 px-2.5 py-1 text-xs font-medium text-zinc-700 dark:border-violet-500/20 dark:bg-violet-500/10 dark:text-zinc-200"
            >
              <FileIcon className="h-3.5 w-3.5 text-orange-600 dark:text-violet-300" />
              {pf.file.name}
              <span className="text-[10px] text-zinc-400">
                {(pf.file.size / 1024).toFixed(0)}kb
              </span>
              <button
                type="button"
                className="ml-0.5 rounded p-0.5 hover:bg-black/5 dark:hover:bg-white/10"
                onClick={() => onRemoveFile(pf.id)}
                aria-label="Remove file"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="relative w-full">
        {mentionOpen && filteredMentions.length > 0 && (
          <div className="absolute bottom-full left-0 z-20 mb-2 w-full max-w-md overflow-hidden rounded-xl border border-zinc-200 bg-white shadow-xl dark:border-white/10 dark:bg-zinc-900">
            <div className="border-b border-zinc-100 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:border-white/5">
              Mention · type to filter
            </div>
            {filteredMentions.map((m, i) => (
              <button
                key={m.id}
                type="button"
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm transition",
                  i === mentionIndex
                    ? "bg-orange-500/10 text-orange-900 dark:text-orange-100"
                    : "text-zinc-800 hover:bg-zinc-50 dark:text-zinc-200 dark:hover:bg-white/5"
                )}
                onMouseDown={(e) => {
                  e.preventDefault();
                  insertMention(m);
                }}
              >
                <AtSign className="h-3.5 w-3.5 opacity-60" />
                <span className="font-semibold">{m.name}</span>
                {m.harness && (
                  <span
                    className={cn(
                      "rounded-full border px-1.5 py-px text-[10px]",
                      harnessTone(m.harness)
                    )}
                  >
                    {m.harness}
                  </span>
                )}
              </button>
            ))}
          </div>
        )}

        <div className="og-composer-shell overflow-hidden">
          {/* Toolbar */}
          <div className="flex flex-wrap items-center gap-1.5 border-b border-zinc-200/80 px-2.5 py-1.5 dark:border-white/10">
            {modeLabel && (
              <span className="mr-1 rounded-full border border-violet-500/25 bg-violet-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-800 dark:text-violet-200">
                {modeLabel}
              </span>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                insertAtCursor("@");
                setMentionOpen(true);
                setMentionQuery("");
                taRef.current?.focus();
              }}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-zinc-600 transition hover:bg-orange-500/10 hover:text-orange-800 dark:text-zinc-300 dark:hover:text-orange-200"
              title="Mention"
            >
              <AtSign className="h-3.5 w-3.5" />
              Mention
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => insertAtCursor("@all ")}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-zinc-600 transition hover:bg-violet-500/10 hover:text-violet-800 dark:text-zinc-300 dark:hover:text-violet-200"
              title="Broadcast to all agents"
            >
              @all
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium text-zinc-600 transition hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-white/5"
              title="Attach files"
            >
              <Paperclip className="h-3.5 w-3.5" />
              Attach
            </button>
            <span className="ml-auto text-[10px] text-zinc-400">
              Enter send · Shift+Enter newline
              {chars > 0 ? ` · ${chars}` : ""}
            </span>
          </div>

          <div className="flex items-end gap-0">
            <input
              ref={fileRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                onAddFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <textarea
              ref={taRef}
              rows={2}
              value={value}
              disabled={busy}
              placeholder={
                placeholder || "Message…  @agent  ·  @all  ·  attach files"
              }
              className="max-h-[200px] min-h-[64px] w-full flex-1 resize-none bg-transparent px-4 py-3 text-[15px] leading-relaxed text-zinc-900 outline-none placeholder:text-zinc-400 disabled:opacity-50 dark:text-zinc-100 dark:placeholder:text-zinc-500"
              onChange={onInput}
              onKeyDown={onKeyDown}
            />
            <div className="flex shrink-0 flex-col gap-1 p-2">
              <button
                type="button"
                disabled={!canSend}
                onClick={() => void submit()}
                className={cn(
                  "flex h-11 w-11 items-center justify-center rounded-xl transition sm:h-12 sm:w-14 sm:rounded-2xl",
                  "bg-gradient-to-br from-orange-400 via-orange-500 to-violet-600 text-white shadow-md shadow-orange-900/20",
                  "hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
                )}
                aria-label="Send"
              >
                <Send className="h-5 w-5" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export const AgentChat = memo(function AgentChat({
  messages,
  onSend,
  status = "ready",
  error,
  emptyState,
  placeholder,
  disabled,
  footerExtra,
  className,
  mentionables = [],
  bookmarkedIds,
  onBookmark,
  onFork,
  onCopy,
  highlightMessageId,
  modeLabel,
}: AgentChatProps) {
  const [draft, setDraft] = useState("");
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);

  const messagesWithError: AgentMessage[] = useMemo(() => {
    if (!error) return messages;
    return [
      ...messages,
      {
        id: "agent-chat-error",
        role: "assistant" as const,
        name: "error",
        parts: [
          {
            type: "text" as const,
            text: `${error.title ?? "Error"}: ${error.message}`,
          },
        ],
      },
    ];
  }, [messages, error]);

  const isEmpty = !error && messages.length === 0;

  const handleSend = async (payload: {
    role: "user";
    content: string;
    files: File[];
    mentionIds: string[];
  }) => {
    await onSend?.(payload);
    setPendingFiles([]);
  };

  return (
    <div className={cn("flex h-full min-h-0 w-full flex-col", className)}>
      {isEmpty ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-4">
          {emptyState ?? (
            <p className="text-base text-zinc-500">No messages yet</p>
          )}
        </div>
      ) : (
        <MessageList
          messages={messagesWithError}
          bookmarkedIds={bookmarkedIds}
          onBookmark={onBookmark}
          onFork={onFork}
          onCopy={onCopy}
          highlightMessageId={highlightMessageId}
        />
      )}
      <InputBar
        onSend={handleSend}
        status={status}
        value={draft}
        onChange={setDraft}
        placeholder={placeholder}
        disabled={disabled}
        footerExtra={footerExtra}
        mentionables={mentionables}
        pendingFiles={pendingFiles}
        modeLabel={modeLabel}
        onAddFiles={(list) => {
          if (!list?.length) return;
          const next = Array.from(list).map((file) => ({
            id: `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 7)}`,
            file,
          }));
          setPendingFiles((prev) => [...prev, ...next]);
        }}
        onRemoveFile={(id) =>
          setPendingFiles((prev) => prev.filter((p) => p.id !== id))
        }
      />
    </div>
  );
});

export default AgentChat;
