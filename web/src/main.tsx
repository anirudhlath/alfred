import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import { installViewportVars } from "@/lib/viewport";

// Before the first paint: #root is sized from --app-height, and the composer's
// padding from --keyboard-inset. (installAudioUnlock() joins this in plan 1b,
// task 28 — iOS blocks audio until a gesture, so the unlock hook has to exist
// before the first tap, not before the first render.)
installViewportVars();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
