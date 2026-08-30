# 시작하기

> 상위: [MADO 사용 설명서](../README.md) · 다음: [설치와 첫 실행](01-installation.md)

---

## 가장 빠른 길

```bash
pip install -r requirements.txt
python setup_mcp.py            # MCP 서버 준비 (인터넷 필요, 최초 1회)
cp conf.example.json conf.json # 설정 템플릿 복사
cp .env.example .env           # 엔드포인트·키 입력
python -m app.main
```

접속 주소는 기동 로그의 `Web UI: http://...` 줄에 나옵니다.

---

## 미리 알아둘 것

### 엔드포인트가 없으면 토론이 안 됩니다

이 시스템은 **답변을 지어내지 않습니다.** `.env` 에 LLM 엔드포인트나 키를
넣지 않으면 에이전트가 발언 차례에 실패하고, 그 자리에 "연결 끊김" 이
기록됩니다. 화면은 뜨고 대화도 만들 수 있지만 토론은 진행되지 않습니다.

최소 설정은 이 정도입니다.

```dotenv
LLM_API_BASE=http://localhost:1234/v1
LLM_MODEL=openai/qwen2.5-coder-32b
LLM_API_KEY=
```

API 키가 필요 없는 로컬 서버(Ollama, LM Studio, vLLM)라면 `LLM_API_KEY` 는
비워도 됩니다 — `api_base` 만 있어도 실제 호출을 시도합니다.

### MCP 서버는 선택이지만 있는 편이 낫습니다

`setup_mcp.py` 없이도 앱은 뜹니다. 다만 에이전트가 파일을 읽거나 코드를
실행하지 못하므로, "이 코드는 동작합니다" 라는 주장을 아무도 검증하지 못합니다.
Node 가 없다면 `--skip-node` 로 건너뛰고 해당 서버를 `conf.json` 에서
`"enabled": false` 로 꺼두세요.

### `conf.json` 은 저장소에 없습니다

`.gitignore` 대상입니다. 그 망의 실제 엔드포인트와 키가 들어가기 때문입니다.
공유용 템플릿은 `conf.example.json` 이고, 새 클론에서는 이것을 복사해 시작합니다.

---

## 이 섹션의 문서

- [설치와 첫 실행](01-installation.md) — 단계별 절차, MCP 서버 준비, 실행 옵션
- [conf.json 설정](02-configuration.md) — 설정 파일 구조, 환경변수 치환, 엔드포인트 예시

---

## 다음에 읽을 것

- 시스템이 어떻게 동작하는지 알고 싶다면 → [핵심 기술 개관](../03-core/README.md)
- 바로 에이전트를 손보고 싶다면 → [로스터 편집](../04-workflows/03-roster-editing.md)
- 폐쇄망에 옮길 계획이라면 → [폐쇄망 배포](../04-workflows/05-airgap-deployment.md)

---

> 다음: [설치와 첫 실행](01-installation.md)
