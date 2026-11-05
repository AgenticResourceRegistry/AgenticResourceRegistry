// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState, useRef, useEffect } from "react";
import { Check, ChevronDown, X } from "lucide-react";
import { cn } from "@/lib/utils";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import type { ModelSuggestion } from "./insights-constants";

interface ModelComboboxProps {
	value: string;
	onChange: (value: string) => void;
	suggestions: ModelSuggestion[];
	label: string;
	subtitle: string;
	placeholder?: string;
	disabled?: boolean;
	/** Render the label for screen readers only, when a settings row already shows it. */
	hideLabel?: boolean;
}

export function ModelCombobox({
	value,
	onChange,
	suggestions,
	label,
	subtitle,
	placeholder = "Select or type model...",
	disabled,
	hideLabel,
}: ModelComboboxProps) {
	const [open, setOpen] = useState(false);
	const [inputValue, setInputValue] = useState(value);
	const inputRef = useRef<HTMLInputElement>(null);

	useEffect(() => {
		setInputValue(value);
	}, [value]);

	const primaryModels = suggestions.filter((s) => s.tier === "primary");
	const fastModels = suggestions.filter((s) => s.tier === "fast");
	const catalogModels = suggestions.filter((s) => s.tier === "catalog");

	const filteredPrimary = primaryModels.filter(
		(s) =>
			!inputValue ||
			s.id.toLowerCase().includes(inputValue.toLowerCase()) ||
			s.label.toLowerCase().includes(inputValue.toLowerCase()),
	);
	const filteredFast = fastModels.filter(
		(s) =>
			!inputValue ||
			s.id.toLowerCase().includes(inputValue.toLowerCase()) ||
			s.label.toLowerCase().includes(inputValue.toLowerCase()),
	);
	const filteredCatalog = catalogModels.filter(
		(s) =>
			!inputValue ||
			s.id.toLowerCase().includes(inputValue.toLowerCase()) ||
			s.label.toLowerCase().includes(inputValue.toLowerCase()),
	);

	const handleSelect = (modelId: string) => {
		setInputValue(modelId);
		onChange(modelId);
		setOpen(false);
	};

	const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
		setInputValue(e.target.value);
		if (!open) setOpen(true);
	};

	const handleInputKeyDown = (e: React.KeyboardEvent) => {
		if (e.key === "Enter") {
			e.preventDefault();
			onChange(inputValue);
			setOpen(false);
		}
		if (e.key === "Escape") {
			setOpen(false);
		}
	};

	const handleInputBlur = () => {
		setTimeout(() => {
			if (inputValue !== value) {
				onChange(inputValue);
			}
			setOpen(false);
		}, 150);
	};

	const handleClear = () => {
		setInputValue("");
		onChange("");
		inputRef.current?.focus();
	};

	return (
		<div className="flex flex-col space-y-1.5">
			{!hideLabel && (
				<div className="min-h-[2.5rem]">
					<p className="text-sm font-medium text-foreground leading-tight">{label}</p>
					<p className="text-xs text-muted-foreground leading-relaxed mt-0.5">{subtitle}</p>
				</div>
			)}
			<Popover open={open} onOpenChange={setOpen}>
				<PopoverTrigger asChild>
					<div className="relative">
						<input
							ref={inputRef}
							type="text"
							value={inputValue}
							onChange={handleInputChange}
							onFocus={() => setOpen(true)}
							onBlur={handleInputBlur}
							onKeyDown={handleInputKeyDown}
							placeholder={placeholder}
							disabled={disabled}
							aria-label={label}
							className={cn(
								"flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-xs font-mono shadow-sm transition-colors",
								"placeholder:text-muted-foreground placeholder:font-sans",
								"focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
								"disabled:cursor-not-allowed disabled:opacity-50",
								"pr-14",
							)}
						/>
						<div className="absolute right-1.5 top-1/2 -translate-y-1/2 flex items-center gap-0.5">
							{inputValue && (
								<button
									type="button"
									className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
									onClick={handleClear}
									tabIndex={-1}
								>
									<X className="h-3 w-3" />
								</button>
							)}
							<button
								type="button"
								className="p-1 rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
								onClick={() => setOpen(!open)}
								tabIndex={-1}
							>
								<ChevronDown className="h-3.5 w-3.5" />
							</button>
						</div>
					</div>
				</PopoverTrigger>
				<PopoverContent
					className="w-[var(--radix-popover-trigger-width)] p-0"
					align="start"
					onOpenAutoFocus={(e) => e.preventDefault()}
				>
					<Command shouldFilter={false}>
						<CommandList>
							{filteredPrimary.length === 0 && filteredFast.length === 0 && filteredCatalog.length === 0 && (
								<CommandEmpty>
									{inputValue ? (
										<span className="text-xs text-muted-foreground">
											Press Enter to use <span className="font-mono font-medium">{inputValue}</span>
										</span>
									) : (
										<span className="text-xs text-muted-foreground">Type a model ID (provider/model-name)</span>
									)}
								</CommandEmpty>
							)}
							{filteredPrimary.length > 0 && (
								<CommandGroup heading="Recommended">
									{filteredPrimary.map((model) => (
										<CommandItem
											key={model.id}
											value={model.id}
											onSelect={() => handleSelect(model.id)}
											onMouseDown={(e) => e.preventDefault()}
										>
											<Check
												className={cn(
													"mr-2 h-3.5 w-3.5 shrink-0",
													value === model.id ? "opacity-100" : "opacity-0",
												)}
											/>
											<div className="flex flex-col min-w-0">
												<span className="text-sm truncate">{model.label}</span>
												<span className="text-xs text-muted-foreground font-mono truncate">
													{model.id}
												</span>
											</div>
										</CommandItem>
									))}
								</CommandGroup>
							)}
							{filteredCatalog.length > 0 && (
								<CommandGroup heading="Models">
									{filteredCatalog.map((model) => (
										<CommandItem
											key={model.id}
											value={model.id}
											onSelect={() => handleSelect(model.id)}
											onMouseDown={(e) => e.preventDefault()}
										>
											<Check
												className={cn(
													"mr-2 h-3.5 w-3.5 shrink-0",
													value === model.id ? "opacity-100" : "opacity-0",
												)}
											/>
											<div className="flex flex-col min-w-0">
												<span className="text-sm truncate">{model.label}</span>
												<span className="text-xs text-muted-foreground font-mono truncate">
													{model.id}
												</span>
											</div>
										</CommandItem>
									))}
								</CommandGroup>
							)}
							{filteredFast.length > 0 && (
								<CommandGroup heading="Fast / Budget">
									{filteredFast.map((model) => (
										<CommandItem
											key={model.id}
											value={model.id}
											onSelect={() => handleSelect(model.id)}
											onMouseDown={(e) => e.preventDefault()}
										>
											<Check
												className={cn(
													"mr-2 h-3.5 w-3.5 shrink-0",
													value === model.id ? "opacity-100" : "opacity-0",
												)}
											/>
											<div className="flex flex-col min-w-0">
												<span className="text-sm truncate">{model.label}</span>
												<span className="text-xs text-muted-foreground font-mono truncate">
													{model.id}
												</span>
											</div>
										</CommandItem>
									))}
								</CommandGroup>
							)}
						</CommandList>
					</Command>
				</PopoverContent>
			</Popover>
		</div>
	);
}
