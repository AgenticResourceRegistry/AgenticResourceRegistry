// SPDX-FileCopyrightText: 2026 Lokesh Selvam <lokeshselvam7025@gmail.com>
// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useDeploymentConfig } from "@/hooks/use-deployment-config";

/**
 * The instance's brand mark.
 *
 * Honours white-label branding: a deployment that replaced the logo sees its
 * own mark, not Observal's. Decorative by default — the surrounding text
 * carries the meaning, so the image stays out of the accessibility tree.
 */
export function RegistryMark({ size = 16, className = "" }: { size?: number; className?: string }) {
	const { brandingLogo } = useDeploymentConfig();
	return (
		<img
			src={brandingLogo || "/logo.png"}
			alt=""
			aria-hidden="true"
			width={size}
			height={size}
			className={`shrink-0 object-contain ${className}`}
		/>
	);
}

/** The instance's display name, for use in sentences like "Already in X". */
export function useRegistryName(): string {
	const { brandingAppName } = useDeploymentConfig();
	return brandingAppName || "Observal";
}
