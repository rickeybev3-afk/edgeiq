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
  script?: string;
}

interface BackfillScriptRun {
  completed_at?: string;
  rows_saved?: number;
  no_bars?: number;
  errors?: number;
  is_overdue?: boolean;
}

interface BackfillScriptSummary {
  completed_at?: string;
  rows_saved?: number;
  no_bars?: number;
  errors?: number;
  is_overdue?: boolean;
  history?: BackfillScriptRun[];
}

interface BackfillErrorAlertsState {
  enabled: boolean;
  loading: boolean;
  saving: boolean;
  error: string | null;
  saved: boolean;
}

interface RecalcZeroAlertsState {
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
  heartbeat_hours?: number;
  is_overdue?: boolean;
  history?: BackfillRun[];
  history_path?: string;
  scripts?: Record<string, BackfillScriptSummary>;
}

interface DryRunTableResult {
  table: string;
  total: number;
  bullish: number;
  bearish: number;
  unfillable: number;
}

interface DryRunResult {
  tables: DryRunTableResult[];
  grand_total: number | null;
  elapsed_s: number | null;
  timed_out: boolean;
  raw_output: string;
  error?: string;
}

interface DryRunState {
  running: boolean;
  result: DryRunResult | null;
  error: string | null;
  showRaw: boolean;
}

interface ContextDryRunTotals {
  candidates: number;
  already_done: number;
  would_update: number;
}

interface ContextDryRunRow {
  user_id: string;
  would_update: number;
}

interface ContextDryRunResult {
  generated_at: string | null;
  mode: string;
  pipeline: string;
  totals: ContextDryRunTotals | null;
  rows: ContextDryRunRow[];
  elapsed_s: number | null;
  timed_out: boolean;
  raw_output: string;
  error?: string;
}

interface ContextDryRunState {
  running: boolean;
  result: ContextDryRunResult | null;
  error: string | null;
  showRaw: boolean;
}

interface RvolSizeTier {
  rvol_min: number;
  multiplier: number;
}

interface RvolSizeTiersState {
  tiers: RvolSizeTier[];
  defaults: RvolSizeTier[];
  draft: RvolSizeTier[];
  loading: boolean;
  saving: boolean;
  error: string | null;
  saved: boolean;
}

interface ConfigParam {
  value: number;
  source: "env" | "override";
}

interface ConfigSummary {
  paper_close_lookback_days: ConfigParam;
  backtest_close_lookback_days: ConfigParam;
  paper_trade_min_tcs: ConfigParam;
  backfill_heartbeat_hours: ConfigParam;
}

interface ConfigSummaryState {
  data: ConfigSummary | null;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
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

  const [recalcZeroAlerts, setRecalcZeroAlerts] = useState<RecalcZeroAlertsState>({
    enabled: true,
    loading: true,
    saving: false,
    error: null,
    saved: false,
  });

  const [activeSection, setActiveSection] = useState<string>("");

  useEffect(() => {
    const sectionIds = [
      "trading-mode",
      "credential-alerts",
      "subscriber-opt-out",
      "backfill-health",
      "context-dryrun",
      "paper-lookback",
      "backfill-heartbeat-window",
      "eod-recalc-health",
      "rvol-size-tiers",
    ];
    const visibleSections = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            visibleSections.add(entry.target.id);
          } else {
            visibleSections.delete(entry.target.id);
          }
        });
        const first = sectionIds.find((id) => visibleSections.has(id));
        if (first !== undefined) setActiveSection(first);
      },
      { threshold: 0.15 }
    );
    sectionIds.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

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

  useEffect(() => {
    let cancelled = false;
    fetch("/api/recalc-zero-alerts")
      .then((r) => {
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) {
          setRecalcZeroAlerts((s) => ({
            ...s,
            enabled: data.enabled !== false,
            loading: false,
          }));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Could not load recalc zero-row alert preference.";
          setRecalcZeroAlerts((s) => ({
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
  const refetchBackfillHealth = useRef<() => void>(() => {});
  const [expandedScripts, setExpandedScripts] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!backfillHealth.scripts) return;
    const currentKeys = new Set(Object.keys(backfillHealth.scripts));
    setExpandedScripts((prev) => {
      const pruned = new Set([...prev].filter((k) => currentKeys.has(k)));
      return pruned.size === prev.size ? prev : pruned;
    });
  }, [backfillHealth.scripts]);

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
    refetchBackfillHealth.current = poll;
    poll();
    const id = setInterval(poll, 60_000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  const [configSummary, setConfigSummary] = useState<ConfigSummaryState>({
    data: null,
    loading: true,
    refreshing: false,
    error: null,
  });

  const fetchConfig = (isManualRefresh = false) => {
    if (isManualRefresh) {
      setConfigSummary((s) => ({ ...s, refreshing: true, error: null }));
    }
    fetch("/api/config")
      .then((r) => {
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        return r.json();
      })
      .then((data: ConfigSummary) => {
        setConfigSummary({ data, loading: false, refreshing: false, error: null });
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Could not load config.";
        setConfigSummary((s) => ({ ...s, data: isManualRefresh ? s.data : null, loading: false, refreshing: false, error: msg }));
      });
  };

  useEffect(() => {
    fetchConfig();
  }, []);

  const [dryRun, setDryRun] = useState<DryRunState>({ running: false, result: null, error: null, showRaw: false });

  const handleDryRun = async () => {
    setDryRun({ running: true, result: null, error: null, showRaw: false });
    try {
      const res = await fetch("/api/backfill-dryrun", { method: "POST" });
      const data: DryRunResult = await res.json();
      if (data.error) {
        setDryRun((s) => ({ ...s, running: false, error: data.error ?? "Unknown error", result: null }));
      } else {
        setDryRun((s) => ({ ...s, running: false, result: data, error: null }));
      }
    } catch (err) {
      setDryRun((s) => ({ ...s, running: false, error: "Could not reach server.", result: null }));
    }
  };

  const [contextDryRun, setContextDryRun] = useState<ContextDryRunState>({ running: false, result: null, error: null, showRaw: false });

  const handleContextDryRun = async () => {
    setContextDryRun({ running: true, result: null, error: null, showRaw: false });
    try {
      const res = await fetch("/api/context-dryrun", { method: "POST" });
      const data: ContextDryRunResult = await res.json();
      if (data.error) {
        setContextDryRun((s) => ({ ...s, running: false, error: data.error ?? "Unknown error", result: null }));
      } else {
        setContextDryRun((s) => ({ ...s, running: false, result: data, error: null }));
      }
    } catch (err) {
      setContextDryRun((s) => ({ ...s, running: false, error: "Could not reach server.", result: null }));
    }
  };

  const [rvolTiers, setRvolTiers] = useState<RvolSizeTiersState>({
    tiers: [],
    defaults: [],
    draft: [],
    loading: true,
    saving: false,
    error: null,
    saved: false,
  });

  useEffect(() => {
    let cancelled = false;
    fetch("/api/rvol-size-tiers")
      .then((r) => {
        if (!r.ok) throw new Error(`Server returned ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) {
          const tiers: RvolSizeTier[] = Array.isArray(data.tiers) ? data.tiers : [];
          const defaults: RvolSizeTier[] = Array.isArray(data.defaults) ? data.defaults : [];
          setRvolTiers((s) => ({ ...s, tiers, defaults, draft: tiers.map((t) => ({ ...t })), loading: false }));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const msg = err instanceof Error ? err.message : "Could not load RVOL size tiers.";
          setRvolTiers((s) => ({ ...s, loading: false, error: msg }));
        }
      });
    return () => { cancelled = true; };
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

  function handleRvolTierAdd() {
    setRvolTiers((s) => ({
      ...s,
      draft: [...s.draft, { rvol_min: 0, multiplier: 1.01 }],
      error: null,
    }));
  }

  function handleRvolTierRemove(index: number) {
    setRvolTiers((s) => ({
      ...s,
      draft: s.draft.filter((_, i) => i !== index),
      error: null,
    }));
  }

  function handleRvolTierEdit(index: number, field: "rvol_min" | "multiplier", value: string) {
    const num = parseFloat(value);
    setRvolTiers((s) => {
      const next = s.draft.map((t, i) => i === index ? { ...t, [field]: isNaN(num) ? 0 : num } : t);
      return { ...s, draft: next, error: null };
    });
  }

  async function handleRvolTiersSave() {
    for (let i = 0; i < rvolTiers.draft.length; i++) {
      const t = rvolTiers.draft[i];
      if (t.rvol_min <= 0) {
        setRvolTiers((s) => ({ ...s, error: `Tier ${i + 1}: RVOL Min must be > 0` }));
        return;
      }
      if (t.multiplier <= 1.0) {
        setRvolTiers((s) => ({ ...s, error: `Tier ${i + 1}: Multiplier must be > 1.0` }));
        return;
      }
    }
    setRvolTiers((s) => ({ ...s, saving: true, error: null, saved: false }));
    try {
      const res = await fetch("/api/rvol-size-tiers", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tiers: rvolTiers.draft }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? `Server returned ${res.status}`);
      }
      const data = await res.json();
      const tiers: RvolSizeTier[] = Array.isArray(data.tiers) ? data.tiers : [];
      setRvolTiers((s) => ({ ...s, tiers, draft: tiers.map((t) => ({ ...t })), saving: false, saved: true }));
      setTimeout(() => setRvolTiers((s) => ({ ...s, saved: false })), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setRvolTiers((s) => ({ ...s, saving: false, error: msg }));
    }
  }

  function handleRvolTiersReset() {
    setRvolTiers((s) => ({
      ...s,
      draft: s.defaults.map((t) => ({ ...t })),
      error: null,
    }));
  }

  useHashScroll(
    ["#trading-mode", "#credential-alerts", "#subscriber-opt-out", "#backfill-health", "#context-dryrun", "#paper-lookback", "#backfill-heartbeat-window", "#eod-recalc-health", "#rvol-size-tiers"],
    [state.loading, credAlerts.loading, subscribersState.loading, backfillHealth.loading, backfillErrAlerts.loading, recalcZeroAlerts.loading, paperLookback.loading, heartbeatWindow.loading, eodRecalcHealth.loading, rvolTiers.loading]
  );

  useEffect(() => {
    const sectionIds = ["trading-mode", "credential-alerts", "subscriber-opt-out", "backfill-health", "context-dryrun", "paper-lookback", "backfill-heartbeat-window", "eod-recalc-health", "rvol-size-tiers"];
    const visibleSections = new Set<string>();

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            visibleSections.add(entry.target.id);
          } else {
            visibleSections.delete(entry.target.id);
          }
        });
        const first = sectionIds.find((id) => visibleSections.has(id));
        if (first) setActiveSection(first);
      },
      { rootMargin: "-10% 0px -60% 0px", threshold: 0 }
    );

    sectionIds.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });

    return () => observer.disconnect();
  }, []);

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
      fetchConfig();
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

  async function handleRecalcZeroAlertsToggle(newEnabled: boolean) {
    setRecalcZeroAlerts((s) => ({ ...s, saving: true, error: null, saved: false }));
    try {
      const res = await fetch("/api/recalc-zero-alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: newEnabled }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error ?? `Server returned ${res.status}`);
      }
      const data = await res.json();
      setRecalcZeroAlerts((s) => ({
        ...s,
        enabled: data.enabled !== false,
        saving: false,
        saved: true,
      }));
      setTimeout(() => setRecalcZeroAlerts((s) => ({ ...s, saved: false })), 3000);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      setRecalcZeroAlerts((s) => ({ ...s, saving: false, error: msg }));
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
      fetchConfig();
      refetchBackfillHealth.current();
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
      fetchConfig();
      refetchBackfillHealth.current();
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
      fetchConfig();
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
      fetchConfig();
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
        <h1 style={{ fontSize: "22px", fontWeight: 700, marginBottom: "24px", color: "#f1f5f9" }}>
          Settings
        </h1>

        <section
          style={{
            background: "#131825",
            border: "1px solid #1e2d40",
            borderRadius: "10px",
            padding: "18px 20px",
            marginBottom: "28px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "14px" }}>
            <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.08em", color: "#475569", textTransform: "uppercase" }}>
              Active Config
            </span>
            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "8px" }}>
              {configSummary.data && !configSummary.loading && (
                <span style={{ fontSize: "10px", color: "#334155" }}>read-only</span>
              )}
              <button
                onClick={() => fetchConfig(true)}
                disabled={configSummary.refreshing || configSummary.loading}
                title="Refresh config"
                style={{
                  background: "none",
                  border: "none",
                  cursor: configSummary.refreshing || configSummary.loading ? "default" : "pointer",
                  padding: "2px",
                  color: configSummary.refreshing ? "#3b82f6" : "#475569",
                  display: "flex",
                  alignItems: "center",
                  opacity: configSummary.refreshing || configSummary.loading ? 0.6 : 1,
                  transition: "color 0.15s, opacity 0.15s",
                }}
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{
                    animation: configSummary.refreshing ? "spin 0.8s linear infinite" : "none",
                  }}
                >
                  <polyline points="23 4 23 10 17 10" />
                  <polyline points="1 20 1 14 7 14" />
                  <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                </svg>
              </button>
            </div>
          </div>

          {configSummary.loading && (
            <p style={{ fontSize: "12px", color: "#475569", margin: 0 }}>Loading…</p>
          )}
          {configSummary.error && !configSummary.loading && (
            <p style={{ fontSize: "12px", color: "#f87171", margin: 0 }}>⚠ {configSummary.error}</p>
          )}
          {configSummary.data && !configSummary.loading && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "8px" }}>
              {(
                [
                  {
                    key: "paper_close_lookback_days" as const,
                    label: "Paper Lookback",
                    unit: "days",
                    fmt: (v: number) => String(v),
                  },
                  {
                    key: "backtest_close_lookback_days" as const,
                    label: "Backtest Lookback",
                    unit: "days",
                    fmt: (v: number) => String(v),
                  },
                  {
                    key: "paper_trade_min_tcs" as const,
                    label: "Min TCS",
                    unit: "",
                    fmt: (v: number) => String(v),
                  },
                  {
                    key: "backfill_heartbeat_hours" as const,
                    label: "Heartbeat Window",
                    unit: "hrs",
                    fmt: (v: number) => v % 1 === 0 ? String(v) : v.toFixed(1),
                  },
                ] as const
              ).map(({ key, label, unit, fmt }) => {
                const param = configSummary.data![key];
                const isOverride = param.source === "override";
                return (
                  <div
                    key={key}
                    style={{
                      background: isOverride ? "rgba(234,179,8,0.05)" : "rgba(255,255,255,0.02)",
                      border: `1px solid ${isOverride ? "#713f12" : "#1e2d40"}`,
                      borderRadius: "7px",
                      padding: "10px 12px",
                      display: "flex",
                      flexDirection: "column",
                      gap: "3px",
                    }}
                    title={isOverride ? "User override — set via Settings" : "Environment default"}
                  >
                    <span style={{ fontSize: "11px", color: "#475569", letterSpacing: "0.03em" }}>
                      {label}
                    </span>
                    <div style={{ display: "flex", alignItems: "baseline", gap: "4px" }}>
                      <span style={{ fontSize: "18px", fontWeight: 700, color: isOverride ? "#fbbf24" : "#94a3b8", fontVariantNumeric: "tabular-nums" }}>
                        {fmt(param.value)}
                      </span>
                      {unit && (
                        <span style={{ fontSize: "11px", color: "#475569" }}>{unit}</span>
                      )}
                    </div>
                    {isOverride && (
                      <span style={{ fontSize: "10px", color: "#a16207", letterSpacing: "0.03em" }}>override</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}

        </section>

        <div
          style={{
            position: "sticky",
            top: 0,
            zIndex: 20,
            background: "#0e1117",
            borderBottom: "1px solid #1a2332",
            padding: "8px 0",
            marginBottom: "20px",
            display: "flex",
            flexWrap: "wrap",
            gap: "6px",
            alignItems: "center",
          }}
        >
          <span style={{ fontSize: "10px", fontWeight: 600, color: "#334155", letterSpacing: "0.06em", textTransform: "uppercase", marginRight: "2px" }}>
            Jump to
          </span>
          {(
            [
              { id: "trading-mode", label: "Trading Mode" },
              { id: "credential-alerts", label: "Subscriber Prefs" },
              { id: "subscriber-opt-out", label: "Credential Status" },
              { id: "backfill-health", label: "Backfill Health" },
              { id: "context-dryrun", label: "Context Dry-Run" },
              { id: "paper-lookback", label: "Paper Lookback" },
              { id: "backfill-heartbeat-window", label: "Alert Window" },
              { id: "eod-recalc-health", label: "EOD Recalc" },
              { id: "rvol-size-tiers", label: "RVOL Tiers" },
            ] as const
          ).map(({ id, label }) => (
            <NavPill
              key={id}
              href={`#${id}`}
              label={label}
              active={activeSection === id}
            />
          ))}
        </div>

        <section
          id="trading-mode"
          style={{
            background: "#1e2435",
            border: "1px solid #2d3748",
            borderRadius: "10px",
            padding: "24px",
            scrollMarginTop: "60px",
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
            scrollMarginTop: "60px",
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
                Recalc zero-row alerts
              </div>
              <div style={{ fontSize: "12px", color: "#64748b", lineHeight: "1.5" }}>
                Send a Telegram message when the nightly P&amp;L recalculation writes zero rows on a trading day (suppressed on weekends and market holidays).
              </div>
            </div>

            {recalcZeroAlerts.loading ? (
              <span style={{ fontSize: "12px", color: "#475569" }}>Loading…</span>
            ) : (
              <ToggleSwitch
                enabled={recalcZeroAlerts.enabled}
                disabled={recalcZeroAlerts.saving}
                onChange={handleRecalcZeroAlertsToggle}
              />
            )}
          </div>

          {recalcZeroAlerts.saving && (
            <p style={{ fontSize: "13px", color: "#94a3b8", marginTop: "14px" }}>Saving…</p>
          )}
          {recalcZeroAlerts.saved && (
            <p style={{ fontSize: "13px", color: "#4ade80", marginTop: "14px" }}>
              ✓ Preference saved.
            </p>
          )}
          {recalcZeroAlerts.error && (
            <p style={{ fontSize: "13px", color: "#f87171", marginTop: "14px" }}>
              ⚠ {recalcZeroAlerts.error}
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
            scrollMarginTop: "60px",
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
            scrollMarginTop: "60px",
          }}
        >
          <h2 style={{ fontSize: "15px", fontWeight: 700, color: "#cbd5e1", marginBottom: "6px" }}>
            Backfill Health
          </h2>
          <p style={{ fontSize: "13px", color: "#94a3b8", marginBottom: "20px", lineHeight: "1.6" }}>
            Last-run status for each backfill script. Refreshes every minute.
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
              {backfillHealth.scripts && Object.keys(backfillHealth.scripts).length > 0 ? (
                <div style={{ marginBottom: "14px" }}>
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px", fontFamily: "monospace" }}>
                      <thead>
                        <tr style={{ borderBottom: "1px solid #2d3748" }}>
                          <th style={{ textAlign: "left", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Script</th>
                          <th style={{ textAlign: "left", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Last ran</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Rows saved</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>No-bars</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Errors</th>
                          <th style={{ textAlign: "center", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(backfillHealth.scripts)
                          .sort(([a], [b]) => (a === "other" ? 1 : b === "other" ? -1 : a.localeCompare(b)))
                          .flatMap(([scriptName, info]) => {
                            const hasErrors = (info.errors ?? 0) > 0;
                            const hasMissingBars = (info.no_bars ?? 0) > 0;
                            const rowBg = hasErrors
                              ? "rgba(248,113,113,0.05)"
                              : info.is_overdue
                              ? "rgba(251,191,36,0.05)"
                              : "transparent";
                            const historyRuns = info.history ?? [];
                            const hasHistory = historyRuns.length > 1;
                            const isExpanded = expandedScripts.has(scriptName);
                            const toggleExpand = () => {
                              setExpandedScripts((prev) => {
                                const next = new Set(prev);
                                if (next.has(scriptName)) next.delete(scriptName);
                                else next.add(scriptName);
                                return next;
                              });
                            };
                            const rows = [];
                            rows.push(
                              <tr
                                key={scriptName}
                                style={{ borderBottom: isExpanded ? "none" : "1px solid rgba(45,55,72,0.5)", background: rowBg }}
                              >
                                <td style={{ padding: "7px 10px", color: scriptName === "other" ? "#64748b" : "#cbd5e1", fontStyle: scriptName === "other" ? "italic" : "normal" }}>
                                  <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                                    {hasHistory && (
                                      <button
                                        onClick={toggleExpand}
                                        title={isExpanded ? "Hide run history" : "Show run history"}
                                        style={{
                                          background: "none",
                                          border: "none",
                                          cursor: "pointer",
                                          padding: "0 2px",
                                          color: "#64748b",
                                          fontSize: "10px",
                                          lineHeight: 1,
                                          flexShrink: 0,
                                        }}
                                      >
                                        {isExpanded ? "▾" : "▸"}
                                      </button>
                                    )}
                                    {!hasHistory && <span style={{ display: "inline-block", width: "14px" }} />}
                                    {scriptName}
                                  </span>
                                </td>
                                <td style={{ padding: "7px 10px", color: info.is_overdue ? "#fbbf24" : "#94a3b8" }}>
                                  {info.completed_at ? (
                                    <span title={new Date(info.completed_at).toLocaleString()}>
                                      {formatRelativeTime(info.completed_at)}
                                    </span>
                                  ) : (
                                    <span style={{ color: "#475569" }}>—</span>
                                  )}
                                </td>
                                <td style={{ padding: "7px 10px", textAlign: "right", color: "#4ade80" }}>
                                  {info.rows_saved != null ? info.rows_saved.toLocaleString() : <span style={{ color: "#475569" }}>—</span>}
                                </td>
                                <td style={{ padding: "7px 10px", textAlign: "right", color: hasMissingBars ? "#fbbf24" : "#64748b" }}>
                                  {info.no_bars != null ? info.no_bars.toLocaleString() : <span style={{ color: "#475569" }}>—</span>}
                                </td>
                                <td style={{ padding: "7px 10px", textAlign: "right", color: hasErrors ? "#f87171" : "#4ade80" }}>
                                  {info.errors != null ? info.errors.toLocaleString() : <span style={{ color: "#475569" }}>—</span>}
                                </td>
                                <td style={{ padding: "7px 10px", textAlign: "center" }}>
                                  {hasErrors ? (
                                    <span style={{ fontSize: "10px", color: "#f87171" }}>error</span>
                                  ) : info.is_overdue ? (
                                    <span style={{ fontSize: "10px", color: "#fbbf24" }}>overdue</span>
                                  ) : (
                                    <span style={{ fontSize: "10px", color: "#4ade80" }}>ok</span>
                                  )}
                                </td>
                              </tr>
                            );
                            if (isExpanded && historyRuns.length > 1) {
                              rows.push(
                                <tr key={`${scriptName}-hist-label`} style={{ background: "transparent" }}>
                                  <td colSpan={6} style={{ padding: "2px 10px 2px 28px", fontSize: "10px", color: "#475569", fontFamily: "sans-serif", letterSpacing: "0.04em" }}>
                                    previous runs — latest shown above
                                  </td>
                                </tr>
                              );
                              historyRuns.slice(1).forEach((run, idx) => {
                                const runHasErrors = (run.errors ?? 0) > 0;
                                const runHasMissingBars = (run.no_bars ?? 0) > 0;
                                const runBg = runHasErrors
                                  ? "rgba(248,113,113,0.04)"
                                  : run.is_overdue
                                  ? "rgba(251,191,36,0.04)"
                                  : "transparent";
                                const isLast = idx === historyRuns.length - 2;
                                rows.push(
                                  <tr
                                    key={`${scriptName}-hist-${idx}`}
                                    style={{
                                      borderBottom: isLast ? "1px solid rgba(45,55,72,0.5)" : "1px solid rgba(45,55,72,0.2)",
                                      background: runBg,
                                    }}
                                  >
                                    <td style={{ padding: "4px 10px 4px 28px", color: "#475569", fontSize: "11px" }}>
                                      <span style={{ opacity: 0.6 }}>↳</span>
                                    </td>
                                    <td style={{ padding: "4px 10px", color: run.is_overdue ? "#fbbf24" : "#64748b", fontSize: "11px" }}>
                                      {run.completed_at ? (
                                        <span title={new Date(run.completed_at).toLocaleString()}>
                                          {formatRelativeTime(run.completed_at)}
                                        </span>
                                      ) : (
                                        <span style={{ color: "#475569" }}>—</span>
                                      )}
                                    </td>
                                    <td style={{ padding: "4px 10px", textAlign: "right", color: "#4ade80", fontSize: "11px" }}>
                                      {run.rows_saved != null ? run.rows_saved.toLocaleString() : <span style={{ color: "#475569" }}>—</span>}
                                    </td>
                                    <td style={{ padding: "4px 10px", textAlign: "right", color: runHasMissingBars ? "#fbbf24" : "#475569", fontSize: "11px" }}>
                                      {run.no_bars != null ? run.no_bars.toLocaleString() : <span style={{ color: "#475569" }}>—</span>}
                                    </td>
                                    <td style={{ padding: "4px 10px", textAlign: "right", color: runHasErrors ? "#f87171" : "#4ade80", fontSize: "11px" }}>
                                      {run.errors != null ? run.errors.toLocaleString() : <span style={{ color: "#475569" }}>—</span>}
                                    </td>
                                    <td style={{ padding: "4px 10px", textAlign: "center" }}>
                                      {runHasErrors ? (
                                        <span style={{ fontSize: "9px", color: "#f87171" }}>error</span>
                                      ) : run.is_overdue ? (
                                        <span style={{ fontSize: "9px", color: "#fbbf24" }}>overdue</span>
                                      ) : (
                                        <span style={{ fontSize: "9px", color: "#4ade80" }}>ok</span>
                                      )}
                                    </td>
                                  </tr>
                                );
                              });
                            }
                            return rows;
                          })}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : (
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
              )}
              {backfillHealth.completed_at && !(backfillHealth.scripts && Object.keys(backfillHealth.scripts).length > 0) && (() => {
                const threshold = backfillHealth.heartbeat_hours ?? 25;
                const ageHours = (Date.now() - new Date(backfillHealth.completed_at!).getTime()) / 3_600_000;
                const ageColor = backfillHealth.is_overdue
                  ? "#f87171"
                  : ageHours >= threshold * 0.8
                  ? "#fbbf24"
                  : "#94a3b8";
                return (
                  <p style={{ fontSize: "11px", fontFamily: "monospace", margin: 0, display: "flex", alignItems: "center", gap: "6px", flexWrap: "wrap" }}>
                    <span style={{ color: ageColor, fontWeight: backfillHealth.is_overdue || ageHours >= threshold * 0.8 ? 700 : 400 }}>
                      Last run {formatRelativeTime(backfillHealth.completed_at!)}
                    </span>
                    <span style={{ color: "#475569" }}>·</span>
                    <span style={{ color: "#475569" }}>alert threshold: {threshold} h</span>
                    <span style={{ color: "#334155" }}>·</span>
                    <span style={{ color: "#475569" }}>{new Date(backfillHealth.completed_at!).toLocaleString()}</span>
                  </p>
                );
              })()}
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
                          <th style={{ textAlign: "left", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Script</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Gap</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Rows saved</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>No-bars</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Errors</th>
                        </tr>
                      </thead>
                      <tbody>
                        {backfillHealth.history.map((run, i) => {
                          const heartbeatHours = backfillHealth.heartbeat_hours ?? 25;
                          const prevRun = backfillHealth.history![i + 1];
                          const gapHours = prevRun
                            ? (new Date(run.completed_at).getTime() - new Date(prevRun.completed_at).getTime()) / 3_600_000
                            : null;
                          const isOverdue = gapHours !== null && gapHours > heartbeatHours;
                          const rowBg = isOverdue
                            ? "rgba(251,191,36,0.07)"
                            : i === 0
                            ? "rgba(255,255,255,0.03)"
                            : "transparent";
                          return (
                          <tr
                            key={`${run.completed_at}-${i}`}
                            style={{
                              borderBottom: "1px solid rgba(45,55,72,0.5)",
                              background: rowBg,
                            }}
                          >
                            <td style={{ padding: "6px 10px", color: isOverdue ? "#fbbf24" : i === 0 ? "#cbd5e1" : "#64748b" }}>
                              {formatRelativeTime(run.completed_at)}
                              {i === 0 && (
                                <span style={{ marginLeft: "6px", fontSize: "10px", color: "#4ade80", fontFamily: "sans-serif" }}>latest</span>
                              )}
                              {isOverdue && (
                                <span style={{
                                  marginLeft: "6px",
                                  fontSize: "10px",
                                  color: "#fbbf24",
                                  fontFamily: "sans-serif",
                                  background: "rgba(251,191,36,0.15)",
                                  border: "1px solid rgba(251,191,36,0.3)",
                                  borderRadius: "3px",
                                  padding: "1px 4px",
                                }}>overdue</span>
                              )}
                            </td>
                            <td style={{ padding: "6px 10px", color: run.script && run.script !== "other" ? "#94a3b8" : "#475569", fontStyle: !run.script || run.script === "other" ? "italic" : "normal" }}>
                              {run.script ?? "other"}
                            </td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: gapHours === null ? "#475569" : isOverdue ? "#fbbf24" : "#64748b", fontWeight: isOverdue ? 600 : 400 }}>
                              {gapHours === null
                                ? "—"
                                : gapHours >= 48
                                ? `${Math.floor(gapHours / 24)}d ${Math.floor(gapHours % 24)}h`
                                : gapHours < 1
                                ? `${Math.floor(gapHours * 60)} min`
                                : `${Math.floor(gapHours)} h`}
                            </td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: "#4ade80" }}>
                              {(run.rows_saved ?? 0).toLocaleString()}
                            </td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: (run.no_bars ?? 0) > 0 ? "#fbbf24" : "#64748b" }}>
                              {(run.no_bars ?? 0).toLocaleString()}
                            </td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: (run.errors ?? 0) > 0 ? "#f87171" : "#4ade80" }}>
                              {(run.errors ?? 0).toLocaleString()}
                            </td>
                          </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}

          <div style={{ marginTop: "24px", borderTop: "1px solid #2d3748", paddingTop: "20px" }}>
            <p style={{ fontSize: "13px", color: "#94a3b8", marginBottom: "14px", lineHeight: "1.6" }}>
              Run a <strong style={{ color: "#fbbf24" }}>dry-run preview</strong> to see how many rows would be
              updated without making any database writes.
            </p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "10px", alignItems: "center" }}>
              <button
                onClick={handleDryRun}
                disabled={dryRun.running}
                style={{
                  padding: "9px 18px",
                  background: dryRun.running ? "#78350f" : "#92400e",
                  border: "1px solid #fbbf24",
                  borderRadius: "7px",
                  color: "#fef3c7",
                  fontSize: "13px",
                  fontWeight: 600,
                  cursor: dryRun.running ? "not-allowed" : "pointer",
                  opacity: dryRun.running ? 0.7 : 1,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "7px",
                  transition: "opacity 0.15s",
                }}
              >
                <span style={{ fontSize: "15px" }}>🔍</span>
                {dryRun.running ? "Running dry-run preview… (this may take a minute)" : "Preview Dry Run"}
              </button>
            </div>

            {dryRun.error && (
              <div style={{ marginTop: "14px", padding: "12px 14px", background: "rgba(248,113,113,0.1)", border: "1px solid #f87171", borderRadius: "7px", color: "#f87171", fontSize: "13px" }}>
                ⚠ {dryRun.error}
              </div>
            )}

            {dryRun.result && !dryRun.error && (
              <div style={{ marginTop: "16px" }}>
                {dryRun.result.timed_out && (
                  <div style={{ marginBottom: "12px", padding: "10px 14px", background: "rgba(251,191,36,0.08)", border: "1px solid #fbbf24", borderRadius: "7px", color: "#fbbf24", fontSize: "12px" }}>
                    ⚠ Dry-run timed out after 300s — results below may be partial.
                  </div>
                )}

                <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginBottom: "14px" }}>
                  <p style={{ fontSize: "13px", fontWeight: 700, color: "#fef3c7", margin: 0 }}>
                    Dry-Run Preview Results
                  </p>
                  {dryRun.result.elapsed_s != null && (
                    <span style={{ fontSize: "11px", color: "#64748b" }}>({dryRun.result.elapsed_s.toFixed(0)}s elapsed)</span>
                  )}
                </div>

                {dryRun.result.grand_total != null && (
                  <div style={{ marginBottom: "14px", padding: "12px 16px", background: "rgba(251,191,36,0.08)", border: "1px solid rgba(251,191,36,0.3)", borderRadius: "8px" }}>
                    <span style={{ fontSize: "22px", fontWeight: 700, color: "#fbbf24", fontFamily: "monospace" }}>
                      {dryRun.result.grand_total.toLocaleString()}
                    </span>
                    <span style={{ fontSize: "13px", color: "#94a3b8", marginLeft: "8px" }}>
                      total row(s) would be updated across all users &amp; tables
                    </span>
                  </div>
                )}

                {dryRun.result.tables.length > 0 && (
                  <div style={{ overflowX: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px", fontFamily: "monospace" }}>
                      <thead>
                        <tr style={{ borderBottom: "1px solid #2d3748" }}>
                          <th style={{ textAlign: "left", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Table</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Would update</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Bullish Break</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Bearish Break</th>
                          <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Unfillable</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dryRun.result.tables.map((t) => (
                          <tr key={t.table} style={{ borderBottom: "1px solid rgba(45,55,72,0.5)" }}>
                            <td style={{ padding: "6px 10px", color: "#cbd5e1" }}>{t.table}</td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: "#fbbf24", fontWeight: 600 }}>{t.total.toLocaleString()}</td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: "#7dd3fc" }}>{t.bullish.toLocaleString()}</td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: "#a78bfa" }}>{t.bearish.toLocaleString()}</td>
                            <td style={{ padding: "6px 10px", textAlign: "right", color: t.unfillable > 0 ? "#f87171" : "#64748b" }}>{t.unfillable.toLocaleString()}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {dryRun.result.tables.length === 0 && dryRun.result.grand_total === null && (
                  <p style={{ fontSize: "13px", color: "#64748b", marginTop: "8px" }}>
                    No structured summary lines were found in the output.
                  </p>
                )}

                <button
                  onClick={() => setDryRun((s) => ({ ...s, showRaw: !s.showRaw }))}
                  style={{ marginTop: "12px", background: "none", border: "none", color: "#64748b", fontSize: "11px", cursor: "pointer", padding: 0, textDecoration: "underline" }}
                >
                  {dryRun.showRaw ? "Hide raw output" : "Show raw output"}
                </button>

                {dryRun.showRaw && (
                  <pre style={{ marginTop: "8px", padding: "12px", background: "#0e1117", border: "1px solid #2d3748", borderRadius: "6px", fontSize: "11px", color: "#94a3b8", overflowX: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: "300px", overflowY: "auto" }}>
                    {dryRun.result.raw_output}
                  </pre>
                )}
              </div>
            )}

          </div>
        </section>

        <section
          id="context-dryrun"
          style={{
            background: "#1e2435",
            border: "1px solid #2d3748",
            borderRadius: "10px",
            padding: "24px",
            scrollMarginTop: "60px",
            marginTop: "20px",
          }}
        >
          <h2 style={{ fontSize: "15px", fontWeight: 700, color: "#cbd5e1", marginBottom: "6px" }}>
            Context Dry-Run Preview
          </h2>
          <p style={{ fontSize: "13px", color: "#94a3b8", marginBottom: "16px", lineHeight: "1.6" }}>
            Preview how many context-level rows (S/R, VWAP, MACD) would be updated without
            making any database writes.
          </p>

          <button
            onClick={handleContextDryRun}
            disabled={contextDryRun.running}
            style={{
              padding: "9px 18px",
              background: contextDryRun.running ? "#1e3a5f" : "#1e40af",
              border: "1px solid #60a5fa",
              borderRadius: "7px",
              color: "#dbeafe",
              fontSize: "13px",
              fontWeight: 600,
              cursor: contextDryRun.running ? "not-allowed" : "pointer",
              opacity: contextDryRun.running ? 0.7 : 1,
              display: "inline-flex",
              alignItems: "center",
              gap: "7px",
              transition: "opacity 0.15s",
            }}
          >
            <span style={{ fontSize: "15px" }}>🔎</span>
            {contextDryRun.running ? "Scanning context scope… (this may take a moment)" : "Run Preview"}
          </button>

          {contextDryRun.running && (
            <p style={{ marginTop: "12px", fontSize: "13px", color: "#64748b" }}>
              Counting rows — this may take up to a minute…
            </p>
          )}

          {contextDryRun.error && (
            <div style={{ marginTop: "14px", padding: "12px 14px", background: "rgba(248,113,113,0.1)", border: "1px solid #f87171", borderRadius: "7px", color: "#f87171", fontSize: "13px" }}>
              ⚠ {contextDryRun.error}
            </div>
          )}

          {contextDryRun.result && !contextDryRun.error && (
            <div style={{ marginTop: "20px" }}>
              {contextDryRun.result.timed_out && (
                <div style={{ marginBottom: "12px", padding: "10px 14px", background: "rgba(251,191,36,0.08)", border: "1px solid #fbbf24", borderRadius: "7px", color: "#fbbf24", fontSize: "12px" }}>
                  ⚠ Context dry-run timed out after 300s — results below may be partial.
                </div>
              )}

              <div style={{ marginBottom: "14px" }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: "10px" }}>
                  <p style={{ fontSize: "13px", fontWeight: 700, color: "#dbeafe", margin: 0 }}>
                    Context Dry-Run Results
                  </p>
                  {contextDryRun.result.elapsed_s != null && (
                    <span style={{ fontSize: "11px", color: "#64748b" }}>({contextDryRun.result.elapsed_s.toFixed(1)}s elapsed)</span>
                  )}
                </div>
                {contextDryRun.result.generated_at && (
                  <p style={{ fontSize: "11px", color: "#64748b", margin: "4px 0 0 0" }}>
                    Generated at {new Date(contextDryRun.result.generated_at).toLocaleString()}
                  </p>
                )}
              </div>

              {contextDryRun.result.totals ? (
                <>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", marginBottom: "20px" }}>
                    <div style={{ padding: "14px 18px", background: "rgba(96,165,250,0.08)", border: "1px solid rgba(96,165,250,0.3)", borderRadius: "8px", minWidth: "140px" }}>
                      <div style={{ fontSize: "24px", fontWeight: 700, color: "#60a5fa", fontFamily: "monospace" }}>
                        {contextDryRun.result.totals.candidates.toLocaleString()}
                      </div>
                      <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "3px" }}>candidates</div>
                    </div>
                    <div style={{ padding: "14px 18px", background: "rgba(100,116,139,0.08)", border: "1px solid rgba(100,116,139,0.3)", borderRadius: "8px", minWidth: "140px" }}>
                      <div style={{ fontSize: "24px", fontWeight: 700, color: "#94a3b8", fontFamily: "monospace" }}>
                        {contextDryRun.result.totals.already_done.toLocaleString()}
                      </div>
                      <div style={{ fontSize: "11px", color: "#64748b", marginTop: "3px" }}>already done</div>
                    </div>
                    <div style={{ padding: "14px 18px", background: "rgba(74,222,128,0.08)", border: "1px solid rgba(74,222,128,0.3)", borderRadius: "8px", minWidth: "140px" }}>
                      <div style={{ fontSize: "24px", fontWeight: 700, color: "#4ade80", fontFamily: "monospace" }}>
                        {contextDryRun.result.totals.would_update.toLocaleString()}
                      </div>
                      <div style={{ fontSize: "11px", color: "#94a3b8", marginTop: "3px" }}>would update</div>
                    </div>
                  </div>

                  {contextDryRun.result.rows && contextDryRun.result.rows.length > 0 && (
                    <div style={{ marginBottom: "14px" }}>
                      <p style={{ fontSize: "12px", fontWeight: 600, color: "#94a3b8", marginBottom: "8px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                        Per-User Breakdown
                      </p>
                      <div style={{ overflowX: "auto" }}>
                        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px", fontFamily: "monospace" }}>
                          <thead>
                            <tr style={{ borderBottom: "1px solid #2d3748" }}>
                              <th style={{ textAlign: "left", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>User ID</th>
                              <th style={{ textAlign: "right", padding: "6px 10px", color: "#64748b", fontWeight: 500 }}>Would Update</th>
                            </tr>
                          </thead>
                          <tbody>
                            {contextDryRun.result.rows.map((row) => (
                              <tr key={row.user_id} style={{ borderBottom: "1px solid rgba(45,55,72,0.5)" }}>
                                <td style={{ padding: "6px 10px", color: "#cbd5e1" }}>{row.user_id}</td>
                                <td style={{ padding: "6px 10px", textAlign: "right", color: "#4ade80", fontWeight: 600 }}>{row.would_update.toLocaleString()}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <p style={{ fontSize: "13px", color: "#64748b", marginTop: "8px" }}>
                  No summary data was returned from the server.
                </p>
              )}

              <button
                onClick={() => setContextDryRun((s) => ({ ...s, showRaw: !s.showRaw }))}
                style={{ marginTop: "4px", background: "none", border: "none", color: "#64748b", fontSize: "11px", cursor: "pointer", padding: 0, textDecoration: "underline" }}
              >
                {contextDryRun.showRaw ? "Hide raw output" : "Show raw output"}
              </button>

              {contextDryRun.showRaw && (
                <pre style={{ marginTop: "8px", padding: "12px", background: "#0e1117", border: "1px solid #2d3748", borderRadius: "6px", fontSize: "11px", color: "#94a3b8", overflowX: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: "300px", overflowY: "auto" }}>
                  {contextDryRun.result.raw_output}
                </pre>
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
            scrollMarginTop: "60px",
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
            scrollMarginTop: "60px",
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
            scrollMarginTop: "60px",
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

        <section
          id="rvol-size-tiers"
          style={{
            background: "#131825",
            border: "1px solid #1e2d40",
            borderRadius: "10px",
            padding: "18px 20px",
            marginBottom: "28px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "4px" }}>
            <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.08em", color: "#475569", textTransform: "uppercase" }}>
              RVOL Size Tiers
            </span>
            {rvolTiers.saved && (
              <span style={{ fontSize: "11px", color: "#4ade80", fontWeight: 600 }}>✓ Saved</span>
            )}
          </div>
          <p style={{ fontSize: "12px", color: "#64748b", marginBottom: "16px", lineHeight: "1.6" }}>
            Position-size multipliers applied when RVOL exceeds a threshold. Tiers are evaluated from highest to lowest; first match wins. All changes take effect immediately without a server restart.
          </p>

          {rvolTiers.loading ? (
            <p style={{ fontSize: "13px", color: "#475569" }}>Loading…</p>
          ) : (
            <>
              {rvolTiers.draft.length === 0 && (
                <p style={{ fontSize: "12px", color: "#64748b", fontStyle: "italic", marginBottom: "12px" }}>
                  No tiers configured — all trades use 1× sizing.
                </p>
              )}

              {rvolTiers.draft.length > 0 && (
                <div style={{ marginBottom: "12px" }}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: "8px", marginBottom: "6px" }}>
                    <span style={{ fontSize: "11px", color: "#475569", fontWeight: 600, letterSpacing: "0.05em" }}>RVOL MIN (≥)</span>
                    <span style={{ fontSize: "11px", color: "#475569", fontWeight: 600, letterSpacing: "0.05em" }}>MULTIPLIER (×)</span>
                    <span />
                  </div>
                  {rvolTiers.draft.map((tier, i) => (
                    <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: "8px", marginBottom: "8px", alignItems: "center" }}>
                      <input
                        type="number"
                        step="0.1"
                        min="0.1"
                        value={tier.rvol_min}
                        onChange={(e) => handleRvolTierEdit(i, "rvol_min", e.target.value)}
                        disabled={rvolTiers.saving}
                        style={{
                          background: "#0e1117",
                          border: "1px solid #2d3748",
                          borderRadius: "6px",
                          color: "#f1f5f9",
                          fontSize: "13px",
                          padding: "7px 10px",
                          width: "100%",
                          boxSizing: "border-box",
                          fontFamily: "monospace",
                        }}
                      />
                      <input
                        type="number"
                        step="0.01"
                        min="1.01"
                        value={tier.multiplier}
                        onChange={(e) => handleRvolTierEdit(i, "multiplier", e.target.value)}
                        disabled={rvolTiers.saving}
                        style={{
                          background: "#0e1117",
                          border: "1px solid #2d3748",
                          borderRadius: "6px",
                          color: "#f1f5f9",
                          fontSize: "13px",
                          padding: "7px 10px",
                          width: "100%",
                          boxSizing: "border-box",
                          fontFamily: "monospace",
                        }}
                      />
                      <button
                        onClick={() => handleRvolTierRemove(i)}
                        disabled={rvolTiers.saving}
                        title="Remove tier"
                        style={{
                          background: "rgba(239,68,68,0.1)",
                          border: "1px solid rgba(239,68,68,0.3)",
                          borderRadius: "6px",
                          color: "#f87171",
                          fontSize: "14px",
                          padding: "6px 10px",
                          cursor: rvolTiers.saving ? "not-allowed" : "pointer",
                          lineHeight: 1,
                        }}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {rvolTiers.error && (
                <div style={{ marginBottom: "12px", padding: "10px 12px", background: "rgba(248,113,113,0.08)", border: "1px solid rgba(248,113,113,0.3)", borderRadius: "7px", color: "#f87171", fontSize: "12px" }}>
                  ⚠ {rvolTiers.error}
                </div>
              )}

              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                <button
                  onClick={handleRvolTierAdd}
                  disabled={rvolTiers.saving}
                  style={{
                    padding: "7px 14px",
                    background: "rgba(99,102,241,0.1)",
                    border: "1px solid rgba(99,102,241,0.4)",
                    borderRadius: "7px",
                    color: "#818cf8",
                    fontSize: "12px",
                    fontWeight: 600,
                    cursor: rvolTiers.saving ? "not-allowed" : "pointer",
                  }}
                >
                  + Add Tier
                </button>

                <button
                  onClick={handleRvolTiersSave}
                  disabled={rvolTiers.saving}
                  style={{
                    padding: "7px 16px",
                    background: rvolTiers.saving ? "#1e3a5f" : "#1d4ed8",
                    border: "1px solid #3b82f6",
                    borderRadius: "7px",
                    color: "#eff6ff",
                    fontSize: "12px",
                    fontWeight: 600,
                    cursor: rvolTiers.saving ? "not-allowed" : "pointer",
                    opacity: rvolTiers.saving ? 0.7 : 1,
                  }}
                >
                  {rvolTiers.saving ? "Saving…" : "Save"}
                </button>

                <button
                  onClick={handleRvolTiersReset}
                  disabled={rvolTiers.saving}
                  title="Reset to defaults"
                  style={{
                    padding: "7px 14px",
                    background: "transparent",
                    border: "1px solid #2d3748",
                    borderRadius: "7px",
                    color: "#64748b",
                    fontSize: "12px",
                    cursor: rvolTiers.saving ? "not-allowed" : "pointer",
                  }}
                >
                  Reset to defaults
                </button>
              </div>

              {rvolTiers.tiers.length > 0 && (
                <div style={{ marginTop: "16px", padding: "10px 12px", background: "rgba(255,255,255,0.02)", border: "1px solid #1e2d40", borderRadius: "7px" }}>
                  <p style={{ fontSize: "11px", color: "#475569", margin: "0 0 6px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>Active tiers</p>
                  {rvolTiers.tiers.map((t, i) => (
                    <p key={i} style={{ fontSize: "12px", color: "#94a3b8", fontFamily: "monospace", margin: "0 0 2px" }}>
                      RVOL ≥ <span style={{ color: "#f1f5f9" }}>{t.rvol_min}×</span>
                      {" → "}
                      <span style={{ color: "#4ade80" }}>{t.multiplier}× size</span>
                    </p>
                  ))}
                </div>
              )}
            </>
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

function NavPill({ href, label, active }: { href: string; label: string; active?: boolean }) {
  const [hovered, setHovered] = useState(false);
  return (
    <a
      href={href}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: "inline-block",
        fontSize: "11px",
        fontWeight: active ? 700 : 500,
        color: active ? "#e2e8f0" : hovered ? "#93c5fd" : "#64748b",
        background: active ? "rgba(59,130,246,0.15)" : hovered ? "rgba(147,197,253,0.08)" : "transparent",
        border: `1px solid ${active ? "#3b82f6" : hovered ? "#3b82f6" : "#1e2d40"}`,
        borderRadius: "999px",
        padding: "2px 9px",
        textDecoration: "none",
        transition: "color 0.15s, background 0.15s, border-color 0.15s",
        cursor: "pointer",
        whiteSpace: "nowrap",
        letterSpacing: "0.02em",
      }}
    >
      {label}
    </a>
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
