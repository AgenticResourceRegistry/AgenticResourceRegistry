// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { Suspense, useState, useEffect, useCallback } from "react";
import { useRouter, useSearch } from "@tanstack/react-router";
import { Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import { auth } from "@/lib/api";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function DeviceContent() {
  const router = useRouter();
  const { code: codeParam, sso: ssoParam } = useSearch({ from: "/(auth)/device" });
  const [userCode, setUserCode] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const { brandingAppName, brandingLogo, brandingWordmark } = useDeploymentConfig();

  const formatCode = useCallback((raw: string): string => {
    const stripped = raw.replace(/[^a-zA-Z0-9]/g, "").toUpperCase();
    const limited = stripped.slice(0, 8);
    if (limited.length > 4) {
      return `${limited.slice(0, 4)}-${limited.slice(4)}`;
    }
    return limited;
  }, []);

  // Redirect to login if not authenticated
  useEffect(() => {
    if (typeof window === "undefined") return;
    const hasToken = !!sessionStorage.getItem("observal_access_token");
    if (!hasToken) {
      const code = codeParam;
      const directSso = ssoParam === "1";
      const returnPath = code
        ? `/device?code=${encodeURIComponent(code)}${directSso ? "&sso=1" : ""}`
        : `/device${directSso ? "?sso=1" : ""}`;
      router.navigate({ to: "/login", search: { next: returnPath, ...(directSso ? { sso: "1" } : {}) }, replace: true });
    }
  }, [router, codeParam, ssoParam]);

  // Pre-fill code from query parameter
  useEffect(() => {
    const code = codeParam;
    if (code) {
      setUserCode(formatCode(code));
    }
  }, [codeParam, formatCode]);

  function handleCodeChange(e: React.ChangeEvent<HTMLInputElement>) {
    setUserCode(formatCode(e.target.value));
    setError("");
  }

  async function handleSubmit() {
    const stripped = userCode.replace(/-/g, "");
    if (stripped.length !== 8) {
      setError("Please enter a valid 8-character code (format: XXXX-XXXX).");
      return;
    }

    setError("");
    setLoading(true);
    try {
      await auth.deviceConfirm(userCode);
      setSuccess(true);
      toast.success("Device authorized successfully");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to authorize device";
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }

  // Don't render form until we know user is authenticated
  if (typeof window !== "undefined" && !sessionStorage.getItem("observal_access_token")) {
    return null;
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-surface-sunken p-6">
      <div className="w-full max-w-md">
        <div className="rounded-lg border bg-card shadow-sm">
          {/* Brand header */}
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
              Authorize Device
            </p>
          </div>

          {/* Content */}
          <div className="px-8 py-6">
            {success ? (
              <div className="flex flex-col items-center gap-4 py-4 animate-in">
                <CheckCircle2 className="h-12 w-12 text-success" />
                <p className="text-center text-sm text-muted-foreground">
                  Device authorized! You can close this tab and return to your terminal.
                </p>
              </div>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  handleSubmit();
                }}
                className="space-y-4"
              >
                <div className="space-y-2 animate-in">
                  <Label htmlFor="user-code">
                    Enter the code shown in your terminal to authorize this device.
                  </Label>
                  <Input
                    id="user-code"
                    type="text"
                    placeholder="XXXX-XXXX"
                    value={userCode}
                    onChange={handleCodeChange}
                    required
                    autoFocus
                    className="text-center font-mono text-lg tracking-widest"
                    maxLength={9}
                  />
                </div>

                {/* Error */}
                {error && (
                  <div className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive animate-in">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{error}</span>
                  </div>
                )}

                {/* Submit */}
                <div className="animate-in stagger-2">
                  <Button type="submit" disabled={loading} className="w-full">
                    {loading ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      "Authorize Device"
                    )}
                  </Button>
                </div>
              </form>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function DevicePage() {
  return (
    <Suspense>
      <DeviceContent />
    </Suspense>
  );
}
