// SPDX-FileCopyrightText: 2026 Harishankar <harishankar0301@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useState, useSyncExternalStore } from "react";
import { useRouter, useLocation } from "@tanstack/react-router";
import { auth, setUserRole, getUserRole, clearTabSession, refreshAccessTokenWithReason, getTabActor, isAccountChanged, quarantineTab, AuthContextChanged } from "@/lib/api";
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
  if (isAccountChanged()) return "account_changed";
  const key = sessionStorage.getItem("observal_access_token");
  const role = getUserRole();
  if (key) return role || "pending";
  // No access token in sessionStorage, but refresh token may exist (new tab scenario).
  // Mark as "refreshing" so the guard attempts a silent refresh before redirecting.
  if (getTabActor()) return "account_changed"; // quarantined tabs cannot silently adopt a new account
  const hasRefresh = !!localStorage.getItem("observal_refresh_token");
  return hasRefresh ? "refreshing" : "";
}

function getServerSnapshot() {
  return "ssr";
}

function retryUntilSettled(
  attemptRequest: () => Promise<"ok" | "rejected" | "network_error" | "account_changed" | "superseded">,
  { onSuccess, onRejected, onNetworkError }: {
    onSuccess?: () => void;
    onRejected?: () => void;
    onNetworkError?: () => void;
  } = {},
) {
  let cancelled = false;
  let inFlight = false;
  let retryTimer: number | undefined;

  const attempt = async () => {
    if (cancelled || inFlight) return;
    inFlight = true;
    const result = await attemptRequest();
    inFlight = false;
    if (cancelled) return;

    if (result === "ok") {
      onSuccess?.();
      window.dispatchEvent(new Event("storage"));
    } else if (result === "account_changed") {
      quarantineTab();
      onRejected?.();
    } else if (result === "rejected") {
      clearTabSession();
      onRejected?.();
    } else {
      if (result === "network_error") onNetworkError?.();
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
      const actor = getTabActor();
      const access = sessionStorage.getItem("observal_access_token");
      const refresh = localStorage.getItem("observal_refresh_token");
      if (isAccountChanged()) return "account_changed";
      try {
        const user = await auth.whoami();
        // Token bytes can rotate while whoami is pending. Only the actor is
        // allowed to write shared profile state, never a stale A tab after B signs in.
        if (isAccountChanged() || getTabActor() !== actor ||
          !sessionStorage.getItem("observal_access_token")) return "account_changed";
        setUserRole(user.role);
        return "ok";
      } catch (err) {
        if (err instanceof AuthContextChanged || isAccountChanged() || getTabActor() !== actor) return "account_changed";
        // A rejection for an older token says nothing about the rotated pair.
        if (sessionStorage.getItem("observal_access_token") !== access ||
          localStorage.getItem("observal_refresh_token") !== refresh) return "superseded";
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
  const hasToken = !isSSR && snapshot !== "" && snapshot !== "refreshing" && snapshot !== "account_changed";
  const isRefreshing = snapshot === "refreshing";
  const ready = hasToken && snapshot !== "pending";
  const role = ready ? snapshot : null;

  useEffect(() => {
    if (isSSR) return;

    if (snapshot === "account_changed") {
      if (isAccountChanged()) quarantineTab();
      router.navigate({ to: "/login", replace: true, search: { next: currentPathAsNext() } });
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
  }, [isSSR, hasToken, isRefreshing, snapshot, pathname, router]);

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
  const isRefreshing = snapshot === "refreshing";
  const hasToken = snapshot !== "" && snapshot !== "ssr" && snapshot !== "account_changed" && !isRefreshing;
  const useAnonymousFallback = networkFallback && snapshot === "pending";
  // A quarantined tab may still browse public routes, but never gains B's role.
  const ready = useAnonymousFallback || (snapshot !== "pending" && !isRefreshing);
  const role = !useAnonymousFallback && hasToken && snapshot !== "pending" ? snapshot : null;
  const isAuthenticated = !useAnonymousFallback && hasToken && snapshot !== "pending";

  useEffect(() => {
    if (snapshot === "account_changed") {
      if (isAccountChanged()) quarantineTab();
      return;
    }
    if (isRefreshing) {
      return retryRefreshUntilSettled();
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
  }, [hasToken, isRefreshing, snapshot]);

  return { ready, role, isAuthenticated };
}
