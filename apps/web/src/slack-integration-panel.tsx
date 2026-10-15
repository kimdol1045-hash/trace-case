import type {
  SlackAllowlistPage,
  SlackIntegrationStatus,
  SlackJobPage,
} from "@tc/api-client";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  dedupeEventLogs,
  dedupeJobs,
  formatSlackDate,
  isSlackConflict,
  slackApi,
  slackErrorMessage,
  slackMutationKey,
  type SlackEventLog,
} from "./slack-service";

interface IntegrationSnapshot {
  status: SlackIntegrationStatus | null;
  allowlist: SlackAllowlistPage | null;
  events: SlackEventLog[];
  jobs: SlackJobPage["items"];
}

const EMPTY_SNAPSHOT: IntegrationSnapshot = {
  status: null,
  allowlist: null,
  events: [],
  jobs: [],
};

const connectionStatusLabels: Record<SlackIntegrationStatus["connectionStatus"], string> = {
  CONNECTED: "연결됨",
  DEGRADED: "확인 필요",
  DISCONNECTED: "연결 해제됨",
  SETUP_REQUIRED: "설정 필요",
};

const ingressModeLabels: Record<SlackIntegrationStatus["ingressMode"], string> = {
  HTTP_EVENTS: "Events API",
  SOCKET_MODE: "Socket Mode",
};

export function SlackIntegrationPanel() {
  const [snapshot, setSnapshot] = useState<IntegrationSnapshot>(EMPTY_SNAPSHOT);
  const [phase, setPhase] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  const [partial, setPartial] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [revision, setRevision] = useState(0);
  const [channelIds, setChannelIds] = useState<string[]>([]);
  const [channelDraft, setChannelDraft] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setPhase("loading");
    setError("");
    setConflict(false);
    void Promise.allSettled([
      slackApi.getSlackIntegrationStatus(controller.signal),
      slackApi.listSlackAllowlist({ limit: 200 }, controller.signal),
      slackApi.listSlackEvents({ limit: 20 }, controller.signal),
      slackApi.listSlackJobs({ limit: 50 }, controller.signal),
    ]).then(([statusResult, allowlistResult, eventResult, jobResult]) => {
      if (controller.signal.aborted) return;
      if (statusResult.status === "rejected") {
        setPhase("error");
        setError(slackErrorMessage(statusResult.reason, "Slack 상태를 불러오지 못했습니다."));
        return;
      }
      const nextSnapshot: IntegrationSnapshot = {
        status: statusResult.value,
        allowlist: allowlistResult.status === "fulfilled" ? allowlistResult.value : null,
        events: eventResult.status === "fulfilled"
          ? dedupeEventLogs(eventResult.value.items)
          : [],
        jobs: jobResult.status === "fulfilled" ? dedupeJobs(jobResult.value.items) : [],
      };
      setSnapshot(nextSnapshot);
      setChannelIds(
        nextSnapshot.allowlist?.items
          .filter((item) => item.visibility === "PUBLIC" && item.collectionAllowed)
          .map((item) => item.channelId) ?? [],
      );
      setPartial([allowlistResult, eventResult, jobResult].some((part) => part.status === "rejected"));
      setPhase("ready");
    });
    return () => controller.abort();
  }, [revision]);

  const statistics = useMemo(() => ({
    retry: snapshot.jobs.filter((job) => job.state === "RETRY_WAIT").length,
    dead: snapshot.jobs.filter((job) => job.state === "DEAD").length,
    received: snapshot.events.length,
  }), [snapshot.events.length, snapshot.jobs]);

  const addChannel = () => {
    const normalized = channelDraft.trim().toUpperCase();
    if (!/^C[A-Z0-9]{2,}$/.test(normalized)) {
      setError("공개 Slack channel ID는 C로 시작해야 합니다.");
      return;
    }
    setError("");
    setChannelIds((current) => current.includes(normalized) ? current : [...current, normalized]);
    setChannelDraft("");
  };

  const saveAllowlist = async () => {
    if (!snapshot.status) return;
    setSaving(true);
    setError("");
    setConflict(false);
    const idempotencyKey = slackMutationKey("slack-allowlist");
    try {
      const response = await slackApi.updateSlackAllowlist({
        idempotencyKey,
        workspaceId: snapshot.status.workspaceId,
        expectedVersion: snapshot.status.resourceVersion,
        channels: channelIds.map((channelId) => ({ channelId, visibility: "PUBLIC" })),
      }, { idempotencyKey, actorLabel: "Slack Integrations Web UI" });
      setSnapshot((current) => ({
        ...current,
        status: current.status
          ? { ...current.status, allowlistedPublicChannelCount: response.channels.length, resourceVersion: response.resourceVersion, updatedAt: response.updatedAt }
          : null,
        allowlist: { items: response.channels, nextCursor: null },
      }));
    } catch (requestError) {
      setConflict(isSlackConflict(requestError));
      setError(slackErrorMessage(requestError, "Slack allowlist를 저장하지 못했습니다."));
    } finally {
      setSaving(false);
    }
  };

  if (phase === "loading") {
    return <div className="tm-api-state" role="status">Slack 연결과 수집 채널을 불러오는 중…</div>;
  }
  if (phase === "error") {
    return <div className="tm-api-state is-error" role="alert"><strong>Slack 연동 상태를 불러올 수 없습니다.</strong><p>{error}</p><button type="button" onClick={() => setRevision((value) => value + 1)}>다시 시도</button></div>;
  }
  const status = snapshot.status;
  if (!status) return null;
  const connectionTone = status.connectionStatus === "CONNECTED" ? "is-connected" : "is-pending";
  const setupCopy = status.connectionStatus === "SETUP_REQUIRED"
    ? "Slack 앱 인증 정보를 설정한 뒤 요청 수집을 시작할 수 있습니다."
    : status.connectionStatus === "DISCONNECTED"
      ? "Slack 요청 수집이 중지되어 있습니다."
      : status.connectionStatus === "DEGRADED"
        ? "연결은 설정되었지만 최근 상태를 확인하지 못했습니다."
        : "Slack 연결이 정상이며 허용된 공개 채널의 요청을 수집합니다.";

  return <section className="tm-slack-integration" aria-labelledby="slack-integration-title">
    {partial ? <div className="tm-partial-state" role="status">Slack status는 확인했지만 allowlist 또는 event/job 로그 일부가 지연되고 있습니다. <button type="button" onClick={() => setRevision((value) => value + 1)}>다시 불러오기</button></div> : null}
    {conflict ? <div className="tm-conflict-state" role="alert"><strong>Allowlist 변경 충돌</strong><span>최신 resource version을 다시 불러오세요.</span></div> : null}
    {error ? <div className="tm-api-state is-error" role="alert">{error}</div> : null}
    <div className="tm-integration-grid">
      <article className="tm-integration-card tm-slack-card">
        <header><span className="tm-integration-logo is-slack" aria-hidden="true">S</span><div><h2 id="slack-integration-title">Slack 연결</h2><p>공개 채널의 요청과 답글 수집</p></div><span className={`tm-connection-status ${connectionTone}`}>{connectionStatusLabels[status.connectionStatus]}</span></header>
        <p className="tm-integration-guidance">{setupCopy}</p>
        <dl><div><dt>수집 방식</dt><dd>{ingressModeLabels[status.ingressMode]}</dd></div><div><dt>연결 확인</dt><dd>{status.callbackState === "VERIFIED" ? "확인됨" : "확인 필요"}</dd></div><div><dt>워크스페이스</dt><dd>{status.workspaceId}</dd></div><div><dt>최근 수신</dt><dd>{formatSlackDate(status.lastEventAt)}</dd></div><div><dt>수집 채널</dt><dd>{status.allowlistedPublicChannelCount}개</dd></div><div><dt>재시도 / 중단</dt><dd>{statistics.retry} / {statistics.dead}</dd></div></dl>
        <div className="tm-private-warning" role="note"><strong>수집 범위 안내</strong><p>아래에서 허용한 공개 채널만 수집합니다. 비공개 채널은 기본적으로 제외됩니다.</p></div>
        <footer><Link className="tm-subtle-button" to="/slack-work-items">수집된 요청 보기</Link><button className="tm-subtle-button" type="button" onClick={() => setRevision((value) => value + 1)}>상태 새로고침</button></footer>
      </article>
      <article className="tm-integration-card tm-slack-allowlist-card">
        <header><span className="tm-integration-logo is-api" aria-hidden="true">#</span><div><h2>수집할 공개 채널</h2><p>등록한 채널만 Slack 요청으로 수집</p></div><span className="tm-connection-status is-connected">{channelIds.length}개 허용</span></header>
        <div className="tm-slack-channel-form"><label htmlFor="slack-channel-id">Slack 채널 ID</label><div><input id="slack-channel-id" value={channelDraft} onChange={(event) => setChannelDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addChannel(); } }} placeholder="C012ABCDEF" /><button type="button" onClick={addChannel}>추가</button></div></div>
        <ul className="tm-slack-channel-list" aria-label="허용된 공개 Slack 채널">{channelIds.map((channelId) => <li key={channelId}><code>{channelId}</code><span>공개 채널 수집</span><button type="button" aria-label={`${channelId} 제거`} onClick={() => setChannelIds((current) => current.filter((candidate) => candidate !== channelId))}>제거</button></li>)}</ul>
        {channelIds.length === 0 ? <p className="tm-empty-inline">허용된 public channel이 없습니다.</p> : null}
        <footer><button disabled={saving || status.connectionStatus === "DISCONNECTED"} type="button" onClick={() => void saveAllowlist()}>{saving ? "저장 중…" : "수집 채널 저장"}</button></footer>
      </article>
    </div>
    <div className="tm-slack-log-grid">
      <section className="tm-dashboard-widget"><header><div><h2>최근 수집 활동</h2><p>같은 이벤트는 한 번만 반영 · {statistics.received}건</p></div></header><div className="tm-slack-log-list">{snapshot.events.slice(0, 8).map((event) => <article key={event.id}><span className={`tm-ledger-state is-${event.processingState.toLowerCase()}`}>{event.processingState === "PROCESSED" ? "반영됨" : event.processingState.replaceAll("_", " ")}</span><div><strong>{event.normalizedEvent.action.replaceAll("_", " ")} · {event.channelId}</strong><small>{event.ingressSource === "SOCKET_MODE" ? "Socket Mode" : "Events API"}</small></div><time>{formatSlackDate(event.receivedAt)}</time></article>)}{snapshot.events.length === 0 ? <p className="tm-empty-inline">아직 수신된 Slack 활동이 없습니다.</p> : null}</div></section>
      <section className="tm-dashboard-widget"><header><div><h2>확인이 필요한 작업</h2><p>재시도 중이거나 중단된 동기화 작업</p></div></header><div className="tm-slack-log-list">{snapshot.jobs.filter((job) => job.state === "RETRY_WAIT" || job.state === "DEAD").slice(0, 8).map((job) => <article key={job.id}><span className={`tm-ledger-state is-${job.state.toLowerCase()}`}>{job.state === "RETRY_WAIT" ? "재시도 대기" : "중단됨"}</span><div><strong>{job.jobType.replaceAll("_", " ")}</strong><small>시도 {job.attempt}/{job.maxAttempts}</small></div><time>{formatSlackDate(job.updatedAt)}</time></article>)}{statistics.retry + statistics.dead === 0 ? <p className="tm-empty-inline">확인이 필요한 작업이 없습니다.</p> : null}</div></section>
    </div>
  </section>;
}
