// Trigger a browser "Save as" for an in-memory Blob (auth-gated downloads can't
// use a bare <a href>, so we fetch a Blob then save it here).
export function saveBlob (blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.append(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
