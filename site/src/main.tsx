import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { assertAudioBaseConfigured } from "@/lib/audio-url";

import "./index.css";
import App from "./App.tsx";

assertAudioBaseConfigured();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
