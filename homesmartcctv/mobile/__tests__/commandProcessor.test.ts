import { CommandFailure, CommandProcessor, type Executors, type ProcessedCommand } from '../src/commands/commandProcessor';
import type { CommandType, DeviceCommand } from '../src/types';

const NOW = Date.parse('2026-10-17T10:00:00Z');

function command(id: string, type: CommandType = 'REFRESH_STATUS', expiresInMs = 60000): DeviceCommand {
  return {
    id,
    camera_device_id: 'cam-1',
    command_type: type,
    parameters: null,
    status: 'DELIVERED',
    idempotency_key: `key-${id}`,
    source: 'touchlessx',
    result: null,
    error_code: null,
    created_at: new Date(NOW).toISOString(),
    expires_at: new Date(NOW + expiresInMs).toISOString(),
    delivered_at: null,
    acknowledged_at: null,
    completed_at: null,
  };
}

function setup(overrides: Partial<Executors> = {}, api: { failAck?: Set<string>; failReports?: number } = {}) {
  const calls: string[] = [];
  const reports: Array<{ id: string; status: string; error_code?: string; result?: unknown }> = [];
  const processed: ProcessedCommand[] = [];
  let failReports = api.failReports ?? 0;
  const executors: Executors = {
    REFRESH_STATUS: async (c) => {
      calls.push(`run ${c.id}`);
      return { battery_level: 80 };
    },
    SWITCH_CAMERA: async (c) => {
      calls.push(`run ${c.id}`);
      return { facing: 'front' };
    },
    CAPTURE_SNAPSHOT: async (c) => {
      calls.push(`run ${c.id}`);
      return { event_id: 'e1' };
    },
    ...overrides,
  };
  const processor = new CommandProcessor(
    {
      acknowledge: async (id) => {
        calls.push(`ack ${id}`);
        if (api.failAck?.has(id)) throw new Error('409');
      },
      report: async (id, report) => {
        if (failReports > 0) {
          failReports -= 1;
          throw new Error('network');
        }
        reports.push({ id, ...report });
      },
    },
    executors,
    (p) => processed.push(p),
    () => NOW,
    async () => {},
  );
  return { processor, calls, reports, processed };
}

test('acknowledges, runs and reports a command', async () => {
  const { processor, calls, reports, processed } = setup();
  processor.submit([command('a', 'CAPTURE_SNAPSHOT')]);
  await processor.idle();
  expect(calls).toEqual(['ack a', 'run a']);
  expect(reports).toEqual([{ id: 'a', status: 'SUCCEEDED', result: { event_id: 'e1' } }]);
  expect(processed[0].outcome).toBe('succeeded');
});

test('a command delivered twice runs once', async () => {
  const { processor, calls } = setup();
  processor.submit([command('a')]);
  processor.submit([command('a'), command('a')]);
  await processor.idle();
  expect(calls.filter((c) => c === 'run a')).toHaveLength(1);
});

test('commands run one at a time, in order', async () => {
  const order: string[] = [];
  let release: () => void = () => {};
  const slow = new Promise<void>((resolve) => {
    release = resolve;
  });
  const { processor } = setup({
    SWITCH_CAMERA: async (c) => {
      order.push(`start ${c.id}`);
      await slow;
      order.push(`end ${c.id}`);
      return {};
    },
    REFRESH_STATUS: async (c) => {
      order.push(`start ${c.id}`);
      return {};
    },
  });
  processor.submit([command('flip', 'SWITCH_CAMERA'), command('status')]);
  await new Promise<void>((r) => setTimeout(() => r(), 10));
  expect(order).toEqual(['start flip']);
  release();
  await processor.idle();
  expect(order).toEqual(['start flip', 'end flip', 'start status']);
});

test('an expired command is not started', async () => {
  const { processor, calls, processed } = setup();
  processor.submit([command('old', 'CAPTURE_SNAPSHOT', -1)]);
  await processor.idle();
  expect(calls).toEqual([]);
  expect(processed[0]).toMatchObject({ outcome: 'skipped', errorCode: 'expired' });
});

test('a refused acknowledgement means the camera does not act', async () => {
  const { processor, calls, reports } = setup({}, { failAck: new Set(['cancelled']) });
  processor.submit([command('cancelled', 'CAPTURE_SNAPSHOT')]);
  await processor.idle();
  expect(calls).toEqual(['ack cancelled']);
  expect(reports).toEqual([]);
});

test('a failure is reported with its code', async () => {
  const { processor, reports } = setup({
    CAPTURE_SNAPSHOT: async () => {
      throw new CommandFailure('camera_not_streaming');
    },
  });
  processor.submit([command('a', 'CAPTURE_SNAPSHOT')]);
  await processor.idle();
  expect(reports).toEqual([{ id: 'a', status: 'FAILED', error_code: 'camera_not_streaming' }]);
});

test('an unexpected error becomes a generic code, never the error text', async () => {
  const { processor, reports } = setup({
    REFRESH_STATUS: async () => {
      throw new Error('internal details that must not leave the phone');
    },
  });
  processor.submit([command('a')]);
  await processor.idle();
  expect(reports[0]).toEqual({ id: 'a', status: 'FAILED', error_code: 'execution_error' });
});

test('the result report is retried when the network blips', async () => {
  const { processor, reports } = setup({}, { failReports: 2 });
  processor.submit([command('a')]);
  await processor.idle();
  expect(reports).toHaveLength(1);
});
