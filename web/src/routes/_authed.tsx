// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute, Outlet, useLocation } from "@tanstack/react-router";
import { Suspense } from "react";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { RegistrySidebar } from "@/components/nav/registry-sidebar";
import { Toaster } from "@/components/ui/sonner";
import { AuthGuard, OptionalAuthGuard } from "@/components/layouts/auth-guard";
import { HelpProvider } from "@/components/wiki/help-context";
import { StayInTouchPrompt } from "@/components/usage/stay-in-touch";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { isPublicRegistryPath } from "@/lib/public-registry";

function AppShell() {
  return (
    <HelpProvider>
      <SidebarProvider>
        <RegistrySidebar />
        <SidebarInset>
          <StayInTouchPrompt />
          <Suspense fallback={<div className="flex h-screen w-full items-center justify-center" />}>
            <Outlet />
          </Suspense>
        </SidebarInset>
        <Toaster visibleToasts={1} />
      </SidebarProvider>
    </HelpProvider>
  );
}

function AuthedLayout() {
  const { pathname } = useLocation();
  const { publicRegistryEnabled, loading } = useDeploymentConfig();

  if (loading) {
    return <div className="flex h-screen w-full items-center justify-center" />;
  }

  if (publicRegistryEnabled && isPublicRegistryPath(pathname)) {
    return (
      <OptionalAuthGuard>
        <AppShell />
      </OptionalAuthGuard>
    );
  }

  return (
    <AuthGuard>
      <AppShell />
    </AuthGuard>
  );
}

export const Route = createFileRoute("/_authed")({
  component: AuthedLayout,
});
