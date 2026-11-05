// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import * as React from "react"

import { cn } from "@/lib/utils"

/** Responsive, accessible sequence of connected steps. */
export type StepState = "done" | "current" | "upcoming"

export interface StepPathStep {
  label: React.ReactNode
  value?: React.ReactNode
  state?: StepState
}

export interface StepPathProps extends Omit<React.HTMLAttributes<HTMLOListElement>, "children"> {
  steps: StepPathStep[]
  columns?: number
  size?: "md" | "sm"
}

const dotState: Record<StepState, string> = {
  done: "bg-success ring-1 ring-success",
  current: "bg-primary ring-2 ring-primary animate-breathe",
  upcoming: "bg-surface-raised ring-1 ring-border",
}

const connectorState: Record<StepState, string> = {
  done: "bg-connector-done",
  current: "bg-border",
  upcoming: "bg-border",
}

const StepPath = React.forwardRef<HTMLOListElement, StepPathProps>(
  ({ steps, columns, size = "md", className, style, ...props }, ref) => {
    const cols = Math.max(1, columns ?? steps.length)
    const isSm = size === "sm"

    return (
      <ol
        ref={ref}
        style={{ ["--step-columns" as string]: cols, ...style }}
        className={cn(
          "grid grid-cols-2 gap-x-0 gap-y-3",
          "min-[560px]:gap-y-0 min-[560px]:[grid-template-columns:repeat(var(--step-columns),minmax(0,1fr))]",
          className
        )}
        {...props}
      >
        {steps.map((step, i) => {
          const state = step.state ?? "upcoming"
          const isLast = i === steps.length - 1
          const isCurrent = state === "current"

          return (
            <li
              key={i}
              aria-current={isCurrent ? "step" : undefined}
              className={cn(
                "relative min-w-0 text-muted-foreground",
                isSm ? "pt-[13px] font-mono text-4xs" : "pt-[15px] text-3xs"
              )}
            >
              <span
                aria-hidden="true"
                className={cn(
                  "absolute left-0 z-[1] rounded-full border-2 border-card",
                  dotState[state],
                  isSm ? "top-[3px] h-1.5 w-1.5" : "top-1 h-[7px] w-[7px]"
                )}
              />
              {!isLast && (
                <span
                  aria-hidden="true"
                  className={cn(
                    "absolute h-px",
                    connectorState[state],
                    isSm
                      ? "left-[7px] top-1.5 w-[calc(100%-7px)]"
                      : "left-2 top-[7px] w-[calc(100%-8px)]",
                    i % 2 === 1 && "max-[559px]:hidden"
                  )}
                />
              )}
              <span className="block truncate">
                {step.label}
                {isCurrent && <span className="sr-only"> (current step)</span>}
              </span>
              {step.value !== undefined && (
                <strong
                  className={cn(
                    "mt-[3px] block truncate",
                    isSm ? "text-4xs" : "text-3xs",
                    state === "upcoming"
                      ? "font-medium text-muted-foreground"
                      : isCurrent
                        ? "font-semibold text-foreground"
                        : "font-medium text-foreground"
                  )}
                >
                  {step.value}
                </strong>
              )}
            </li>
          )
        })}
      </ol>
    )
  }
)
StepPath.displayName = "StepPath"

export { StepPath }
