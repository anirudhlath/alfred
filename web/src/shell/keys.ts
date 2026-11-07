/** Focus is somewhere a key is text, so no shortcut may take it. */
export function isEditing(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest("input, textarea, select, [contenteditable]") !== null;
}
