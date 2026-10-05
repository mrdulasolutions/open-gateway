import type {
  AgentRunner,
  AuthInvite,
  Bookmark,
  CreateManagedAgentInput,
  DmThread,
  Fork,
  ManagedAgent,
  ManagedAgentAction,
  ManagedAgentLogs,
  Ping,
  Room,
  RunnerPairing,
  Snapshot,
  Participant,
  RoomMessage,
} from "./types";

const API =
  (import.meta.env.VITE_OPENGATEWAY_URL as string | undefined)?.replace(
    /\/$/,
    ""
  ) ?? "";

const TOKEN_KEY = "og_token";

export function getAuthToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setAuthToken(token: string) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(extra?: HeadersInit): HeadersInit {
  const t = getAuthToken();
  return {
    ...(t ? { Authorization: `Bearer ${t}` } : {}),
    ...(extra || {}),
  };
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData;
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: authHeaders({
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers || {}),
    }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  base: API,
  ping: () => req<Ping>("/ping"),
  authStatus: async () => {
    const res = await fetch(`${API}/v1/auth/status`);
    if (!res.ok) throw new Error("auth status failed");
    return res.json() as Promise<{
      has_users: boolean;
      user_count: number;
      registration_open: boolean;
      require_auth?: boolean;
      open_registration?: boolean;
    }>;
  },
  authRegister: async (body: {
    email: string;
    password: string;
    display_name?: string;
    org_name?: string;
    invite_code?: string;
  }) => {
    const res = await fetch(`${API}/v1/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(
        (j as { detail?: string }).detail || res.statusText || "register failed"
      );
    }
    return j as {
      token: string;
      user: {
        id: string;
        email: string;
        role: string;
        display_name: string;
        tenant_id: string;
      };
      tenant_id: string;
    };
  },
  authLogin: async (body: { email: string; password: string }) => {
    const res = await fetch(`${API}/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(
        (j as { detail?: string }).detail || res.statusText || "login failed"
      );
    }
    return j as {
      token: string;
      user: {
        id: string;
        email: string;
        role: string;
        display_name: string;
        tenant_id: string;
      };
      tenant_id: string;
    };
  },
  authLogout: () =>
    req<{ status: string }>("/v1/auth/logout", { method: "POST" }),
  authMe: () =>
    req<{
      auth_kind: string | null;
      user: {
        email: string;
        role: string;
        display_name: string;
        tenant_id: string;
      } | null;
    }>("/v1/auth/me"),
  authInvite: (body?: { role?: string; tenant_id?: string }) =>
    req<AuthInvite>("/v1/auth/invite", {
      method: "POST",
      body: JSON.stringify(body || {}),
    }),
  listAuthInvites: (tenantId?: string) =>
    req<{ invites: AuthInvite[]; open_registration: boolean }>(
      `/v1/auth/invites${
        tenantId ? `?tenant_id=${encodeURIComponent(tenantId)}` : ""
      }`
    ),
  /** Public — no auth. First-run bootstrap when the hub requires a token. */
  setupStatus: async () => {
    const res = await fetch(`${API}/v1/setup`);
    if (!res.ok) throw new Error("setup status failed");
    return res.json() as Promise<{
      require_auth: boolean;
      claimable: boolean;
      claimed: boolean;
      disabled: boolean;
      hint: string;
    }>;
  },
  setupClaim: async () => {
    const res = await fetch(`${API}/v1/setup/claim`, { method: "POST" });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(
        (j as { detail?: string }).detail || res.statusText || "claim failed"
      );
    }
    return j as { ok: boolean; token: string; message?: string };
  },
  listRooms: (opts?: { includeArchived?: boolean }) =>
    req<{ rooms: Room[] }>(
      `/v1/rooms${
        opts?.includeArchived ? "?include_archived=true" : ""
      }`
    ),
  archiveRoom: (roomId: string, actor?: string) => {
    const q = actor ? `?actor=${encodeURIComponent(actor)}` : "";
    return req<Room>(`/v1/rooms/${roomId}/archive${q}`, { method: "POST" });
  },
  unarchiveRoom: (roomId: string, actor?: string) => {
    const q = actor ? `?actor=${encodeURIComponent(actor)}` : "";
    return req<Room>(`/v1/rooms/${roomId}/unarchive${q}`, { method: "POST" });
  },
  createRoom: (body: {
    name: string;
    goal?: string;
    project_path?: string;
    created_by?: string;
  }) =>
    req<Room>("/v1/rooms", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  snapshot: (roomId: string, forParticipant?: string) => {
    const qs = forParticipant
      ? `?for_participant=${encodeURIComponent(forParticipant)}`
      : "";
    return req<Snapshot>(`/v1/rooms/${roomId}/snapshot${qs}`);
  },
  join: (
    roomId: string,
    body: {
      name: string;
      harness?: string;
      role?: string;
      capabilities?: string[];
      participant_id?: string;
    }
  ) =>
    req<Participant>(`/v1/rooms/${roomId}/join`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  rename: (roomId: string, participantId: string, name: string) =>
    req<Participant>(`/v1/rooms/${roomId}/participants/${participantId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  updateParticipant: (
    roomId: string,
    participantId: string,
    body: { name?: string; role?: string; capabilities?: string[] }
  ) =>
    req<Participant>(`/v1/rooms/${roomId}/participants/${participantId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  postMessage: (
    roomId: string,
    body: {
      from_participant_id: string;
      content: string;
      nudge_all?: boolean;
      to_participant_id?: string;
      parts?: {
        name?: string;
        content_type: string;
        content?: string;
        content_url?: string;
        content_encoding?: "plain" | "base64";
      }[];
      metadata?: Record<string, unknown>;
    }
  ) =>
    req<RoomMessage | { message: RoomMessage; nudge_count: number }>(
      `/v1/rooms/${roomId}/messages`,
      {
        method: "POST",
        body: JSON.stringify(body),
      }
    ),
  uploadFile: async (roomId: string, sharedBy: string, file: File) => {
    const fd = new FormData();
    fd.append("shared_by", sharedBy);
    fd.append("file", file);
    return req<{
      id: string;
      name: string;
      content_type: string;
      content_url?: string | null;
    }>(`/v1/rooms/${roomId}/files`, { method: "POST", body: fd });
  },
  listBookmarks: (roomId: string) =>
    req<{ bookmarks: Bookmark[] }>(`/v1/rooms/${roomId}/bookmarks`),
  createBookmark: (
    roomId: string,
    body: {
      message_id: string;
      title: string;
      excerpt?: string;
      created_by: string;
    }
  ) =>
    req<Bookmark>(`/v1/rooms/${roomId}/bookmarks`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteBookmark: (roomId: string, bookmarkId: string) =>
    req<{ status: string }>(`/v1/rooms/${roomId}/bookmarks/${bookmarkId}`, {
      method: "DELETE",
    }),
  listForks: (roomId: string) =>
    req<{ forks: Fork[] }>(`/v1/rooms/${roomId}/forks`),
  createFork: (
    roomId: string,
    body: {
      root_message_id: string;
      title?: string;
      note?: string;
      created_by: string;
      created_by_name?: string;
    }
  ) =>
    req<Fork>(`/v1/rooms/${roomId}/forks`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listDms: (roomId: string, participantId: string) =>
    req<{ threads: DmThread[] }>(
      `/v1/rooms/${roomId}/dms?participant_id=${encodeURIComponent(participantId)}`
    ),
  search: (q: string, limit = 40, forParticipant?: string | null) => {
    const params = new URLSearchParams({
      q,
      limit: String(limit),
    });
    if (forParticipant) {
      params.set("for_participant", forParticipant);
    }
    return req<{
      query: string;
      hits: {
        type: string;
        type_label?: string;
        score: number;
        id: string;
        title: string;
        subtitle: string;
        room_id?: string;
        path?: string;
        meta?: Record<string, unknown>;
      }[];
      suggestions: { label: string; query: string; type?: string }[];
    }>(`/v1/search?${params.toString()}`);
  },
  listGateways: () =>
    req<{
      gateways: {
        id: string;
        name: string;
        mode: string;
        network: string;
        base_url: string;
        require_auth: boolean;
        is_self: boolean;
        notes?: string;
      }[];
      self: Record<string, unknown>;
      lan_ips: string[];
      tips: Record<string, string>;
    }>("/v1/gateways"),
  listRunners: async () => {
    const payload = await req<
      | AgentRunner[]
      | {
          runners?: AgentRunner[];
          items?: AgentRunner[];
        }
    >("/v1/runners");
    return {
      runners: Array.isArray(payload)
        ? payload
        : payload.runners || payload.items || [],
    };
  },
  pairRunner: async () => {
    const payload = await req<
      | RunnerPairing
      | {
          pairing?: RunnerPairing;
          pair?: RunnerPairing;
        }
    >("/v1/runners/pair", {
      method: "POST",
      body: JSON.stringify({}),
    });
    if ("pairing" in payload && payload.pairing) return payload.pairing;
    if ("pair" in payload && payload.pair) return payload.pair;
    return payload as RunnerPairing;
  },
  deleteRunner: (id: string) =>
    req<{ ok?: boolean; status?: string }>(
      `/v1/runners/${encodeURIComponent(id)}`,
      { method: "DELETE" }
    ),
  listManagedAgents: async () => {
    const payload = await req<
      | ManagedAgent[]
      | {
          managed_agents?: ManagedAgent[];
          agents?: ManagedAgent[];
          items?: ManagedAgent[];
        }
    >("/v1/managed-agents");
    return {
      agents: Array.isArray(payload)
        ? payload
        : payload.managed_agents || payload.agents || payload.items || [],
    };
  },
  createManagedAgent: async (body: CreateManagedAgentInput) => {
    const payload = await req<
      ManagedAgent | { agent?: ManagedAgent; managed_agent?: ManagedAgent }
    >("/v1/managed-agents", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if ("agent" in payload && payload.agent) return payload.agent;
    if ("managed_agent" in payload && payload.managed_agent) {
      return payload.managed_agent;
    }
    return payload as ManagedAgent;
  },
  joinManagedAgentRoom: async (id: string, roomId: string) => {
    const payload = await req<
      | ManagedAgent
      | { agent?: ManagedAgent; managed_agent?: ManagedAgent; job?: unknown }
    >(`/v1/managed-agents/${encodeURIComponent(id)}/rooms`, {
      method: "POST",
      body: JSON.stringify({ room_id: roomId }),
    });
    if ("agent" in payload && payload.agent) return payload.agent;
    if ("managed_agent" in payload && payload.managed_agent) {
      return payload.managed_agent;
    }
    return payload as ManagedAgent;
  },
  leaveManagedAgentRoom: async (id: string, roomId: string) => {
    const payload = await req<
      | ManagedAgent
      | { agent?: ManagedAgent; managed_agent?: ManagedAgent; job?: unknown }
    >(
      `/v1/managed-agents/${encodeURIComponent(id)}/rooms/${encodeURIComponent(roomId)}`,
      { method: "DELETE" }
    );
    if ("agent" in payload && payload.agent) return payload.agent;
    if ("managed_agent" in payload && payload.managed_agent) {
      return payload.managed_agent;
    }
    return payload as ManagedAgent;
  },
  moveManagedAgent: async (id: string, roomId: string) => {
    const payload = await req<
      | ManagedAgent
      | { agent?: ManagedAgent; managed_agent?: ManagedAgent; job?: unknown }
    >(`/v1/managed-agents/${encodeURIComponent(id)}/actions/move`, {
      method: "POST",
      body: JSON.stringify({ room_id: roomId }),
    });
    if ("agent" in payload && payload.agent) return payload.agent;
    if ("managed_agent" in payload && payload.managed_agent) {
      return payload.managed_agent;
    }
    return payload as ManagedAgent;
  },
  managedAgentAction: async (id: string, action: ManagedAgentAction) => {
    const payload = await req<
      ManagedAgent | { agent?: ManagedAgent; managed_agent?: ManagedAgent }
    >(
      `/v1/managed-agents/${encodeURIComponent(id)}/actions/${encodeURIComponent(action)}`,
      {
        method: "POST",
        body: JSON.stringify({}),
      }
    );
    if ("agent" in payload && payload.agent) return payload.agent;
    if ("managed_agent" in payload && payload.managed_agent) {
      return payload.managed_agent;
    }
    return payload as ManagedAgent;
  },
  deleteManagedAgent: (id: string) =>
    req<{ ok?: boolean; status?: string }>(
      `/v1/managed-agents/${encodeURIComponent(id)}`,
      { method: "DELETE" }
    ),
  managedAgentLogs: (id: string, limit = 200) =>
    req<ManagedAgentLogs>(
      `/v1/managed-agents/${encodeURIComponent(id)}/logs?limit=${Math.max(
        1,
        Math.min(limit, 500)
      )}`
    ),
  registerGateway: (body: {
    name: string;
    mode?: string;
    network?: string;
    base_url: string;
    require_auth?: boolean;
    notes?: string;
  }) =>
    req(`/v1/gateways`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  eventsUrl: (roomId: string, participantId?: string) => {
    const params = new URLSearchParams();
    const t = getAuthToken();
    if (t) params.set("token", t);
    if (participantId) params.set("participant_id", participantId);
    const qs = params.toString();
    return `${API}/v1/rooms/${roomId}/events${qs ? `?${qs}` : ""}`;
  },
  fileUrl: (roomId: string, fileId: string) =>
    `${API}/v1/rooms/${roomId}/files/${fileId}/download`,
  createPair: (body?: {
    room_id?: string;
    label?: string;
    ttl_seconds?: number;
    /** Advertise this origin in the QR (LAN vs Tailscale MagicDNS). */
    base_url?: string;
    /** Prefer path: lan | tailscale */
    network?: string;
  }) =>
    req<{
      code: string;
      url: string;
      qr_payload: string;
      ttl_seconds: number;
      max_uses: number;
      instructions: string[];
      room_id?: string | null;
      base_url?: string;
      urls?: Record<string, string>;
      access?: Record<string, string>;
    }>("/v1/pair", { method: "POST", body: JSON.stringify(body || {}) }),
  redeemPair: (code: string, name?: string) =>
    req<{
      ok: boolean;
      room_id?: string | null;
      suggested_name: string;
      harness: string;
      auth_token?: string | null;
      require_auth: boolean;
    }>("/v1/pair/redeem", {
      method: "POST",
      body: JSON.stringify({ code, name }),
    }),
  networkStatus: () =>
    req<{
      gateway: Record<string, unknown>;
      lan_ips: string[];
      tailscale: Record<string, unknown>;
      probes: { host: string; port: number; ok: boolean; error?: string }[];
      recommended: { mode: string; commands: string[]; why: string };
    }>("/v1/network"),
  listKeys: () => req<{ keys: ApiKeyMeta[] }>("/v1/keys"),
  createKey: (body: {
    name: string;
    scopes?: string[];
    role?: string;
    device_label?: string;
  }) =>
    req<ApiKeyMeta & { token: string }>("/v1/keys", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteKey: (id: string, revoke = false) =>
    req<{ status: string }>(
      `/v1/keys/${id}${revoke ? "?revoke=true" : ""}`,
      { method: "DELETE" }
    ),
  pushVapid: () =>
    req<{ configured: boolean; public_key: string | null; hint?: string }>(
      "/v1/push/vapid"
    ),
  pushSubscribe: (body: {
    subscription: PushSubscriptionJSON;
    participant_id?: string;
    device_label?: string;
  }) =>
    req<{ id: string; endpoint: string }>("/v1/push/subscribe", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listToolCredentials: () =>
    req<{
      credentials: {
        name: string;
        description?: string;
        inject?: string;
        header_name?: string;
        allowed_hosts?: string[];
        has_value?: boolean;
        updated_at?: string;
      }[];
    }>("/v1/tools/credentials"),
  putToolCredential: (
    name: string,
    body: {
      value: string;
      description?: string;
      inject?: string;
      header_name?: string;
      allowed_hosts?: string[];
    }
  ) =>
    req<Record<string, unknown>>(
      `/v1/tools/credentials/${encodeURIComponent(name)}`,
      {
        method: "PUT",
        body: JSON.stringify(body),
      }
    ),
  deleteToolCredential: (name: string) =>
    req<{ ok: boolean; name: string }>(
      `/v1/tools/credentials/${encodeURIComponent(name)}`,
      { method: "DELETE" }
    ),
  listWorkspace: (roomId: string, prefix = "") =>
    req<{
      room_id: string;
      files: { path: string; bytes?: number; content_type?: string }[];
    }>(
      `/v1/rooms/${roomId}/workspace${prefix ? `?prefix=${encodeURIComponent(prefix)}` : ""}`
    ),
  writeWorkspace: (
    roomId: string,
    path: string,
    body: {
      content?: string;
      content_base64?: string;
      content_type?: string;
      updated_by?: string;
    }
  ) =>
    req<Record<string, unknown>>(
      `/v1/rooms/${roomId}/workspace/${path.replace(/^\//, "")}`,
      { method: "PUT", body: JSON.stringify(body) }
    ),
  deleteWorkspace: (roomId: string, path: string) =>
    req<{ ok: boolean; path: string }>(
      `/v1/rooms/${roomId}/workspace/${path.replace(/^\//, "")}`,
      { method: "DELETE" }
    ),
};

export type ApiKeyMeta = {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  role: string;
  device_label: string;
  created_at?: string;
  revoked_at?: string | null;
  metadata?: Record<string, unknown>;
};

export function isAllCall(text: string): boolean {
  const t = text.toLowerCase();
  return [
    "everyone",
    "everybody",
    "@all",
    "@everyone",
    "all agents",
    "hey all",
    "hey everyone",
    "all of you",
    "you all",
  ].some((n) => t.includes(n));
}
