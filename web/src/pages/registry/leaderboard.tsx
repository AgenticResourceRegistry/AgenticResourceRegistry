// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/** Registry rankings based exclusively on live leaderboard data. */

import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Search, Users } from "lucide-react";
import { PageHeader } from "@/components/layouts/page-header";
import { EmptyState } from "@/components/shared/empty-state";
import { ErrorState } from "@/components/shared/error-state";
import { TableSkeleton } from "@/components/shared/skeleton-layouts";
import { Input } from "@/components/ui/input";
import {
  LeaderFeatureCard,
  RankingHead,
  RankingRow,
  SegmentedControl,
} from "@/components/registry/registry-primitives";
import { useComponentLeaderboard, useLeaderboard } from "@/hooks/use-api";
import { LEADERBOARD_DEPRECATION_NOTICE } from "@/lib/leaderboard";
import { registryItemPath, type RegistryRouteType } from "@/lib/registry-name";
import { compactNumber } from "@/lib/utils";
import type { LeaderboardWindow } from "@/lib/types";

type TopTab = "agents" | "components";
type SubTab = "leaderboard" | "users";

interface UserAggregate {
  email: string;
  username?: string | null;
  totalDownloads: number;
  itemCount: number;
}

function componentRouteType(type: string): RegistryRouteType {
  return (
    {
      mcp: "mcps",
      skill: "skills",
      hook: "hooks",
      prompt: "prompts",
      sandbox: "sandboxes",
    } as const
  )[type] ?? "mcps";
}

function UserRankings({ users, itemLabel }: { users: UserAggregate[]; itemLabel: string }) {
  if (users.length === 0) {
    return (
      <EmptyState
        icon={Users}
        title="No publisher rankings yet"
        description={`Publisher totals will appear once approved ${itemLabel.toLowerCase()} record downloads.`}
      />
    );
  }

  return (
    <section className="overflow-hidden rounded-xl bg-card shadow-sm">
      <div className="flex items-center justify-between border-b border-border px-5 py-4">
        <div>
          <h2 className="text-sm font-medium">Publisher rankings</h2>
          <p className="mt-0.5 text-2xs text-muted-foreground">
            Creators ranked by total downloads across their approved {itemLabel.toLowerCase()}.
          </p>
        </div>
      </div>
      <div className="grid grid-cols-[42px_minmax(0,1fr)_100px_120px] items-center gap-3 border-b border-border px-5 py-2.5 text-2xs font-medium uppercase tracking-[0.05em] text-muted-foreground">
        <span>Rank</span>
        <span>Publisher</span>
        <span className="text-right">{itemLabel}</span>
        <span className="text-right">Downloads</span>
      </div>
      {users.map((user, index) => (
        <div
          key={user.email}
          className="grid grid-cols-[42px_minmax(0,1fr)_100px_120px] items-center gap-3 border-b border-border px-5 py-3 last:border-b-0"
        >
          <span className="font-mono text-xs text-muted-foreground">{index + 1}</span>
          <div className="min-w-0">
            <strong className="block truncate text-xs font-medium">{user.username ? `@${user.username}` : user.email}</strong>
            {user.username && <span className="block truncate text-2xs text-muted-foreground">{user.email}</span>}
          </div>
          <span className="text-right font-mono text-xs text-muted-foreground">{user.itemCount}</span>
          <span className="text-right font-mono text-xs text-muted-foreground">{compactNumber(user.totalDownloads)}</span>
        </div>
      ))}
    </section>
  );
}

export default function LeaderboardPage() {
  const [topTab, setTopTab] = useState<TopTab>("agents");
  const [agentSubTab, setAgentSubTab] = useState<SubTab>("leaderboard");
  const [componentSubTab, setComponentSubTab] = useState<SubTab>("leaderboard");
  const [window, setWindow] = useState<LeaderboardWindow>("7d");
  const [userFilterInput, setUserFilterInput] = useState("");
  const [userFilter, setUserFilter] = useState("");

  useEffect(() => {
    const timer = globalThis.setTimeout(() => setUserFilter(userFilterInput.trim()), 300);
    return () => globalThis.clearTimeout(timer);
  }, [userFilterInput]);

  const { data: leaderboard, isLoading: agentsLoading, isError: agentsError, error: agentsErrorDetail, refetch: refetchAgents } =
    useLeaderboard(window, 50, userFilter || undefined);
  const {
    data: componentLeaderboard,
    isLoading: componentsLoading,
    isError: componentsError,
    error: componentsErrorDetail,
    refetch: refetchComponents,
  } = useComponentLeaderboard(window, 50, userFilter || undefined);
  const isAgents = topTab === "agents";
  const isLoading = isAgents ? agentsLoading : componentsLoading;
  const isError = isAgents ? agentsError : componentsError;
  const error = isAgents ? agentsErrorDetail : componentsErrorDetail;
  const refetch = isAgents ? refetchAgents : refetchComponents;
  const subTab = isAgents ? agentSubTab : componentSubTab;
  const setSubTab = isAgents ? setAgentSubTab : setComponentSubTab;

  const rankings = useMemo(() => {
    if (topTab === "agents") {
      if (!leaderboard?.length) return [];
      return [...leaderboard]
        .sort((a, b) => b.download_count - a.download_count)
        .map((item, index) => ({
          id: item.id,
          name: item.name,
          handle: item.namespace ? `${item.namespace}/${item.slug ?? item.name}` : item.name,
          description: item.description ?? "",
          downloads: compactNumber(item.download_count),
          rating: item.average_rating?.toFixed(1) ?? "New",
          href: registryItemPath(item, "agents", item.id),
          position: index + 1,
        }));
    }

    if (!componentLeaderboard?.length) return [];
    return [...componentLeaderboard]
      .sort((a, b) => b.download_count - a.download_count)
      .map((item, index) => ({
        id: item.id,
        name: item.name,
        handle: item.namespace ? `${item.namespace}/${item.slug ?? item.name}` : item.name,
        description: item.description,
        downloads: compactNumber(item.download_count),
        rating: item.average_rating?.toFixed(1) ?? "New",
        href: registryItemPath(item, componentRouteType(item.component_type), item.id),
        position: index + 1,
      }));
  }, [componentLeaderboard, leaderboard, topTab]);

  const userRankings = useMemo<UserAggregate[]>(() => {
    const items = topTab === "agents" ? leaderboard : componentLeaderboard;
    if (!items) return [];

    const users = new Map<string, UserAggregate>();
    for (const item of items) {
      const email = item.created_by_email || ("owner" in item ? item.owner : "") || "Unknown publisher";
      const existing = users.get(email);
      if (existing) {
        existing.totalDownloads += item.download_count;
        existing.itemCount += 1;
      } else {
        users.set(email, {
          email,
          username: "created_by_username" in item ? item.created_by_username : null,
          totalDownloads: item.download_count,
          itemCount: 1,
        });
      }
    }
    return [...users.values()].sort((a, b) => b.totalDownloads - a.totalDownloads);
  }, [componentLeaderboard, leaderboard, topTab]);

  const featuredItem = rankings[0];
  const entityLabel = isAgents ? "Agent File" : "Component";

  return (
    <>
      <PageHeader
        title="Leaderboard"
        breadcrumbs={[
          { label: "Registry", href: "/" },
          { label: "Leaderboard" },
        ]}
      />

      <div className="page-body w-full mx-auto space-y-0">
        <div
          role="status"
          className="mb-4 flex items-start gap-3 rounded-md border border-warning/40 bg-warning/10 px-4 py-3"
        >
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-warning" aria-hidden="true" />
          <p className="text-sm text-foreground">{LEADERBOARD_DEPRECATION_NOTICE}</p>
        </div>
        <div className="mb-4 flex flex-wrap items-center gap-2.5">
          <SegmentedControl
            options={[
              { value: "agents", label: "Agent Files" },
              { value: "components", label: "Components" },
            ]}
            value={topTab}
            onChange={(value) => setTopTab(value as TopTab)}
          />
          <SegmentedControl
            options={[
              { value: "leaderboard", label: "Rankings" },
              { value: "users", label: "Publishers" },
            ]}
            value={subTab}
            onChange={(value) => setSubTab(value as SubTab)}
          />
          <div className="relative min-w-[220px] flex-1 sm:max-w-xs">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              aria-label="Filter by publisher email or username"
              placeholder="Filter by email or username..."
              value={userFilterInput}
              onChange={(event) => setUserFilterInput(event.target.value)}
              className="h-[34px] pl-9 text-xs"
            />
          </div>
          <div className="ml-auto">
            <SegmentedControl
              options={[
                { value: "24h", label: "24h" },
                { value: "7d", label: "7 days" },
                { value: "30d", label: "30 days" },
                { value: "all", label: "All time" },
              ]}
              value={window}
              onChange={(value) => setWindow(value as LeaderboardWindow)}
            />
          </div>
        </div>

        {isLoading ? (
          <TableSkeleton rows={8} cols={4} />
        ) : isError ? (
          <ErrorState message={error?.message} onRetry={() => refetch()} />
        ) : subTab === "users" ? (
          <UserRankings users={userRankings} itemLabel={entityLabel} />
        ) : rankings.length === 0 ? (
          <EmptyState
            title={`No ${topTab} rankings yet`}
            description={`Approved ${topTab} will appear once they have recorded downloads in this period.`}
          />
        ) : (
          <>
            <section className="mb-3.5">
              <LeaderFeatureCard
                rank="Most adopted in this period"
                title={featuredItem.name}
                handle={featuredItem.handle}
                description={featuredItem.description || `${entityLabel} adoption is based on recorded Registry downloads.`}
                stats={[
                  { label: "Downloads", value: featuredItem.downloads },
                  { label: "Rating", value: featuredItem.rating },
                ]}
              />
            </section>

            <section className="overflow-hidden rounded-xl bg-card shadow-sm">
              <div className="flex items-center justify-between border-b border-border px-5 py-4">
                <div>
                  <h2 className="text-sm font-medium">{entityLabel} rankings</h2>
                  <p className="mt-0.5 text-2xs text-muted-foreground">
                    Approved {topTab} ranked by downloads in the selected period.
                  </p>
                </div>
              </div>
              <RankingHead entityLabel={entityLabel} />
              {rankings.map((ranking) => (
                <RankingRow
                  key={ranking.id}
                  position={ranking.position}
                  downloads={ranking.downloads}
                  rating={ranking.rating}
                  isTop={ranking.position <= 3}
                >
                  <Link
                    to={ranking.href}
                    className="block rounded-sm outline-none hover:text-primary focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <strong className="block text-xs font-medium">{ranking.name}</strong>
                    <span className="block mt-0.5 font-mono text-[10px] text-muted-foreground">
                      {ranking.handle}
                    </span>
                  </Link>
                </RankingRow>
              ))}
            </section>
          </>
        )}
      </div>
    </>
  );
}
