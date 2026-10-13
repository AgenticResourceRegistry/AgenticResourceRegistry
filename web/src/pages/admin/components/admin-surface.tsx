// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function AdminPanel({
  title,
  subtitle,
  action,
  children,
  className,
  contentClassName,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <section className={cn("overflow-hidden rounded-xl bg-card shadow-sm", className)}>
      {(title || subtitle || action) && (
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-medium text-foreground">{title}</h2>}
            {subtitle && <p className="mt-1 text-2xs text-muted-foreground">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      <div className={contentClassName}>{children}</div>
    </section>
  );
}

export function AdminMetricStrip({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid grid-cols-1 gap-px overflow-hidden rounded-xl bg-border shadow-sm sm:grid-cols-2 xl:grid-cols-4",
        className,
      )}
    >
      {children}
    </div>
  );
}

const toneClasses = {
  default: "text-foreground",
  success: "text-success",
  warning: "text-warning",
  destructive: "text-destructive",
  info: "text-info",
} as const;

export function AdminMetric({
  label,
  value,
  detail,
  icon,
  tone = "default",
}: {
  label: ReactNode;
  value: ReactNode;
  detail?: ReactNode;
  icon?: ReactNode;
  tone?: keyof typeof toneClasses;
}) {
  return (
    <div className="min-w-0 bg-card px-5 py-4">
      <div className="flex items-center justify-between gap-3 text-2xs text-muted-foreground">
        <span>{label}</span>
        {icon && (
          <span aria-hidden="true" className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-surface-raised [&_svg]:h-3.5 [&_svg]:w-3.5">
            {icon}
          </span>
        )}
      </div>
      <strong className={cn("mt-3 block text-2xl font-semibold tabular-nums tracking-[-0.025em]", toneClasses[tone])}>
        {value}
      </strong>
      {detail && <p className="mt-1 text-3xs text-muted-foreground">{detail}</p>}
    </div>
  );
}

export function AdminTableFooter({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3">
      {children}
    </div>
  );
}
