// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute } from "@tanstack/react-router";
import { lazy } from "react";
const DiscoverPage = lazy(() => import("@/pages/registry/discover"));

const KINDS = ["agent", "mcp", "skill", "hook", "prompt", "sandbox", "external"] as const;

export const Route = createFileRoute("/_authed/discover")({
  component: DiscoverPage,
  validateSearch: (search: Record<string, unknown>) => ({
    q: typeof search.q === "string" ? search.q.slice(0, 500) : undefined,
    kind: typeof search.kind === "string" && KINDS.some((kind) => kind === search.kind)
      ? search.kind as (typeof KINDS)[number] : undefined,
    harness: typeof search.harness === "string" ? search.harness : undefined,
    unapproved: search.unapproved === true ? true : undefined,
  }),
});
