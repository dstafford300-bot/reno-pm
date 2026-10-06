import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useAuth } from "../auth";
import { useProperty } from "../property";
import { Confirm, ErrorNote, Sheet, Skeleton, fmtDate, money, money2, useToast } from "../components/ui";
import type { BudgetData, Milestone } from "../types";

interface ParseResult {
  parsed: { store_name: string; total_cost: number; purchase_date?: string; line_items?: { description: string; cost: number }[] };
  property_id: string | null;
  units: { id: string; unit_name: string }[];
  suggested_unit_id: string | null;
  raw_text: string;
}
interface Unassigned { id: string; store: string; amount: number; purchase_date: string; snippet: string }

export default function Budget() {
  const { user } = useAuth();
  const { selected } = useProperty();
  const canEdit = user!.capabilities.edit_budget;
  const id = selected?.id;
  const { data, isLoading, error } = useQuery({
    queryKey: ["budget", id],
    queryFn: () => api.get<BudgetData>(`/api/properties/${id}/budget`),
    enabled: !!id,
  });

  if (!selected) return <div className="empty">No properties yet.</div>;
  if (isLoading) return <Skeleton rows={5} />;
  if (error || !data) return <ErrorNote error={error} />;
  const archived = data.property.archived;
  const editable = canEdit && !archived;

  return (
    <div className="stack">
      <div className="page-head"><h1>Budget</h1>{!canEdit && <span className="tag">View only</span>}</div>
      {archived && <div className="notice">🔒 This project is finished and read-only.</div>}

      <div className="kpis">
        <div className="kpi"><div className="label" title="Labor for most tasks">SOW budget</div><div className="value">{money(data.total_budget)}</div></div>
        <div className="kpi"><div className="label">Materials logged</div><div className="value">{money(data.materials_logged)}</div></div>
        <div className="kpi"><div className="label">Funds released</div><div className="value">{money(data.total_released)}</div></div>
        <div className="kpi"><div className="label">Next draw</div><div className="value">{data.next_draw ? money(data.next_draw.amount) : "—"}</div>
          {data.next_draw && <div className="muted tiny">{data.next_draw.name}</div>}</div>
      </div>

      <MaterialsByUnit data={data} editable={editable} />
      <Milestones data={data} editable={editable} />
      {canEdit && <Flags data={data} editable={editable} />}
      {canEdit && <Receipts propertyId={data.property.id} />}
      <Unassigned canEdit={canEdit} />
    </div>
  );
}

function useRefreshBudget(propertyId: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["budget", propertyId] });
    void qc.invalidateQueries({ queryKey: ["material-logs", propertyId] });
  };
}

function MaterialsByUnit({ data, editable }: { data: BudgetData; editable: boolean }) {
  const toast = useToast();
  const refresh = useRefreshBudget(data.property.id);
  const assign = useMutation({
    mutationFn: ({ logId, unitId }: { logId: string; unitId: string }) => api.post(`/api/material-logs/${logId}/unit`, { unit_id: unitId }),
    onSuccess: () => { toast("Filed under that unit"); refresh(); },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });
  return (
    <div>
      <div className="section-title">Materials by unit</div>
      <p className="muted small" style={{ marginTop: 0 }}>
        Materials are tracked separately from the SOW budget (labor). Only units with labor + materials tasks are compared to a budget.
      </p>
      <div className="stack">
        {data.materials_by_unit.length === 0 && <div className="empty">No purchases filed under a unit yet.</div>}
        {data.materials_by_unit.map((u) => (
          <div key={u.unit_id} className="card">
            <div className="row between">
              <b>{u.unit_name}</b>
              <span><b>{money(u.spent)}</b> <span className="muted small">({u.count} purchase{u.count === 1 ? "" : "s"})</span></span>
            </div>
            {u.labor_plus_materials_budget != null && u.variance != null && (
              <div className="row between small" style={{ marginTop: 6 }}>
                <span className="muted">Budget (labor + materials) {money(u.labor_plus_materials_budget)}</span>
                <span className={`tag ${u.variance < 0 ? "danger" : "ok"}`}>{u.variance < 0 ? `−${money(-u.variance)} over` : `${money(u.variance)} left`}</span>
              </div>
            )}
          </div>
        ))}
      </div>

      {data.needs_unit.length > 0 && (
        <details className="group" style={{ marginTop: 12 }} open>
          <summary>Which unit was this for?<span className="count">{data.needs_unit.length} to file</span></summary>
          <div style={{ padding: "0 14px 12px" }} className="stack">
            <p className="muted small" style={{ margin: "8px 0 0" }}>
              Receipts that don't name a unit stay here — nothing is guessed. Tip: ask the contractor to write the unit on the receipt (e.g. “809 Fred Unit 3 kitchen”).
            </p>
            {data.needs_unit.map((l) => (
              <div key={l.id} className="row wrap" style={{ paddingTop: 8, borderTop: "1px solid var(--border)" }}>
                <div className="grow"><b>{l.store}</b> · {money2(l.amount)}<div className="muted tiny">{fmtDate(l.purchase_date, true)}</div></div>
                {editable && (
                  <select style={{ width: 160 }} value="" aria-label="Unit" onChange={(e) => e.target.value && assign.mutate({ logId: l.id, unitId: e.target.value })}>
                    <option value="">Choose unit…</option>
                    {data.units.map((u) => <option key={u.id} value={u.id}>{u.unit_name}</option>)}
                  </select>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function Milestones({ data, editable }: { data: BudgetData; editable: boolean }) {
  const toast = useToast();
  const refresh = useRefreshBudget(data.property.id);
  const [release, setRelease] = useState<Milestone | null>(null);
  const [remove, setRemove] = useState<Milestone | null>(null);
  const [adding, setAdding] = useState(false);

  const doRelease = useMutation({
    mutationFn: (m: Milestone) => api.post(`/api/milestones/${m.id}/release`, { confirm_not_eligible: !m.eligible }),
    onSuccess: () => { toast("Draw released"); setRelease(null); refresh(); },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });
  const doRemove = useMutation({
    mutationFn: (m: Milestone) => api.del(`/api/milestones/${m.id}`),
    onSuccess: () => { toast("Milestone deleted"); setRemove(null); refresh(); },
  });

  return (
    <div>
      <div className="row between"><div className="section-title" style={{ margin: "18px 2px 8px" }}>Draw milestones</div>
        {editable && <button className="btn small" onClick={() => setAdding(true)}>＋ Add</button>}</div>
      <div className="stack">
        {data.milestones.length === 0 && <div className="empty">No draw milestones yet.</div>}
        {data.milestones.map((m) => {
          const released = m.status === "Released";
          return (
            <div key={m.id} className="card stack">
              <div className="row between"><b>{m.milestone_name}</b><b>{money2(m.draw_amount)}</b></div>
              {m.task_progress.length === 0 && <div className="notice warn small">No tasks linked — nothing to verify, so this can never become eligible.</div>}
              {m.task_progress.map((t) => {
                const met = t.actual_percent >= t.required_percent;
                return <div key={t.line_item_id} className="small">{met ? "✅" : "⏳"} {t.task_name} — {Math.round(t.actual_percent)}% <span className="muted">/ needs {Math.round(t.required_percent)}%</span></div>;
              })}
              {released ? <span className="tag ok" style={{ alignSelf: "flex-start" }}>Released {fmtDate(m.released_at, true)}</span> : (
                <div className="row wrap">
                  <span className={`tag ${m.eligible ? "ok" : "warn"}`}>{m.eligible ? "Ready to release" : "Thresholds not met"}</span>
                  {editable && <>
                    <span className="grow" />
                    <button className="btn small primary" onClick={() => setRelease(m)}>Authorize release</button>
                    <button className="btn small ghost" onClick={() => setRemove(m)}>Delete</button>
                  </>}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {release && (
        <Confirm title="Release this draw?" confirmLabel="Authorize release" busy={doRelease.isPending}
          body={release.eligible ? `${money2(release.draw_amount)} will be released and announced in Telegram.`
            : `Not every linked task has met its required completion yet. Release ${money2(release.draw_amount)} anyway?`}
          onConfirm={() => doRelease.mutate(release)} onClose={() => setRelease(null)} />
      )}
      {remove && <Confirm title="Delete milestone?" confirmLabel="Delete" danger busy={doRemove.isPending} body={`“${remove.milestone_name}” will be removed permanently.`}
        onConfirm={() => doRemove.mutate(remove)} onClose={() => setRemove(null)} />}
      {adding && <AddMilestone data={data} onClose={() => setAdding(false)} onDone={refresh} />}
    </div>
  );
}

function AddMilestone({ data, onClose, onDone }: { data: BudgetData; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("");
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<Record<string, number>>({});
  const shown = useMemo(() => data.tasks.filter((t) => t.label.toLowerCase().includes(filter.toLowerCase())), [data.tasks, filter]);
  const save = useMutation({
    mutationFn: () => api.post(`/api/properties/${data.property.id}/milestones`, {
      milestone_name: name, draw_amount: Number(amount) || 0,
      task_requirements: Object.entries(picked).map(([line_item_id, required_percent]) => ({ line_item_id, required_percent })),
    }),
    onSuccess: () => { toast("Milestone added"); onDone(); onClose(); },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });
  return (
    <Sheet title="Add milestone" onClose={onClose}>
      <div className="stack">
        <label className="field" style={{ marginBottom: 0 }}><span>Name</span><input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Framing complete" /></label>
        <label className="field" style={{ marginBottom: 0 }}><span>Draw amount ($)</span><input type="number" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} /></label>
        <div className="section-title" style={{ margin: 0 }}>Link tasks ({Object.keys(picked).length})</div>
        <input type="text" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search tasks…" />
        <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid var(--border)", borderRadius: 11 }}>
          {shown.map((t) => (
            <label key={t.id} className="row" style={{ padding: "9px 12px", borderBottom: "1px solid var(--border)" }}>
              <input type="checkbox" style={{ width: 20, height: 20, minHeight: 0 }} checked={t.id in picked}
                onChange={(e) => setPicked((p) => { const n = { ...p }; if (e.target.checked) n[t.id] = 100; else delete n[t.id]; return n; })} />
              <span className="grow small">{t.label}</span>
              {t.id in picked && (
                <span className="row" style={{ gap: 4 }}>
                  <input type="number" inputMode="numeric" min={0} max={100} style={{ width: 64, minHeight: 34 }} value={picked[t.id]}
                    onChange={(e) => setPicked((p) => ({ ...p, [t.id]: Math.min(100, Math.max(0, Number(e.target.value))) }))} /><span className="small">%</span>
                </span>
              )}
            </label>
          ))}
        </div>
        <button className="btn primary block" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Saving…" : "Add milestone"}</button>
      </div>
    </Sheet>
  );
}

function Flags({ data, editable }: { data: BudgetData; editable: boolean }) {
  const toast = useToast();
  const refresh = useRefreshBudget(data.property.id);
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const [ids, setIds] = useState<string[]>(() => data.tasks.filter((t) => t.budget_includes_materials).map((t) => t.id));
  const save = useMutation({
    mutationFn: () => api.put(`/api/properties/${data.property.id}/budget/materials-flags`, { line_item_ids: ids }),
    onSuccess: () => { toast("Saved"); refresh(); },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });
  const shown = data.tasks.filter((t) => t.label.toLowerCase().includes(filter.toLowerCase()));
  return (
    <details className="group" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>Tasks whose budget includes materials<span className="count">{ids.length}</span></summary>
      <div style={{ padding: "0 14px 14px" }} className="stack">
        <p className="muted small" style={{ margin: "8px 0 0" }}>Most SOW budgets are labor only. Mark tasks quoted as labor <b>and</b> materials (e.g. a roof) so their material spend is compared to the budget and can trigger overrun alerts.</p>
        <input type="text" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search tasks…" />
        <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid var(--border)", borderRadius: 11 }}>
          {shown.map((t) => (
            <label key={t.id} className="row" style={{ padding: "9px 12px", borderBottom: "1px solid var(--border)" }}>
              <input type="checkbox" style={{ width: 20, height: 20, minHeight: 0 }} disabled={!editable} checked={ids.includes(t.id)}
                onChange={(e) => setIds((cur) => (e.target.checked ? [...cur, t.id] : cur.filter((x) => x !== t.id)))} />
              <span className="small">{t.label}</span>
            </label>
          ))}
        </div>
        <button className="btn primary" disabled={!editable || save.isPending} onClick={() => save.mutate()}>Save</button>
      </div>
    </details>
  );
}

function Receipts({ propertyId }: { propertyId: string }) {
  const toast = useToast();
  const refresh = useRefreshBudget(propertyId);
  const [text, setText] = useState("");
  const [result, setResult] = useState<ParseResult | null>(null);
  const [unit, setUnit] = useState("");
  const [prop, setProp] = useState<string | null>(null);
  const { data: props } = useQuery({ queryKey: ["properties"], queryFn: () => api.get<{ id: string; property_name: string }[]>("/api/properties") });

  const parse = useMutation({
    mutationFn: () => api.post<ParseResult>("/api/receipts/parse", { text }),
    onSuccess: (r) => { setResult(r); setUnit(r.suggested_unit_id ?? ""); setProp(r.property_id); },
    onError: (e) => toast(e instanceof Error ? e.message : "Couldn't read that", "error"),
  });
  const save = useMutation({
    mutationFn: () => api.post("/api/receipts", {
      store_name: result!.parsed.store_name, total_cost: result!.parsed.total_cost, purchase_date: result!.parsed.purchase_date,
      line_items: result!.parsed.line_items ?? [], property_id: prop, unit_id: unit || null, raw_text: result!.raw_text,
    }),
    onSuccess: () => { toast("Receipt saved"); setResult(null); setText(""); refresh(); },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });
  const sync = useMutation({
    mutationFn: () => api.post<{ found: number; processed: number; unassigned: number }>("/api/receipts/sync-email"),
    onSuccess: (r) => { toast(r.found ? `Found ${r.found}, processed ${r.processed}` : "No new receipt emails"); refresh(); },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });

  return (
    <div className="stack">
      <div className="section-title">Receipts</div>
      <div className="card stack">
        <h3>📥 Import a receipt</h3>
        <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste the text of a Home Depot / Lowe's e-receipt" />
        <button className="btn primary" disabled={!text.trim() || parse.isPending} onClick={() => parse.mutate()}>{parse.isPending ? "Reading…" : "Parse receipt"}</button>
        {result && (
          <div className="stack">
            <div><b>{result.parsed.store_name}</b> · {money2(result.parsed.total_cost)} <span className="muted small">{result.parsed.purchase_date}</span></div>
            {(result.parsed.line_items ?? []).slice(0, 8).map((i, k) => <div key={k} className="muted small">• {i.description} — {money2(i.cost)}</div>)}
            <label className="field" style={{ marginBottom: 0 }}><span>Property</span>
              <select value={prop ?? ""} onChange={(e) => { setProp(e.target.value || null); setUnit(""); }}>
                <option value="">(not assigned to a property)</option>
                {(props ?? []).map((p) => <option key={p.id} value={p.id}>{p.property_name}</option>)}
              </select></label>
            {prop === result.property_id && result.units.length > 0 && (
              <label className="field" style={{ marginBottom: 0 }}><span>Unit {result.suggested_unit_id ? "(read from the job name)" : ""}</span>
                <select value={unit} onChange={(e) => setUnit(e.target.value)}>
                  <option value="">(not filed under a unit)</option>
                  {result.units.map((u) => <option key={u.id} value={u.id}>{u.unit_name}</option>)}
                </select></label>
            )}
            <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>Save receipt</button>
          </div>
        )}
      </div>
      <div className="card row between">
        <div><h3>📧 Email receipts</h3><div className="muted small">Checks Gmail for Home Depot / Lowe's receipts — also runs nightly.</div></div>
        <button className="btn" disabled={sync.isPending} onClick={() => sync.mutate()}>{sync.isPending ? "Checking…" : "Check now"}</button>
      </div>
    </div>
  );
}

function Unassigned({ canEdit }: { canEdit: boolean }) {
  const toast = useToast();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["unassigned-materials"], queryFn: () => api.get<Unassigned[]>("/api/materials/unassigned") });
  const { data: props } = useQuery({ queryKey: ["properties"], queryFn: () => api.get<{ id: string; property_name: string }[]>("/api/properties") });
  const assign = useMutation({
    mutationFn: ({ id, propertyId }: { id: string; propertyId: string }) => api.post(`/api/material-logs/${id}/property`, { property_id: propertyId }),
    onSuccess: () => { toast("Assigned"); void qc.invalidateQueries({ queryKey: ["unassigned-materials"] }); void qc.invalidateQueries({ queryKey: ["budget"] }); },
    onError: (e) => toast(e instanceof Error ? e.message : "Failed", "error"),
  });
  if (!data?.length) return null;
  return (
    <div>
      <div className="section-title">Unassigned purchases (no property)</div>
      <div className="stack">
        {data.map((l) => (
          <div key={l.id} className="card row wrap">
            <div className="grow"><b>{l.store}</b> · {money2(l.amount)}<div className="muted tiny">{l.snippet}</div></div>
            {canEdit && (
              <select style={{ width: 180 }} value="" aria-label="Assign to property" onChange={(e) => e.target.value && assign.mutate({ id: l.id, propertyId: e.target.value })}>
                <option value="">Assign to property…</option>
                {(props ?? []).map((p) => <option key={p.id} value={p.id}>{p.property_name}</option>)}
              </select>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
