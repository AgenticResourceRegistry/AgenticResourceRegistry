// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import * as React from "react"

import { cn } from "@/lib/utils"
import { Card } from "@/components/ui/card"

/** Responsive two-pane card with a divider between panes. */
export interface SplitViewProps extends React.HTMLAttributes<HTMLDivElement> {
  minHeight?: number | string
}

const SplitView = React.forwardRef<HTMLDivElement, SplitViewProps>(
  ({ className, minHeight = 480, style, ...props }, ref) => (
    <Card
      ref={ref}
      style={{ minHeight, ...style }}
      className={cn(
        "grid grid-cols-1 overflow-hidden",
        "min-[900px]:grid-cols-[minmax(250px,0.72fr)_minmax(0,1.28fr)]",
        "[&>*+*]:border-t [&>*+*]:border-border",
        "min-[900px]:[&>*+*]:border-t-0 min-[900px]:[&>*+*]:border-l",
        className
      )}
      {...props}
    />
  )
)
SplitView.displayName = "SplitView"


const SplitPane = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("min-h-0 min-w-0 p-5", className)} {...props} />
  )
)
SplitPane.displayName = "SplitPane"

export { SplitView, SplitPane }
