import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/index.css";
import App from "@/App";
import { installAudioUnlock } from "@/lib/audio";
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
