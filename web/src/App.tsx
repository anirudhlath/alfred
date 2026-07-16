import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
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
  const { data, isLoading, isError } = useQuery<AuthStatus>({
    queryKey: ["auth-status"],
    queryFn: () => api("/api/auth/status"),
  });
  if (isLoading) return null;
  // Fail closed: if the status query errored (backend restarting, 5xx, network
  // blip — 401 is handled by api()'s hard redirect), `data` is undefined. Redirect
  // to /login rather than falling through and mounting the protected shell with
  // undefined auth state.
  if (isError || !data) return <Navigate to="/login" replace />;
  if (!data.registered) return <Navigate to="/onboarding" replace />;
  if (!data.authenticated) return <Navigate to="/login" replace />;
  return (
    <AlfredProvider>
      <AppShell />
    </AlfredProvider>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      {/* App-level so toasts render on /login and /onboarding too, not only inside AppShell. */}
      <Toaster position="top-right" />
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/onboarding" element={<OnboardingPage />} />
          <Route element={<Guarded />}>
            <Route path="/" element={<ChatPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/triggers" element={<TriggersPage />} />
            <Route path="/system" element={<HealthPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
