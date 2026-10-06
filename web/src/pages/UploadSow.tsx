import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { fmtDate, money, useToast } from "../components/ui";

interface Parsed {
  properties: { property_name: string; address?: string; units: { unit_name: string; line_items: { task_name: string; cost_group?: string; budgeted_cost: number; start_date?: string; estimated_end_date?: string }[] }[] }[];
}

export default function UploadSow() {
  const toast = useToast();
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [parsed, setParsed] = useState<Parsed | null>(null);

  const parse = useMutation({
    mutationFn: () => { const f = new FormData(); f.append("file", file!); return api.post<{ parsed: Parsed }>("/api/sow/parse", f); },
    onSuccess: (r) => setParsed(r.parsed),
    onError: (e) => toast(e instanceof Error ? e.message : "Couldn't read that file", "error"),
  });
  const save = useMutation({
    mutationFn: () => api.post<{ properties: number; units: number; line_items: number }>("/api/sow/import", { parsed }),
    onSuccess: (r) => {
      toast(`Imported ${r.properties} propert${r.properties === 1 ? "y" : "ies"}, ${r.units} unit(s), ${r.line_items} task(s)`);
      setParsed(null); setFile(null);
      void qc.invalidateQueries({ queryKey: ["properties"] });
    },
    onError: (e) => toast(e instanceof Error ? e.message : "Import failed", "error"),
  });

  return (
    <div className="stack">
      <h1>Upload scope of work</h1>
      <p className="muted" style={{ margin: 0 }}>Upload a spreadsheet or a signed contractor PDF. Claude finds the properties, units and line items, then you review before anything is saved.</p>
      <label className="card" style={{ cursor: "pointer", textAlign: "center", borderStyle: "dashed" }}>
        <input type="file" accept=".xlsx,.xls,.csv,.pdf" style={{ display: "none" }} onChange={(e) => { setFile(e.target.files?.[0] ?? null); setParsed(null); }} />
        {file ? <b>{file.name}</b> : <span className="muted">Tap to choose an Excel, CSV or PDF file</span>}
      </label>
      <button className="btn primary" disabled={!file || parse.isPending} onClick={() => parse.mutate()}>
        {parse.isPending ? "Reading… this can take a minute" : "Read file"}
      </button>

      {parsed && (
        <div className="stack">
          <div className="section-title" style={{ margin: 0 }}>Review</div>
          {parsed.properties.map((p, i) => (
            <div key={i} className="card stack">
              <b>{p.property_name}</b>{p.address && <span className="muted small">{p.address}</span>}
              {p.units.map((u, j) => (
                <details key={j} className="group" style={{ marginBottom: 0 }}>
                  <summary>{u.unit_name}<span className="count">{u.line_items.length} tasks · {money(u.line_items.reduce((s, t) => s + (t.budgeted_cost || 0), 0))}</span></summary>
                  {u.line_items.map((t, k) => (
                    <div key={k} className="task" style={{ cursor: "default" }}>
                      <span className="grow"><div className="name">{t.task_name}</div>
                        <div className="meta">{t.cost_group} · {fmtDate(t.start_date)} → {fmtDate(t.estimated_end_date)}</div></span>
                      <b>{money(t.budgeted_cost)}</b>
                    </div>
                  ))}
                </details>
              ))}
            </div>
          ))}
          <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? "Importing…" : "Import to database"}</button>
        </div>
      )}
    </div>
  );
}
