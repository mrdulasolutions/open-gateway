import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AlertTriangle,
  Bot,
  Check,
  Circle,
  Copy,
  Loader2,
  RefreshCw,
  Server,
  Terminal,
  Wifi,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import {
  MANAGED_HARNESSES,
  MANAGED_HARNESS_LABELS,
  defaultManagedAgentName,
  runnerCapability,
  runnerIsConnected,
  runnerIsEmbedded,
  runnerPairCommand,
} from "@/lib/agentKit";
import { cn } from "@/lib/utils";
import type {
  AgentRunner,
  ManagedAgent,
  ManagedHarness,
  Participant,
  Room,
  RunnerPairing,
} from "@/lib/types";

type Props = {
  rooms: Room[];
  currentRoomId: string | null;
  participants: Participant[];
  onClose: () => void;
  onChanged: () => void;
  onSelectRoom?: (roomId: string) => void;
};

type Phase = "configure" | "starting" | "complete" | "failed";

function stateOf(agent: ManagedAgent | null): string {
  return (agent?.state || agent?.status || "pending").toLowerCase();
}

function agentParticipant(
  agent: ManagedAgent,
  participants: Participant[]
): Participant | undefined {
  const managed = participants.find(
    (p) => p.metadata?.managed_agent_id === agent.id
  );
  if (managed) return managed;
  if (agent.participant_id) {
    const exact = participants.find((p) => p.id === agent.participant_id);
    if (exact) return exact;
  }
  return participants.find(
    (p) =>
      p.name.trim().toLowerCase() === agent.name.trim().toLowerCase() &&
      String(p.harness).toLowerCase() === String(agent.harness).toLowerCase()
  );
}

function participantIsListening(participant?: Participant): boolean {
  return Boolean(
    participant &&
      (participant.presence === "listening" ||
        (participant.status === "online" && participant.last_poll_at))
  );
}

export function AddAgentWizard({
  rooms,
  currentRoomId,
  participants,
  onClose,
  onChanged,
  onSelectRoom,
}: Props) {
  const [phase, setPhase] = useState<Phase>("configure");
  const [runners, setRunners] = useState<AgentRunner[]>([]);
  const [runnerId, setRunnerId] = useState("");
  const [harness, setHarness] =
    useState<ManagedHarness>("claude-code");
  const [roomId, setRoomId] = useState(
    currentRoomId || rooms[0]?.id || ""
  );
  const [name, setName] = useState(
    defaultManagedAgentName("claude-code")
  );
  const [nameTouched, setNameTouched] = useState(false);
  const [runnerBusy, setRunnerBusy] = useState(true);
  const [pairBusy, setPairBusy] = useState(false);
  const [pairing, setPairing] = useState<RunnerPairing | null>(null);
  const [copied, setCopied] = useState(false);
  const [startBusy, setStartBusy] = useState(false);
  const [deletingRunnerId, setDeletingRunnerId] = useState<string | null>(null);
  const [agent, setAgent] = useState<ManagedAgent | null>(null);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState("");
  const [monitorNote, setMonitorNote] = useState("");
  const pairStarted = useRef(false);
  const onChangedRef = useRef(onChanged);
  const agentRef = useRef<ManagedAgent | null>(null);
  const autoSelectedRunner = useRef<string | null>(null);

  useEffect(() => {
    onChangedRef.current = onChanged;
  }, [onChanged]);

  useEffect(() => {
    agentRef.current = agent;
  }, [agent]);

  const beginPairing = useCallback(async () => {
    setPairBusy(true);
    setError("");
    pairStarted.current = true;
    try {
      const next = await api.pairRunner();
      setPairing(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPairBusy(false);
    }
  }, []);

  const refreshRunners = useCallback(
    async (autoPair: boolean) => {
      setRunnerBusy(true);
      setError("");
      try {
        const { runners: next } = await api.listRunners();
        const active = next.filter(
          (runner) =>
            !runner.revoked_at &&
            (runner.status || runner.state || "").toLowerCase() !== "revoked"
        );
        setRunners(active);
        const connected = active.filter(runnerIsConnected);
        setRunnerId((current) =>
          connected.some((runner) => runner.id === current)
            ? current
            : connected[0]?.id || ""
        );
        if (connected.length > 0) setPairing(null);
        if (
          active.length === 0 &&
          autoPair &&
          !pairStarted.current
        ) {
          await beginPairing();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setRunnerBusy(false);
      }
    },
    [beginPairing]
  );

  useEffect(() => {
    void refreshRunners(true);
  }, [refreshRunners]);

  useEffect(() => {
    if (!pairing || runners.some(runnerIsConnected)) return;
    const timer = window.setInterval(() => {
      void refreshRunners(false);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [pairing, refreshRunners, runners]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (!roomId && rooms.length) {
      setRoomId(currentRoomId || rooms[0].id);
    }
  }, [currentRoomId, roomId, rooms]);

  useEffect(() => {
    if (!nameTouched) setName(defaultManagedAgentName(harness));
  }, [harness, nameTouched]);

  const connectedRunners = useMemo(
    () => runners.filter(runnerIsConnected),
    [runners]
  );
  const selectedRunner = useMemo(
    () => connectedRunners.find((runner) => runner.id === runnerId) || null,
    [connectedRunners, runnerId]
  );
  const capabilities = useMemo(
    () =>
      MANAGED_HARNESSES.map((id) =>
        selectedRunner
          ? runnerCapability(selectedRunner, id)
          : {
              harness: id,
              reported: false,
              available: false,
              status: "unavailable",
              detail: "Choose a connected runner first.",
              setupInstructions: [],
            }
      ),
    [selectedRunner]
  );
  const selectedCapability = capabilities.find(
    (capability) => capability.harness === harness
  );

  useEffect(() => {
    if (!selectedRunner) {
      autoSelectedRunner.current = null;
      return;
    }
    if (autoSelectedRunner.current === selectedRunner.id) return;
    autoSelectedRunner.current = selectedRunner.id;
    const firstReady = capabilities.find((capability) => capability.available);
    if (firstReady) setHarness(firstReady.harness);
  }, [capabilities, selectedRunner]);

  const pairCommand = useMemo(
    () =>
      pairing
        ? runnerPairCommand(
            pairing,
            api.base || window.location.origin
          )
        : "",
    [pairing]
  );

  const removeRunner = async (runner: AgentRunner) => {
    if (
      !window.confirm(
        `Revoke runner “${runner.name || runner.hostname || runner.id}”?`
      )
    ) {
      return;
    }
    setDeletingRunnerId(runner.id);
    setError("");
    try {
      await api.deleteRunner(runner.id);
      if (runnerId === runner.id) setRunnerId("");
      await refreshRunners(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingRunnerId(null);
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (
      !selectedRunner ||
      !selectedCapability?.available ||
      !roomId ||
      !name.trim()
    ) {
      return;
    }
    setStartBusy(true);
    setError("");
    setMonitorNote("");
    try {
      const created = await api.createManagedAgent({
        runner_id: selectedRunner.id,
        harness,
        room_id: roomId,
        name: name.trim(),
      });
      setAgent(created);
      setListening(false);
      setPhase("starting");
      onChangedRef.current();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStartBusy(false);
    }
  };

  const monitoredAgentId = agent?.id;

  useEffect(() => {
    if (phase !== "starting" || !monitoredAgentId) return;
    let cancelled = false;
    let timer: number | undefined;

    const check = async () => {
      try {
        const [managed, snapshot] = await Promise.all([
          api.listManagedAgents(),
          api.snapshot(roomId),
        ]);
        if (cancelled) return;
        const current =
          managed.agents.find(
            (candidate) => candidate.id === monitoredAgentId
          ) || agentRef.current;
        if (!current) return;
        setAgent(current);
        const roomParticipants =
          roomId === currentRoomId
            ? [...snapshot.participants, ...participants]
            : snapshot.participants;
        const isListening = participantIsListening(
          agentParticipant(current, roomParticipants)
        );
        setListening(isListening);
        setMonitorNote("");

        const state = stateOf(current);
        if (
          ["failed", "error", "stopped", "deleting", "deleted"].includes(
            state
          )
        ) {
          setError(
            current.last_error ||
              current.error ||
              "The runner could not start this agent."
          );
          setPhase("failed");
          onChangedRef.current();
          return;
        }
        if (state === "running" && isListening) {
          setPhase("complete");
          onChangedRef.current();
          if (roomId) onSelectRoom?.(roomId);
          return;
        }
      } catch (err) {
        if (!cancelled) {
          setMonitorNote(
            `Still checking: ${
              err instanceof Error ? err.message : String(err)
            }`
          );
        }
      }
      if (!cancelled) timer = window.setTimeout(check, 1500);
    };

    void check();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [
    currentRoomId,
    monitoredAgentId,
    onSelectRoom,
    participants,
    phase,
    roomId,
  ]);

  const selectedRoom = rooms.find((room) => room.id === roomId);
  const state = stateOf(agent);
  const running = state === "running";
  const failed = phase === "failed";

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-black/65 p-3 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-agent-title"
        className="max-h-[94dvh] w-[min(720px,96vw)] overflow-y-auto rounded-2xl border border-zinc-200 bg-white shadow-2xl dark:border-white/10 dark:bg-zinc-900"
      >
        <div className="sticky top-0 z-10 flex items-start justify-between gap-3 border-b border-zinc-200 bg-white/95 px-5 py-4 backdrop-blur dark:border-white/10 dark:bg-zinc-900/95">
          <div className="flex min-w-0 items-start gap-3">
            <span className="rounded-xl bg-orange-500/12 p-2 text-orange-600 dark:text-orange-300">
              <Bot className="h-5 w-5" />
            </span>
            <div>
              <h2
                id="add-agent-title"
                className="text-lg font-semibold tracking-tight"
              >
                Add agent
              </h2>
              <p className="mt-0.5 text-xs text-zinc-500">
                Launch a managed agent on a connected runner. No API keys or
                vendor credentials are entered here.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-white/10 dark:hover:text-zinc-200"
            aria-label="Close Add Agent"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {phase === "configure" ? (
          <form className="space-y-5 p-5" onSubmit={(event) => void submit(event)}>
            <section aria-labelledby="runner-heading" className="space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <h3
                    id="runner-heading"
                    className="flex items-center gap-1.5 text-sm font-semibold"
                  >
                    <Server className="h-4 w-4 text-orange-500" />
                    1. Runner
                  </h3>
                  <p className="mt-0.5 text-xs text-zinc-500">
                    The runner launches the agent on a machine you control.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void refreshRunners(false)}
                  disabled={runnerBusy}
                  className="inline-flex items-center gap-1 rounded-lg border border-zinc-200 px-2.5 py-1.5 text-xs font-semibold text-zinc-600 hover:bg-zinc-50 disabled:opacity-50 dark:border-white/10 dark:text-zinc-300 dark:hover:bg-white/5"
                >
                  <RefreshCw
                    className={cn("h-3.5 w-3.5", runnerBusy && "animate-spin")}
                  />
                  Recheck
                </button>
              </div>

              {runnerBusy && runners.length === 0 && !pairing ? (
                <div className="flex items-center gap-2 rounded-xl border border-zinc-200 p-3 text-sm text-zinc-500 dark:border-white/10">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Looking for connected runners…
                </div>
              ) : connectedRunners.length > 0 ? (
                <div className="space-y-1.5">
                  <label
                    htmlFor="agent-runner"
                    className="block text-xs font-medium text-zinc-600 dark:text-zinc-300"
                  >
                    Connected runner
                    <select
                      id="agent-runner"
                      value={runnerId}
                      onChange={(event) => setRunnerId(event.target.value)}
                      className="field-input mt-1"
                    >
                      {connectedRunners.map((runner) => (
                        <option key={runner.id} value={runner.id}>
                          {runner.name || runner.hostname || runner.id}
                          {runner.platform ? ` · ${runner.platform}` : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  {selectedRunner && (
                    <div className="flex items-center justify-between gap-2 text-[10px] text-zinc-500">
                      <span className="truncate">
                        {selectedRunner.hostname || "Connected"}
                        {selectedRunner.version
                          ? ` · v${selectedRunner.version}`
                          : ""}
                      </span>
                      {!runnerIsEmbedded(selectedRunner) && (
                        <button
                          type="button"
                          onClick={() => void removeRunner(selectedRunner)}
                          disabled={deletingRunnerId === selectedRunner.id}
                          className="shrink-0 font-semibold text-rose-600 hover:underline disabled:opacity-50 dark:text-rose-300"
                        >
                          {deletingRunnerId === selectedRunner.id
                            ? "Removing…"
                            : "Remove runner"}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              ) : !pairing ? (
                <div className="rounded-xl border border-amber-500/35 bg-amber-50 p-3 dark:border-amber-500/25 dark:bg-amber-500/10">
                  <p className="text-xs font-semibold text-amber-950 dark:text-amber-100">
                    {runners.length
                      ? "No runner is connected"
                      : "A runner is required"}
                  </p>
                  <p className="mt-1 text-[11px] leading-relaxed text-amber-900/80 dark:text-amber-100/75">
                    Pair a machine once, then it can launch and supervise agents
                    without exposing agent tokens.
                  </p>
                  <button
                    type="button"
                    onClick={() => void beginPairing()}
                    disabled={pairBusy}
                    className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-amber-900 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50 dark:bg-amber-100 dark:text-amber-950"
                  >
                    {pairBusy ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Terminal className="h-3.5 w-3.5" />
                    )}
                    Pair runner
                  </button>
                  {runners.filter((runner) => !runnerIsConnected(runner))
                    .length > 0 && (
                    <div className="mt-2 space-y-1 border-t border-amber-700/15 pt-2">
                      {runners
                        .filter((runner) => !runnerIsConnected(runner))
                        .map((runner) => (
                          <div
                            key={runner.id}
                            className="flex items-center justify-between gap-2 text-[10px]"
                          >
                            <span className="truncate">
                              {runner.name || runner.hostname || runner.id} ·{" "}
                              {runner.status || runner.state || "offline"}
                            </span>
                            <button
                              type="button"
                              onClick={() => void removeRunner(runner)}
                              disabled={deletingRunnerId === runner.id}
                              className="shrink-0 font-semibold underline disabled:opacity-50"
                            >
                              {deletingRunnerId === runner.id
                                ? "Removing…"
                                : "Remove"}
                            </button>
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              ) : null}

              {pairing && (
                <div className="rounded-xl border border-orange-500/35 bg-orange-50 p-3 dark:border-orange-500/25 dark:bg-orange-500/10">
                  <div className="flex items-center gap-2 text-xs font-semibold text-orange-950 dark:text-orange-100">
                    <Terminal className="h-4 w-4" />
                    Run this once on the runner machine
                  </div>
                  <p className="mt-1 text-[11px] text-orange-900/75 dark:text-orange-100/75">
                    It starts a background service, so you can close the
                    terminal after the runner connects.
                  </p>
                  <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-zinc-950 p-2.5 font-mono text-[10px] leading-relaxed text-zinc-100">
                    {pairCommand}
                  </pre>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        void navigator.clipboard.writeText(pairCommand);
                        setCopied(true);
                        window.setTimeout(() => setCopied(false), 1500);
                      }}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-orange-500/30 bg-white px-2.5 py-1.5 text-xs font-semibold text-orange-900 dark:bg-zinc-900 dark:text-orange-100"
                    >
                      <Copy className="h-3.5 w-3.5" />
                      {copied ? "Copied" : "Copy command"}
                    </button>
                    <span className="inline-flex items-center gap-1 text-[11px] text-orange-900/70 dark:text-orange-100/70">
                      <Loader2 className="h-3 w-3 animate-spin" />
                      Waiting for runner…
                    </span>
                  </div>
                </div>
              )}
            </section>

            <section aria-labelledby="harness-heading" className="space-y-2.5">
              <div>
                <h3
                  id="harness-heading"
                  className="flex items-center gap-1.5 text-sm font-semibold"
                >
                  <Bot className="h-4 w-4 text-orange-500" />
                  2. Harness
                </h3>
                <p className="mt-0.5 text-xs text-zinc-500">
                  Availability comes directly from the selected runner.
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-3">
                {capabilities.map((capability) => {
                  const active = harness === capability.harness;
                  return (
                    <button
                      key={capability.harness}
                      type="button"
                      onClick={() => setHarness(capability.harness)}
                      aria-pressed={active}
                      className={cn(
                        "rounded-xl border p-3 text-left transition",
                        active
                          ? "border-orange-500/45 bg-orange-500/10"
                          : "border-zinc-200 hover:border-zinc-300 dark:border-white/10 dark:hover:border-white/20",
                        !capability.available && "opacity-75"
                      )}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="text-sm font-semibold">
                          {MANAGED_HARNESS_LABELS[capability.harness]}
                        </span>
                        <span
                          className={cn(
                            "h-2 w-2 rounded-full",
                            capability.available
                              ? "bg-emerald-500"
                              : "bg-amber-400"
                          )}
                        />
                      </span>
                      <span className="mt-1 block text-[10px] text-zinc-500">
                        {capability.available
                          ? capability.version
                            ? `Ready · ${capability.version}`
                            : "Ready"
                          : capability.reported
                            ? "Setup required"
                            : "Not reported"}
                      </span>
                    </button>
                  );
                })}
              </div>

              {selectedRunner &&
                selectedCapability &&
                !selectedCapability.available && (
                  <div className="rounded-xl border border-amber-500/35 bg-amber-50 p-3 text-amber-950 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-100">
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-semibold">
                          {MANAGED_HARNESS_LABELS[harness]} needs setup on{" "}
                          {selectedRunner.name}
                        </p>
                        <p className="mt-1 text-[11px] leading-relaxed opacity-80">
                          {selectedCapability.detail}
                        </p>
                        {selectedCapability.setupInstructions.length > 0 && (
                          <ol className="mt-2 list-decimal space-y-1 pl-4 font-mono text-[10px] leading-relaxed">
                            {selectedCapability.setupInstructions.map(
                              (instruction, index) => (
                                <li
                                  key={`${instruction}-${index}`}
                                  className="whitespace-pre-wrap break-words"
                                >
                                  {instruction}
                                </li>
                              )
                            )}
                          </ol>
                        )}
                        <button
                          type="button"
                          onClick={() => void refreshRunners(false)}
                          disabled={runnerBusy}
                          className="mt-2 inline-flex items-center gap-1 rounded-lg border border-amber-700/25 bg-white/70 px-2.5 py-1.5 text-xs font-semibold dark:bg-zinc-900/70"
                        >
                          <RefreshCw
                            className={cn(
                              "h-3.5 w-3.5",
                              runnerBusy && "animate-spin"
                            )}
                          />
                          Recheck
                        </button>
                      </div>
                    </div>
                  </div>
                )}
            </section>

            <div className="grid gap-4 sm:grid-cols-2">
              <section aria-labelledby="room-heading">
                <h3 id="room-heading" className="text-sm font-semibold">
                  3. Room
                </h3>
                <label
                  htmlFor="agent-room"
                  className="mt-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-300"
                >
                  Agent room
                  <select
                    id="agent-room"
                    value={roomId}
                    onChange={(event) => setRoomId(event.target.value)}
                    className="field-input mt-1"
                    required
                  >
                    {rooms.length === 0 && (
                      <option value="">Create a room first</option>
                    )}
                    {rooms.map((room) => (
                      <option key={room.id} value={room.id}>
                        {room.name}
                        {room.id === currentRoomId ? " · current" : ""}
                      </option>
                    ))}
                  </select>
                </label>
              </section>

              <section aria-labelledby="name-heading">
                <h3 id="name-heading" className="text-sm font-semibold">
                  4. Name
                </h3>
                <label
                  htmlFor="agent-name"
                  className="mt-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-300"
                >
                  Confirm agent name
                  <input
                    id="agent-name"
                    value={name}
                    onChange={(event) => {
                      setNameTouched(true);
                      setName(event.target.value);
                    }}
                    className="field-input mt-1"
                    maxLength={64}
                    required
                    autoComplete="off"
                  />
                </label>
              </section>
            </div>

            {error && (
              <p
                role="alert"
                className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-800 dark:text-rose-200"
              >
                {error}
              </p>
            )}

            <div className="flex flex-col-reverse gap-2 border-t border-zinc-200 pt-4 dark:border-white/10 sm:flex-row sm:items-center sm:justify-end">
              <button type="button" onClick={onClose} className="btn-ghost">
                Cancel
              </button>
              <button
                type="submit"
                disabled={
                  startBusy ||
                  !selectedRunner ||
                  !selectedCapability?.available ||
                  !roomId ||
                  !name.trim()
                }
                className="btn-primary inline-flex items-center justify-center gap-1.5"
              >
                {startBusy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Bot className="h-4 w-4" />
                )}
                {startBusy ? "Starting…" : "Start agent"}
              </button>
            </div>
          </form>
        ) : (
          <div className="space-y-5 p-5">
            <div
              className={cn(
                "rounded-xl border p-4",
                phase === "complete"
                  ? "border-emerald-500/30 bg-emerald-500/10"
                  : failed
                    ? "border-rose-500/30 bg-rose-500/10"
                    : "border-orange-500/30 bg-orange-500/10"
              )}
            >
              <div className="flex items-start gap-3">
                {phase === "complete" ? (
                  <span className="rounded-full bg-emerald-500 p-1 text-white">
                    <Check className="h-4 w-4" />
                  </span>
                ) : failed ? (
                  <AlertTriangle className="h-6 w-6 text-rose-500" />
                ) : (
                  <Loader2 className="h-6 w-6 animate-spin text-orange-500" />
                )}
                <div>
                  <h3 className="text-sm font-semibold">
                    {phase === "complete"
                      ? `${agent?.name || name} is listening`
                      : failed
                        ? "Agent did not start"
                        : `Starting ${agent?.name || name}`}
                  </h3>
                  <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-300">
                    {MANAGED_HARNESS_LABELS[harness]} ·{" "}
                    {selectedRoom?.name || roomId} ·{" "}
                    {selectedRunner?.name || agent?.runner_name || "runner"}
                  </p>
                </div>
              </div>
            </div>

            <ol className="space-y-3" aria-label="Agent startup progress">
              {[
                {
                  label: "Launch request accepted",
                  done: Boolean(agent),
                  active: !agent,
                },
                {
                  label: "Runner is starting the harness",
                  done: ["starting", "running"].includes(state),
                  active: state === "pending",
                },
                {
                  label: "Managed process is running",
                  done: running,
                  active: !running && !failed,
                },
                {
                  label: "Room participant is listening",
                  done: listening,
                  active: running && !listening && !failed,
                },
              ].map((step) => (
                <li
                  key={step.label}
                  className="flex items-center gap-3 rounded-xl border border-zinc-200 px-3 py-2.5 dark:border-white/10"
                >
                  {step.done ? (
                    <span className="rounded-full bg-emerald-500 p-0.5 text-white">
                      <Check className="h-3.5 w-3.5" />
                    </span>
                  ) : step.active ? (
                    <Loader2 className="h-4 w-4 animate-spin text-orange-500" />
                  ) : (
                    <Circle className="h-4 w-4 text-zinc-300 dark:text-zinc-700" />
                  )}
                  <span
                    className={cn(
                      "text-sm",
                      step.done
                        ? "font-medium text-zinc-800 dark:text-zinc-100"
                        : "text-zinc-500"
                    )}
                  >
                    {step.label}
                  </span>
                  {step.label.includes("listening") && step.done && (
                    <Wifi className="ml-auto h-4 w-4 text-emerald-500" />
                  )}
                </li>
              ))}
            </ol>

            {monitorNote && !failed && (
              <p className="text-xs text-zinc-500" aria-live="polite">
                {monitorNote}
              </p>
            )}
            {error && (
              <p
                role="alert"
                className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-800 dark:text-rose-200"
              >
                {error}
              </p>
            )}

            <div className="flex flex-col-reverse gap-2 border-t border-zinc-200 pt-4 dark:border-white/10 sm:flex-row sm:justify-end">
              {failed && agent && (
                <button
                  type="button"
                  className="btn-ghost inline-flex items-center justify-center gap-1.5"
                  onClick={() => {
                    void (async () => {
                      setError("");
                      setMonitorNote("");
                      try {
                        await api.managedAgentAction(agent.id, "restart");
                        setAgent({
                          ...agent,
                          state: "starting",
                          status: "starting",
                          last_error: null,
                          error: null,
                        });
                        setListening(false);
                        setPhase("starting");
                        onChangedRef.current();
                      } catch (err) {
                        setError(
                          err instanceof Error ? err.message : String(err)
                        );
                      }
                    })();
                  }}
                >
                  <RefreshCw className="h-4 w-4" />
                  Restart
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  if (roomId) onSelectRoom?.(roomId);
                  onClose();
                }}
                className="btn-primary"
              >
                {phase === "complete" ? "Open room" : "Close"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
