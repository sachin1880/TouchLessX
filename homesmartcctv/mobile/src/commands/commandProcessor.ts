import type { CommandType, DeviceCommand } from '../types';

/**
 * Carries out device commands on the camera phone, one at a time.
 *
 * Kept free of React Native so it can be unit-tested: the API calls and the camera
 * operations are passed in. Rules:
 *  - Commands run strictly one after another - two camera operations (a flip and a
 *    snapshot) never overlap.
 *  - The same command can arrive twice (pushed over the control socket and found by
 *    polling); it runs once.
 *  - A command past its expiry is not started - the backend has already marked it
 *    EXPIRED, and the user has been told it didn't happen.
 *  - The camera acknowledges before acting; if the backend refuses the ack (cancelled,
 *    expired), the command is dropped without acting.
 *  - Every accepted command ends with a result report - SUCCEEDED, or FAILED with a
 *    short error code - retried a few times if the network blips.
 */

export class CommandFailure extends Error {
  constructor(public readonly code: string) {
    super(code);
  }
}

export type CommandResult = Record<string, unknown>;
export type Executors = Record<CommandType, (command: DeviceCommand) => Promise<CommandResult>>;

export interface CommandApi {
  acknowledge(commandId: string): Promise<unknown>;
  report(
    commandId: string,
    report: { status: 'SUCCEEDED' | 'FAILED'; error_code?: string; result?: CommandResult },
  ): Promise<unknown>;
}

export interface ProcessedCommand {
  id: string;
  type: CommandType;
  outcome: 'succeeded' | 'failed' | 'skipped';
  errorCode?: string;
  at: number;
}

const EXECUTION_TIMEOUT_MS = 20000;
const REPORT_ATTEMPTS = 3;
const REMEMBERED_IDS = 200;

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new CommandFailure('timeout')), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        clearTimeout(timer);
        reject(error);
      },
    );
  });
}

export class CommandProcessor {
  private queue: DeviceCommand[] = [];
  private seen = new Set<string>();
  private running = false;

  constructor(
    private readonly api: CommandApi,
    private readonly executors: Executors,
    private readonly onProcessed: (processed: ProcessedCommand) => void = () => {},
    private readonly now: () => number = Date.now,
    private readonly sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
  ) {}

  /** Adds commands to the queue (duplicates and already-seen commands are ignored). */
  submit(commands: DeviceCommand[]): void {
    for (const command of commands) {
      if (this.seen.has(command.id)) continue;
      this.seen.add(command.id);
      this.queue.push(command);
    }
    if (this.seen.size > REMEMBERED_IDS) {
      // Forget the oldest ids; anything that old has long expired on the server.
      this.seen = new Set(Array.from(this.seen).slice(-REMEMBERED_IDS / 2));
    }
    this.drain();
  }

  /** Resolves once everything queued so far has been handled (for tests). */
  async idle(): Promise<void> {
    while (this.running || this.queue.length > 0) {
      await this.sleep(5);
    }
  }

  private async drain(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      let command = this.queue.shift();
      while (command) {
        await this.handle(command);
        command = this.queue.shift();
      }
    } finally {
      this.running = false;
    }
  }

  private async handle(command: DeviceCommand): Promise<void> {
    const done = (outcome: ProcessedCommand['outcome'], errorCode?: string) =>
      this.onProcessed({ id: command.id, type: command.command_type, outcome, errorCode, at: this.now() });

    if (Date.parse(command.expires_at) <= this.now()) {
      done('skipped', 'expired');
      return;
    }
    const executor = this.executors[command.command_type];
    try {
      await this.api.acknowledge(command.id);
    } catch {
      // Refused (cancelled / expired / not ours) or unreachable: don't act without an ack.
      done('skipped', 'not_acknowledged');
      return;
    }

    let report: { status: 'SUCCEEDED' | 'FAILED'; error_code?: string; result?: CommandResult };
    if (!executor) {
      report = { status: 'FAILED', error_code: 'unsupported_command' };
    } else {
      try {
        const result = await withTimeout(executor(command), EXECUTION_TIMEOUT_MS);
        report = { status: 'SUCCEEDED', result };
      } catch (error) {
        report = { status: 'FAILED', error_code: error instanceof CommandFailure ? error.code : 'execution_error' };
      }
    }

    for (let attempt = 1; attempt <= REPORT_ATTEMPTS; attempt++) {
      try {
        await this.api.report(command.id, report);
        break;
      } catch {
        if (attempt < REPORT_ATTEMPTS) await this.sleep(1000 * attempt);
      }
    }
    done(report.status === 'SUCCEEDED' ? 'succeeded' : 'failed', report.error_code);
  }
}
