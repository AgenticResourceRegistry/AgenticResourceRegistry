// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-FileCopyrightText: 2026 Apoorv Garg <apoorvgarg.work@gmail.com>
// SPDX-License-Identifier: Apache-2.0


import { useQuery } from "@tanstack/react-query";
import { config, type PublicConfig } from "@/lib/api";

export function useDeploymentConfig() {
	const { data, isLoading, isError } = useQuery<PublicConfig>({
		queryKey: ["config", "public"],
		queryFn: config.public,
		staleTime: 10_000, // rollout hints must not remain fresh for minutes
		refetchOnMount: "always",
		refetchOnWindowFocus: "always",
		retry: 2,
	});

	return {
		licensed: data?.licensed ?? true,
		licensedFeatures: data?.licensed_features ?? ["all"],
		ssoEnabled: data?.sso_enabled ?? false,
		googleSsoEnabled: data?.google_sso_enabled ?? false,
		githubSsoEnabled: data?.github_sso_enabled ?? false,
		ssoOnly: data?.sso_only ?? false,
		publicRegistryEnabled: data?.public_registry_enabled ?? false,
		selfRegistrationEnabled: data?.self_registration_enabled ?? false,
		skillFolderDeliveryEnabled: data?.skill_folder_delivery_enabled === true && !isError,
		samlEnabled: data?.saml_enabled ?? false,
		enabledFeatures: data?.enabled_features ?? [],
		brandingLogo: data?.branding_logo ?? null,
		brandingAppName: data?.branding_app_name ?? null,
		brandingWordmark: data?.branding_wordmark ?? null,
		loading: isLoading,
		configError: isError,
	};
}

export function useServerVersion() {
	const { data, isLoading } = useQuery({
		queryKey: ["config", "version"],
		queryFn: config.version,
		staleTime: 5 * 60 * 1000,
		retry: 1,
	});

	return {
		serverVersion: data?.server_version ?? null,
		loading: isLoading,
	};
}
