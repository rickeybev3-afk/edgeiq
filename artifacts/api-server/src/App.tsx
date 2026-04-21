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

interface PdtDayEntry {
  date: string;
  count: number;
  avg_tcs: number | null;
  tickers: string[];
  estimated_r: number | null;
  r_rows: number;
}

interface PdtSummary {
  total_deferred: number;
  avg_tcs: number | null;
  total_estimated_r: number | null;
  r_rows: number;
  elite_slots_used: number;
}

interface PdtGatedData {
  available: boolean;
  loading: boolean;
  per_day: PdtDayEntry[];
  summary: PdtSummary;
  error: string | null;
}

function PdtGatedPanel() {
  const [data, setData] = useState<PdtGatedData>({
    available: false,
    loading: true,
    per_day: [],
    summary: { total_deferred: 0, avg_tcs: null, total_estimated_r: null, r_rows: 0, elite_slots_used: 0 },
    error: null,
  });
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch("/api/pdt-gated-trades");
        const json = await res.json();
        if (!cancelled) {
          setData({
            available: json.available ?? false,
            loading: false,
            per_day: json.per_day ?? [],
            summary: json.summary ?? { total_deferred: 0, avg_tcs: null, total_estimated_r: null, r_rows: 0, elite_slots_used: 0 },
            error: json.error ?? null,
          });
        }
      } catch {
        if (!cancelled) {
          setData((prev) => ({ ...prev, loading: false, error: "Could not load PDT gate data." }));
        }
      }
    };
    load();
    const interval = setInterval(load, 5 * 60_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const s = data.summary;
  const hasData = data.available && s.total_deferred > 0;

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
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "4px" }}>
        <h2
          style={{
            fontSize: "13px",
            fontWeight: 700,
            color: "#94a3b8",
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            margin: 0,
          }}
        >
          PDT Quality Gate — Deferred Setups
        </h2>
        {hasData && data.per_day.length > 0 && (
          <button
            onClick={() => setExpanded((o) => !o)}
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
            title={expanded ? "Hide daily breakdown" : "Show daily breakdown"}
          >
            {expanded ? "▲ Hide" : "▼ Details"}
          </button>
        )}
      </div>
      <p
        style={{
          fontSize: "12px",
          color: "#64748b",
          marginTop: "4px",
          marginBottom: "16px",
          lineHeight: "1.5",
        }}
      >
        TCS&lt;70 setups deferred while account is sub-$25k (last 90 days). PDT slots reserved for elite tier only.
      </p>

      {data.loading && (
        <p style={{ fontSize: "13px", color: "#64748b", margin: 0 }}>Loading…</p>
      )}
      {!data.loading && data.error && !hasData && (
        <p style={{ fontSize: "13px", color: "#f87171", margin: 0 }}>⚠ {data.error}</p>
      )}
      {!data.loading && !data.error && !hasData && (
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
          No PDT-gated setups in the last 90 days — all qualifying signals were taken.
        </div>
      )}

      {hasData && (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
              gap: "10px",
              marginBottom: expanded ? "16px" : 0,
            }}
          >
            <div
              style={{
                background: "rgba(251,146,60,0.08)",
                border: "1px solid rgba(251,146,60,0.25)",
                borderRadius: "8px",
                padding: "10px 14px",
              }}
            >
              <div style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Deferred
              </div>
              <div style={{ fontSize: "22px", fontWeight: 700, color: "#fb923c", fontFamily: "monospace" }}>
                {s.total_deferred}
              </div>
              <div style={{ fontSize: "11px", color: "#64748b", marginTop: "2px" }}>setups</div>
            </div>

            <div
              style={{
                background: "rgba(148,163,184,0.06)",
                border: "1px solid #2d3748",
                borderRadius: "8px",
                padding: "10px 14px",
              }}
            >
              <div style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Avg TCS
              </div>
              <div style={{ fontSize: "22px", fontWeight: 700, color: "#cbd5e1", fontFamily: "monospace" }}>
                {s.avg_tcs != null ? s.avg_tcs.toFixed(1) : "—"}
              </div>
              <div style={{ fontSize: "11px", color: "#64748b", marginTop: "2px" }}>of deferred</div>
            </div>

            <div
              style={{
                background: s.total_estimated_r != null && s.total_estimated_r > 0
                  ? "rgba(74,222,128,0.06)"
                  : s.total_estimated_r != null && s.total_estimated_r < 0
                    ? "rgba(239,68,68,0.06)"
                    : "rgba(148,163,184,0.06)",
                border: "1px solid #2d3748",
                borderRadius: "8px",
                padding: "10px 14px",
              }}
            >
              <div style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Est. Missed R
              </div>
              <div
                style={{
                  fontSize: "22px",
                  fontWeight: 700,
                  fontFamily: "monospace",
                  color: s.total_estimated_r == null
                    ? "#64748b"
                    : s.total_estimated_r >= 0
                      ? "#4ade80"
                      : "#f87171",
                }}
              >
                {s.total_estimated_r != null
                  ? `${s.total_estimated_r >= 0 ? "+" : ""}${s.total_estimated_r.toFixed(2)}R`
                  : "—"}
              </div>
              <div style={{ fontSize: "11px", color: "#64748b", marginTop: "2px" }}>
                {s.r_rows > 0 ? `${s.r_rows} of ${s.total_deferred} settled` : "no settled data"}
              </div>
            </div>

            <div
              style={{
                background: "rgba(99,102,241,0.08)",
                border: "1px solid rgba(99,102,241,0.25)",
                borderRadius: "8px",
                padding: "10px 14px",
              }}
            >
              <div style={{ fontSize: "11px", color: "#94a3b8", marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                Elite Slots Used
              </div>
              <div style={{ fontSize: "22px", fontWeight: 700, color: "#818cf8", fontFamily: "monospace" }}>
                {s.elite_slots_used}
              </div>
              <div style={{ fontSize: "11px", color: "#64748b", marginTop: "2px" }}>TCS≥70 trades taken</div>
            </div>
          </div>

          {expanded && data.per_day.length > 0 && (
            <div
              style={{
                background: "#131720",
                border: "1px solid #2d3748",
                borderRadius: "8px",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "auto 1fr auto auto auto",
                  gap: "0",
                  fontSize: "11px",
                  color: "#475569",
                  fontWeight: 600,
                  padding: "6px 14px",
                  borderBottom: "1px solid #2d3748",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                }}
              >
                <span>Date</span>
                <span style={{ paddingLeft: "12px" }}>Tickers</span>
                <span style={{ textAlign: "right", paddingLeft: "12px" }}>Count</span>
                <span style={{ textAlign: "right", paddingLeft: "12px" }}>Avg TCS</span>
                <span style={{ textAlign: "right", paddingLeft: "12px" }}>Est. R</span>
              </div>
              {data.per_day.map((day) => (
                <div
                  key={day.date}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "auto 1fr auto auto auto",
                    gap: "0",
                    fontSize: "12px",
                    padding: "7px 14px",
                    borderBottom: "1px solid #1e2435",
                    alignItems: "center",
                  }}
                >
                  <span style={{ color: "#64748b", fontFamily: "monospace", whiteSpace: "nowrap" }}>{day.date}</span>
                  <span
                    style={{
                      color: "#94a3b8",
                      fontSize: "11px",
                      paddingLeft: "12px",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={day.tickers.join(", ")}
                  >
                    {day.tickers.join(", ") || "—"}
                  </span>
                  <span style={{ color: "#fb923c", fontWeight: 600, textAlign: "right", paddingLeft: "12px" }}>{day.count}</span>
                  <span style={{ color: "#cbd5e1", textAlign: "right", paddingLeft: "12px" }}>
                    {day.avg_tcs != null ? day.avg_tcs.toFixed(1) : "—"}
                  </span>
                  <span
                    style={{
                      fontFamily: "monospace",
                      fontWeight: 600,
                      textAlign: "right",
                      paddingLeft: "12px",
                      color: day.estimated_r == null
                        ? "#475569"
                        : day.estimated_r >= 0
                          ? "#4ade80"
                          : "#f87171",
                    }}
                  >
                    {day.estimated_r != null
                      ? `${day.estimated_r >= 0 ? "+" : ""}${day.estimated_r.toFixed(2)}R`
                      : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
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

interface ConfigRowEdit {
  draft: string;
  saving: boolean;
  saved: boolean;
  error: string | null;
}

type ConfigKey = keyof BotConfig;

function makeEditState(value: number): ConfigRowEdit {
  return { draft: String(value), saving: false, saved: false, error: null };
}

async function saveConfigValue(
  key: ConfigKey,
  draft: string
): Promise<{ value: number; source: "env" | "override" }> {
  if (key === "backfill_heartbeat_hours") {
    const parsed = parseFloat(draft);
    if (isNaN(parsed) || parsed < 1 || parsed > 8760) throw new Error("Enter a number between 1 and 8760.");
    const res = await fetch("/api/backfill-heartbeat-window", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hours: parsed }),
    });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error ?? `Server returned ${res.status}`); }
    const data = await res.json();
    return { value: data.hours, source: data.source === "override" ? "override" : "env" };
  }
  if (key === "paper_close_lookback_days") {
    const parsed = parseInt(draft, 10);
    if (isNaN(parsed) || parsed < 1 || parsed > 3650) throw new Error("Enter a whole number between 1 and 3650.");
    const res = await fetch("/api/paper-lookback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ days: parsed }),
    });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error ?? `Server returned ${res.status}`); }
    const data = await res.json();
    return { value: data.days, source: data.source === "override" ? "override" : "env" };
  }
  if (key === "backtest_close_lookback_days") {
    const parsed = parseInt(draft, 10);
    if (isNaN(parsed) || parsed < 1 || parsed > 3650) throw new Error("Enter a whole number between 1 and 3650.");
    const res = await fetch("/api/backtest-lookback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ days: parsed }),
    });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error ?? `Server returned ${res.status}`); }
    const data = await res.json();
    return { value: data.days, source: data.source === "override" ? "override" : "env" };
  }
  if (key === "paper_trade_min_tcs") {
    const parsed = parseInt(draft, 10);
    if (isNaN(parsed) || parsed < 0 || parsed > 100) throw new Error("Enter a whole number between 0 and 100.");
    const res = await fetch("/api/paper-trade-min-tcs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: parsed }),
    });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error ?? `Server returned ${res.status}`); }
    const data = await res.json();
    return { value: data.value, source: data.source === "override" ? "override" : "env" };
  }
  throw new Error("Unknown config key");
}

async function resetConfigValue(
  key: ConfigKey
): Promise<{ value: number; source: "env" | "override" }> {
  if (key === "backfill_heartbeat_hours") {
    const res = await fetch("/api/backfill-heartbeat-window", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ hours: null }) });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error ?? `Server returned ${res.status}`); }
    const data = await res.json();
    return { value: data.hours, source: "env" };
  }
  if (key === "paper_close_lookback_days") {
    const res = await fetch("/api/paper-lookback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ days: null }) });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error ?? `Server returned ${res.status}`); }
    const data = await res.json();
    return { value: data.days, source: "env" };
  }
  if (key === "backtest_close_lookback_days") {
    const res = await fetch("/api/backtest-lookback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ days: null }) });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error ?? `Server returned ${res.status}`); }
    const data = await res.json();
    return { value: data.days, source: "env" };
  }
  if (key === "paper_trade_min_tcs") {
    const res = await fetch("/api/paper-trade-min-tcs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value: null }) });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error ?? `Server returned ${res.status}`); }
    const data = await res.json();
    return { value: data.value, source: "env" };
  }
  throw new Error("Unknown config key");
}

interface GridSearchBestCombo {
  tcs_offset?: number;
  tcs_label?: string;
  rvol_min?: number;
  gap_min?: number;
  follow_min?: number;
  follow_label?: string;
  struct_filter?: string;
  struct_label?: string;
  excl_false_break?: boolean;
  n_trades?: number;
  win_rate?: number;
  avg_r?: number;
  total_r?: number;
  profit_factor?: number;
  sharpe?: number;
  max_drawdown_r?: number;
  trades_per_week?: number;
  proj_weekly_usd?: number;
  low_sample?: boolean;
}

interface GridSearchData {
  available: boolean;
  stale?: boolean;
  run_at?: string;
  combos_tested?: number;
  combos_qualifying?: number;
  min_n?: number;
  best_combo?: GridSearchBestCombo;
  error?: string;
}

function GridSearchPanel() {
  const [data, setData] = useState<GridSearchData>({ available: false });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch("/api/grid-search-summary");
        const json: GridSearchData = await res.json();
        if (!cancelled) { setData(json); setLoading(false); }
      } catch {
        if (!cancelled) { setData({ available: false, error: "Could not load grid search data." }); setLoading(false); }
      }
    };
    load();
    const interval = setInterval(load, 5 * 60_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const bc = data.best_combo;

  return (
    <div
      style={{
        background: "#1e2435",
        border: data.stale ? "1px solid #78350f" : "1px solid #2d3748",
        borderRadius: "10px",
        padding: "20px 24px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
        <h2 style={{ fontSize: "13px", fontWeight: 700, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.06em", margin: 0 }}>
          Grid Search
        </h2>
        {!loading && data.available && data.stale && (
          <span
            title="Last run was more than 7 days ago"
            style={{
              fontSize: "11px",
              fontWeight: 700,
              color: "#fbbf24",
              background: "rgba(251,191,36,0.1)",
              border: "1px solid #78350f",
              borderRadius: "4px",
              padding: "2px 8px",
              letterSpacing: "0.04em",
              textTransform: "uppercase",
            }}
          >
            Stale
          </span>
        )}
      </div>

      {loading && (
        <p style={{ fontSize: "13px", color: "#64748b", margin: 0 }}>Loading…</p>
      )}
      {!loading && !data.available && (
        <p style={{ fontSize: "13px", color: "#64748b", margin: 0 }}>
          {data.error ?? "No grid search results found. Run the weekly grid search to populate this panel."}
        </p>
      )}
      {!loading && data.available && (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
            {data.run_at && (
              <span style={{ fontSize: "12px", color: "#64748b", fontFamily: "monospace" }}>
                {formatEodAge(data.run_at)}
              </span>
            )}
            {data.combos_tested != null && (
              <span style={{ fontSize: "12px", color: "#475569" }}>
                {data.combos_qualifying?.toLocaleString() ?? "—"} / {data.combos_tested?.toLocaleString()} combos qualified
              </span>
            )}
            {data.min_n != null && (
              <span style={{ fontSize: "12px", color: "#475569" }}>
                min N={data.min_n}
              </span>
            )}
          </div>

          {bc && (
            <div
              style={{
                background: "rgba(255,255,255,0.03)",
                border: "1px solid #2d3748",
                borderRadius: "8px",
                padding: "14px 16px",
                display: "flex",
                flexDirection: "column",
                gap: "10px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "8px" }}>
                <span style={{ fontSize: "13px", fontWeight: 700, color: "#cbd5e1" }}>Best Combo</span>
                {bc.low_sample && (
                  <span
                    title="Fewer trades than the minimum sample threshold — treat with caution"
                    style={{
                      fontSize: "11px",
                      fontWeight: 700,
                      color: "#f59e0b",
                      background: "rgba(245,158,11,0.1)",
                      border: "1px solid #92400e",
                      borderRadius: "4px",
                      padding: "1px 6px",
                      letterSpacing: "0.04em",
                      textTransform: "uppercase",
                    }}
                  >
                    Low sample
                  </span>
                )}
              </div>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))",
                  gap: "8px 16px",
                }}
              >
                {[
                  { label: "Sharpe", value: bc.sharpe?.toFixed(2) },
                  { label: "N trades", value: bc.n_trades?.toLocaleString() },
                  { label: "Win rate", value: bc.win_rate != null ? `${bc.win_rate.toFixed(1)}%` : undefined },
                  { label: "Avg R", value: bc.avg_r?.toFixed(3) },
                  { label: "Total R", value: bc.total_r?.toFixed(2) },
                  { label: "Max DD", value: bc.max_drawdown_r != null ? `${bc.max_drawdown_r.toFixed(2)} R` : undefined },
                  { label: "Profit factor", value: bc.profit_factor?.toFixed(2) },
                  { label: "Trades/wk", value: bc.trades_per_week?.toFixed(1) },
                ].map(({ label, value }) => (
                  value != null ? (
                    <div key={label} style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                      <span style={{ fontSize: "11px", color: "#475569", textTransform: "uppercase", letterSpacing: "0.04em" }}>{label}</span>
                      <span style={{ fontSize: "14px", fontWeight: 700, color: "#e2e8f0", fontFamily: "monospace" }}>{value}</span>
                    </div>
                  ) : null
                ))}
              </div>

              <div style={{ borderTop: "1px solid #2d3748", paddingTop: "10px", display: "flex", flexDirection: "column", gap: "4px" }}>
                <span style={{ fontSize: "11px", color: "#475569", textTransform: "uppercase", letterSpacing: "0.04em", marginBottom: "4px" }}>Parameters</span>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                  {bc.tcs_label && (
                    <span style={{ fontSize: "12px", color: "#94a3b8", background: "rgba(255,255,255,0.05)", border: "1px solid #334155", borderRadius: "4px", padding: "2px 8px" }}>
                      TCS: {bc.tcs_label}
                    </span>
                  )}
                  {bc.rvol_min != null && (
                    <span style={{ fontSize: "12px", color: "#94a3b8", background: "rgba(255,255,255,0.05)", border: "1px solid #334155", borderRadius: "4px", padding: "2px 8px" }}>
                      RVol ≥ {bc.rvol_min}
                    </span>
                  )}
                  {bc.gap_min != null && (
                    <span style={{ fontSize: "12px", color: "#94a3b8", background: "rgba(255,255,255,0.05)", border: "1px solid #334155", borderRadius: "4px", padding: "2px 8px" }}>
                      Gap ≥ {bc.gap_min}%
                    </span>
                  )}
                  {bc.follow_label && (
                    <span style={{ fontSize: "12px", color: "#94a3b8", background: "rgba(255,255,255,0.05)", border: "1px solid #334155", borderRadius: "4px", padding: "2px 8px" }}>
                      Follow {bc.follow_label}
                    </span>
                  )}
                  {bc.struct_label && (
                    <span style={{ fontSize: "12px", color: "#94a3b8", background: "rgba(255,255,255,0.05)", border: "1px solid #334155", borderRadius: "4px", padding: "2px 8px" }}>
                      {bc.struct_label}
                    </span>
                  )}
                  {bc.excl_false_break != null && (
                    <span style={{ fontSize: "12px", color: "#94a3b8", background: "rgba(255,255,255,0.05)", border: "1px solid #334155", borderRadius: "4px", padding: "2px 8px" }}>
                      Excl false break: {bc.excl_false_break ? "Yes" : "No"}
                    </span>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ConfigPanel() {
  const [config, setConfig] = useState<BotConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editState, setEditState] = useState<Record<ConfigKey, ConfigRowEdit>>({
    paper_close_lookback_days: makeEditState(60),
    backtest_close_lookback_days: makeEditState(60),
    paper_trade_min_tcs: makeEditState(50),
    backfill_heartbeat_hours: makeEditState(25),
  });

  const loadConfig = async (cancelled: { val: boolean }, isInitial = false) => {
    try {
      const res = await fetch("/api/config");
      if (!res.ok) throw new Error(`Server returned ${res.status}`);
      const data: BotConfig = await res.json();
      if (!cancelled.val) {
        setConfig(data);
        setError(null);
        setLoading(false);
        const keys: ConfigKey[] = [
          "paper_close_lookback_days",
          "backtest_close_lookback_days",
          "paper_trade_min_tcs",
          "backfill_heartbeat_hours",
        ];
        setEditState((prev) => {
          const next = { ...prev };
          for (const key of keys) {
            const row = prev[key];
            if (row.saving) continue;
            if (isInitial || row.draft === String(data[key].value)) {
              next[key] = makeEditState(data[key].value);
            }
          }
          return next;
        });
      }
    } catch {
      if (!cancelled.val) {
        setError("Could not load config values.");
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    const cancelled = { val: false };
    loadConfig(cancelled, true);
    const interval = setInterval(() => loadConfig(cancelled, false), 60_000);
    return () => { cancelled.val = true; clearInterval(interval); };
  }, []);

  const setRowEdit = (key: ConfigKey, patch: Partial<ConfigRowEdit>) => {
    setEditState((s) => ({ ...s, [key]: { ...s[key], ...patch } }));
  };

  const handleSave = async (key: ConfigKey) => {
    setRowEdit(key, { saving: true, error: null, saved: false });
    try {
      const result = await saveConfigValue(key, editState[key].draft);
      setConfig((c) => c ? { ...c, [key]: { value: result.value, source: result.source } } : c);
      setRowEdit(key, { saving: false, saved: true, draft: String(result.value), error: null });
      setTimeout(() => setRowEdit(key, { saved: false }), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setRowEdit(key, { saving: false, error: msg });
    }
  };

  const handleReset = async (key: ConfigKey) => {
    setRowEdit(key, { saving: true, error: null, saved: false });
    try {
      const result = await resetConfigValue(key);
      setConfig((c) => c ? { ...c, [key]: { value: result.value, source: "env" } } : c);
      setRowEdit(key, { saving: false, saved: true, draft: String(result.value), error: null });
      setTimeout(() => setRowEdit(key, { saved: false }), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setRowEdit(key, { saving: false, error: msg });
    }
  };

  const rows: Array<{ label: string; key: ConfigKey; unit: string; step?: string; min: string; max: string }> = [
    { label: "Paper close look-back", key: "paper_close_lookback_days", unit: "days", min: "1", max: "3650" },
    { label: "Backtest close look-back", key: "backtest_close_lookback_days", unit: "days", min: "1", max: "3650" },
    { label: "Min TCS threshold", key: "paper_trade_min_tcs", unit: "", min: "0", max: "100" },
    { label: "Backfill heartbeat window", key: "backfill_heartbeat_hours", unit: "h", step: "0.5", min: "1", max: "8760" },
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
          {rows.map(({ label, key, unit, step, min, max }, idx) => {
            const entry = config[key];
            const isOverride = entry.source === "override";
            const row = editState[key];
            const isDirty = row.draft !== String(entry.value);
            return (
              <div
                key={key}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "6px",
                  padding: "12px 0",
                  borderTop: idx > 0 ? "1px solid #2d3748" : undefined,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <span style={{ fontSize: "14px", color: "#cbd5e1", flex: 1 }}>{label}</span>
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
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <input
                    type="number"
                    step={step ?? "1"}
                    min={min}
                    max={max}
                    value={row.draft}
                    disabled={row.saving}
                    onChange={(e) => setRowEdit(key, { draft: e.target.value, error: null })}
                    onKeyDown={(e) => { if (e.key === "Enter") handleSave(key); }}
                    style={{
                      width: "90px",
                      padding: "5px 8px",
                      fontSize: "13px",
                      fontFamily: "monospace",
                      background: "#0e1117",
                      border: row.error ? "1px solid #ef4444" : "1px solid #334155",
                      borderRadius: "6px",
                      color: "#e2e8f0",
                      outline: "none",
                    }}
                  />
                  {unit && <span style={{ fontSize: "13px", color: "#64748b" }}>{unit}</span>}
                  <button
                    onClick={() => handleSave(key)}
                    disabled={row.saving || !isDirty}
                    style={{
                      padding: "5px 12px",
                      fontSize: "12px",
                      fontWeight: 600,
                      background: isDirty && !row.saving ? "#3b82f6" : "#1e293b",
                      color: isDirty && !row.saving ? "#fff" : "#475569",
                      border: "1px solid #334155",
                      borderRadius: "6px",
                      cursor: isDirty && !row.saving ? "pointer" : "default",
                      transition: "background 0.15s",
                    }}
                  >
                    {row.saving ? "Saving…" : "Save"}
                  </button>
                  {isOverride && (
                    <button
                      onClick={() => handleReset(key)}
                      disabled={row.saving}
                      title="Clear override and revert to env-var default"
                      style={{
                        padding: "5px 10px",
                        fontSize: "12px",
                        fontWeight: 500,
                        background: "transparent",
                        color: "#94a3b8",
                        border: "1px solid #334155",
                        borderRadius: "6px",
                        cursor: row.saving ? "default" : "pointer",
                      }}
                    >
                      Reset
                    </button>
                  )}
                  {row.saved && (
                    <span style={{ fontSize: "12px", color: "#4ade80" }}>✓ Saved</span>
                  )}
                </div>
                {row.error && (
                  <span style={{ fontSize: "12px", color: "#f87171" }}>{row.error}</span>
                )}
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

interface ScreenerCalibItem {
  key: string;
  label: string;
  count: number;
  threshold: number;
  ready: boolean;
  script: string;
  extra_args: string;
  last_alerted_utc: string | null;
  error: string | null;
}

function formatLastAlerted(utcIso: string): string {
  const alertedAt = new Date(utcIso);
  if (isNaN(alertedAt.getTime())) return "alerted recently";
  const diffMs = Date.now() - alertedAt.getTime();
  if (diffMs < 0) return "alerted just now";
  const diffMins = Math.floor(diffMs / 60_000);
  if (diffMins < 2) return "alerted just now";
  if (diffMins < 60) return diffMins === 1 ? "alerted 1 minute ago" : `alerted ${diffMins} minutes ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return diffHours === 1 ? "alerted 1 hour ago" : `alerted ${diffHours} hours ago`;
  const diffDays = Math.floor(diffHours / 24);
  return diffDays === 1 ? "alerted 1 day ago" : `alerted ${diffDays} days ago`;
}

interface ScreenerCalibState {
  loading: boolean;
  screeners: ScreenerCalibItem[];
  error: string | null;
}

function Home({ health }: { health: HealthState }) {
  const [credAlertsEnabled, setCredAlertsEnabled] = useState<boolean | null>(null);
  const [backfillErrorAlertsEnabled, setBackfillErrorAlertsEnabled] = useState<boolean | null>(null);
  const [backfillHealth, setBackfillHealth] = useState<BackfillHealthData>({ available: false, loading: true });
  const [eodSweep, setEodSweep] = useState<EodSweepData>({ available: false, loading: true });
  const [eodHistoryOpen, setEodHistoryOpen] = useState(false);
  const [eodRecalcHealth, setEodRecalcHealth] = useState<EodRecalcHealth>({ available: false, loading: true });
  const [screenerCalib, setScreenerCalib] = useState<ScreenerCalibState>({ loading: true, screeners: [], error: null });
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

  useEffect(() => {
    let cancelled = false;
    const fetchScreenerCalib = () => {
      fetch("/api/screener-calibration")
        .then((r) => r.json())
        .then((data) => {
          if (!cancelled) setScreenerCalib({ loading: false, screeners: data.screeners ?? [], error: null });
        })
        .catch(() => {
          if (!cancelled) setScreenerCalib({ loading: false, screeners: [], error: "Could not load calibration data." });
        });
    };
    fetchScreenerCalib();
    const interval = setInterval(fetchScreenerCalib, 300_000);
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

        <div
          style={{
            background: "#1e2435",
            border: "1px solid #2d3748",
            borderRadius: "10px",
            padding: "20px 24px",
          }}
        >
          <h2 style={{ fontSize: "13px", fontWeight: 700, color: "#94a3b8", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: "16px", marginTop: 0 }}>
            Screener Calibration
          </h2>
          {screenerCalib.loading ? (
            <p style={{ fontSize: "13px", color: "#64748b", margin: 0 }}>Loading…</p>
          ) : screenerCalib.error && screenerCalib.screeners.length === 0 ? (
            <p style={{ fontSize: "13px", color: "#f87171", margin: 0 }}>⚠ {screenerCalib.error}</p>
          ) : screenerCalib.screeners.length === 0 ? (
            <p style={{ fontSize: "13px", color: "#64748b", margin: 0 }}>No screener data available.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              {screenerCalib.screeners.map((s) => (
                <div
                  key={s.key}
                  style={{
                    background: s.ready ? "rgba(20,83,45,0.35)" : "rgba(255,255,255,0.03)",
                    border: s.ready ? "1px solid #166534" : "1px solid #2d3748",
                    borderRadius: "8px",
                    padding: "12px 16px",
                    display: "flex",
                    flexDirection: "column",
                    gap: "8px",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
                    <span style={{ fontSize: "13px", fontWeight: 600, color: s.ready ? "#4ade80" : "#cbd5e1" }}>
                      {s.label}
                    </span>
                    <span
                      style={{
                        fontSize: "13px",
                        fontWeight: 700,
                        color: s.ready ? "#86efac" : "#94a3b8",
                        fontFamily: "monospace",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {s.error && s.count === 0 ? "—" : `${s.count} / ${s.threshold}`}
                    </span>
                  </div>
                  {s.error && s.count === 0 ? (
                    <p style={{ fontSize: "12px", color: "#f87171", margin: 0 }}>⚠ {s.error}</p>
                  ) : (
                    <>
                      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                        <div
                          style={{
                            flex: 1,
                            height: "5px",
                            background: "#0e1117",
                            borderRadius: "3px",
                            overflow: "hidden",
                          }}
                        >
                          <div
                            style={{
                              height: "100%",
                              width: `${Math.min(100, Math.round((s.count / s.threshold) * 100))}%`,
                              background: s.ready ? "#22c55e" : "#3b82f6",
                              borderRadius: "3px",
                              transition: "width 0.4s ease",
                            }}
                          />
                        </div>
                        <span style={{ fontSize: "11px", color: "#64748b", whiteSpace: "nowrap" }}>
                          {s.threshold} trades needed
                        </span>
                      </div>
                      {s.ready && (
                        <p style={{ fontSize: "12px", color: "#86efac", margin: 0, lineHeight: "1.5" }}>
                          Ready — run{" "}
                          <code style={{ fontFamily: "monospace", background: "rgba(0,0,0,0.3)", padding: "1px 5px", borderRadius: "3px", fontSize: "11px", color: "#a3e635" }}>
                            {`python ${s.script}${s.extra_args ? ` ${s.extra_args}` : ""} --apply`}
                          </code>{" "}
                          to update sizing.
                        </p>
                      )}
                      {s.ready && s.last_alerted_utc && (
                        <p style={{ fontSize: "11px", color: "#64748b", margin: 0 }}>
                          {formatLastAlerted(s.last_alerted_utc)}
                        </p>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <GridSearchPanel />

        <ConfigPanel />

        <PdtGatedPanel />

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
