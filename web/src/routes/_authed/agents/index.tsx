// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute, redirect } from "@tanstack/react-router";

// Legacy list URL; the list now lives under Agentic Resources.
export const Route = createFileRoute("/_authed/agents/")({
  validateSearch: (search: Record<string, unknown>) => search,
  beforeLoad: ({ search }) => {
    throw redirect({ to: "/resources/$type", params: { type: "agents" }, search, replace: true });
  },
});
