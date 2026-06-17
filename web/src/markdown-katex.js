// Minimal KaTeX rule for markdown-it — inline $…$ and block $$…$$.
// We call `katex` directly instead of `@vscode/markdown-it-katex` : that CJS plugin
// bundles its own katex, which Vite's dep-optimizer (esbuild browser+minify) mangles
// (the lexer reads only `\`+1 letter → `\text` renders as red `\t` + `ext`). Our own
// `katex` import survives the same optimize untouched. This is the canonical
// markdown-it-katex tokenizer (Waylon Flinn, MIT) — battle-tested for delimiters,
// escaping (`\$`) and the currency guard (no digit right after a closing `$`).
import katex from 'katex'

function isValidDelim (state, pos) {
  const prev = pos > 0 ? state.src.charCodeAt(pos - 1) : -1
  const next = pos + 1 <= state.posMax ? state.src.charCodeAt(pos + 1) : -1
  // can't open on a space/tab right after `$` ; can't close on a space before,
  // nor when a digit follows (so "$5 and $6" isn't read as math).
  const canOpen = !(next === 0x20 || next === 0x09)
  const canClose = !(prev === 0x20 || prev === 0x09) && !(next >= 0x30 && next <= 0x39)
  return { canOpen, canClose }
}

function mathInline (state, silent) {
  if (state.src[state.pos] !== '$') return false
  let res = isValidDelim(state, state.pos)
  if (!res.canOpen) {
    if (!silent) state.pending += '$'
    state.pos += 1
    return true
  }
  const start = state.pos + 1
  let match = start
  let pos
  while ((match = state.src.indexOf('$', match)) !== -1) {
    pos = match - 1
    while (state.src[pos] === '\\') pos -= 1
    if ((match - pos) % 2 === 1) break // odd run of backslashes → unescaped `$`
    match += 1
  }
  if (match === -1) {
    if (!silent) state.pending += '$'
    state.pos = start
    return true
  }
  if (match - start === 0) { // empty `$$`
    if (!silent) state.pending += '$$'
    state.pos = start + 1
    return true
  }
  res = isValidDelim(state, match)
  if (!res.canClose) {
    if (!silent) state.pending += '$'
    state.pos = start
    return true
  }
  if (!silent) {
    const token = state.push('math_inline', 'math', 0)
    token.markup = '$'
    token.content = state.src.slice(start, match)
  }
  state.pos = match + 1
  return true
}

function mathBlock (state, start, end, silent) {
  let firstLine
  let lastLine
  let next
  let lastPos
  let found = false
  let pos = state.bMarks[start] + state.tShift[start]
  let max = state.eMarks[start]
  if (pos + 2 > max) return false
  if (state.src.slice(pos, pos + 2) !== '$$') return false
  pos += 2
  firstLine = state.src.slice(pos, max)
  if (silent) return true
  if (firstLine.trim().slice(-2) === '$$') {
    firstLine = firstLine.trim().slice(0, -2)
    found = true
  }
  for (next = start; !found;) {
    next++
    if (next >= end) break
    pos = state.bMarks[next] + state.tShift[next]
    max = state.eMarks[next]
    if (pos < max && state.tShift[next] < state.blkIndent) break
    if (state.src.slice(pos, max).trim().slice(-2) === '$$') {
      lastPos = state.src.slice(0, max).lastIndexOf('$$')
      lastLine = state.src.slice(pos, lastPos)
      found = true
    }
  }
  state.line = next + 1
  const token = state.push('math_block', 'math', 0)
  token.block = true
  token.content =
    (firstLine && firstLine.trim() ? firstLine + '\n' : '') +
    state.getLines(start + 1, next, state.tShift[start], true) +
    (lastLine && lastLine.trim() ? lastLine : '')
  token.map = [start, state.line]
  token.markup = '$$'
  return true
}

// throwOnError:false → malformed LaTeX from a small model shows its source, never a crash.
export default function katexPlugin (md, options) {
  const opts = { throwOnError: false, ...(options || {}) }
  const render = (latex, displayMode) => {
    try {
      return katex.renderToString(latex, { ...opts, displayMode })
    } catch {
      return md.utils.escapeHtml(latex)
    }
  }
  md.inline.ruler.after('escape', 'math_inline', mathInline)
  md.block.ruler.after('blockquote', 'math_block', mathBlock, {
    alt: ['paragraph', 'reference', 'blockquote', 'list'],
  })
  md.renderer.rules.math_inline = (tokens, idx) => render(tokens[idx].content, false)
  md.renderer.rules.math_block = (tokens, idx) => render(tokens[idx].content, true) + '\n'
}
