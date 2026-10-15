import {
  ApiClientError,
  createApiClient,
  type CycleApiErrorResponse,
} from "@tc/api-client";

export type CycleApiClient = ReturnType<typeof createApiClient>;
export type CycleTerminalStepStatus = "PASS" | "FAIL" | "BLOCKED";
export type DerivedCycleExecutionStatus = "UNEXECUTED" | CycleTerminalStepStatus;

export function deriveStepExecutionStatus(
  statuses: Array<CycleTerminalStepStatus | undefined>,
): DerivedCycleExecutionStatus {
  if (statuses.some((status) => status === undefined)) {
    return "UNEXECUTED";
  }
  if (statuses.includes("FAIL")) return "FAIL";
  if (statuses.includes("BLOCKED")) return "BLOCKED";
  return "PASS";
}

export type CycleServiceErrorCode = CycleApiErrorResponse["code"] | "API_UNAVAILABLE" | "FALLBACK_ACTIVE" | "UNKNOWN";

export class CycleServiceError extends Error {
  constructor(
    readonly code: CycleServiceErrorCode,
    message: string,
    readonly details: Record<string, unknown> = {},
    readonly status?: number,
  ) {
    super(message);
    this.name = "CycleServiceError";
  }
}

export function isCycleMockFallbackEnabled(
  mode = import.meta.env.MODE,
  flag = import.meta.env.VITE_ENABLE_CYCLE_MOCK_FALLBACK,
) {
  return mode === "development" && flag === "true";
}

export const cycleMockFallbackEnabled = isCycleMockFallbackEnabled();

export async function loadCycleDetailWithPartialProgress<
  Cycle extends { status: "PLANNED" | "IN_PROGRESS" | "COMPLETED"; deletedAt?: string | null },
  ItemPage,
  Progress,
>(requests: {
  cycle: () => Promise<Cycle>;
  items: () => Promise<ItemPage>;
  progress: () => Promise<Progress>;
}) {
  const [cycle, items] = await Promise.all([requests.cycle(), requests.items()]);
  if (cycle.status === "PLANNED" || cycle.deletedAt) {
    return { cycle, items, progress: undefined, partialFailure: undefined };
  }
  try {
    const progress = await requests.progress();
    return { cycle, items, progress, partialFailure: undefined };
  } catch (error) {
    return {
      cycle,
      items,
      progress: undefined,
      partialFailure: `진행률 일부를 불러오지 못했습니다: ${error instanceof Error ? error.message : "unknown"}`,
    };
  }
}

/**
 * Thin, typed boundary around the generated client. Availability failures only
 * latch when an explicitly enabled development fixture is allowed; production
 * callers remain in an error/retry state and never receive demo data.
 */
export class CycleService {
  private fallback = false;

  constructor(
    readonly api: CycleApiClient,
    private readonly allowMockFallback = false,
  ) {}

  get fallbackActive() {
    return this.fallback;
  }

  resetFallback() {
    this.fallback = false;
  }

  async call<Result>(operation: (api: CycleApiClient) => Promise<Result>): Promise<Result> {
    if (this.fallback) {
      throw new CycleServiceError("FALLBACK_ACTIVE", "Cycle API 장애로 이 세션은 mock snapshot을 유지합니다.");
    }
    try {
      return await operation(this.api);
    } catch (error) {
      if (isAvailabilityError(error)) {
        if (this.allowMockFallback) {
          this.fallback = true;
          throw new CycleServiceError(
            "API_UNAVAILABLE",
            "Cycle API에 연결할 수 없어 개발용 mock snapshot으로 전환했습니다.",
          );
        }
        throw new CycleServiceError(
          "API_UNAVAILABLE",
          "Cycle API에 연결할 수 없습니다. 운영 모드에서는 가짜 데이터를 표시하지 않습니다.",
        );
      }
      if (error instanceof ApiClientError) {
        const response = error.response as CycleApiErrorResponse | undefined;
        throw new CycleServiceError(
          response?.code ?? "UNKNOWN",
          response?.message ?? error.message,
          response?.details ?? {},
          error.status,
        );
      }
      throw new CycleServiceError("UNKNOWN", error instanceof Error ? error.message : "알 수 없는 Cycle API 오류입니다.");
    }
  }
}

let defaultService: CycleService | undefined;

export function getDefaultCycleService() {
  if (!defaultService) {
    const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "";
    defaultService = new CycleService(createApiClient(baseUrl), cycleMockFallbackEnabled);
  }
  return defaultService;
}

export function cycleMutationOptions(operation: string) {
  const nonce = globalThis.crypto?.randomUUID?.()
    ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return {
    actorLabel: "Cycle Web UI",
    idempotencyKey: `${operation}-${nonce}`,
  };
}

export function isCycleAvailabilityError(error: unknown) {
  return error instanceof CycleServiceError
    && (error.code === "API_UNAVAILABLE" || error.code === "FALLBACK_ACTIVE");
}

export function isCycleConflict(error: unknown) {
  if (!(error instanceof CycleServiceError)) return false;
  return error.code === "VERSION_CONFLICT"
    || error.code === "IDEMPOTENCY_CONFLICT"
    || error.code === "SNAPSHOT_PIN_CONFLICT"
    || error.code === "EXECUTION_ATTEMPT_SEQUENCE_CONFLICT"
    || error.code === "EXECUTION_ATTEMPT_ALREADY_ACTIVE";
}

function isAvailabilityError(error: unknown) {
  return error instanceof TypeError
    || (error instanceof ApiClientError && (error.status >= 500 || error.status === 0));
}
