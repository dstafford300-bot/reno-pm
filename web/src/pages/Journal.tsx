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
            {(() => {
              const photos = g.media.filter((m) => m.kind === "photo");
              const others = g.media.filter((m) => m.kind !== "photo");
              return (
                <>
                  {photos.length > 1 && <div className="muted small" style={{ marginTop: 6 }}>📷 {photos.length} photos</div>}
                  {photos.length > 0 && (
                    <div className="photo-grid">
                      {photos.map((m) => <img key={m.src} src={m.src} loading="lazy" alt="Job site" onClick={() => setZoom(m.src)} />)}
                    </div>
                  )}
                  {others.map((m) =>
                    m.kind === "voice" || m.kind === "audio" ? (
                      <audio key={m.src} controls preload="none" src={m.src} style={{ width: "100%", marginTop: 8 }} />
                    ) : m.kind === "video" || m.kind === "video_note" || m.kind === "animation" ? (
                      <video key={m.src} controls preload="metadata" src={m.src} style={{ width: "100%", marginTop: 8, borderRadius: 10 }} />
                    ) : (
                      <a key={m.src} href={m.src} target="_blank" rel="noreferrer" className="btn small" style={{ marginTop: 8 }}>📎 Open file</a>
                    ),
                  )}
                </>
              );
            })()}
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
          <img src={zoom} alt="Job site" />
        </div>
      )}
    </div>
  );
}
