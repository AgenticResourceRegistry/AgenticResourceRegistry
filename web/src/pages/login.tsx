// SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { Suspense, useState, useEffect, useRef } from "react";
import { Link, useRouter, useSearch } from "@tanstack/react-router";
import { Eye, EyeOff, ArrowRight, Loader2, AlertCircle, RefreshCw, CheckCircle2, XCircle } from "lucide-react";
import { toast } from "sonner";
import { auth, config as configApi, commitLogin, loginStart, getAccessToken, leaveForGuest, getUserRole } from "@/lib/api";
import { isPublicRegistryPath } from "@/lib/public-registry";
import { isSafeNext, safeNext } from "@/lib/safe-next";
import type { SsoHealthResult, E2eStatusResult, HealthCheck } from "@/lib/api";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { Button } from "@/components/ui/button";
import { GoogleGIcon } from "@/components/ui/google-g-icon";
import { GithubMarkIcon } from "@/components/ui/github-mark-icon";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function LoginContent() {
  const router = useRouter();
  const searchParams = useSearch({ from: "/(auth)/login" });
  const {
    ssoEnabled,
    googleSsoEnabled,
    githubSsoEnabled,
    ssoOnly,
    publicRegistryEnabled,
    selfRegistrationEnabled,
    samlEnabled,
    brandingAppName,
    brandingLogo,
    brandingWordmark,
  } = useDeploymentConfig();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [ssoLoading, setSsoLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const [mustChangePassword, setMustChangePassword] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [ssoHealth, setSsoHealth] = useState<SsoHealthResult | null>(null);
  const [ssoHealthLoading, setSsoHealthLoading] = useState(false);
  const directSsoStarted = useRef(false);
  // StrictMode replays effects with the same component state; callback codes
  // are single-use, so claim before removing the start marker or exchanging.
  const claimedSsoCallback = useRef<string | null>(null);
  const [ssoErrorDiag, setSsoErrorDiag] = useState<E2eStatusResult | null>(null);
  const [ssoErrorDiagExpanded, setSsoErrorDiagExpanded] = useState(true);

  useEffect(() => {
    const corrId = searchParams.sso_error;
    if (!corrId) return;
    // Real-login failure: backend already redirected here with a 10-min TTL
    // diagnostics record. Fetch and render it so the user sees exactly which
    // step broke instead of a generic "SSO Authentication Failed".
    console.debug("[sso] fetching error diagnostics", corrId);
    auth.ssoErrorDiagnostics(corrId)
      .then((diag) => {
        console.info("[sso] error diagnostics", { ok: diag.ok, checks: diag.checks?.length });
        setSsoErrorDiag(diag);
        const firstFail = diag.checks?.find((c) => c.status === "fail");
        if (firstFail) {
          setError(`SSO login failed at: ${firstFail.label}`);
        } else {
          setError(diag.summary || "SSO login failed");
        }
      })
      .catch((e) => {
        console.warn("[sso] error diagnostics fetch failed", e);
        setError("SSO login failed (diagnostics expired)");
      })
      .finally(() => {
        // Strip the query param so a page refresh doesn't re-fetch.
        window.history.replaceState({}, "", "/login");
      });
  }, [searchParams.sso_error]);

  useEffect(() => {
    if (!ssoEnabled && !samlEnabled) return;
    setSsoHealthLoading(true);
    configApi.ssoHealth()
      .then(setSsoHealth)
      .catch(() => {})
      .finally(() => setSsoHealthLoading(false));
  }, [ssoEnabled, samlEnabled]);

  useEffect(() => {
    if (!navigator.locks?.request) {
      setError("This browser does not support secure sign-in (Web Locks required).");
    }
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    // Don't redirect to "/" if a SAML token exchange is pending
    const params = new URLSearchParams(window.location.search);
    if (params.get("saml_token") || params.get("code") || params.get("saml_code")) return;
    const hasToken = !!getAccessToken();
    if (hasToken && getUserRole()) {
      // Already signed in: honor a shared-link `next` instead of always going home.
      window.location.replace(safeNext(searchParams.next));
    }
  }, [router, searchParams.next]);

  useEffect(() => {
    const samlTokenId = searchParams.saml_token;
    const ssoCode = searchParams.code || searchParams.saml_code;
    const callback = samlTokenId ? `saml_token:${samlTokenId}` :
      searchParams.code ? `code:${searchParams.code}` : ssoCode ? `saml_code:${ssoCode}` : null;
    if (callback && claimedSsoCallback.current === callback) return;
    let started: string | null = null;
    if (callback) {
      claimedSsoCallback.current = callback;
      started = sessionStorage.getItem("observal_sso_start");
      if (started === null) {
        setError("Sign-in was cancelled. Please try again.");
        return;
      }
      sessionStorage.removeItem("observal_sso_start");
      setLoading(true);
      window.history.replaceState({}, "", "/login");
    }
    if (samlTokenId) {
      (async () => {
        try {
          const res = await fetch(`/api/v1/sso/saml/exchange?token_id=${samlTokenId}`, {
            method: "POST",
          });
          if (!res.ok) throw new Error("Exchange failed");
          const data = await res.json();
          if (started === null || !await commitLogin(data, started)) throw new Error("Sign-in was cancelled");
          window.location.replace(safeNext(searchParams.next));
        } catch {
          setError("SAML sign-in failed. Please try again.");
          toast.error("SAML sign-in failed.");
          setLoading(false);
        }
      })();
      return;
    }

    if (ssoCode) {
      auth.exchangeCode({ code: ssoCode })
        .then(async (data) => {
          if (started === null || !await commitLogin(data, started)) throw new Error("Sign-in was cancelled");
          window.location.href = safeNext(searchParams.next);
        })
        .catch((err) => {
          const msg = err instanceof Error ? err.message : "SSO sign-in failed";
          setError(msg);
          toast.error("SSO sign-in failed -- the code may have expired. Please try again.");
          setLoading(false);
        });
    } else if (searchParams.error) {
      setError(searchParams.error || "SSO Authentication Failed");
    }
  }, [searchParams, router]);

  useEffect(() => {
    const reason = searchParams.reason;
    if (reason === "session_expired") {
      toast.info("Your session has expired. Please sign in again.");
      // Strip only `reason` from the URL, preserving `next` so sign-in still
      // returns the user to the page the expiry interrupted. Rewriting to a
      // bare "/login" here would drop `next` — TanStack re-parses the location
      // that replaceState sets, so the router's search state would lose it too.
      const preserved = searchParams.next && isSafeNext(searchParams.next) ? `/login?next=${encodeURIComponent(searchParams.next)}` : "/login";
      window.history.replaceState({}, "", preserved);
    }
  }, [searchParams]);

  async function handlePasswordLogin() {
    setError("");
    setLoading(true);
    try {
      const started = loginStart();
      const res = await auth.login({ email, password });
      if (!await commitLogin(res, started)) throw new Error("Sign-in was cancelled");

      if (res.must_change_password) {
        setMustChangePassword(true);
        setLoading(false);
        return;
      }

      toast.success("Signed in successfully");
      window.location.replace(safeNext(searchParams.next));
    } catch (e) {
      const raw = e instanceof Error ? e.message : "Login failed";
      const status = e instanceof Error ? (e as Error & { status?: number }).status : undefined;
      let msg = raw;
      if (status === 429 || raw.toLowerCase().includes("rate limit")) {
        msg = "Too many login attempts. Please wait a minute before trying again.";
      }
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }

  async function handleChangePassword() {
    setError("");
    if (newPassword !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }
    setLoading(true);
    try {
      // Changing the password signs out every session; keep this one on the fresh pair it hands back.
      const started = loginStart();
      const { exchange_code } = await auth.changePassword({ current_password: password, new_password: newPassword });
      const session = await auth.exchangeCode({ code: exchange_code });
      if (!await commitLogin(session, started)) throw new Error("Session changed");
      toast.success("Password changed successfully");
      window.location.replace(safeNext(searchParams.next));
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to change password";
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }

  function ssoStartUrl(base: string): string {
    const nextParam = searchParams.next;
    return isSafeNext(nextParam) ? `${base}?next=${encodeURIComponent(nextParam)}` : base;
  }

  function handleSsoLogin() {
    if (!navigator.locks?.request) { setError("This browser does not support secure sign-in (Web Locks required)."); return; }
    setSsoLoading(true);
    sessionStorage.setItem("observal_sso_start", loginStart() ?? "");
    window.location.href = ssoStartUrl("/api/v1/auth/oauth/login");
  }

  function handleGoogleLogin() {
    if (!navigator.locks?.request) { setError("This browser does not support secure sign-in (Web Locks required)."); return; }
    setSsoLoading(true);
    sessionStorage.setItem("observal_sso_start", loginStart() ?? "");
    window.location.href = ssoStartUrl("/api/v1/auth/oauth/google/login");
  }

  function handleGithubLogin() {
    if (!navigator.locks?.request) { setError("This browser does not support secure sign-in (Web Locks required)."); return; }
    setSsoLoading(true);
    sessionStorage.setItem("observal_sso_start", loginStart() ?? "");
    window.location.href = ssoStartUrl("/api/v1/auth/oauth/github/login");
  }

  function handleSamlLogin() {
    if (!navigator.locks?.request) { setError("This browser does not support secure sign-in (Web Locks required)."); return; }
    setSsoLoading(true);
    sessionStorage.setItem("observal_sso_start", loginStart() ?? "");
    window.location.href = ssoStartUrl("/api/v1/sso/saml/login");
  }

  async function handleGuestLogin() {
    sessionStorage.removeItem("observal_sso_start");
    try {
      await leaveForGuest();
      const requestedPath = safeNext(searchParams.next);
      const pathname = requestedPath.split(/[?#]/, 1)[0];
      window.location.replace(isPublicRegistryPath(pathname) ? requestedPath : "/");
    } catch {
      setError("Could not leave the previous session. Please try again.");
    }
  }

  useEffect(() => {
    if (searchParams.sso !== "1" || directSsoStarted.current) return;
    directSsoStarted.current = true;
    configApi.public()
      .then((publicConfig) => {
        if (publicConfig.sso_enabled) {
          handleSsoLogin();
        } else if (publicConfig.saml_enabled) {
          handleSamlLogin();
        } else {
          directSsoStarted.current = false;
          setError("SSO is not configured on this server.");
        }
      })
      .catch(() => {
        directSsoStarted.current = false;
        setError("Could not load SSO configuration.");
      });
  }, [searchParams.sso]);

  if (mustChangePassword) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-surface-sunken p-6">
        <div className="w-full max-w-md">
          <div className="rounded-lg border bg-card shadow-sm">
            <div className="flex flex-col items-center gap-2 border-b px-8 pb-6 pt-8 animate-in">
              {brandingLogo ? (
                <img loading="lazy" src={brandingLogo} alt="" width={32} height={32} className="object-contain" />
              ) : (
                <img loading="lazy" src="/logo.png" alt="" width={32} height={32} className="object-contain" />
              )}
              {brandingWordmark ? (
                <img loading="lazy" src={brandingWordmark} alt={brandingAppName || "Observal"} width={192} height={24} className="h-6 max-w-48 object-contain" />
              ) : (
                <h1 className="text-2xl font-semibold tracking-tight font-[family-name:var(--font-display)]">
                  {brandingAppName || "Observal"}
                </h1>
              )}
              <p className="text-sm text-muted-foreground">
                You must change your password before continuing
              </p>
            </div>
            <div className="px-8 py-6">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleChangePassword();
                }}
                className="space-y-4"
              >
                <div className="space-y-2 animate-in">
                  <Label htmlFor="new-password">New Password</Label>
                  <div className="relative">
                    <Input
                      id="new-password"
                      type={showPassword ? "text" : "password"}
                      placeholder="Enter new password"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      required
                      autoFocus
                      className="pr-10"
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
                </div>
                <div className="space-y-2 animate-in stagger-1">
                  <Label htmlFor="confirm-password">Confirm Password</Label>
                  <Input
                    id="confirm-password"
                    type={showPassword ? "text" : "password"}
                    placeholder="Confirm new password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    required
                  />
                </div>

                {error && (
                  <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive animate-in">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{error}</span>
                  </div>
                )}

                <Button type="submit" disabled={loading} className="w-full">
                  {loading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <>
                      Change Password
                      <ArrowRight className="ml-1 h-4 w-4" />
                    </>
                  )}
                </Button>
              </form>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-surface-sunken p-6">
      <div className="w-full max-w-md">
        <div className="rounded-lg border bg-card shadow-sm">
          <div className="flex flex-col items-center gap-2 border-b px-8 pb-6 pt-8 animate-in">
            {brandingLogo ? (
              <img loading="lazy" src={brandingLogo} alt="" width={32} height={32} className="object-contain" />
            ) : (
              <img loading="lazy" src="/logo.png" alt="" width={32} height={32} className="object-contain" />
            )}
            {brandingWordmark ? (
              <img loading="lazy" src={brandingWordmark} alt={brandingAppName || "Observal"} width={192} height={24} className="h-6 max-w-48 object-contain" />
            ) : (
              <h1 className="text-2xl font-semibold tracking-tight font-[family-name:var(--font-display)]">
                {brandingAppName || "Observal"}
              </h1>
            )}
            <p className="text-sm text-muted-foreground">
              {publicRegistryEnabled ? "Sign in or browse the public registry" : "Sign in to your account"}
            </p>
          </div>

          <div className="px-8 py-6">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handlePasswordLogin();
              }}
              className="space-y-4"
            >
              {!ssoOnly && (
                <>
                  <div className="space-y-2 animate-in">
                    <Label htmlFor="email">Email or username</Label>
                    <Input
                      id="email"
                      type="text"
                      placeholder="you@company.com or username"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      required
                      autoFocus
                    />
                  </div>
                  <div className="space-y-2 animate-in stagger-1">
                    <Label htmlFor="password">Password</Label>
                    <div className="relative">
                      <Input
                        id="password"
                        type={showPassword ? "text" : "password"}
                        placeholder="Enter password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        required
                        className="pr-10"
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
                  </div>
                </>
              )}

              {error && (
                <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive animate-in">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              {ssoErrorDiag?.checks?.length ? (
                <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs animate-in">
                  <button
                    type="button"
                    onClick={() => setSsoErrorDiagExpanded((v) => !v)}
                    className="w-full flex items-center justify-between"
                  >
                    <span className="font-medium text-destructive">
                      SSO sign-in failed at "{ssoErrorDiag.checks.find((c) => c.status === "fail")?.label || ssoErrorDiag.summary || "unknown step"}"
                    </span>
                    <span className="text-muted-foreground ml-2">
                      {ssoErrorDiagExpanded ? "Hide steps" : "Show steps"}
                    </span>
                  </button>
                  {ssoErrorDiagExpanded && (
                    <ul className="mt-2 divide-y divide-border">
                      {ssoErrorDiag.checks.map((c: HealthCheck) => (
                        <li key={c.name} className="py-1.5 flex items-start gap-2">
                          {c.status === "pass" ? (
                            <CheckCircle2 className="h-3.5 w-3.5 mt-0.5 text-emerald-500 shrink-0" />
                          ) : c.status === "fail" ? (
                            <XCircle className="h-3.5 w-3.5 mt-0.5 text-destructive shrink-0" />
                          ) : (
                            <RefreshCw className="h-3.5 w-3.5 mt-0.5 text-muted-foreground shrink-0" />
                          )}
                          <div className="flex-1 min-w-0">
                            <div className="font-medium text-foreground">{c.label}</div>
                            {c.message && (
                              <div className="text-muted-foreground mt-0.5">{c.message}</div>
                            )}
                            {c.hint && (
                              <div className="text-muted-foreground italic mt-0.5">
                                Hint: {c.hint}
                              </div>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}

              <div className="animate-in stagger-2 space-y-3">
                {!ssoOnly && (
                  <Button type="submit" disabled={loading || ssoLoading} className="w-full">
                    {loading && !ssoLoading ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <>
                        Sign in
                        <ArrowRight className="ml-1 h-4 w-4" />
                      </>
                    )}
                  </Button>
                )}

                {!ssoOnly && (ssoEnabled || googleSsoEnabled || githubSsoEnabled || samlEnabled) && (
                  <div className="relative py-2">
                    <div className="absolute inset-0 flex items-center">
                      <span className="w-full border-t" />
                    </div>
                    <div className="relative flex justify-center text-xs uppercase">
                      <span className="bg-card px-2 text-muted-foreground">Or</span>
                    </div>
                  </div>
                )}

                {googleSsoEnabled && (
                  <Button
                    type="button"
                    variant={ssoOnly ? "default" : "outline"}
                    className="w-full"
                    onClick={handleGoogleLogin}
                    disabled={loading || ssoLoading}
                  >
                    {ssoLoading ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <GoogleGIcon className="mr-2 h-4 w-4" />
                    )}
                    Sign in with Google
                  </Button>
                )}

                {githubSsoEnabled && (
                  <Button
                    type="button"
                    variant={ssoOnly ? "default" : "outline"}
                    className="w-full"
                    onClick={handleGithubLogin}
                    disabled={loading || ssoLoading}
                  >
                    {ssoLoading ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <GithubMarkIcon className="mr-2 h-4 w-4" />
                    )}
                    Sign in with GitHub
                  </Button>
                )}

                {ssoEnabled && (
                  <div className="flex items-center">
                    <Button
                      type="button"
                      variant={ssoOnly ? "default" : "outline"}
                      className="relative flex-1 pr-10"
                      onClick={handleSsoLogin}
                      disabled={loading || ssoLoading}
                    >
                      {ssoLoading ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : (
                        <RefreshCw className="mr-2 h-4 w-4" />
                      )}
                      Sign in with SSO
                      {ssoHealthLoading ? (
                        <Loader2 className="absolute right-3 h-5 w-5 animate-spin text-muted-foreground" />
                      ) : ssoHealth?.oidc && (
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="absolute right-3">
                                {ssoHealth.oidc.ok ? (
                                  <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                                ) : (
                                  <XCircle className="h-5 w-5 text-destructive" />
                                )}
                              </span>
                            </TooltipTrigger>
                            <TooltipContent side="right" className="max-w-xs text-xs">
                              {ssoHealth.oidc.ok
                                ? `OIDC config verified (${ssoHealth.oidc.latency_ms}ms), does not test a full user login`
                                : ssoHealth.oidc.error}
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      )}
                    </Button>
                  </div>
                )}

                {samlEnabled && (
                  <div className="flex items-center">
                    <Button
                      type="button"
                      variant={ssoOnly ? "default" : "outline"}
                      className="relative flex-1 pr-10"
                      onClick={handleSamlLogin}
                      disabled={loading || ssoLoading}
                    >
                      Sign in with SAML SSO
                      {ssoHealthLoading ? (
                        <Loader2 className="absolute right-3 h-5 w-5 animate-spin text-muted-foreground" />
                      ) : ssoHealth?.saml && (
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="absolute right-3">
                                {ssoHealth.saml.ok ? (
                                  <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                                ) : (
                                  <XCircle className="h-5 w-5 text-destructive" />
                                )}
                              </span>
                            </TooltipTrigger>
                            <TooltipContent side="right" className="max-w-xs text-xs">
                              {ssoHealth.saml.ok
                                ? `SAML config verified (${ssoHealth.saml.latency_ms}ms), does not test a full user login`
                                : ssoHealth.saml.error}
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      )}
                    </Button>
                  </div>
                )}

                {publicRegistryEnabled && (
                  <>
                    <div className="relative py-2">
                      <div className="absolute inset-0 flex items-center">
                        <span className="w-full border-t" />
                      </div>
                      <div className="relative flex justify-center text-xs uppercase">
                        <span className="bg-card px-2 text-muted-foreground">Public access</span>
                      </div>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      className="w-full"
                      onClick={handleGuestLogin}
                      disabled={loading || ssoLoading}
                    >
                      Sign in as guest
                      <ArrowRight className="ml-1 h-4 w-4" />
                    </Button>
                    <p className="text-center text-xs leading-5 text-muted-foreground">
                      Browse and install approved public registry content without an account.
                    </p>
                  </>
                )}
              </div>

              {!ssoOnly && (
                <div className="animate-in stagger-3 space-y-3 text-center">
                  {selfRegistrationEnabled && !ssoOnly && (
                    <Button asChild variant="outline" className="w-full">
                      <Link to="/register" search={searchParams.next ? { next: searchParams.next } : undefined}>
                        Register
                      </Link>
                    </Button>
                  )}
                  <p className="text-sm text-muted-foreground/60">
                    Forgot password? Contact your admin.
                  </p>
                </div>
              )}
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginContent />
    </Suspense>
  );
}
