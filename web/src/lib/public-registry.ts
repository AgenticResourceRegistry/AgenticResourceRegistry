// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

export function isPublicRegistryPath(pathname: string): boolean {
  const segments = pathname.split("/").filter(Boolean);
  if (segments.length === 0) return true;
  // Discovery search is served to guests under the same switch as the rest of the public registry.
  if (["leaderboard", "wiki", "discover"].includes(segments[0])) return segments.length === 1;
  if (segments[0] === "resources") return segments.length <= 2;
  if (segments[0] === "components") {
    return segments.length === 1 || segments.length === 2 || segments.length === 4;
  }
  if (segments[0] === "agents") {
    return (
      segments.length === 1 ||
      (segments.length === 2 && segments[1] !== "builder") ||
      (segments.length === 3 && segments[2] !== "insights")
    );
  }
  return false;
}
