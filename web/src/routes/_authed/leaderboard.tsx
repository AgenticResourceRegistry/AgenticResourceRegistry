// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Navigate, createFileRoute } from "@tanstack/react-router";
import { lazy } from "react";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
const LeaderboardPage = lazy(() => import("@/pages/registry/leaderboard"));

// The Leaderboard is deprecated and off by default. Wait for the deployment
// config before deciding so the page never mounts (or calls its API) while off.
function LeaderboardRoute() {
  const { leaderboardEnabled, loading } = useDeploymentConfig();
  if (loading) return null;
  if (!leaderboardEnabled) return <Navigate to="/" replace />;
  return <LeaderboardPage />;
}

export const Route = createFileRoute("/_authed/leaderboard")({
  component: LeaderboardRoute,
});
