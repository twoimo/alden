export type LifecycleState = "visible" | "hidden" | "closed" | "locked";

export interface RenderLoopControl {
  start(): void;
  stop(): void;
}

export class RenderLifecycle {
  private active = false;

  constructor(
    private readonly loop: RenderLoopControl,
    private readonly stopTimers: () => void,
    private readonly startTimers: () => void,
  ) {}

  transition(state: LifecycleState): void {
    if (state === "visible") {
      if (this.active) return;
      // `active` only follows a start that actually happened. Set before a
      // throwing `start()`, it would make every later visible signal return
      // early, and nothing would render until the next hide/show cycle.
      try {
        this.loop.start();
      } catch {
        return;
      }
      this.active = true;
      this.safely(this.startTimers);
      return;
    }
    this.active = false;
    // Each half is guarded on its own: a throwing timer cleanup must not skip
    // the loop stop and leave a render loop running behind a hidden window.
    this.safely(this.stopTimers);
    this.safely(() => this.loop.stop());
  }

  private safely(run: () => void): void {
    try {
      run();
    } catch {
      // Render or timer failures must not take the panel process down.
    }
  }
}
