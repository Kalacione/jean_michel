// Shared markdown-it renderer — used by the chat bubbles (ChatPane) and the
// reflection trace (EventTrace). One instance, one set of options.
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

export function renderMarkdown (text) {
  return md.render(text || '')
}
