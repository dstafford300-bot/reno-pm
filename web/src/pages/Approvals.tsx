import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, Skeleton, fmtDateTime, useToast } from "../components/ui";
import type { ChangeRequest } from "../types";

function describe(c: ChangeRequest["changes"]) {
  const parts: string[] = [];
  if (c.status) parts.push(String(c.status) + (c.status === "In Progress" && c.percent_complete != null ? ` (${Math.round(Number(c.percent_complete))}%)` : ""));
  if (c.start_date) parts.push(`start ${c.start_date}`);
  if (c.estimated_end_date) parts.push(`end ${c.estimated_end_date}`);
  return parts.join(" · ");
}

export default function Approvals() {
  const { user } = useAuth();
  const owner = user!.capabilities.approve_changes;
  const toast = useToast();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"pending" | "approved" | "rejected">("pending");

  const { data, isLoading, error } = useQuery({
    queryKey: ["change-requests", tab],
    queryFn: () => api.get<ChangeRequest[]>(`/api/change-requests?status=${tab}`),
    staleTime: 0,
  });
  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) => api.post(`/api/change-requests/${id}/${approve ? "approve" : "reject"}`),
    onSuccess: (_r, v) => {
      toast(v.approve ? "Approved — schedule updated" : "Rejected");
      void qc.invalidateQueries({ queryKey: ["change-requests"] });
      void qc.invalidateQueries({ queryKey: ["schedule"] });
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });

  return (
    <div className="stack">
      <h1>{owner ? "Approvals" : "My requests"}</h1>
      <div className="seg">
        {(["pending", "approved", "rejected"] as const).map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
        ))}
      </div>
      {isLoading && <Skeleton rows={3} />}
      {error && <ErrorNote error={error} />}
      {data?.length === 0 && <div className="empty">Nothing {tab}.</div>}
      {data?.map((r) => (
        <div key={r.id} className="card stack">
          <div className="row between wrap"><b>{r.task_label}</b><span className="muted tiny">{fmtDateTime(r.created_at)}</span></div>
          <div className="muted small">{r.property_name}{owner && r.requested_by ? ` · from ${r.requested_by}` : ""}</div>
          <div><span className="tag">{describe(r.changes)}</span></div>
          {r.note && <div className="notice small">“{r.note}”</div>}
          {owner && r.status === "pending" && (
            <div className="row">
              <button className="btn grow" disabled={decide.isPending} onClick={() => decide.mutate({ id: r.id, approve: false })}>Reject</button>
              <button className="btn primary grow" disabled={decide.isPending} onClick={() => decide.mutate({ id: r.id, approve: true })}>Approve</button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
