import { StatePanel, VIEW_STATE } from "@tc/ui";
import { Link } from "react-router-dom";

import { appNavigationOptions } from "./navigation-policy";

export function NotFoundPage() {
  return (
    <div className="page-stack page-stack--centered">
      <StatePanel
        state={VIEW_STATE.ERROR}
        title="페이지를 찾을 수 없습니다"
        description="주소를 확인하거나 Overview에서 다시 시작해 주세요."
        action={
          <Link
            className="button button--secondary"
            replace={appNavigationOptions.canonicalRedirect.replace}
            to="/"
          >
            Overview로 이동
          </Link>
        }
      />
    </div>
  );
}
