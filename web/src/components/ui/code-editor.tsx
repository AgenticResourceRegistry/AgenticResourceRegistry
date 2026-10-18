// SPDX-FileCopyrightText: 2026 Hari Srinivasan <harisrini21@gmail.com>
// SPDX-FileCopyrightText: 2026 Kaushik <kaushikrjpm10@gmail.com>
// SPDX-License-Identifier: Apache-2.0

import { closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { python } from "@codemirror/lang-python";
import { HighlightStyle, StreamLanguage, syntaxHighlighting } from "@codemirror/language";
import { shell } from "@codemirror/legacy-modes/mode/shell";
import type { Extension } from "@codemirror/state";
import { highlightActiveLine, keymap, lineNumbers, placeholder } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import { EditorView, minimalSetup } from "codemirror";
import { useEffect, useMemo, useRef } from "react";
import { cn } from "@/lib/utils";

export type CodeEditorLanguage = "bash" | "javascript" | "json" | "plain" | "python";

interface CodeEditorProps {
	id?: string;
	label?: string;
	invalid?: boolean;
	errorId?: string;
	value: string;
	onChange: (value: string) => void;
	language?: CodeEditorLanguage;
	placeholder?: string;
	minHeightClassName?: string;
	className?: string;
}

const theme = EditorView.theme({
	"&": {
		backgroundColor: "oklch(var(--surface-sunken))",
		border: "1px solid oklch(var(--input))",
		borderRadius: "var(--radius-md)",
		color: "oklch(var(--foreground))",
		fontSize: "0.8125rem",
	},
	"&.cm-focused": {
		borderColor: "oklch(var(--ring))",
		outline: "2px solid oklch(var(--ring) / 0.2)",
		outlineOffset: "0",
	},
	".cm-content": {
		caretColor: "oklch(var(--foreground))",
		fontFamily: "var(--font-mono)",
		lineHeight: "1.65",
		padding: "0.625rem 0.875rem",
	},
	".cm-cursor": {
		borderLeftColor: "oklch(var(--foreground))",
		borderLeftWidth: "2px",
	},
	".cm-scroller": {
		fontFamily: "var(--font-mono)",
	},
	".cm-gutters": {
		backgroundColor: "oklch(var(--surface-sunken))",
		borderRight: "1px solid oklch(var(--border) / 0.7)",
		color: "oklch(var(--muted-foreground) / 0.72)",
		fontFamily: "var(--font-mono)",
		fontSize: "0.75rem",
	},
	".cm-lineNumbers .cm-gutterElement": {
		minWidth: "2.35rem",
		padding: "0 0.75rem 0 0.625rem",
		textAlign: "right",
	},
	".cm-activeLine, .cm-activeLineGutter": {
		backgroundColor: "oklch(var(--muted) / 0.38)",
	},
	".cm-selectionBackground, &.cm-focused .cm-selectionBackground": {
		backgroundColor: "oklch(var(--primary) / 0.2)",
	},
	".cm-placeholder": {
		color: "oklch(var(--muted-foreground) / 0.7)",
	},
	".cm-matchingBracket": {
		backgroundColor: "oklch(var(--primary-accent) / 0.16)",
		color: "oklch(var(--foreground))",
	},
});

const highlightStyle = HighlightStyle.define([
	{ tag: tags.keyword, color: "oklch(var(--primary-accent))" },
	{ tag: tags.propertyName, color: "oklch(var(--info))" },
	{ tag: tags.variableName, color: "oklch(var(--info))" },
	{ tag: tags.string, color: "oklch(var(--success))" },
	{ tag: tags.number, color: "oklch(var(--primary-accent))" },
	{ tag: tags.bool, color: "oklch(var(--warning))" },
	{ tag: tags.null, color: "oklch(var(--muted-foreground))" },
	{ tag: tags.comment, color: "oklch(var(--muted-foreground))", fontStyle: "italic" },
	{ tag: tags.punctuation, color: "oklch(var(--foreground) / 0.7)" },
]);

export function codeLanguageFromFilename(filename: string): CodeEditorLanguage {
	const lower = filename.toLowerCase().trim();
	if (/\.(bash|sh|zsh)$/.test(lower)) return "bash";
	if (/\.py$/.test(lower)) return "python";
	if (/\.(cjs|js|jsx|mjs|ts|tsx)$/.test(lower)) return "javascript";
	if (/\.json$/.test(lower)) return "json";
	return "plain";
}

export function codeLanguageLabel(language: CodeEditorLanguage) {
	return {
		bash: "Bash",
		javascript: "JavaScript",
		json: "JSON",
		plain: "plain text",
		python: "Python",
	}[language];
}

function languageExtension(language: CodeEditorLanguage): Extension | null {
	switch (language) {
		case "bash":
			return StreamLanguage.define(shell);
		case "javascript":
			return javascript({ jsx: true, typescript: true });
		case "json":
			return json();
		case "python":
			return python();
		case "plain":
			return null;
	}
}

export function CodeEditor({
	id,
	label,
	invalid,
	errorId,
	value,
	onChange,
	language = "json",
	placeholder: placeholderText,
	minHeightClassName = "min-h-56",
	className,
}: CodeEditorProps) {
	const parentRef = useRef<HTMLDivElement>(null);
	const viewRef = useRef<EditorView>(null);
	const onChangeRef = useRef(onChange);
	onChangeRef.current = onChange;

	const extensions = useMemo(() => {
		const items: Extension[] = [
			minimalSetup,
			lineNumbers(),
			highlightActiveLine(),
			closeBrackets(),
			keymap.of(closeBracketsKeymap),
			theme,
			syntaxHighlighting(highlightStyle),
			EditorView.lineWrapping,
			EditorView.updateListener.of((update) => {
				if (update.docChanged) onChangeRef.current(update.state.doc.toString());
			}),
		];
		const languageSupport = languageExtension(language);
		if (languageSupport) items.push(languageSupport);
		if (placeholderText) items.push(placeholder(placeholderText));
		return items;
	}, [language, placeholderText]);

	useEffect(() => {
		if (!parentRef.current) return;
		const view = new EditorView({ doc: value, extensions, parent: parentRef.current });
		viewRef.current = view;
		return () => view.destroy();
	}, [extensions]);

	useEffect(() => {
		const view = viewRef.current;
		if (!view || view.state.doc.toString() === value) return;
		view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } });
	}, [value]);

	useEffect(() => {
		const input = viewRef.current?.contentDOM;
		if (!input) return;
		input.setAttribute("aria-label", label ?? id ?? "Code editor");
		if (invalid) input.setAttribute("aria-invalid", "true");
		else input.removeAttribute("aria-invalid");
		if (invalid && errorId) input.setAttribute("aria-describedby", errorId);
		else input.removeAttribute("aria-describedby");
	}, [label, id, invalid, errorId]);

	return (
		<div
			aria-label={id}
			id={id}
			onClick={() => viewRef.current?.focus()}
			ref={parentRef}
			className={cn(
				"cursor-text overflow-hidden rounded-md [&_.cm-editor]:min-h-56 [&_.cm-scroller]:min-h-56",
				minHeightClassName,
				className,
			)}
		/>
	);
}
