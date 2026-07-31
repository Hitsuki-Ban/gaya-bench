import { Navigate, Route, Routes } from "react-router";

import { CompletionPage } from "@/completion/completion-page";
import { InternalLayout } from "@/internal/internal-layout";
import { InternalNotFoundPage } from "@/internal/internal-not-found-page";
import { CuratePage } from "@/pages/curate-page";
import { PilotPage } from "@/pages/pilot-page";

export default function InternalApp() {
  return (
    <Routes>
      <Route element={<InternalLayout />}>
        <Route index element={<Navigate replace to="/curate" />} />
        <Route path="curate" element={<CuratePage />} />
        <Route path="completion" element={<CompletionPage />} />
        <Route path="pilot" element={<PilotPage />} />
        <Route path="*" element={<InternalNotFoundPage />} />
      </Route>
    </Routes>
  );
}
