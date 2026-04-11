import { Navigate, Route, Routes } from "react-router-dom";

import AuthLayout from "@/components/AuthLayout";
import ProtectedRoute from "@/components/ProtectedRoute";
import BuildingsPage from "@/pages/BuildingsPage";
import DashboardPage from "@/pages/DashboardPage";
import LoginPage from "@/pages/LoginPage";
import PaymentsPage from "@/pages/PaymentsPage";
import TenantsPage from "@/pages/TenantsPage";
import UnitsPage from "@/pages/UnitsPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/login" element={<LoginPage />} />

      <Route
        element={
          <ProtectedRoute>
            <AuthLayout />
          </ProtectedRoute>
        }
      >
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/buildings" element={<BuildingsPage />} />
        <Route path="/units" element={<UnitsPage />} />
        <Route path="/tenants" element={<TenantsPage />} />
        <Route path="/payments" element={<PaymentsPage />} />
      </Route>

      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
