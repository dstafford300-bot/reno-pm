import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Sheet, Skeleton, useToast } from "../components/ui";
import type { AppUser, PropertySummary, Role } from "../types";

const ROLE_LABEL: Record<Role, string> = { owner: "Owner", pm: "Project manager", contractor: "Contractor" };
const ROLE_HELP: Record<Role, string> = {
  owner: "Everything, including budgets, approvals and users.",
  pm: "Sees everything and edits the schedule. Budgets are read-only.",
  contractor: "Sees only assigned properties' schedule. Changes need your approval.",
};

export default function Users() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["users"], queryFn: () => api.get<AppUser[]>("/api/users") });
  const { data: props } = useQuery({ queryKey: ["properties"], queryFn: () => api.get<PropertySummary[]>("/api/properties") });
  const [editing, setEditing] = useState<AppUser | "new" | null>(null);
  const [secret, setSecret] = useState<{ email: string; password: string } | null>(null);

  return (
    <div className="stack">
      <div className="page-head"><h1>Users</h1><button className="btn primary" onClick={() => setEditing("new")}>＋ Add user</button></div>
      {isLoading && <Skeleton rows={3} />}
      {data?.map((u) => (
        <button key={u.id} className="card" style={{ textAlign: "left", width: "100%", cursor: "pointer", opacity: u.active ? 1 : 0.55 }} onClick={() => setEditing(u)}>
          <div className="row between"><b>{u.name}</b><span className="tag">{ROLE_LABEL[u.role]}</span></div>
          <div className="muted small">{u.email}{!u.active ? " · disabled" : ""}</div>
          {u.role === "contractor" && <div className="muted tiny">{u.property_ids.length} propert{u.property_ids.length === 1 ? "y" : "ies"}</div>}
        </button>
      ))}
      {editing && (
        <UserSheet user={editing === "new" ? null : editing} properties={props ?? []} onClose={() => setEditing(null)}
          onSaved={(s) => { void qc.invalidateQueries({ queryKey: ["users"] }); setEditing(null); if (s) setSecret(s); }} />
      )}
      {secret && (
        <Sheet title="Temporary password" onClose={() => setSecret(null)}>
          <div className="stack">
            <p className="muted" style={{ margin: 0 }}>Give this to <b>{secret.email}</b>. It's shown only once, and they'll choose their own at first sign-in.</p>
            <code style={{ display: "block", padding: 14, fontSize: "1.2rem", background: "var(--surface-2)", borderRadius: 10, textAlign: "center", userSelect: "all" }}>{secret.password}</code>
            <button className="btn primary block" onClick={() => setSecret(null)}>Done</button>
          </div>
        </Sheet>
      )}
    </div>
  );
}

function UserSheet({ user, properties, onClose, onSaved }: {
  user: AppUser | null; properties: PropertySummary[]; onClose: () => void;
  onSaved: (secret?: { email: string; password: string }) => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(user?.name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [role, setRole] = useState<Role>(user?.role ?? "contractor");
  const [active, setActive] = useState(user?.active ?? true);
  const [propIds, setPropIds] = useState<string[]>(user?.property_ids ?? []);
  const fail = (e: unknown) => toast(e instanceof Error ? e.message : "Failed", "error");

  const save = useMutation({
    mutationFn: async () => {
      if (!user) return api.post<{ temporary_password: string }>("/api/users", { email, name, role, property_ids: role === "contractor" ? propIds : [] });
      await api.patch(`/api/users/${user.id}`, { name, role, active, property_ids: role === "contractor" ? propIds : [] });
      return null;
    },
    onSuccess: (r) => onSaved(r ? { email, password: r.temporary_password } : undefined), onError: fail,
  });
  const reset = useMutation({
    mutationFn: () => api.post<{ temporary_password: string }>(`/api/users/${user!.id}/reset-password`),
    onSuccess: (r) => onSaved({ email: user!.email, password: r.temporary_password }), onError: fail,
  });

  return (
    <Sheet title={user ? "Edit user" : "Add user"} onClose={onClose}>
      <div className="stack">
        <label className="field" style={{ marginBottom: 0 }}><span>Name</span><input type="text" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="field" style={{ marginBottom: 0 }}><span>Email</span><input type="email" value={email} disabled={!!user} onChange={(e) => setEmail(e.target.value)} autoCapitalize="none" /></label>
        <label className="field" style={{ marginBottom: 0 }}><span>Role</span>
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {(Object.keys(ROLE_LABEL) as Role[]).map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
          </select></label>
        <div className="muted small">{ROLE_HELP[role]}</div>
        {role === "contractor" && (
          <div>
            <div className="section-title" style={{ margin: "0 0 6px" }}>Can see</div>
            {properties.map((p) => (
              <label key={p.id} className="row" style={{ padding: "6px 0" }}>
                <input type="checkbox" style={{ width: 20, height: 20, minHeight: 0 }} checked={propIds.includes(p.id)}
                  onChange={(e) => setPropIds((cur) => (e.target.checked ? [...cur, p.id] : cur.filter((x) => x !== p.id)))} />
                {p.property_name}
              </label>
            ))}
          </div>
        )}
        {user && (
          <label className="row"><input type="checkbox" style={{ width: 20, height: 20, minHeight: 0 }} checked={active} onChange={(e) => setActive(e.target.checked)} /> Account active</label>
        )}
        <button className="btn primary block" disabled={!name.trim() || !email.trim() || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : "Save"}</button>
        {user && <button className="btn block" disabled={reset.isPending} onClick={() => reset.mutate()}>Reset password</button>}
      </div>
    </Sheet>
  );
}
