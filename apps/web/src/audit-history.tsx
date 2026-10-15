import { StatePanel, VIEW_STATE, type AsyncResource } from "@tc/ui";

import type { AuditPageResponse } from "./workspace-service";

export interface AuditHistoryProps {
  resource: AsyncResource<AuditPageResponse>;
  onRetry: () => void;
  title?: string;
}

const actionLabels: Record<string, string> = {
  CREATE: "폴더 생성",
  MOVE_SUBTREE: "하위 트리 이동",
  RENAME: "이름 변경",
  RESTORE_SUBTREE: "하위 트리 복원",
  UPDATE: "설정 변경",
  SOFT_DELETE_SUBTREE: "하위 트리 삭제",
  SYSTEM_FOLDER_CREATED: "시스템 폴더 생성",
};

export function AuditHistory({ resource, onRetry, title = "최근 변경 이력" }: AuditHistoryProps) {
  if (resource.status === VIEW_STATE.LOADING) {
    return (
      <StatePanel
        compact
        state={VIEW_STATE.LOADING}
        title="감사 이력을 불러오는 중"
        description="append-only 변경 기록을 확인하고 있습니다."
      />
    );
  }
  if (resource.status === VIEW_STATE.ERROR) {
    return (
      <StatePanel
        compact
        state={VIEW_STATE.ERROR}
        title="감사 이력을 불러오지 못했습니다"
        description={resource.message}
        action={
          <button className="button button--secondary" type="button" onClick={onRetry}>
            다시 시도
          </button>
        }
      />
    );
  }
  if (resource.status === VIEW_STATE.EMPTY || resource.data.items.length === 0) {
    return (
      <StatePanel
        compact
        state={VIEW_STATE.EMPTY}
        title="아직 변경 이력이 없습니다"
        description="폴더나 워크스페이스 설정을 변경하면 이곳에 기록됩니다."
      />
    );
  }
  return (
    <section className="audit-panel" aria-labelledby="audit-heading">
      <div className="panel-heading">
        <div>
          <p className="panel-heading__eyebrow">AUDIT · APPEND ONLY</p>
          <h2 id="audit-heading">{title}</h2>
        </div>
        <span className="count-badge">{resource.data.total}</span>
      </div>
      <ol className="audit-list">
        {resource.data.items.map((entry) => (
          <li key={entry.id}>
            <span className={`audit-list__source audit-list__source--${entry.source.type.toLowerCase()}`}>
              {entry.source.type}
            </span>
            <div>
              <strong>{actionLabels[entry.action] ?? entry.action}</strong>
              <p>
                {entry.source.actorLabel ?? "System"} · {entry.entityType.toLowerCase()}
              </p>
              {entry.reason ? <p className="audit-list__reason">{entry.reason}</p> : null}
            </div>
            <time dateTime={entry.occurredAt}>{formatTime(entry.occurredAt)}</time>
          </li>
        ))}
      </ol>
    </section>
  );
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
