import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/index.css";
import App from "@/App";
import { installAudioUnlock } from "@/lib/audio";
import { installServiceWorker } from "@/lib/sw";
import { installViewportVars } from "@/lib/viewport";

// Before the first paint: #root is sized from --app-height and the composer's
// padding comes from --keyboard-inset.
installViewportVars();

// Before the first tap: iOS refuses audio until a gesture has resumed a context,
// and will not retroactively allow a sound requested before then. Installing the
// listener here means the very first spoken reply is audible (constraint §4.7).
installAudioUnlock();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Last, so nothing of the worker's stands between this module and the app being
// handed to React. `render()` only schedules the first paint and this does not
// wait for it; it need not, because `register()` returns at once and the fetching
// and precaching happen off this thread. In dev this unregisters instead — see
// lib/sw.ts. A failure is reported, never dropped: on a phone with no worker yet,
// a rejected registration is the only sign the deploy did not ship sw.js.
installServiceWorker().catch((error: unknown) => {
  console.error("service worker setup failed", error);
});
