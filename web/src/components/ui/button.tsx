// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-[7px] whitespace-nowrap text-xs font-medium transition-[transform,background-color,color,opacity] duration-[120ms] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 active:translate-y-px",
  {
    variants: {
      variant: {
        default: "rounded-[var(--radius-control)] bg-primary text-primary-foreground shadow hover:opacity-85",
        destructive: "rounded-[var(--radius-control-compact)] bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90",
        outline: "rounded-[var(--radius-control-compact)] border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground",
        secondary: "rounded-[var(--radius-control-compact)] bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80",
        ghost: "rounded-[var(--radius-control-compact)] hover:bg-surface-raised hover:text-foreground text-foreground/65",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "min-h-[34px] px-3.5 py-1",
        sm: "min-h-[30px] px-3 text-xs",
        lg: "min-h-[38px] px-5",
        icon: "h-[34px] w-[34px] p-0",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }
