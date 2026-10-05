import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  FileText,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Square,
  Trash2,
  Wifi,
} from "lucide-react";
import { api } from "@/lib/api";
import { MANAGED_HARNESS_LABELS, runnerIsConnected } from "@/lib/agentKit";
import { cn } from "@/lib/utils";
import type {
  AgentRunner,
  ManagedAgent,
  ManagedAgentLogEntry,
  ManagedAgentLogs,
  Participant,
  Room,
} from "@/lib/types";

type Props = {
  rooms: Room[];
  currentRoomId: string | null;
  participants: Participant[];
  refreshKey?: number;
  onAddAgent: () => void;
  onChanged?: () => void;
  onSelectRoom?: (roomId: string) => void;
  onCountChange?: (count: number) => void;
};

const stateOf = (agent: ManagedAgent) =>
  (agent.state || agent.status || "unknown").toLowerCase();

function statusClass(state: string): string {
  if (state === "running") {
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200";
  }
  if (["failed", "error"].includes(state)) {
    return "border-rose-500/30 bg-rose-500/10 text-rose-800 dark:text-rose-200";
  }
  if (
    ["pending", "starting", "restarting", "stopping", "deleting"].includes(
      state
    )
  ) {
    return "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-200";
  }
  return "border-zinc-200 bg-zinc-100 text-black dark:border-white/10 dark:bg-zinc-800 dark:text-white";
}

function boundedLogs(payload: ManagedAgentLogs): string[] {
  const raw = payload.lines ?? payload.logs ?? [];
  const lines = typeof raw === "string" ? raw.split(/\r?\n/) : raw;
  return lines
    .map((entry: string | ManagedAgentLogEntry) => {
      if (typeof entry === "string") return entry;
      return [
        entry.timestamp || entry.created_at,
        entry.level || entry.stream,
        entry.message || entry.line,
      ]
        .filter(Boolean)
        .join("  ");
    })
    .filter(Boolean)
    .slice(-200);
}

function isListening(
  agent: ManagedAgent,
  participants: Participant[]
): boolean {
  if (
    agent.presence === "listening" ||
    (stateOf(agent) === "running" && Boolean(agent.last_poll_at))
  ) {
    return true;
  }
  const participant = participants.find(
    (candidate) =>
      candidate.metadata?.managed_agent_id === agent.id ||
      candidate.id === agent.participant_id ||
      (candidate.name.trim().toLowerCase() ===
        agent.name.trim().toLowerCase() &&
        String(candidate.harness).toLowerCase() ===
          String(agent.harness).toLowerCase())
  );
  return Boolean(
    participant &&
      (participant.presence === "listening" ||
        (participant.status === "online" && participant.last_poll_at))
  );
}

export function ManagedAgentsPanel({
  rooms,
  currentRoomId,
  participants,
  refreshKey = 0,
  onAddAgent,
  onChanged,
  onSelectRoom,
  onCountChange,
}: Props) {
  const [agents, setAgents] = useState<ManagedAgent[]>([]);
  const [runners, setRunners] = useState<AgentRunner[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [openLogs, setOpenLogs] = useState<string | null>(null);
  const [logs, setLogs] = useState<Record<string, string[]>>({});
  const [logError, setLogError] = useState<Record<string, string>>({});
  const [logBusy, setLogBusy] = useState<string | null>(null);
  const [moveRoomId, setMoveRoomId] = useState<Record<string, string>>({});

  const load = useCallback(async (showSpinner = false) => {
    if (showSpinner) setRefreshing(true);
    try {
      const [managed, runnerList] = await Promise.all([
        api.listManagedAgents(),
        api.listRunners(),
      ]);
      const rows = managed.agents.filter(
        (agent) => !["deleted", "deleting"].includes(stateOf(agent))
      );
      setAgents(managed.agents);
      setRunners(runnerList.runners);
      onCountChange?.(rows.length);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [onCountChange]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (refreshKey) void load();
  }, [load, refreshKey]);

  const roomNames = useMemo(
    () => new Map(rooms.map((room) => [room.id, room.name])),
    [rooms]
  );
  const runnerNames = useMemo(
    () => new Map(runners.map((runner) => [runner.id, runner.name])),
    [runners]
  );

  const action = async (
    agent: ManagedAgent,
    operation: "stop" | "restart"
  ) => {
    setBusy((value) => ({ ...value, [agent.id]: operation }));
    try {
      await api.managedAgentAction(agent.id, operation);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy((value) => {
        const next = { ...value };
        delete next[agent.id];
        return next;
      });
    }
  };

  const moveToRoom = async (agent: ManagedAgent) => {
    const target = (moveRoomId[agent.id] || "").trim();
    if (!target || target === agent.room_id) return;
    const label =
      roomNames.get(target) || rooms.find((room) => room.id === target)?.name;
    if (
      !window.confirm(
        `Move “${agent.name}” to room “${label || target}”? The agent will leave its current room and start listening in the new one.`
      )
    ) {
      return;
    }
    setBusy((value) => ({ ...value, [agent.id]: "move" }));
    try {
      await api.moveManagedAgent(agent.id, target);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy((value) => {
        const next = { ...value };
        delete next[agent.id];
        return next;
      });
    }
  };

  const remove = async (agent: ManagedAgent) => {
    if (!window.confirm(`Delete managed agent “${agent.name}”?`)) return;
    setBusy((value) => ({ ...value, [agent.id]: "delete" }));
    try {
      await api.deleteManagedAgent(agent.id);
      setOpenLogs((value) => (value === agent.id ? null : value));
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy((value) => {
        const next = { ...value };
        delete next[agent.id];
        return next;
      });
    }
  };

  const fetchLogs = async (id: string) => {
    setLogBusy(id);
    setLogError((value) => ({ ...value, [id]: "" }));
    try {
      const payload = await api.managedAgentLogs(id, 200);
      setLogs((value) => ({ ...value, [id]: boundedLogs(payload) }));
    } catch (err) {
      setLogError((value) => ({
        ...value,
        [id]: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      setLogBusy((value) => (value === id ? null : value));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-2 py-3 text-xs text-zinc-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading managed agents…
      </div>
    );
  }

  return (
    <div className="space-y-2.5 px-1 pb-1">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[10px] leading-relaxed text-zinc-500">
          Runner-supervised agents. Tokens stay behind the API.
        </p>
        <div className="flex shrink-0 gap-1">
          <button
            type="button"
            onClick={() => void load(true)}
            disabled={refreshing}
            className="rounded-lg border border-zinc-200 p-1.5 text-zinc-600 disabled:opacity-50 dark:border-white/10 dark:text-zinc-300"
            aria-label="Refresh managed agents"
          >
            <RefreshCw
              className={cn("h-3.5 w-3.5", refreshing && "animate-spin")}
            />
          </button>
          <button
            type="button"
            onClick={onAddAgent}
            className="inline-flex items-center gap-1 rounded-lg bg-orange-500/15 px-2 py-1.5 text-[10px] font-semibold text-orange-900 dark:text-orange-100"
          >
            <Plus className="h-3.5 w-3.5" />
            Add
          </button>
        </div>
      </div>

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-2.5 py-2 text-[10px] text-rose-700 dark:text-rose-200"
        >
          {error}
        </p>
      )}

      {agents.length === 0 ? (
        <button
          type="button"
          onClick={onAddAgent}
          className="flex w-full flex-col items-center rounded-xl border border-dashed border-zinc-300 px-3 py-4 text-center dark:border-white/15"
        >
          <Bot className="h-5 w-5 text-zinc-500" />
          <span className="mt-1.5 text-xs font-semibold">No managed agents</span>
          <span className="mt-0.5 text-[10px] text-zinc-500">
            Add one without copying keys or commands.
          </span>
        </button>
      ) : (
        <ul className="space-y-2">
          {agents.map((agent) => {
            const state = stateOf(agent);
            const pending = busy[agent.id];
            const runner = runners.find((item) => item.id === agent.runner_id);
            const roomName =
              agent.room_name || roomNames.get(agent.room_id) || agent.room_id;
            const runnerName =
              agent.runner_name ||
              runnerNames.get(agent.runner_id) ||
              agent.runner_id;
            const listening = isListening(agent, participants);
            const expanded = openLogs === agent.id;
            const lastError = agent.last_error || agent.error;
            return (
              <li
                key={agent.id}
                className="rounded-xl border border-zinc-200 bg-white p-2.5 dark:border-white/10 dark:bg-zinc-950/60"
              >
                <div className="flex items-start gap-2">
                  <Bot className="mt-1 h-4 w-4 shrink-0 text-zinc-500" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => onSelectRoom?.(agent.room_id)}
                        className="truncate text-left text-xs font-semibold hover:underline"
                      >
                        {agent.name}
                      </button>
                      <span
                        className={cn(
                          "rounded-full border px-1.5 py-px text-[9px] font-semibold capitalize",
                          statusClass(state)
                        )}
                      >
                        {state}
                      </span>
                      {listening && (
                        <span className="inline-flex items-center gap-0.5 rounded-full border border-emerald-500/25 bg-emerald-500/10 px-1.5 py-px text-[9px] font-semibold text-emerald-700 dark:text-emerald-200">
                          <Wifi className="h-2.5 w-2.5" />
                          listening
                        </span>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => onSelectRoom?.(agent.room_id)}
                      className="mt-1 block w-full truncate text-left text-[9px] text-zinc-500 hover:underline"
                    >
                      {MANAGED_HARNESS_LABELS[
                        agent.harness as keyof typeof MANAGED_HARNESS_LABELS
                      ] || agent.harness}
                      {" · "}
                      room {roomName}
                      {agent.room_id === currentRoomId ? " · current" : ""}
                    </button>
                    <p className="truncate text-[9px] text-zinc-500">
                      runner {runnerName}
                      {runner && !runnerIsConnected(runner)
                        ? " · disconnected"
                        : ""}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void remove(agent)}
                    disabled={
                      Boolean(pending) ||
                      ["deleting", "deleted"].includes(state)
                    }
                    className="rounded-md p-1 text-zinc-500 hover:text-rose-600 disabled:opacity-40"
                    aria-label={`Delete ${agent.name}`}
                  >
                    {pending === "delete" ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>

                {lastError && (
                  <p className="mt-2 rounded-lg bg-rose-500/10 px-2 py-1.5 text-[9px] text-rose-700 dark:text-rose-200">
                    Last error: {lastError}
                  </p>
                )}

                <div className="mt-2 flex flex-wrap gap-1">
                  {["pending", "starting", "restarting", "running"].includes(
                    state
                  ) && (
                    <button
                      type="button"
                      onClick={() => void action(agent, "stop")}
                      disabled={Boolean(pending)}
                      className="inline-flex items-center gap-1 rounded-lg border border-zinc-200 px-2 py-1 text-[9px] font-semibold dark:border-white/10"
                    >
                      {pending === "stop" ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Square className="h-3 w-3" />
                      )}
                      Stop
                    </button>
                  )}
                  {!["deleting", "deleted"].includes(state) && (
                    <button
                      type="button"
                      onClick={() => void action(agent, "restart")}
                      disabled={Boolean(pending)}
                      className="inline-flex items-center gap-1 rounded-lg border border-zinc-200 px-2 py-1 text-[9px] font-semibold dark:border-white/10"
                    >
                      {pending === "restart" ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Play className="h-3 w-3" />
                      )}
                      Restart
                    </button>
                  )}
                  {!["deleting", "deleted"].includes(state) &&
                    rooms.some(
                      (room) =>
                        room.id !== agent.room_id &&
                        room.status !== "archived"
                    ) && (
                      <div className="inline-flex max-w-full items-center gap-1 rounded-lg border border-zinc-200 px-1 py-0.5 dark:border-white/10">
                        <label className="sr-only" htmlFor={`move-${agent.id}`}>
                          Move {agent.name} to room
                        </label>
                        <select
                          id={`move-${agent.id}`}
                          value={moveRoomId[agent.id] || ""}
                          onChange={(event) =>
                            setMoveRoomId((value) => ({
                              ...value,
                              [agent.id]: event.target.value,
                            }))
                          }
                          disabled={Boolean(pending)}
                          className="max-w-[7.5rem] truncate rounded-md bg-transparent py-0.5 pl-1 text-[9px] font-semibold outline-none disabled:opacity-40"
                        >
                          <option value="">Move to…</option>
                          {rooms
                            .filter(
                              (room) =>
                                room.id !== agent.room_id &&
                                room.status !== "archived"
                            )
                            .map((room) => (
                              <option key={room.id} value={room.id}>
                                {room.name}
                              </option>
                            ))}
                        </select>
                        <button
                          type="button"
                          onClick={() => void moveToRoom(agent)}
                          disabled={
                            Boolean(pending) ||
                            !(moveRoomId[agent.id] || "").trim()
                          }
                          className="rounded-md px-1.5 py-0.5 text-[9px] font-semibold text-orange-800 disabled:opacity-40 dark:text-orange-200"
                        >
                          {pending === "move" ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            "Go"
                          )}
                        </button>
                      </div>
                    )}
                  <button
                    type="button"
                    onClick={() => {
                      setOpenLogs(expanded ? null : agent.id);
                      if (!expanded) void fetchLogs(agent.id);
                    }}
                    className="inline-flex items-center gap-1 rounded-lg border border-zinc-200 px-2 py-1 text-[9px] font-semibold dark:border-white/10"
                    aria-expanded={expanded}
                  >
                    {expanded ? (
                      <ChevronDown className="h-3 w-3" />
                    ) : (
                      <ChevronRight className="h-3 w-3" />
                    )}
                    <FileText className="h-3 w-3" />
                    Logs
                  </button>
                </div>

                {expanded && (
                  <div className="mt-2 rounded-lg border border-zinc-700 bg-black p-2 text-white">
                    <div className="mb-1.5 flex items-center justify-between">
                      <span className="font-mono text-[9px] text-white">
                        Last {logs[agent.id]?.length || 0} lines · max 200
                      </span>
                      <button
                        type="button"
                        onClick={() => void fetchLogs(agent.id)}
                        disabled={logBusy === agent.id}
                        className="text-white disabled:opacity-40"
                        aria-label={`Refresh logs for ${agent.name}`}
                      >
                        <RefreshCw
                          className={cn(
                            "h-3 w-3",
                            logBusy === agent.id && "animate-spin"
                          )}
                        />
                      </button>
                    </div>
                    {logError[agent.id] ? (
                      <p className="text-[9px] text-rose-200">
                        {logError[agent.id]}
                      </p>
                    ) : (
                      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[9px] leading-relaxed text-white">
                        {logBusy === agent.id && !logs[agent.id]
                          ? "Loading logs…"
                          : (logs[agent.id] || ["No logs yet."]).join("\n")}
                      </pre>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
