import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useAuth } from "../auth";
import { Sheet, StatusPill, fmtDate, useToast } from "./ui";
import type { ScheduleData, Status, Task, TaskEditBody } from "../types";

const STATUSES: Status[] = ["Pending", "In Progress", "Completed"];

function addDays(iso: string, days: number) {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
const dayDiff = (a: string, b: string) =>
  Math.round((new Date(`${b}T00:00:00`).getTime() - new Date(`${a}T00:00:00`).getTime()) / 86_400_000);

export default function TaskEditor({
  task, propertyId, archived, allTasks, onClose,
}: { task: Task; propertyId: string; archived: boolean; allTasks: Task[]; onClose: () => void }) {
  const { user } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const contractor = user!.role === "contractor";

  const [status, setStatus] = useState<Status>(task.status);
  const [percent, setPercent] = useState<number>(
    task.status === "In Progress" && task.percent_complete > 0 && task.percent_complete < 100 ? Math.round(task.percent_complete) : 25,
  );
  const [start, setStart] = useState(task.start_date ?? "");
  const [end, setEnd] = useState(task.estimated_end_date ?? "");
  const [note, setNote] = useState("");
  const [showDates, setShowDates] = useState(false);

  const duration = task.start_date && task.estimated_end_date ? dayDiff(task.start_date, task.estimated_end_date) : 0;
  const deps = allTasks.filter((t) => task.dependencies.includes(t.id));

  const save = useMutation({
    mutationFn: (body: TaskEditBody) => api.patch<{ status: string }>(`/api/tasks/${task.id}`, body),
    onMutate: async (body) => {
      // Optimistic: the schedule updates instantly; rolled back if it fails.
      if (contractor) return {};
      await qc.cancelQueries({ queryKey: ["schedule", propertyId] });
      const prev = qc.getQueryData<ScheduleData>(["schedule", propertyId]);
      if (prev) {
        qc.setQueryData<ScheduleData>(["schedule", propertyId], {
          ...prev,
          tasks: prev.tasks.map((t) => (t.id === task.id ? ({ ...t, ...body } as Task) : t)),
        });
      }
      return { prev };
    },
    onError: (err, _b, ctx) => {
      const prev = (ctx as { prev?: ScheduleData } | undefined)?.prev;
      if (prev) qc.setQueryData(["schedule", propertyId], prev);
      toast(err instanceof Error ? err.message : "Couldn't save", "error");
    },
    onSuccess: (res) => {
      toast(res.status === "pending_approval" ? "Sent for approval" : "Saved");
      onClose();
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["schedule", propertyId] });
      void qc.invalidateQueries({ queryKey: ["property", propertyId] });
      void qc.invalidateQueries({ queryKey: ["properties"] });
    },
  });

  const submit = () => {
    const body: TaskEditBody = { status };
    if (status === "In Progress") body.percent_complete = percent;
    if (start && start !== task.start_date) body.start_date = start;
    if (end && end !== task.estimated_end_date) body.estimated_end_date = end;
    if (contractor && note.trim()) body.note = note.trim();
    save.mutate(body);
  };

  const unchanged =
    status === task.status && (status !== "In Progress" || percent === Math.round(task.percent_complete)) &&
    start === (task.start_date ?? "") && end === (task.estimated_end_date ?? "");

  return (
    <Sheet onClose={onClose}>
      <div className="stack">
        <div>
          <div className="muted tiny">{task.unit_name}{task.cost_group ? ` · ${task.cost_group}` : ""}</div>
          <h2>{task.task_name}</h2>
          <div className="row" style={{ marginTop: 6 }}>
            <StatusPill status={task.status} percent={task.percent_complete} />
            <span className="muted small">{fmtDate(task.start_date)} → {fmtDate(task.estimated_end_date)}</span>
          </div>
        </div>

        {task.awaiting_approval && <div className="notice warn">A change to this task is already awaiting approval.</div>}
        {archived && <div className="notice">This project is finished — read-only.</div>}

        <div className="seg" role="group" aria-label="Status">
          {STATUSES.map((s) => (
            <button key={s} className={`${status === s ? `on ${s}` : ""}`} disabled={archived} onClick={() => setStatus(s)}>{s}</button>
          ))}
        </div>

        {status === "In Progress" && (
          <div>
            <div className="stepper">
              <button className="btn" disabled={archived} onClick={() => setPercent((p) => Math.max(1, p - 5))}>−5</button>
              <div className="big">{percent}%</div>
              <button className="btn" disabled={archived} onClick={() => setPercent((p) => Math.min(99, p + 5))}>+5</button>
            </div>
            <input className="range" type="range" min={1} max={99} step={1} value={percent} disabled={archived}
              onChange={(e) => setPercent(Number(e.target.value))} aria-label="Percent complete" />
          </div>
        )}

        <div>
          <button className="btn ghost small" onClick={() => setShowDates((v) => !v)}>
            {showDates ? "Hide dates" : "Change dates"}
          </button>
          {showDates && (
            <div className="row" style={{ marginTop: 8, alignItems: "flex-end" }}>
              <label className="field grow" style={{ marginBottom: 0 }}><span>Start</span>
                <input type="date" value={start} disabled={archived} onChange={(e) => {
                  setStart(e.target.value);
                  if (e.target.value) setEnd(addDays(e.target.value, duration)); // keep the same duration
                }} />
              </label>
              <label className="field grow" style={{ marginBottom: 0 }}><span>End</span>
                <input type="date" value={end} min={start || undefined} disabled={archived} onChange={(e) => setEnd(e.target.value)} />
              </label>
            </div>
          )}
        </div>

        {contractor && (
          <label className="field" style={{ marginBottom: 0 }}><span>Note for the owner (optional)</span>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Finished early, inspector signed off" />
          </label>
        )}

        {deps.length > 0 && <div className="muted small">🔗 Depends on: {deps.map((d) => d.task_name).join(", ")}</div>}

        <div className="row">
          <button className="btn grow" onClick={onClose}>Close</button>
          <button className="btn primary grow" disabled={archived || unchanged || save.isPending} onClick={submit}>
            {save.isPending ? "Saving…" : contractor ? "Send for approval" : "Save"}
          </button>
        </div>
        {contractor && <div className="muted tiny" style={{ textAlign: "center" }}>Your change shows on the schedule once the owner approves it.</div>}
      </div>
    </Sheet>
  );
}
