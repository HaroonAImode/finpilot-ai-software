import { createFileRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/app-shell";
import { RequireAuth } from "@/components/require-auth";

export const Route = createFileRoute("/app")({
  head: () => ({
    meta: [
      { title: "FinPilot AI — Workspace" },
      { name: "description", content: "AI accounting workspace for Pakistani SMEs." },
      { property: "og:title", content: "FinPilot AI — Workspace" },
      { property: "og:description", content: "AI accounting workspace for Pakistani SMEs." },
    ],
  }),
  component: ProtectedWorkspace,
});

function ProtectedWorkspace() {
  return (
    <RequireAuth>
      <AppShell />
    </RequireAuth>
  );
}
