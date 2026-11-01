// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useState, useEffect } from "react";
import { useSearch } from "@tanstack/react-router";
import { loadDoc, listDocPaths } from "@/lib/docs-loader";
import { WikiRenderer } from "@/components/wiki/wiki-renderer";
import { PageHeader } from "@/components/layouts/page-header";
import { Loader2, ChevronRight, ArrowLeft } from "lucide-react";
import { WikiNavItem } from "@/components/registry/registry-primitives";

function organizeBySection(paths: string[]): Record<string, string[]> {
	const sections: Record<string, string[]> = {};
	for (const p of paths) {
		const parts = p.split("/");
		const section = parts.length > 1 ? parts.slice(0, -1).join("/") : "general";
		if (!sections[section]) sections[section] = [];
		sections[section].push(p);
	}
	return sections;
}

function pathToTitle(path: string): string {
	const filename = path.split("/").pop()?.replace(".md", "") || path;
	return filename
		.split("-")
		.map((w) => w.charAt(0).toUpperCase() + w.slice(1))
		.join(" ");
}

function sectionLabel(key: string): string {
	const labels: Record<string, string> = {
		"general": "General",
		"self-hosting": "Self-Hosting",
		"getting-started": "Getting Started",
		"reference": "Reference",
		"use-cases": "Use Cases",
	};
	return labels[key] || pathToTitle(key);
}

function docHref(path: string): string {
	return `/wiki?doc=${encodeURIComponent(path)}`;
}

export default function WikiPage() {
	const search = useSearch({ strict: false }) as { doc?: string };
	const activePath = search.doc || null;
	const [content, setContent] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const allPaths = listDocPaths();
	const sections = organizeBySection(allPaths);

	useEffect(() => {
		if (!activePath) {
			setContent(null);
			return;
		}
		setLoading(true);
		loadDoc(activePath)
			.then((md) => setContent(md ?? null))
			.catch(() => setContent(null))
			.finally(() => setLoading(false));
	}, [activePath]);

	const sortedSections = Object.entries(sections).sort(([a], [b]) => {
		const order = ["getting-started", "general", "self-hosting", "reference", "use-cases"];
		return (order.indexOf(a) === -1 ? 99 : order.indexOf(a)) - (order.indexOf(b) === -1 ? 99 : order.indexOf(b));
	});

	return (
		<>
			<PageHeader
				title={activePath ? pathToTitle(activePath) : "Wiki"}
				breadcrumbs={[
					{ label: "Registry", href: "/" },
					{ label: "Wiki", href: "/wiki" },
					...(activePath ? [{ label: pathToTitle(activePath) }] : []),
				]}
			/>
			<div className="page-body w-full">
				{!activePath ? (
					<>
						<div className="grid grid-cols-1 gap-3.5 md:grid-cols-[200px_minmax(0,1fr)]">
							{/* Sidebar navigation */}
							<aside className="self-start rounded-xl bg-card p-2 shadow-sm">
								{sortedSections.map(([section, paths]) => (
									<div key={section} className="mb-3 last:mb-0">
										<p className="mb-1 px-3 text-2xs font-medium uppercase tracking-[0.06em] text-muted-foreground">
											{sectionLabel(section)}
										</p>
										{paths.sort().map((p) => (
											<WikiNavItem key={p} onClick={() => window.location.href = docHref(p)}>
												{pathToTitle(p)}
											</WikiNavItem>
										))}
									</div>
								))}
							</aside>

							{/* Overview grid */}
							<div className="rounded-xl bg-card p-6 shadow-sm">
								<p className="mb-1.5 text-2xs font-medium uppercase tracking-[0.06em] text-muted-foreground">
									Getting started · overview
								</p>
								<h2 className="text-xl font-medium tracking-tight">How Observal fits together</h2>
								<p className="mt-2.5 max-w-[680px] text-sm leading-relaxed text-muted-foreground">
									Observal gives engineering teams one place to package coding Agent Files, install them across
									different harnesses, and understand what those Agent Files actually do during execution.
								</p>

								<div className="mt-6 space-y-3">
									<h3 className="text-base font-medium">The operating model</h3>
									<pre className="rounded-[11px] bg-surface-raised px-4 py-3 font-mono text-2xs leading-relaxed text-foreground">
										Registry asset → Agent File version → Harness installation → Session trace → Insight
									</pre>
								</div>

								<div className="mt-6 space-y-3">
									<h3 className="text-base font-medium">Browse all pages</h3>
									<div className="divide-y divide-border">
										{sortedSections.map(([section, paths]) => (
											<section key={section} className="grid gap-4 py-4 md:grid-cols-[140px_minmax(0,1fr)]">
												<div>
													<h4 className="text-xs font-medium">{sectionLabel(section)}</h4>
													<p className="mt-0.5 text-[10px] text-muted-foreground">{paths.length} page{paths.length === 1 ? "" : "s"}</p>
												</div>
												<ul className="grid gap-x-6 gap-y-1 sm:grid-cols-2 xl:grid-cols-3">
													{paths.sort().map((p) => (
														<li key={p}>
															<a href={docHref(p)} className="group flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-foreground/75 transition-colors hover:bg-surface-raised hover:text-foreground">
																<ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground transition-colors group-hover:text-primary" />
																<span className="min-w-0 flex-1 truncate">{pathToTitle(p)}</span>
															</a>
														</li>
													))}
												</ul>
											</section>
										))}
									</div>
								</div>
							</div>
						</div>
					</>
				) : loading ? (
					<div className="flex items-center justify-center py-12">
						<Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
					</div>
				) : content ? (
					<article className="mx-auto max-w-4xl">
						<a href="/wiki" className="mb-6 inline-flex items-center gap-2 text-xs text-muted-foreground transition-colors hover:text-foreground">
							<ArrowLeft className="h-3.5 w-3.5" />
							Back to wiki
						</a>
						<WikiRenderer content={content} basePath={activePath} />
					</article>
				) : (
					<div className="py-12 text-center">
						<p className="text-sm text-muted-foreground">Document not found.</p>
						<a href="/wiki" className="mt-3 inline-flex text-xs text-primary hover:underline">Back to wiki</a>
					</div>
				)}
			</div>
		</>
	);
}
