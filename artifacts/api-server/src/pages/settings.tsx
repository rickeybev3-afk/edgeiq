import { useEffect, useRef, useState } from "react";
import { useHashScroll } from "@/hooks/useHashScroll";

type TradingMode = "paper" | "live";

interface TradingModeState {
  mode: TradingMode;
  loading: boolean;
  saving: boolean;
  error: string | null;
  saved: boolean;
}

interface CredentialAlertsState {
  enabled: boolean;
  loading: boolean;
  saving: boolean;
  error: string | null;
  saved: boolean;
}

interface SubscriberCredentialStatus {
  user_id: string;
  credential_alerts_enabled: boolean;
  tg_name?: string;
}

interface SubscribersState {
  subscribers: SubscriberCredentialStatus[];
  loading: boolean;
  error: string | null;
}

interface BackfillRun {
  completed_at: string;
  rows_saved: number;
  no_bars: number;
  errors: number;
}

interface BackfillErrorAlertsState {
  enabled: boolean;
  loading: boolean;
  saving: boolean;
  error: string | null;
  saved: boolean;
}

interface PaperLookbackState {
  days: number;
  source: "env" | "override";
  draft: string;
  loading: boolean;
  saving: boolean;
  error: string | null;
  saved: boolean;
}

interface BackfillHeartbeatWindowState {
  hours: number;
  source: "env" | "override";
  draft: string;
  loading: boolean;
  saving: boolean;
  error: string | null;
  saved: boolean;
}

interface BackfillHealth {
  available: boolean;
  loading: boolean;
  completed_at?: string;
  rows_saved?: number;
  no_bars?: number;
  errors?: number;
  error?: string;
  history?: BackfillRun[];
  history_path?: string;
}

interface EodRecalcRun {
  completed_at: string;
  path: string;
  written: number;
  skipped: number;
  elapsed_s: number;
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
  history?: EodRecalcRun[];
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

export default function Settings() {
  const [state, setState] = useState<TradingModeState>({
    mode: "paper",
    loading: true,
    saving: false,
    error: null,
    saved: false,
  });

  const [credAlerts, setCredAlerts] = useState<CredentialAlertsState>({
    enabled: true,
    loading: true,
    saving: false,
    error: null,
    saved: false,
  });

  const [subscribersState, setSubscribersState] = useState<SubscribersState>({
    subscribers: [],
    loading: true,
    error: null,
  });

  const [backfillErrAlerts, setBackfillErrAlerts] = useState<BackfillErrorAlertsState>({
    enabled: true,
    loading: true,
    saving: false,
    error: null,
    saved: false,
  });

  useEffect(() => {
    let cancelled = false;
    fetch("/api/trading-mode")
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) {
          setState((s) => ({
            ...s,
            mode: data.mode === "live" ? "live" : "paper",
            loading: false,
          }));
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState((s) => ({ ...s, loading: false, error: "Could not load trading mode." }));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/subscribers/credential-alerts")
      .then((r) => {
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) {
          setSubscribersState({
            subscribers: Array.isArray(data.subscribers) ? data.subscribers : [],
            loading: false,
            error: null,
          });
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Could not load subscriber list.";
          setSubscribersState({ subscribers: [], loading: false, error: msg });
        }
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/credential-alerts")
      .then((r) => {
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) {
          setCredAlerts((s) => ({
            ...s,
            enabled: data.enabled !== false,
            loading: false,
          }));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Could not load credential alert preference.";
          setCredAlerts((s) => ({
            ...s,
            loading: false,
            error: msg,
          }));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/backfill-error-alerts")
      .then((r) => {
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) {
          setBackfillErrAlerts((s) => ({
            ...s,
            enabled: data.enabled !== false,
            loading: false,
          }));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Could not load backfill error alert preference.";
          setBackfillErrAlerts((s) => ({
            ...s,
            loading: false,
            error: msg,
          }));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const [paperLookback, setPaperLookback] = useState<PaperLookbackState>({
    days: 60,
    source: "env",
    draft: "60",
    loading: true,
    saving: false,
    error: null,
    saved: false,
  });

  const [heartbeatWindow, setHeartbeatWindow] = useState<BackfillHeartbeatWindowState>({
    hours: 25,
    source: "env",
    draft: "25",
    loading: true,
    saving: false,
    error: null,
    saved: false,
  });

  useEffect(() => {
    let cancelled = false;
    fetch("/api/backfill-heartbeat-window")
      .then((r) => {
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) {
          setHeartbeatWindow((s) => ({
            ...s,
            hours: data.hours,
            source: data.source === "override" ? "override" : "env",
            draft: String(data.hours),
            loading: false,
          }));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Could not load heartbeat window.";
          setHeartbeatWindow((s) => ({ ...s, loading: false, error: msg }));
        }
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/paper-lookback")
      .then((r) => {
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) {
          setPaperLookback((s) => ({
            ...s,
            days: data.days,
            source: data.source === "override" ? "override" : "env",
            draft: String(data.days),
            loading: false,
          }));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Could not load look-back window.";
          setPaperLookback((s) => ({ ...s, loading: false, error: msg }));
        }
      });
    return () => { cancelled = true; };
  }, []);

  const [backfillHealth, setBackfillHealth] = useState<BackfillHealth>({ available: false, loading: true });

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      fetch("/api/backfill-health")
        .then((r) => r.json())
        .then((data) => {
          if (!cancelled) {
            setBackfillHealth({ loading: false, ...data });
          }
        })
        .catch(() => {
          if (!cancelled) {
            setBackfillHealth({ available: false, loading: false, error: "Could not reach server." });
          }
        });
    };
    poll();
    const id = setInterval(poll, 60_000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const [eodRecalcHealth, setEodRecalcHealth] = useState<EodRecalcHealth>({ available: false, loading: true });

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      fetch("/api/eod-recalc-health")
        .then((r) => r.json())
        .then((data) => {
          if (!cancelled) {
            setEodRecalcHealth({ loading: false, ...data });
          }
        })
        .catch(() => {
          if (!cancelled) {
            setEodRecalcHealth({ available: false, loading: false, error: "Could not reach server." });
          }
        });
    };
    poll();
    const id = setInterval(poll, 60_000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  useHashScroll(
    ["#trading-mode", "#credential-alerts", "#subscriber-opt-out", "#backfill-health", "#paper-lookback", "#backfill-heartbeat-window", "#eod-recalc-health"],
    [state.loading, credAlerts.loading, subscribersState.loading, backfillHealth.loading, backfillErrAlerts.loading, paperLookback.loading, heartbeatWindow.loading, eodRecalcHealth.loading]
  );

  async function handleChange(newMode: TradingMode) {
    setState((s) => ({ ...s, saving: true, error: null, saved: false }));
    try {
      const res = await fetch("/api/trading-mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: newMode }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? `Server returned ${res.status}`);
      }
      const data = await res.json();
      setState((s) => ({
        ...s,
        mode: data.mode === "live" ? "live" : "paper",
        saving: false,
        saved: true,
      }));
      setTimeout(() => setState((s) => ({ ...s, saved: false })), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setState((s) => ({ ...s, saving: false, error: msg }));
    }
  }

  async function handleCredAlertsToggle(newEnabled: boolean) {
    setCredAlerts((s) => ({ ...s, saving: true, error: null, saved: false }));
    try {
      const res = await fetch("/api/credential-alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: newEnabled }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? `Server returned ${res.status}`);
      }
      const data = await res.json();
      setCredAlerts((s) => ({
        ...s,
        enabled: data.enabled !== false,
        saving: false,
        saved: true,
      }));
      setTimeout(() => setCredAlerts((s) => ({ ...s, saved: false })), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setCredAlerts((s) => ({ ...s, saving: false, error: msg }));
    }
  }

  async function handleBackfillErrAlertsToggle(newEnabled: boolean) {
    setBackfillErrAlerts((s) => ({ ...s, saving: true, error: null, saved: false }));
    try {
      const res = await fetch("/api/backfill-error-alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: newEnabled }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? `Server returned ${res.status}`);
      }
      const data = await res.json();
      setBackfillErrAlerts((s) => ({
        ...s,
        enabled: data.enabled !== false,
        saving: false,
        saved: true,
      }));
      setTimeout(() => setBackfillErrAlerts((s) => ({ ...s, saved: false })), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setBackfillErrAlerts((s) => ({ ...s, saving: false, error: msg }));
    }
  }

  async function handleHeartbeatWindowSave() {
    const parsed = parseFloat(heartbeatWindow.draft);
    if (isNaN(parsed) || parsed < 1 || parsed > 8760) {
      setHeartbeatWindow((s) => ({ ...s, error: "Enter a number between 1 and 8760." }));
      return;
    }
    setHeartbeatWindow((s) => ({ ...s, saving: true, error: null, saved: false }));
    try {
      const res = await fetch("/api/backfill-heartbeat-window", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hours: parsed }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? `Server returned ${res.status}`);
      }
      const data = await res.json();
      setHeartbeatWindow((s) => ({
        ...s,
        hours: data.hours,
        source: data.source === "override" ? "override" : "env",
        draft: String(data.hours),
        saving: false,
        saved: true,
      }));
      setTimeout(() => setHeartbeatWindow((s) => ({ ...s, saved: false })), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setHeartbeatWindow((s) => ({ ...s, saving: false, error: msg }));
    }
  }

  async function handleHeartbeatWindowReset() {
    setHeartbeatWindow((s) => ({ ...s, saving: true, error: null, saved: false }));
    try {
      const res = await fetch("/api/backfill-heartbeat-window", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hours: null }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? `Server returned ${res.status}`);
      }
      const data = await res.json();
      setHeartbeatWindow((s) => ({
        ...s,
        hours: data.hours,
        source: "env",
        draft: String(data.hours),
        saving: false,
        saved: true,
      }));
      setTimeout(() => setHeartbeatWindow((s) => ({ ...s, saved: false })), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setHeartbeatWindow((s) => ({ ...s, saving: false, error: msg }));
    }
  }

  async function handlePaperLookbackSave() {
    const parsed = parseInt(paperLookback.draft, 10);
    if (isNaN(parsed) || parsed < 1 || parsed > 3650) {
      setPaperLookback((s) => ({ ...s, error: "Enter a whole number between 1 and 3650." }));
      return;
    }
    setPaperLookback((s) => ({ ...s, saving: true, error: null, saved: false }));
    try {
      const res = await fetch("/api/paper-lookback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: parsed }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? `Server returned ${res.status}`);
      }
      const data = await res.json();
      setPaperLookback((s) => ({
        ...s,
        days: data.days,
        source: data.source === "override" ? "override" : "env",
        draft: String(data.days),
        saving: false,
        saved: true,
      }));
      setTimeout(() => setPaperLookback((s) => ({ ...s, saved: false })), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setPaperLookback((s) => ({ ...s, saving: false, error: msg }));
    }
  }

  async function handlePaperLookbackReset() {
    setPaperLookback((s) => ({ ...s, saving: true, error: null, saved: false }));
    try {
      const res = await fetch("/api/paper-lookback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ days: null }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? `Server returned ${res.status}`);
      }
      const data = await res.json();
      setPaperLookback((s) => ({
        ...s,
        days: data.days,
        source: "env",
        draft: String(data.days),
        saving: false,
        saved: true,
      }));
      setTimeout(() => setPaperLookback((s) => ({ ...s, saved: false })), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setPaperLookback((s) => ({ ...s, saving: false, error: msg }));
    }
  }

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
      <div style={{ maxWidth: "640px", margin: "0 auto" }}>
        <h1 style={{ fontSize: "22px", fontWeight: 700, marginBottom: "32px", color: "#f1f5f9" }}>
          Settings
        </h1>

        <section
          id="trading-mode"
          style={{
            background: "#1e2435",
            border: "1px solid #2d3748",
            borderRadius: "10px",
            padding: "24px",
            scrollMarginTop: "24px",
            marginBottom: "20px",
          }}
        >
          <h2 style={{ fontSize: "15px", fontWeight: 700, color: "#cbd5e1", marginBottom: "6px" }}>
            Trading Mode
          </h2>
          <p style={{ fontSize: "13px", color: "#94a3b8", marginBottom: "20px", lineHeight: "1.6" }}>
            Select whether the system should use Paper (simulated) or Live (real money) trading.
            This must match the type of Alpaca API keys you have configured.
          </p>

          {state.loading ? (
            <p style={{ fontSize: "13px", color: "#64748b" }}>Loading…</p>
          ) : (
            <div style={{ display: "flex", gap: "12px" }}>
              <ModeButton
                label="Paper"
                description="Simulated trading — no real money"
                active={state.mode === "paper"}
                disabled={state.saving}
                onClick={() => handleChange("paper")}
                color="#22c55e"
              />
              <ModeButton
                label="Live"
                description="Real brokerage account — real money"
                active={state.mode === "live"}
                disabled={state.saving}
                onClick={() => handleChange("live")}
                color="#ef4444"
              />
            </div>
          )}

          {state.saving && (
            <p style={{ fontSize: "13px", color: "#94a3b8", marginTop: "14px" }}>Saving…</p>
          )}
          {state.saved && (
            <p style={{ fontSize: "13px", color: "#4ade80", marginTop: "14px" }}>
              ✓ Trading mode updated successfully.
            </p>
          )}
          {state.error && (
            <p style={{ fontSize: "13px", color: "#f87171", marginTop: "14px" }}>
              ⚠ {state.error}
            </p>
          )}
        </section>

        <section
          id="credential-alerts"
          style={{
            background: "#1e2435",
            border: "1px solid #2d3748",
            borderRadius: "10px",
            padding: "24px",
            scrollMarginTop: "24px",
            marginBottom: "20px",
          }}
        >
          <h2 style={{ fontSize: "15px", fontWeight: 700, color: "#cbd5e1", marginBottom: "6px" }}>
            Subscriber Preferences
          </h2>
          <p style={{ fontSize: "13px", color: "#94a3b8", marginBottom: "20px", lineHeight: "1.6" }}>
            Control which alerts subscribers receive via Telegram.
          </p>

          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "16px",
              background: "rgba(255,255,255,0.03)",
              border: "1px solid #2d3748",
              borderRadius: "8px",
            }}
          >
            <div style={{ flex: 1, marginRight: "16px" }}>
              <div style={{ fontSize: "14px", fontWeight: 600, color: "#e2e8f0", marginBottom: "4px" }}>
                Credential failure alerts
              </div>
              <div style={{ fontSize: "12px", color: "#64748b", lineHeight: "1.5" }}>
                Send a Telegram message when broker credential errors are detected or resolved.
              </div>
            </div>

            {credAlerts.loading ? (
              <span style={{ fontSize: "12px", color: "#475569" }}>Loading…</span>
            ) : (
              <ToggleSwitch
                enabled={credAlerts.enabled}
                disabled={credAlerts.saving}
                onChange={handleCredAlertsToggle}
              />
            )}
          </div>

          {credAlerts.saving && (
            <p style={{ fontSize: "13px", color: "#94a3b8", marginTop: "14px" }}>Saving…</p>
          )}
          {credAlerts.saved && (
            <p style={{ fontSize: "13px", color: "#4ade80", marginTop: "14px" }}>
              ✓ Preference saved.
            </p>
          )}
          {credAlerts.error && (
            <p style={{ fontSize: "13px", color: "#f87171", marginTop: "14px" }}>
              ⚠ {credAlerts.error}
            </p>
          )}

          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "16px",
              background: "rgba(255,255,255,0.03)",
              border: "1px solid #2d3748",
              borderRadius: "8px",
              marginTop: "12px",
            }}
          >
            <div style={{ flex: 1, marginRight: "16px" }}>
              <div style={{ fontSize: "14px", fontWeight: 600, color: "#e2e8f0", marginBottom: "4px" }}>
                Backfill error alerts
              </div>
              <div style={{ fontSize: "12px", color: "#64748b", lineHeight: "1.5" }}>
                Send a Telegram message when a context-levels backfill run completes with errors.
              </div>
            </div>

            {backfillErrAlerts.loading ? (
              <span style={{ fontSize: "12px", color: "#475569" }}>Loading…</span>
            ) : (
              <ToggleSwitch
                enabled={backfillErrAlerts.enabled}
                disabled={backfillErrAlerts.saving}
                onChange={handleBackfillErrAlertsToggle}
              />
            )}
          </div>

          {backfillErrAlerts.saving && (
            <p style={{ fontSize: "13px", color: "#94a3b8", marginTop: "14px" }}>Saving…</p>
          )}
          {backfillErrAlerts.saved && (
            <p style={{ fontSize: "13px", color: "#4ade80", marginTop: "14px" }}>
              ✓ Preference saved.
            </p>
          )}
          {backfillErrAlerts.error && (
            <p style={{ fontSize: "13px", color: "#f87171", marginTop: "14px" }}>
              ⚠ {backfillErrAlerts.error}
            </p>
          )}
        </section>

        <section
          id="subscriber-opt-out"
          style={{
            background: "#1e2435",
            border: "1px solid #2d3748",
            borderRadius: "10px",
            padding: "24px",
            scrollMarginTop: "24px",
            marginBottom: "20px",
          }}
        >
          <h2 style={{ fontSize: "15px", fontWeight: 700, color: "#cbd5e1", marginBottom: "6px" }}>
            Per-Subscriber Credential Alert Status
          </h2>
          <p style={{ fontSize: "13px", color: "#94a3b8", marginBottom: "20px", lineHeight: "1.6" }}>
            Shows whether each subscriber will receive credential failure alerts.
            Subscribers can opt out via the Telegram bot's /settings command.
          </p>

          {subscribersState.loading ? (
            <p style={{ fontSize: "13px", color: "#64748b" }}>Loading…</p>
          ) : subscribersState.error ? (
            <p style={{ fontSize: "13px", color: "#f87171" }}>⚠ {subscribersState.error}</p>
          ) : subscribersState.subscribers.length === 0 ? (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "10px",
                padding: "14px 16px",
                background: "rgba(255,255,255,0.03)",
                border: "1px solid #2d3748",
                borderRadius: "8px",
                color: "#64748b",
                fontSize: "13px",
              }}
            >
              <span style={{ fontSize: "16px" }}>—</span>
              No subscribers found. Subscribers appear here once they have connected via Telegram.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
              {subscribersState.subscribers.map((sub) => (
                <SubscriberRow
                  key={sub.user_id}
                  subscriber={sub}
                  onToggle={(userId, enabled) => {
                    setSubscribersState((s) => ({
                      ...s,
                      subscribers: s.subscribers.map((sr) =>
                        sr.user_id === userId
                          ? { ...sr, credential_alerts_enabled: enabled }
                          : sr
                      ),
                    }));
                  }}
                />
              ))}
            </div>
          )}
        </section>

        <section
          id="backfill-health"
          style={{
            background: "#1e2435",
            border: "1px solid #2d3748",
            borderRadius: "10px",
            padding: "24px",
            scrollMarginTop: "24px",
          }}
        >
          <h2 style={{ fontSize: "15px", fontWeight: 700, color: "#cbd5e1", marginBottom: "6px" }}>
            Backfill Health
          </h2>
          <p style={{ fontSize: "13px", color: "#94a3b8", marginBottom: "20px", lineHeight: "1.6" }}>
            Summary of the most recent context-levels backfill run. Refreshes every minute.
          </p>

          {backfillHealth.loading ? (
            <p style={{ fontSize: "13px", color: "#64748b" }}>Loading…</p>
          ) : !backfillHealth.available ? (
            <div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  padding: "14px 16px",
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid #2d3748",
                  borderRadius: "8px",
                  color: "#64748b",
                  fontSize: "13px",
                }}
              >
                <span style={{ fontSize: "16px" }}>—</span>
                No backfill run recorded yet. Stats will appear here after the next run.
              </div>
              {backfillHealth.history_path && (
                <p style={{ fontSize: "11px", color: "#475569", fontFamily: "monospace", marginTop: "10px", marginBottom: 0 }}>
                  History file: {backfillHealth.history_path}
                </p>
              )}
            </div>
          ) : (
            <div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: "12px",
                  marginBottom: "14px",
                }}
              >
                <BackfillStat
                  label="Rows saved"
                  value={backfillHealth.rows_saved ?? 0}
                  color="#4ade80"
                  prev={backfillHealth.history && backfillHealth.history.length > 1 ? backfillHealth.history[1].rows_saved : undefined}
                  higherIsBetter={true}
                />
                <BackfillStat
                  label="No-bars"
                  value={backfillHealth.no_bars ?? 0}
                  color="#fbbf24"
                  warn={(backfillHealth.no_bars ?? 0) > 0}
                  prev={backfillHealth.history && backfillHealth.history.length > 1 ? backfillHealth.history[1].no_bars : undefined}
                  higherIsBetter={false}
                />
                <BackfillStat
                  label="Errors"
                  value={backfillHealth.errors ?? 0}
                  color={(backfillHealth.errors ?? 0) > 0 ? "#f87171" : "#4ade80"}
                  warn={(backfillHealth.errors ?? 0) > 0}
                  prev={backfillHealth.history && backfillHealth.history.length > 1 ? backfillHealth.history[1].errors : undefined}
                  higherIsBetter={false}
                />
              </div>
              {backfillHealth.completed_at && (
                <p style={{ fontSize: "11px", color: "#475569", fontFamily: "monospace", margin: 0 }}>
                  Completed {formatRelativeTime(backfillHealth.completed_at)} &nbsp;·&nbsp;{" "}
                  {new Date(backfillHealth.completed_at).toLocaleString()}
                </p>
              )}
              {backfillHealth.history_path && (
                <p style={{ fontSize: "11px", color: "#475569", fontFamily: "monospace", marginTop: "6px", marginBottom: 0 }}>
                  History file: {backfillHealth.history_path}
                </p>
              )}
              {backfillHealth.history && backfillHealth.history.length > 1 && (
                <div style={{ marginTop: "20px" }}>
                  <p style={{ fontSize: "12px", fontWeight: 600, color: "#94a3b8", marginBottom: "8px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                    Run History
                  </p>
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px", fontFamily: "monospace" }}>
                      <thead>
                        <tr style={{ borderBottom: "1px solid #2d3748" }}>
                          <th style={{ textAlign: "left", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Completed</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Rows saved</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>No-bars</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Errors</th>
                        </tr>
                      </thead>
                      <tbody>
                        {backfillHealth.history.map((run, i) => (
                          <tr
                            key={run.completed_at}
                            style={{
                              borderBottom: "1px solid rgba(45,55,72,0.5)",
                              background: i === 0 ? "rgba(255,255,255,0.03)" : "transparent",
                            }}
                          >
                            <td style={{ padding: "6px 10px", color: i === 0 ? "#cbd5e1" : "#64748b" }}>
                              {formatRelativeTime(run.completed_at)}
                              {i === 0 && (
                                <span style={{ marginLeft: "6px", fontSize: "10px", color: "#4ade80", fontFamily: "sans-serif" }}>latest</span>
                              )}
                            </td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: "#4ade80" }}>
                              {run.rows_saved.toLocaleString()}
                            </td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: run.no_bars > 0 ? "#fbbf24" : "#64748b" }}>
                              {run.no_bars.toLocaleString()}
                            </td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: run.errors > 0 ? "#f87171" : "#4ade80" }}>
                              {run.errors.toLocaleString()}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </section>

        <section
          id="paper-lookback"
          style={{
            background: "#1e2435",
            border: "1px solid #2d3748",
            borderRadius: "10px",
            padding: "24px",
            scrollMarginTop: "24px",
            marginTop: "20px",
          }}
        >
          <h2 style={{ fontSize: "15px", fontWeight: 700, color: "#cbd5e1", marginBottom: "6px" }}>
            Paper Trades Look-back Window
          </h2>
          <p style={{ fontSize: "13px", color: "#94a3b8", marginBottom: "20px", lineHeight: "1.6" }}>
            How many calendar days the nightly EOD sweep looks back to fill in missing
            paper-trade close prices. Corresponds to <code style={{ fontFamily: "monospace", color: "#7dd3fc" }}>PAPER_CLOSE_LOOKBACK_DAYS</code> on the server.
          </p>

          {paperLookback.loading ? (
            <p style={{ fontSize: "13px", color: "#64748b" }}>Loading…</p>
          ) : (
            <div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  marginBottom: "14px",
                }}
              >
                <input
                  type="number"
                  min={1}
                  max={3650}
                  value={paperLookback.draft}
                  disabled={paperLookback.saving}
                  onChange={(e) =>
                    setPaperLookback((s) => ({ ...s, draft: e.target.value, error: null }))
                  }
                  onKeyDown={(e) => e.key === "Enter" && handlePaperLookbackSave()}
                  style={{
                    width: "100px",
                    padding: "8px 12px",
                    background: "#0e1117",
                    border: "1px solid #3d4f6b",
                    borderRadius: "6px",
                    color: "#f1f5f9",
                    fontSize: "15px",
                    fontFamily: "monospace",
                    outline: "none",
                  }}
                />
                <span style={{ fontSize: "13px", color: "#64748b" }}>days</span>
                <button
                  onClick={handlePaperLookbackSave}
                  disabled={paperLookback.saving || paperLookback.draft === String(paperLookback.days)}
                  style={{
                    padding: "8px 16px",
                    background: "#2563eb",
                    border: "none",
                    borderRadius: "6px",
                    color: "#fff",
                    fontSize: "13px",
                    fontWeight: 600,
                    cursor: paperLookback.saving || paperLookback.draft === String(paperLookback.days) ? "not-allowed" : "pointer",
                    opacity: paperLookback.saving || paperLookback.draft === String(paperLookback.days) ? 0.5 : 1,
                  }}
                >
                  Save
                </button>
                {paperLookback.source === "override" && (
                  <button
                    onClick={handlePaperLookbackReset}
                    disabled={paperLookback.saving}
                    style={{
                      padding: "8px 14px",
                      background: "transparent",
                      border: "1px solid #475569",
                      borderRadius: "6px",
                      color: "#94a3b8",
                      fontSize: "13px",
                      cursor: paperLookback.saving ? "not-allowed" : "pointer",
                      opacity: paperLookback.saving ? 0.5 : 1,
                    }}
                  >
                    Reset to env default
                  </button>
                )}
              </div>
              <p style={{ fontSize: "12px", color: "#475569", margin: 0 }}>
                {paperLookback.source === "override"
                  ? "Using a dashboard override. The server env var is ignored until reset."
                  : `Reading from server environment (PAPER_CLOSE_LOOKBACK_DAYS=${paperLookback.days}).`}
              </p>
            </div>
          )}

          {paperLookback.saving && (
            <p style={{ fontSize: "13px", color: "#94a3b8", marginTop: "14px" }}>Saving…</p>
          )}
          {paperLookback.saved && (
            <p style={{ fontSize: "13px", color: "#4ade80", marginTop: "14px" }}>
              ✓ Look-back window updated.
            </p>
          )}
          {paperLookback.error && (
            <p style={{ fontSize: "13px", color: "#f87171", marginTop: "14px" }}>
              ⚠ {paperLookback.error}
            </p>
          )}
        </section>

        <section
          id="backfill-heartbeat-window"
          style={{
            background: "#1e2435",
            border: "1px solid #2d3748",
            borderRadius: "10px",
            padding: "24px",
            scrollMarginTop: "24px",
            marginTop: "20px",
          }}
        >
          <h2 style={{ fontSize: "15px", fontWeight: 700, color: "#cbd5e1", marginBottom: "6px" }}>
            Backfill Alert Window
          </h2>
          <p style={{ fontSize: "13px", color: "#94a3b8", marginBottom: "20px", lineHeight: "1.6" }}>
            How many hours can pass since the last successful backfill before a Telegram alert is sent.
            Corresponds to{" "}
            <code style={{ fontFamily: "monospace", color: "#7dd3fc" }}>BACKFILL_HEARTBEAT_HOURS</code>{" "}
            on the server. The default is 25 h (24-hour cycle plus a 1-hour grace period).
          </p>

          {heartbeatWindow.loading ? (
            <p style={{ fontSize: "13px", color: "#64748b" }}>Loading…</p>
          ) : (
            <div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  marginBottom: "14px",
                }}
              >
                <input
                  type="number"
                  min={1}
                  max={8760}
                  step={0.5}
                  value={heartbeatWindow.draft}
                  disabled={heartbeatWindow.saving}
                  onChange={(e) =>
                    setHeartbeatWindow((s) => ({ ...s, draft: e.target.value, error: null }))
                  }
                  onKeyDown={(e) => e.key === "Enter" && handleHeartbeatWindowSave()}
                  style={{
                    width: "100px",
                    padding: "8px 12px",
                    background: "#0e1117",
                    border: "1px solid #3d4f6b",
                    borderRadius: "6px",
                    color: "#f1f5f9",
                    fontSize: "15px",
                    fontFamily: "monospace",
                    outline: "none",
                  }}
                />
                <span style={{ fontSize: "13px", color: "#64748b" }}>hours</span>
                <button
                  onClick={handleHeartbeatWindowSave}
                  disabled={heartbeatWindow.saving || heartbeatWindow.draft === String(heartbeatWindow.hours)}
                  style={{
                    padding: "8px 16px",
                    background: "#2563eb",
                    border: "none",
                    borderRadius: "6px",
                    color: "#fff",
                    fontSize: "13px",
                    fontWeight: 600,
                    cursor: heartbeatWindow.saving || heartbeatWindow.draft === String(heartbeatWindow.hours) ? "not-allowed" : "pointer",
                    opacity: heartbeatWindow.saving || heartbeatWindow.draft === String(heartbeatWindow.hours) ? 0.5 : 1,
                  }}
                >
                  Save
                </button>
                {heartbeatWindow.source === "override" && (
                  <button
                    onClick={handleHeartbeatWindowReset}
                    disabled={heartbeatWindow.saving}
                    style={{
                      padding: "8px 14px",
                      background: "transparent",
                      border: "1px solid #475569",
                      borderRadius: "6px",
                      color: "#94a3b8",
                      fontSize: "13px",
                      cursor: heartbeatWindow.saving ? "not-allowed" : "pointer",
                      opacity: heartbeatWindow.saving ? 0.5 : 1,
                    }}
                  >
                    Reset to env default
                  </button>
                )}
              </div>
              <p style={{ fontSize: "12px", color: "#475569", margin: 0 }}>
                {heartbeatWindow.source === "override"
                  ? "Using a dashboard override. The server env var is ignored until reset."
                  : `Reading from server environment (BACKFILL_HEARTBEAT_HOURS=${heartbeatWindow.hours}).`}
              </p>
            </div>
          )}

          {heartbeatWindow.saving && (
            <p style={{ fontSize: "13px", color: "#94a3b8", marginTop: "14px" }}>Saving…</p>
          )}
          {heartbeatWindow.saved && (
            <p style={{ fontSize: "13px", color: "#4ade80", marginTop: "14px" }}>
              ✓ Alert window updated.
            </p>
          )}
          {heartbeatWindow.error && (
            <p style={{ fontSize: "13px", color: "#f87171", marginTop: "14px" }}>
              ⚠ {heartbeatWindow.error}
            </p>
          )}
        </section>

        <section
          id="eod-recalc-health"
          style={{
            background: "#1e2435",
            border: "1px solid #2d3748",
            borderRadius: "10px",
            padding: "24px",
            scrollMarginTop: "24px",
            marginTop: "20px",
          }}
        >
          <h2 style={{ fontSize: "15px", fontWeight: 700, color: "#cbd5e1", marginBottom: "6px" }}>
            EOD P&amp;L Recalc
          </h2>
          <p style={{ fontSize: "13px", color: "#94a3b8", marginBottom: "20px", lineHeight: "1.6" }}>
            Timing and row counts from each nightly end-of-day P&amp;L recalculation run. Refreshes every minute.
          </p>

          {eodRecalcHealth.loading ? (
            <p style={{ fontSize: "13px", color: "#64748b" }}>Loading…</p>
          ) : !eodRecalcHealth.available ? (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "10px",
                padding: "14px 16px",
                background: "rgba(255,255,255,0.03)",
                border: "1px solid #2d3748",
                borderRadius: "8px",
                color: "#64748b",
                fontSize: "13px",
              }}
            >
              <span style={{ fontSize: "16px" }}>—</span>
              No recalc run recorded yet. Stats will appear here after the next nightly run.
              {eodRecalcHealth.error && (
                <span style={{ color: "#f87171", marginLeft: "8px" }}>({eodRecalcHealth.error})</span>
              )}
            </div>
          ) : (
            <div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(3, 1fr)",
                  gap: "12px",
                  marginBottom: "14px",
                }}
              >
                <BackfillStat
                  label="Rows updated"
                  value={eodRecalcHealth.written ?? 0}
                  color="#4ade80"
                  prev={eodRecalcHealth.history && eodRecalcHealth.history.length > 1 ? eodRecalcHealth.history[1].written : undefined}
                  higherIsBetter={true}
                />
                <BackfillStat
                  label="Rows skipped"
                  value={eodRecalcHealth.skipped ?? 0}
                  color="#94a3b8"
                  prev={eodRecalcHealth.history && eodRecalcHealth.history.length > 1 ? eodRecalcHealth.history[1].skipped : undefined}
                  higherIsBetter={false}
                />
                <div
                  style={{
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid #2d3748",
                    borderRadius: "8px",
                    padding: "14px 16px",
                    textAlign: "center",
                  }}
                >
                  <div style={{ fontSize: "24px", fontWeight: 700, color: "#818cf8", fontVariantNumeric: "tabular-nums" }}>
                    {eodRecalcHealth.elapsed_s != null ? `${eodRecalcHealth.elapsed_s.toFixed(2)}s` : "—"}
                  </div>
                  <div style={{ fontSize: "11px", color: "#64748b", marginTop: "4px", letterSpacing: "0.03em" }}>Elapsed</div>
                </div>
              </div>
              {eodRecalcHealth.completed_at && (
                <p style={{ fontSize: "11px", color: "#475569", fontFamily: "monospace", margin: 0 }}>
                  Completed {formatRelativeTime(eodRecalcHealth.completed_at)} &nbsp;·&nbsp;{" "}
                  {new Date(eodRecalcHealth.completed_at).toLocaleString()} &nbsp;·&nbsp;{" "}
                  path: <span style={{ color: "#94a3b8" }}>{eodRecalcHealth.path ?? "—"}</span>
                </p>
              )}
              {eodRecalcHealth.history && eodRecalcHealth.history.length > 1 && (
                <div style={{ marginTop: "20px" }}>
                  <p style={{ fontSize: "12px", fontWeight: 600, color: "#94a3b8", marginBottom: "8px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                    Run History
                  </p>
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px", fontFamily: "monospace" }}>
                      <thead>
                        <tr style={{ borderBottom: "1px solid #2d3748" }}>
                          <th style={{ textAlign: "left", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Completed</th>
                          <th style={{ textAlign: "left", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Path</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Updated</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Skipped</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Elapsed</th>
                        </tr>
                      </thead>
                      <tbody>
                        {eodRecalcHealth.history.map((run, i) => (
                          <tr
                            key={run.completed_at + i}
                            style={{
                              borderBottom: "1px solid rgba(45,55,72,0.5)",
                              background: i === 0 ? "rgba(255,255,255,0.03)" : "transparent",
                            }}
                          >
                            <td style={{ padding: "6px 10px", color: i === 0 ? "#cbd5e1" : "#64748b" }}>
                              {formatRelativeTime(run.completed_at)}
                              {i === 0 && (
                                <span style={{ marginLeft: "6px", fontSize: "10px", color: "#4ade80", fontFamily: "sans-serif" }}>latest</span>
                              )}
                            </td>
                            <td style={{ padding: "6px 10px", color: "#94a3b8" }}>{run.path}</td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: "#4ade80" }}>
                              {run.written.toLocaleString()}
                            </td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: "#64748b" }}>
                              {run.skipped.toLocaleString()}
                            </td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: "#818cf8" }}>
                              {run.elapsed_s.toFixed(2)}s
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function ToggleSwitch({
  enabled,
  disabled,
  onChange,
}: {
  enabled: boolean;
  disabled: boolean;
  onChange: (value: boolean) => void;
}) {
  const activeColor = "#22c55e";
  const inactiveColor = "#475569";
  return (
    <button
      role="switch"
      aria-checked={enabled}
      disabled={disabled}
      onClick={() => onChange(!enabled)}
      style={{
        position: "relative",
        display: "inline-flex",
        alignItems: "center",
        width: "44px",
        height: "24px",
        borderRadius: "9999px",
        border: "none",
        background: enabled ? activeColor : inactiveColor,
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.6 : 1,
        transition: "background 0.2s",
        flexShrink: 0,
        padding: 0,
      }}
    >
      <span
        style={{
          position: "absolute",
          left: enabled ? "22px" : "2px",
          width: "20px",
          height: "20px",
          borderRadius: "50%",
          background: "#fff",
          transition: "left 0.2s",
          boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
        }}
      />
    </button>
  );
}

function ModeButton({
  label,
  description,
  active,
  disabled,
  onClick,
  color,
}: {
  label: string;
  description: string;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  color: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled || active}
      aria-pressed={active}
      style={{
        flex: 1,
        background: active ? "rgba(255,255,255,0.06)" : "transparent",
        border: active ? `2px solid ${color}` : "2px solid #334155",
        borderRadius: "8px",
        padding: "16px",
        cursor: active || disabled ? "default" : "pointer",
        textAlign: "left",
        transition: "border-color 0.15s, background 0.15s",
        opacity: disabled && !active ? 0.6 : 1,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          marginBottom: "4px",
        }}
      >
        <span
          style={{
            width: "10px",
            height: "10px",
            borderRadius: "50%",
            background: active ? color : "#475569",
            flexShrink: 0,
          }}
        />
        <span style={{ fontWeight: 700, fontSize: "14px", color: active ? color : "#94a3b8" }}>
          {label}
        </span>
        {active && (
          <span
            style={{
              marginLeft: "auto",
              fontSize: "11px",
              background: color + "22",
              color: color,
              border: `1px solid ${color}44`,
              borderRadius: "4px",
              padding: "1px 6px",
              fontWeight: 600,
            }}
          >
            Active
          </span>
        )}
      </div>
      <p style={{ fontSize: "12px", color: "#64748b", margin: 0, paddingLeft: "18px" }}>
        {description}
      </p>
    </button>
  );
}

function SubscriberRow({
  subscriber,
  onToggle,
}: {
  subscriber: SubscriberCredentialStatus;
  onToggle: (userId: string, enabled: boolean) => void;
}) {
  const [enabled, setEnabled] = useState(subscriber.credential_alerts_enabled);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const displayName = subscriber.tg_name || subscriber.user_id;
  const hasName = Boolean(subscriber.tg_name);

  useEffect(() => {
    if (!saving) {
      setEnabled(subscriber.credential_alerts_enabled);
    }
  }, [subscriber.credential_alerts_enabled]);

  useEffect(() => {
    if (!saving) {
      setEnabled(subscriber.credential_alerts_enabled);
    }
  }, [subscriber.credential_alerts_enabled]);

  async function handleToggle() {
    const next = !enabled;
    setEnabled(next);
    setError(null);
    setSaved(false);
    if (savedTimerRef.current !== null) {
      clearTimeout(savedTimerRef.current);
      savedTimerRef.current = null;
    }
    setSaving(true);
    try {
      const res = await fetch("/api/subscribers/credential-alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: subscriber.user_id, enabled: next }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `Server error ${res.status}`);
      }
      onToggle(subscriber.user_id, next);
      setSaved(true);
      savedTimerRef.current = setTimeout(() => {
        setSaved(false);
        savedTimerRef.current = null;
      }, 2000);
    } catch (err: unknown) {
      setEnabled(!next);
      setError(err instanceof Error ? err.message : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 16px",
        background: "rgba(255,255,255,0.03)",
        border: `1px solid ${enabled ? "#2d3748" : "#7f1d1d"}`,
        borderRadius: "8px",
        gap: "12px",
      }}
    >
      <div style={{ flex: 1, overflow: "hidden" }}>
        <div
          style={{
            fontSize: "13px",
            color: "#cbd5e1",
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
            fontFamily: hasName ? "inherit" : "monospace",
          }}
        >
          {displayName}
        </div>
        {hasName && (
          <div
            style={{
              fontSize: "11px",
              fontFamily: "monospace",
              color: "#475569",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              marginTop: "2px",
            }}
          >
            {subscriber.user_id}
          </div>
        )}
        {error && (
          <div style={{ fontSize: "11px", color: "#f87171", marginTop: "4px" }}>
            ⚠ {error}
          </div>
        )}
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "8px",
          flexShrink: 0,
        }}
      >
        {saved && (
          <span
            style={{
              fontSize: "11px",
              color: "#4ade80",
              fontWeight: 600,
            }}
          >
            ✓ Saved
          </span>
        )}
        <span
          style={{
            fontSize: "11px",
            color: enabled ? "#4ade80" : "#f87171",
            fontWeight: 600,
          }}
        >
          {enabled ? "Enabled" : "Opted out"}
        </span>
        <ToggleSwitch
          enabled={enabled}
          disabled={saving}
          onChange={() => handleToggle()}
        />
      </div>
    </div>
  );
}

function BackfillStat({
  label,
  value,
  color,
  warn = false,
  prev,
  higherIsBetter = true,
}: {
  label: string;
  value: number;
  color: string;
  warn?: boolean;
  prev?: number;
  higherIsBetter?: boolean;
}) {
  let trendArrow: string | null = null;
  let trendColor = "#64748b";

  if (prev !== undefined) {
    if (value > prev) {
      trendArrow = "↑";
      trendColor = higherIsBetter ? "#4ade80" : "#f87171";
    } else if (value < prev) {
      trendArrow = "↓";
      trendColor = higherIsBetter ? "#f87171" : "#4ade80";
    } else {
      trendArrow = "=";
      trendColor = "#64748b";
    }
  }

  return (
    <div
      style={{
        background: warn ? "rgba(239,68,68,0.06)" : "rgba(255,255,255,0.03)",
        border: `1px solid ${warn ? "#7f1d1d" : "#2d3748"}`,
        borderRadius: "8px",
        padding: "14px 16px",
        textAlign: "center",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "center", gap: "6px" }}>
        <span style={{ fontSize: "24px", fontWeight: 700, color, fontVariantNumeric: "tabular-nums" }}>
          {value.toLocaleString()}
        </span>
        {trendArrow && (
          <span
            style={{ fontSize: "14px", fontWeight: 700, color: trendColor, lineHeight: 1 }}
            title={prev !== undefined ? `Previous run: ${prev.toLocaleString()}` : undefined}
          >
            {trendArrow}
          </span>
        )}
      </div>
      <div style={{ fontSize: "11px", color: "#64748b", marginTop: "4px", letterSpacing: "0.03em" }}>
        {label}
      </div>
    </div>
  );
}
