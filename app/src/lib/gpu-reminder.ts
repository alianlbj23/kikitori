/**
 * Whether to remind the user to download the GPU acceleration component.
 *
 * Role: pure decision behind the startup notice in main.ts. A machine with an
 * NVIDIA card but without NVIDIA's cuBLAS runs speech recognition on the CPU
 * until the user accepts the NVIDIA license and downloads the component on the
 * Models page (kikitori cannot ship it in the installer). The first-run notice
 * mentions this once in passing, which users miss; this reminder comes back
 * on every launch until the component is there or the user says "don't remind
 * me" (`ui.gpu_reminder_dismissed`). Closing it only hides it for this launch.
 */

import type { GpuRuntime } from "./types";

export interface GpuReminderInput {
  /**
   * The engine's `recommendation.gpu_runtime_missing` (an NVIDIA card is there
   * but the component is not); null before any detection or preview arrived.
   */
  runtimeMissing: boolean | null;
  /** `models.list.gpu_runtime`; null while the models list is still loading. */
  runtime: GpuRuntime | null;
  /** The component is downloading right now. */
  downloading: boolean;
  /** The user chose "don't remind me" (saved in settings). */
  dismissed: boolean;
  /** The user closed the notice during this launch. */
  closed: boolean;
}

/** True when the startup reminder should be on screen. */
export function showGpuReminder(input: GpuReminderInput): boolean {
  if (input.runtimeMissing !== true || input.dismissed || input.closed || input.downloading) return false;
  const runtime = input.runtime;
  return runtime === null || (runtime.supported && !runtime.installed);
}
