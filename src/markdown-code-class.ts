// Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

// Kept dependency-free (no React/react-markdown imports) so it can be unit
// tested directly under Jest: markdown-renderer.tsx imports react-markdown
// at module scope, which ships ESM `export` syntax that jest.config.js's
// default (no `transformIgnorePatterns` override) can't parse — anything
// importing markdown-renderer.tsx transitively fails at parse time before a
// test can run (see the reverted MarkdownRenderer tests from PR #385).

// react-markdown@9 never sets `inline` (removed upstream), so the `code`
// component's `if (inline || !match)` branch also catches a fenced block
// with an unrecognized/missing language — that case keeps its default
// `pre > code` nesting from remark-rehype, unlike the SyntaxHighlighter
// branch, so it shouldn't get inline-code styling either. Distinguish the
// two by content shape instead: remark-rehype always appends a trailing
// `\n` to fenced code (even single-line), while true inline code never
// contains one. Only genuine inline code gets `.inline-code`, so CSS can
// target it directly instead of via `:not(pre) > code`, which broke once
// `PreTag="div"` put a wrapper div between a highlighted block's `<code>`
// and the outer `<pre>`.
export function resolveCodeClassName(
  children: unknown,
  className?: string
): string | undefined {
  const isTrulyInline = !String(children).includes('\n');
  return isTrulyInline ? `inline-code ${className || ''}`.trim() : className;
}
