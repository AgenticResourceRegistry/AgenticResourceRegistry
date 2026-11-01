// SPDX-FileCopyrightText: 2026 Kaushik Kumar <kaushikrjpm10@gmail.com>
// SPDX-FileCopyrightText: 2026 dexterhere-2k <deepakmirchandani.ai28@jecrc.ac.in>
// SPDX-License-Identifier: Apache-2.0


import { useEffect } from "react";
import { useDeploymentConfig } from "@/hooks/use-deployment-config";
import { DEFAULT_REGISTRY_NAME } from "@/components/registry/registry-mark";

export function DynamicTitle() {
  const { brandingAppName, brandingLogo } = useDeploymentConfig();

  useEffect(() => {
    document.title = brandingAppName || DEFAULT_REGISTRY_NAME;
  }, [brandingAppName]);

  useEffect(() => {
    // 1. Remove all existing icon tags
    const iconLinks = document.querySelectorAll<HTMLLinkElement>("link[rel*='icon']");
    iconLinks.forEach((link) => link.remove());

    // Safari strictly requires a true network URL to reliably update favicons dynamically.
    // It ignores Data URIs and often fails on Blob URLs in Private Browsing.
    // We point directly to our dedicated API endpoint that serves the binary image.
    const timestamp = Date.now();
    const finalHref = `/api/v1/config/favicon?t=${timestamp}`;

    // 2. Inject fresh tags
    const newLink = document.createElement("link");
    newLink.id = "dynamic-favicon";
    newLink.rel = "shortcut icon";
    newLink.href = finalHref;
    document.head.appendChild(newLink);

    const standardLink = document.createElement("link");
    standardLink.id = "dynamic-favicon-standard";
    standardLink.rel = "icon";
    standardLink.href = finalHref;
    document.head.appendChild(standardLink);

    const appleLink = document.createElement("link");
    appleLink.id = "dynamic-favicon-apple";
    appleLink.rel = "apple-touch-icon";
    appleLink.href = finalHref;
    document.head.appendChild(appleLink);
  }, [brandingLogo]);

  return null;
}
