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

export type Participant = {
  id: string;
  name: string;
  harness: Harness;
  role: string;
  status: string;
  room_id?: string | null;
  capabilities: string[];
  joined_at: string;
  last_seen_at: string;
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
};
