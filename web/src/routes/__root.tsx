// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createRootRoute, Outlet } from "@tanstack/react-router";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "@/lib/theme";
import { makeQueryClient } from "@/lib/query-client";
import { DynamicTitle } from "@/components/dynamic-title";
import { ErrorBoundary } from "@/components/shared/error-boundary";
import { VersionMismatchBanner } from "@/components/shared/version-mismatch-banner";
import "@/app.css";

function subscribeToAuthChanges(callback: () => void) {
  window.addEventListener("storage", callback);
  return () => window.removeEventListener("storage", callback);
}

function getAuthTokenSnapshot() {
  return typeof window === "undefined"
    ? ""
    : sessionStorage.getItem("observal_access_token") ?? "";
}

function RootComponent() {
  const [queryClient] = useState(makeQueryClient);
  const authToken = useSyncExternalStore(subscribeToAuthChanges, getAuthTokenSnapshot, () => "");
  const previousAuthToken = useRef(authToken);

  useEffect(() => {
    if (previousAuthToken.current !== authToken) {
      queryClient.clear();
      previousAuthToken.current = authToken;
    }
  }, [authToken, queryClient]);

  useEffect(() => {
    const clearAccountData = () => queryClient.clear();
    window.addEventListener("observal:session-cleared", clearAccountData);
    return () => window.removeEventListener("observal:session-cleared", clearAccountData);
  }, [queryClient]);

  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        {/* Appearance defaults to the monochrome preset in system mode; the
            preset and legacy theme lists live in @/lib/theme. */}
        <ThemeProvider defaultTheme="system">
          <Outlet />
          <VersionMismatchBanner />
        </ThemeProvider>
        <DynamicTitle />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}

export const Route = createRootRoute({
  component: RootComponent,
});
