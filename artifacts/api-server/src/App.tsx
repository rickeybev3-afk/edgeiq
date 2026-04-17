import { useEffect, useRef, useState } from "react";
import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AlertCircle } from "lucide-react";
import NotFound from "@/pages/not-found";
import Settings from "@/pages/settings";

const queryClient = new QueryClient();

const MISMATCH_DISMISSED_KEY = "alpaca_mismatch_banner_dismissed";

interface HealthState {
  checked: boolean;
  ok: boolean;
  errors: string[];
  alpaca_mode_mismatch?: boolean;
  alpaca_mismatch_message?: string;
  db_checked_at?: string;
}

function formatCheckedAgo(isoTimestamp: string): string {
  const checkedAt = new Date(isoTimestamp);
  if (isNaN(checkedAt.getTime())) return "DB check time unavailable";
  const diffMs = Date.now() - checkedAt.getTime();
  const diffSec = Math.round(diffMs / 1000);
  if (diffSec < 60) return `DB checked ${diffSec} s ago`;
  const diffMin = Math.round(diffSec / 60);
  return `DB checked ${diffMin} min ago`;
}

function AlpacaMismatchBanner({
  message,
  dismissed,
  onDismiss,
}: {
  message: string;
  dismissed: boolean;
  onDismiss: () => void;
}) {
  if (dismissed) return null;

  return (
    <div
      role="alert"
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        zIndex: 9998,
        background: "#431407",
        borderBottom: "1px solid #c2410c",
        padding: "10px 20px",
        display: "flex",
        alignItems: "center",
        gap: "10px",
      }}
    >
      <AlertCircle style={{ color: "#fb923c", flexShrink: 0, width: "18px", height: "18px" }} />
      <span style={{ color: "#fed7aa", fontSize: "13px", lineHeight: "1.5", flex: 1 }}>
        <strong style={{ color: "#fdba74" }}>⚠️ Alpaca credential mismatch:</strong>{" "}
        {message}{" "}
        <a
          href={`${import.meta.env.BASE_URL}settings#trading-mode`}
          style={{
            color: "#fdba74",
            fontWeight: 700,
            textDecoration: "underline",
            whiteSpace: "nowrap",
          }}
        >
          Go to Trading Mode settings →
        </a>
      </span>
      <button
        onClick={onDismiss}
        aria-label="Dismiss banner"
        style={{
          background: "none",
          border: "none",
          cursor: "pointer",
          color: "#fed7aa",
          fontSize: "18px",
          lineHeight: 1,
          padding: "0 4px",
          flexShrink: 0,
          opacity: 0.8,
        }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "1"; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.opacity = "0.8"; }}
      >
        ×
      </button>
    </div>
  );
}

function ErrorBanner({ errors, dbCheckedAt }: { errors: string[]; dbCheckedAt?: string }) {
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!dbCheckedAt) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [dbCheckedAt]);

  return (
    <div
      role="alert"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        background: "#0e1117",
        padding: "24px",
      }}
    >
      <div
        style={{
          maxWidth: "560px",
          width: "100%",
          background: "#1a0a0a",
          border: "1px solid #7f1d1d",
          borderRadius: "8px",
          padding: "24px 28px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "16px" }}>
          <AlertCircle style={{ color: "#ef4444", flexShrink: 0, width: "24px", height: "24px" }} />
          <h1 style={{ color: "#fca5a5", fontSize: "18px", fontWeight: 700, margin: 0 }}>
            Database connection is not configured
          </h1>
        </div>
        <p style={{ color: "#f87171", fontSize: "14px", marginBottom: "16px", lineHeight: "1.6" }}>
          The server detected one or more configuration problems at startup. The
          dashboard cannot display data until these are resolved. Contact your
          administrator.
        </p>
        <ul style={{ margin: 0, padding: "0 0 0 20px" }}>
          {errors.map((err, i) => (
            <li
              key={i}
              style={{ color: "#fca5a5", fontSize: "13px", fontFamily: "monospace", marginBottom: "6px", lineHeight: "1.5" }}
            >
              {err}
            </li>
          ))}
        </ul>
        {dbCheckedAt && (
          <p style={{ color: "#6b7280", fontSize: "12px", margin: "16px 0 0", fontFamily: "monospace" }}>
            {formatCheckedAgo(dbCheckedAt)}
          </p>
        )}
      </div>
    </div>
  );
}

interface DbEvent {
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
}

function formatUtc(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZoneName: "short",
    });
  } catch {
    return iso;
  }
}

function DbEventsPanel() {
  const [events, setEvents] = useState<DbEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch("/api/db-events");
        const data = await res.json();
        if (!cancelled) {
          setEvents(data.events ?? []);
          setLoading(false);
        }
      } catch {
        if (!cancelled) {
          setError("Could not load DB event history.");
          setLoading(false);
        }
      }
    };
    load();
    const interval = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <div
      style={{
        background: "#1e2435",
        border: "1px solid #2d3748",
        borderRadius: "10px",
        padding: "24px",
        maxWidth: "640px",
        width: "100%",
      }}
    >
      <h2
        style={{
          fontSize: "15px",
          fontWeight: 700,
          color: "#cbd5e1",
          marginBottom: "6px",
          marginTop: 0,
        }}
      >
        DB Connectivity
      </h2>
      <p
        style={{
          fontSize: "13px",
          color: "#94a3b8",
          marginBottom: "16px",
          lineHeight: "1.6",
          marginTop: 0,
        }}
      >
        Recent database outage events (up to last 50), newest first. Refreshes every 30 s.
      </p>

      {loading && (
        <p style={{ fontSize: "13px", color: "#64748b" }}>Loading…</p>
      )}
      {error && (
        <p style={{ fontSize: "13px", color: "#f87171" }}>⚠ {error}</p>
      )}
      {!loading && !error && events.length === 0 && (
        <div
          style={{
            padding: "16px",
            background: "rgba(74,222,128,0.06)",
            border: "1px solid #14532d",
            borderRadius: "8px",
            fontSize: "13px",
            color: "#86efac",
          }}
        >
          No outages recorded since server start.
        </div>
      )}
      {!loading && !error && events.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {events.map((ev, i) => {
            const ongoing = ev.ended_at === null;
            return (
              <div
                key={i}
                style={{
                  padding: "12px 16px",
                  background: ongoing
                    ? "rgba(239,68,68,0.08)"
                    : "rgba(255,255,255,0.03)",
                  border: ongoing ? "1px solid #7f1d1d" : "1px solid #2d3748",
                  borderRadius: "8px",
                  fontSize: "13px",
                  color: "#e2e8f0",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: "12px",
                    flexWrap: "wrap",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span
                      style={{
                        width: "8px",
                        height: "8px",
                        borderRadius: "50%",
                        background: ongoing ? "#ef4444" : "#64748b",
                        flexShrink: 0,
                        ...(ongoing ? { boxShadow: "0 0 6px #ef4444" } : {}),
                      }}
                    />
                    <span
                      style={{
                        fontWeight: 600,
                        color: ongoing ? "#fca5a5" : "#94a3b8",
                      }}
                    >
                      {ongoing ? "Ongoing outage" : "Outage"}
                    </span>
                  </div>
                  <span
                    style={{
                      fontSize: "12px",
                      fontWeight: 700,
                      color: ongoing ? "#f87171" : "#94a3b8",
                      background: ongoing
                        ? "rgba(239,68,68,0.15)"
                        : "rgba(255,255,255,0.05)",
                      border: ongoing ? "1px solid #7f1d1d" : "1px solid #334155",
                      borderRadius: "4px",
                      padding: "2px 8px",
                    }}
                  >
                    {ev.duration_seconds !== null
                      ? formatDuration(ev.duration_seconds)
                      : "—"}
                  </span>
                </div>
                <div
                  style={{
                    marginTop: "8px",
                    display: "grid",
                    gridTemplateColumns: "auto 1fr",
                    gap: "4px 12px",
                    fontSize: "12px",
                    color: "#64748b",
                  }}
                >
                  <span style={{ color: "#475569" }}>Started</span>
                  <span style={{ fontFamily: "monospace", color: "#94a3b8" }}>
                    {formatUtc(ev.started_at)}
                  </span>
                  <span style={{ color: "#475569" }}>Ended</span>
                  <span style={{ fontFamily: "monospace", color: ongoing ? "#f87171" : "#94a3b8" }}>
                    {ev.ended_at ? formatUtc(ev.ended_at) : "still down"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: "8px",
        height: "8px",
        borderRadius: "50%",
        background: ok ? "#22c55e" : "#ef4444",
        flexShrink: 0,
      }}
    />
  );
}

function Home({ health }: { health: HealthState }) {
  const [credAlertsEnabled, setCredAlertsEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/credential-alerts")
      .then((r) => {
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setCredAlertsEnabled(data.enabled !== false);
      })
      .catch(() => {
        if (!cancelled) setCredAlertsEnabled(null);
      });
    return () => { cancelled = true; };
  }, []);

  const settingsBase = import.meta.env.BASE_URL.replace(/\/$/, "");

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#0e1117",
        color: "#fafafa",
        fontFamily: "system-ui, -apple-system, sans-serif",
        padding: "40px 24px",
      }}
    >
      <div
        style={{
          maxWidth: "640px",
          margin: "0 auto",
          display: "flex",
          flexDirection: "column",
          gap: "24px",
        }}
      >
        <div>
          <h1
            style={{
              fontSize: "22px",
              fontWeight: 700,
              color: "#f1f5f9",
              marginBottom: "4px",
              marginTop: 0,
            }}
          >
            EdgeIQ Dashboard
          </h1>
          <p style={{ fontSize: "14px", color: "#64748b", marginTop: 0 }}>
            Connected and ready.
          </p>
        </div>

        <div
          style={{
            background: "#1e2435",
            border: "1px solid #2d3748",
            borderRadius: "10px",
            padding: "20px 24px",
          }}
        >
          <h2 style={{ fontSize: "13px", fontWeight: 700, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "16px", marginTop: 0 }}>
            System Health
          </h2>

          <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <StatusDot ok={health.ok} />
              <span style={{ fontSize: "14px", color: "#cbd5e1", flex: 1 }}>Database</span>
              <span style={{ fontSize: "13px", color: health.ok ? "#86efac" : "#fca5a5", fontWeight: 600 }}>
                {health.ok ? "Connected" : "Error"}
              </span>
            </div>

            {health.db_checked_at && (
              <div style={{ paddingLeft: "18px" }}>
                <span style={{ fontSize: "11px", color: "#475569", fontFamily: "monospace" }}>
                  {formatCheckedAgo(health.db_checked_at)}
                </span>
              </div>
            )}

            <div style={{ borderTop: "1px solid #2d3748", paddingTop: "12px", display: "flex", alignItems: "center", gap: "10px" }}>
              <StatusDot ok={credAlertsEnabled === true} />
              <span style={{ fontSize: "14px", color: "#cbd5e1", flex: 1 }}>Credential Alerts</span>
              <a
                href={`${settingsBase}/settings#credential-alerts`}
                style={{
                  fontSize: "13px",
                  color: credAlertsEnabled === true ? "#86efac" : credAlertsEnabled === false ? "#94a3b8" : "#475569",
                  fontWeight: 600,
                  textDecoration: "none",
                }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLAnchorElement).style.textDecoration = "underline"; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLAnchorElement).style.textDecoration = "none"; }}
                title="Go to credential-alerts settings"
              >
                {credAlertsEnabled === null ? "—" : credAlertsEnabled ? "On" : "Off"}
              </a>
            </div>
          </div>
        </div>

        <DbEventsPanel />
      </div>
    </div>
  );
}

function Router({ health }: { health: HealthState }) {
  return (
    <Switch>
      <Route path="/">{() => <Home health={health} />}</Route>
      <Route path="/settings" component={Settings} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  const [health, setHealth] = useState<HealthState>({ checked: false, ok: true, errors: [] });
  const [mismatchDismissed, setMismatchDismissed] = useState(
    () => localStorage.getItem(MISMATCH_DISMISSED_KEY) === "1"
  );
  const prevMismatch = useRef(false);

  useEffect(() => {
    const current = !!health.alpaca_mode_mismatch;
    if (current && !prevMismatch.current) {
      localStorage.removeItem(MISMATCH_DISMISSED_KEY);
      setMismatchDismissed(false);
    }
    prevMismatch.current = current;
  }, [health.alpaca_mode_mismatch]);

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === MISMATCH_DISMISSED_KEY) {
        setMismatchDismissed(e.newValue === "1");
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const res = await fetch("/api/health");
        const data = await res.json();
        if (!cancelled) {
          setHealth({
            checked: true,
            ok: !!data.ok,
            errors: data.errors ?? [],
            alpaca_mode_mismatch: !!data.alpaca_mode_mismatch,
            alpaca_mismatch_message: data.alpaca_mismatch_message ?? "",
            db_checked_at: data.db_checked_at ?? undefined,
          });
        }
      } catch {
        if (!cancelled) {
          setHealth({
            checked: true,
            ok: false,
            errors: ["Could not reach the server — check that the backend is running."],
          });
        }
      }
    };
    check();
    const interval = setInterval(check, 10_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    if (health.checked && !health.alpaca_mode_mismatch) {
      localStorage.removeItem(MISMATCH_DISMISSED_KEY);
    }
  }, [health.checked, health.alpaca_mode_mismatch]);

  if (!health.checked) {
    return null;
  }

  if (!health.ok) {
    return <ErrorBanner errors={health.errors} dbCheckedAt={health.db_checked_at} />;
  }

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        {health.alpaca_mode_mismatch && health.alpaca_mismatch_message && (
          <AlpacaMismatchBanner
            message={health.alpaca_mismatch_message}
            dismissed={mismatchDismissed}
            onDismiss={() => {
              localStorage.setItem(MISMATCH_DISMISSED_KEY, "1");
              setMismatchDismissed(true);
            }}
          />
        )}
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Router health={health} />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
