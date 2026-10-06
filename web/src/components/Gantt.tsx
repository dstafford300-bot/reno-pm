import { useMemo, useState } from "react";
import { STATUS_COLOR, fmtDate } from "./ui";
import type { Task } from "../types";

const DAY = 86_400_000;
const toTime = (iso: string) => new Date(`${iso}T00:00:00`).getTime();

export default function Gantt({
  tasks, showUnit, onSelect,
}: { tasks: Task[]; showUnit: boolean; onSelect: (t: Task) => void }) {
  const wide = typeof window !== "undefined" && window.matchMedia("(min-width: 900px)").matches;
  const [px, setPx] = useState(wide ? 14 : 9);
  const labelW = wide ? 280 : 150;

  const dated = useMemo(
    () => tasks.filter((t) => t.start_date && t.estimated_end_date)
      .sort((a, b) => toTime(a.start_date!) - toTime(b.start_date!)),
    [tasks],
  );
  if (!dated.length) return <div className="empty">No tasks with dates to chart.</div>;

  const min = Math.min(...dated.map((t) => toTime(t.start_date!))) - 2 * DAY;
  const max = Math.max(...dated.map((t) => toTime(t.estimated_end_date!))) + 3 * DAY;
  const days = Math.ceil((max - min) / DAY);
  const width = days * px;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const todayX = ((today.getTime() - min) / DAY) * px;

  // One tick per Monday; the label shows the month on the first of each.
  const ticks: { x: number; label: string }[] = [];
  for (let d = new Date(min); d.getTime() <= max; d.setDate(d.getDate() + 1)) {
    if (d.getDay() === 1) {
      const label = d.getDate() <= 7 ? d.toLocaleDateString("en-US", { month: "short", day: "numeric" }) : String(d.getDate());
      ticks.push({ x: ((d.getTime() - min) / DAY) * px, label });
    }
  }

  return (
    <div>
      <div className="row between" style={{ marginBottom: 8 }}>
        <span className="muted small">{dated.length} tasks</span>
        <div className="row">
          <button className="btn small" aria-label="Zoom out" onClick={() => setPx((p) => Math.max(4, Math.round(p * 0.7)))}>−</button>
          <button className="btn small" aria-label="Zoom in" onClick={() => setPx((p) => Math.min(40, Math.round(p * 1.4)))}>+</button>
        </div>
      </div>
      <div className="gantt">
        <div className="gantt-inner" style={{ width: labelW + width }}>
          <div className="gantt-head">
            <div className="gantt-label" style={{ width: labelW }}>Task</div>
            <div className="gantt-track" style={{ width }}>
              {ticks.map((t, i) => <div key={i} className="gantt-tick" style={{ left: t.x }}>{t.label}</div>)}
            </div>
          </div>
          {dated.map((t) => {
            const x = ((toTime(t.start_date!) - min) / DAY) * px;
            const w = Math.max(6, ((toTime(t.estimated_end_date!) - toTime(t.start_date!)) / DAY + 1) * px);
            const name = showUnit && t.unit_name ? `${t.unit_name}: ${t.task_name}` : t.task_name;
            return (
              <div key={t.id} className="gantt-row" onClick={() => onSelect(t)} role="button" tabIndex={0}
                onKeyDown={(e) => e.key === "Enter" && onSelect(t)}
                title={`${name} — ${t.status} · ${fmtDate(t.start_date)} → ${fmtDate(t.estimated_end_date)}`}>
                <div className="gantt-label" style={{ width: labelW }}>{name}</div>
                <div className="gantt-track" style={{ width }}>
                  {ticks.map((tk, i) => <div key={i} className="gantt-tick" style={{ left: tk.x, borderLeftStyle: "dotted" }} />)}
                  <div className="gantt-bar" style={{ left: x, width: w, background: STATUS_COLOR[t.status] }}>
                    {t.status === "In Progress" && <i style={{ width: `${t.percent_complete}%` }} />}
                  </div>
                </div>
              </div>
            );
          })}
          {todayX > 0 && todayX < width && <div className="gantt-today" style={{ left: labelW + todayX }} />}
        </div>
      </div>
    </div>
  );
}
