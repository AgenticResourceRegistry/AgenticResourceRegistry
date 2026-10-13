// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

/** Theme-aware colors for trace event categories. */
const KIND_TEXT: Record<string, string> = {
  prompts: "text-kind-prompt",
  responses: "text-kind-response",
  thinking: "text-kind-thinking",
  tools: "text-kind-tool",
  agents: "text-kind-agent",
  tasks: "text-kind-task",
  mcp: "text-kind-mcp",
  worktree: "text-kind-worktree",
  api: "text-info",
  errors: "text-destructive",
  notifications: "text-warning",
  lifecycle: "text-muted-foreground",
};

const KIND_CHIP: Record<string, string> = {
  prompts: "bg-kind-prompt/10 text-kind-prompt border-kind-prompt/20",
  responses: "bg-kind-response/10 text-kind-response border-kind-response/20",
  thinking: "bg-kind-thinking/10 text-kind-thinking border-kind-thinking/20",
  tools: "bg-kind-tool/10 text-kind-tool border-kind-tool/20",
  agents: "bg-kind-agent/10 text-kind-agent border-kind-agent/20",
  tasks: "bg-kind-task/10 text-kind-task border-kind-task/20",
  mcp: "bg-kind-mcp/10 text-kind-mcp border-kind-mcp/20",
  worktree: "bg-kind-worktree/10 text-kind-worktree border-kind-worktree/20",
  api: "bg-info/10 text-info border-info/20",
  errors: "bg-destructive/10 text-destructive border-destructive/20",
  notifications: "bg-warning/10 text-warning border-warning/20",
  lifecycle: "bg-muted text-muted-foreground border-border",
};

const EVENT_KIND: Record<string, string> = {
  user_prompt: "prompts",
  hook_userpromptsubmit: "prompts",
  hook_assistant_response: "responses",
  hook_assistant_thinking: "thinking",
  api_request: "api",
  tool_result: "tools",
  tool_decision: "tools",
  hook_pretooluse: "tools",
  hook_posttooluse: "tools",
  hook_posttoolusefailure: "errors",
  hook_stopfailure: "errors",
  hook_subagentstart: "agents",
  hook_subagentstop: "agents",
  hook_taskcreated: "tasks",
  hook_taskcompleted: "tasks",
  hook_elicitation: "mcp",
  hook_elicitationresult: "mcp",
  hook_worktreecreate: "worktree",
  hook_worktreeremove: "worktree",
  hook_notification: "notifications",
  hook_sessionstart: "lifecycle",
  hook_stop: "lifecycle",
  hook_precompact: "lifecycle",
  hook_postcompact: "lifecycle",
};

const NEUTRAL_TEXT = "text-muted-foreground";
const NEUTRAL_CHIP = "bg-muted text-muted-foreground border-border";

export function eventKind(eventName: string | undefined | null): string | undefined {
  if (!eventName) return undefined;
  return EVENT_KIND[eventName.toLowerCase()];
}

export function kindTextClass(kind: string | undefined | null): string {
  if (!kind) return NEUTRAL_TEXT;
  return KIND_TEXT[kind.toLowerCase()] ?? NEUTRAL_TEXT;
}

export function kindChipClasses(kind: string | undefined | null): string {
  if (!kind) return NEUTRAL_CHIP;
  return KIND_CHIP[kind.toLowerCase()] ?? NEUTRAL_CHIP;
}

export function eventTextClass(eventName: string | undefined | null): string {
  return kindTextClass(eventKind(eventName));
}
