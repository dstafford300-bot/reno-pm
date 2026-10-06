import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useAuth } from "../auth";
import { useProperty } from "../property";
import Gantt from "../components/Gantt";
import TaskEditor from "../components/TaskEditor";
import {
  Confirm, ErrorNote, Icon, Sheet, Skeleton, StatusPill, STATUS_COLOR, fmtDate, useToast,
} from "../components/ui";
import type { ScheduleData, Task } from "../types";

type View = "list" | "gantt";
type Filter = "all" | "active" | "open";

interface AdjustTask { task_name: string; start_date?: string; estimated_end_date?: string; [k: string]: unknown }

export default function Schedule() {
  const { user } = useAuth();
  const { selected } = useProperty();
  const toast = useToast();
  const qc = useQueryClient();
  const caps = user!.capabilities;

  const [view, setView] = useState<View>(() =>
    (localStorage.getItem("reno.view") as View) || (window.matchMedia("(min-width: 900px)").matches ? "gantt" : "list"));
  const [unit, setUnit] = useState("all");
  const [filter, setFilter] = useState<Filter>("all");
  const [editing, setEditing] = useState<Task | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [adjustOpen, setAdjustOpen] = useState(false);

  const id = selected?.id;
  const { data, isLoading, error } = useQuery({
    queryKey: ["schedule", id],
    queryFn: () => api.get<ScheduleData>(`/api/properties/${id}/schedule`),
    enabled: !!id,
  });

  const setViewPersist = (v: View) => { setView(v); localStorage.setItem("reno.view", v); };

  const tasks = useMemo(() => {
    let list = data?.tasks ?? [];
    if (unit !== "all") list = list.filter((t) => t.unit_id === unit);
    if (filter === "active") list = list.filter((t) => t.status === "In Progress");
    if (filter === "open") list = list.filter((t) => t.status !== "Completed");
    return [...list].sort((a, b) => (a.start_date ?? "9").localeCompare(b.start_date ?? "9"));
  }, [data, unit, filter]);

  const groups = useMemo(() => {
    const map = new Map<string, Task[]>();
    for (const t of tasks) {
      const key = t.cost_group || "Uncategorized";
      map.set(key, [...(map.get(key) ?? []), t]);
    }
    return [...map.entries()];
  }, [tasks]);

  const nudges = useMutation({
    mutationFn: () => api.post<{ checked: number; sent: number }>(`/api/properties/${id}/nudges/check`),
    onSuccess: (r) => toast(`Checked ${r.checked} task(s), sent ${r.sent} nudge(s)`),
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });

  if (!selected) return <div className="empty">No properties yet. Upload a scope of work to get started.</div>;
  if (isLoading) return <Skeleton rows={6} />;
  if (error || !data) return <ErrorNote error={error} />;

  const archived = data.property.archived;
  const pendingCount = data.pending_publish_count ?? 0;
  const showUnit = unit === "all" && data.units.length > 1;

  return (
    <div>
      <div className="page-head">
        <h1>Schedule</h1>
        <div className="seg" style={{ minWidth: 150 }}>
          <button className={view === "list" ? "on" : ""} onClick={() => setViewPersist("list")}>List</button>
          <button className={view === "gantt" ? "on" : ""} onClick={() => setViewPersist("gantt")}>Gantt</button>
        </div>
      </div>

      {archived && <div className="notice" style={{ marginBottom: 12 }}>🔒 This project is finished and read-only.</div>}

      {caps.edit_schedule && pendingCount > 0 && !archived && (
        <div className="notice warn row between" style={{ marginBottom: 12 }}>
          <span><b>{pendingCount}</b> change{pendingCount === 1 ? "" : "s"} ready to announce to the Telegram group</span>
          <button className="btn small primary" onClick={() => setPublishOpen(true)}>Review &amp; publish</button>
        </div>
      )}

      <div className="row wrap" style={{ marginBottom: 12 }}>
        <select aria-label="Unit" style={{ flex: "1 1 180px", width: "auto" }} value={unit} onChange={(e) => setUnit(e.target.value)}>
          <option value="all">All units / areas</option>
          {data.units.map((u) => <option key={u.id} value={u.id}>{u.unit_name}</option>)}
        </select>
        <div className="seg" style={{ flex: "1 1 220px" }}>
          {([["all", "All"], ["active", "Active"], ["open", "Not done"]] as [Filter, string][]).map(([k, label]) => (
            <button key={k} className={filter === k ? "on" : ""} onClick={() => setFilter(k)}>{label}</button>
          ))}
        </div>
      </div>

      {tasks.length === 0 ? (
        <div className="empty">No tasks match.</div>
      ) : view === "gantt" ? (
        <Gantt tasks={tasks} showUnit={showUnit} onSelect={setEditing} />
      ) : (
        <div>
          {groups.map(([name, list]) => {
            const done = list.filter((t) => t.status === "Completed").length;
            return (
              <details key={name} className="group" open={filter !== "all" || list.some((t) => t.status === "In Progress")}>
                <summary>
                  {name}
                  <span className="count">{done}/{list.length} done</span>
                  <span className="chev" style={{ width: 18, height: 18 }}>{Icon.chevron}</span>
                </summary>
                {list.map((t) => (
                  <button key={t.id} className="task" onClick={() => setEditing(t)}>
                    <span className="dot" style={{ background: STATUS_COLOR[t.status] }} />
                    <span className="grow">
                      <div className="name">{showUnit && t.unit_name ? `${t.unit_name}: ` : ""}{t.task_name}</div>
                      <div className="meta">{fmtDate(t.start_date)} → {fmtDate(t.estimated_end_date)}{t.awaiting_approval ? " · ⏳ change awaiting approval" : ""}</div>
                    </span>
                    <StatusPill status={t.status} percent={t.percent_complete} />
                  </button>
                ))}
              </details>
            );
          })}
        </div>
      )}

      {caps.edit_schedule && !archived && (
        <div className="row wrap" style={{ marginTop: 16 }}>
          <button className="btn" onClick={() => setAdjustOpen(true)}>🔧 Adjust timeline with AI</button>
          <button className="btn" disabled={nudges.isPending} onClick={() => nudges.mutate()}>🔔 Check trade look-ahead nudges</button>
        </div>
      )}

      {editing && (
        <TaskEditor task={editing} propertyId={selected.id} archived={archived} allTasks={data.tasks} onClose={() => setEditing(null)} />
      )}
      {publishOpen && <PublishSheet propertyId={selected.id} onClose={() => setPublishOpen(false)} />}
      {adjustOpen && (
        <AdjustSheet propertyId={selected.id} current={data.tasks} onClose={() => setAdjustOpen(false)}
          onApplied={() => void qc.invalidateQueries({ queryKey: ["schedule", selected.id] })} />
      )}
    </div>
  );
}

function PublishSheet({ propertyId, onClose }: { propertyId: string; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["pending-publish", propertyId],
    queryFn: () => api.get<{ changes: { id: string; description: string }[] }>(`/api/properties/${propertyId}/pending-publish`),
    staleTime: 0,
  });
  const done = () => {
    void qc.invalidateQueries({ queryKey: ["schedule", propertyId] });
    void qc.invalidateQueries({ queryKey: ["pending-publish", propertyId] });
  };
  const publish = useMutation({
    mutationFn: () => api.post(`/api/properties/${propertyId}/publish`),
    onSuccess: () => { toast("Published to Telegram"); done(); onClose(); },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });
  const discard = useMutation({
    mutationFn: () => api.del(`/api/properties/${propertyId}/pending-publish`),
    onSuccess: () => { toast("Discarded"); done(); onClose(); },
  });
  return (
    <Sheet title="Announce to Telegram" onClose={onClose}>
      <div className="stack">
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {(data?.changes ?? []).map((c) => <li key={c.id} style={{ marginBottom: 4 }}>{c.description}</li>)}
        </ul>
        <div className="row">
          <button className="btn grow" disabled={discard.isPending} onClick={() => discard.mutate()}>Discard</button>
          <button className="btn primary grow" disabled={publish.isPending || !data?.changes.length} onClick={() => publish.mutate()}>
            {publish.isPending ? "Sending…" : "Publish"}
          </button>
        </div>
      </div>
    </Sheet>
  );
}

function AdjustSheet({
  propertyId, current, onClose, onApplied,
}: { propertyId: string; current: Task[]; onClose: () => void; onApplied: () => void }) {
  const toast = useToast();
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<AdjustTask[] | null>(null);
  const [confirm, setConfirm] = useState(false);

  const run = useMutation({
    mutationFn: () => api.post<{ tasks: AdjustTask[] }>(`/api/properties/${propertyId}/schedule/adjust/preview`, { instruction }),
    onSuccess: (r) => setPreview(r.tasks),
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });
  const apply = useMutation({
    mutationFn: () => api.post<{ updated: number; inserted: number; deleted: number }>(
      `/api/properties/${propertyId}/schedule/adjust/apply`, { instruction, tasks: preview }),
    onSuccess: (r) => { toast(`Updated ${r.updated}, added ${r.inserted}, removed ${r.deleted}`); onApplied(); onClose(); },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });

  // Show only what would actually change, not the whole schedule.
  const diff = useMemo(() => {
    if (!preview) return [];
    const byLabel = new Map(current.map((t) => [`${t.unit_name}: ${t.task_name}`, t]));
    const seen = new Set<string>();
    const rows: { label: string; text: string }[] = [];
    for (const p of preview) {
      const old = byLabel.get(p.task_name);
      if (old) seen.add(p.task_name);
      if (!old) rows.push({ label: p.task_name, text: `new · ${fmtDate(p.start_date)} → ${fmtDate(p.estimated_end_date)}` });
      else if (old.start_date !== p.start_date || old.estimated_end_date !== p.estimated_end_date)
        rows.push({ label: p.task_name, text: `${fmtDate(old.start_date)}–${fmtDate(old.estimated_end_date)}  →  ${fmtDate(p.start_date)}–${fmtDate(p.estimated_end_date)}` });
    }
    for (const [label] of byLabel) if (!seen.has(label)) rows.push({ label, text: "removed" });
    return rows;
  }, [preview, current]);

  return (
    <Sheet title="Adjust timeline with AI" onClose={onClose}>
      <div className="stack">
        <label className="field" style={{ marginBottom: 0 }}><span>What should change?</span>
          <textarea value={instruction} onChange={(e) => { setInstruction(e.target.value); setPreview(null); }}
            placeholder="e.g. Push framing back by 4 days" />
        </label>
        <button className="btn block" disabled={!instruction.trim() || run.isPending} onClick={() => run.mutate()}>
          {run.isPending ? "Asking Claude…" : "Preview change"}
        </button>
        {preview && (
          <>
            {diff.length === 0 ? <div className="notice">That wouldn't change any dates.</div> : (
              <div className="stack">
                <div className="section-title" style={{ margin: 0 }}>{diff.length} change{diff.length === 1 ? "" : "s"}</div>
                {diff.map((r) => (
                  <div key={r.label} className="card" style={{ padding: 10 }}>
                    <div style={{ fontWeight: 600 }}>{r.label}</div>
                    <div className="muted small">{r.text}</div>
                  </div>
                ))}
              </div>
            )}
            <button className="btn primary block" disabled={diff.length === 0 || apply.isPending} onClick={() => setConfirm(true)}>
              Apply changes
            </button>
          </>
        )}
      </div>
      {confirm && (
        <Confirm title="Apply these changes?" confirmLabel="Apply" busy={apply.isPending}
          body="This updates the live schedule and notifies the Telegram group." onConfirm={() => apply.mutate()} onClose={() => setConfirm(false)} />
      )}
    </Sheet>
  );
}
