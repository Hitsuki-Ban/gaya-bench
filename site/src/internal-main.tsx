import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router";

import "./index.css";

if (import.meta.env.VITE_GAYA_LISTENING_APP === "true") {
  document.documentElement.lang = "zh-CN";
  document.title = "Gaya Bench — 角色声音听测";
}
import InternalApp from "./internal/InternalApp.tsx";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HashRouter>
      <InternalApp />
    </HashRouter>
  </StrictMode>,
);
