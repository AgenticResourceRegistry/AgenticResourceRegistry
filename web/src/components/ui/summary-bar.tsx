// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import * as React from "react"

import { cn } from "@/lib/utils"

/** Responsive health and statistics summary. */
const dotClasses = {
  success: "bg-success",
  warning: "bg-warning",
  destructive: "bg-destructive",
  info: "bg-info",
  neutral: "bg-muted-foreground",
} as const

export type SummaryTone = keyof typeof dotClasses

const SummaryBar = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("overflow-hidden rounded-xl bg-card shadow-sm", className)}
      {...props}
    >
      <div className="-mb-px -mr-px flex flex-wrap items-stretch">{children}</div>
    </div>
  )
)
SummaryBar.displayName = "SummaryBar"

const cellClasses = "min-w-0 border-b border-r border-border px-4 py-3.5"


export function SummaryBarStatus({
  tone = "success",
  pulse = false,
  title,
  detail,
  className,
}: {
  tone?: SummaryTone
  pulse?: boolean
  title: React.ReactNode
  detail?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn(cellClasses, "flex flex-1 basis-[250px] items-center gap-2.5", className)}>
      <span
        aria-hidden="true"
        className={cn(
          "h-2 w-2 shrink-0 rounded-full",
          dotClasses[tone],
          pulse && "animate-breathe"
        )}
      />
      <span className="block min-w-0">
        <strong className="block text-2xs font-medium text-foreground">{title}</strong>
        {detail && (
          <span className="mt-0.5 block text-3xs text-muted-foreground">{detail}</span>
        )}
      </span>
    </div>
  )
}

export function SummaryBarStat({
  label,
  value,
  className,
}: {
  label: React.ReactNode
  value: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn(cellClasses, "flex-1 basis-[125px]", className)}>
      <span className="block text-3xs text-muted-foreground">{label}</span>
      <strong className="mt-[3px] block text-lg font-semibold tabular-nums tracking-[-0.02em] text-foreground">
        {value}
      </strong>
    </div>
  )
}

export { SummaryBar }
