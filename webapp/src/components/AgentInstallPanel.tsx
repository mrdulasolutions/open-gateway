/**
 * Multi-harness agent install snippets — public uvx (no private git path).
 */
import { useMemo, useState } from "react";
import { Copy, ExternalLink, Shield } from "lucide-react";
import {
  type AgentHarness,
  HARNESS_LABELS,
  defaultAgentName,
  envBlock,
  harnessConfigSnippet,
  imSeatSnippet,
  mcpEnvSnippet,
  DOCS_CONNECT,
} from "../lib/agentKit";

type Props = {
  hubUrl: string;
  token: string;
  defaultName?: string;
  roomHint?: string;
};

type Tab = "env" | "config" | "im";

export function AgentInstallPanel({
  hubUrl,
  token,
  defaultName,
  roomHint = "main",
}: Props) {
  const [harness, setHarness] = useState<AgentHarness>("claude-code");
  const [tab, setTab] = useState<Tab>("config");
  const [copied, setCopied] = useState<string | null>(null);

  const name = defaultName || defaultAgentName(harness);
  const text = useMemo(() => {
    const opts = { hubUrl, token, harness, name, room: roomHint };
    if (tab === "env") return mcpEnvSnippet(opts);
    if (tab === "im") return imSeatSnippet(opts);
    return harnessConfigSnippet(opts);
  }, [hubUrl, token, harness, name, roomHint, tab]);

  const copy = async (key: string, value: string) => {
    await navigator.clipboard.writeText(value);
    setCopied(key);
    window.setTimeout(() => setCopied(null), 1500);
  };

  return (
    <div className="space-y-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-2">
      <p className="text-[10px] font-semibold text-emerald-900 dark:text-emerald-100">
        Install for agent — public CLI (no private git)
      </p>
      <p className="text-[9px] leading-relaxed text-zinc-600 dark:text-zinc-400">
        Token shown once. Docs:{" "}
        <a
          href={DOCS_CONNECT}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-0.5 text-orange-600 hover:underline dark:text-orange-400"
        >
          docs/AGENTS_AUTH.md
          <ExternalLink className="h-2.5 w-2.5" />
        </a>
      </p>

      <label className="block text-[10px] font-medium text-zinc-500">
        Harness
        <select
          value={harness}
          onChange={(e) => setHarness(e.target.value as AgentHarness)}
          className="field-input mt-0.5 w-full text-xs"
        >
          {(Object.keys(HARNESS_LABELS) as AgentHarness[]).map((h) => (
            <option key={h} value={h}>
              {HARNESS_LABELS[h]}
            </option>
          ))}
        </select>
      </label>

      <div className="flex gap-1">
        {(
          [
            ["config", "MCP config"],
            ["env", "Env + install"],
            ["im", "IM seat"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={
              tab === id
                ? "rounded-lg bg-orange-500/20 px-2 py-1 text-[10px] font-semibold text-orange-800 dark:text-orange-200"
                : "rounded-lg border border-zinc-200 px-2 py-1 text-[10px] font-medium dark:border-white/10"
            }
          >
            {label}
          </button>
        ))}
      </div>

      <pre className="max-h-40 overflow-auto rounded bg-zinc-900/90 p-2 font-mono text-[9px] text-zinc-200">
        {text}
      </pre>

      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          className="flex flex-1 items-center justify-center gap-1 rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-[10px] font-semibold dark:border-white/10 dark:bg-zinc-900"
          onClick={() => void copy("snippet", text)}
        >
          <Copy className="h-3 w-3" />
          {copied === "snippet" ? "Copied" : "Copy snippet"}
        </button>
        <button
          type="button"
          className="flex flex-1 items-center justify-center gap-1 rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-[10px] font-semibold dark:border-white/10 dark:bg-zinc-900"
          onClick={() => void copy("token", token)}
        >
          <Shield className="h-3 w-3" />
          {copied === "token" ? "Copied" : "Copy token only"}
        </button>
        <button
          type="button"
          className="flex flex-1 items-center justify-center gap-1 rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-[10px] font-semibold dark:border-white/10 dark:bg-zinc-900"
          onClick={() =>
            void copy(
              "env",
              envBlock({ hubUrl, token, harness, name })
            )
          }
        >
          <Copy className="h-3 w-3" />
          {copied === "env" ? "Copied" : "Copy env only"}
        </button>
      </div>
    </div>
  );
}
