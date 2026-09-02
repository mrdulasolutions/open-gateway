/**
 * Agent-kit install snippets for OSS self-host.
 * PyPI distribution: open-gateway (CLI entry: opengateway).
 */

export type AgentHarness = "grok" | "claude-code" | "cursor" | "hermes";

export const OSS_VERSION = "0.1.0";
export const PYPI_PACKAGE = "open-gateway";
export const GIT_REPO = "https://github.com/mrdulasolutions/open-gateway.git";
export const GIT_INSTALL = `git+${GIT_REPO}@v${OSS_VERSION}`;

export const DOCS_CONNECT =
  "https://github.com/mrdulasolutions/open-gateway/blob/main/docs/AGENTS_AUTH.md";
export const DOCS_IM =
  "https://github.com/mrdulasolutions/open-gateway/blob/main/docs/AGENTS_IM.md";
export const DOCS_RADIO =
  "https://github.com/mrdulasolutions/open-gateway/blob/main/docs/AGENTS_RADIO.md";

/** Recommended OSS install — PyPI package open-gateway @ version. CLI entry: opengateway */
export const INSTALL_SPEC = `${PYPI_PACKAGE}==${OSS_VERSION}`;

export const INSTALL_CLI = `# Install OpenGateway OSS hub CLI (self-host)
# Recommended (PyPI):
uv tool install "${INSTALL_SPEC}"
# one-shot MCP:
#   uvx ${PYPI_PACKAGE} mcp
# git @ release:
#   uv tool install "${GIT_INSTALL}"
# dev checkout:
#   git clone ${GIT_REPO} && cd open-gateway && uv sync && uv run opengateway mcp`;

export const HARNESS_LABELS: Record<AgentHarness, string> = {
  grok: "Grok",
  "claude-code": "Claude Code",
  cursor: "Cursor",
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
opengateway mcp
# one-shot: uvx ${PYPI_PACKAGE} mcp
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
opengateway im ${room} \\
  --name "${name}" \\
  --harness ${opts.harness} \\
  --wake ${wakeFlag}

# Background service (macOS launchd / Linux systemd --user):
opengateway im-service install ${room} \\
  --name "${name}" --harness ${opts.harness} --wake ${wakeFlag} \\
  --token "${opts.token}" --url "${url}"
# opengateway im-service list
# opengateway im-service logs <label>
`;
}
