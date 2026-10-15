/**
 * 화면 전환 의도를 한곳에서 정의한다.
 *
 * 목록에서 생성/상세로 들어갈 때는 이전 화면을 보존하고, 생성 완료나 삭제 완료처럼
 * 되돌아가면 유효하지 않은 화면은 현재 history entry를 대체한다.
 */
export const appNavigationOptions = {
  openCreate: { replace: false, flushSync: true },
  openDetail: { replace: false, flushSync: true },
  openExecution: { replace: false, flushSync: true },
  cancelCreate: { replace: true, flushSync: true },
  createSuccess: { replace: true, flushSync: true },
  deleteSuccess: { replace: true, flushSync: true },
  returnToList: { replace: true, flushSync: true },
  canonicalRedirect: { replace: true, flushSync: true },
} as const;
