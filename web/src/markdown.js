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
