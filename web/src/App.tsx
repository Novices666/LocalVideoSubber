import { Routes, Route, Navigate } from "react-router-dom";
import { AppLayout } from "@/components/AppLayout";
import { HomePage } from "@/pages/HomePage";
import { TranscribePage } from "@/pages/TranscribePage";
import { TranslatePage } from "@/pages/TranslatePage";
import { RenderPage } from "@/pages/RenderPage";
import { SubtitleSettingsPage } from "@/pages/SubtitleSettingsPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { FilesPage } from "@/pages/FilesPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/transcribe" element={<TranscribePage />} />
        <Route path="/translate" element={<TranslatePage />} />
        <Route path="/render" element={<RenderPage />} />
        <Route path="/subtitle-settings" element={<SubtitleSettingsPage />} />
        <Route path="/files" element={<FilesPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
