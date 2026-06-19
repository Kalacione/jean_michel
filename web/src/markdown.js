// Shared markdown-it renderer — used by the chat bubbles (ChatPane) and the
// reflection trace (EventTrace). One instance, one set of options.
import MarkdownIt from 'markdown-it'
import katexPlugin from './markdown-katex'

// The model writes maths in LaTeX (inline $…$, block $$…$$, \frac \text \pi …). Without a math
// engine markdown-it renders it raw ("plein de balises, dur à lire") → render it with KaTeX.
// The plugin calls `katex` directly (see markdown-katex.js for why, not the CJS wrapper).
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
  .use(katexPlugin)

export function renderMarkdown (text) {
  return md.render(text || '')
}

// Above this many characters we render RAW text instead of markdown/KaTeX : parsing
// a very large buffer (a runaway generation, or replayed on reopen) blocks the main
// thread, while raw text is cheap. Normal messages sit far below this.
export const MD_RAW_THRESHOLD = 40000

export function isHugeText (text) {
  return (text || '').length > MD_RAW_THRESHOLD
}
