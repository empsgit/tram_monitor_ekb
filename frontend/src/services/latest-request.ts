/** Abort old requests AND reject late responses even if transport ignores abort. */
export class LatestRequest {
  private controller: AbortController | null = null;
  private version = 0;

  cancel(): void {
    this.controller?.abort();
    this.controller = null;
    this.version++;
  }

  start(): { signal: AbortSignal; isCurrent: () => boolean } {
    this.cancel();
    const version = this.version;
    this.controller = new AbortController();
    return { signal: this.controller.signal, isCurrent: () => this.version === version };
  }
}
