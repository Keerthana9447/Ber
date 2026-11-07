import type { ReactElement } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./lib/auth";
import { MODULES } from "./lib/modules";
import { Layout } from "./components/Layout";
import { LoadingState } from "./components/ui";
import { LoginPage } from "./pages/LoginPage";
import { DashboardPage } from "./pages/DashboardPage";
import { ModuleListPage } from "./pages/ModuleListPage";
import { ProductsPage } from "./pages/ProductsPage";
import { InventoryPage } from "./pages/InventoryPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ProfilePage } from "./pages/ProfilePage";
import { ForbiddenPage, NotFoundPage } from "./pages/Misc";

function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") return <LoadingState label="Restoring session…" />;
  if (status === "anon") return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <Outlet />;
}

function RequirePermission({ permission, children }: { permission?: string; children: ReactElement }) {
  const { can } = useAuth();
  return can(permission) ? children : <ForbiddenPage />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route
            index
            element={
              <RequirePermission permission="dashboard:read">
                <DashboardPage />
              </RequirePermission>
            }
          />
          {MODULES.filter((m) => !m.special).map((m) => (
            <Route
              key={m.key}
              path={m.path.slice(1)}
              element={
                <RequirePermission permission={m.permission}>
                  {m.key === "products" ? (
                    <ProductsPage />
                  ) : m.key === "inventory" ? (
                    <InventoryPage />
                  ) : (
                    <ModuleListPage module={m} />
                  )}
                </RequirePermission>
              }
            />
          ))}
          <Route
            path="settings"
            element={
              <RequirePermission permission="settings:read">
                <SettingsPage />
              </RequirePermission>
            }
          />
          <Route path="profile" element={<ProfilePage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
