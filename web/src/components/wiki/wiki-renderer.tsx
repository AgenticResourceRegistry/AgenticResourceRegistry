// SPDX-FileCopyrightText: 2026 Vishnu Muthiah <vishnu.muthiah04@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useId, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { Info, AlertTriangle, Lightbulb, ShieldAlert } from "lucide-react";
import { listDocPaths } from "@/lib/docs-loader";

function MermaidBlock({ chart }: { chart: string }) {
	const id = useId().replace(/:/g, "");
	const [svg, setSvg] = useState<string | null>(null);
	const [error, setError] = useState(false);

	useEffect(() => {
		let cancelled = false;
		import("mermaid")
			.then(async ({ default: mermaid }) => {
				mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });
				const result = await mermaid.render(`mermaid-${id}`, chart);
				if (!cancelled) setSvg(result.svg);
			})
			.catch(() => {
				if (!cancelled) setError(true);
			});
		return () => {
			cancelled = true;
		};
	}, [chart, id]);

	if (error) {
		return <code className="block bg-[#1a1f2e] rounded-lg p-4 text-[13px] font-mono text-slate-300 overflow-x-auto mb-4 leading-relaxed whitespace-pre">{chart}</code>;
	}
	if (!svg) return <div className="mb-4 h-36 rounded-lg border border-border/50 bg-muted/20 animate-pulse" />;
	return <div className="mb-5 rounded-lg border border-border/50 bg-muted/20 p-4 overflow-x-auto" dangerouslySetInnerHTML={{ __html: svg }} />;
}

function CodeBlock({ children }: { children: React.ReactNode }) {
	const [copied, setCopied] = useState(false);
	const codeText = (() => {
		try {
			const child = children as { props?: { children?: unknown } };
			if (child?.props?.children) return String(child.props.children).trim();
		} catch {}
		return "";
	})();
	return (
		<pre className="mb-4 relative group">
			{codeText && (
				<button
					type="button"
					className="absolute top-2.5 right-2.5 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring bg-white/10 hover:bg-white/15 rounded px-1.5 py-1 flex items-center gap-1"
					onClick={() => { navigator.clipboard.writeText(codeText); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
					aria-label={copied ? "Code copied" : "Copy code"}
				>
					{copied ? (
						<>
							<svg className="h-3 w-3 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}><path d="M5 13l4 4L19 7" /></svg>
							<span className="text-[10px] text-emerald-400 font-medium">Copied</span>
						</>
					) : (
						<>
							<svg className="h-3 w-3 text-muted-foreground" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}><rect x="9" y="9" width="13" height="13" rx="2" ry="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>
							<span className="text-[10px] text-muted-foreground">Copy</span>
						</>
					)}
				</button>
			)}
			{children}
		</pre>
	);
}

const HEADING_ID = /\s*\{#([a-z0-9-]+)\}$/;

// Headings with an explicit anchor arrive as "Heading {#id}" (see stripDocNoise).
function splitHeading(children: React.ReactNode): { id?: string; children: React.ReactNode } {
	const parts = Array.isArray(children) ? [...children] : [children];
	const last = parts[parts.length - 1];
	if (typeof last !== "string") return { children };
	const match = last.match(HEADING_ID);
	if (!match) return { children };
	parts[parts.length - 1] = last.replace(HEADING_ID, "");
	return { id: match[1], children: parts };
}

function extractId(children: unknown, props: Record<string, unknown>): string {
	if (props.id) return props.id as string;
	return String(children).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-$/, "");
}

function frontmatterTitle(frontmatter: string): string | undefined {
	const raw = frontmatter.match(/^title:\s*(.+?)\s*$/m)?.[1];
	if (!raw) return undefined;
	if (!raw.startsWith('"')) return raw;
	try {
		return JSON.parse(raw) as string;
	} catch {
		return raw.slice(1, -1);
	}
}

function stripDocNoise(content: string): string {
	return content
		// Docs pages carry YAML frontmatter (SPDX comments and the page title that
		// the docs site renders). Show the title as the page heading instead.
		.replace(/^---\r?\n([\s\S]*?)\r?\n---\r?\n/, (_match, frontmatter: string) => {
			const title = frontmatterTitle(frontmatter);
			return title ? `# ${title}\n\n` : "";
		})
		// Drop SPDX and other HTML comment blocks wherever they appear.
		.replace(/<!--[\s\S]*?-->/g, "")
		// Docs pages pin a heading's id with an `<a id="...">` line above it, so
		// deep links keep working on the docs site. Carry the id onto the heading.
		.replace(/^<a id="([a-z0-9-]+)"><\/a>\s*\n(#{1,6} .+)$/gm, "$2 {#$1}")
		.trim();
}

const DOCS_SITE = "https://docs.observal.io";

// Docs pages link to each other by docs-site route (`/cli/config`, `/self-hosting`).
// Map a route back to the bundled markdown file, preferring a folder's index page.
function routeToDocPath(route: string, known: string[]): string | undefined {
	return [`${route}.md`, `${route}/index.md`, ...(route ? [] : ["index.md"])].find((path) => known.includes(path));
}

function resolveDocHref(href: string | undefined, basePath?: string): string | undefined {
	if (!href) return undefined;
	if (href.startsWith("http://") || href.startsWith("https://") || href.startsWith("#")) return href;

	const [pathPart, hash] = href.split("#");
	const isMarkdownFile = pathPart.endsWith(".md");
	const isDocRoute = pathPart.startsWith("/") && !/\.[a-z0-9]+$/i.test(pathPart);
	if (!isMarkdownFile && !isDocRoute) return href;

	const baseDir = basePath?.includes("/") ? basePath.split("/").slice(0, -1).join("/") : "";
	const rawParts = (pathPart.startsWith("/") ? pathPart.slice(1) : `${baseDir}/${pathPart}`).split("/");
	const normalized: string[] = [];
	for (const part of rawParts) {
		if (!part || part === ".") continue;
		if (part === "..") normalized.pop();
		else normalized.push(part);
	}
	const joined = normalized.join("/");
	const route = isMarkdownFile ? joined.replace(/\.md$/, "").replace(/(^|\/)index$/, "") : joined;
	const fragment = hash ? `#${hash}` : "";
	const docPath = routeToDocPath(route, listDocPaths());
	// The wiki bundles a curated subset of pages. Send the rest to the docs site.
	if (!docPath) return `${DOCS_SITE}/${route}${fragment}`;
	return `/wiki?doc=${encodeURIComponent(docPath)}${fragment}`;
}

function createComponents(basePath?: string): Components {
	return {
	h1: ({ children: raw, ...props }) => {
		const { id: anchor, children } = splitHeading(raw);
		const id = anchor ?? extractId(children, props);
		return (
			<h1 id={id} className="scroll-mt-20 text-3xl font-bold mt-10 mb-5 pb-3 border-b border-border">
				{children}
			</h1>
		);
	},
	h2: ({ children: raw, ...props }) => {
		const { id: anchor, children } = splitHeading(raw);
		const id = anchor ?? extractId(children, props);
		return (
			<h2 id={id} className="scroll-mt-20 text-xl font-semibold mt-10 mb-4 pb-2 border-b border-primary/30 text-foreground">
				{children}
			</h2>
		);
	},
	h3: ({ children: raw, ...props }) => {
		const { id: anchor, children } = splitHeading(raw);
		const id = anchor ?? extractId(children, props);
		return (
			<h3 id={id} className="scroll-mt-20 text-lg font-semibold mt-8 mb-3 text-foreground">
				{children}
			</h3>
		);
	},
	h4: ({ children: raw, ...props }) => {
		const { id: anchor, children } = splitHeading(raw);
		const id = anchor ?? extractId(children, props);
		return (
			<h4 id={id} className="scroll-mt-20 text-base font-semibold mt-7 mb-2 text-foreground">
				{children}
			</h4>
		);
	},
	p: ({ children }) => <p className="text-[15px] leading-[1.75] mb-4 text-foreground/80">{children}</p>,
	ul: ({ children }) => <ul className="list-disc pl-5 mb-5 text-[15px] space-y-2 text-foreground/80 marker:text-[#08c9b9]/50">{children}</ul>,
	ol: ({ children }) => <ol className="list-decimal pl-5 mb-5 text-[15px] space-y-2 text-foreground/80">{children}</ol>,
	li: ({ children }) => <li className="leading-[1.75]">{children}</li>,
	code: ({ children, className }) => {
		const isBlock = className?.includes("language-");
		if (isBlock) {
			if (className?.includes("language-mermaid")) {
				return <MermaidBlock chart={String(children).trim()} />;
			}
			return (
				<code className="block bg-[#1a1f2e] rounded-lg p-4 text-[13px] font-mono text-slate-300 overflow-x-auto mb-4 leading-relaxed">
					{children}
				</code>
			);
		}
		return <code className="font-mono text-[13px]">{children}</code>;
	},
	pre: ({ children }) => {
		const child = children as { props?: { className?: string; children?: unknown } };
		const className = child?.props?.className || "";
		// Mermaid renders its own wrapper.
		if (className.includes("language-mermaid")) {
			return <>{children}</>;
		}
		// Only show copy button on bash, shell, nginx (runnable/pasteable content)
		const copyable = /language-(bash|sh|shell|nginx|yaml|toml)/.test(className);
		if (copyable) {
			return <CodeBlock>{children}</CodeBlock>;
		}
		// Everything else: plain code block, no copy button
		return <pre className="mb-4">{children}</pre>;
	},
	strong: ({ children }) => {
		const text = String(children);
		if (text === "Required") return <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-destructive/15 text-destructive">Required</span>;
		if (text === "Optional") return <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase bg-muted text-muted-foreground">Optional</span>;
		if (text.startsWith("Default:")) return <span className="text-[15px]"><span className="font-medium text-muted-foreground">Default:</span> <span className="font-mono text-[14px]">{text.slice(8).trim()}</span></span>;
		if (text.startsWith("Example:")) return <span className="text-[15px]"><span className="font-medium text-muted-foreground">Example:</span> <span className="font-mono text-[14px]">{text.slice(8).trim()}</span></span>;
		if (text.startsWith("Checkpoint:")) return <span className="text-[15px]"><span className="inline-flex items-center gap-1 font-semibold text-emerald-400">✓ Checkpoint:</span> {text.slice(11).trim()}</span>;
		return <strong className="font-semibold text-foreground">{children}</strong>;
	},
	a: ({ children, href }) => {
		const resolvedHref = resolveDocHref(href, basePath);
		const external = resolvedHref?.startsWith("http://") || resolvedHref?.startsWith("https://");
		return (
			<a
				href={resolvedHref}
				className="text-primary underline decoration-primary/40 hover:decoration-primary underline-offset-2 transition-colors"
				target={external ? "_blank" : undefined}
				rel={external ? "noopener noreferrer" : undefined}
			>
				{children}
			</a>
		);
	},
	blockquote: ({ children }) => {
		const text = String(children);
		let type: "info" | "warning" | "tip" | "danger" = "info";
		if (text.includes("Warning:") || text.includes("⚠")) type = "warning";
		else if (text.includes("Tip:") || text.includes("💡")) type = "tip";
		else if (text.includes("Danger:") || text.includes("🚨")) type = "danger";

		const styles = {
			info: "border-l-[3px] border-blue-500/60 bg-blue-500/5",
			warning: "border-l-[3px] border-amber-500/60 bg-amber-500/5",
			tip: "border-l-[3px] border-emerald-500/60 bg-emerald-500/5",
			danger: "border-l-[3px] border-red-500/60 bg-red-500/5",
		};
		const icons = {
			info: <Info className="h-3.5 w-3.5 text-blue-500 shrink-0 mt-0.5" />,
			warning: <AlertTriangle className="h-3.5 w-3.5 text-amber-500 shrink-0 mt-0.5" />,
			tip: <Lightbulb className="h-3.5 w-3.5 text-emerald-500 shrink-0 mt-0.5" />,
			danger: <ShieldAlert className="h-3.5 w-3.5 text-red-500 shrink-0 mt-0.5" />,
		};

		return (
			<blockquote className={`${styles[type]} rounded-r-md pl-3 pr-3 py-3 mb-5 not-italic`}>
				<div className="flex gap-2 items-start">
					{icons[type]}
					<div className="flex-1 min-w-0 text-[15px] leading-[1.7] [&>p]:mb-1.5 [&>p:last-child]:mb-0">{children}</div>
				</div>
			</blockquote>
		);
	},
	hr: () => <hr className="my-8 border-border/30" />,
	table: ({ children }) => (
		<div className="overflow-x-auto mb-5 rounded-lg border border-border/50">
			<table className="min-w-[640px] w-full text-[14px]">{children}</table>
		</div>
	),
	thead: ({ children }) => <thead className="bg-muted/40">{children}</thead>,
	th: ({ children }) => <th className="px-3 py-2 text-left font-semibold text-xs uppercase tracking-wide text-muted-foreground border-b border-border/60">{children}</th>,
	td: ({ children }) => <td className="px-3 py-2 border-b border-border/30 align-top">{children}</td>,
	tr: ({ children }) => <tr className="hover:bg-muted/20 transition-colors">{children}</tr>,
	};
}

export function WikiRenderer({ content, basePath }: { content: string; basePath?: string }) {
	const cleaned = stripDocNoise(content);
	return (
		<div className="max-w-none">
			<ReactMarkdown remarkPlugins={[remarkGfm]} components={createComponents(basePath)}>
				{cleaned}
			</ReactMarkdown>
		</div>
	);
}
