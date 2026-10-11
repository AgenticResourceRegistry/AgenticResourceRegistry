// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute } from "@tanstack/react-router";
import { lazy } from "react";
const ReviewPage = lazy(() => import("@/pages/review/queue"));

export type ReviewSearch = {
  tab?: "agents" | "components" | "teamspaces";
};

export const Route = createFileRoute("/_authed/review")({
  component: ReviewPage,
  // Queue links may select the relevant subject tab.
  validateSearch: (search: Record<string, unknown>): ReviewSearch => ({
    tab:
      search.tab === "components"
        ? "components"
        : search.tab === "teamspaces"
          ? "teamspaces"
          : search.tab === "agents"
            ? "agents"
            : undefined,
  }),
});
