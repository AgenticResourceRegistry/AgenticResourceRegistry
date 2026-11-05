// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link, useRouter } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Blocks,
  Bot,
  Star,
  Terminal,
} from "lucide-react";
import { PageHeader } from "@/components/layouts/page-header";
import { RecommendedForYou } from "@/components/registry/recommended-for-you";
import { RegistryName } from "@/components/registry/registry-name";
import { EntityGlyph } from "@/components/registry/entity-glyph";
import {
  Panel,
  CompactRow,
  IntentSearch,
  IntentChip,
  RegistryHomeGrid,
} from "@/components/registry/registry-primitives";
import { ErrorState } from "@/components/shared/error-state";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";
import {
  useMyAgents,
  useRegistryList,
  useSessions2,
  useTopAgents,
  useWhoami,
} from "@/hooks/use-api";
import { useOptionalAuth } from "@/hooks/use-auth";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { hasMinRole } from "@/hooks/use-role-guard";
import { getUserRole } from "@/lib/api";
import { registryItemPath } from "@/lib/registry-name";
import { compactNumber } from "@/lib/utils";
import type { RegistryItem, Session, TopAgentItem } from "@/lib/types";

const TIME_FORMATTER = new Intl.DateTimeFormat("en", {
  hour: "numeric",
  minute: "2-digit",
});

const DATE_TIME_FORMATTER = new Intl.DateTimeFormat("en", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

function toNumber(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function formatTime(value?: string): string {
  if (!value) return "No activity";
  const parsed = new Date(
    value.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(value)
      ? value
      : `${value.replace(" ", "T")}Z`,
  );
  if (Number.isNaN(parsed.getTime())) return "No activity";
  return parsed.toDateString() === new Date().toDateString()
    ? TIME_FORMATTER.format(parsed)
    : DATE_TIME_FORMATTER.format(parsed);
}

function isApproved(agent: RegistryItem): boolean {
  return !agent.status || agent.status === "approved";
}

function sessionTitle(session: Session): string {
  const prompts = toNumber(session.prompt_count);
  return `${session.agent_name ? `${session.agent_name} · ` : ""}${prompts} ${prompts === 1 ? "prompt" : "prompts"}`;
}

function sessionPlatform(session: Session): string {
  return session.platform || session.service_name || "Unknown harness";
}

export default function RegistryHome() {
  const [search, setSearch] = useState("");
  const router = useRouter();
  const { isAuthenticated } = useOptionalAuth();
  const { data: whoami } = useWhoami(isAuthenticated);
  const { brandingAppName, leaderboardEnabled } = useDeploymentConfig();
  const { data: sessions, isLoading: sessionsLoading } = useSessions2({
    days: 7,
    limit: 8,
    mine: true,
    refetchInterval: 30_000,
    enabled: isAuthenticated,
  });
  const { data: myAgents, isLoading: myAgentsLoading } = useMyAgents(isAuthenticated);
  const { data: topAgents, isLoading: topAgentsLoading } = useTopAgents(6);
  const {
    data: agents,
    isLoading: agentsLoading,
    isError: agentsError,
    error: agentsErrorDetail,
    refetch: refetchAgents,
  } = useRegistryList("agents");

  const approvedAgents = useMemo(
    () => (agents ?? []).filter(isApproved),
    [agents],
  );
  const workInProgress = useMemo(
    () =>
      (myAgents ?? []).filter((agent) =>
        ["draft", "pending", "rejected"].includes(
          typeof agent.status === "string" ? agent.status : "",
        ),
      ),
    [myAgents],
  );
  const trustedAgents = useMemo(
    () => approvedAgents.slice(0, 5),
    [approvedAgents],
  );
  const recentSessions = (sessions ?? []).slice(0, 4);
  const displayName =
    whoami?.name || whoami?.username || whoami?.email || "Welcome back";
  const canReview = hasMinRole(getUserRole(), "reviewer");
  const daySummary = isAuthenticated
    ? myAgentsLoading || sessionsLoading
      ? "Loading your registry activity."
      : `${workInProgress.length} registry item${workInProgress.length === 1 ? "" : "s"} need attention · ${(sessions ?? []).length} recent session${(sessions ?? []).length === 1 ? "" : "s"} captured.`
    : "Browse and install approved public Agent Files and components without an account. Sign in when you are ready to publish or manage your own work.";

  function handleSearch() {
    const query = search.trim();
    if (query) router.navigate({ to: "/resources/$type", params: { type: "agents" }, search: { search: query } });
  }

  return (
    <>
      <PageHeader
        title="Registry"
        breadcrumbs={[
          { label: "Registry" },
          { label: "Home" },
        ]}
      />

      <div className="page-body w-full">
        {/* ── Guest CLI banner ── */}
        {!isAuthenticated && (
          <section
            aria-labelledby="guest-cli-title"
            className="mb-[22px] rounded-xl border border-primary-accent/25 bg-primary-accent/5 px-5 py-5 sm:px-6"
          >
            <div className="flex items-start gap-3">
              <div className="mt-0.5 rounded-md bg-primary-accent/10 p-2 text-primary-accent">
                <Terminal className="h-4 w-4" />
              </div>
              <div>
                <h2 id="guest-cli-title" className="text-base font-semibold text-foreground">
                  Use the public registry from your terminal
                </h2>
                <p className="mt-1 text-sm leading-6 text-muted-foreground">
                  No account or token is required for approved public content.
                </p>
              </div>
            </div>
            <ol className="mt-5 grid gap-4 text-sm md:grid-cols-3">
              <li className="min-w-0">
                <p className="mb-1.5 font-medium text-foreground">1. Install the CLI</p>
                <code className="block overflow-x-auto whitespace-nowrap rounded-md bg-background px-3 py-2 font-mono text-xs text-foreground">
                  uv tool install observal-cli
                </code>
              </li>
              <li className="min-w-0">
                <p className="mb-1.5 font-medium text-foreground">2. Find an Agent File</p>
                <code className="block overflow-x-auto whitespace-nowrap rounded-md bg-background px-3 py-2 font-mono text-xs text-foreground">
                  observal agent list
                </code>
              </li>
              <li className="min-w-0">
                <p className="mb-1.5 font-medium text-foreground">3. Pull it into your harness</p>
                <code className="block overflow-x-auto whitespace-nowrap rounded-md bg-background px-3 py-2 font-mono text-xs text-foreground">
                  observal pull namespace/agent --harness pi
                </code>
              </li>
            </ol>
          </section>
        )}

        {/* ── Intent box ── */}
        <section className="mb-[22px] rounded-xl bg-card p-[30px] shadow-sm animate-in">
          <h2 className="max-w-[670px] text-balance text-[clamp(24px,3vw,38px)] font-semibold tracking-[-0.02em]">
            What are you working on?
          </h2>
          <p className="mt-2 max-w-[600px] text-sm text-muted-foreground">
            Find an approved Agent File for the work you need to do.
          </p>

          <IntentSearch
            value={search}
            onChange={setSearch}
            onSubmit={handleSearch}
            placeholder='Try "review a Python service" or "database migration"'
            className="mt-[22px] max-w-[780px]"
          />

          <nav
            aria-label="Quick actions"
            className="mt-[14px] flex flex-wrap gap-[7px]"
          >
            <IntentChip href="/resources/agents">Browse Agent Files</IntentChip>
            <IntentChip href="/agents/builder">Build from components</IntentChip>
            {isAuthenticated && (
              <IntentChip href="/traces">Inspect a trace</IntentChip>
            )}
            {canReview && <IntentChip href="/review">Review submissions</IntentChip>}
          </nav>
        </section>

        {/* ── Main grid ── */}
        <RegistryHomeGrid>
          {/* Recommendations */}
          <div className="col-span-full xl:col-span-1">
            {isAuthenticated ? (
              <RecommendedForYou limit={3} />
            ) : (
              <Panel
                title="Public registry"
                subtitle="Approved building blocks you can use immediately."
                action={
                  <Link
                    to="/login"
                    className="text-2xs font-medium text-muted-foreground hover:text-foreground"
                  >
                    Sign in to contribute →
                  </Link>
                }
              >
                {agentsLoading ? (
                  <TableSkeleton rows={3} cols={2} />
                ) : trustedAgents.length > 0 ? (
                  trustedAgents.slice(0, 3).map((agent) => (
                    <CompactRow
                      key={agent.id}
                      icon={<EntityGlyph type="agent" size="sm" labelled={false} />}
                      title={
                        agent.namespace && agent.slug
                          ? `${agent.namespace}/${agent.slug}`
                          : agent.name
                      }
                      description={
                        typeof agent.description === "string"
                          ? agent.description
                          : "Approved Agent File"
                      }
                      href={registryItemPath(agent, "agents", agent.id)}
                    />
                  ))
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Approved Agent Files will appear here when the registry starts publishing.
                  </p>
                )}
              </Panel>
            )}
          </div>

          {/* Your work */}
          <Panel
            title={isAuthenticated ? "Your work" : "Start exploring"}
            subtitle={isAuthenticated ? "Publishing and maintenance that needs you" : "Browse the public catalog without signing in."}
            action={
              isAuthenticated ? (
                <Link
                  to="/resources/$type"
                  params={{ type: "agents" }}
                  className="text-2xs font-medium text-muted-foreground hover:text-foreground"
                >
                  View all →
                </Link>
              ) : undefined
            }
          >
            <CompactRow
              icon={
                <EntityGlyph type="agent" size="sm" labelled={false} />
              }
              title={
                isAuthenticated
                  ? myAgentsLoading
                    ? "Loading your work…"
                    : workInProgress.length > 0
                      ? `${workInProgress.length} item${workInProgress.length === 1 ? "" : "s"} need attention`
                      : "Your Agent Files are up to date"
                  : "Browse public Agent Files"
              }
              description={
                isAuthenticated
                  ? workInProgress.length > 0
                    ? "Open drafts, pending reviews, and rejected submissions."
                    : "Review published Agent Files or start a new release."
                  : "Find an approved Agent File and pull it into your harness."
              }
              href="/resources/agents"
            />
            {isAuthenticated && (
              <CompactRow
                icon={<EntityGlyph type="agent" size="sm" labelled={false} />}
                title="Build an Agent File"
                description="Bundle components into a portable Agent File."
                href="/agents/builder"
              />
            )}
            <CompactRow
              icon={<EntityGlyph type="mcp" size="sm" labelled={false} />}
              title="Browse components"
              description="Find approved reusable building blocks."
              href="/resources/mcps"
            />
          </Panel>

          {/* Agents gaining adoption */}
          {(isAuthenticated || topAgentsLoading || agentsLoading || Boolean(topAgents?.length) || Boolean(agentsError) || trustedAgents.length > 0) && (
          <Panel
            title={
              topAgents?.length
                ? "Agent Files gaining adoption"
                : "Trusted Agent Files"
            }
            subtitle={
              topAgents?.length
                ? "Frequently installed Agent Files from across the registry"
                : "Approved Agent Files available to install now."
            }
            action={
              leaderboardEnabled ? (
                <Link
                  to="/leaderboard"
                  className="text-2xs font-medium text-muted-foreground hover:text-foreground"
                >
                  Leaderboard →
                </Link>
              ) : undefined
            }
            wide
          >
            {topAgentsLoading || agentsLoading ? (
              <TableSkeleton rows={3} cols={3} />
            ) : topAgents?.length ? (
              topAgents.slice(0, 3).map((agent) => (
                <CompactRow
                  key={agent.id}
                  icon={<EntityGlyph type="agent" size="sm" labelled={false} />}
                  title={
                    agent.namespace && agent.slug
                      ? `${agent.namespace}/${agent.slug}`
                      : agent.name
                  }
                  description={agent.description}
                  meta={
                    <>
                      {compactNumber(agent.download_count)} pulls
                      <br />★{" "}
                      {agent.average_rating
                        ? agent.average_rating.toFixed(1)
                        : "New"}
                    </>
                  }
                  href={registryItemPath(agent, "agents", agent.id)}
                />
              ))
            ) : agentsError ? (
              <ErrorState
                message={agentsErrorDetail?.message}
                onRetry={() => refetchAgents()}
              />
            ) : trustedAgents.length > 0 ? (
              trustedAgents.slice(0, 3).map((agent) => (
                <CompactRow
                  key={agent.id}
                  icon={<EntityGlyph type="agent" size="sm" labelled={false} />}
                  title={
                    agent.namespace && agent.slug
                      ? `${agent.namespace}/${agent.slug}`
                      : agent.name
                  }
                  description={
                    typeof agent.description === "string"
                      ? agent.description
                      : "Approved Agent File"
                  }
                  href={registryItemPath(agent, "agents", agent.id)}
                />
              ))
            ) : (
              <p className="text-xs text-muted-foreground">
                Approved Agent Files will appear here when your registry starts
                publishing.
              </p>
            )}
          </Panel>
          )}

          {/* Recent execution / More public agents */}
          <Panel
            title={isAuthenticated ? "Recent execution" : "More public Agent Files"}
            subtitle={isAuthenticated ? "Your latest captured coding sessions" : "Recently approved Agent Files from the public registry."}
            action={
              <Link
                {...(isAuthenticated ? { to: "/traces" } : { to: "/resources/$type", params: { type: "agents" } })}
                className="text-2xs font-medium text-muted-foreground hover:text-foreground"
              >
                {isAuthenticated ? "All traces →" : "All Agent Files →"}
              </Link>
            }
          >
            {isAuthenticated ? (
              sessionsLoading ? (
                <TableSkeleton rows={3} cols={2} />
              ) : recentSessions.length === 0 ? (
                <div className="flex gap-3 text-xs leading-6 text-muted-foreground">
                  <Activity className="mt-0.5 h-4 w-4 shrink-0" />
                  Enable telemetry in a supported harness to connect registry
                  assets with execution evidence.
                </div>
              ) : (
                recentSessions.map((session) => (
                  <CompactRow
                    key={session.session_id}
                    icon={
                      <span className="inline-grid h-6 w-6 shrink-0 place-items-center rounded-md bg-surface-raised font-mono text-[9px] font-medium text-muted-foreground">
                        {(sessionPlatform(session) || "?")
                          .slice(0, 2)
                          .toUpperCase()}
                      </span>
                    }
                    title={sessionTitle(session)}
                    description={`${sessionPlatform(session)} · ${session.model || "Unknown model"}`}
                    meta={
                      <>
                        {formatTime(session.last_event_time)}
                        <br />
                        {compactNumber(toNumber(session.tool_result_count))} tools
                      </>
                    }
                    href={`/traces/${session.session_id}`}
                  />
                ))
              )
            ) : agentsLoading ? (
              <TableSkeleton rows={3} cols={2} />
            ) : (
              approvedAgents.slice(5, 9).map((agent) => (
                <CompactRow
                  key={agent.id}
                  icon={<EntityGlyph type="agent" size="sm" labelled={false} />}
                  title={
                    agent.namespace && agent.slug
                      ? `${agent.namespace}/${agent.slug}`
                      : agent.name
                  }
                  description={
                    typeof agent.description === "string"
                      ? agent.description
                      : "Approved Agent File"
                  }
                  href={registryItemPath(agent, "agents", agent.id)}
                />
              ))
            )}
          </Panel>
        </RegistryHomeGrid>
      </div>
    </>
  );
}
