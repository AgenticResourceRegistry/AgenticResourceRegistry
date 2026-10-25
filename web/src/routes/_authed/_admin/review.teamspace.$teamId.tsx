// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute } from "@tanstack/react-router";
import { lazy } from "react";

const TeamspaceReview = lazy(() => import("@/pages/review/teamspace"));

export const Route = createFileRoute("/_authed/_admin/review/teamspace/$teamId")({ component: TeamspaceReview });
