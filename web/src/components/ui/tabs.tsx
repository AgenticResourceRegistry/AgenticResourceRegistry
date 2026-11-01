// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import * as React from "react"
import * as TabsPrimitive from "@radix-ui/react-tabs"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const Tabs = TabsPrimitive.Root

const tabsListVariants = cva("text-muted-foreground", {
  variants: {
    variant: {
      pill: "inline-flex items-center justify-center gap-0 rounded-[var(--radius-control)] bg-surface-raised p-1",
      rail: "flex w-full items-center justify-start gap-1 rounded-none border-b border-border bg-transparent p-0 pb-2",
      ghost: "flex w-full items-center justify-start gap-1 overflow-x-auto rounded-none bg-transparent p-0",
    },
  },
  defaultVariants: { variant: "pill" },
})

const tabsTriggerVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 hover:text-foreground data-[state=active]:text-foreground",
  {
    variants: {
      variant: {
        // Active: bg-card (surface/white) + shadow-sm.
        pill: "rounded-lg px-3 py-[7px] text-xs text-muted-foreground data-[state=active]:bg-card data-[state=active]:shadow-sm",
        rail: "shrink-0 rounded-md bg-transparent px-3 py-[7px] text-2xs text-muted-foreground data-[state=active]:bg-surface-raised data-[state=active]:shadow-none",
        ghost: "shrink-0 rounded-md bg-transparent px-3 py-[7px] text-3xs text-muted-foreground data-[state=active]:bg-surface-raised data-[state=active]:shadow-none",
      },
    },
    defaultVariants: { variant: "pill" },
  }
)

const TabsList = React.forwardRef<
  React.ComponentRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List> &
    VariantProps<typeof tabsListVariants>
>(({ className, variant, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn(tabsListVariants({ variant }), className)}
    {...props}
  />
))
TabsList.displayName = TabsPrimitive.List.displayName

const TabsTrigger = React.forwardRef<
  React.ComponentRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger> &
    VariantProps<typeof tabsTriggerVariants>
>(({ className, variant, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(tabsTriggerVariants({ variant }), className)}
    {...props}
  />
))
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName

const TabsContent = React.forwardRef<
  React.ComponentRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn(
      "mt-2 ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
      className
    )}
    {...props}
  />
))
TabsContent.displayName = TabsPrimitive.Content.displayName

export {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
  tabsListVariants,
  tabsTriggerVariants,
}
