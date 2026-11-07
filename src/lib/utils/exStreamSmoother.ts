/**
 * Reveal streamed text at a steady rate instead of dumping whole chunks.
 *
 * The problem
 * -----------
 * Deltas arrive from the backend in whatever sizes the engine happens to emit them.
 * llama.cpp tends to give small, evenly spaced pieces, which already look smooth. Strata
 * emits much larger bursts separated by real pauses while it works. Rendering each burst
 * the instant it lands makes the text appear in lurches: a wall of words, a gap, another
 * wall. The eye reads the gaps as stalling.
 *
 * The fix
 * -------
 * Keep the real content untouched and animate a *reveal position* across it. Text is the
 * same either way; only the moment it becomes visible changes. This matters for
 * correctness: the revealed layer must never be written back to the message, or the model
 * would receive a truncated transcript on the next turn.
 *
 * Pacing
 * ------
 * A fixed characters-per-second rate would fall ever further behind on a fast burst, so the
 * rate is adaptive: the reveal speeds up as the backlog grows and settles back to the base
 * rate when it has caught up. The result is smooth output that still finishes promptly,
 * with a hard cap on how long it may lag so the transcript never trails badly behind the
 * end of the turn.
 */

/** Tuning. Deliberately conservative: the goal is to hide seams, not to fake typing. */
const BASE_CHARS_PER_SECOND = 240;
const MAX_CHARS_PER_SECOND = 3000;
/** Backlog above which the reveal is considered behind and accelerates. */
const BACKLOG_FOR_MAX_SPEED = 900;
/**
 * Absolute backlog ceiling, in characters, before the reveal gives up and jumps to the end.
 *
 * This is a safety net for absurd cases only, not a normal path. The adaptive rate already
 * reaches MAX_CHARS_PER_SECOND, which drains a 900 character backlog in about 300ms, so
 * ordinary bursts are handled smoothly and never need this. An earlier version derived the
 * ceiling from a lag budget, which worked out to ~170 characters and meant a routine
 * 400 character Strata burst dumped instantly instead of smoothing, defeating the point.
 */
const HARD_CATCHUP_CHARS = 4000;

export type SmootherOptions = {
	charsPerSecond?: number;
	maxCharsPerSecond?: number;
	/** Set false to disable entirely, for example when the user prefers raw output. */
	enabled?: boolean;
	/** Called with the number of characters to display. */
	onTick: (revealed: number) => void;
};

export class StreamSmoother {
	private target = 0;
	private revealed = 0;
	private carry = 0;
	private lastFrame = 0;
	private frame: number | null = null;
	private readonly options: Required<Omit<SmootherOptions, 'onTick'>> & { onTick: (n: number) => void };

	constructor(options: SmootherOptions) {
		this.options = {
			charsPerSecond: options.charsPerSecond ?? BASE_CHARS_PER_SECOND,
			maxCharsPerSecond: options.maxCharsPerSecond ?? MAX_CHARS_PER_SECOND,
			enabled: options.enabled ?? true,
			onTick: options.onTick
		};
	}

	/** Full length of the content that exists so far. */
	setTarget(length: number) {
		this.target = Math.max(0, length);
		// A shorter target means a new message or a rewrite; never reveal past the end.
		if (this.revealed > this.target) {
			this.revealed = this.target;
			this.options.onTick(this.revealed);
		}
		this.start();
	}

	/** Jump straight to the end. Called when the turn completes. */
	finish() {
		this.stop();
		this.revealed = this.target;
		this.options.onTick(this.revealed);
	}

	/** Current revealed length, for a consumer that needs it synchronously. */
	get value() {
		return this.revealed;
	}

	private start() {
		if (!this.options.enabled) {
			// Disabled: reveal everything immediately and never schedule a frame.
			this.revealed = this.target;
			this.options.onTick(this.revealed);
			return;
		}
		if (this.frame !== null) return;
		this.lastFrame = 0;
		this.frame = requestAnimationFrame(this.tick);
	}

	private stop() {
		if (this.frame !== null) {
			cancelAnimationFrame(this.frame);
			this.frame = null;
		}
	}

	private tick = (now: number) => {
		this.frame = null;

		const backlog = this.target - this.revealed;
		if (backlog <= 0) {
			this.lastFrame = 0;
			this.carry = 0;
			return;
		}

		// First frame after a pause: measure from now, do not bill the idle time.
		if (this.lastFrame === 0) this.lastFrame = now;
		const elapsed = Math.min(now - this.lastFrame, 100); // clamp, so a long stall cannot dump everything
		this.lastFrame = now;

		// Hard catch-up, for absurd cases only. See HARD_CATCHUP_CHARS.
		if (backlog > HARD_CATCHUP_CHARS) {
			this.revealed = this.target;
			this.carry = 0;
			this.lastFrame = 0;
			this.options.onTick(this.revealed);
			return;
		}

		// Adaptive rate. Because a chunk arrives as a step, the backlog is the honest signal
		// for how far behind the reveal is, so it drives the speed directly.
		const pressure = Math.min(1, backlog / BACKLOG_FOR_MAX_SPEED);
		const rate =
			this.options.charsPerSecond +
			(this.options.maxCharsPerSecond - this.options.charsPerSecond) * pressure;

		this.carry += (rate * elapsed) / 1000;

		// Consume whole characters from the carry, then give back whatever was not used so it
		// is not lost between frames.
		const take = Math.min(Math.floor(this.carry), backlog);
		if (take > 0) {
			this.revealed += take;
			this.carry -= take;
		}

		this.options.onTick(this.revealed);
		this.frame = requestAnimationFrame(this.tick);
	};

	destroy() {
		this.stop();
	}
}

/**
 * Convenience for a component: returns the substring that should be displayed.
 * Never returns longer than the content, and returns the whole thing once done.
 */
export function revealedText(content: string, revealed: number, done: boolean): string {
	if (done) return content ?? '';
	if (!content) return '';
	return content.slice(0, Math.min(revealed, content.length));
}
