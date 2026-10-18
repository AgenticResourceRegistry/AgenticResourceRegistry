// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute } from "@tanstack/react-router";
import { lazy } from "react";
import type { RegistryType } from "@/lib/api";

const BuildPage = lazy(() => import("@/pages/registry/build"));
const COMPONENT_KINDS: RegistryType[] = ["mcps", "skills", "hooks", "prompts", "sandboxes"];

export type BuildSearch = { kind?: RegistryType; draft?: string };

export const Route = createFileRoute("/_authed/build")({
  component: BuildPage,
  validateSearch: (search: Record<string, unknown>): BuildSearch => ({
    kind: typeof search.kind === "string" && COMPONENT_KINDS.includes(search.kind as RegistryType)
      ? search.kind as RegistryType : undefined,
    draft: typeof search.draft === "string" && /^[0-9a-f-]{36}$/i.test(search.draft)
      ? search.draft : undefined,
  }),
});
