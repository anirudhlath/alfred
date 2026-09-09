import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { DoorProvider } from "@/door/DoorProvider";
import { AuthGate } from "@/gates/AuthGate";
import { Room } from "@/room/Room";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { QueryProvider } from "@/shell/QueryProvider";
import { ThemeProvider } from "@/shell/ThemeProvider";

/**
 * Provider order is load-bearing: ConnectionProvider and AuthGate both read the
 * query client, AuthGate reads the connection for the Denied gate's stamp, and
 * DoorProvider sits inside AuthGate so nothing reads `/api/actions/pending`
 * before there is a session to read it with.
 *
 * There is one screen. `/actions/:id` is the Room as well — a notification tap
 * must land on the approval it names (spec §6.3), and `useActionRoute` opens the
 * Door over it.
 */
export default function App() {
  return (
    <QueryProvider>
      <ThemeProvider>
        <ConnectionProvider>
          <BrowserRouter>
            <AuthGate>
              <DoorProvider>
                <Routes>
                  <Route path="/" element={<Room />} />
                  <Route path="/actions/:id" element={<Room />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </DoorProvider>
            </AuthGate>
          </BrowserRouter>
        </ConnectionProvider>
      </ThemeProvider>
    </QueryProvider>
  );
}
