import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { PropertySummary } from "./types";

interface Ctx {
  properties: PropertySummary[];
  loading: boolean;
  selected: PropertySummary | null;
  select: (id: string) => void;
}
const PropertyCtx = createContext<Ctx>(null as unknown as Ctx);
export const useProperty = () => useContext(PropertyCtx);

const KEY = "reno.property";

export function PropertyProvider({ children }: { children: ReactNode }) {
  const { data, isLoading } = useQuery({
    queryKey: ["properties"],
    queryFn: () => api.get<PropertySummary[]>("/api/properties"),
    staleTime: 60_000,
  });
  const [id, setId] = useState<string | null>(() => {
    try { return localStorage.getItem(KEY); } catch { return null; }
  });
  const properties = useMemo(() => data ?? [], [data]);

  const selected = useMemo(
    () => properties.find((p) => p.id === id) ?? properties.find((p) => !p.archived) ?? properties[0] ?? null,
    [properties, id],
  );
  useEffect(() => {
    if (selected && selected.id !== id) {
      try { localStorage.setItem(KEY, selected.id); } catch { /* private mode */ }
    }
  }, [selected, id]);

  const select = (next: string) => {
    setId(next);
    try { localStorage.setItem(KEY, next); } catch { /* private mode */ }
  };
  return <PropertyCtx.Provider value={{ properties, loading: isLoading, selected, select }}>{children}</PropertyCtx.Provider>;
}
