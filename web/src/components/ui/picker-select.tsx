// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useId, useMemo, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface PickerSelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

interface PickerSelectProps {
  value: string;
  onValueChange: (value: string) => void;
  options: PickerSelectOption[];
  placeholder?: string;
  emptyLabel?: string;
  disabled?: boolean;
  className?: string;
  inputClassName?: string;
  ariaLabel?: string;
  id?: string;
}

export function PickerSelect({
  value,
  onValueChange,
  options,
  placeholder = "Select...",
  emptyLabel = "No matches",
  disabled,
  className,
  inputClassName,
  ariaLabel,
  id,
}: PickerSelectProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const listboxId = `${inputId}-options`;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const selected = options.find((option) => option.value === value);

  const selectedIndex = (availableOptions: PickerSelectOption[]) =>
    availableOptions.findIndex((option) => option.value === value && !option.disabled);

  const filteredOptions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => `${option.label} ${option.value}`.toLowerCase().includes(needle));
  }, [options, query]);

  useEffect(() => {
    if (!open) {
      setQuery("");
      setActiveIndex(-1);
    }
  }, [open]);

  const openWithSelectedOption = () => {
    if (!open) {
      setActiveIndex(selectedIndex(filteredOptions));
      setOpen(true);
    }
  };

  const choose = (next: string) => {
    onValueChange(next);
    setOpen(false);
  };
  const scrollList = filteredOptions.length > 12;
  const activeOption = filteredOptions[activeIndex];

  const moveActiveOption = (direction: 1 | -1) => {
    const enabledIndexes = filteredOptions
      .map((option, index) => (option.disabled ? -1 : index))
      .filter((index) => index >= 0);
    if (!enabledIndexes.length) return;

    const currentPosition = enabledIndexes.indexOf(activeIndex);
    const nextPosition = currentPosition < 0
      ? (direction === 1 ? 0 : enabledIndexes.length - 1)
      : (currentPosition + direction + enabledIndexes.length) % enabledIndexes.length;
    setActiveIndex(enabledIndexes[nextPosition]);
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div className={cn("relative", className)}>
          <Input
            id={inputId}
            value={open ? query : selected?.label ?? ""}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
            }}
            onFocus={openWithSelectedOption}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setOpen(true);
                moveActiveOption(1);
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setOpen(true);
                moveActiveOption(-1);
              } else if (event.key === "Enter") {
                const optionToChoose = activeOption && !activeOption.disabled
                  ? activeOption
                  : filteredOptions.find((option) => !option.disabled);
                if (optionToChoose) {
                  event.preventDefault();
                  choose(optionToChoose.value);
                }
              } else if (event.key === "Escape") {
                setOpen(false);
              }
            }}
            placeholder={placeholder}
            role="combobox"
            aria-autocomplete="list"
            aria-expanded={open}
            aria-controls={listboxId}
            aria-activedescendant={open && activeIndex >= 0 ? `${listboxId}-${activeIndex}` : undefined}
            aria-label={ariaLabel ?? (id ? undefined : placeholder)}
            disabled={disabled}
            className={cn("pr-9", inputClassName)}
          />
          <button
            type="button"
            onClick={() => {
              if (open) setOpen(false);
              else openWithSelectedOption();
            }}
            disabled={disabled}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded text-muted-foreground hover:text-foreground disabled:pointer-events-none disabled:opacity-50"
            aria-label={open ? "Hide options" : "Show options"}
            aria-expanded={open}
            aria-controls={listboxId}
          >
            <ChevronsUpDown className="h-3.5 w-3.5" />
          </button>
        </div>
      </PopoverAnchor>
      <PopoverContent
        align="start"
        className={cn(
          "w-[var(--radix-popover-trigger-width)] p-1",
          scrollList && "max-h-[min(24rem,var(--radix-popover-content-available-height))] overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden",
        )}
        role="listbox"
        id={listboxId}
      >
        {filteredOptions.length ? (
          filteredOptions.map((option, index) => (
            <button
              key={option.value}
              id={`${listboxId}-${index}`}
              role="option"
              aria-selected={value === option.value}
              type="button"
              disabled={option.disabled}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(option.value)}
              className={cn(
                "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50",
                (value === option.value || activeIndex === index) && "bg-accent text-accent-foreground",
              )}
            >
              <Check className={cn("h-3.5 w-3.5", value === option.value ? "opacity-100" : "opacity-0")} />
              <span className="truncate">{option.label}</span>
            </button>
          ))
        ) : (
          <div className="px-2 py-3 text-xs text-muted-foreground" role="status">{emptyLabel}</div>
        )}
      </PopoverContent>
    </Popover>
  );
}
