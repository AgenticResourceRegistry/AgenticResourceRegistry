// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute } from "@tanstack/react-router";
import { lazy } from "react";
import type { RegistryType } from "@/lib/api";

const ComponentsPage = lazy(() => import("@/pages/registry/components/index"));

export type ComponentsSearch = {
  type?: RegistryType;
  search?: string;
  namespace?: string;
  team?: string;
  category?: string;
  task_type?: string;
  event?: string;
  scope?: string;
  runtime_type?: string;
  folderListingId?: string;
  folderVersionId?: string;
  folderVersion?: string;
};

const TYPES = new Set<RegistryType>([
  "mcps",
  "skills",
  "hooks",
  "prompts",
  "sandboxes",
]);

export const Route = createFileRoute("/_authed/components/")({
  component: ComponentsPage,
  validateSearch: (search: Record<string, unknown>): ComponentsSearch => ({
    type: TYPES.has(search.type as RegistryType)
      ? (search.type as RegistryType)
      : undefined,
    search: (search.search as string) || undefined,
    namespace: (search.namespace as string) || undefined,
    team: (search.team as string) || undefined,
    category: (search.category as string) || undefined,
    task_type: (search.task_type as string) || undefined,
    event: (search.event as string) || undefined,
    scope: (search.scope as string) || undefined,
    runtime_type: (search.runtime_type as string) || undefined,
    folderListingId: typeof search.folderListingId === "string" && /^[\da-f-]{36}$/i.test(search.folderListingId) ? search.folderListingId : undefined,
    folderVersionId: typeof search.folderVersionId === "string" && /^[\da-f-]{36}$/i.test(search.folderVersionId) ? search.folderVersionId : undefined,
    folderVersion: typeof search.folderVersion === "string" && /^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$/.test(search.folderVersion) ? search.folderVersion : undefined,
  }),
});
