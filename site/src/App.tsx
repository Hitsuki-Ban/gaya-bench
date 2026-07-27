import { Route, Routes } from "react-router";

import { AppLayout } from "@/components/app-layout";
import { AbPage } from "@/pages/ab-page";
import { CreditsPage } from "@/pages/credits-page";
import { HomePage } from "@/pages/home-page";
import { ModelPage } from "@/pages/model-page";
import { NotFoundPage } from "@/pages/not-found-page";
import { ScenarioPage } from "@/pages/scenario-page";

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<HomePage />} />
        <Route path="scenario/:id" element={<ScenarioPage />} />
        <Route path="models/:id" element={<ModelPage />} />
        <Route path="ab" element={<AbPage />} />
        <Route path="credits" element={<CreditsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
