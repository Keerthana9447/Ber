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
import { CustomersPage } from "./pages/CustomersPage";
import { InvoicesPage } from "./pages/InvoicesPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ProfilePage } from "./pages/ProfilePage";
import { PaymentsPage } from "./pages/PaymentsPage";
import { CreditNotesPage } from "./pages/CreditNotesPage";
import { CustomerLedgerPage } from "./pages/CustomerLedgerPage";
import { SalesFlowPage } from "./pages/SalesFlowPage";
import { ExpensesPage } from "./pages/ExpensesPage";
import { DealerPortalPage } from "./pages/DealerPortalPage";
import { PurchasingPage } from "./pages/PurchasingPage";
import { DealersPage } from "./pages/DealersPage";
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
  const { user } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route
            index
            element={
              user?.role === "Dealer / Distributor"
                ? <Navigate to="/products" replace />
                : <RequirePermission permission="dashboard:read"><DashboardPage /></RequirePermission>
            }
          />
          {MODULES.filter((m) => !m.special).map((m) => (
            <Route
              key={m.key}
              path={m.path.slice(1)}
              element={
                <RequirePermission permission={m.permission}>
                  {m.key === "customers" ? (
                    <CustomersPage />
                  ) : ["quotations", "orders", "delivery-challans", "reports"].includes(m.key) ? (
                    <SalesFlowPage />
                  ) : m.key === "invoices" ? (
                    <InvoicesPage />
                  ) : m.key === "products" ? (
                    user?.role === "Dealer / Distributor" ? <DealerPortalPage /> : <ProductsPage />
                  ) : m.key === "inventory" ? (
                    <InventoryPage />
                  ) : m.key === "payments" ? (
                    <PaymentsPage />
                  ) : m.key === "credit-notes" ? (
                    <CreditNotesPage />
                  ) : m.key === "customer-ledger" ? (
                    <CustomerLedgerPage />
                  ) : m.key === "expenses" ? (
                    <ExpensesPage />
                  ) : m.key === "purchases" ? (
                    <PurchasingPage />
                  ) : m.key === "dealers" ? (
                    <DealersPage />
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
