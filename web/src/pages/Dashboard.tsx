import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useAuth } from "../auth";
import { useProperty } from "../property";
import { Confirm, ErrorNote, Icon, Skeleton, StatusPill, fmtDate, money, useToast } from "../components/ui";
import type { PropertyDetail } from "../types";

interface DraftTask {
  task_name: string; cost_group?: string; start_date?: string; estimated_end_date?: string;
  budgeted_cost?: number; depends_on?: string[]; [k: string]: unknown;
}

export default function Dashboard() {
  const { user } = useAuth();
  const { properties, selected, select, loading } = useProperty();
  const nav = useNavigate();
  const caps = user!.capabilities;

  const detail = useQuery({
    queryKey: ["property", selected?.id],
    queryFn: () => api.get<PropertyDetail>(`/api/properties/${selected!.id}`),
    enabled: !!selected,
  });

  if (loading) return <Skeleton rows={3} />;
  if (!properties.length) {
    return (
      <div className="empty">
        <p>No properties yet.</p>
        {caps.upload_sow && <button className="btn primary" onClick={() => nav("/sow")}>Upload a scope of work</button>}
      </div>
    );
  }

  return (
    <div className="stack">
      <h1>Dashboard</h1>

      <div>
        <div className="section-title" style={{ marginTop: 0 }}>Properties</div>
        <div className="stack">
          {properties.map((p) => (
            <button key={p.id} className="card" style={{ width: "100%", textAlign: "left", cursor: "pointer", borderColor: p.id === selected?.id ? "var(--primary)" : undefined }}
              onClick={() => select(p.id)}>
              <div className="row between">
                <b>{p.property_name}</b>
                {p.archived ? <span className="tag">Finished</span> : <span className="muted small">{Math.round(p.progress_percent)}%</span>}
              </div>
              <div className="bar" style={{ margin: "8px 0 6px" }}><span style={{ width: `${p.progress_percent}%` }} /></div>
              <div className="muted tiny">{p.task_count} tasks{p.total_budget != null ? ` · ${money(p.total_budget)} budget` : ""}</div>
            </button>
          ))}
        </div>
      </div>

      {detail.isLoading && <Skeleton rows={3} />}
      {detail.error && <ErrorNote error={detail.error} />}
      {detail.data && <PropertyPanel d={detail.data} />}
    </div>
  );
}

function PropertyPanel({ d }: { d: PropertyDetail }) {
  const { user } = useAuth();
  const caps = user!.capabilities;
  const nav = useNavigate();

  return (
    <div className="stack">
      <div className="section-title">{d.property_name}</div>
      {d.archived && <div className="notice">🔒 This project is finished and read-only. Everything is still viewable.</div>}

      <div className="kpis">
        {d.total_budget != null && <div className="kpi"><div className="label">Budget</div><div className="value">{money(d.total_budget)}</div></div>}
        <div className="kpi"><div className="label">Progress</div><div className="value">{Math.round(d.progress_percent)}%</div></div>
        <div className="kpi"><div className="label">Living units</div><div className="value">{d.living_units}</div></div>
        <div className="kpi"><div className="label">Tasks</div><div className="value">{d.units.reduce((n, u) => n + u.tasks.length, 0)}</div></div>
      </div>

      {!d.has_schedule ? (
        caps.edit_schedule && !d.archived ? <TimelineBuilder id={d.id} name={d.property_name} /> : <div className="empty">No schedule yet.</div>
      ) : (
        <div>
          {d.units.filter((u) => u.tasks.length).map((u) => {
            const done = u.tasks.filter((t) => t.status === "Completed").length;
            return (
              <details key={u.id} className="group">
                <summary>{u.unit_name}<span className="count">{done}/{u.tasks.length} done</span>
                  <span className="chev" style={{ width: 18, height: 18 }}>{Icon.chevron}</span></summary>
                {u.tasks.map((t) => (
                  <div key={t.id} className="task" style={{ cursor: "default" }}>
                    <span className="grow">
                      <div className="name">{t.task_name}</div>
                      <div className="meta">{t.cost_group ?? "Uncategorized"}{t.budgeted_cost != null ? ` · ${money(t.budgeted_cost)}` : ""}</div>
                    </span>
                    <StatusPill status={t.status} percent={t.percent_complete} />
                  </div>
                ))}
              </details>
            );
          })}
          <button className="btn block" onClick={() => nav("/schedule")}>Open schedule</button>
        </div>
      )}

      {caps.edit_schedule && <TelegramCard d={d} />}
      {caps.manage_projects && <ProjectStatus d={d} />}
      {caps.manage_users && <DigestCard />}
    </div>
  );
}

function TelegramCard({ d }: { d: PropertyDetail }) {
  const { user } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const refresh = useMutation({
    mutationFn: () => api.post(`/api/properties/${d.id}/telegram/refresh-link`),
    onSuccess: () => toast("Re-sent and re-pinned the portal link"),
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });
  const unlink = useMutation({
    mutationFn: () => api.post(`/api/properties/${d.id}/telegram/unlink`),
    onSuccess: () => { toast("Unlinked"); setConfirm(false); void qc.invalidateQueries({ queryKey: ["property", d.id] }); },
  });
  return (
    <div className="card stack">
      <h3>🔗 Telegram group</h3>
      {d.telegram_linked ? (
        <>
          <div className="notice">Linked — Jeeves posts schedule updates and logs the group's journal here.</div>
          <div className="row wrap">
            <button className="btn" disabled={refresh.isPending} onClick={() => refresh.mutate()}>🔄 Refresh pinned link</button>
            {user!.role === "owner" && <button className="btn ghost" onClick={() => setConfirm(true)}>Unlink</button>}
          </div>
        </>
      ) : (
        <div>
          <p className="muted small" style={{ marginTop: 0 }}>Add Jeeves to your Telegram group, then send this exact message in it — it links automatically within seconds:</p>
          <code style={{ display: "block", padding: 10, background: "var(--surface-2)", borderRadius: 10, wordBreak: "break-word" }}>{d.telegram_phrase}</code>
        </div>
      )}
      {confirm && <Confirm title="Unlink this group?" body="Jeeves will stop posting here and logging its messages." confirmLabel="Unlink" danger onConfirm={() => unlink.mutate()} onClose={() => setConfirm(false)} />}
    </div>
  );
}

function DigestCard() {
  const toast = useToast();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["pm-digest"], queryFn: () => api.get<{ linked: boolean; phrase: string }>("/api/pm-digest") });
  const test = useMutation({
    mutationFn: () => api.post("/api/pm-digest/test"),
    onSuccess: () => toast("Sent — check your Telegram DM with Jeeves"),
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });
  const unlink = useMutation({
    mutationFn: () => api.del("/api/pm-digest"),
    onSuccess: () => { toast("Unlinked"); void qc.invalidateQueries({ queryKey: ["pm-digest"] }); },
  });
  if (!data) return null;
  return (
    <div className="card stack">
      <h3>🔔 Daily PM summary</h3>
      {data.linked ? (
        <div className="row wrap">
          <span className="notice grow">Linked — Jeeves DMs you a daily summary.</span>
          <button className="btn" disabled={test.isPending} onClick={() => test.mutate()}>Send test now</button>
          <button className="btn ghost" onClick={() => unlink.mutate()}>Unlink</button>
        </div>
      ) : (
        <div>
          <p className="muted small" style={{ marginTop: 0 }}>Open a direct message with Jeeves (not a group) and send:</p>
          <code style={{ display: "block", padding: 10, background: "var(--surface-2)", borderRadius: 10 }}>{data.phrase}</code>
        </div>
      )}
    </div>
  );
}

function ProjectStatus({ d }: { d: PropertyDetail }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [typed, setTyped] = useState("");
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["properties"] });
    void qc.invalidateQueries({ queryKey: ["property", d.id] });
    void qc.invalidateQueries({ queryKey: ["schedule", d.id] });
  };
  const archive = useMutation({
    mutationFn: (archived: boolean) => api.post(`/api/properties/${d.id}/archive`, { archived }),
    onSuccess: (_r, archived) => { toast(archived ? "Marked finished — now read-only" : "Reopened"); refresh(); },
  });
  const del = useMutation({
    mutationFn: () => api.del(`/api/properties/${d.id}`, { confirm_name: typed }),
    onSuccess: () => { toast("Permanently deleted"); setConfirmDelete(false); localStorage.removeItem("reno.property"); refresh(); },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });
  return (
    <div className="card stack">
      <h3>📁 Project status</h3>
      <p className="muted small" style={{ margin: 0 }}>
        Finishing a project locks it from edits everywhere but keeps all data viewable. You can reopen it any time.
      </p>
      {d.archived
        ? <button className="btn block" onClick={() => archive.mutate(false)}>↩️ Reopen project</button>
        : <button className="btn block" onClick={() => archive.mutate(true)}>✅ Mark project finished</button>}
      <hr style={{ width: "100%", border: 0, borderTop: "1px solid var(--border)" }} />
      <p className="muted small" style={{ margin: 0 }}>⚠️ Permanently deletes this property and everything under it. This cannot be undone.</p>
      <button className="btn danger block" onClick={() => { setTyped(""); setConfirmDelete(true); }}>🗑️ Delete project permanently</button>
      {confirmDelete && (
        <Confirm title="Delete permanently?" confirmLabel="Delete forever" danger busy={del.isPending}
          body={<div className="stack"><span>Type <b>{d.property_name}</b> to confirm.</span>
            <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Confirm property name" /></div>}
          onConfirm={() => typed === d.property_name && del.mutate()} onClose={() => setConfirmDelete(false)} />
      )}
    </div>
  );
}

function TimelineBuilder({ id, name }: { id: string; name: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [notes, setNotes] = useState("");
  const [tasks, setTasks] = useState<DraftTask[] | null>(null);
  const [tweak, setTweak] = useState("");
  const fail = (e: unknown) => toast(e instanceof Error ? e.message : "Failed", "error");

  const draft = useMutation({
    mutationFn: () => api.post<{ tasks: DraftTask[] }>(`/api/properties/${id}/timeline/draft`, { notes }),
    onSuccess: (r) => setTasks(r.tasks), onError: fail,
  });
  const refine = useMutation({
    mutationFn: () => api.post<{ tasks: DraftTask[] }>(`/api/properties/${id}/timeline/refine`, { tasks, instruction: tweak }),
    onSuccess: (r) => { setTasks(r.tasks); setTweak(""); }, onError: fail,
  });
  const publish = useMutation({
    mutationFn: () => api.post<{ line_items: number }>(`/api/properties/${id}/timeline/publish`, { tasks }),
    onSuccess: (r) => {
      toast(`Published ${r.line_items} tasks`);
      void qc.invalidateQueries({ queryKey: ["property", id] });
      void qc.invalidateQueries({ queryKey: ["schedule", id] });
      void qc.invalidateQueries({ queryKey: ["properties"] });
    }, onError: fail,
  });

  return (
    <div className="card stack">
      <h3>🧪 Timeline builder</h3>
      <p className="muted small" style={{ margin: 0 }}>No schedule yet for <b>{name}</b>. Draft one from rough notes before it goes live.</p>
      <textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="e.g. Full gut reno, 2 units. Demo first. Kitchen cabinets are custom order, 3 week lead time…" style={{ minHeight: 140 }} />
      <button className="btn primary" disabled={!notes.trim() || draft.isPending} onClick={() => draft.mutate()}>
        {draft.isPending ? "Drafting…" : "Generate draft timeline"}
      </button>
      {tasks && (
        <>
          <div className="section-title" style={{ margin: 0 }}>Draft · {tasks.length} tasks</div>
          <div className="stack">
            {tasks.map((t, i) => (
              <div key={i} className="card" style={{ padding: 10 }}>
                <div style={{ fontWeight: 600 }}>{t.task_name}</div>
                <div className="muted small">{t.cost_group} · {fmtDate(t.start_date)} → {fmtDate(t.estimated_end_date)}{t.budgeted_cost ? ` · ${money(t.budgeted_cost)}` : ""}</div>
              </div>
            ))}
          </div>
          <div className="row">
            <input type="text" className="grow" value={tweak} onChange={(e) => setTweak(e.target.value)} placeholder="Tweak, e.g. add a 2-day buffer after framing" />
            <button className="btn" disabled={!tweak.trim() || refine.isPending} onClick={() => refine.mutate()}>Update</button>
          </div>
          <button className="btn primary block" disabled={publish.isPending} onClick={() => publish.mutate()}>
            {publish.isPending ? "Publishing…" : "🚀 Publish timeline"}
          </button>
        </>
      )}
    </div>
  );
}
