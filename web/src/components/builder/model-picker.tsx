// SPDX-FileCopyrightText: 2026 Aryan Iyappan <aryaniyappan2006@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useMemo, useState } from "react";
import { Check, ChevronsUpDown, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover";
import { PickerSelect } from "@/components/ui/picker-select";
import { useHarnesses } from "@/hooks/use-harnesses";
import { AUTO_MODEL } from "@/lib/agent-model";
import { cn } from "@/lib/utils";

interface ModelPickerProps {
  modelName: string;
  onModelNameChange: (value: string) => void;
  modelsByHarness: Record<string, string>;
  onModelsByHarnessChange: (value: Record<string, string>) => void;
}

interface ModelComboboxProps {
  value: string;
  models: string[];
  placeholder: string;
  onChange: (value: string) => void;
}

function ModelCombobox({ value, models, placeholder, onChange }: ModelComboboxProps) {
  const [open, setOpen] = useState(false);
  const query = value.trim().toLowerCase();
  // An exact selection (e.g. the "auto" default) shows every option instead of filtering to itself.
  const filteredModels = models
    .filter((model) => !query || models.includes(value.trim()) || model.toLowerCase().includes(query))
    .slice(0, 50);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div className="relative">
          <Input
            value={value}
            onChange={(event) => {
              onChange(event.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            placeholder={placeholder}
            className="pr-9 font-mono text-xs"
          />
          <button
            type="button"
            onClick={() => setOpen((current) => !current)}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded text-muted-foreground hover:text-foreground"
            aria-label="Show model suggestions"
          >
            <ChevronsUpDown className="h-3.5 w-3.5" />
          </button>
        </div>
      </PopoverAnchor>
      <PopoverContent
        align="start"
        className="max-h-[min(24rem,var(--radix-popover-content-available-height))] w-[var(--radix-popover-trigger-width)] overflow-y-auto p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {filteredModels.length ? (
          filteredModels.map((model) => (
            <button
              key={model}
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                onChange(model);
                setOpen(false);
              }}
              className={cn(
                "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left font-mono text-xs hover:bg-accent hover:text-accent-foreground",
                value === model && "bg-accent text-accent-foreground",
              )}
            >
              <Check className={cn("h-3.5 w-3.5", value === model ? "opacity-100" : "opacity-0")} />
              <span className="truncate">{model}</span>
            </button>
          ))
        ) : (
          <div className="px-2 py-3 text-xs text-muted-foreground">No registry suggestion. Custom value is allowed.</div>
        )}
      </PopoverContent>
    </Popover>
  );
}

export function ModelPicker({
  modelName,
  onModelNameChange,
  modelsByHarness,
  onModelsByHarnessChange,
}: ModelPickerProps) {
  const { data: harnesses, defaultHarness } = useHarnesses();
  const allHarnesses = useMemo(() => harnesses ?? [], [harnesses]);
  const allModels = Array.from(new Set([AUTO_MODEL, ...allHarnesses.flatMap((harness) => harness.supported_models ?? [])]));
  const overrideEntries = Object.entries(modelsByHarness);
  const fallbackHarness =
    (defaultHarness && allHarnesses.some((harness) => harness.name === defaultHarness) ? defaultHarness : undefined) ??
    allHarnesses[0]?.name ??
    "";
  const [selectedHarness, setSelectedHarness] = useState(fallbackHarness);
  const [selectedModel, setSelectedModel] = useState("");

  useEffect(() => {
    if (!selectedHarness && fallbackHarness) setSelectedHarness(fallbackHarness);
  }, [fallbackHarness, selectedHarness]);

  useEffect(() => {
    setSelectedModel(modelsByHarness[selectedHarness] ?? "");
  }, [modelsByHarness, selectedHarness]);

  const selectedHarnessMeta = allHarnesses.find((harness) => harness.name === selectedHarness);
  const selectedModels = selectedHarnessMeta?.supported_models ?? [];

  function setOverride(harness: string, value: string) {
    const next = { ...modelsByHarness };
    const trimmed = value.trim();
    if (trimmed) next[harness] = trimmed;
    else delete next[harness];
    onModelsByHarnessChange(next);
  }

  function saveOverride() {
    if (!selectedHarness) return;
    setOverride(selectedHarness, selectedModel);
  }

  function removeOverride(harness: string) {
    const next = { ...modelsByHarness };
    delete next[harness];
    onModelsByHarnessChange(next);
    if (selectedHarness === harness) setSelectedModel("");
  }

  function editOverride(harness: string, model: string) {
    setSelectedHarness(harness);
    setSelectedModel(model);
  }

  function harnessLabel(name: string) {
    return allHarnesses.find((harness) => harness.name === name)?.display_name ?? name;
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="agent-default-model" className="text-sm font-medium">
          Default model
        </Label>
        <ModelCombobox
          value={modelName}
          onChange={onModelNameChange}
          placeholder={`${AUTO_MODEL} (let the harness pick)`}
          models={allModels}
        />
        <p className="text-xs text-muted-foreground">
          Used when no harness override is set. <span className="font-mono">{AUTO_MODEL}</span> lets each harness use its own default; a blank value saves as <span className="font-mono">{AUTO_MODEL}</span>.
        </p>
      </div>

      {allHarnesses.length > 0 ? (
        <div className="space-y-3 rounded-md border border-border bg-muted/20 p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1">
              <Label className="text-sm font-medium">Harness overrides</Label>
              <p className="text-xs text-muted-foreground">Set exceptions only. Pick a harness, choose or type its model, then save.</p>
            </div>
            {overrideEntries.length > 0 ? (
              <span className="rounded bg-primary/10 px-2 py-1 text-xs text-primary">{overrideEntries.length} set</span>
            ) : null}
          </div>

          <div className="grid gap-2 md:grid-cols-[minmax(160px,220px)_minmax(0,1fr)_auto]">
            <PickerSelect
              value={selectedHarness}
              onValueChange={setSelectedHarness}
              placeholder="Select harness"
              options={allHarnesses.map((harness) => ({ value: harness.name, label: harness.display_name }))}
            />

            <ModelCombobox
              value={selectedModel}
              onChange={setSelectedModel}
              models={selectedModels}
              placeholder={selectedHarnessMeta ? `Use default for ${selectedHarnessMeta.display_name}` : "Use default"}
            />

            <Button type="button" size="sm" onClick={saveOverride} disabled={!selectedHarness || !selectedModel.trim()}>
              Save
            </Button>
          </div>

          {overrideEntries.length > 0 ? (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {overrideEntries.map(([harness, model]) => (
                <Badge key={harness} variant="secondary" className="gap-1.5 pr-1 font-normal">
                  <button type="button" className="text-left" onClick={() => editOverride(harness, model)}>
                    <span className="font-medium">{harnessLabel(harness)}</span>: <span className="font-mono">{model}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => removeOverride(harness)}
                    className="rounded p-0.5 text-muted-foreground hover:bg-background/60 hover:text-foreground"
                    aria-label={`Remove ${harnessLabel(harness)} override`}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
