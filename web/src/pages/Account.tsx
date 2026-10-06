import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { useToast } from "../components/ui";

export default function Account() {
  const { user, refresh, logout } = useAuth();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/api/auth/change-password", { current_password: current, new_password: next });
      toast("Password changed");
      setCurrent(""); setNext("");
      await refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : "Couldn't change password", "error");
    } finally { setBusy(false); }
  };

  return (
    <div className="stack" style={{ maxWidth: 460 }}>
      <h1>Account</h1>
      <div className="card">
        <div style={{ fontWeight: 700 }}>{user!.name}</div>
        <div className="muted small">{user!.email} · {user!.role}</div>
      </div>
      {user!.must_change_password && (
        <div className="notice warn">Please choose your own password before continuing.</div>
      )}
      <form className="card" onSubmit={submit}>
        <h2 style={{ marginBottom: 10 }}>Change password</h2>
        <label className="field"><span>Current password</span>
          <input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label className="field"><span>New password (10+ characters)</span>
          <input type="password" autoComplete="new-password" required minLength={10} value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <button className="btn primary block" disabled={busy}>{busy ? "Saving…" : "Change password"}</button>
      </form>
      <button className="btn block" onClick={() => void logout()}>Sign out</button>
    </div>
  );
}
