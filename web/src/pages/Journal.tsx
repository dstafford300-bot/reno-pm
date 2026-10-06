import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useAuth } from "../auth";
import { useProperty } from "../property";
import { ErrorNote, Skeleton, fmtDateTime, useToast } from "../components/ui";
import type { JournalData } from "../types";

export default function Journal() {
  const { user } = useAuth();
  const { selected } = useProperty();
  const toast = useToast();
  const qc = useQueryClient();
  const [zoom, setZoom] = useState<string | null>(null);
  const id = selected?.id;
  const canEdit = user!.capabilities.edit_schedule;

  const { data, isLoading, error } = useQuery({
    queryKey: ["journal", id],
    queryFn: () => api.get<JournalData>(`/api/properties/${id}/journal`),
    enabled: !!id,
  });
  const link = useMutation({
    mutationFn: ({ ids, taskId }: { ids: string[]; taskId: string | null }) => api.put("/api/journal/link", { entry_ids: ids, line_item_id: taskId }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["journal", id] }),
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });

  if (!selected) return <div className="empty">No properties yet.</div>;
  if (isLoading) return <Skeleton rows={5} />;
  if (error || !data) return <ErrorNote error={error} />;

  return (
    <div>
      <div className="page-head"><h1>Journal</h1></div>
      <p className="muted small" style={{ marginTop: 0 }}>Only project facts and photos from the Telegram group are kept — questions and chatter are filtered out. Syncs in real time.</p>
      {!data.property.telegram_linked && <div className="notice warn" style={{ marginBottom: 12 }}>No Telegram group is linked yet — link one on the Dashboard.</div>}
      {data.groups.length === 0 && <div className="empty">No journal entries yet.</div>}
      <div className="stack">
        {data.groups.map((g) => (
          <div key={g.ids[0]} className="card">
            <div className="muted small">{fmtDateTime(g.posted_at)} · {g.author_name}</div>
            {g.message_text && <p style={{ margin: "6px 0 0", whiteSpace: "pre-wrap" }}>{g.message_text}</p>}
            {g.photo_file_ids.length > 0 && (
              <>
                {g.photo_file_ids.length > 1 && <div className="muted small" style={{ marginTop: 6 }}>📷 {g.photo_file_ids.length} photos</div>}
                <div className="photo-grid">
                  {g.photo_file_ids.map((f) => (
                    <img key={f} src={`/api/photos/${encodeURIComponent(f)}`} loading="lazy" alt="Job site" onClick={() => setZoom(f)} />
                  ))}
                </div>
              </>
            )}
            {canEdit && !data.property.archived && (
              <select style={{ marginTop: 10 }} aria-label="Linked task" value={g.linked_line_item_id ?? ""}
                onChange={(e) => link.mutate({ ids: g.ids, taskId: e.target.value || null })}>
                <option value="">Link to a task…</option>
                {data.tasks.map((t) => <option key={t.id} value={t.id}>{t.task_name}</option>)}
              </select>
            )}
          </div>
        ))}
      </div>
      {zoom && (
        <div className="lightbox" onClick={() => setZoom(null)} role="dialog" aria-label="Photo">
          <img src={`/api/photos/${encodeURIComponent(zoom)}`} alt="Job site" />
        </div>
      )}
    </div>
  );
}
