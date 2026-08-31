import { useState, type FormEvent } from "react";
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { AlertCircle, Check, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth-context";
import { AuthError } from "@/lib/auth";

/** Must match the service's own floor, or the form would accept what the API rejects. */
const MIN_PASSWORD_LENGTH = 12;

export const Route = createFileRoute("/signup")({
  head: () => ({
    meta: [
      { title: "Create your account — FinPilot AI" },
      { name: "description", content: "Create a FinPilot AI workspace for your company." },
    ],
  }),
  component: SignupPage,
});

function SignupPage() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ company_name: "", full_name: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const passwordLongEnough = form.password.length >= MIN_PASSWORD_LENGTH;

  function update(field: keyof typeof form) {
    return (event: { target: { value: string } }) =>
      setForm((prev) => ({ ...prev, [field]: event.target.value }));
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signup(form);
      await navigate({ to: "/app" });
    } catch (caught) {
      setError(
        caught instanceof AuthError
          ? caught.message
          : "Could not create your account. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-muted/30 px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 text-center">
          <h1 className="font-display text-2xl font-bold">Create your workspace</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Sets up your company and makes you its admin.
          </p>
        </div>

        <form onSubmit={onSubmit} className="surface space-y-4 p-6">
          {error && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
            >
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="company_name">Company name</Label>
            <Input
              id="company_name"
              required
              value={form.company_name}
              onChange={update("company_name")}
              placeholder="Khan Enterprises (Pvt) Ltd"
              className="rounded-xl"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="full_name">Your name</Label>
            <Input
              id="full_name"
              required
              autoComplete="name"
              value={form.full_name}
              onChange={update("full_name")}
              placeholder="Ayesha Khan"
              className="rounded-xl"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="email">Work email</Label>
            <Input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={form.email}
              onChange={update("email")}
              placeholder="you@company.pk"
              className="rounded-xl"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              required
              autoComplete="new-password"
              minLength={MIN_PASSWORD_LENGTH}
              value={form.password}
              onChange={update("password")}
              className="rounded-xl"
            />
            {/* A length floor beats complexity rules: "Passw0rd!" passes most
                complexity checks and is trivially cracked, while a long
                passphrase is both stronger and easier to remember. */}
            <p
              className={`flex items-center gap-1.5 text-xs ${
                passwordLongEnough ? "text-success" : "text-muted-foreground"
              }`}
            >
              {passwordLongEnough && <Check className="h-3 w-3" />}
              At least {MIN_PASSWORD_LENGTH} characters. A short sentence works well.
            </p>
          </div>

          <Button
            type="submit"
            className="w-full gap-2 rounded-xl"
            disabled={submitting || !passwordLongEnough}
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {submitting ? "Creating your workspace…" : "Create account"}
          </Button>

          <p className="text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link to="/login" className="font-medium text-primary hover:underline">
              Sign in
            </Link>
          </p>
        </form>
      </div>
    </main>
  );
}
