/** Play base64-encoded WAV audio (TTS), swallowing autoplay-policy rejections. */
export function playWavBase64(base64: string): void {
  void new Audio(`data:audio/wav;base64,${base64}`).play().catch(() => {});
}
