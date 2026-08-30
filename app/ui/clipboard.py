"""클립보드 복사 한 가지만 하는 모듈.

`navigator.clipboard` 는 **보안 컨텍스트(HTTPS 또는 localhost)에서만** 동작합니다.
이 앱의 기본 설정은 `host = "0.0.0.0"` 이라, 같은 망의 다른 PC 가 `http://<ip>:8000`
으로 접속하면 그 API 가 없습니다. 그대로 두면 복사 버튼이 아무 일도 하지 않으면서
성공한 것처럼 보입니다.

그래서 예전 방식(`execCommand('copy')`) 을 뒤에 둡니다. 사장된 API 지만 아직 모든
브라우저에서 동작하고, 안 되는 경우를 조용히 넘기는 것보다 낫습니다.
"""

import json

from nicegui import ui


def copy_to_clipboard(text: str) -> None:
    """`text` 를 클립보드에 넣습니다 (비보안 컨텍스트 대비 폴백 포함)."""
    payload = json.dumps(text or "")
    ui.run_javascript(
        f"""
        (async () => {{
            const text = {payload};
            try {{
                if (navigator.clipboard && window.isSecureContext) {{
                    await navigator.clipboard.writeText(text);
                    return;
                }}
            }} catch (e) {{
                // 아래 폴백으로 넘어갑니다.
            }}
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.setAttribute('readonly', '');
            ta.style.position = 'fixed';
            ta.style.top = '-1000px';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            try {{ document.execCommand('copy'); }} finally {{ ta.remove(); }}
        }})();
        """
    )
