import type { HealthResponse } from "@tc/api-client";
import { StatePanel, VIEW_STATE, type AsyncResource } from "@tc/ui";

export interface ApiHealthProps {
  resource: AsyncResource<HealthResponse>;
  onRetry: () => void;
}

export function ApiHealthIndicator({ resource, onRetry }: ApiHealthProps) {
  if (resource.status === VIEW_STATE.READY) {
    return (
      <span className="api-indicator api-indicator--ready">
        <span className="api-indicator__dot" aria-hidden="true" />
        API v{resource.data.version}
      </span>
    );
  }
  if (resource.status === VIEW_STATE.ERROR) {
    return (
      <button className="api-indicator api-indicator--error" type="button" onClick={onRetry}>
        <span className="api-indicator__dot" aria-hidden="true" />
        API 재연결
      </button>
    );
  }
  return (
    <span className="api-indicator api-indicator--loading">
      <span className="api-indicator__dot" aria-hidden="true" />
      API 확인 중
    </span>
  );
}


export function ApiHealthPanel({ resource, onRetry }: ApiHealthProps) {
  if (resource.status === VIEW_STATE.READY) {
    return (
      <section className="health-card" aria-label="API 연결 상태">
        <div>
          <p className="health-card__label">API CONNECTION</p>
          <h2>개발 환경이 연결되었습니다</h2>
        </div>
        <dl>
          <div>
            <dt>Service</dt>
            <dd>{resource.data.service}</dd>
          </div>
          <div>
            <dt>Version</dt>
            <dd>{resource.data.version}</dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{resource.data.status}</dd>
          </div>
        </dl>
      </section>
    );
  }
  if (resource.status === VIEW_STATE.ERROR) {
    return (
      <StatePanel
        compact
        state={VIEW_STATE.ERROR}
        title="API 연결을 확인해 주세요"
        description={resource.message}
        action={
          <button className="button button--secondary" type="button" onClick={onRetry}>
            다시 확인
          </button>
        }
      />
    );
  }
  if (resource.status === VIEW_STATE.EMPTY) {
    return (
      <StatePanel
        compact
        state={VIEW_STATE.EMPTY}
        title="API 정보가 비어 있습니다"
        description="서버가 상태 정보를 반환하지 않았습니다."
      />
    );
  }
  return (
    <StatePanel
      compact
      state={VIEW_STATE.LOADING}
      title="API 연결 확인 중"
      description="FastAPI 개발 서버의 health endpoint를 확인하고 있습니다."
    />
  );
}
