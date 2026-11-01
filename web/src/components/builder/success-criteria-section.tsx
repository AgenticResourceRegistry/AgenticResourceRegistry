// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, Plus, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { SuccessCriteria, SuccessMetric } from "@/lib/types";

interface SuccessCriteriaSectionProps {
  value: SuccessCriteria | null;
  onChange: (value: SuccessCriteria | null) => void;
}

const EMPTY_METRIC: SuccessMetric = { name: "", target: "", measurement: "" };

export function hasSuccessCriteriaContent(v: SuccessCriteria | null): boolean {
  if (!v) return false;
  return !!(
    v.intended_purpose?.trim() ||
    v.evaluation_notes?.trim() ||
    v.success_metrics?.some((m) => m.name.trim() || m.target.trim() || m.measurement.trim())
  );
}

export function validateSuccessCriteria(v: SuccessCriteria | null): string | null {
  if (!v || !hasSuccessCriteriaContent(v)) return null;
  if (!v.intended_purpose?.trim()) return "Intended purpose is required when success criteria are defined.";
  const incompleteMetric = (v.success_metrics ?? []).findIndex(
    (metric) => !metric.name.trim() || !metric.target.trim() || !metric.measurement.trim(),
  );
  return incompleteMetric === -1
    ? null
    : `Complete all fields for metric ${incompleteMetric + 1}.`;
}

export function normalizeSuccessCriteria(v: SuccessCriteria | null): SuccessCriteria | null {
  if (!v || !hasSuccessCriteriaContent(v)) return null;
  return {
    intended_purpose: v.intended_purpose?.trim() ?? "",
    success_metrics: (v.success_metrics ?? []).map((metric) => ({
      name: metric.name.trim(),
      target: metric.target.trim(),
      measurement: metric.measurement.trim(),
    })),
    evaluation_notes: v.evaluation_notes?.trim() ?? "",
  };
}

export function SuccessCriteriaSection({ value, onChange }: SuccessCriteriaSectionProps) {
  const [expanded, setExpanded] = useState(hasSuccessCriteriaContent(value));
  const defined = hasSuccessCriteriaContent(value);
  const validationError = validateSuccessCriteria(value);

  function handleToggle() {
    if (expanded) {
      setExpanded(false);
    } else {
      setExpanded(true);
    }
  }

  function update(partial: Partial<SuccessCriteria>) {
    const current = value ?? { intended_purpose: "", success_metrics: [], evaluation_notes: "" };
    onChange({ ...current, ...partial });
  }

  function handleClear() {
    onChange(null);
    setExpanded(false);
  }

  function addMetric() {
    const metrics = [...(value?.success_metrics ?? []), { ...EMPTY_METRIC }];
    update({ success_metrics: metrics });
  }

  function updateMetric(index: number, field: keyof SuccessMetric, fieldValue: string) {
    const metrics = [...(value?.success_metrics ?? [])];
    metrics[index] = { ...metrics[index], [field]: fieldValue };
    update({ success_metrics: metrics });
  }

  function removeMetric(index: number) {
    const metrics = (value?.success_metrics ?? []).filter((_, i) => i !== index);
    update({ success_metrics: metrics });
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleToggle}
          className="flex items-center gap-2 text-sm font-medium font-[family-name:var(--font-display)] hover:text-foreground/80 transition-colors"
        >
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          Success Criteria
          <span className="text-xs font-normal text-muted-foreground">(optional)</span>
        </button>
        {!expanded && defined && (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="h-3 w-3" />
            Defined
          </span>
        )}
      </div>

      {expanded && (
        <div className="space-y-4 pl-6 border-l-2 border-muted">
          <div className="space-y-2">
            <Label htmlFor="intended-purpose" className="text-sm font-medium">
              Intended Purpose
            </Label>
            <Textarea
              id="intended-purpose"
              placeholder="What problem does this Agent File solve? What task does it handle?"
              value={value?.intended_purpose ?? ""}
              onChange={(e) => update({ intended_purpose: e.target.value })}
              rows={2}
              className="resize-y"
            />
            <p className="text-xs text-muted-foreground">
              Required if you define success criteria. Describe the Agent File&apos;s core purpose.
            </p>
          </div>

          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label className="text-sm font-medium">Success Metrics</Label>
              {(value?.success_metrics?.length ?? 0) < 10 && (
                <Button type="button" variant="ghost" size="sm" onClick={addMetric}>
                  <Plus className="mr-1 h-3 w-3" />
                  Add Metric
                </Button>
              )}
            </div>

            {(value?.success_metrics ?? []).length === 0 && (
              <p className="text-xs text-muted-foreground">
                No metrics defined. Add metrics to quantify what success looks like.
              </p>
            )}

            {(value?.success_metrics ?? []).map((metric, index) => (
              <div key={index} className="grid grid-cols-1 sm:grid-cols-[1fr_1fr_1fr_auto] gap-2 items-start">
                <Input
                  placeholder="Metric name"
                  aria-label={`Metric ${index + 1} name`}
                  value={metric.name}
                  onChange={(e) => updateMetric(index, "name", e.target.value)}
                  className="text-sm"
                />
                <Input
                  placeholder="Target (e.g. < 5%)"
                  aria-label={`Metric ${index + 1} target`}
                  value={metric.target}
                  onChange={(e) => updateMetric(index, "target", e.target.value)}
                  className="text-sm"
                />
                <Input
                  placeholder="How to measure"
                  aria-label={`Metric ${index + 1} measurement method`}
                  value={metric.measurement}
                  onChange={(e) => updateMetric(index, "measurement", e.target.value)}
                  className="text-sm"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove metric ${index + 1}`}
                  onClick={() => removeMetric(index)}
                  className="h-9 w-9 text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
            {validationError && (
              <p role="alert" className="text-xs text-destructive">
                {validationError}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="evaluation-notes" className="text-sm font-medium">
              Evaluation Notes
            </Label>
            <Textarea
              id="evaluation-notes"
              placeholder="Any context on how to judge whether this Agent File is working well..."
              value={value?.evaluation_notes ?? ""}
              onChange={(e) => update({ evaluation_notes: e.target.value })}
              rows={2}
              className="resize-y"
            />
          </div>

          {defined && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleClear}
              className="text-muted-foreground hover:text-destructive"
            >
              <X className="mr-1 h-3 w-3" />
              Clear Success Criteria
            </Button>
          )}
        </div>
      )}
    </section>
  );
}
