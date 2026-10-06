import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import Gantt from "../components/Gantt";
import { ErrorNote, Skeleton, StatusPill, STATUS_COLOR, fmtDate } from "../components/ui";
import type { ScheduleData } from "../types";

/** Read-only schedule for a shared link (?token=…&property_id=…). No sign-in, no costs. */
export default function Client() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("token") ?? "";
  const propertyId = params.get("property_id") ?? "";
  const [view, setView] = useState<"list" | "gantt">(window.matchMedia("(min-width: 900px)").matches ? "gantt" : "list");

  const { data, isLoading, error } = useQuery({
    queryKey: ["public-schedule", propertyId],
    queryFn: () => api.get<ScheduleData>(`/api/public/schedule?token=${encodeURIComponent(token)}&property_id=${encodeURIComponent(propertyId)}`),
    enabled: !!token && !!propertyId,
    refetchInterval: 120_000,
  });
  const tasks = useMemo(() => [...(data?.tasks ?? [])].sort((a, b) => (a.start_date ?? "9").localeCompare(b.start_date ?? "9")), [data]);

  return (
    <div className="content" style={{ paddingBottom: 40 }}>
      <div className="page-head">
        <div><h1>📅 Live schedule</h1>{data && <div className="muted">{data.property.property_name}</div>}</div>
        <div className="seg" style={{ minWidth: 150 }}>
          <button className={view === "list" ? "on" : ""} onClick={() => setView("list")}>List</button>
          <button className={view === "gantt" ? "on" : ""} onClick={() => setView("gantt")}>Gantt</button>
        </div>
      </div>
      {(!token || !propertyId) && <div className="notice warn">This link is missing something — ask for an updated one.</div>}
      {isLoading && <Skeleton rows={5} />}
      {error && <ErrorNote error={error} />}
      {data && (view === "gantt" ? <Gantt tasks={tasks} showUnit onSelect={() => undefined} /> : (
        <div className="group">
          {tasks.map((t) => (
            <div key={t.id} className="task" style={{ cursor: "default" }}>
              <span className="dot" style={{ background: STATUS_COLOR[t.status] }} />
              <span className="grow"><div className="name">{t.unit_name}: {t.task_name}</div>
                <div className="meta">{fmtDate(t.start_date)} → {fmtDate(t.estimated_end_date)}</div></span>
              <StatusPill status={t.status} percent={t.percent_complete} />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
