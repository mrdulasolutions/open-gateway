import { useCallback, useEffect, useMemo, useState } from "react";
import { Moon, Sun, PanelRightOpen, PanelRightClose } from "lucide-react";
import { AgentChat, type AgentMessage } from "@/components/agent-chat/AgentChat";
import { GlobalSearch, type SearchHit } from "@/components/GlobalSearch";
import {
  NotificationBell,
  type AppNotification,
} from "@/components/NotificationBell";
import { OpsSidebar, type LeftSection } from "@/components/layout/OpsSidebar";
import { SideRail } from "@/components/layout/SideRail";
import { LoginPage } from "@/components/LoginPage";
import { api, getAuthToken, isAllCall, setAuthToken } from "@/lib/api";
import { useTheme } from "@/hooks/useTheme";
import {
  getStoredName,
  getStoredPid,
  getStoredRole,
  setStoredName,
  setStoredPid,
  setStoredRole,
} from "@/lib/identity";
import type {
  Artifact,
  Bookmark,
  DmThread,
  Fork,
  Participant,
  Ping,
  Room,
  RoomMessage,
  Task,
} from "@/lib/types";

/** Shown when public gateway needs a bearer token in this browser. */
function AuthConnectBanner({
  authToken,
  onToken,
}: {
  authToken: string;
  onToken: (t: string) => void;
}) {
  const [draft, setDraft] = useState(authToken);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [claimable, setClaimable] = useState(false);

  useEffect(() => {
    void api
      .setupStatus()
      .then((s) => setClaimable(Boolean(s.claimable)))
      .catch(() => setClaimable(false));
  }, []);

  const claim = async () => {
    setBusy(true);
    setMsg("");
    try {
      const res = await api.setupClaim();
      if (res.token) {
        onToken(res.token);
        setMsg(res.message || "Connected — this browser is authorized.");
      }
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
      setClaimable(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-b border-amber-500/40 bg-amber-50 px-4 py-4 text-sm text-amber-950 dark:bg-amber-500/10 dark:text-amber-100">
      <div className="mx-auto max-w-3xl space-y-3">
        <div>
          <strong className="text-base">Gateway authorization required</strong>
          <p className="mt-1 text-xs leading-relaxed opacity-90">
            Public hubs (including Railway) require a Bearer token. This is not
            an error in the app — this browser has not been authorized yet.
          </p>
        </div>

        {claimable && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void claim()}
            className="rounded-xl bg-gradient-to-b from-orange-400 to-orange-600 px-4 py-2.5 text-sm font-semibold text-orange-950 shadow-sm disabled:opacity-50"
          >
            {busy ? "Connecting…" : "Connect this browser (one-time setup)"}
          </button>
        )}

        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <label className="min-w-0 flex-1 text-xs font-medium">
            Or paste token (Railway Variables → OPENGATEWAY_AUTH_TOKEN)
            <input
              type="password"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="field-input mt-1 font-mono text-xs"
              placeholder="Master or device API key"
              autoComplete="off"
            />
          </label>
          <button
            type="button"
            className="rounded-xl border border-amber-700/30 bg-white px-4 py-2 text-sm font-semibold text-amber-950 dark:border-white/20 dark:bg-zinc-900 dark:text-amber-50"
            onClick={() => {
              if (draft.trim()) onToken(draft.trim());
            }}
          >
            Save &amp; connect
          </button>
        </div>

        {msg && <p className="text-xs opacity-90">{msg}</p>}

        <p className="text-[11px] leading-relaxed opacity-80">
          After connecting, open <strong>Agent tokens</strong> in the left menu
          to mint keys for Grok / Claude / Cursor MCP. Template pre-sets the
          master token on the server; this browser still needs it once.
        </p>
      </div>
    </div>
  );
}

function roomMsgToAgent(
  m: RoomMessage,
  meId: string | null,
  harnessMap: Record<string, string>
): AgentMessage {
  const parts: AgentMessage["parts"] = [];
  for (const p of m.message?.parts || []) {
    if (p.name || (p.content_type && !p.content_type.startsWith("text/"))) {
      parts.push({
        type: "file",
        name: p.name || "file",
        url: p.content_url
          ? p.content_url.startsWith("http")
            ? p.content_url
            : `${api.base}${p.content_url}`
          : undefined,
        contentType: p.content_type,
      });
      if (p.content && p.content_type?.startsWith("text/")) {
        parts.push({ type: "text", text: p.content });
      }
    } else if (p.content) {
      parts.push({ type: "text", text: p.content });
    }
  }
  if (parts.length === 0) parts.push({ type: "text", text: "" });

  const isSystem = m.from_name === "system" || Boolean(m.metadata?.system);
  const isMine = Boolean(meId && m.from_participant_id === meId);

  if (isSystem) {
    const text = parts
      .filter((x): x is { type: "text"; text: string } => x.type === "text")
      .map((x) => x.text)
      .join("\n");
    return {
      id: m.id,
      role: "system",
      parts: [{ type: "text", text }],
      createdAt: m.created_at,
    };
  }
  return {
    id: m.id,
    role: isMine ? "user" : "assistant",
    name: m.from_name,
    harness: harnessMap[m.from_name] || (isMine ? "human" : "other"),
    parts,
    createdAt: m.created_at,
  };
}

export default function App() {
  const { theme, toggle: toggleTheme } = useTheme();
  const [ping, setPing] = useState<Ping | null>(null);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [roomId, setRoomId] = useState<string | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [messages, setMessages] = useState<RoomMessage[]>([]);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [forks, setForks] = useState<Fork[]>([]);
  const [dmThreads, setDmThreads] = useState<DmThread[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const [displayName, setDisplayName] = useState(getStoredName());
  const [displayRole, setDisplayRole] = useState(getStoredRole());
  const [participantId, setParticipantId] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  // Mobile-first: drawers closed by default on narrow viewports
  const [leftOpen, setLeftOpen] = useState(
    () => typeof window !== "undefined" && window.innerWidth >= 768
  );
  const [railOpen, setRailOpen] = useState(
    () => typeof window !== "undefined" && window.innerWidth >= 1024
  );
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.innerWidth < 768
  );
  const [mobileSearchOpen, setMobileSearchOpen] = useState(false);
  const [leftSection, setLeftSection] = useState<LeftSection>("rooms");
  const [activeDmPeerId, setActiveDmPeerId] = useState<string | null>(null);
  const [highlightMessageId, setHighlightMessageId] = useState<string | null>(
    null
  );
  const [error, setError] = useState<{ message: string } | undefined>();
  const [showNewRoom, setShowNewRoom] = useState(false);
  const [gateways, setGateways] = useState<
    {
      id: string;
      name: string;
      mode: string;
      network: string;
      base_url: string;
      require_auth: boolean;
      is_self: boolean;
      notes?: string;
    }[]
  >([]);
  const [authToken, setAuthTokenState] = useState(getAuthToken());
  const [authNeeded, setAuthNeeded] = useState(false);
  const [sessionUser, setSessionUser] = useState<{
    email: string;
    role: string;
    display_name: string;
  } | null>(null);
  const [authGate, setAuthGate] = useState<"loading" | "login" | "app">(
    "loading"
  );
  const [notifications, setNotifications] = useState<AppNotification[]>([]);

  const pushNotif = useCallback((n: Omit<AppNotification, "read"> & { read?: boolean }) => {
    setNotifications((prev) => {
      if (prev.some((x) => x.id === n.id)) return prev;
      return [{ ...n, read: n.read ?? false }, ...prev].slice(0, 60);
    });
  }, []);

  const log = useCallback((line: string) => {
    const t = new Date().toLocaleTimeString();
    setEvents((prev) => [`${t}  ${line}`, ...prev].slice(0, 80));
  }, []);

  const harnessMap = useMemo(() => {
    const m: Record<string, string> = {};
    for (const p of participants) m[p.name] = p.harness;
    return m;
  }, [participants]);

  // Public room: only broadcasts (no to). DM view: private thread only.
  const visibleMessages = useMemo(() => {
    if (!participantId) {
      return messages.filter((m) => !m.to_participant_id);
    }
    if (!activeDmPeerId) {
      return messages.filter((m) => !m.to_participant_id);
    }
    return messages.filter(
      (m) =>
        m.to_participant_id &&
        ((m.from_participant_id === participantId &&
          m.to_participant_id === activeDmPeerId) ||
          (m.from_participant_id === activeDmPeerId &&
            m.to_participant_id === participantId))
    );
  }, [messages, activeDmPeerId, participantId]);

  const chatMessages = useMemo(
    () =>
      visibleMessages.map((m) => roomMsgToAgent(m, participantId, harnessMap)),
    [visibleMessages, participantId, harnessMap]
  );

  const bookmarkedIds = useMemo(
    () => new Set(bookmarks.map((b) => b.message_id)),
    [bookmarks]
  );

  const mentionables = useMemo(() => {
    const people = participants
      .filter((p) => p.id !== participantId)
      .map((p) => ({ id: p.id, name: p.name, harness: p.harness }));
    // Synthetic @all — server treats "everyone" / "@all" as full nudge
    return [
      { id: "__all__", name: "all", harness: "broadcast" },
      ...people,
    ];
  }, [participants, participantId]);

  const refreshGateways = useCallback(async () => {
    try {
      const res = await api.listGateways();
      setGateways(res.gateways || []);
    } catch {
      setGateways([]);
    }
  }, []);

  const refreshPing = useCallback(async () => {
    try {
      setPing(await api.ping());
      await refreshGateways();
    } catch (e) {
      setPing(null);
      log(`ping failed: ${e instanceof Error ? e.message : e}`);
    }
  }, [log, refreshGateways]);

  const refreshRooms = useCallback(async () => {
    try {
      const { rooms: list } = await api.listRooms();
      setRooms(list);
      setAuthNeeded(false);
      return list;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (/unauthoriz/i.test(msg) || /401/.test(msg)) {
        setAuthNeeded(true);
        setRooms([]);
        return [];
      }
      throw e;
    }
  }, []);

  const refreshDms = useCallback(
    async (rid: string, pid: string) => {
      try {
        const { threads } = await api.listDms(rid, pid);
        setDmThreads(threads);
      } catch {
        setDmThreads([]);
      }
    },
    []
  );

  const refreshSnapshot = useCallback(
    async (id: string) => {
      const snap = await api.snapshot(id);
      setRoom(snap.room);
      setMessages(snap.messages || []);
      setParticipants(snap.participants || []);
      setTasks(snap.tasks || []);
      setArtifacts(snap.artifacts || []);
      setBookmarks(snap.bookmarks || []);
      setForks(snap.forks || []);
    },
    []
  );

  const ensureJoined = useCallback(
    async (id: string, name: string, existingPid?: string | null) => {
      const pid = existingPid || getStoredPid(id);
      const role = getStoredRole();
      const p = await api.join(id, {
        name: name.trim() || "human",
        harness: "human",
        role: role || "observer",
        capabilities: ["monitor", "chat"],
        participant_id: pid || undefined,
      });
      setParticipantId(p.id);
      setStoredPid(id, p.id);
      setStoredName(name.trim() || "human");
      setDisplayRole(p.role || role || "observer");
      if (p.role) setStoredRole(p.role);
      log(`session ${p.id.slice(0, 8)}… as ${p.name}`);
      await refreshDms(id, p.id);
      return p;
    },
    [log, refreshDms]
  );

  const selectRoom = useCallback(
    async (id: string) => {
      setRoomId(id);
      setActiveDmPeerId(null);
      setLeftSection("rooms");
      setError(undefined);
      const name = getStoredName();
      setDisplayName(name);
      const pid = getStoredPid(id);
      setParticipantId(pid);
      await refreshSnapshot(id);
      await ensureJoined(id, name, pid);
      await refreshSnapshot(id);
      window.location.hash = id;
    },
    [refreshSnapshot, ensureJoined]
  );

  useEffect(() => {
    if (!roomId) return;
    const es = new EventSource(api.eventsUrl(roomId));
    es.onopen = () => {
      setLive(true);
      log("SSE connected");
    };
    es.onerror = () => {
      setLive(false);
      log("SSE reconnecting…");
    };
    const onMessage = (raw: string) => {
      try {
        const data = JSON.parse(raw);
        const msg = data.payload?.message || data.message;
        if (!msg?.id) return;
        setMessages((prev) =>
          prev.some((m) => m.id === msg.id) ? prev : [...prev, msg]
        );
        if (participantId) refreshDms(roomId, participantId);

        // Notifications: incoming DMs / room chatter while in a DM / @mentions
        const fromId = msg.from_participant_id as string | undefined;
        const toId = msg.to_participant_id as string | null | undefined;
        const fromName = (msg.from_name as string) || "agent";
        const text =
          msg.message?.parts?.[0]?.content ||
          (msg.message?.parts || [])
            .map((p: { content?: string }) => p.content)
            .filter(Boolean)
            .join(" ") ||
          "";
        const mine = participantId && fromId === participantId;
        if (mine) return;

        if (toId && participantId && toId === participantId) {
          // Direct message to me
          if (activeDmPeerId !== fromId) {
            pushNotif({
              id: `dm-${msg.id}`,
              kind: "dm",
              title: fromName,
              body: text.slice(0, 140) || "New direct message",
              createdAt: msg.created_at || new Date().toISOString(),
              roomId,
              peerId: fromId,
              messageId: msg.id,
            });
          }
        } else if (!toId) {
          const lower = text.toLowerCase();
          const myName = (displayName || "").toLowerCase();
          if (
            myName &&
            (lower.includes(`@${myName}`) ||
              lower.includes("@all") ||
              lower.includes("everyone"))
          ) {
            pushNotif({
              id: `mention-${msg.id}`,
              kind: "mention",
              title: fromName,
              body: text.slice(0, 140) || "Mentioned you",
              createdAt: msg.created_at || new Date().toISOString(),
              roomId,
              messageId: msg.id,
            });
          } else if (activeDmPeerId) {
            // Room activity while viewing a DM
            pushNotif({
              id: `room-${msg.id}`,
              kind: "room",
              title: fromName,
              body: text.slice(0, 140) || "Room message",
              createdAt: msg.created_at || new Date().toISOString(),
              roomId,
              messageId: msg.id,
            });
          }
        }
      } catch {
        /* ignore */
      }
    };
    const onSide = (type: string, raw: string) => {
      try {
        const data = JSON.parse(raw);
        log(`${type}: ${data.payload?.action || ""}`);
        refreshSnapshot(roomId).catch(() => {});
        if (type === "task" && data.payload?.action === "created") {
          const t = data.payload?.task;
          if (t?.claimed_by === participantId || t?.metadata?.assignee_id === participantId) {
            pushNotif({
              id: `task-${t.id}`,
              kind: "task",
              title: t.title || "Task assigned",
              body: (t.description || "You were assigned a task").slice(0, 140),
              createdAt: t.created_at || new Date().toISOString(),
              roomId,
            });
          }
        }
      } catch {
        /* ignore */
      }
    };
    es.addEventListener("message", (ev) => onMessage(ev.data));
    for (const t of [
      "participant",
      "task",
      "artifact",
      "room",
      "bookmark",
      "fork",
    ]) {
      es.addEventListener(t, (ev) => onSide(t, ev.data));
    }
    return () => {
      es.close();
      setLive(false);
    };
  }, [
    roomId,
    log,
    refreshSnapshot,
    refreshDms,
    participantId,
    activeDmPeerId,
    displayName,
    pushNotif,
  ]);

  // Mark DM notifications read when opening that thread
  useEffect(() => {
    if (!activeDmPeerId) return;
    setNotifications((prev) =>
      prev.map((n) =>
        n.kind === "dm" && n.peerId === activeDmPeerId
          ? { ...n, read: true }
          : n
      )
    );
  }, [activeDmPeerId]);

  useEffect(() => {
    const onResize = () => {
      const mobile = window.innerWidth < 768;
      setIsMobile(mobile);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    (async () => {
      // Auth gate: login page unless session / bearer already present
      try {
        const st = await api.authStatus();
        const token = getAuthToken();
        if (token) {
          try {
            const me = await api.authMe();
            if (me.user) setSessionUser(me.user);
            setAuthGate("app");
          } catch {
            // Bearer master still ok even if /me has no user
            if (token.startsWith("ogs_")) {
              setAuthToken("");
              setAuthTokenState("");
              setAuthGate("login");
              return;
            }
            setAuthGate("app");
          }
        } else if (st.require_auth !== false) {
          setAuthGate("login");
          // still allow pair deep-link below if token in hash
        } else {
          setAuthGate("app");
        }
      } catch {
        setAuthGate(getAuthToken() ? "app" : "login");
      }

      // Deep link: #pair=CODE&room=ID&token=…
      const hash = window.location.hash.replace(/^#/, "");
      const params = new URLSearchParams(
        hash.includes("=") ? hash.replace(/&/g, "&") : ""
      );
      // Support both #pair=x&room=y and #room=id
      const hashParts = Object.fromEntries(
        hash.split("&").map((p) => {
          const [k, ...rest] = p.split("=");
          return [k, rest.join("=")];
        })
      );
      const pairCode = hashParts.pair || params.get("pair");
      const hashRoom = hashParts.room || params.get("room");
      const hashToken = hashParts.token || params.get("token");
      if (hashToken) {
        setAuthToken(hashToken);
        setAuthTokenState(hashToken);
        setAuthGate("app");
      }
      if (pairCode) {
        try {
          const redeemed = await api.redeemPair(
            pairCode,
            getStoredName() || "mobile"
          );
          if (redeemed.auth_token) {
            setAuthToken(redeemed.auth_token);
            setAuthTokenState(redeemed.auth_token);
            setAuthGate("app");
          }
          if (redeemed.suggested_name) {
            setDisplayName(redeemed.suggested_name);
            setStoredName(redeemed.suggested_name);
          }
          log(`paired as ${redeemed.suggested_name || "mobile"}`);
        } catch (e) {
          log(`pair failed: ${e instanceof Error ? e.message : e}`);
        }
      }

      if (getAuthToken() || authGate === "app") {
        setAuthGate("app");
        await refreshPing();
        const list = await refreshRooms();
        const pick =
          list.find(
            (r) =>
              r.id === hashRoom ||
              r.name === hashRoom ||
              r.id === hash ||
              r.name === hash
          ) || list[0];
        if (pick) await selectRoom(pick.id);
        if (hashToken || pairCode) {
          const clean = hashRoom ? `room=${hashRoom}` : pick ? pick.id : "";
          window.location.hash = clean;
        }
      }
    })().catch((e) =>
      setError({ message: e instanceof Error ? e.message : String(e) })
    );
    const t = setInterval(() => {
      if (getAuthToken()) void refreshPing();
    }, 15000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (authGate === "loading") {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-zinc-50 text-sm text-zinc-500 dark:bg-zinc-950">
        Loading OpenGateway…
      </div>
    );
  }

  if (authGate === "login") {
    return (
      <LoginPage
        onAuthenticated={(token, user) => {
          setAuthToken(token);
          setAuthTokenState(token);
          if (user) {
            setSessionUser(user);
            if (user.display_name) {
              setDisplayName(user.display_name);
              setStoredName(user.display_name);
            }
          }
          setAuthGate("app");
          setAuthNeeded(false);
          void refreshPing();
          void refreshRooms();
        }}
      />
    );
  }

  const onSend = async ({
    content,
    files,
    mentionIds,
  }: {
    role: "user";
    content: string;
    files: File[];
    mentionIds: string[];
  }) => {
    if (!roomId || !participantId) return;
    try {
      const uploaded: {
        id: string;
        name: string;
        content_type: string;
        content_url?: string | null;
      }[] = [];
      for (const f of files) {
        const art = await api.uploadFile(roomId, participantId, f);
        uploaded.push(art);
        log(`uploaded ${art.name}`);
      }
      const parts: {
        name?: string;
        content_type: string;
        content?: string;
        content_url?: string;
      }[] = [];
      if (content.trim()) {
        parts.push({ content_type: "text/plain", content: content.trim() });
      }
      for (const u of uploaded) {
        parts.push({
          name: u.name,
          content_type: u.content_type || "application/octet-stream",
          content_url: u.content_url || api.fileUrl(roomId, u.id),
        });
      }

      // Room posts stay public (@mentions still visible). Only DM mode is private.
      const allCall =
        isAllCall(content) ||
        mentionIds.includes("__all__") ||
        mentionIds.some((id) => id === "__all__");
      // Explicit DM only when user opened a DM thread (or future private mode)
      let to = activeDmPeerId || undefined;

      const res = await api.postMessage(roomId, {
        from_participant_id: participantId,
        content:
          content.trim() ||
          uploaded.map((u) => `📎 ${u.name}`).join("\n"),
        nudge_all: !activeDmPeerId && allCall,
        to_participant_id: to,
        parts: parts.length ? parts : undefined,
        metadata: uploaded.length
          ? { files: uploaded.map((u) => u.id) }
          : undefined,
      });
      if (res && typeof res === "object" && "nudge_count" in res) {
        log(`nudged ${(res as { nudge_count: number }).nudge_count} agent(s)`);
      }
      await refreshSnapshot(roomId);
      await refreshDms(roomId, participantId);
    } catch (e) {
      setError({ message: e instanceof Error ? e.message : String(e) });
    }
  };

  /** Local draft only — do not trim/API on every keystroke (breaks spaces). */
  const onNameDraft = (name: string) => {
    setDisplayName(name);
  };

  /** Commit identity: trim ends only, keep internal spaces (e.g. "Mark Dula"). */
  const onNameCommit = async (name: string) => {
    // Collapse runs of whitespace to single spaces; trim ends — allow multi-word names
    const next = name.replace(/\s+/g, " ").trim() || "human";
    setDisplayName(next);
    setStoredName(next);
    if (!roomId || !participantId) {
      log(`identity saved locally as ${next} (join a room to sync)`);
      return;
    }
    try {
      const p = await api.updateParticipant(roomId, participantId, {
        name: next,
      });
      setParticipantId(p.id);
      setStoredPid(roomId, p.id);
      setStoredName(p.name || next);
      setDisplayName(p.name || next);
      log(`renamed → ${p.name || next}`);
      await refreshSnapshot(roomId);
    } catch (e) {
      log(`rename failed: ${e instanceof Error ? e.message : e}`);
      try {
        await ensureJoined(roomId, next, participantId);
      } catch (e2) {
        setError({
          message: `Could not update identity: ${e instanceof Error ? e.message : e}`,
        });
      }
    }
  };

  const onRoleDraft = (role: string) => {
    setDisplayRole(role);
  };

  const onRoleCommit = async (role: string) => {
    const next = role.replace(/\s+/g, " ").trim() || "observer";
    setDisplayRole(next);
    setStoredRole(next);
    if (!roomId || !participantId) {
      log(`role saved locally as ${next}`);
      return;
    }
    try {
      const p = await api.updateParticipant(roomId, participantId, {
        role: next,
      });
      setDisplayRole(p.role || next);
      log(`role → ${p.role || next}`);
      await refreshSnapshot(roomId);
    } catch (e) {
      log(`role update failed: ${e instanceof Error ? e.message : e}`);
      setError({
        message: `Could not update role: ${e instanceof Error ? e.message : e}`,
      });
    }
  };

  const onBookmark = async (messageId: string) => {
    if (!roomId || !participantId) return;
    const existing = bookmarks.find(
      (b) => b.message_id === messageId && b.created_by === participantId
    );
    if (existing) {
      await api.deleteBookmark(roomId, existing.id);
      log("bookmark removed");
    } else {
      const msg = messages.find((m) => m.id === messageId);
      const excerpt = msg?.message?.parts?.[0]?.content?.slice(0, 160) || "";
      await api.createBookmark(roomId, {
        message_id: messageId,
        title: excerpt.slice(0, 48) || `Bookmark · ${msg?.from_name || "msg"}`,
        excerpt,
        created_by: participantId,
      });
      log("bookmarked");
    }
    await refreshSnapshot(roomId);
  };

  const onFork = async (messageId: string) => {
    if (!roomId || !participantId) return;
    await api.createFork(roomId, {
      root_message_id: messageId,
      created_by: participantId,
      created_by_name: displayName,
    });
    log("fork created");
    setLeftSection("forks");
    await refreshSnapshot(roomId);
  };

  const dmPeer = participants.find((p) => p.id === activeDmPeerId);
  const chatTitle = activeDmPeerId
    ? `DM · ${dmPeer?.name || activeDmPeerId.slice(0, 8)}`
    : room?.name || "Select a room";
  const chatGoal = activeDmPeerId
    ? "Private thread — only you and this agent"
    : room?.goal || "Agent communication over ACP + MCP";

  return (
    <div className="og-shell flex overflow-hidden bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
      <div
        aria-hidden
        className="pointer-events-none fixed inset-0 bg-[radial-gradient(900px_500px_at_15%_-5%,rgba(249,115,22,0.08),transparent_55%)] opacity-70 dark:opacity-100"
      />

      {/* Mobile drawer backdrop */}
      {isMobile && (leftOpen || railOpen) && (
        <button
          type="button"
          aria-label="Close panels"
          className="og-drawer-backdrop"
          onClick={() => {
            setLeftOpen(false);
            setRailOpen(false);
          }}
        />
      )}

      <div
        className={
          isMobile
            ? leftOpen
              ? "og-drawer-left"
              : "hidden"
            : undefined
        }
      >
        <OpsSidebar
          rooms={rooms}
          activeRoomId={roomId}
          ping={ping}
          displayName={displayName}
          displayRole={displayRole}
          participantId={participantId}
          participants={participants}
          dmThreads={dmThreads}
          forks={forks}
          gateways={gateways}
          activeDmPeerId={activeDmPeerId}
          section={leftSection}
          collapsed={!isMobile && !leftOpen}
          authToken={authToken}
          onToggleCollapsed={() => setLeftOpen((v) => !v)}
          onSection={(s) => {
            setLeftSection(s);
          }}
          onSelectRoom={(id) => {
            selectRoom(id).catch((e) =>
              setError({ message: e instanceof Error ? e.message : String(e) })
            );
            if (isMobile) setLeftOpen(false);
          }}
          onSelectDm={(peerId) => {
            setActiveDmPeerId(peerId);
            if (peerId) setLeftSection("dms");
            if (isMobile) setLeftOpen(false);
          }}
          onSelectFork={(forkId) => {
            const f = forks.find((x) => x.id === forkId);
            if (f) {
              setActiveDmPeerId(null);
              setHighlightMessageId(f.root_message_id);
              setTimeout(() => setHighlightMessageId(null), 2500);
            }
            if (isMobile) setLeftOpen(false);
          }}
          onRefresh={() => {
            refreshPing();
            refreshRooms();
            if (roomId) {
              refreshSnapshot(roomId);
              if (participantId) refreshDms(roomId, participantId);
            }
          }}
          onNewRoom={() => setShowNewRoom(true)}
          onNameChange={onNameDraft}
          onNameCommit={onNameCommit}
          onRoleChange={onRoleDraft}
          onRoleCommit={onRoleCommit}
          onAuthTokenChange={(token) => {
            setAuthToken(token);
            setAuthTokenState(token);
            setAuthNeeded(false);
            void (async () => {
              await refreshPing();
              try {
                const list = await refreshRooms();
                if (list[0] && !roomId) await selectRoom(list[0].id);
                else if (roomId) {
                  await refreshSnapshot(roomId);
                  if (participantId) await refreshDms(roomId, participantId);
                }
              } catch (e) {
                setError({
                  message: e instanceof Error ? e.message : String(e),
                });
              }
            })();
          }}
        />
      </div>

      <main className="relative flex min-w-0 flex-1 flex-col">
        {(authNeeded ||
          (ping?.require_auth && !authToken) ||
          (error?.message && /unauthoriz/i.test(error.message))) && (
          <AuthConnectBanner
            authToken={authToken}
            onToken={(t) => {
              setAuthToken(t);
              setAuthTokenState(t);
              setAuthNeeded(false);
              setError(undefined);
              void refreshRooms().then((list) => {
                if (list[0] && !roomId) void selectRoom(list[0].id);
              });
            }}
          />
        )}
        {activeDmPeerId && (
          <div className="border-b border-violet-500/30 bg-violet-50 px-4 py-2 text-xs text-violet-900 dark:bg-violet-500/10 dark:text-violet-100">
            Viewing <strong>private DM</strong> with{" "}
            {dmPeer?.name || "agent"} — not the room thread.{" "}
            <button
              type="button"
              className="font-semibold underline"
              onClick={() => setActiveDmPeerId(null)}
            >
              Back to room chat
            </button>
          </div>
        )}
        <header className="flex items-center gap-2 border-b border-zinc-200 bg-white/80 px-3 py-2.5 backdrop-blur-md dark:border-white/[0.06] dark:bg-zinc-950/70 sm:gap-3 sm:px-5 sm:py-3">
          {isMobile && (
            <button
              type="button"
              onClick={() => {
                setRailOpen(false);
                setLeftOpen(true);
              }}
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-zinc-200 bg-white text-zinc-600 dark:border-white/10 dark:bg-zinc-900 dark:text-zinc-300"
              title="Menu"
            >
              <span className="text-lg leading-none">☰</span>
            </button>
          )}
          <div className="og-header-title min-w-0 shrink sm:max-w-[28%]">
            <h1 className="truncate text-base font-semibold tracking-tight sm:text-lg">
              {chatTitle}
            </h1>
            <p className="mt-0.5 hidden truncate text-sm text-zinc-500 sm:block">
              {chatGoal}
            </p>
          </div>
          <div className="og-search-wrap min-w-0 flex-1">
            <GlobalSearch
              onNavigate={(hit: SearchHit) => {
                void (async () => {
                  if (hit.room_id && hit.room_id !== roomId) {
                    await selectRoom(hit.room_id);
                  }
                  if (hit.type === "participant" && hit.id) {
                    setActiveDmPeerId(hit.id);
                    setLeftSection("dms");
                  } else if (hit.type === "dm") {
                    const from =
                      hit.path?.match(/dm_from=([^&]+)/)?.[1] ||
                      (hit.meta?.from_participant_id as string | undefined);
                    const to =
                      hit.path?.match(/dm_to=([^&]+)/)?.[1] ||
                      (hit.meta?.to_participant_id as string | undefined);
                    const peer =
                      from && participantId && from === participantId
                        ? to
                        : from || to;
                    if (peer) {
                      setActiveDmPeerId(peer);
                      setLeftSection("dms");
                    }
                    const msgMatch = hit.path?.match(/msg=([^&]+)/);
                    if (msgMatch?.[1] || hit.id) {
                      setHighlightMessageId(msgMatch?.[1] || hit.id);
                      setTimeout(() => setHighlightMessageId(null), 2800);
                    }
                  } else if (
                    hit.type === "message" ||
                    hit.type === "bookmark" ||
                    hit.type === "fork"
                  ) {
                    setActiveDmPeerId(null);
                    setHighlightMessageId(hit.id);
                    const msgMatch = hit.path?.match(/msg=([^&]+)/);
                    if (msgMatch?.[1]) setHighlightMessageId(msgMatch[1]);
                    setTimeout(() => setHighlightMessageId(null), 2800);
                  } else if (hit.type === "room") {
                    setActiveDmPeerId(null);
                  }
                })().catch((e) =>
                  setError({
                    message: e instanceof Error ? e.message : String(e),
                  })
                );
              }}
            />
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {isMobile && (
              <button
                type="button"
                onClick={() => setMobileSearchOpen((v) => !v)}
                className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-zinc-200 bg-white text-zinc-600 dark:border-white/10 dark:bg-zinc-900 dark:text-zinc-300"
                title="Search"
              >
                <span className="text-sm">⌕</span>
              </button>
            )}
            <NotificationBell
              items={notifications}
              onMarkAllRead={() =>
                setNotifications((prev) =>
                  prev.map((n) => ({ ...n, read: true }))
                )
              }
              onMarkRead={(id) =>
                setNotifications((prev) =>
                  prev.map((n) => (n.id === id ? { ...n, read: true } : n))
                )
              }
              onSelect={(n) => {
                if (n.kind === "dm" && n.peerId) {
                  setActiveDmPeerId(n.peerId);
                  setLeftSection("dms");
                } else {
                  setActiveDmPeerId(null);
                  if (n.messageId) {
                    setHighlightMessageId(n.messageId);
                    setTimeout(() => setHighlightMessageId(null), 2800);
                  }
                }
              }}
            />
            {!isMobile && !leftOpen && (
              <button
                type="button"
                onClick={() => setLeftOpen(true)}
                className="rounded-full border border-zinc-200 bg-white px-3 py-1.5 text-xs font-semibold dark:border-white/10 dark:bg-zinc-900"
              >
                Menu
              </button>
            )}
            <button
              type="button"
              onClick={toggleTheme}
              className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-zinc-200 bg-white text-zinc-600 dark:border-white/10 dark:bg-zinc-900 dark:text-zinc-300"
              title={theme === "dark" ? "Day mode" : "Night mode"}
            >
              {theme === "dark" ? (
                <Sun className="h-4 w-4" />
              ) : (
                <Moon className="h-4 w-4" />
              )}
            </button>
            <button
              type="button"
              onClick={() => {
                if (isMobile) setLeftOpen(false);
                setRailOpen((v) => !v);
              }}
              className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-zinc-200 bg-white text-zinc-600 dark:border-white/10 dark:bg-zinc-900 dark:text-zinc-300"
              title={railOpen ? "Hide right panel" : "Show right panel"}
            >
              {railOpen ? (
                <PanelRightClose className="h-4 w-4" />
              ) : (
                <PanelRightOpen className="h-4 w-4" />
              )}
            </button>
            <span
              className={
                live
                  ? "inline-flex items-center gap-1.5 rounded-full border border-orange-500/30 bg-orange-500/10 px-2.5 py-1 text-xs font-medium text-orange-800 dark:text-orange-200"
                  : "inline-flex items-center gap-1.5 rounded-full border border-zinc-200 bg-zinc-100 px-2.5 py-1 text-xs font-medium text-zinc-500 dark:border-white/10 dark:bg-zinc-900"
              }
            >
              <span
                className={
                  live
                    ? "h-1.5 w-1.5 rounded-full bg-orange-500"
                    : "h-1.5 w-1.5 rounded-full bg-zinc-400"
                }
              />
              {live ? "Live" : "Offline"}
            </span>
            <span className="hidden rounded-full border border-zinc-200 bg-white px-2.5 py-1 text-xs text-zinc-500 md:inline dark:border-white/10 dark:bg-zinc-900">
              {visibleMessages.length} messages
            </span>
            {activeDmPeerId && (
              <button
                type="button"
                onClick={() => setActiveDmPeerId(null)}
                className="rounded-full border border-violet-300 bg-violet-50 px-2.5 py-1 text-xs font-medium text-violet-800 dark:border-violet-500/30 dark:bg-violet-500/10 dark:text-violet-200"
              >
                Exit DM
              </button>
            )}
          </div>
        </header>

        {isMobile && mobileSearchOpen && (
          <div className="og-search-wrap-mobile border-b border-zinc-200 px-3 py-2 dark:border-white/10">
            <GlobalSearch
              onNavigate={(hit: SearchHit) => {
                setMobileSearchOpen(false);
                void (async () => {
                  if (hit.room_id && hit.room_id !== roomId) {
                    await selectRoom(hit.room_id);
                  }
                  if (hit.type === "participant" && hit.id) {
                    setActiveDmPeerId(hit.id);
                  } else if (hit.type === "dm") {
                    const from = hit.path?.match(/dm_from=([^&]+)/)?.[1];
                    const to = hit.path?.match(/dm_to=([^&]+)/)?.[1];
                    const peer =
                      from && participantId && from === participantId
                        ? to
                        : from || to;
                    if (peer) setActiveDmPeerId(peer);
                  } else {
                    setActiveDmPeerId(null);
                    if (hit.id) {
                      setHighlightMessageId(hit.id);
                      setTimeout(() => setHighlightMessageId(null), 2800);
                    }
                  }
                })();
              }}
            />
          </div>
        )}

        <div className="flex min-h-0 flex-1">
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            <AgentChat
              className="min-h-0 flex-1"
              messages={chatMessages}
              onSend={onSend}
              status={participantId ? "ready" : "idle"}
              disabled={!participantId}
              error={error}
              mentionables={mentionables}
              bookmarkedIds={bookmarkedIds}
              onBookmark={onBookmark}
              onFork={onFork}
              highlightMessageId={highlightMessageId}
              modeLabel={
                activeDmPeerId
                  ? `DM · ${dmPeer?.name || "peer"}${
                      dmPeer?.status === "online" ? " · online" : " · offline"
                    }`
                  : "Room · public"
              }
              placeholder={
                activeDmPeerId
                  ? `Private message to ${dmPeer?.name || "agent"}…`
                  : "Message the room…  @all  ·  @agent  ·  attach files"
              }
              emptyState={
                <div className="flex flex-col items-center gap-3 text-center">
                  <img
                    src={`${import.meta.env.BASE_URL}og-logo.png`}
                    alt=""
                    className="og-logo h-20 w-20"
                  />
                  <h3 className="text-base font-semibold tracking-tight text-zinc-700 dark:text-zinc-300">
                    {activeDmPeerId ? "No DMs yet" : "Awaiting room traffic"}
                  </h3>
                  <p className="max-w-[340px] text-sm leading-relaxed text-zinc-500">
                    {activeDmPeerId ? (
                      <>
                        Private thread with{" "}
                        <strong>{dmPeer?.name || "agent"}</strong>. Only you two
                        see these messages.
                      </>
                    ) : (
                      <>
                        Type <strong>@all</strong> to reach every agent.{" "}
                        <strong>@name</strong> or click a participant to open a
                        private DM. Hover messages to bookmark or fork.
                      </>
                    )}
                  </p>
                </div>
              }
              footerExtra={
                <div className="flex w-full flex-wrap items-center justify-between gap-2 text-xs text-zinc-500">
                  <span>
                    {participantId
                      ? `as ${displayName} (${displayRole}) · private DMs filtered from room · ⌘K search`
                      : "Join a room to send"}
                  </span>
                </div>
              }
            />
          </div>
          {railOpen && (
            <div
              className={
                isMobile ? "og-drawer-right h-full bg-zinc-50 dark:bg-zinc-950" : "contents"
              }
            >
              <SideRail
                participants={participants}
                tasks={tasks}
                artifacts={artifacts}
                bookmarks={bookmarks}
                events={events}
                meId={participantId}
                activeDmPeerId={activeDmPeerId}
                onOpenDm={(peerId) => {
                  setActiveDmPeerId(peerId);
                  setLeftSection("dms");
                  if (isMobile) setRailOpen(false);
                }}
                onJumpMessage={(id) => {
                  setActiveDmPeerId(null);
                  setHighlightMessageId(id);
                  setTimeout(() => setHighlightMessageId(null), 2500);
                  if (isMobile) setRailOpen(false);
                }}
              />
            </div>
          )}
        </div>
      </main>

      {showNewRoom && (
        <NewRoomModal
          createdBy={displayName}
          onClose={() => setShowNewRoom(false)}
          onCreated={async (r) => {
            setShowNewRoom(false);
            await refreshRooms();
            await selectRoom(r.id);
          }}
        />
      )}
    </div>
  );
}

function NewRoomModal({
  createdBy,
  onClose,
  onCreated,
}: {
  createdBy: string;
  onClose: () => void;
  onCreated: (r: Room) => void;
}) {
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/65 backdrop-blur-sm">
      <form
        className="w-[min(420px,92vw)] space-y-3.5 rounded-2xl border border-zinc-200 bg-white p-5 shadow-2xl dark:border-white/10 dark:bg-zinc-900"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr("");
          try {
            const r = await api.createRoom({
              name,
              goal,
              project_path: path || undefined,
              created_by: createdBy || "human",
            });
            onCreated(r);
          } catch (ex) {
            setErr(ex instanceof Error ? ex.message : String(ex));
          } finally {
            setBusy(false);
          }
        }}
      >
        <h2 className="text-base font-semibold tracking-tight">Create room</h2>
        <label className="block text-xs font-medium text-zinc-500">
          Name
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="field-input mt-1"
            placeholder="feature-x"
          />
        </label>
        <label className="block text-xs font-medium text-zinc-500">
          Goal
          <input
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            className="field-input mt-1"
            placeholder="Ship the thing together"
          />
        </label>
        <label className="block text-xs font-medium text-zinc-500">
          Project path
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            className="field-input mt-1"
            placeholder="/path/to/repo"
          />
        </label>
        {err && <p className="text-xs text-rose-500">{err}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="btn-ghost">
            Cancel
          </button>
          <button type="submit" disabled={busy} className="btn-primary">
            Create
          </button>
        </div>
      </form>
    </div>
  );
}
