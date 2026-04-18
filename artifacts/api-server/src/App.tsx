import { useEffect, useRef, useState } from "react";
import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { useToast } from "@/hooks/use-toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AlertCircle } from "lucide-react";
import NotFound from "@/pages/not-found";
import Settings from "@/pages/settings";

const queryClient = new QueryClient();

const HEALTH_POLL_MS = Number(import.meta.env.VITE_HEALTH_POLL_MS) || 10_000;

const MISMATCH_DISMISSED_KEY = "alpaca_mismatch_banner_dismissed";

interface HealthState {
  checked: boolean;
  ok: boolean;
  errors: string[];
  alpaca_mode_mismatch?: boolean;
  alpaca_mismatch_message?: string;
  db_checked_at?: string;
}

interface BackfillHealthData {
  available: boolean;
  loading: boolean;
  completed_at?: string;
  rows_saved?: number;
  no_bars?: number;
  errors?: number;
  error?: string;
  heartbeat_hours?: number;
  is_overdue?: boolean;
}

interface EodSweepEntry {
  ran_at: string;
  paper_healed: number;
  backtest_healed: number;
  total_healed: number;
}

interface EodSweepData {
  available: boolean;
  loading: boolean;
  ran_at?: string;
  paper_healed?: number;
  backtest_healed?: number;
  total_healed?: number;
  error?: string;
  history?: EodSweepEntry[];
}

interface EodRecalcHealth {
  available: boolean;
  loading: boolean;
  completed_at?: string;
  path?: string;
  written?: number;
  skipped?: number;
  elapsed_s?: number;
  error?: string;
}

function formatRelativeTime(isoTimestamp: string): string {
  const t = new Date(isoTimestamp);
  if (isNaN(t.getTime())) return isoTimestamp;
  const diffMs = Date.now() - t.getTime();
  const diffSec = Math.round(diffMs / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDays = Math.round(diffHr / 24);
  return `${diffDays}d ago`;
}

function formatEodAge(isoTimestamp: string): string {
  const t = new Date(isoTimestamp);
  if (isNaN(t.getTime())) return "ran at unknown time";
  const diffMs = Date.now() - t.getTime();
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 1) return "ran just now";
  if (diffMin < 60) return `ran ${diffMin} minute${diffMin === 1 ? "" : "s"} ago`;
  const hh = t.getUTCHours().toString().padStart(2, "0");
  const mm = t.getUTCMinutes().toString().padStart(2, "0");
  const nowUtc = new Date();
  const sameDay =
    t.getUTCFullYear() === nowUtc.getUTCFullYear() &&
    t.getUTCMonth() === nowUtc.getUTCMonth() &&
    t.getUTCDate() === nowUtc.getUTCDate();
  if (sameDay) return `ran today at ${hh}:${mm} UTC`;
  const prevDay = new Date(nowUtc);
  prevDay.setUTCDate(prevDay.getUTCDate() - 1);
  const yesterday =
    t.getUTCFullYear() === prevDay.getUTCFullYear() &&
    t.getUTCMonth() === prevDay.getUTCMonth() &&
    t.getUTCDate() === prevDay.getUTCDate();
  if (yesterday) return `ran yesterday at ${hh}:${mm} UTC`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 48) return `ran ${diffHr} hour${diffHr === 1 ? "" : "s"} ago`;
  const mon = t.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  const day = t.getUTCDate();
  return `ran ${mon} ${day} at ${hh}:${mm} UTC`;
}

function isEodStale(isoTimestamp: string): boolean {
  const t = new Date(isoTimestamp);
  if (isNaN(t.getTime())) return false;
  return Date.now() - t.getTime() > 30 * 60 * 60 * 1000;
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

function useSecondTick(dep?: string): void {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!dep) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [dep]);
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
  useSecondTick(dbCheckedAt);

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

interface ConfigEntry {
  value: number;
  source: "env" | "override";
}

interface BotConfig {
  paper_close_lookback_days: ConfigEntry;
  backtest_close_lookback_days: ConfigEntry;
  paper_trade_min_tcs: ConfigEntry;
  backfill_heartbeat_hours: ConfigEntry;
}

function ConfigPanel() {
  const [config, setConfig] = useState<BotConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch("/api/config");
        if (!res.ok) throw new Error(`Server returned ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          setConfig(data);
          setError(null);
          setLoading(false);
        }
      } catch {
        if (!cancelled) {
          setError("Could not load config values.");
          setLoading(false);
        }
      }
    };
    load();
    const interval = setInterval(load, 60_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const rows: Array<{ label: string; key: keyof BotConfig; unit: string }> = [
    { label: "Paper close look-back", key: "paper_close_lookback_days", unit: "days" },
    { label: "Backtest close look-back", key: "backtest_close_lookback_days", unit: "days" },
    { label: "Min TCS threshold", key: "paper_trade_min_tcs", unit: "" },
    { label: "Backfill heartbeat window", key: "backfill_heartbeat_hours", unit: "h" },
  ];

  return (
    <div
      style={{
        background: "#1e2435",
        border: "1px solid #2d3748",
        borderRadius: "10px",
        padding: "20px 24px",
        maxWidth: "640px",
        width: "100%",
      }}
    >
      <h2
        style={{
          fontSize: "13px",
          fontWeight: 700,
          color: "#94a3b8",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          marginBottom: "16px",
          marginTop: 0,
        }}
      >
        Active Configuration
      </h2>

      {loading && (
        <p style={{ fontSize: "13px", color: "#64748b", margin: 0 }}>Loading…</p>
      )}
      {error && (
        <p style={{ fontSize: "13px", color: "#f87171", margin: 0 }}>⚠ {error}</p>
      )}
      {!loading && !error && config && (
        <div style={{ display: "flex", flexDirection: "column", gap: "0" }}>
          {rows.map(({ label, key, unit }, idx) => {
            const entry = config[key];
            const isOverride = entry.source === "override";
            return (
              <div
                key={key}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  padding: "10px 0",
                  borderTop: idx > 0 ? "1px solid #2d3748" : undefined,
                }}
              >
                <span style={{ fontSize: "14px", color: "#cbd5e1", flex: 1 }}>{label}</span>
                <span
                  style={{
                    fontSize: "13px",
                    fontWeight: 700,
                    color: "#e2e8f0",
                    fontFamily: "monospace",
                  }}
                >
                  {entry.value}{unit ? ` ${unit}` : ""}
                </span>
                <span
                  title={isOverride ? "Set via user-pref override (not the env var)" : "From environment variable"}
                  style={{
                    fontSize: "11px",
                    fontWeight: 600,
                    color: isOverride ? "#fdba74" : "#475569",
                    background: isOverride ? "rgba(251,191,36,0.08)" : "rgba(255,255,255,0.04)",
                    border: isOverride ? "1px solid #92400e" : "1px solid #334155",
                    borderRadius: "4px",
                    padding: "2px 7px",
                    letterSpacing: "0.04em",
                    textTransform: "uppercase",
                    cursor: "default",
                    flexShrink: 0,
                  }}
                >
                  {isOverride ? "override" : "env"}
                </span>
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
  const [backfillErrorAlertsEnabled, setBackfillErrorAlertsEnabled] = useState<boolean | null>(null);
  const [backfillHealth, setBackfillHealth] = useState<BackfillHealthData>({ available: false, loading: true });
  const [eodSweep, setEodSweep] = useState<EodSweepData>({ available: false, loading: true });
  const [eodHistoryOpen, setEodHistoryOpen] = useState(false);
  const [eodRecalcHealth, setEodRecalcHealth] = useState<EodRecalcHealth>({ available: false, loading: true });
  useSecondTick(health.db_checked_at);

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

  useEffect(() => {
    let cancelled = false;
    fetch("/api/backfill-error-alerts")
      .then((r) => {
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setBackfillErrorAlertsEnabled(data.enabled !== false);
      })
      .catch(() => {
        if (!cancelled) setBackfillErrorAlertsEnabled(null);
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fetchBackfill = () => {
      fetch("/api/backfill-health")
        .then((r) => r.json())
        .then((data) => {
          if (!cancelled) setBackfillHealth({ loading: false, ...data });
        })
        .catch(() => {
          if (!cancelled) setBackfillHealth({ available: false, loading: false });
        });
    };
    fetchBackfill();
    const interval = setInterval(fetchBackfill, 60_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fetchEodSweep = () => {
      fetch("/api/eod-sweep")
        .then((r) => r.json())
        .then((data) => {
          if (!cancelled) setEodSweep({ loading: false, ...data });
        })
        .catch(() => {
          if (!cancelled) setEodSweep({ available: false, loading: false });
        });
    };
    fetchEodSweep();
    const interval = setInterval(fetchEodSweep, 300_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fetchEodRecalc = () => {
      fetch("/api/eod-recalc-health")
        .then((r) => r.json())
        .then((data) => {
          if (!cancelled) setEodRecalcHealth({ loading: false, ...data });
        })
        .catch(() => {
          if (!cancelled) setEodRecalcHealth({ available: false, loading: false });
        });
    };
    fetchEodRecalc();
    const interval = setInterval(fetchEodRecalc, 300_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const settingsBase = import.meta.env.BASE_URL.replace(/\/$/, "");
  const eodStale = eodSweep.ran_at ? isEodStale(eodSweep.ran_at) : false;

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

            <div style={{ borderTop: "1px solid #2d3748", paddingTop: "12px", display: "flex", alignItems: "center", gap: "10px" }}>
              <StatusDot ok={backfillErrorAlertsEnabled === true} />
              <span style={{ fontSize: "14px", color: "#cbd5e1", flex: 1 }}>Backfill Error Alerts</span>
              <a
                href={`${settingsBase}/settings#credential-alerts`}
                style={{
                  fontSize: "13px",
                  color: backfillErrorAlertsEnabled === true ? "#86efac" : backfillErrorAlertsEnabled === false ? "#94a3b8" : "#475569",
                  fontWeight: 600,
                  textDecoration: "none",
                }}
                onMouseEnter={(e) => { (e.currentTarget as HTMLAnchorElement).style.textDecoration = "underline"; }}
                onMouseLeave={(e) => { (e.currentTarget as HTMLAnchorElement).style.textDecoration = "none"; }}
                title="Go to backfill error alerts settings"
              >
                {backfillErrorAlertsEnabled === null ? "—" : backfillErrorAlertsEnabled ? "On" : "Off"}
              </a>
            </div>

            <div style={{ borderTop: "1px solid #2d3748", paddingTop: "12px", display: "flex", alignItems: "flex-start", gap: "10px" }}>
              {backfillHealth.loading ? (
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#475569", display: "inline-block", flexShrink: 0, marginTop: "3px" }} />
              ) : !backfillHealth.available ? (
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#f87171", display: "inline-block", flexShrink: 0, marginTop: "3px", boxShadow: "0 0 6px #f87171" }} />
              ) : (backfillHealth.errors ?? 0) > 0 ? (
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#f87171", display: "inline-block", flexShrink: 0, marginTop: "3px", boxShadow: "0 0 6px #f87171" }} />
              ) : backfillHealth.is_overdue ? (
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#f97316", display: "inline-block", flexShrink: 0, marginTop: "3px", boxShadow: "0 0 6px #f97316" }} />
              ) : (backfillHealth.no_bars ?? 0) > 0 ? (
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#fbbf24", display: "inline-block", flexShrink: 0, marginTop: "3px", boxShadow: "0 0 6px #fbbf24" }} />
              ) : (
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#4ade80", display: "inline-block", flexShrink: 0, marginTop: "3px", boxShadow: "0 0 6px #4ade80" }} />
              )}
              <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: "6px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <span style={{ fontSize: "14px", color: "#cbd5e1", flex: 1 }}>Backfill</span>
                  {backfillHealth.loading ? (
                    <span style={{ fontSize: "13px", color: "#475569", fontWeight: 600 }}>—</span>
                  ) : !backfillHealth.available ? (
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 700,
                        color: "#fca5a5",
                        background: "rgba(239,68,68,0.12)",
                        border: "1px solid #7f1d1d",
                        borderRadius: "4px",
                        padding: "2px 7px",
                        letterSpacing: "0.04em",
                        textTransform: "uppercase",
                      }}
                      title={`No backfill run recorded — threshold is ${backfillHealth.heartbeat_hours ?? 25} h`}
                    >
                      Overdue
                    </span>
                  ) : (
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 700,
                        color: backfillHealth.is_overdue ? "#fdba74" : "#86efac",
                        background: backfillHealth.is_overdue ? "rgba(249,115,22,0.12)" : "rgba(74,222,128,0.1)",
                        border: backfillHealth.is_overdue ? "1px solid #9a3412" : "1px solid #14532d",
                        borderRadius: "4px",
                        padding: "2px 7px",
                        letterSpacing: "0.04em",
                        textTransform: "uppercase",
                      }}
                      title={`Heartbeat threshold: ${backfillHealth.heartbeat_hours ?? 25} h`}
                    >
                      {backfillHealth.is_overdue ? "Overdue" : "OK"}
                    </span>
                  )}
                </div>
                {!backfillHealth.loading && backfillHealth.available && (
                  <a
                    href={`${settingsBase}/settings#backfill-health`}
                    style={{ fontSize: "12px", textDecoration: "none", display: "flex", alignItems: "center", gap: "6px", color: "#64748b" }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLAnchorElement).style.color = "#94a3b8"; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLAnchorElement).style.color = "#64748b"; }}
                    title="Go to backfill health details"
                  >
                    <span style={{ color: "#94a3b8" }}>{(backfillHealth.rows_saved ?? 0).toLocaleString()} rows</span>
                    {(backfillHealth.no_bars ?? 0) > 0 && (
                      <span style={{ color: "#fbbf24" }}>· {backfillHealth.no_bars} no-bars</span>
                    )}
                    {(backfillHealth.errors ?? 0) > 0 && (
                      <span style={{ color: "#f87171" }}>· {backfillHealth.errors} err</span>
                    )}
                    {backfillHealth.completed_at && (
                      <span>· {formatRelativeTime(backfillHealth.completed_at)}</span>
                    )}
                    {backfillHealth.heartbeat_hours != null && (
                      <span style={{ color: "#475569" }}>· {backfillHealth.heartbeat_hours} h window</span>
                    )}
                  </a>
                )}
                {!backfillHealth.loading && !backfillHealth.available && (
                  <span style={{ fontSize: "12px", color: "#475569" }}>
                    No run recorded — threshold {backfillHealth.heartbeat_hours ?? 25} h
                  </span>
                )}
              </div>
            </div>

            <div style={{ borderTop: "1px solid #2d3748", paddingTop: "12px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                {eodSweep.loading ? (
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#475569", display: "inline-block", flexShrink: 0 }} />
                ) : !eodSweep.available ? (
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#475569", display: "inline-block", flexShrink: 0 }} />
                ) : eodStale ? (
                  <span title="Stale — last run was over 30 hours ago" style={{ width: 10, height: 10, borderRadius: "50%", background: "#f59e0b", display: "inline-block", flexShrink: 0, boxShadow: "0 0 6px #f59e0b" }} />
                ) : (
                  <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#4ade80", display: "inline-block", flexShrink: 0, boxShadow: "0 0 6px #4ade80" }} />
                )}
                <span style={{ fontSize: "14px", color: "#cbd5e1", flex: 1 }}>EOD P&amp;L Sweep</span>
                {eodSweep.loading ? (
                  <span style={{ fontSize: "13px", color: "#475569", fontWeight: 600 }}>—</span>
                ) : !eodSweep.available ? (
                  <span style={{ fontSize: "13px", color: "#475569", fontWeight: 600 }}>No data</span>
                ) : (
                  <span
                    style={{ fontSize: "13px", display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}
                    title={eodSweep.ran_at ? `Last run: ${formatUtc(eodSweep.ran_at)}` : undefined}
                  >
                    {(eodSweep.total_healed ?? 0) === 0 ? (
                      <span style={{ color: "#86efac", fontWeight: 600 }}>All healed</span>
                    ) : (
                      <span style={{ color: "#86efac", fontWeight: 600 }}>{eodSweep.total_healed} healed</span>
                    )}
                    {(eodSweep.paper_healed ?? 0) > 0 && (
                      <span style={{ color: "#94a3b8" }}>({eodSweep.paper_healed}p</span>
                    )}
                    {(eodSweep.paper_healed ?? 0) > 0 && (eodSweep.backtest_healed ?? 0) > 0 && (
                      <span style={{ color: "#94a3b8" }}>+{eodSweep.backtest_healed}b)</span>
                    )}
                    {(eodSweep.paper_healed ?? 0) > 0 && (eodSweep.backtest_healed ?? 0) === 0 && (
                      <span style={{ color: "#94a3b8" }}>)</span>
                    )}
                    {eodSweep.ran_at && (
                      <span
                        style={{
                          color: eodStale ? "#f59e0b" : "#64748b",
                          fontSize: "12px",
                          fontStyle: eodStale ? "italic" : undefined,
                        }}
                        title={eodStale ? "Stale — over 30 hours since last sweep" : undefined}
                      >
                        · {formatEodAge(eodSweep.ran_at)}
                      </span>
                    )}
                  </span>
                )}
                {(eodSweep.history ?? []).length > 0 && (
                  <button
                    onClick={() => setEodHistoryOpen((o) => !o)}
                    style={{
                      background: "none",
                      border: "none",
                      cursor: "pointer",
                      color: "#64748b",
                      fontSize: "11px",
                      padding: "2px 6px",
                      borderRadius: "4px",
                      lineHeight: 1,
                    }}
                    title={eodHistoryOpen ? "Hide history" : "Show history"}
                  >
                    {eodHistoryOpen ? "▲" : "▼"} {(eodSweep.history ?? []).length}
                  </button>
                )}
              </div>
              {eodHistoryOpen && (eodSweep.history ?? []).length > 0 && (
                <div
                  style={{
                    marginTop: "10px",
                    background: "#131720",
                    border: "1px solid #2d3748",
                    borderRadius: "6px",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr auto auto auto",
                      gap: "0",
                      fontSize: "11px",
                      color: "#475569",
                      fontWeight: 600,
                      padding: "6px 12px",
                      borderBottom: "1px solid #2d3748",
                      textTransform: "uppercase",
                      letterSpacing: "0.05em",
                    }}
                  >
                    <span>Date</span>
                    <span style={{ textAlign: "right", paddingLeft: "12px" }}>Paper</span>
                    <span style={{ textAlign: "right", paddingLeft: "12px" }}>Backtest</span>
                    <span style={{ textAlign: "right", paddingLeft: "12px" }}>Total</span>
                  </div>
                  {(eodSweep.history ?? []).map((entry, idx) => (
                    <div
                      key={entry.ran_at}
                      style={{
                        display: "grid",
                        gridTemplateColumns: "1fr auto auto auto",
                        gap: "0",
                        fontSize: "12px",
                        padding: "6px 12px",
                        borderBottom: idx < (eodSweep.history ?? []).length - 1 ? "1px solid #1e2435" : "none",
                        background: idx % 2 === 0 ? "transparent" : "#0e1117",
                      }}
                    >
                      <span style={{ color: "#94a3b8" }} title={formatUtc(entry.ran_at)}>
                        {new Date(entry.ran_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
                        <span style={{ color: "#475569", marginLeft: "6px", fontSize: "11px" }}>
                          {new Date(entry.ran_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
                        </span>
                      </span>
                      <span style={{ color: "#cbd5e1", textAlign: "right", paddingLeft: "12px" }}>{entry.paper_healed}</span>
                      <span style={{ color: "#cbd5e1", textAlign: "right", paddingLeft: "12px" }}>{entry.backtest_healed}</span>
                      <span style={{ color: entry.total_healed === 0 ? "#86efac" : "#f8fafc", fontWeight: 600, textAlign: "right", paddingLeft: "12px" }}>
                        {entry.total_healed === 0 ? "✓" : entry.total_healed}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div style={{ borderTop: "1px solid #2d3748", paddingTop: "12px", display: "flex", alignItems: "center", gap: "10px" }}>
              {eodRecalcHealth.loading ? (
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#475569", display: "inline-block", flexShrink: 0 }} />
              ) : !eodRecalcHealth.available ? (
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#475569", display: "inline-block", flexShrink: 0 }} />
              ) : (
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#4ade80", display: "inline-block", flexShrink: 0, boxShadow: "0 0 6px #4ade80" }} />
              )}
              <span style={{ fontSize: "14px", color: "#cbd5e1", flex: 1 }}>Last P&amp;L Recalc</span>
              {eodRecalcHealth.loading ? (
                <span style={{ fontSize: "13px", color: "#475569", fontWeight: 600 }}>—</span>
              ) : !eodRecalcHealth.available ? (
                <span style={{ fontSize: "13px", color: "#475569", fontWeight: 600 }}>No data</span>
              ) : (
                <a
                  href={`${settingsBase}/settings#eod-recalc-health`}
                  style={{ fontSize: "12px", textDecoration: "none", display: "flex", alignItems: "center", gap: "6px", color: "#64748b" }}
                  onMouseEnter={(e) => { (e.currentTarget as HTMLAnchorElement).style.color = "#94a3b8"; }}
                  onMouseLeave={(e) => { (e.currentTarget as HTMLAnchorElement).style.color = "#64748b"; }}
                  title={eodRecalcHealth.completed_at ? `Last run: ${formatUtc(eodRecalcHealth.completed_at)}` : undefined}
                >
                  <span style={{ color: "#86efac", fontWeight: 600 }}>
                    {(eodRecalcHealth.written ?? 0)} rows
                  </span>
                  {eodRecalcHealth.elapsed_s != null && (
                    <span style={{ color: "#94a3b8" }}>· {eodRecalcHealth.elapsed_s.toFixed(1)}s</span>
                  )}
                  {eodRecalcHealth.completed_at && (
                    <span>· {formatRelativeTime(eodRecalcHealth.completed_at)}</span>
                  )}
                </a>
              )}
            </div>
          </div>
        </div>

        <ConfigPanel />

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
  const { toast } = useToast();

  useEffect(() => {
    const current = !!health.alpaca_mode_mismatch;
    if (current && !prevMismatch.current) {
      localStorage.removeItem(MISMATCH_DISMISSED_KEY);
      setMismatchDismissed(false);
    } else if (!current && prevMismatch.current) {
      toast({
        title: "Alpaca credentials are now consistent",
        description: "The credential mismatch has been resolved.",
        duration: 5000,
        variant: "success",
      });
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
    const interval = setInterval(check, HEALTH_POLL_MS);
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
