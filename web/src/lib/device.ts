/**
 * Whether this is an iPhone, for the copy that names one. iPadOS reports a Mac,
 * so an iPad reads false, and keeps System › Reach as every other device does.
 */
export function onIPhone(): boolean {
  return /\biPhone\b/.test(navigator.userAgent);
}
