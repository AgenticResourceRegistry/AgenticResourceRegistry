// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { Suspense } from "react";
import { useLocation, useSearch } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { PageHeader } from "@/components/layouts/page-header";
import { Button } from "@/components/ui/button";
import { AdoptionTab } from "./components/adoption-tab";
import { CostTab } from "./components/cost-tab";
import { InvestmentsTab } from "./components/investments-tab";
import { InsightsTab } from "./components/insights-tab";
import { DepartmentsTab } from "./components/departments-tab";
import { VelocityTab } from "./components/velocity-tab";
import { useExecAdoption, useExecAgentCounts, useExecConfig } from "@/hooks/use-api";
import { RefreshCw, Calendar, Rocket, Download } from "lucide-react";
import { useState, useCallback } from "react";
import { DashboardRangeContext } from "./context";

const TABS = ["adoption", "cost", "investments", "insights", "departments", "velocity"] as const;
type TabId = typeof TABS[number];

const RANGES = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
] as const;

function OnboardingWizard({ onDismiss }: { onDismiss: () => void }) {
  return (
    <section className="rounded-xl border border-dashed border-primary/30 bg-primary/5 p-5 sm:p-6" aria-labelledby="dashboard-welcome-title">
      <div className="flex items-start gap-4">
        <div className="mt-0.5 rounded-full bg-primary/10 p-2.5">
          <Rocket className="h-5 w-5 text-primary" />
        </div>
        <div className="flex-1">
          <h2 id="dashboard-welcome-title" className="mb-1 text-base font-semibold">Welcome to the Executive Dashboard</h2>
          <p className="mb-4 text-sm text-muted-foreground">
            Set up these three things to unlock the full dashboard experience:
          </p>
          <ol className="space-y-3">
            <li className="flex items-start gap-3">
              <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">1</span>
              <div>
                <p className="text-sm font-medium">Assign departments to users</p>
                <p className="text-xs text-muted-foreground">
                  Go to Users &rarr; click a user &rarr; set their department. Or configure SSO groups for automatic mapping.
                </p>
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">2</span>
              <div>
                <p className="text-sm font-medium">Set cost baselines</p>
                <p className="text-xs text-muted-foreground">
                  Open the Cost Intelligence tab and enter what tasks cost before AI. This enables savings calculations and ROI projections.
                </p>
              </div>
            </li>
            <li className="flex items-start gap-3">
              <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">3</span>
              <div>
                <p className="text-sm font-medium">Categorize your Agent Files</p>
                <p className="text-xs text-muted-foreground">
                  In the Agent File Builder, assign categories (Code Review, Testing, etc.) so the dashboard can group usage and costs.
                </p>
              </div>
            </li>
          </ol>
          <button
            onClick={onDismiss}
            className="mt-4 text-xs text-muted-foreground hover:text-foreground transition-colors underline underline-offset-2"
          >
            Dismiss — I&apos;ll set this up later
          </button>
        </div>
      </div>
    </section>
  );
}

function ExportDropdown({ activeTab }: { activeTab: string }) {
  const [open, setOpen] = useState(false);

  const handleCSV = useCallback(() => {
    setOpen(false);
    // Collect visible table data from the DOM
    const tables = document.querySelectorAll("table");
    if (tables.length === 0) {
      alert("No table data on this tab. Switch to Departments, Velocity, or Investments for exportable data.");
      return;
    }
    const rows: string[] = [];
    tables.forEach((table) => {
      const headers = Array.from(table.querySelectorAll("thead th")).map((th) => th.textContent?.trim() ?? "");
      if (headers.length > 0) rows.push(headers.join(","));
      table.querySelectorAll("tbody tr").forEach((tr) => {
        const cells = Array.from(tr.querySelectorAll("td")).map((td) => {
          const text = td.textContent?.trim()?.replace(/,/g, " ") ?? "";
          return text;
        });
        if (cells.length > 0) rows.push(cells.join(","));
      });
      rows.push("");
    });
    const blob = new Blob([rows.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `observal-dashboard-${activeTab}-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [activeTab]);

  const handlePrint = useCallback(() => {
    setOpen(false);
    window.print();
  }, []);

  return (
    <div className="relative">
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="menu"
      >
        <Download className="h-3.5 w-3.5" />
        Export
      </Button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-50 mt-1 min-w-[140px] rounded-lg border border-border bg-card py-1 shadow-md" role="menu">
            <button
              onClick={handleCSV}
              className="w-full px-3 py-2 text-left text-xs transition-colors hover:bg-surface-raised"
              role="menuitem"
            >
              Export as CSV
            </button>
            <button
              onClick={handlePrint}
              className="w-full px-3 py-2 text-left text-xs transition-colors hover:bg-surface-raised"
              role="menuitem"
            >
              Print / PDF
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function DashboardContent() {
  const { tab: tabParam, range: rangeParam } = useSearch({ from: "/_authed/_admin/dashboard" });
  const { pathname } = useLocation();
  const queryClient = useQueryClient();

  const [activeTab, setActiveTab] = useState<TabId>(() =>
    tabParam && TABS.includes(tabParam as TabId) ? (tabParam as TabId) : "adoption"
  );

  const [activeRange, setActiveRange] = useState(() =>
    rangeParam && ["7d", "30d", "90d"].includes(rangeParam) ? rangeParam : "30d"
  );

  const [refreshing, setRefreshing] = useState(false);
  const [wizardDismissed, setWizardDismissed] = useState(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("observal_dash_onboarding_dismissed") === "1";
    }
    return false;
  });

  const { data: adoption } = useExecAdoption();
  const { data: agents } = useExecAgentCounts();
  const { data: config } = useExecConfig();

  const showOnboarding = !wizardDismissed && (
    (adoption?.departments_covered ?? 0) === 0 &&
    !config &&
    (agents?.total ?? 0) === 0
  );

  const handleTabChange = useCallback((value: string) => {
    setActiveTab(value as TabId);
    const params = new URLSearchParams(window.location.search);
    params.set("tab", value);
    window.history.replaceState(null, "", `${pathname}?${params.toString()}`);
  }, [pathname]);

  const handleRangeChange = useCallback((value: string) => {
    setActiveRange(value);
    const params = new URLSearchParams(window.location.search);
    params.set("range", value);
    window.history.replaceState(null, "", `${pathname}?${params.toString()}`);
  }, [pathname]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    await queryClient.invalidateQueries({
      queryKey: ["exec"],
      predicate: (query) => query.queryKey[1] !== "ai-insights",
    });
    setTimeout(() => setRefreshing(false), 600);
  }, [queryClient]);

  const handleDismissWizard = useCallback(() => {
    setWizardDismissed(true);
    localStorage.setItem("observal_dash_onboarding_dismissed", "1");
  }, []);

  return (
    <DashboardRangeContext.Provider value={activeRange}>
      <PageHeader
        title="Executive Dashboard"
        breadcrumbs={[{ label: "Administration" }, { label: "Dashboard" }]}
      />
      <div className="page-body mx-auto w-full">
        <div className="space-y-5">
          {showOnboarding && <OnboardingWizard onDismiss={handleDismissWizard} />}

          <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full">
            <div className="flex flex-col gap-2 xl:flex-row xl:items-center">
              <div className="min-w-0 flex-1 overflow-x-auto rounded-xl bg-surface-raised p-1">
                <TabsList className="grid min-w-[720px] grid-cols-6 bg-transparent p-0 shadow-none">
                  <TabsTrigger value="adoption">AI Adoption</TabsTrigger>
                  <TabsTrigger value="cost">Cost Intelligence</TabsTrigger>
                  <TabsTrigger value="investments">Investments</TabsTrigger>
                  <TabsTrigger value="insights">AI Insights</TabsTrigger>
                  <TabsTrigger value="departments">Departments</TabsTrigger>
                  <TabsTrigger value="velocity">Velocity</TabsTrigger>
                </TabsList>
              </div>
              <div className="flex flex-wrap items-center gap-2 xl:shrink-0">
                <div className="flex items-center gap-1 rounded-xl bg-surface-raised p-1" aria-label="Dashboard date range">
                  <Calendar className="ml-2 h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                  {RANGES.map((r) => (
                    <button
                      key={r.value}
                      onClick={() => handleRangeChange(r.value)}
                      aria-pressed={activeRange === r.value}
                      className={`rounded-lg px-3 py-2 text-xs font-medium transition-colors ${
                        activeRange === r.value
                          ? "bg-card text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
                <ExportDropdown activeTab={activeTab} />
                <Button variant="outline" size="sm" onClick={handleRefresh} disabled={refreshing}>
                  <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
                  Refresh
                </Button>
              </div>
            </div>

            <TabsContent value="adoption">
            <AdoptionTab />
          </TabsContent>

          <TabsContent value="cost">
            <CostTab />
          </TabsContent>

          <TabsContent value="investments">
            <InvestmentsTab />
          </TabsContent>

          <TabsContent value="insights">
            <InsightsTab />
          </TabsContent>

          <TabsContent value="departments">
            <DepartmentsTab />
          </TabsContent>

            <TabsContent value="velocity">
              <VelocityTab />
            </TabsContent>
          </Tabs>
        </div>
      </div>
    </DashboardRangeContext.Provider>
  );
}

export default function DashboardPage() {
  return (
    <Suspense fallback={<div className="p-6 animate-pulse" />}>
      <DashboardContent />
    </Suspense>
  );
}
