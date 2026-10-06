import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { PropertyProvider } from "./property";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Schedule from "./pages/Schedule";
import Account from "./pages/Account";

// Rarely-used screens load on demand so the first paint stays fast.
const Budget = lazy(() => import("./pages/Budget"));
const Journal = lazy(() => import("./pages/Journal"));
const Materials = lazy(() => import("./pages/Materials"));
const UploadSow = lazy(() => import("./pages/UploadSow"));
const Approvals = lazy(() => import("./pages/Approvals"));
const Users = lazy(() => import("./pages/Users"));
const Client = lazy(() => import("./pages/Client"));

function Guard({ cap, children }: { cap?: keyof import("./types").Capabilities; children: ReactNode }) {
  const { user } = useAuth();
  if (cap && !user!.capabilities[cap]) return <Navigate to="/" replace />;
  return <>{children}</>;
}

export default function App() {
  const { user, loading } = useAuth();
  const { pathname } = useLocation();

  // Shared read-only link (Telegram portal / demo) — no sign-in.
  if (pathname === "/client" || new URLSearchParams(window.location.search).get("view") === "client") {
    return <Suspense fallback={<Spinner />}><Client /></Suspense>;
  }
  if (loading) return <Spinner />;
  if (!user) return <Login />;
  if (user.must_change_password && pathname !== "/account") return <Navigate to="/account" replace />;

  return (
    <PropertyProvider>
      <Suspense fallback={<Spinner />}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="schedule" element={<Schedule />} />
            <Route path="budget" element={<Guard cap="view_budget"><Budget /></Guard>} />
            <Route path="journal" element={<Guard cap="view_journal"><Journal /></Guard>} />
            <Route path="materials" element={<Guard cap="view_materials"><Materials /></Guard>} />
            <Route path="sow" element={<Guard cap="upload_sow"><UploadSow /></Guard>} />
            <Route path="approvals" element={<Approvals />} />
            <Route path="users" element={<Guard cap="manage_users"><Users /></Guard>} />
            <Route path="account" element={<Account />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </PropertyProvider>
  );
}
