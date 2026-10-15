// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { createFileRoute, redirect } from "@tanstack/react-router";

export const Route = createFileRoute("/_authed/resources/")({
  beforeLoad: () => {
    throw redirect({ to: "/resources/$type", params: { type: "agents" }, replace: true });
  },
});
