// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import * as React from "react"
import { Slot } from "@radix-ui/react-slot"

import { cn } from "@/lib/utils"

/** Selectable list row with optional polymorphic rendering. */
export interface ListItemProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  selected?: boolean
  asChild?: boolean
}

const ListItem = React.forwardRef<HTMLButtonElement, ListItemProps>(
  ({ className, selected = false, asChild = false, type, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        ref={ref}
        {...(asChild ? {} : { type: type ?? "button" })}
        aria-current={selected ? props["aria-current"] ?? "true" : props["aria-current"]}
        className={cn(
          "flex w-full items-center gap-[11px] rounded-lg p-[11px] text-left transition-colors duration-[120ms] hover:bg-surface-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          selected && "bg-surface-raised ring-1 ring-border",
          className
        )}
        {...props}
      />
    )
  }
)
ListItem.displayName = "ListItem"

const ListStack = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("grid gap-1", className)} {...props} />
  )
)
ListStack.displayName = "ListStack"

const ListItemLeading = React.forwardRef<HTMLSpanElement, React.HTMLAttributes<HTMLSpanElement>>(
  ({ className, ...props }, ref) => (
    <span ref={ref} className={cn("flex shrink-0 items-center", className)} {...props} />
  )
)
ListItemLeading.displayName = "ListItemLeading"

const ListItemCopy = React.forwardRef<HTMLSpanElement, React.HTMLAttributes<HTMLSpanElement>>(
  ({ className, ...props }, ref) => (
    <span ref={ref} className={cn("block min-w-0 flex-1", className)} {...props} />
  )
)
ListItemCopy.displayName = "ListItemCopy"

const ListItemTitle = React.forwardRef<HTMLSpanElement, React.HTMLAttributes<HTMLSpanElement>>(
  ({ className, ...props }, ref) => (
    <span
      ref={ref}
      className={cn("block truncate text-xs font-medium text-foreground", className)}
      {...props}
    />
  )
)
ListItemTitle.displayName = "ListItemTitle"

const ListItemSub = React.forwardRef<HTMLSpanElement, React.HTMLAttributes<HTMLSpanElement>>(
  ({ className, ...props }, ref) => (
    <span
      ref={ref}
      className={cn("block truncate text-3xs text-muted-foreground", className)}
      {...props}
    />
  )
)
ListItemSub.displayName = "ListItemSub"

const ListItemTrailing = React.forwardRef<HTMLSpanElement, React.HTMLAttributes<HTMLSpanElement>>(
  ({ className, ...props }, ref) => (
    <span
      ref={ref}
      className={cn("ml-auto flex shrink-0 items-center gap-2", className)}
      {...props}
    />
  )
)
ListItemTrailing.displayName = "ListItemTrailing"

export {
  ListItem,
  ListStack,
  ListItemLeading,
  ListItemCopy,
  ListItemTitle,
  ListItemSub,
  ListItemTrailing,
}
