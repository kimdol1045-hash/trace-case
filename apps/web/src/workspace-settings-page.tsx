import { PageHeader, StatePanel, VIEW_STATE, type AsyncResource } from "@tc/ui";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { AuditHistory } from "./audit-history";
import {
  PRIMARY_WORKSPACE_ID,
  type AuditPageResponse,
  type UpdateWorkspaceSettingsRequest,
  type WorkspaceService,
  type WorkspaceSettings,
} from "./workspace-service";

export interface WorkspaceSettingsPageProps {
  service: WorkspaceService;
}

export function WorkspaceSettingsPage({ service }: WorkspaceSettingsPageProps) {
  const [settings, setSettings] = useState<AsyncResource<WorkspaceSettings>>({
    status: VIEW_STATE.LOADING,
  });
  const [audit, setAudit] = useState<AsyncResource<AuditPageResponse>>({
    status: VIEW_STATE.LOADING,
  });
  const [saveState, setSaveState] = useState<
    { status: "idle" | "saving" | "success" | "error"; message?: string }
  >({ status: "idle" });

  const loadSettings = useCallback(async () => {
    setSettings({ status: VIEW_STATE.LOADING });
    try {
      const response = await service.getWorkspaceSettings(PRIMARY_WORKSPACE_ID);
      setSettings(response.teamName ? { status: VIEW_STATE.READY, data: response } : { status: VIEW_STATE.EMPTY });
    } catch {
      setSettings({ status: VIEW_STATE.ERROR, message: "워크스페이스 설정을 불러오지 못했습니다." });
    }
  }, [service]);

  const loadAudit = useCallback(async () => {
    setAudit({ status: VIEW_STATE.LOADING });
    try {
      const response = await service.getAuditHistory(PRIMARY_WORKSPACE_ID, { limit: 12 });
      setAudit(
        response.items.length
          ? { status: VIEW_STATE.READY, data: response }
          : { status: VIEW_STATE.EMPTY },
      );
    } catch {
      setAudit({ status: VIEW_STATE.ERROR, message: "변경 이력을 불러오지 못했습니다." });
    }
  }, [service]);

  useEffect(() => {
    void loadSettings();
    void loadAudit();
  }, [loadAudit, loadSettings]);

  const updateSettings = async (request: UpdateWorkspaceSettingsRequest) => {
    setSaveState({ status: "saving", message: "설정을 저장하고 있습니다." });
    try {
      const updated = await service.updateWorkspaceSettings(PRIMARY_WORKSPACE_ID, request);
      setSettings({ status: VIEW_STATE.READY, data: updated });
      setSaveState({ status: "success", message: "워크스페이스 설정을 저장했습니다." });
      await loadAudit();
    } catch (error) {
      setSaveState({
        status: "error",
        message: error instanceof Error ? error.message : "설정을 저장하지 못했습니다.",
      });
    }
  };

  return (
    <div className="page-stack settings-page">
      <PageHeader
        eyebrow="WORKSPACE · SETTINGS"
        title="Workspace settings"
        description="팀 식별자와 공개 키 prefix, 테스트의 기본 실행 환경을 한곳에서 관리합니다."
        action={
          settings.status === VIEW_STATE.READY ? (
            <span className="contract-badge">VERSION {settings.data.version}</span>
          ) : null
        }
      />

      {settings.status === VIEW_STATE.LOADING ? (
        <StatePanel
          state={VIEW_STATE.LOADING}
          title="워크스페이스 설정을 불러오는 중"
          description="팀 이름, prefix와 기본 환경을 확인하고 있습니다."
        />
      ) : null}
      {settings.status === VIEW_STATE.ERROR ? (
        <StatePanel
          state={VIEW_STATE.ERROR}
          title="설정을 불러오지 못했습니다"
          description={settings.message}
          action={
            <button className="button button--secondary" type="button" onClick={() => void loadSettings()}>
              다시 시도
            </button>
          }
        />
      ) : null}
      {settings.status === VIEW_STATE.EMPTY ? (
        <StatePanel
          state={VIEW_STATE.EMPTY}
          title="워크스페이스 설정이 비어 있습니다"
          description="워크스페이스 초기화를 확인한 뒤 다시 시도해 주세요."
          action={
            <button className="button button--secondary" type="button" onClick={() => void loadSettings()}>
              다시 확인
            </button>
          }
        />
      ) : null}
      {settings.status === VIEW_STATE.READY ? (
        <WorkspaceSettingsForm
          key={settings.data.version}
          settings={settings.data}
          disabled={saveState.status === "saving"}
          onSubmit={(request) => void updateSettings(request)}
        />
      ) : null}

      {saveState.status !== "idle" ? (
        <p
          className={`operation-status operation-status--${saveState.status}`}
          role={saveState.status === "error" ? "alert" : "status"}
        >
          {saveState.message}
        </p>
      ) : null}

      <AuditHistory resource={audit} onRetry={() => void loadAudit()} title="워크스페이스 감사 이력" />
    </div>
  );
}

export interface WorkspaceSettingsFormProps {
  settings: WorkspaceSettings;
  disabled?: boolean;
  onSubmit: (request: UpdateWorkspaceSettingsRequest) => void;
}

export function WorkspaceSettingsForm({
  settings,
  disabled = false,
  onSubmit,
}: WorkspaceSettingsFormProps) {
  const [teamName, setTeamName] = useState(settings.teamName);
  const [casePrefix, setCasePrefix] = useState(settings.casePrefix);
  const [issuePrefix, setIssuePrefix] = useState(settings.issuePrefix);
  const [defaultEnvironment, setDefaultEnvironment] = useState(settings.defaultEnvironment);
  const [generationQuietMinutes, setGenerationQuietMinutes] = useState(
    settings.generationQuietMinutes,
  );

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit({
      baseVersion: settings.version,
      teamName: teamName.trim(),
      casePrefix: casePrefix.trim().toUpperCase(),
      issuePrefix: issuePrefix.trim().toUpperCase(),
      defaultEnvironment: defaultEnvironment.trim(),
      generationQuietMinutes,
    });
  };

  return (
    <form className="settings-form" data-mobile-layout="single-column" onSubmit={handleSubmit}>
      <section className="settings-form__section" aria-labelledby="team-settings-heading">
        <div className="settings-form__heading">
          <p className="panel-heading__eyebrow">IDENTITY</p>
          <h2 id="team-settings-heading">팀과 공개 키</h2>
          <p>이 값은 워크스페이스 전체에서 케이스와 이슈를 식별할 때 사용됩니다.</p>
        </div>
        <div className="settings-form__fields">
          <label>
            <span>팀 이름</span>
            <input
              name="teamName"
              value={teamName}
              onChange={(event) => setTeamName(event.target.value)}
              required
              maxLength={200}
            />
          </label>
          <div className="settings-form__field-grid">
            <label>
              <span>Test case prefix</span>
              <input
                name="casePrefix"
                value={casePrefix}
                onChange={(event) => setCasePrefix(event.target.value.toUpperCase())}
                pattern="[A-Z][A-Z0-9]{1,15}"
                required
                maxLength={16}
              />
              <small>예: {casePrefix || "TC"}-000123</small>
            </label>
            <label>
              <span>Issue prefix</span>
              <input
                name="issuePrefix"
                value={issuePrefix}
                onChange={(event) => setIssuePrefix(event.target.value.toUpperCase())}
                pattern="[A-Z][A-Z0-9]{1,15}"
                required
                maxLength={16}
              />
              <small>예: {issuePrefix || "ISSUE"}-000123</small>
            </label>
          </div>
        </div>
      </section>

      <section className="settings-form__section" aria-labelledby="environment-settings-heading">
        <div className="settings-form__heading">
          <p className="panel-heading__eyebrow">DEFAULTS</p>
          <h2 id="environment-settings-heading">실행과 생성 환경</h2>
          <p>새 테스트 사이클과 AI 생성 작업에 적용할 기본값입니다.</p>
        </div>
        <div className="settings-form__fields">
          <label>
            <span>기본 환경</span>
            <select
              name="defaultEnvironment"
              value={defaultEnvironment}
              onChange={(event) => setDefaultEnvironment(event.target.value)}
            >
              <option value="local">local</option>
              <option value="development">development</option>
              <option value="staging">staging</option>
              <option value="production">production</option>
            </select>
          </label>
          <label>
            <span>AI 생성 quiet time</span>
            <span className="input-with-unit">
              <input
                name="generationQuietMinutes"
                type="number"
                min={0}
                max={1440}
                value={generationQuietMinutes}
                onChange={(event) => setGenerationQuietMinutes(Number(event.target.value))}
              />
              <span>분</span>
            </span>
            <small>마지막 입력 이후 자동 생성을 기다리는 시간입니다.</small>
          </label>
        </div>
      </section>

      <footer className="settings-form__footer">
        <p>
          <span>마지막 변경</span>
          <time dateTime={settings.updatedAt}>{formatUpdatedAt(settings.updatedAt)}</time>
        </p>
        <button className="button button--primary" type="submit" disabled={disabled}>
          {disabled ? "저장 중…" : "설정 저장"}
        </button>
      </footer>
    </form>
  );
}

function formatUpdatedAt(value: string) {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
