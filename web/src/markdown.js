// Shared markdown-it renderer — used by the chat bubbles (ChatPane) and the
// reflection trace (EventTrace). One instance, one set of options.
import * as katexMod from '@vscode/markdown-it-katex'
import MarkdownIt from 'markdown-it'

// The model writes maths in LaTeX (inline $…$, block $$…$$, \frac \text \pi …). Without a math
// engine markdown-it renders it raw ("plein de balises, dur à lire") → render it with KaTeX.
// The plugin is CJS-only (main, no ESM `module`) ; the interop nests the plugin function under
// `.default` (sometimes twice) depending on the bundler → unwrap to the actual function so
// `md.use(fn)` doesn't get an object ("plugin.apply is not a function").
function _resolveDefault (m) {
  let p = m && m.default !== undefined ? m.default : m
  while (p && typeof p !== 'function' && p.default) p = p.default
  return p
}
const katexPlugin = _resolveDefault(katexMod)

// throwOnError:false : malformed LaTeX from a small model shows its source, never a crash/red error.
// The KaTeX CSS is imported once in main.js (glyphs + spacing).
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })
  .use(katexPlugin, { throwOnError: false })

export function renderMarkdown (text) {
  return md.render(text || '')
}
