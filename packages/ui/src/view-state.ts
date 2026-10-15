export const VIEW_STATE = {
  EMPTY: "empty",
  ERROR: "error",
  LOADING: "loading",
  READY: "ready",
} as const;

export type ViewState = (typeof VIEW_STATE)[keyof typeof VIEW_STATE];
export type PendingViewState = Exclude<ViewState, typeof VIEW_STATE.READY>;

export type AsyncResource<T> =
  | { status: typeof VIEW_STATE.LOADING }
  | { status: typeof VIEW_STATE.ERROR; message: string }
  | { status: typeof VIEW_STATE.EMPTY }
  | { status: typeof VIEW_STATE.READY; data: T };

