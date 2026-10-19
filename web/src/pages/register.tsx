// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Suspense, useEffect, useState } from "react";
import { Link, useRouter, useSearch } from "@tanstack/react-router";
import { ArrowRight, CheckCircle2, Eye, EyeOff, Loader2, ShieldCheck, AlertCircle } from "lucide-react";
import { toast } from "sonner";
import { auth, commitLogin, loginStart, getAccessToken } from "@/lib/api";
import { safeNext } from "@/lib/safe-next";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const PASSWORD_RULES = [
  { id: "len", label: "At least 8 characters", test: (p: string) => p.length >= 8 },
  { id: "upper", label: "One uppercase letter", test: (p: string) => /[A-Z]/.test(p) },
  { id: "digit", label: "One number", test: (p: string) => /[0-9]/.test(p) },
  { id: "special", label: "One special character", test: (p: string) => /[^A-Za-z0-9]/.test(p) },
];

function passwordIsStrong(password: string) {
  return PASSWORD_RULES.every((rule) => rule.test(password));
}

function RegisterContent() {
  const router = useRouter();
  const searchParams = useSearch({ from: "/(auth)/register" });
  const { selfRegistrationEnabled, ssoOnly, brandingAppName, brandingLogo, brandingWordmark, loading: configLoading } = useDeploymentConfig();
  // Signed-in visitors are redirected by the effect below; rendering nothing in
  // the meantime avoids flashing the form at someone who cannot use it.
  const [alreadyAuthed] = useState(
    () => typeof window !== "undefined" && !!getAccessToken(),
  );
  const appName = brandingAppName || "Observal";
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [passwordTouched, setPasswordTouched] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const passwordStrong = passwordIsStrong(password);
  const passwordsMatch = password === confirmPassword;

  useEffect(() => {
    if (typeof window === "undefined") return;
    const hasToken = !!getAccessToken();
    // Already signed in: honor a shared-link `next` instead of always going home.
    if (hasToken) window.location.replace(safeNext(searchParams.next));
  }, [router, searchParams.next]);

  async function handleRegister() {
    setError("");
    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (!passwordStrong) {
      setError("Password does not meet the requirements");
      return;
    }

    setLoading(true);
    try {
      const started = loginStart();
      const res = await auth.register({
        email,
        name,
        username: username.trim() || undefined,
        password,
      });
      if (!await commitLogin(res, started)) throw new Error("Sign-in was cancelled");
      toast.success("Account created");
      window.location.replace(safeNext(searchParams.next));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Registration failed";
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }

  if (alreadyAuthed) {
    return null;
  }

  if (configLoading) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-surface-sunken p-6">
        <div className="flex items-center gap-3 rounded-lg border bg-card px-4 py-3 text-sm text-muted-foreground shadow-sm">
          <Loader2 className="h-4 w-4 animate-spin" />
          Checking registration policy
        </div>
      </div>
    );
  }

  if (ssoOnly || !selfRegistrationEnabled) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-surface-sunken p-6">
        <div className="w-full max-w-md rounded-lg border bg-card p-8 text-center shadow-sm">
          <ShieldCheck className="mx-auto h-10 w-10 text-muted-foreground" />
          <h1 className="mt-4 text-2xl font-semibold tracking-tight">Registration is closed</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {ssoOnly
              ? "This server uses SSO sign-in. Your account is created automatically the first time you sign in with your identity provider."
              : "Ask your admin for access, or sign in if you already have an account."}
          </p>
          <Button asChild className="mt-6 w-full">
            <Link to="/login">Back to sign in</Link>
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-surface-sunken p-6">
      <div className="grid w-full max-w-5xl overflow-hidden rounded-xl border bg-card shadow-sm md:grid-cols-[0.9fr_1.1fr]">
        <aside className="hidden border-r bg-muted/30 p-8 md:flex md:flex-col md:justify-between">
          <div>
            <div className="flex items-center gap-3">
              {brandingLogo ? (
                <img loading="lazy" src={brandingLogo} alt="" width={32} height={32} className="object-contain" />
              ) : (
                <img loading="lazy" src="/logo.png" alt="" width={32} height={32} className="object-contain" />
              )}
              <span className="text-lg font-semibold tracking-tight">{appName}</span>
            </div>
            <h1 className="mt-10 max-w-sm text-3xl font-semibold tracking-tight text-balance">
              Governed Agent File access, without waiting on setup.
            </h1>
            <p className="mt-4 max-w-sm text-sm leading-6 text-muted-foreground">
              Create a standard user account for browsing the registry, viewing traces, and using approved Agent File context.
            </p>
          </div>
          <ul className="space-y-3 text-sm text-muted-foreground">
            {["Standard access by default", "Registry browsing ready", "You can request more permissions later"].map((item) => (
              <li key={item} className="flex items-center gap-2">
                <CheckCircle2 className="h-4 w-4 text-success" />
                {item}
              </li>
            ))}
          </ul>
        </aside>

        <main className="p-8 sm:p-10">
          <div className="mx-auto max-w-md">
            <div className="flex flex-col items-center gap-2 text-center md:items-start md:text-left">
              {brandingWordmark ? (
                <img loading="lazy" src={brandingWordmark} alt={appName} width={192} height={24} className="h-6 max-w-48 object-contain md:hidden" />
              ) : (
                <span className="text-xl font-semibold tracking-tight md:hidden">{appName}</span>
              )}
              <h2 className="text-2xl font-semibold tracking-tight">Create your account</h2>
              <p className="text-sm text-muted-foreground">You will start with standard user permissions.</p>
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleRegister();
              }}
              className="mt-8 space-y-4"
            >
              <div className="space-y-2">
                <Label htmlFor="name">Full name</Label>
                <Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Ada Lovelace" required autoFocus />
              </div>
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" required />
              </div>
              <div className="space-y-2">
                <Label htmlFor="username">Username <span className="text-muted-foreground">optional</span></Label>
                <Input id="username" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="ada-lovelace" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <div className="relative">
                  <Input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => {
                      setPassword(e.target.value);
                      setPasswordTouched(true);
                    }}
                    placeholder="At least 8 characters"
                    required
                    className={`pr-10 ${
                      passwordTouched && password
                        ? passwordStrong
                          ? "border-green-500 focus-visible:ring-green-500"
                          : "border-destructive focus-visible:ring-destructive"
                        : ""
                    }`}
                  />
                  <button
                    type="button"
                    tabIndex={-1}
                    className="absolute right-0 top-0 flex h-full w-10 items-center justify-center text-muted-foreground transition-colors hover:text-foreground"
                    onClick={() => setShowPassword(!showPassword)}
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
                {passwordTouched && password && (
                  <ul className="mt-2 space-y-1">
                    {PASSWORD_RULES.map((rule) => {
                      const ok = rule.test(password);
                      return (
                        <li
                          key={rule.id}
                          className={`flex items-center gap-1.5 text-xs ${
                            ok ? "text-green-600 dark:text-green-400" : "text-muted-foreground"
                          }`}
                        >
                          <span>{ok ? "✓" : "○"}</span>
                          {rule.label}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="confirm-password">Confirm password</Label>
                <Input
                  id="confirm-password"
                  type={showPassword ? "text" : "password"}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="Repeat your password"
                  required
                  className={
                    confirmPassword
                      ? passwordsMatch
                        ? "border-green-500 focus-visible:ring-green-500"
                        : "border-destructive focus-visible:ring-destructive"
                      : ""
                  }
                />
                {confirmPassword && !passwordsMatch && (
                  <p className="mt-1 text-xs text-destructive">Passwords do not match</p>
                )}
              </div>

              {error && (
                <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              <Button
                type="submit"
                disabled={
                  loading ||
                  configLoading ||
                  !selfRegistrationEnabled ||
                  !passwordStrong ||
                  !passwordsMatch
                }
                className="w-full"
              >
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    Create account
                    <ArrowRight className="ml-1 h-4 w-4" />
                  </>
                )}
              </Button>
            </form>

            <p className="mt-6 text-center text-sm text-muted-foreground">
              Already have an account?{" "}
              <Link to="/login" search={searchParams.next ? { next: searchParams.next } : undefined} className="font-medium text-foreground underline-offset-4 hover:underline">
                Sign in
              </Link>
            </p>
          </div>
        </main>
      </div>
    </div>
  );
}

export default function RegisterPage() {
  return (
    <Suspense>
      <RegisterContent />
    </Suspense>
  );
}
