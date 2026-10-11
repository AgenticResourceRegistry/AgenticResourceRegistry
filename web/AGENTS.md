<!-- SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com> -->
<!-- SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Web Frontend

Vite 8 / React 19 / TypeScript 6 / TanStack Router / Tailwind CSS 4 / Playwright 1.59

## How users interact with it

The web UI is one of three ways to interact with Observal, alongside the CLI and the bundled observal skill. It covers:

- **Browsing and installing agents and components** from the registry (Agentic Resources)
- **Viewing session traces** with conversation replay, span tree, and token counts
- **Admin operations** such as review queue, user management, insights, and audit logs
- **Agent building** with component assembly and live YAML preview

## Stack decisions

| Concern | Choice | Why |
|---------|--------|-----|
| Framework | Vite SPA with TanStack Router | Static Docker asset, simple split from FastAPI |
| UI primitives | shadcn/ui | Composable, accessible, themeable |
| Data fetching | TanStack Query via `use-api.ts` | Caching, deduplication, mutations |
| Tables | TanStack Table | Sort, filter, pagination built-in |
| Charts | Recharts 3 | Simple, works with OKLCH tokens |
| Auth storage | Current code uses sessionStorage for access token, localStorage for refresh token and profile cache | Documents existing behavior. Do not add new auth localStorage use unless intentionally changing the auth model. |
| API proxy | Vite dev proxy and nginx in Docker | Single origin for `/api/v1/*`, no CORS in prod |
| Fonts | Local files only | No Google Fonts CDN calls |
| Design tokens | OKLCH in `app.css` | Perceptually uniform themes |
| Harness list | Server-fetched (`/api/v1/config/harnesses`) | Never hardcoded in frontend |

## Design system

OKLCH color space with semantic tokens: `background`, `foreground`, `card`, `border`, `primary`, `secondary`, `accent`, `destructive`, `success`, `warning`, `info`.

Themes include light, dark, midnight, forest, sunset, solarized, dracula, nord, monokai, gruvbox, catppuccin, tokyo night, one dark, and rose pine. Tokens are defined in `app.css` and switched by `ThemeProvider` in `src/lib/theme.tsx`.

Typography uses local fonts only. Tailwind CSS 4 reads tokens directly from CSS.

## Route groups

TanStack Router file routes live in `src/routes/`. Several routes lazy-load page modules from `src/pages/` while the migration from page components to route files continues.

```
src/routes/
├── (auth)/                         # Unauthenticated
│   ├── login.tsx                   #   Login and first-run admin init
│   ├── register.tsx                #   User registration
│   └── device.tsx                  #   Device authorization
├── _authed.tsx                     # Authenticated layout and guard
├── _authed/
│   ├── index.tsx                   #   Registry home
│   ├── resources/
│   │   └── $type.tsx               #   Agentic Resources list (agents | mcps | skills | hooks | prompts | sandboxes)
│   ├── agents/
│   │   ├── index.tsx               #   Redirect to /resources/agents
│   │   ├── $agentId.tsx            #   Agent detail
│   │   ├── builder.tsx             #   Agent builder
│   │   └── $agentId/insights/$reportId.tsx
│   ├── components/
│   │   ├── index.tsx               #   Redirect to /resources/mcps
│   │   └── $componentId.tsx        #   Component detail
│   ├── leaderboard.tsx             #   Agent leaderboard
│   ├── insights/$reportId.tsx      #   Insight report detail
│   ├── wiki/index.tsx              #   Wiki/help content
│   ├── _admin.tsx                  #   Admin layout and guard
│   ├── _admin/
│   │   ├── dashboard.tsx
│   │   ├── review.tsx
│   │   ├── settings.tsx
│   │   ├── sso.tsx
│   │   ├── users.tsx
│   │   ├── audit-log.tsx
│   │   ├── security-events.tsx
│   │   └── diagnostics.tsx
│   ├── _user.tsx                   #   User layout
│   └── _user/
│       ├── account.tsx
│       └── traces/
│           ├── index.tsx
│           └── $traceId.tsx
└── __root.tsx                      # Query client, theme provider, error boundary
```

## Key directories

```
src/components/
├── builder/       # model-picker, preview-panel, sortable-component-list, validation-panel
├── dashboard/     # Stat cards, trend charts, bar lists, heatmap, time range select
├── layouts/       # AuthGuard, AdminGuard, RoleGuard, DashboardShell, PageHeader
├── nav/           # RegistrySidebar, CommandMenu, NavUser, GitHubStarBanner
├── registry/      # AgentCard, AgentEditForm, ComponentCard, ComponentEditForm, PullCommand,
│                  # StatusBadge, SubmitComponentDialog, ReviewForm, HarnessBadges
├── review/        # ReviewDetailSheet, ValidationBadges
├── shared/        # SkeletonLayouts, ErrorState, EmptyState
├── traces/        # TraceList, TraceDetail, SpanTree
└── ui/            # shadcn/ui primitives

src/pages/         # Lazy-loaded page components used by route files
src/routes/        # TanStack Router file routes
src/hooks/         # TanStack Query hooks and auth guards
src/lib/           # API wrapper, types, query client, theme, GraphQL WS
```

## Key files

- `src/lib/api.ts`: typed fetch wrapper and current auth storage helpers
- `src/lib/types.ts`: shared TypeScript interfaces for API responses
- `src/lib/graphql-ws.ts`: GraphQL WebSocket subscription client
- `src/lib/harness-capabilities.ts`: harness capability detection
- `src/lib/query-client.ts`: TanStack Query client config
- `src/lib/theme.tsx`: theme provider and storage
- `src/hooks/use-api.ts`: TanStack Query hook exports for endpoints
- `src/hooks/use-auth.ts`: auth guard and optional auth helper
- `src/hooks/use-deployment-config.ts`: feature flags and license status
- `src/hooks/use-harnesses.ts`: harness list from server
- `vite.config.ts`: Vite build, chunks, and dev proxy

## Coding patterns

**Data fetching:** Always use hooks from `use-api.ts`. Never call `fetch` directly in components unless the endpoint is not covered yet and the smallest change is local. Prefer adding a hook for reusable endpoints.

**Types:** API response types live in `src/lib/types.ts`. Do not define inline types for API data that is shared by multiple components. If a new endpoint is added, add its types there.

**Access control:** Feature access is role-based and enforced server-side. The frontend may use deployment config for display decisions, but never trusts the client to enforce access.

**Harness list:** Fetched from `/api/v1/config/harnesses` via `useHarnesses()`. Never hardcode harness names or capabilities in the frontend. The server is the single source of truth.

**Auth state:** Current implementation stores `observal_access_token` in sessionStorage. It stores `observal_refresh_token` and cached user role/profile fields in localStorage so refresh and nav state survive reloads and new tabs. Treat this as existing behavior, not a pattern to expand. If changing auth storage, migrate deliberately and update this file in the same PR.

**Theming:** Use semantic tokens such as `var(--primary)` and `var(--destructive)`. Never use raw color values in components. Theme tokens live in `app.css`.

## Commands

```bash
pnpm dev          # Vite dev server on :3000
pnpm build        # Typecheck and production build (Vite 8 / Rolldown; chunking is `build.rolldownOptions.output.codeSplitting`, not Rollup `manualChunks`)
pnpm lint         # ESLint
pnpm typecheck    # TypeScript only
pnpm e2e          # Playwright, requires running Docker stack
pnpm e2e:kiro     # Kiro-specific e2e tests
pnpm e2e:ui       # Playwright UI mode
```

E2E specs live in `tests/e2e/*.spec.ts` in the repo root workspace.

For UI changes, rebuild the live stack with `make rebuild` and check the affected pages in a browser (or run the relevant spec with `CI=1 pnpm exec playwright test <spec>` against `:80`) in addition to `pnpm build` and `pnpm lint`.
