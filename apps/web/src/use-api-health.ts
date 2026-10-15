import { createApiClient, type HealthResponse } from "@tc/api-client";
import { VIEW_STATE, type AsyncResource } from "@tc/ui";
import { useCallback, useEffect, useState } from "react";

const api = createApiClient(import.meta.env.VITE_API_BASE_URL ?? "");

export function useApiHealth() {
  const [requestVersion, setRequestVersion] = useState(0);
  const [resource, setResource] = useState<AsyncResource<HealthResponse>>({
    status: VIEW_STATE.LOADING,
  });
  const retry = useCallback(() => setRequestVersion((version) => version + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setResource({ status: VIEW_STATE.LOADING });
    api
      .getHealth(controller.signal)
      .then((data) => setResource({ status: VIEW_STATE.READY, data }))
      .catch(() => {
        if (!controller.signal.aborted) {
          setResource({
            status: VIEW_STATE.ERROR,
            message: "API 응답을 확인할 수 없습니다.",
          });
        }
      });
    return () => controller.abort();
  }, [requestVersion]);

  return { resource, retry };
}
