// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useQuery } from "@tanstack/react-query";
import { discovery } from "@/lib/api";
import type { DiscoverySearchFilter } from "@/lib/types";

const MIN_QUERY_LENGTH = 2;

/**
 * Discovery search over every registry kind at once. Disabled until the
 * query has a couple of characters. Results are never carried over from a
 * previous query key: a card's copyable command is built from the result's
 * identifier, so showing another query's results while this one loads could
 * hand the user a command for a resource that does not match what they typed.
 */
export function useDiscoverySearch(text: string, filter: DiscoverySearchFilter = {}, pageSize = 10, enabled = true) {
	const trimmed = text.trim();
	return useQuery({
		queryKey: ["discovery", "search", trimmed, filter.kind ?? "all", filter.harness ?? "any", !!filter.includeUnapproved, pageSize],
		queryFn: () => discovery.search(trimmed, filter, pageSize),
		enabled: enabled && trimmed.length >= MIN_QUERY_LENGTH,
		staleTime: 60 * 1000,
		retry: false,
	});
}
