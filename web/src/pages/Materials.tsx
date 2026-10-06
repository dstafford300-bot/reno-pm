import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { useProperty } from "../property";
import { ErrorNote, Skeleton, fmtDate, money2 } from "../components/ui";
import type { MaterialLog } from "../types";

export default function Materials() {
  const { selected } = useProperty();
  const id = selected?.id;
  const { data, isLoading, error } = useQuery({
    queryKey: ["material-logs", id],
    queryFn: () => api.get<{ logs: MaterialLog[]; total: number }>(`/api/properties/${id}/material-logs`),
    enabled: !!id,
  });
  if (!selected) return <div className="empty">No properties yet.</div>;
  if (isLoading) return <Skeleton rows={4} />;
  if (error || !data) return <ErrorNote error={error} />;

  return (
    <div className="stack">
      <div className="page-head"><h1>Materials</h1></div>
      <div className="kpi"><div className="label">Total materials logged</div><div className="value">{money2(data.total)}</div></div>
      {data.logs.length === 0 && <div className="empty">No purchases logged yet. Receipts arrive by email, Telegram photo, or the Budget screen.</div>}
      {data.logs.map((l) => (
        <details key={l.id} className="group" style={{ marginBottom: 0 }}>
          <summary>
            <span className="grow"><div>{l.store} · {money2(l.amount)}</div>
              <div className="muted small" style={{ fontWeight: 400 }}>{fmtDate(l.purchase_date, true)} · via {l.source}{l.unit_name ? ` · 📍 ${l.unit_name}` : ""}</div></span>
          </summary>
          <div style={{ padding: "0 14px 14px" }}>
            {l.items.map((i, k) => <div key={k} className="small" style={{ padding: "3px 0" }}>• {i.description} — {money2(i.cost ?? 0)}</div>)}
            {l.items.length === 0 && l.details && <div className="muted small">{l.details}</div>}
            {l.photo_url && <img src={l.photo_url} alt="Receipt" style={{ maxWidth: 260, borderRadius: 10, marginTop: 8 }} />}
          </div>
        </details>
      ))}
    </div>
  );
}
