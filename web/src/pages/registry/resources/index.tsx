// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Suspense } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import { PageHeader } from "@/components/layouts/page-header";
import { TypeTabs } from "@/components/registry/registry-primitives";
import type { RegistryType } from "@/lib/api";
import AgentsView from "./agents-view";
import ComponentsView from "./components-view";

const TABS: { value: RegistryType; label: string }[] = [
  { value: "agents", label: "Agent Files" },
  { value: "mcps", label: "MCP Clients" },
  { value: "skills", label: "Skills" },
  { value: "hooks", label: "Hooks" },
  { value: "prompts", label: "Prompts" },
  { value: "sandboxes", label: "Sandboxes" },
];

export default function ResourcesPage() {
  const { type } = useParams({ from: "/_authed/resources/$type" }) as { type: RegistryType };
  const navigate = useNavigate({ from: "/resources/$type" });

  return (
    <>
      <PageHeader
        title="Agentic Resources"
        breadcrumbs={[{ label: "Registry", href: "/" }, { label: "Agentic Resources" }]}
      />
      <div className="page-body w-full mx-auto">
        <TypeTabs
          changesPage
          tabs={TABS}
          active={type}
          onTabChange={(v) =>
            // Keep the filters every type understands (and the view, except agents-only Archived); type-specific ones reset.
            navigate({
              params: { type: v },
              search: (cur) => ({
                search: cur.search,
                namespace: cur.namespace,
                team: cur.team,
                view: v === "agents" || cur.view !== "archived" ? cur.view : undefined,
              }),
            })
          }
        />
        <Suspense>
          {/* key remounts the view so tab, view mode and dialog state reset per type */}
          {type === "agents" ? <AgentsView key={type} /> : <ComponentsView key={type} type={type} />}
        </Suspense>
      </div>
    </>
  );
}
