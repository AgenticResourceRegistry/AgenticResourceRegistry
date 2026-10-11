// SPDX-FileCopyrightText: 2026 Naraen Rammoorthi <naraen13@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute } from "@tanstack/react-router";
import { lazy } from "react";

const ReviewDetail = lazy(() => import("@/pages/review/detail"));

export const Route = createFileRoute("/_authed/review_/$number")({ component: ReviewDetail });
