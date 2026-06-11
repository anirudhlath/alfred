import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError } from "@/lib/api";
import type { AuthStatus } from "@/lib/types";
import { AlfredProvider } from "@/shell/AlfredProvider";
import { AppShell } from "@/shell/AppShell";
import { ChatPage } from "@/chat/ChatPage";
import { ActivityPage } from "@/pages/ActivityPage";
import { HealthPage } from "@/pages/HealthPage";
import { LoginPage } from "@/pages/LoginPage";
import { MemoryPage } from "@/pages/MemoryPage";
import { OnboardingPage } from "@/pages/OnboardingPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { TriggersPage } from "@/pages/TriggersPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) =>
        !(error instanceof ApiError && error.status < 500) && failureCount < 1,
      refetchOnWindowFocus: true,
    },
  },
});

function Guarded() {
  const { data, isLoading } = useQuery<AuthStatus>({
    queryKey: ["auth-status"],
    queryFn: () => api("/api/auth/status"),
  });
  if (isLoading) return null;
  if (data && !data.registered) return <Navigate to="/onboarding" replace />;
  if (data && !data.authenticated) return <Navigate to="/login" replace />;
  return (
    <AlfredProvider>
      <AppShell />
    </AlfredProvider>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/onboarding" element={<OnboardingPage />} />
          <Route element={<Guarded />}>
            <Route path="/" element={<ChatPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/triggers" element={<TriggersPage />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
