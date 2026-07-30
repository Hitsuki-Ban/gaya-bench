import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router";

import { assertAudioBaseConfigured } from "@/lib/audio-url";

import "./index.css";
import InternalApp from "./internal/InternalApp.tsx";

assertAudioBaseConfigured();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter>
      <InternalApp />
    </HashRouter>
  </StrictMode>,
);
