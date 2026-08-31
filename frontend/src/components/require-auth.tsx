import { useEffect, type ReactNode } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useAuth } from "@/lib/auth-context";

/**
 * Gate for everything under /app.
 *
 * The access token lives in memory, so on a fresh page load we genuinely do not
 * know yet whether the user is signed in — the refresh cookie has to be
 * exchanged first. Rendering a redirect during that window would bounce a
 * legitimately signed-in user to /login on every refresh, so this waits for
 * `isRestoring` to finish before deciding.
 *
 * This is a client-side convenience only. It hides UI; it does not protect data.
 * The services enforce access on every request regardless of what the browser
 * chooses to render.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated, isRestoring } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!isRestoring && !isAuthenticated) {
      void navigate({ to: "/login" });
    }
  }, [isRestoring, isAuthenticated, navigate]);

  if (isRestoring) {
    return (
      <div className="grid min-h-screen place-items-center" aria-busy="true">
        <div className="flex flex-col items-center gap-2 text-muted-foreground">
          <Loader2 className="h-6 w-6 animate-spin" />
          <p className="text-sm">Restoring your session…</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) return null;

  return <>{children}</>;
}
