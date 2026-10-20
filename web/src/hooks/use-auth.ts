// SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState, useSyncExternalStore } from "react";
import { useRouter, useLocation } from "@tanstack/react-router";
import { auth, setUserRole, getUserRole, clearSession, refreshAccessTokenWithReason, authEpoch, canSilentRefresh, isCurrentAuth, updateProfile, getAccessToken, hasLegacySession, migrateLegacySession, abandonLegacySession } from "@/lib/api";
import { currentPathAsNext } from "@/lib/safe-next";

function isNetworkError(err: unknown): boolean {
  return err instanceof TypeError || (typeof navigator !== "undefined" && !navigator.onLine);
}

function subscribe(cb: () => void) {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
}

function getAuthSnapshot() {
  if (typeof window === "undefined") return "";
  if (hasLegacySession()) return "migrating";
  const key = getAccessToken();
  const role = getUserRole();
  if (key) return role || "pending";
  // No access token in sessionStorage, but refresh token may exist (new tab scenario).
  // Mark as "refreshing" so the guard attempts a silent refresh before redirecting.
  const hasRefresh = canSilentRefresh() && !!localStorage.getItem("observal_refresh_token");
  return hasRefresh ? "refreshing" : "";
}

function getServerSnapshot() {
  return "ssr";
}

function retryUntilSettled(
  attemptRequest: () => Promise<"ok" | "rejected" | "network_error" | "superseded" | "reauth_required">,
  { onSuccess, onRejected, onNetworkError }: {
    onSuccess?: () => void;
    onRejected?: () => void;
    onNetworkError?: () => void;
  } = {},
) {
  const epoch = authEpoch();
  let cancelled = false;
  let inFlight = false;
  let retryTimer: number | undefined;

  const attempt = async () => {
    if (cancelled || inFlight) return;
    inFlight = true;
    let result: Awaited<ReturnType<typeof attemptRequest>>;
    try {
      result = await attemptRequest();
    } catch {
      // A failed Web Lock cannot leave a guarded route spinning forever.
      result = "reauth_required";
    } finally {
      inFlight = false;
    }
    if (cancelled || authEpoch() !== epoch) return;

    if (result === "ok") {
      onSuccess?.();
      window.dispatchEvent(new Event("storage"));
    } else if (result === "rejected" || result === "reauth_required") {
      clearSession();
      onRejected?.();
    } else if (result === "network_error") {
      onNetworkError?.();
      retryTimer = window.setTimeout(attempt, 5_000);
    }
  };

  const retryNow = () => {
    if (retryTimer) window.clearTimeout(retryTimer);
    void attempt();
  };

  window.addEventListener("online", retryNow);
  void attempt();
  return () => {
    cancelled = true;
    if (retryTimer) window.clearTimeout(retryTimer);
    window.removeEventListener("online", retryNow);
  };
}

function retryRefreshUntilSettled(onRejected?: () => void) {
  return retryUntilSettled(refreshAccessTokenWithReason, { onRejected });
}

function retryWhoamiUntilSettled({
  onSuccess,
  onRejected,
  onNetworkError,
}: {
  onSuccess?: () => void;
  onRejected?: () => void;
  onNetworkError?: () => void;
} = {}) {
  return retryUntilSettled(
    async () => {
      try {
        const epoch = authEpoch();
        const actor = localStorage.getItem("observal_user_email");
        const user = await auth.whoami();
        if (!isCurrentAuth(epoch, actor) || (actor && user.email !== actor)) return "superseded";
        if (!await updateProfile(epoch, actor, () => setUserRole(user.role))) return "superseded";
        return "ok";
      } catch (err) {
        return isNetworkError(err) ? "network_error" : "rejected";
      }
    },
    { onSuccess, onRejected, onNetworkError },
  );
}

export function useAuthGuard() {
  const router = useRouter();
  const { pathname } = useLocation();
  const snapshot = useSyncExternalStore(subscribe, getAuthSnapshot, getServerSnapshot);
  const isSSR = snapshot === "ssr";
  const isMigrating = snapshot === "migrating";
  const hasToken = !isSSR && snapshot !== "" && snapshot !== "refreshing" && !isMigrating;
  const isRefreshing = snapshot === "refreshing";
  const ready = hasToken && snapshot !== "pending";
  const role = ready ? snapshot : null;

  useEffect(() => {
    if (isSSR) return;

    if (isMigrating) {
      void migrateLegacySession().then(() => window.dispatchEvent(new Event("storage")), abandonLegacySession);
      return;
    }
    // New tab: no access token but refresh token exists. Try silent refresh.
    if (isRefreshing) {
      return retryRefreshUntilSettled(() => {
        router.navigate({ to: "/login", replace: true, search: { next: currentPathAsNext() } });
      });
    }

    if (!hasToken && pathname !== "/login") {
      router.navigate({ to: "/login", replace: true, search: { next: currentPathAsNext() } });
      return;
    }
    if (!hasToken) return;

    if (snapshot === "pending") {
      return retryWhoamiUntilSettled({
        onRejected: () => {
          router.navigate({ to: "/login", replace: true, search: { next: currentPathAsNext() } });
        },
      });
    }
  }, [isSSR, hasToken, isRefreshing, isMigrating, snapshot, pathname, router]);

  return { ready, role };
}

/**
 * Optional auth — resolves immediately for unauthenticated users.
 * Authenticated users get their role resolved via whoami.
 * Does NOT redirect to login.
 */
export function useOptionalAuth() {
  const snapshot = useSyncExternalStore(subscribe, getAuthSnapshot, getServerSnapshot);
  const [networkFallback, setNetworkFallback] = useState(false);
  const isMigrating = snapshot === "migrating";
  const isRefreshing = snapshot === "refreshing";
  const hasToken = snapshot !== "" && snapshot !== "ssr" && !isRefreshing && !isMigrating;
  // A fresh tab cannot retry an indeterminate rotation. Public pages must
  // settle anonymously even while the shared refresh token remains fenced.
  const useAnonymousFallback = networkFallback && (snapshot === "pending" || isRefreshing);
  const ready = useAnonymousFallback || (snapshot !== "pending" && !isRefreshing && !isMigrating);
  const role = !useAnonymousFallback && hasToken && snapshot !== "pending" ? snapshot : null;
  const isAuthenticated = !useAnonymousFallback && hasToken && snapshot !== "pending";

  useEffect(() => {
    if (isMigrating) {
      void migrateLegacySession().then(() => window.dispatchEvent(new Event("storage")), abandonLegacySession);
      return;
    }
    if (isRefreshing) {
      return retryUntilSettled(refreshAccessTokenWithReason, {
        onSuccess: () => setNetworkFallback(false),
        onRejected: () => setNetworkFallback(true),
        onNetworkError: () => setNetworkFallback(true),
      });
    }

    if (hasToken && snapshot === "pending") {
      return retryWhoamiUntilSettled({
        onSuccess: () => setNetworkFallback(false),
        onNetworkError: () => {
          setNetworkFallback(true);
          window.dispatchEvent(new Event("observal:session-cleared"));
        },
      });
    }
  }, [hasToken, isRefreshing, isMigrating, snapshot]);

  return { ready, role, isAuthenticated };
}
