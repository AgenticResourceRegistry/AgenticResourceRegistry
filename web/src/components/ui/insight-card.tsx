// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import * as React from "react"

import { cn } from "@/lib/utils"

/** A themed symbol-and-copy evidence row. */
const toneClasses = {
  primary: "bg-primary/12 text-primary",
  success: "bg-success/12 text-success",
  warning: "bg-warning/12 text-warning",
  destructive: "bg-destructive/12 text-destructive",
  info: "bg-info/12 text-info",
} as const

export type InsightTone = keyof typeof toneClasses

const InsightList = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("grid gap-2.5", className)} {...props} />
  )
)
InsightList.displayName = "InsightList"

export function InsightSymbol({
  tone = "primary",
  className,
  children,
}: {
  tone?: InsightTone
  className?: string
  children?: React.ReactNode
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "grid h-[34px] w-[34px] shrink-0 place-items-center rounded-full text-base leading-none [&_svg]:h-4 [&_svg]:w-4",
        toneClasses[tone],
        className
      )}
    >
      {children}
    </span>
  )
}

export interface InsightCardProps
  extends Omit<React.HTMLAttributes<HTMLDivElement>, "title"> {
  symbol?: React.ReactNode
  tone?: InsightTone
  title: React.ReactNode
  copy?: React.ReactNode
  children?: React.ReactNode
}

const InsightCard = React.forwardRef<HTMLDivElement, InsightCardProps>(
  ({ className, symbol, tone = "primary", title, copy, children, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "grid grid-cols-[34px_minmax(0,1fr)_auto] items-center gap-3 rounded-lg bg-surface-raised p-3.5",
        className
      )}
      {...props}
    >
      <InsightSymbol tone={tone}>{symbol}</InsightSymbol>
      <span className="block min-w-0">
        <span className="block text-xs font-medium text-foreground">{title}</span>
        {copy && (
          <span className="mt-0.5 block text-2xs leading-[1.45] text-muted-foreground">
            {copy}
          </span>
        )}
      </span>
      <span className="flex items-center justify-end gap-2">{children}</span>
    </div>
  )
)
InsightCard.displayName = "InsightCard"

export { InsightList, InsightCard }
