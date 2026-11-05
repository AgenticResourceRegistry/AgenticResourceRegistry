// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute } from "@tanstack/react-router";
import { lazy } from "react";

const ReviewPolicyPage = lazy(() => import("@/pages/review/policy"));

export const Route = createFileRoute("/_authed/_admin/review-policy")({ component: ReviewPolicyPage });
