export type Harness =
  | "grok"
  | "claude-code"
  | "cursor"
  | "hermes"
  | "acp"
  | "mcp"
  | "human"
  | "other"
  | string;

export type AuthInvite = {
  code: string;
  role: string;
  max_uses: number;
  uses: number;
  created_at?: string | null;
  created_by?: string | null;
};

export type Room = {
  id: string;
  name: string;
  goal: string;
  project_path?: string | null;
  status: string;
  participant_ids: string[];
  created_by: string;
  created_at: string;
  updated_at: string;
};

/** Derived radio state from last_poll_at / last_seen (server computed_field). */
export type Presence = "listening" | "joined" | "offline" | string;

export type Participant = {
  id: string;
  name: string;
  harness: Harness;
  role: string;
  status: string;
  /** listening = long-poll radio on; joined = online but not polling; offline = stale */
  presence?: Presence;
  room_id?: string | null;
  capabilities: string[];
  joined_at: string;
  last_seen_at: string;
  last_poll_at?: string | null;
  metadata?: Record<string, unknown>;
};

export type RoomMessage = {
  id: string;
  room_id: string;
  from_participant_id: string;
  from_name: string;
  to_participant_id?: string | null;
  message: {
    role: string;
    parts: {
      name?: string | null;
      content?: string | null;
      content_type?: string;
      content_url?: string | null;
      content_encoding?: string;
    }[];
  };
  created_at: string;
  metadata?: Record<string, unknown>;
};

export type Task = {
  id: string;
  room_id: string;
  title: string;
  description: string;
  status: string;
  claimed_by?: string | null;
  created_by: string;
  result?: string | null;
  created_at?: string;
  updated_at?: string;
  metadata?: Record<string, unknown>;
};

export type Artifact = {
  id: string;
  room_id: string;
  name: string;
  content_type: string;
  shared_by: string;
  description?: string;
  content_url?: string | null;
};

export type Bookmark = {
  id: string;
  room_id: string;
  message_id: string;
  title: string;
  excerpt: string;
  created_by: string;
  created_at: string;
};

export type Fork = {
  id: string;
  room_id: string;
  root_message_id: string;
  forked_room_id?: string | null;
  title: string;
  created_by: string;
  created_by_name: string;
  note: string;
  created_at: string;
  metadata?: Record<string, unknown>;
};

export type DmThread = {
  peer_id: string;
  peer_name: string;
  peer_harness: string;
  peer_status?: string;
  peer_role?: string;
  peer_last_seen?: string | null;
  messages: RoomMessage[];
  last_at?: string | null;
};

export type Snapshot = {
  room: Room;
  participants: Participant[];
  messages: RoomMessage[];
  tasks: Task[];
  artifacts: Artifact[];
  bookmarks?: Bookmark[];
  forks?: Fork[];
};

export type Ping = {
  status: string;
  service: string;
  version: string;
  persistent: boolean;
  db_path?: string | null;
  rooms: number;
  mode?: string;
  network?: string;
  require_auth?: boolean;
  base_url?: string;
  local_runner?: boolean;
};

/** Harnesses that the local runner can launch and supervise. */
export type ManagedHarness = "claude-code" | "grok" | "hermes";

export type RunnerCapability = {
  harness?: ManagedHarness | string;
  name?: string;
  available?: boolean;
  ready?: boolean;
  supported?: boolean;
  installed?: boolean;
  authenticated?: boolean | null;
  status?: string;
  version?: string | null;
  detail?: string | null;
  reason?: string | null;
  error?: string | null;
  setup_instructions?: string | string[] | null;
  metadata?: Record<string, unknown>;
};

/**
 * Runner versions may report capabilities as a keyed map or as a list.
 * The UI normalizes both forms while keeping the API boundary typed.
 */
export type RunnerCapabilities =
  | Record<string, RunnerCapabilityValue>
  | Array<RunnerCapability | ManagedHarness | string>;

export type RunnerCapabilityValue =
  | RunnerCapability
  | boolean
  | string
  | string[]
  | null
  | undefined
  | { [key: string]: RunnerCapabilityValue };

export type AgentRunner = {
  id: string;
  name: string;
  status?: string;
  state?: string;
  connected?: boolean;
  online?: boolean;
  version?: string | null;
  platform?: string | null;
  hostname?: string | null;
  last_seen_at?: string | null;
  revoked_at?: string | null;
  created_at?: string;
  updated_at?: string;
  capabilities?: RunnerCapabilities;
  harnesses?: RunnerCapabilities;
  setup_instructions?: Record<string, string | string[] | null>;
  metadata?: Record<string, unknown>;
};

export type RunnerPairing = {
  code: string;
  command?: string;
  connect_command?: string;
  runner_connect_command?: string;
  url?: string;
  hub_url?: string;
  expires_at?: string | null;
  expires_in?: number;
  ttl_seconds?: number;
};

export type ManagedAgentState =
  | "pending"
  | "starting"
  | "running"
  | "stopping"
  | "stopped"
  | "restarting"
  | "deleting"
  | "deleted"
  | "failed"
  | "error"
  | string;

export type ManagedAgent = {
  id: string;
  name: string;
  harness: ManagedHarness | string;
  room_id: string;
  runner_id: string;
  state?: ManagedAgentState;
  status?: ManagedAgentState;
  participant_id?: string | null;
  runner_name?: string | null;
  room_name?: string | null;
  last_error?: string | null;
  error?: string | null;
  created_at?: string;
  updated_at?: string;
  started_at?: string | null;
  stopped_at?: string | null;
  deleted_at?: string | null;
  presence?: Presence;
  last_poll_at?: string | null;
  metadata?: Record<string, unknown>;
};

export type CreateManagedAgentInput = {
  runner_id: string;
  harness: ManagedHarness;
  room_id: string;
  name: string;
};

export type ManagedAgentAction = "stop" | "restart";

export type ManagedAgentLogEntry = {
  id?: string;
  runner_id?: string;
  managed_agent_id?: string;
  timestamp?: string;
  created_at?: string;
  level?: string;
  stream?: string;
  message?: string;
  line?: string;
};

export type ManagedAgentLogs = {
  agent_id?: string;
  logs?: Array<string | ManagedAgentLogEntry> | string;
  lines?: Array<string | ManagedAgentLogEntry> | string;
  truncated?: boolean;
};
