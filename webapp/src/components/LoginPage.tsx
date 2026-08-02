/**
 * Login / first-admin register — layout adapted from 21st.dev
 * "Login with Email and Password" (ephraimduncan/login-03).
 */
import { useEffect, useState } from "react";
import { api, setAuthToken } from "@/lib/api";

type Props = {
  onAuthenticated: (token: string, user?: { email: string; role: string; display_name: string }) => void;
};

export function LoginPage({ onAuthenticated }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [invite, setInvite] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState<{
    has_users: boolean;
    registration_open: boolean;
  } | null>(null);

  useEffect(() => {
    void api
      .authStatus()
      .then((s) => {
        setStatus(s);
        if (!s.has_users) setMode("register");
      })
      .catch(() => setStatus({ has_users: false, registration_open: true }));
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res =
        mode === "register"
          ? await api.authRegister({
              email,
              password,
              display_name: displayName,
              org_name: orgName,
              invite_code: invite,
            })
          : await api.authLogin({ email, password });
      if (!res.token) throw new Error("No session token returned");
      setAuthToken(res.token);
      onAuthenticated(res.token, res.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center bg-gradient-to-b from-zinc-50 to-zinc-100 px-4 dark:from-zinc-950 dark:to-zinc-900">
      <div className="mb-8 flex flex-col items-center gap-3">
        <img
          src={`${import.meta.env.BASE_URL}og-logo.png`}
          alt="OpenGateway"
          className="og-logo h-14 w-14 rounded-2xl shadow-lg shadow-orange-500/20"
        />
        <div className="text-center">
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            OpenGateway
          </h1>
          <p className="mt-1 text-sm text-zinc-500">
            Multi-agent collaboration hub
          </p>
        </div>
      </div>

      <div className="w-full max-w-sm rounded-2xl border border-zinc-200 bg-white p-6 shadow-xl dark:border-white/10 dark:bg-zinc-900">
        <h2 className="text-center text-lg font-semibold text-zinc-900 dark:text-zinc-50">
          {!status?.has_users
            ? "Create admin account"
            : mode === "login"
              ? "Welcome back"
              : "Join organization"}
        </h2>
        <p className="mt-1 text-pretty text-center text-sm text-zinc-500">
          {!status?.has_users
            ? "First user becomes the organization admin."
            : mode === "login"
              ? "Sign in with email and password."
              : "Register with an invite code, or open registration."}
        </p>

        <form className="mt-6 space-y-4" onSubmit={(e) => void submit(e)}>
          {mode === "register" && !status?.has_users && (
            <div>
              <label className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
                Organization name
              </label>
              <input
                className="field-input mt-1.5"
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                placeholder="My team"
                autoComplete="organization"
              />
            </div>
          )}
          {mode === "register" && (
            <div>
              <label className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
                Display name
              </label>
              <input
                className="field-input mt-1.5"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Your name"
                autoComplete="name"
              />
            </div>
          )}
          <div>
            <label
              className="text-xs font-medium text-zinc-600 dark:text-zinc-300"
              htmlFor="og-email"
            >
              Email
            </label>
            <input
              id="og-email"
              className="field-input mt-1.5"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
            />
          </div>
          <div>
            <label
              className="text-xs font-medium text-zinc-600 dark:text-zinc-300"
              htmlFor="og-password"
            >
              Password
            </label>
            <input
              id="og-password"
              className="field-input mt-1.5"
              type="password"
              autoComplete={
                mode === "register" ? "new-password" : "current-password"
              }
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••••••"
            />
          </div>
          {mode === "register" && status?.has_users && (
            <div>
              <label className="text-xs font-medium text-zinc-600 dark:text-zinc-300">
                Invite code (optional if open registration)
              </label>
              <input
                className="field-input mt-1.5 font-mono uppercase"
                value={invite}
                onChange={(e) => setInvite(e.target.value)}
                placeholder="ABCD1234"
                autoComplete="off"
              />
            </div>
          )}

          {error && (
            <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-800 dark:text-rose-200">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="mt-2 w-full rounded-xl bg-gradient-to-b from-orange-400 to-orange-600 py-2.5 text-sm font-semibold text-orange-950 shadow-sm shadow-orange-500/25 disabled:opacity-50"
          >
            {busy
              ? "Please wait…"
              : mode === "register"
                ? status?.has_users
                  ? "Create account"
                  : "Create admin & org"
                : "Sign in"}
          </button>
        </form>

        {status?.has_users && (
          <p className="mt-5 text-center text-sm text-zinc-500">
            {mode === "login" ? (
              <>
                Need an account?{" "}
                <button
                  type="button"
                  className="font-semibold text-orange-600 hover:underline dark:text-orange-400"
                  onClick={() => {
                    setMode("register");
                    setError("");
                  }}
                >
                  Register
                </button>
              </>
            ) : (
              <>
                Already have an account?{" "}
                <button
                  type="button"
                  className="font-semibold text-orange-600 hover:underline dark:text-orange-400"
                  onClick={() => {
                    setMode("login");
                    setError("");
                  }}
                >
                  Sign in
                </button>
              </>
            )}
          </p>
        )}
      </div>

      <p className="mt-6 max-w-sm text-center text-[11px] leading-relaxed text-zinc-400">
        Agents still use API keys from{" "}
        <strong className="text-zinc-500">Agent tokens</strong> after you sign
        in. Session login is for humans on Live Ops.
      </p>
    </div>
  );
}
