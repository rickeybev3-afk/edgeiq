import { useEffect, useState } from "react";
import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AlertCircle } from "lucide-react";
import NotFound from "@/pages/not-found";

const queryClient = new QueryClient();

interface HealthState {
  checked: boolean;
  ok: boolean;
  errors: string[];
  alpaca_mode_mismatch?: boolean;
  alpaca_mismatch_message?: string;
}

function AlpacaMismatchBanner({ message }: { message: string }) {
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
      <span style={{ color: "#fed7aa", fontSize: "13px", lineHeight: "1.5" }}>
        <strong style={{ color: "#fdba74" }}>⚠️ Alpaca credential mismatch:</strong>{" "}
        {message}
      </span>
    </div>
  );
}

function ErrorBanner({ errors }: { errors: string[] }) {
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
      </div>
    </div>
  );
}

function Home() {
  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <h1 className="text-2xl font-bold text-gray-900">EdgeIQ Dashboard</h1>
        <p className="mt-2 text-sm text-gray-600">Connected and ready.</p>
      </div>
    </div>
  );
}

function Router() {
  return (
    <Switch>
      <Route path="/" component={Home} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  const [health, setHealth] = useState<HealthState>({ checked: false, ok: true, errors: [] });

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
    const interval = setInterval(check, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (!health.checked) {
    return null;
  }

  if (!health.ok) {
    return <ErrorBanner errors={health.errors} />;
  }

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        {health.alpaca_mode_mismatch && health.alpaca_mismatch_message && (
          <AlpacaMismatchBanner message={health.alpaca_mismatch_message} />
        )}
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
