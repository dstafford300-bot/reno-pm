import {
  createContext, useCallback, useContext, useEffect, useState, type ReactNode,
} from "react";
import type { Status } from "../types";

/* ---------- icons ---------- */
const P = (d: string) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
);
export const Icon = {
  home: P("M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10"),
  calendar: P("M4 6h16v14H4zM4 10h16M9 3v6M15 3v6"),
  money: P("M12 3v18M16.5 7.5C16.5 6 14.5 5 12 5S7.5 6 7.5 8s2 2.6 4.5 3 4.5 1 4.5 3-2 3-4.5 3-4.5-1-4.5-2.5"),
  book: P("M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3zM5 17a3 3 0 013-3h11"),
  receipt: P("M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6"),
  upload: P("M12 16V4m0 0L7 9m5-5l5 5M5 20h14"),
  check: P("M5 12l4 4 10-10"),
  users: P("M16 19v-1a4 4 0 00-4-4H7a4 4 0 00-4 4v1M9.5 10a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM21 19v-1a4 4 0 00-3-3.9M16 3.2a3.5 3.5 0 010 6.6"),
  more: P("M5 12h.01M12 12h.01M19 12h.01"),
  chevron: P("M9 6l6 6-6 6"),
  user: P("M12 12a4 4 0 100-8 4 4 0 000 8zM4 21a8 8 0 0116 0"),
};

/* ---------- toast ---------- */
type ToastFn = (message: string, kind?: "ok" | "error") => void;
const ToastCtx = createContext<ToastFn>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ message: string; kind: string } | null>(null);
  const show = useCallback<ToastFn>((message, kind = "ok") => setToast({ message, kind }), []);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), toast.kind === "error" ? 5000 : 2600);
    return () => clearTimeout(t);
  }, [toast]);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {toast && <div className={`toast ${toast.kind === "error" ? "error" : ""}`} role="status">{toast.message}</div>}
    </ToastCtx.Provider>
  );
}

/* ---------- bottom sheet / modal ---------- */
export function Sheet({ title, onClose, children }: { title?: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title}>
        <div className="grab" />
        {title && <h2 style={{ marginBottom: 12 }}>{title}</h2>}
        {children}
      </div>
    </div>
  );
}

export function Confirm({
  title, body, confirmLabel = "Confirm", danger, busy, onConfirm, onClose,
}: {
  title: string; body: ReactNode; confirmLabel?: string; danger?: boolean; busy?: boolean;
  onConfirm: () => void; onClose: () => void;
}) {
  return (
    <Sheet title={title} onClose={onClose}>
      <div className="stack">
        <div className="muted">{body}</div>
        <div className="row">
          <button className="btn grow" onClick={onClose}>Cancel</button>
          <button className={`btn grow ${danger ? "danger" : "primary"}`} disabled={busy} onClick={onConfirm}>
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </Sheet>
  );
}

/* ---------- small pieces ---------- */
export const STATUS_COLOR: Record<Status, string> = {
  Pending: "var(--pending)", "In Progress": "var(--progress)", Completed: "var(--done)",
};

export function StatusPill({ status, percent }: { status: Status; percent?: number }) {
  const label = status === "In Progress" && percent ? `${Math.round(percent)}%` : status;
  return <span className={`pill ${status}`}>{label}</span>;
}

export function Spinner() {
  return <div className="center"><div className="spinner" /></div>;
}

export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="stack">
      {Array.from({ length: rows }, (_, i) => <div key={i} className="skeleton" style={{ height: 64 }} />)}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  return <div className="notice danger">{error instanceof Error ? error.message : "Something went wrong"}</div>;
}

export const money = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
export const money2 = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD" });

export function fmtDate(iso: string | null | undefined, withYear = false) {
  if (!iso) return "—";
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(withYear ? { year: "numeric" } : {}) });
}
export function fmtDateTime(iso: string) {
  return new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
