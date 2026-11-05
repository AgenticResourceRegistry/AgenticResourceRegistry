// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import type { ReactNode } from "react";
import type {
  ComponentAnalysis,
  ComponentAttentionItem,
  ComponentInterpretation,
  ComponentOutcomeSide,
  ComponentOutcomes,
  ComponentReadingPoint,
  ComponentSuggestion,
} from "@/lib/types";

const HEALTH = {
  healthy: { label: "Healthy", tone: "bg-success/12 text-success" },
  watch: { label: "Worth watching", tone: "bg-warning/12 text-warning" },
  needs_attention: { label: "Needs attention", tone: "bg-destructive/12 text-destructive" },
  insufficient_data: { label: "Not enough data to judge", tone: "bg-muted text-muted-foreground" },
} as const;

const ACTION = {
  edit_component: "Edit the component",
  change_install: "Change how it is installed",
  add_companion: "Add a companion",
  remove_or_replace: "Remove or replace",
  usage_guidance: "Tell users how to use it",
  collect_more_data: "Collect more data",
} as const;

const EVIDENCE_LABEL: Record<string, string> = {
  metrics: "the counts above",
  outcomes: "the session comparison",
  friction: "friction in sessions with it",
};

const percent = (part: number, whole: number) => (whole ? `${Math.round((100 * part) / whole)}%` : "n/a");
const words = (value: string) => value.replaceAll("_", " ");

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-3">
      <h3 className="text-lg font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function Note({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "error" }) {
  return (
    <p role={tone === "error" ? "status" : undefined} className={`max-w-[70ch] text-sm ${tone === "error" ? "text-destructive" : "text-muted-foreground"}`}>
      {children}
    </p>
  );
}

function Points({ title, points }: { title: string; points?: ComponentReadingPoint[] }) {
  if (!points?.length) return null;
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium">{title}</h4>
      <ul className="space-y-2">
        {points.map((point) => (
          <li key={point.title} className="max-w-[70ch] text-sm leading-relaxed">
            <span className="font-medium">{point.title}.</span> <span className="text-muted-foreground">{point.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function AtAGlance({ analysis }: { analysis?: ComponentAnalysis }) {
  if (!analysis) return <Note>This report predates outcome analysis. Generate a new report to add it.</Note>;
  if (analysis.state === "unavailable") return <Note tone="error">The reading could not run because the model failed. Generate the report again.</Note>;
  if (analysis.state === "insufficient") return <Note>There are too few sessions with outcome data, and nothing flagged, to write a reading yet.</Note>;
  if (analysis.state !== "assessed" || !analysis.at_a_glance) return <Note>No reading was kept: it was not requested, or its answers failed the checks. This does not mean there is nothing to find.</Note>;
  const health = HEALTH[analysis.at_a_glance.health];
  return (
    <div className="space-y-2">
      <span className={`inline-block rounded-md px-2 py-1 text-xs font-medium ${health.tone}`}>{health.label}</span>
      <p className="max-w-[70ch] text-sm leading-relaxed">{analysis.at_a_glance.summary}</p>
    </div>
  );
}

function Attention({ items }: { items?: ComponentAttentionItem[] }) {
  if (!items?.length) return <Note>Nothing in the counts crossed a threshold. That is not proof nothing needs work.</Note>;
  return (
    <ul className="space-y-4">
      {items.map((item) => (
        <li key={item.id} className="space-y-1">
          <p className="flex flex-wrap items-center gap-2 text-sm font-medium">
            <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${item.severity === "attention" ? "bg-warning/12 text-warning" : "bg-muted text-muted-foreground"}`}>
              {item.severity === "attention" ? "Look at this" : "Note"}
            </span>
            {item.title}
          </p>
          <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">{item.detail}</p>
          {item.suggestion && <p className="max-w-[70ch] text-sm leading-relaxed">{item.suggestion}</p>}
        </li>
      ))}
    </ul>
  );
}

function SideRow({ label, side }: { label: string; side?: ComponentOutcomeSide }) {
  if (!side) return null;
  const known = side.achieved + side.not_achieved;
  const friction = side.friction_types.map(([kind, count]) => `${words(kind)} ${count}`).join(", ");
  return (
    <tr className="border-t border-border align-top">
      <th scope="row" className="py-2 pr-4 text-left font-medium">{label}</th>
      <td className="py-2 pr-4 tabular-nums">
        {side.achieved} of {known} <span className="text-muted-foreground">({percent(side.achieved, known)})</span>
      </td>
      <td className="py-2 pr-4 tabular-nums">{side.analyzed} of {side.sessions}<span className="block text-xs text-muted-foreground">{side.sampled} sampled</span></td>
      <td className="py-2 text-muted-foreground">{friction || "None recorded"}</td>
    </tr>
  );
}

const VERDICT = {
  lower: "Sessions with it achieved their goal less often.",
  higher: "Sessions with it achieved their goal more often.",
  no_difference: "No difference large enough to report.",
} as const;

function Outcomes({ outcomes }: { outcomes?: ComponentOutcomes }) {
  if (!outcomes) return null;
  if (outcomes.state === "unavailable") return <Note tone="error">Outcomes could not be read because a session read or the model failed. Generate the report again.</Note>;
  if (outcomes.state !== "assessed") return <Note>No session with this component has outcome data yet. Outcomes are read from session summaries, which are made when a report is generated.</Note>;
  const gap = outcomes.gap;
  const verdict = gap && gap.state !== "insufficient"
    ? VERDICT[gap.state]
    : `Too few sessions to compare: it needs ${gap?.min_per_side ?? 10} with a known outcome on each side.`;
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] text-sm">
          <thead>
            <tr className="text-left text-xs font-medium uppercase tracking-wide text-muted-foreground">
              <th scope="col" className="pb-2 pr-4 font-medium"><span className="sr-only">Group</span></th>
              <th scope="col" className="pb-2 pr-4 font-medium">Achieved their goal</th>
              <th scope="col" className="pb-2 pr-4 font-medium">With outcome data</th>
              <th scope="col" className="pb-2 font-medium">Friction (sessions)</th>
            </tr>
          </thead>
          <tbody>
            <SideRow label="With it" side={outcomes.with} />
            <SideRow label="Without it" side={outcomes.without} />
          </tbody>
        </table>
      </div>
      <p className="max-w-[70ch] text-sm">{verdict}</p>
      <p className="max-w-[70ch] text-xs text-muted-foreground">
        These sessions differ in more than the component, such as the task and the user, so this is an association, not a cause.
        {outcomes.comparison ? ` Without it means: ${outcomes.comparison.charAt(0).toLowerCase()}${outcomes.comparison.slice(1)}` : ""}
      </p>
    </div>
  );
}

function Suggestions({ suggestions, attention }: { suggestions?: ComponentSuggestion[]; attention?: ComponentAttentionItem[] }) {
  if (!suggestions?.length) return null;
  const title = (id: string) => EVIDENCE_LABEL[id] ?? attention?.find((item) => item.id === id)?.title ?? words(id);
  return (
    <Section title="Suggested changes">
      <ol className="space-y-5">
        {suggestions.map((suggestion) => (
          <li key={suggestion.title} className="space-y-1">
            <p className="text-sm font-medium">{suggestion.title}</p>
            <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">{suggestion.detail}</p>
            <p className="text-xs text-muted-foreground">
              {ACTION[suggestion.action_type]} · {suggestion.confidence} confidence · based on {suggestion.evidence.map(title).join("; ")}
            </p>
          </li>
        ))}
      </ol>
    </Section>
  );
}

/** What an owner acts on, ahead of the counts: the reading, what to look at, how sessions compare, and what to change. */
export function ComponentInterpretationPanel({ narrative }: { narrative?: ComponentInterpretation | null }) {
  const analysis = narrative?.analysis;
  const reading = analysis?.state === "assessed" ? analysis : undefined;
  return (
    <div className="space-y-8">
      <Section title="At a glance"><AtAGlance analysis={analysis} /></Section>
      {narrative && ("attention" in narrative || "outcomes" in narrative) && (
        <>
          <Section title="What to look at"><Attention items={narrative.attention} /></Section>
          <Section title="Sessions with and without it"><Outcomes outcomes={narrative.outcomes} /></Section>
        </>
      )}
      {(reading?.what_works?.length || reading?.friction?.length) ? (
        <Section title="What the sessions show">
          <Points title="What works" points={reading?.what_works} />
          <Points title="Friction" points={reading?.friction} />
        </Section>
      ) : null}
      <Suggestions suggestions={reading?.suggestions} attention={narrative?.attention} />
    </div>
  );
}
