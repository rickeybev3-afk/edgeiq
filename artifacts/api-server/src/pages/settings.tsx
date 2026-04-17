import { useEffect, useState } from "react";

type TradingMode = "paper" | "live";

interface TradingModeState {
  mode: TradingMode;
  loading: boolean;
  saving: boolean;
  error: string | null;
  saved: boolean;
}

export default function Settings() {
  const [state, setState] = useState<TradingModeState>({
    mode: "paper",
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
    const hash = window.location.hash;
    if (hash === "#trading-mode") {
      const el = document.getElementById("trading-mode");
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  }, [state.loading]);

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
      </div>
    </div>
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
