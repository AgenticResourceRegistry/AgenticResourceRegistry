// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute, redirect } from "@tanstack/react-router";
import { lazy } from "react";
import type { RegistryType } from "@/lib/api";

const ResourcesPage = lazy(() => import("@/pages/registry/resources/index"));

/** Every agentic resource type, in tab order. `agents` first, then the five component types. */
export const RESOURCE_TYPES: RegistryType[] = ["agents", "mcps", "skills", "hooks", "prompts", "sandboxes"];

/** Filters shared across types plus the type-specific ones; irrelevant keys are ignored per type. */
export type ResourcesSearch = {
  /** Which slice to list: discover (default), my, pending, archived. */
  view?: string;
  search?: string;
  namespace?: string;
  team?: string;
  category?: string;
  harness?: string;
  task_type?: string;
  event?: string;
  scope?: string;
  runtime_type?: string;
  /** Deep link to an exact skill folder draft (set by the skill detail page). */
  folderListingId?: string;
  folderVersionId?: string;
  folderVersion?: string;
};

const SEARCH_KEYS = [
  "view",
  "search",
  "namespace",
  "team",
  "category",
  "harness",
  "task_type",
  "event",
  "scope",
  "runtime_type",
] as const;

const asMatch = (value: unknown, pattern: RegExp) =>
  typeof value === "string" && pattern.test(value) ? value : undefined;

const UUID = /^[\da-f-]{36}$/i;
const SEMVER = /^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$/;

export const Route = createFileRoute("/_authed/resources/$type")({
  component: ResourcesPage,
  beforeLoad: ({ params }) => {
    if (!RESOURCE_TYPES.includes(params.type as RegistryType)) {
      throw redirect({ to: "/resources/$type", params: { type: "agents" }, replace: true });
    }
  },
  validateSearch: (search: Record<string, unknown>): ResourcesSearch =>
    ({
      ...Object.fromEntries(SEARCH_KEYS.map((k) => [k, typeof search[k] === "string" && search[k] ? search[k] : undefined])),
      folderListingId: asMatch(search.folderListingId, UUID),
      folderVersionId: asMatch(search.folderVersionId, UUID),
      folderVersion: asMatch(search.folderVersion, SEMVER),
    }),
});
