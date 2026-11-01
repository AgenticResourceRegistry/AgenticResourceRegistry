// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import * as React from "react"
import { Search } from "lucide-react"

import { cn } from "@/lib/utils"

/** Controlled search input with optional shortcut and leading icon. */
export interface SearchFieldProps
  extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "size" | "value" | "onChange" | "type"> {
  value: string
  onValueChange: (value: string) => void
  mono?: boolean
  kbd?: React.ReactNode
  size?: "sm" | "md"
  icon?: React.ReactNode
  containerClassName?: string
  inputClassName?: string
}

const SearchField = React.forwardRef<HTMLInputElement, SearchFieldProps>(
  (
    {
      value,
      onValueChange,
      mono = false,
      kbd,
      size = "md",
      icon,
      className,
      containerClassName,
      inputClassName,
      disabled,
      ...props
    },
    ref
  ) => (
    <div
      className={cn(
        "flex items-center gap-2.5 rounded-lg border border-border bg-card py-1.5 pl-3.5 pr-2.5",
        "focus-within:outline-none focus-within:ring-2 focus-within:ring-ring",
        size === "sm" ? "min-h-[38px]" : "min-h-[44px]",
        disabled && "opacity-50",
        className,
        containerClassName
      )}
    >
      <span aria-hidden="true" className="flex shrink-0 text-muted-foreground [&_svg]:h-4 [&_svg]:w-4">
        {icon ?? <Search />}
      </span>
      <input
        ref={ref}
        {...props}
        type="search"
        value={value}
        disabled={disabled}
        onChange={(e) => onValueChange(e.target.value)}
        className={cn(
          "min-w-0 flex-1 border-0 bg-transparent p-0 text-foreground outline-none",
          "placeholder:text-muted-foreground/60 disabled:cursor-not-allowed",
          "[&::-webkit-search-cancel-button]:appearance-none",
          mono ? "font-mono text-2xs" : "text-xs",
          inputClassName
        )}
      />
      {kbd && (
        <kbd className="ml-auto hidden shrink-0 rounded-sm bg-surface-raised px-1.5 py-0.5 font-mono text-3xs font-normal text-muted-foreground sm:inline-block">
          {kbd}
        </kbd>
      )}
    </div>
  )
)
SearchField.displayName = "SearchField"

export { SearchField }
