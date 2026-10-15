import type { CasePersistenceState } from "./test-management-demo";

const statusCopy = {
  LOADING: "테스트케이스를 불러오는 중입니다.",
  SAVING: "변경사항을 저장하는 중입니다.",
  ERROR: "테스트케이스 요청을 완료하지 못했습니다.",
  CONFLICT: "다른 사용자가 먼저 저장했습니다. 최신 버전을 다시 불러온 뒤 변경사항을 확인하세요.",
  PARTIAL: "현재 버전은 불러왔지만 일부 이력 정보를 표시하지 못했습니다.",
} as const;

export function CasePersistenceBanner({
  persistence,
  onRetry,
}: {
  persistence: CasePersistenceState;
  onRetry?: () => void;
}) {
  if (persistence.status === "READY") return null;

  const isBusy = persistence.status === "LOADING" || persistence.status === "SAVING";
  const message = persistence.message ?? statusCopy[persistence.status];

  return (
    <div
      className={`tm-persistence-banner tm-persistence-banner--${persistence.status.toLowerCase()}`}
      role={persistence.status === "ERROR" || persistence.status === "CONFLICT" ? "alert" : "status"}
      aria-live="polite"
    >
      <span className="tm-persistence-banner__indicator" aria-hidden="true">{isBusy ? "◌" : "!"}</span>
      <div>
        <strong>{message}</strong>
        <small>PostgreSQL API-backed state</small>
      </div>
      {onRetry && !isBusy ? <button type="button" onClick={onRetry}>다시 시도</button> : null}
    </div>
  );
}
