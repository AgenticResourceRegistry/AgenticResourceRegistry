// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute, redirect } from "@tanstack/react-router";
import type { RegistryType } from "@/lib/api";

// Legacy list URL (`/components?type=skills`); the list now lives under Agentic Resources.
export const Route = createFileRoute("/_authed/components/")({
  validateSearch: (search: Record<string, unknown>) => search as { type?: RegistryType },
  beforeLoad: ({ search }) => {
    const { type, ...rest } = search as Record<string, string | undefined>;
    throw redirect({
      to: "/resources/$type",
      params: { type: type ?? "mcps" },
      search: rest,
      replace: true,
    });
  },
});
