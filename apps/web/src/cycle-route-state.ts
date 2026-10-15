export interface KeyedCycleResource<Data, State> {
  key: string;
  data?: Data;
  state: State;
}

export type CycleExecutionStatus = "UNEXECUTED" | "PASS" | "FAIL" | "BLOCKED";
export type CycleAggregateStatus = Exclude<CycleExecutionStatus, "BLOCKED">;

export function aggregateExecutionStatuses(values: CycleExecutionStatus[]): CycleAggregateStatus {
  const executed = values.filter((status) => status !== "UNEXECUTED" && status !== "BLOCKED");
  if (!executed.length) return "UNEXECUTED";
  if (executed.includes("FAIL")) return "FAIL";
  if (executed.every((status) => status === "PASS")) return "PASS";
  return "UNEXECUTED";
}

export function canRetryCyclePersistence(state: { retryable?: boolean }) {
  return state.retryable === true;
}

export function canEditCycleExecution(status: "PLANNED" | "IN_PROGRESS" | "COMPLETED" | undefined) {
  return status === "IN_PROGRESS";
}

export function appendCursorPage<T extends { id: string }>(current: T[], next: T[], total: number) {
  const byId = new Map(current.map((item) => [item.id, item]));
  next.forEach((item) => byId.set(item.id, item));
  const items = [...byId.values()];
  return { items, loaded: items.length, total, complete: items.length >= total };
}

export function resolveKeyedCycleResource<Data, State>(
  key: string | undefined,
  resource: KeyedCycleResource<Data, State> | undefined,
  loadingState: State,
) {
  if (!key || resource?.key !== key) {
    return { data: undefined, state: loadingState };
  }
  return { data: resource.data, state: resource.state };
}
