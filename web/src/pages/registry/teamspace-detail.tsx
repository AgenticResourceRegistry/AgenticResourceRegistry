// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { toast } from "sonner";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useState } from "react";
import {
	ArrowLeft,
	Ban,
	Bot,
	Building2,
	Check,
	ClipboardCheck,
	Clock,
	Copy,
	Link2,
	Loader2,
	Lock,
	LogOut,
	Plus,
	Puzzle,
	ShieldCheck,
	Terminal,
	Ticket,
	Trash2,
	UserPlus,
	Users,
	X,
} from "lucide-react";
import { PageHeader } from "@/components/layouts/page-header";
import { AgentCard } from "@/components/registry/agent-card";
import { ComponentCard } from "@/components/registry/component-card";
import { SubmitComponentDialog } from "@/components/registry/submit-component-dialog";
import { TeamSettingsTab } from "@/components/review/team-settings-tab";
import { TeamReviewLinks } from "@/components/review/team-review-links";
import { EmptyState } from "@/components/shared/empty-state";
import { ErrorState } from "@/components/shared/error-state";
import { CardSkeleton, DetailSkeleton, TableSkeleton } from "@/components/shared/skeleton-layouts";
import { UserSearchInput } from "@/components/shared/user-search-input";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PickerSelect } from "@/components/ui/picker-select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { ShareLinkButton } from "@/components/registry/share-link-button";
import {
	useCancelJoinRequest,
	useComponentSaveDraft,
	useCreateTeamInvite,
	useComponentSubmit,
	useDecideJoinRequest,
	useDeleteTeam,
	useDeleteTeamInvite,
	useJoinRequests,
	useLeaveTeam,
	useMyJoinRequests,
	useRegistryList,
	useRemoveTeamMember,
	useRequestJoin,
	useRevokeTeamInvite,
	useTeamByHandle,
	useTeamInviteRequests,
	useTeamInvites,
	useTeamMembers,
	useTeamReviewQueue,
	useUpdateTeamVisibility,
	useUpsertTeamMember,
} from "@/hooks/use-api";
import { hasMinRole } from "@/hooks/use-role-guard";
import { getUserRole, type RegistryType } from "@/lib/api";
import type {
	RegistryItem,
	Team,
	TeamInvite,
	TeamInviteCreated,
	TeamJoinRequest,
	TeamMember,
	TeamRole,
} from "@/lib/types";
import { cn, copyToClipboard } from "@/lib/utils";

const ROLE_OPTIONS = [
	{ value: "member", label: "Member" },
	{ value: "reviewer", label: "Reviewer" },
	{ value: "owner", label: "Owner" },
];

const COMPONENT_TYPES: { value: RegistryType; label: string }[] = [
	{ value: "mcps", label: "MCPs" },
	{ value: "skills", label: "Skills" },
	{ value: "hooks", label: "Hooks" },
	{ value: "prompts", label: "Prompts" },
	{ value: "sandboxes", label: "Sandboxes" },
];

const COMPONENT_TYPE_LABELS: Record<string, string> = Object.fromEntries(
	COMPONENT_TYPES.map((type) => [type.value, type.label]),
);

/** Team owners and team reviewers are the only roles that clear team-private review. */
const REVIEWING_TEAM_ROLES: TeamRole[] = ["owner", "reviewer"];

const GRID_CLASS = "grid gap-4 pt-2 sm:grid-cols-2 xl:grid-cols-3";

function isPrivateItem(item: RegistryItem): boolean {
	return item.visibility === "team" || item.is_private === true;
}

/**
 * Chip that straddles the top border of a registry card.
 *
 * The registry cards already own their own top-right corner (version for
 * agents, type for components), so the private marker floats above the card
 * rather than competing for space inside it. Pointer events pass through so the
 * whole card stays one click target.
 */
function PrivateChip() {
	return (
		<Badge
			variant="outline"
			className="pointer-events-none absolute -top-2 left-3 z-10 gap-1 border-primary-accent/40 bg-card px-1.5 py-0 text-[10px] font-medium text-primary-accent"
		>
			<Lock className="h-2.5 w-2.5" />
			Team-private
		</Badge>
	);
}

function AgentsTab({ team }: { team: Team }) {
	const { data: agents = [], isLoading, isError, error, refetch } = useRegistryList("agents", { team_id: team.id });

	if (isLoading) return <CardSkeleton count={6} columns={3} />;
	if (isError) return <ErrorState message={error?.message} onRetry={() => refetch()} />;
	if (agents.length === 0) {
		return (
			<EmptyState
				icon={Bot}
				title="No Agent Files in this teamspace"
				description={`Agent Files published under ${team.handle}/ appear here, both public and team-private.`}
			/>
		);
	}

	return (
		<div className={GRID_CLASS}>
			{agents.map((agent: RegistryItem) => (
				<div key={agent.id} className="relative">
					{isPrivateItem(agent) && <PrivateChip />}
					<AgentCard
						id={agent.id}
						name={agent.name}
						namespace={agent.namespace}
						slug={agent.slug}
						qualified_name={agent.qualified_name}
						description={agent.description}
						owner={agent.owner as string | undefined}
						version={agent.version as string | undefined}
						downloads={agent.download_count as number | undefined}
						score={(agent.average_rating as number | null) ?? undefined}
						status={agent.status}
						component_count={agent.component_count as number | undefined}
						supported_harnesses={agent.supported_harnesses as string[] | undefined}
						inferred_supported_harnesses={agent.inferred_supported_harnesses as string[] | undefined}
						className="h-full"
					/>
				</div>
			))}
		</div>
	);
}

function ComponentsTab({
	team,
	activeType,
	onTypeChange,
}: {
	team: Team;
	activeType: RegistryType;
	onTypeChange: (type: RegistryType) => void;
}) {
	const { data: items = [], isLoading, isError, error, refetch } = useRegistryList(activeType, { team_id: team.id });
	const label = COMPONENT_TYPE_LABELS[activeType] ?? activeType;

	return (
		<div className="space-y-4">
			<div className="flex items-center gap-1 border-b border-border">
				{COMPONENT_TYPES.map((type) => (
					<button
						key={type.value}
						type="button"
						onClick={() => onTypeChange(type.value)}
						className={cn(
							"relative px-3 py-2 text-sm font-medium transition-colors hover:text-foreground",
							activeType === type.value ? "text-foreground" : "text-muted-foreground",
						)}
					>
						{type.label}
						{activeType === type.value && <span className="absolute inset-x-0 -bottom-px h-0.5 bg-primary-accent" />}
					</button>
				))}
			</div>

			{isLoading ? (
				<CardSkeleton count={6} columns={3} />
			) : isError ? (
				<ErrorState message={error?.message} onRetry={() => refetch()} />
			) : items.length === 0 ? (
				<EmptyState
					icon={Puzzle}
					title={`No ${label.toLowerCase()} in this teamspace`}
					description={`${label} published under ${team.handle}/ appear here, both public and team-private.`}
				/>
			) : (
				<div className={GRID_CLASS}>
					{items.map((item: RegistryItem) => (
						<div key={item.id} className="relative">
							{isPrivateItem(item) && <PrivateChip />}
							<ComponentCard
								id={item.id}
								name={item.name}
								namespace={item.namespace}
								slug={item.slug}
								qualified_name={item.qualified_name}
								type={activeType}
								description={item.description}
								version={item.version as string | undefined}
								status={item.status}
								git_url={item.git_url as string | undefined}
								className="h-full"
							/>
						</div>
					))}
				</div>
			)}
		</div>
	);
}

function MembersTab({ team }: { team: Team }) {
	const isOwner = team.role === "owner";
	const isMember = Boolean(team.role);
	const canManageMembers = !team.is_personal && (isOwner || hasMinRole(getUserRole(), "admin"));
	const canViewMembers = isMember || hasMinRole(getUserRole(), "admin");
	const { data: members = [], isLoading, isError, error, refetch } = useTeamMembers(team.id, canViewMembers);
	const upsert = useUpsertTeamMember(team.id);
	const removeMember = useRemoveTeamMember(team.id);
	const [role, setRole] = useState<TeamRole>("member");
	const [searchValue, setSearchValue] = useState("");
	const [selectedUserId, setSelectedUserId] = useState<string | null>(null);

	function addUser() {
		if (!selectedUserId) return;
		upsert.mutate(
			{ user_id: selectedUserId, role },
			{
				onSuccess: () => {
					setSearchValue("");
					setSelectedUserId(null);
				},
			},
		);
	}

	if (!canViewMembers) {
		return (
			<EmptyState
				icon={ShieldCheck}
				title="Membership required"
				description="This teamspace is discoverable, but its member roster is only visible to members and administrators. Use Request to join in the header to ask for access."
			/>
		);
	}

	return (
		<div className="space-y-4">
			{canManageMembers && (
				<div className="rounded-lg border border-border/80 bg-card/70 p-4">
					<div className="mb-3 flex items-center gap-2">
						<UserPlus className="h-4 w-4 text-primary-accent" />
						<div>
							<h3 className="text-sm font-medium">Add a member</h3>
							<p className="text-xs text-muted-foreground">Search for a person, then choose their role.</p>
						</div>
					</div>
					<div className="flex flex-col gap-2 lg:flex-row lg:items-center">
						<UserSearchInput
							placeholder="Search people"
							value={searchValue}
							onValueChange={(value) => {
								setSearchValue(value);
								setSelectedUserId(null);
							}}
							onSelect={(user) => {
								setSearchValue(user.username ? `@${user.username}` : user.email);
								setSelectedUserId(user.id);
							}}
							className="min-w-0 flex-1"
							disabled={upsert.isPending}
						/>
						<PickerSelect
							value={role}
							onValueChange={(value) => setRole(value as TeamRole)}
							className="w-full lg:w-32"
							options={ROLE_OPTIONS}
						/>
						<Button
							className="bg-primary-accent text-primary-foreground hover:bg-primary-accent/90"
							onClick={addUser}
							disabled={upsert.isPending || !selectedUserId}
						>
							{upsert.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
							Add member
						</Button>
					</div>
					<p className="mt-3 text-xs leading-5 text-muted-foreground">
						Owners and reviewers clear the team-private review queue. Members publish, but their team-private
						submissions wait for an owner or a reviewer.
					</p>
				</div>
			)}

			{isLoading ? (
				<div className="space-y-2" aria-label="Loading members">
					<div className="h-12 animate-pulse rounded-md bg-muted/60" />
					<div className="h-12 animate-pulse rounded-md bg-muted/60" />
				</div>
			) : isError ? (
				<ErrorState message={error?.message} onRetry={() => refetch()} />
			) : members.length === 0 ? (
				<EmptyState
					icon={Users}
					title="No members yet"
					description="Add someone above to start publishing together."
				/>
			) : (
				<div className="divide-y divide-border/70 rounded-lg border border-border/80">
					{members.map((member: TeamMember) => (
						<div key={member.id} className="flex items-center justify-between gap-4 px-4 py-3">
							<div className="min-w-0">
								<p className="truncate text-sm font-medium">{member.username ? `@${member.username}` : member.email}</p>
								{member.name && <p className="truncate text-xs text-muted-foreground">{member.name}</p>}
							</div>
							<div className="flex shrink-0 items-center gap-2">
								{canManageMembers ? (
									<PickerSelect
										value={member.role}
										onValueChange={(value) => {
											const nextRole = value as TeamRole;
											if (nextRole !== member.role) upsert.mutate({ user_id: member.id, role: nextRole });
										}}
										options={ROLE_OPTIONS}
										ariaLabel={`Change role for ${member.username ? `@${member.username}` : member.email}`}
										className="w-28"
										inputClassName="h-8 bg-background/80 px-2 text-xs"
										disabled={upsert.isPending}
									/>
								) : (
									<Badge variant="outline" className="capitalize px-2 py-0.5 text-[11px]">
										{member.role}
									</Badge>
								)}
								{canManageMembers && (
									<Button
										variant="ghost"
										size="icon"
										className="h-7 w-7 text-muted-foreground hover:text-destructive"
										onClick={() => removeMember.mutate(member.id)}
										disabled={removeMember.isPending}
										aria-label={`Remove ${member.username ? `@${member.username}` : member.email}`}
										title="Remove member"
									>
										<Trash2 className="h-3.5 w-3.5" />
									</Button>
								)}
							</div>
						</div>
					))}
				</div>
			)}
		</div>
	);
}

/** Normal teamspace visibility control. Personal teamspaces stay private. */
function VisibilityControl({ team }: { team: Team }) {
	const canChange = !team.is_personal && (team.role === "owner" || hasMinRole(getUserRole(), "admin"));
	const updateVisibility = useUpdateTeamVisibility(team.id);
	if (!canChange) return null;
	const isPrivate = team.visibility === "private";
	const pending = team.visibility_request_status === "pending";
	return (
		<Button
			variant="outline"
			size="sm"
			onClick={() => updateVisibility.mutate(pending ? "private" : isPrivate ? "public" : "private")}
			disabled={updateVisibility.isPending}
			title={
				isPrivate
					? "Make this teamspace discoverable by every signed-in user"
					: "Hide this teamspace from users who are not members"
			}
		>
			{updateVisibility.isPending ? (
				<Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
			) : (
				<Lock className="mr-1.5 h-3.5 w-3.5" />
			)}
			{pending ? "Cancel public request" : isPrivate ? "Request public" : "Make private"}
		</Button>
	);
}

function requesterLabel(request: TeamJoinRequest): string {
	return request.username ? `@${request.username}` : (request.email ?? "Unknown user");
}

/**
 * Header control for signed-in non-members: request member access, see the
 * pending state, or withdraw. The shared link that brought someone here never
 * grants access — this explicit request plus an owner's approval is the only
 * path onto the roster.
 */
function JoinRequestControl({ team }: { team: Team }) {
	const eligible = !team.is_personal && !team.role;
	const { data: mine = [] } = useMyJoinRequests(team.id, eligible);
	const requestJoin = useRequestJoin(team.id);
	const cancelRequest = useCancelJoinRequest(team.id);
	const [open, setOpen] = useState(false);
	const [message, setMessage] = useState("");

	if (!eligible) return null;
	const pending = mine.find((request) => request.status === "pending");

	if (pending) {
		return (
			<div className="flex items-center gap-2">
				<Badge variant="outline" className="gap-1 px-2 py-1 text-[11px]">
					<Clock className="h-3 w-3" />
					Request pending
				</Badge>
				<Button
					variant="outline"
					size="sm"
					onClick={() => cancelRequest.mutate(pending.id)}
					disabled={cancelRequest.isPending}
				>
					Withdraw
				</Button>
			</div>
		);
	}

	return (
		<>
			<Button
				size="sm"
				className="bg-primary-accent text-primary-foreground hover:bg-primary-accent/90"
				onClick={() => setOpen(true)}
			>
				<UserPlus className="mr-1.5 h-3.5 w-3.5" /> Request to join
			</Button>
			<Dialog open={open} onOpenChange={setOpen}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Request to join {team.name}</DialogTitle>
					</DialogHeader>
					<div className="space-y-2">
						<Label htmlFor="join-request-message">Message (optional)</Label>
						<Textarea
							id="join-request-message"
							value={message}
							onChange={(event) => setMessage(event.target.value)}
							rows={3}
							maxLength={500}
							placeholder="Tell the owners why you want to join"
						/>
						<p className="text-xs text-muted-foreground">
							You are asking for member access. A team owner approves or rejects the request; the decision
							arrives in your inbox.
						</p>
					</div>
					<DialogFooter>
						<Button variant="outline" onClick={() => setOpen(false)}>
							Cancel
						</Button>
						<Button
							disabled={requestJoin.isPending}
							onClick={() =>
								requestJoin.mutate(
									message.trim() ? { message: message.trim() } : {},
									{
										onSuccess: () => {
											setOpen(false);
											setMessage("");
										},
									},
								)
							}
						>
							{requestJoin.isPending ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
							Send request
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</>
	);
}

function CreateTeamInviteDialogs({
	team,
	open,
	onOpenChange,
}: {
	team: Team;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) {
	const createInvite = useCreateTeamInvite(team.id);
	const [name, setName] = useState("");
	const [expiresDays, setExpiresDays] = useState("7");
	const [maxUses, setMaxUses] = useState("5");
	const [minted, setMinted] = useState<TeamInviteCreated | null>(null);

	async function copyInvite(url: string) {
		try {
			await copyToClipboard(url);
			toast.success("Invite link copied");
		} catch {
			toast.error("Could not copy the invite link");
		}
	}

	return (
		<>
			<Dialog open={open} onOpenChange={onOpenChange}>
				<DialogContent>
					<DialogHeader><DialogTitle>New private-team invite</DialogTitle></DialogHeader>
					<p className="text-sm text-muted-foreground">
						Choose how long the link works and how many access requests it accepts. Each creation makes a new link.
					</p>
					<div className="space-y-3">
						<div className="space-y-1.5">
							<Label htmlFor="team-invite-name">Name</Label>
							<Input id="team-invite-name" value={name} maxLength={100} onChange={(event) => setName(event.target.value)} placeholder="Recruiting email" />
						</div>
						<div className="space-y-1.5">
							<Label htmlFor="team-invite-expiry">Valid for this many days</Label>
							<Input id="team-invite-expiry" type="number" min={1} max={365} value={expiresDays} onChange={(event) => setExpiresDays(event.target.value)} />
						</div>
						<div className="space-y-1.5">
							<Label htmlFor="team-invite-uses">Maximum access requests</Label>
							<Input id="team-invite-uses" type="number" min={1} max={10000} value={maxUses} onChange={(event) => setMaxUses(event.target.value)} />
						</div>
					</div>
					<DialogFooter>
						<Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
						<Button disabled={createInvite.isPending} onClick={() => createInvite.mutate({
							name: name.trim() || undefined,
							expires_in_days: Math.max(1, Math.min(365, Number(expiresDays) || 7)),
							max_uses: Math.max(1, Math.min(10000, Number(maxUses) || 5)),
						}, { onSuccess: (invite) => { onOpenChange(false); setMinted(invite); setName(""); } })}>
							{createInvite.isPending && <Loader2 className="animate-spin" />} Create invite
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>

			<Dialog open={!!minted} onOpenChange={(nextOpen) => { if (!nextOpen) setMinted(null); }}>
				<DialogContent>
					<DialogHeader><DialogTitle>Invite link created</DialogTitle></DialogHeader>
					<code className="block break-all rounded-md border bg-background px-3 py-2 text-xs">{minted?.url}</code>
					{minted && (
						<p className="text-xs text-muted-foreground">
							Valid until {new Date(minted.expires_at).toLocaleString()} for up to {minted.max_uses ?? "unlimited"} access requests. Copy it now because the plaintext link is not stored.
						</p>
					)}
					<DialogFooter>
						<Button variant="outline" onClick={() => setMinted(null)}>Done</Button>
						<Button onClick={() => minted && copyInvite(minted.url)}><Copy /> Copy link</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</>
	);
}

function InviteAuditDialog({
	team,
	invite,
	onClose,
}: {
	team: Team;
	invite: TeamInvite | null;
	onClose: () => void;
}) {
	const requests = useTeamInviteRequests(team.id, invite?.id);
	return (
		<Dialog open={!!invite} onOpenChange={(open) => { if (!open) onClose(); }}>
			<DialogContent className="max-w-2xl">
				<DialogHeader><DialogTitle>{invite?.name ?? "Invite usage"}</DialogTitle></DialogHeader>
				<p className="text-xs text-muted-foreground">
					Each access request consumes one use. Approval is still required before membership is granted.
				</p>
				<div className="max-h-80 space-y-2 overflow-y-auto pr-1">
					{requests.isLoading ? (
						<TableSkeleton rows={3} cols={3} />
					) : requests.isError ? (
						<ErrorState message={requests.error?.message} onRetry={() => requests.refetch()} />
					) : (requests.data ?? []).length === 0 ? (
						<EmptyState icon={Users} title="No access requests" description="Nobody has used this invite yet." />
					) : (
						(requests.data ?? []).map((request) => (
							<div key={request.id} className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-xs">
								<div className="min-w-0">
									<p className="truncate font-medium">{requesterLabel(request)}</p>
									<p className="text-muted-foreground">
										Requested {request.created_at ? new Date(request.created_at).toLocaleString() : "access"}
									</p>
								</div>
								<Badge variant="outline" className="shrink-0 capitalize">{request.status}</Badge>
							</div>
						))
					)}
				</div>
			</DialogContent>
		</Dialog>
	);
}

function InviteLinksTab({ team }: { team: Team }) {
	const invites = useTeamInvites(team.id);
	const revokeInvite = useRevokeTeamInvite(team.id);
	const deleteInvite = useDeleteTeamInvite(team.id);
	const [createOpen, setCreateOpen] = useState(false);
	const [auditInvite, setAuditInvite] = useState<TeamInvite | null>(null);
	const [deleteTarget, setDeleteTarget] = useState<TeamInvite | null>(null);

	async function copyInvite(url: string) {
		try {
			await copyToClipboard(url);
			toast.success("Invite link copied");
		} catch {
			toast.error("Could not copy the invite link");
		}
	}

	return (
		<div className="space-y-4">
			<div className="flex items-center justify-between gap-3">
				<div>
					<h3 className="text-sm font-medium">Private-team invite links</h3>
					<p className="text-xs text-muted-foreground">
						Recipients sign in first, then explicitly request access for an owner to approve.
					</p>
				</div>
				<Button size="sm" onClick={() => setCreateOpen(true)}>
					<Link2 /> New invite
				</Button>
			</div>

			{invites.isLoading ? (
				<TableSkeleton rows={3} cols={3} />
			) : invites.isError ? (
				<ErrorState message={invites.error?.message} onRetry={() => invites.refetch()} />
			) : (invites.data ?? []).length === 0 ? (
				<EmptyState icon={Ticket} title="No invite links" description="Create a link when someone needs to request access to this private teamspace." />
			) : (
				<div className="max-h-[28rem] divide-y divide-border/70 overflow-y-auto rounded-lg border border-border/80">
					{(invites.data ?? []).map((invite) => (
						<div key={invite.id} className="flex items-center gap-2 px-3 py-3 text-xs">
							<button type="button" title="View access request audit" className="min-w-0 flex-1 text-left" onClick={() => setAuditInvite(invite)}>
								<div className="flex flex-wrap items-center gap-2">
									<span className="font-medium text-foreground">{invite.name}</span>
									<Badge variant="outline" className="capitalize">{invite.state}</Badge>
									<span className="text-muted-foreground">
										{invite.use_count}{invite.max_uses != null ? ` / ${invite.max_uses}` : ""} used
									</span>
									<span className="text-muted-foreground">expires {new Date(invite.expires_at).toLocaleDateString()}</span>
									{invite.invited_by_username && <span className="text-muted-foreground">by @{invite.invited_by_username}</span>}
								</div>
								<code className="mt-1 block truncate text-[11px] text-muted-foreground">
									{invite.url ?? "Link unavailable for this older invite"}
								</code>
							</button>
							<div className="flex shrink-0 items-center gap-1">
								<Button variant="ghost" size="icon" title={invite.state === "active" ? "Copy invite link" : "Only active links can be copied"} disabled={!invite.url || invite.state !== "active"} onClick={() => invite.url && copyInvite(invite.url)}>
									<Copy />
								</Button>
								{invite.state === "active" && (
									<Button variant="ghost" size="sm" onClick={() => revokeInvite.mutate(invite.id)} disabled={revokeInvite.isPending}>
										<Ban /> Revoke
									</Button>
								)}
								{invite.use_count === 0 && (
									<Button variant="ghost" size="icon" title="Delete unused invite" onClick={() => setDeleteTarget(invite)}>
										<Trash2 />
									</Button>
								)}
							</div>
						</div>
					))}
				</div>
			)}

			<CreateTeamInviteDialogs team={team} open={createOpen} onOpenChange={setCreateOpen} />
			<InviteAuditDialog team={team} invite={auditInvite} onClose={() => setAuditInvite(null)} />
			<AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete {deleteTarget?.name}?</AlertDialogTitle>
						<AlertDialogDescription>This unused link will stop working immediately.</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction onClick={() => deleteTarget && deleteInvite.mutate(deleteTarget.id, { onSuccess: () => setDeleteTarget(null) })}>
							Delete invite
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}


/**
 * Join requests with approve/reject actions and decision history rendered
 * straight from the request rows. This sits beside the listing Review tab but
 * serves a different audience: owners and global admins only, while listing
 * review includes team reviewers.
 */
function JoinRequestsTab({
	team,
	requests,
	isLoading,
	isError,
	errorMessage,
	onRetry,
}: {
	team: Team;
	requests: TeamJoinRequest[];
	isLoading: boolean;
	isError: boolean;
	errorMessage?: string;
	onRetry: () => void;
}) {
	const decide = useDecideJoinRequest(team.id);
	const [rejectTarget, setRejectTarget] = useState<TeamJoinRequest | null>(null);
	const [reason, setReason] = useState("");

	if (isLoading) return <TableSkeleton rows={3} cols={3} />;
	if (isError) return <ErrorState message={errorMessage} onRetry={onRetry} />;

	const pending = requests.filter((request) => request.status === "pending");
	const decided = requests.filter((request) => request.status !== "pending");

	return (
		<div className="space-y-6">
			{pending.length === 0 ? (
				<EmptyState
					icon={ClipboardCheck}
					title="No pending join requests"
					description="When someone opens this teamspace from a shared link and asks to join, their request lands here for owners to decide."
				/>
			) : (
				<div className="divide-y divide-border/70 overflow-hidden rounded-lg border border-border/80">
					{pending.map((request) => (
						<div key={request.id} className="flex flex-col gap-3 px-4 py-4 sm:flex-row sm:items-start">
							<div className="min-w-0 flex-1">
								<div className="flex flex-wrap items-center gap-2">
									<p className="truncate text-sm font-medium">{requesterLabel(request)}</p>
									{request.name && <span className="text-xs text-muted-foreground">{request.name}</span>}
									{request.created_at && (
										<span className="text-[11px] text-muted-foreground">
											{new Date(request.created_at).toLocaleDateString()}
										</span>
									)}
								</div>
								{request.message && (
									<p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">“{request.message}”</p>
								)}
								<p className="mt-1.5 text-[11px] text-muted-foreground">
									Approval grants <span className="font-medium">member</span> access. Roles are changed from the
									Members tab.
								</p>
							</div>
							<div className="flex shrink-0 items-center gap-2">
								<Button
									size="sm"
									className="h-8 bg-primary-accent text-xs text-primary-foreground hover:bg-primary-accent/90"
									onClick={() => decide.mutate({ requestId: request.id, approve: true })}
									disabled={decide.isPending}
								>
									<Check className="mr-1 h-3.5 w-3.5" /> Approve
								</Button>
								<Button
									size="sm"
									variant="outline"
									className="h-8 text-xs"
									onClick={() => {
										setReason("");
										setRejectTarget(request);
									}}
									disabled={decide.isPending}
								>
									<X className="mr-1 h-3.5 w-3.5" /> Reject
								</Button>
							</div>
						</div>
					))}
				</div>
			)}

			{decided.length > 0 && (
				<div>
					<h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
						Decision history
					</h3>
					<div className="divide-y divide-border/70 overflow-hidden rounded-lg border border-border/80">
						{decided.map((request) => (
							<div key={request.id} className="flex flex-col gap-1 px-4 py-3 text-xs sm:flex-row sm:items-center sm:justify-between">
								<div className="flex min-w-0 flex-wrap items-center gap-2">
									<span className="truncate font-medium text-foreground">{requesterLabel(request)}</span>
									<Badge
										variant="outline"
										className={cn(
											"px-1.5 py-0 text-[10px] capitalize",
											request.status === "approved" && "border-success/40 text-success",
											request.status === "rejected" && "border-destructive/40 text-destructive",
										)}
									>
										{request.status}
									</Badge>
									{request.decision_reason && (
										<span className="truncate text-muted-foreground">— {request.decision_reason}</span>
									)}
								</div>
								<div className="shrink-0 text-muted-foreground">
									{request.decided_by_username && <span>by @{request.decided_by_username} </span>}
									{request.decided_at && <span>{new Date(request.decided_at).toLocaleDateString()}</span>}
								</div>
							</div>
						))}
					</div>
				</div>
			)}

			<Dialog
				open={!!rejectTarget}
				onOpenChange={(open) => {
					if (!open) setRejectTarget(null);
				}}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Reject {rejectTarget ? requesterLabel(rejectTarget) : "request"}?</DialogTitle>
					</DialogHeader>
					<div className="space-y-2">
						<Label htmlFor="join-reject-reason">Reason (optional)</Label>
						<Textarea
							id="join-reject-reason"
							value={reason}
							onChange={(event) => setReason(event.target.value)}
							rows={3}
							maxLength={500}
							placeholder="Shown to the requester with the decision"
						/>
						<p className="text-xs text-muted-foreground">
							The requester is notified in their inbox and may request again later.
						</p>
					</div>
					<DialogFooter>
						<Button variant="outline" onClick={() => setRejectTarget(null)}>
							Cancel
						</Button>
						<Button
							variant="destructive"
							disabled={decide.isPending}
							onClick={() => {
								if (!rejectTarget) return;
								decide.mutate({
									requestId: rejectTarget.id,
									approve: false,
									reason: reason.trim() || undefined,
								});
								setRejectTarget(null);
							}}
						>
							Reject request
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}

export default function TeamspaceDetailPage() {
	const { handle } = useParams({ from: "/_authed/teamspaces/$handle" });
	const { tab, type } = useSearch({ from: "/_authed/teamspaces/$handle" });
	const navigate = useNavigate();
	const { team, isLoading, isError, error, refetch, notFound } = useTeamByHandle(handle);
	const leaveTeam = useLeaveTeam();
	const deleteTeam = useDeleteTeam();
	const [inviteDialogOpen, setInviteDialogOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [componentDialogOpen, setComponentDialogOpen] = useState(false);
	const isAdmin = hasMinRole(getUserRole(), "admin");

	const visibilityPending = team?.visibility_request_status === "pending";
	const canReview = !visibilityPending && (isAdmin || (team?.role ? REVIEWING_TEAM_ROLES.includes(team.role) : false));
	const reviewQueue = useTeamReviewQueue(team?.id, canReview);
	const reviewItems = reviewQueue.data ?? [];

	// Membership requests are owners-and-admins only — a narrower audience than
	// the listing Review tab, which team reviewers also see.
	const canManageRequests = !visibilityPending && !team?.is_personal && (team?.role === "owner" || isAdmin);
	const canManageInvites = team?.visibility === "private" && canManageRequests;
	// Review rules and deletion are owner/admin decisions; personal teamspaces cannot be deleted or reconfigured.
	const canManageSettings = !visibilityPending && !team?.is_personal && (team?.role === "owner" || isAdmin);
	const joinRequestsQuery = useJoinRequests(team?.id, canManageRequests);
	const joinRequests = joinRequestsQuery.data ?? [];
	const pendingJoinCount = joinRequests.filter((request) => request.status === "pending").length;

	const tabs = [
		{ value: "agents", label: "Agent Files" },
		{ value: "components", label: "Components" },
		{ value: "members", label: "Members" },
		...(canReview ? [{ value: "review", label: "Review" }] : []),
		...(canManageRequests ? [{ value: "join-requests", label: "Join requests" }] : []),
		...(canManageInvites ? [{ value: "invite-links", label: "Invite links" }] : []),
		...(canManageSettings ? [{ value: "settings", label: "Settings" }] : []),
	];
	const activeTab = tabs.some((entry) => entry.value === tab) ? tab! : "agents";
	const activeType: RegistryType = type ?? "mcps";
	const submitComponent = useComponentSubmit(activeType);
	const saveComponentDraft = useComponentSaveDraft(activeType);

	function goto(next: { tab?: string; type?: RegistryType }) {
		navigate({
			to: "/teamspaces/$handle",
			params: { handle },
			search: { tab, type, ...next },
			replace: true,
		});
	}

	const header = (
		<PageHeader
			title={team?.name ?? handle}
			breadcrumbs={[
				{ label: "Registry", href: "/" },
				{ label: "Teamspaces", href: "/teamspaces" },
				{ label: team?.name ?? handle },
			]}
		/>
	);

	if (isLoading) {
		return (
			<>
				{header}
				<div className="page-body w-full">
					<DetailSkeleton />
				</div>
			</>
		);
	}

	if (isError) {
		return (
			<>
				{header}
				<div className="page-body w-full">
					<ErrorState message={error?.message} onRetry={() => refetch()} />
				</div>
			</>
		);
	}

	if (notFound || !team) {
		return (
			<>
				{header}
				<div className="page-body w-full">
					<EmptyState
						icon={Building2}
						title={`No teamspace named ${handle}`}
						description="The handle does not match any teamspace you can see. It may have been renamed or deleted."
						actionLabel="Back to teamspaces"
						actionHref="/teamspaces"
					/>
				</div>
			</>
		);
	}

	const isMember = Boolean(team.role);
	const isOwner = team.role === "owner";
	const canPublish = !visibilityPending && (isMember || (isAdmin && !team.is_personal));
	const canDelete = isOwner || isAdmin;

	return (
		<>
			{header}
			<main className="min-h-0 flex-1 overflow-y-auto bg-surface-sunken/30">
				<div className="page-body w-full">
					<Link
						to="/teamspaces"
						className="inline-flex items-center gap-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
					>
						<ArrowLeft className="h-3.5 w-3.5" />
						All teamspaces
					</Link>

					<header className="mt-4 flex flex-col gap-5 border-b border-border/80 pb-6 sm:flex-row sm:items-start sm:justify-between">
						<div className="flex min-w-0 items-start gap-3">
							<div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-primary-accent/25 bg-primary-accent/10 text-primary-accent">
								<Building2 className="h-5 w-5" />
							</div>
							<div className="min-w-0">
								<h2 className="truncate text-xl font-semibold tracking-tight">{team.name}</h2>
								<div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
									<span className="font-mono">{team.handle}</span>
									<span aria-hidden="true">·</span>
									<Badge variant="outline" className="px-1.5 py-0 text-[11px] font-medium capitalize">
										{team.role ?? (isAdmin ? "admin access" : "discoverable")}
									</Badge>
									{team.visibility === "private" && (
										<Badge
											variant="outline"
											className="gap-1 border-primary-accent/40 px-1.5 py-0 text-[11px] font-medium text-primary-accent"
										>
											<Lock className="h-3 w-3" /> Private
										</Badge>
									)}
									{team.visibility === "public" && team.visibility_request_status === "approved" && (
										<Badge variant="outline" className="gap-1 px-1.5 py-0 text-[11px] font-medium text-success">
											<ShieldCheck className="h-3 w-3" /> Public approved
										</Badge>
									)}
									{team.member_count != null && <span>{team.member_count} members</span>}
								</div>
								{team.description && (
									<p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">{team.description}</p>
								)}
							</div>
						</div>
						<div className="flex shrink-0 flex-wrap gap-2 sm:justify-end">
							{team.visibility === "public" ? (
								<ShareLinkButton path={`/teamspaces/${team.handle}`} />
							) : canManageInvites ? (
								<Button variant="outline" size="sm" onClick={() => setInviteDialogOpen(true)}>
									<Ticket /> Invite link
								</Button>
							) : null}
							<VisibilityControl team={team} />
							<JoinRequestControl team={team} />
							{!visibilityPending && isMember && !team.is_personal && (
								<Button
									variant="outline"
									size="sm"
									onClick={() => leaveTeam.mutate(team.id, { onSuccess: () => navigate({ to: "/teamspaces" }) })}
									disabled={leaveTeam.isPending}
								>
									<LogOut className="mr-1.5 h-3.5 w-3.5" /> Leave
								</Button>
							)}
						</div>
					</header>

					{team.visibility_request_status === "pending" && (
						<div className="mt-6 rounded-lg border border-warning/40 bg-warning/5 p-5">
							<p className="text-sm font-semibold text-warning">Public visibility review pending</p>
							<p className="mt-1 text-sm text-muted-foreground">
								This teamspace is locked until a global reviewer approves or rejects the request.
							</p>
						</div>
					)}
					{team.visibility_request_status === "rejected" && (
						<div className="mt-6 rounded-lg border border-destructive/40 bg-destructive/5 p-5">
							<p className="text-sm font-semibold text-destructive">Public visibility request rejected</p>
							{team.visibility_rejection_reason && (
								<p className="mt-1 text-sm text-muted-foreground">{team.visibility_rejection_reason}</p>
							)}
						</div>
					)}

					{team.visibility_request_status !== "pending" && (
						<>
							<Tabs value={activeTab} onValueChange={(value) => goto({ tab: value })} className="mt-6">
						<div className="flex items-center justify-between gap-3">
							<TabsList>
								{tabs.map((entry) => (
									<TabsTrigger key={entry.value} value={entry.value}>
										{entry.label}
										{entry.value === "review" && reviewItems.length > 0 && (
											<span className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary-accent px-1 text-[10px] font-semibold text-primary-foreground">
												{reviewItems.length}
											</span>
										)}
										{entry.value === "join-requests" && pendingJoinCount > 0 && (
											<span className="ml-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-primary-accent px-1 text-[10px] font-semibold text-primary-foreground">
												{pendingJoinCount}
											</span>
										)}
									</TabsTrigger>
								))}
							</TabsList>

							{canPublish && activeTab === "agents" && (
								<Button asChild size="sm">
									<Link to="/agents/builder" search={{ team: team.id }}>
										<Bot /> Build Agent File
									</Link>
								</Button>
							)}
							{canPublish && activeTab === "components" && (
								<Button size="sm" onClick={() => setComponentDialogOpen(true)}>
									<Plus /> Create component
								</Button>
							)}
						</div>

						<TabsContent value="agents" className="mt-5">
							<AgentsTab team={team} />
						</TabsContent>
						<TabsContent value="components" className="mt-5">
							<ComponentsTab team={team} activeType={activeType} onTypeChange={(next) => goto({ type: next })} />
						</TabsContent>
						<TabsContent value="members" className="mt-5">
							<MembersTab team={team} />
						</TabsContent>
						{canReview && (
							<TabsContent value="review" className="mt-5">
								<TeamReviewLinks
									items={reviewItems}
									isLoading={reviewQueue.isLoading}
									isError={reviewQueue.isError}
									errorMessage={reviewQueue.error?.message}
									onRetry={() => reviewQueue.refetch()}
                                    canEditPolicy={canManageSettings}
                                    onOpenSettings={() => goto({ tab: "settings" })}
								/>
							</TabsContent>
						)}
						{canManageRequests && (
							<TabsContent value="join-requests" className="mt-5">
								<JoinRequestsTab
									team={team}
									requests={joinRequests}
									isLoading={joinRequestsQuery.isLoading}
									isError={joinRequestsQuery.isError}
									errorMessage={joinRequestsQuery.error?.message}
									onRetry={() => joinRequestsQuery.refetch()}
								/>
							</TabsContent>
						)}
						{canManageInvites && (
							<TabsContent value="invite-links" className="mt-5">
								<InviteLinksTab team={team} />
							</TabsContent>
						)}
						{canManageSettings && (
							<TabsContent value="settings" className="mt-5">
								<TeamSettingsTab
									teamId={team.id}
									teamName={team.name}
									canDelete={canDelete}
									deleting={deleteTeam.isPending}
									onRequestDelete={() => setDeleteOpen(true)}
								/>
							</TabsContent>
						)}
							</Tabs>

							<footer className="mt-6 flex flex-col gap-3 rounded-lg border border-border/80 bg-card/70 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
						<div className="flex items-start gap-2.5">
							<Terminal className="mt-0.5 h-4 w-4 shrink-0 text-primary-accent" />
							<div>
								<p className="text-xs font-medium text-foreground">Publishing namespace</p>
								<p className="mt-1 text-xs text-muted-foreground">
									Use this identity when installing an Agent File or component from the team.
								</p>
							</div>
						</div>
						<code className="max-w-full overflow-x-auto whitespace-nowrap rounded-md border border-border/80 bg-background px-3 py-2 font-mono text-xs text-foreground">
							observal agent pull {team.handle}/agent-name
						</code>
							</footer>
						</>
					)}
				</div>
			</main>

			{canManageInvites && (
				<CreateTeamInviteDialogs team={team} open={inviteDialogOpen} onOpenChange={setInviteDialogOpen} />
			)}

			<SubmitComponentDialog
				key={`${team.id}-${activeType}`}
				open={componentDialogOpen}
				onOpenChange={setComponentDialogOpen}
				type={activeType}
				fixedTeamId={team.id}
				fixedVisibility={team.visibility === "private" ? "team" : undefined}
				onSubmit={(body) =>
					submitComponent.mutate(body, { onSuccess: () => setComponentDialogOpen(false) })
				}
				onSaveDraft={(body) =>
					saveComponentDraft.mutate(body, { onSuccess: () => setComponentDialogOpen(false) })
				}
				isSubmitting={submitComponent.isPending}
				isSavingDraft={saveComponentDraft.isPending}
			/>

			<AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete {team.name}?</AlertDialogTitle>
						<AlertDialogDescription>
							This permanently removes the empty teamspace and its membership records. Teamspaces that own registry items cannot be deleted.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
							onClick={() => deleteTeam.mutate(team.id, { onSuccess: () => navigate({ to: "/teamspaces" }) })}
						>
							Delete teamspace
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}
