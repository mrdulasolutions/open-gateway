/**
 * Agent-kit install snippets for OSS self-host.
 * PyPI: opengateways (CLI: opengateways or opengateway).
 */
import type {
  AgentRunner,
  ManagedHarness,
  RunnerPairing,
} from "./types";

export type AgentHarness = "grok" | "claude-code" | "cursor" | "hermes";
export type { ManagedHarness } from "./types";

export const OSS_VERSION = "0.1.2";
export const PYPI_PACKAGE = "opengateways";
export const GIT_REPO = "https://github.com/mrdulasolutions/open-gateway.git";
export const GIT_INSTALL = `git+${GIT_REPO}@v${OSS_VERSION}`;

export const DOCS_CONNECT =
  "https://github.com/mrdulasolutions/open-gateway/blob/main/docs/AGENTS_AUTH.md";
export const DOCS_IM =
  "https://github.com/mrdulasolutions/open-gateway/blob/main/docs/AGENTS_IM.md";
export const DOCS_RADIO =
  "https://github.com/mrdulasolutions/open-gateway/blob/main/docs/AGENTS_RADIO.md";

export const INSTALL_SPEC = `${PYPI_PACKAGE}==${OSS_VERSION}`;

export const INSTALL_CLI = `# Install OpenGateway OSS hub CLI (self-host)
# Recommended (PyPI):
uv tool install "${INSTALL_SPEC}"
# CLI: opengateways serve  (alias: opengateway)
# one-shot MCP:
#   uvx ${PYPI_PACKAGE} mcp
# git @ release:
#   uv tool install "${GIT_INSTALL}"
# dev checkout:
#   git clone ${GIT_REPO} && cd open-gateway && uv sync && uv run opengateways mcp`;

export const HARNESS_LABELS: Record<AgentHarness, string> = {
  grok: "Grok",
  "claude-code": "Claude Code",
  cursor: "Cursor",
  hermes: "Hermes",
};

export const MANAGED_HARNESSES: readonly ManagedHarness[] = [
  "claude-code",
  "grok",
  "hermes",
] as const;

export const MANAGED_HARNESS_LABELS: Record<ManagedHarness, string> = {
  "claude-code": "Claude Code",
  grok: "Grok",
  hermes: "Hermes",
};

export function defaultAgentName(harness: AgentHarness): string {
  switch (harness) {
    case "claude-code":
      return "Claude COO";
    case "cursor":
      return "Cursor";
    case "hermes":
      return "Hermes COO";
    default:
      return "Grok";
  }
}

export function defaultManagedAgentName(harness: ManagedHarness): string {
  return defaultAgentName(harness);
}

export type NormalizedRunnerCapability = {
  harness: ManagedHarness;
  reported: boolean;
  available: boolean;
  status: string;
  detail: string;
  version?: string;
  setupInstructions: string[];
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function harnessAliases(harness: ManagedHarness): string[] {
  if (harness === "claude-code") {
    return ["claude-code", "claude_code", "claude", "claude code"];
  }
  return [harness];
}

function normalizeInstructions(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .filter((line): line is string => typeof line === "string")
      .map((line) => line.trim())
      .filter(Boolean);
  }
  if (typeof value === "string") {
    return value
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean);
  }
  return [];
}

function capabilityFrom(
  value: unknown,
  harness: ManagedHarness
): unknown | undefined {
  const aliases = harnessAliases(harness);
  if (Array.isArray(value)) {
    for (const entry of value) {
      if (
        typeof entry === "string" &&
        aliases.includes(entry.toLowerCase())
      ) {
        return true;
      }
      const row = asRecord(entry);
      const id = String(
        row?.harness ?? row?.name ?? row?.id ?? row?.slug ?? ""
      ).toLowerCase();
      if (row && aliases.includes(id)) return row;
    }
    return undefined;
  }

  const record = asRecord(value);
  if (!record) return undefined;
  for (const alias of aliases) {
    if (alias in record) return record[alias];
  }
  for (const nestedKey of ["harnesses", "providers", "agents"]) {
    if (nestedKey in record) {
      const nested = capabilityFrom(record[nestedKey], harness);
      if (nested !== undefined) return nested;
    }
  }
  return undefined;
}

function boolFrom(
  record: Record<string, unknown>,
  keys: string[]
): boolean | undefined {
  for (const key of keys) {
    if (typeof record[key] === "boolean") return record[key] as boolean;
  }
  return undefined;
}

export function hubRunsOnLoopback(baseUrl: string): boolean {
  try {
    const host = new URL(baseUrl, window.location.href).hostname
      .replace(/^\[|\]$/g, "")
      .toLowerCase();
    return host === "127.0.0.1" || host === "localhost" || host === "::1";
  } catch {
    return false;
  }
}

export function runnerIsConnected(runner: AgentRunner): boolean {
  if (typeof runner.connected === "boolean") return runner.connected;
  if (typeof runner.online === "boolean") return runner.online;
  const state = (runner.status || runner.state || "").toLowerCase();
  if (!state) return true;
  return ![
    "offline",
    "disconnected",
    "expired",
    "revoked",
    "stale",
  ].includes(state);
}

export function runnerIsEmbedded(runner: AgentRunner): boolean {
  const capabilities = asRecord(runner.capabilities);
  return capabilities?.embedded === true;
}

export function runnerCapability(
  runner: AgentRunner,
  harness: ManagedHarness
): NormalizedRunnerCapability {
  const raw =
    capabilityFrom(runner.capabilities, harness) ??
    capabilityFrom(runner.harnesses, harness);
  const row = asRecord(raw);
  const status = String(
    row?.status ??
      (typeof raw === "string"
        ? raw
        : raw === true
          ? "ready"
          : "unavailable")
  )
    .trim()
    .toLowerCase();
  const explicit = row
    ? boolFrom(row, ["available", "ready", "supported"])
    : undefined;
  const installed = row
    ? boolFrom(row, ["installed", "cli_available"])
    : undefined;
  const metadata = asRecord(row?.metadata);
  const authenticated = row
    ? boolFrom(row, ["authenticated", "auth_ready", "logged_in"])
    : undefined;
  const readyStates = ["ready", "available", "ok", "authenticated", "running"];
  const capabilityAvailable =
    typeof raw === "boolean"
      ? raw
      : explicit ??
        (typeof raw === "string"
          ? readyStates.includes(status)
          : readyStates.includes(status) ||
            ((installed === true || authenticated === true) &&
              installed !== false &&
              authenticated !== false));
  const available = capabilityAvailable && authenticated !== false;

  let setupInstructions = normalizeInstructions(
    row?.setup_instructions ??
      row?.setupInstructions ??
      metadata?.setup_instructions ??
      metadata?.setupInstructions
  );
  const capabilityRoot = asRecord(runner.capabilities);
  const capabilitySetup = asRecord(
    capabilityRoot?.setup_instructions ?? capabilityRoot?.setupInstructions
  );
  if (setupInstructions.length === 0 && capabilitySetup) {
    for (const alias of harnessAliases(harness)) {
      setupInstructions = normalizeInstructions(capabilitySetup[alias]);
      if (setupInstructions.length) break;
    }
  }
  if (setupInstructions.length === 0 && runner.setup_instructions) {
    for (const alias of harnessAliases(harness)) {
      setupInstructions = normalizeInstructions(
        runner.setup_instructions[alias]
      );
      if (setupInstructions.length) break;
    }
  }

  const detail = String(
    row?.detail ??
      row?.reason ??
      row?.error ??
      metadata?.detail ??
      metadata?.reason ??
      (raw === undefined
        ? "This runner did not report support for this harness."
        : available
          ? "Ready on this runner."
          : "Setup is required on the runner host.")
  );
  const version =
    typeof row?.version === "string" && row.version.trim()
      ? row.version.trim()
      : undefined;

  return {
    harness,
    reported: raw !== undefined,
    available,
    status,
    detail,
    version,
    setupInstructions,
  };
}

export function runnerPairCommand(
  pairing: RunnerPairing,
  fallbackUrl: string
): string {
  const supplied =
    pairing.command ||
    pairing.connect_command ||
    pairing.runner_connect_command;
  if (supplied) return supplied;
  const url = pairing.url || pairing.hub_url || fallbackUrl;
  const quote = (value: string) => `'${value.replace(/'/g, `'"'"'`)}'`;
  return `opengateways runner connect --url ${quote(url)} --code ${quote(
    pairing.code
  )} --name runner`;
}

/** Render a compatibility snippet without ever putting a raw token on screen. */
export function hideRawToken(text: string, token: string): string {
  if (!token) return text;
  return text.split(token).join("<token hidden — use Copy>");
}

export function envBlock(opts: {
  hubUrl: string;
  token: string;
  harness: AgentHarness;
  name?: string;
}): string {
  const name = opts.name || defaultAgentName(opts.harness);
  const url = opts.hubUrl.replace(/\/$/, "");
  return `OPENGATEWAY_URL="${url}"
OPENGATEWAY_AUTH_TOKEN="${opts.token}"
OPENGATEWAY_HARNESS="${opts.harness}"
OPENGATEWAY_AGENT_NAME="${name}"`;
}

export function mcpEnvSnippet(opts: {
  hubUrl: string;
  token: string;
  harness: AgentHarness;
  name?: string;
}): string {
  return `# OpenGateway agent kit — OSS self-host
# Docs: ${DOCS_CONNECT}

${INSTALL_CLI}

# Env for MCP / CLI
${envBlock(opts)}

# Start MCP (stdio) for this harness
opengateways mcp
# alias: opengateway mcp
`;
}

export function harnessConfigSnippet(opts: {
  hubUrl: string;
  token: string;
  harness: AgentHarness;
  name?: string;
}): string {
  const name = opts.name || defaultAgentName(opts.harness);
  const url = opts.hubUrl.replace(/\/$/, "");
  const token = opts.token;
  const h = opts.harness;
  const spec = INSTALL_SPEC;

  if (h === "grok") {
    return `# ~/.grok/config.toml  (or project MCP config)
[mcp_servers.opengateway]
command = "uvx"
args = ["${spec}", "mcp"]
env = { OPENGATEWAY_URL = "${url}", OPENGATEWAY_AUTH_TOKEN = "${token}", OPENGATEWAY_HARNESS = "grok", OPENGATEWAY_AGENT_NAME = "${name}" }
enabled = true
`;
  }

  if (h === "hermes") {
    return `# Hermes MCP env / config
# Install: uv tool install "${spec}"
# Then point Hermes MCP at:
#   command: uvx
#   args: ["${spec}", "mcp"]
# Env:
OPENGATEWAY_URL=${url}
OPENGATEWAY_AUTH_TOKEN=${token}
OPENGATEWAY_HARNESS=hermes
OPENGATEWAY_AGENT_NAME=${name}
`;
  }

  return `{
  "mcpServers": {
    "opengateway": {
      "command": "uvx",
      "args": ["${spec}", "mcp"],
      "env": {
        "OPENGATEWAY_URL": "${url}",
        "OPENGATEWAY_AUTH_TOKEN": "${token}",
        "OPENGATEWAY_HARNESS": "${h}",
        "OPENGATEWAY_AGENT_NAME": "${name}"
      }
    }
  }
}
`;
}

export function imSeatSnippet(opts: {
  hubUrl: string;
  token: string;
  harness: AgentHarness;
  name?: string;
  room?: string;
}): string {
  const name = opts.name || defaultAgentName(opts.harness);
  const room = opts.room || "main";
  const url = opts.hubUrl.replace(/\/$/, "");
  const wake =
    opts.harness === "claude-code"
      ? "claude"
      : opts.harness === "cursor"
        ? "hook"
        : opts.harness === "hermes"
          ? "hermes"
          : "grok";
  const wakeFlag = wake === "hook" ? "auto" : wake;
  return `# Always-on IM seat (auto-reply without desktop chat)
# Docs: ${DOCS_IM}

export OPENGATEWAY_URL="${url}"
export OPENGATEWAY_AUTH_TOKEN="${opts.token}"

# Foreground (terminal):
opengateways im ${room} \\
  --name "${name}" \\
  --harness ${opts.harness} \\
  --wake ${wakeFlag}

# Background service (macOS launchd / Linux systemd --user):
opengateways im-service install ${room} \\
  --name "${name}" --harness ${opts.harness} --wake ${wakeFlag} \\
  --token "${opts.token}" --url "${url}"
# opengateways im-service list
# opengateways im-service logs <label>
`;
}
