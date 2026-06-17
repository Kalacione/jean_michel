// Shared markdown-it renderer — used by the chat bubbles (ChatPane) and the
// reflection trace (EventTrace). One instance, one set of options.
import katex from '@vscode/markdown-it-katex'
import MarkdownIt from 'markdown-it'

// The model writes maths in LaTeX (inline $…$, block $$…$$, \frac \text \pi …). Without a math
// engine markdown-it renders it raw ("plein de balises, dur à lire") → render it with KaTeX.
// throwOnError:false : malformed LaTeX from a small model shows its source, never a crash/red error.
// The KaTeX CSS is imported once in main.js (glyphs + spacing).
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
  .use(katex, { throwOnError: false })

export function renderMarkdown (text) {
  return md.render(text || '')
}
