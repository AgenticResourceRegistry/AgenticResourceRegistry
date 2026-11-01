// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Shreem Seth <shreemseth26@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { Link } from "@tanstack/react-router";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";
import { CommandMenu } from "@/components/nav/command-menu";

export interface BreadcrumbEntry {
  label: string;
  href?: string;
}

interface TabDef {
  value: string;
  label: string;
  href: string;
}

interface PageHeaderProps {
  title: string;
  breadcrumbs?: BreadcrumbEntry[];
  children?: React.ReactNode;
  actionButtonsLeft?: React.ReactNode;
  actionButtonsRight?: React.ReactNode;
  tabs?: TabDef[];
  activeTab?: string;
}

export function PageHeader({
  title,
  breadcrumbs,
  children,
  actionButtonsLeft,
  actionButtonsRight,
  tabs,
  activeTab,
}: PageHeaderProps) {

  // Build breadcrumb text: "Group / Page" with the last entry bolded
  const crumbParts = breadcrumbs ?? [];
  const lastCrumb = crumbParts[crumbParts.length - 1];
  const parentCrumbs = crumbParts.slice(0, -1);

  return (
    <header className="sticky top-0 z-30 flex min-h-[54px] items-center gap-3.5 border-b bg-background px-[30px]">
      <h1 className="sr-only">{title}</h1>
      {/* Sidebar toggle */}
      <SidebarTrigger className="h-[34px] w-[34px] shrink-0 rounded-[9px] text-foreground/65 hover:bg-surface-raised hover:text-foreground" />

      {/* Breadcrumb */}
      {crumbParts.length > 0 && (
        <nav className="min-w-0 truncate text-xs text-muted-foreground">
          {parentCrumbs.map((crumb, i) => (
            <span key={i}>
              {crumb.href ? (
                <Link to={crumb.href} className="hover:text-foreground">
                  {crumb.label}
                </Link>
              ) : (
                crumb.label
              )}
              <span className="mx-1.5">/</span>
            </span>
          ))}
          {lastCrumb && (
            <strong className="text-[13px] font-medium text-foreground">
              {lastCrumb.label}
            </strong>
          )}
        </nav>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Page-level action buttons placed in the header */}
      {actionButtonsLeft}
      {actionButtonsRight}
      {children}

      {/* Search button trigger */}
      <CommandMenu />

    </header>
  );
}

export function PageIntro({
  eyebrow,
  title,
  subtitle,
  children,
  size = "page",
  className,
}: {
  eyebrow?: string;
  title?: string;
  subtitle?: string;
  children?: React.ReactNode;
  size?: "page" | "pane";
  className?: string;
}) {
  const isPane = size === "pane";

  return (
    <div
      className={cn(
        isPane ? "" : "mb-[24px] flex flex-wrap items-end justify-between gap-5",
        className
      )}
    >
      <div>
        {eyebrow && (
          <p className="mb-[5px] text-2xs font-medium uppercase tracking-[0.06em] text-muted-foreground">
            {eyebrow}
          </p>
        )}
        {title && (
          <h1
            className={cn(
              "font-medium leading-[1.3] tracking-[-0.025em]",
              isPane ? "text-xl" : "text-2xl"
            )}
          >
            {title}
          </h1>
        )}
        {subtitle && (
          <p className="mt-[6px] text-[13px] text-muted-foreground">{subtitle}</p>
        )}
      </div>
      {children && (
        <div className={cn("flex items-center gap-2", isPane && "mt-3 flex-wrap")}>
          {children}
        </div>
      )}
    </div>
  );
}
