# trail-walk-data

Android 앱 **둘레길 지도**(`com.mjhlabs.trailwalkguide`)가 읽는 걷기길 데이터 저장소입니다.
GitHub Actions가 하루 1번(04:00 KST 예약, 실제 시작은 보통 몇 시간 늦음) 공공데이터포털 API에서
코스 목록과 GPX 경로를 받아 색인 파일과 코스별 선 파일을 만들어 이 저장소에 커밋합니다.

이 저장소와 앱은 공식 서비스가 아니며 한국관광공사·산림청 등 어떤 기관과도 관계가 없습니다.
현장 안내판과 공식 안내를 우선하세요.

- 출처: 한국관광공사 두루누비 정보 서비스 (공공데이터포털 15101974),
  https://www.data.go.kr/data/15101974/openapi.do
- 산림청 숲서비스 및 둘레길 정보(15158968)는 개발계정 심의 중이라 **꺼져 있습니다**
  (`collector/config.py`의 `SOURCES["kfs"] = False`, 할 일은 `collector/kfs.py`).

## 앱이 읽는 파일

```
https://raw.githubusercontent.com/jinhom6225/trail-walk-data/main/data/index.json
https://raw.githubusercontent.com/jinhom6225/trail-walk-data/main/data/courses/<id>.json
```

- `data/index.json` — `schemaVersion` · `generatedAt` · `sources[]` · `routes[]` · `items[]`.
  성공한 회차마다 `generatedAt`이 새로 바뀝니다(앱의 48시간 "기준 자료" 표시 기준).
- `data/courses/<id>.json` — 코스 하나의 선(Google encoded polyline, 정밀도 5)과 소개 글.
  내용이 바뀔 때만 다시 씁니다. `hasLine=false`인 코스는 이 파일이 없습니다.
- 데이터 계약(필드 뜻·단위)은 앱 설계 문서 `trail_walk_guide/research.md` §6-2
  "데이터 계약"이 기준입니다. 요약:
  - id `dnb-<crsIdx>`, 길 id `dnb-r-<routeIdx>`, 좌표 `[위도, 경도]` 소수 5자리,
    `bbox` = `[최소 위도, 최소 경도, 최대 위도, 최대 경도]`.
  - `distanceKm`·`durationMin`(분)·`level`(1·2·3)은 원천 값 그대로, `lineKm`은 단순화 전 GPX 길이.
  - `lineHash` = 인코딩된 polyline의 sha256 앞 12자리. 앱은 이 값으로 코스 파일 캐시를 갱신합니다.
  - `reservation` = 이름에 "예약노선"(DMZ 평화의 길 통제 구간).

## 수집 규칙

- 코스 목록·길 목록: 100건씩 빈 쪽이 나올 때까지 받고, 받은 건수가 `totalCount`와 다르면 실패.
- GPX: `state/gpx.json`의 `modifiedtime`·URL과 다른 코스만 다시 받습니다(1초에 1번, 실패 시 2번 재시도).
  원본 GPX는 커밋하지 않습니다. 받기에 실패하면 이전 선을 그대로 두고 다음 회차에 다시 받습니다.
- GPX는 첫 번째 `trk`만 씁니다(일부 파일에 같은 코스가 `trk` 두 개로 들어 있음). `wpt`는 무시합니다.
- 선 단순화: Douglas-Peucker 허용 오차 5m, 고도는 버립니다.
- 20% 규칙: 코스 수가 이전보다 20% 넘게 줄거나 검증 탈락이 20%를 넘으면 이번 회차를 버리고
  (아무 파일도 쓰지 않음) 실패로 끝냅니다. 앱은 마지막 정상 자료를 그대로 씁니다.
- 바뀐 내용(새 코스·선 바뀜·정보 바뀜·사라짐)은 커밋 메시지와 Telegram 알림에 들어갑니다.

## 워크플로

- `.github/workflows/collect.yml` — 매일 04:00 KST + 수동 실행(`refresh_gpx`: GPX 모두 다시 받기,
  `force_fail`: 실패 알림 확인용). 테스트 → 수집 → `data/`·`state/`가 바뀌었을 때만 커밋 → Telegram.
- `.github/workflows/keepalive.yml` — 매주 월요일, 두 워크플로를 다시 켜서 60일 비활성 정지를 막습니다.

## 필요한 Secret (저장소 Settings → Secrets and variables → Actions)

| 이름 | 용도 |
|---|---|
| `DATA_GO_KR_SERVICE_KEY` | 공공데이터포털 서비스키(디코딩 키). Collect 단계에서만 씁니다. |
| `TELEGRAM_BOT_TOKEN` | 수집 결과 알림 봇 토큰 (없으면 알림만 꺼짐) |
| `TELEGRAM_CHAT_ID` | 알림 받을 채팅 id (없으면 알림만 꺼짐) |

## 로컬에서

저장소 루트에서 실행합니다. Python 3.12, 표준 라이브러리만 씁니다.

```sh
python3.12 -m unittest discover -s collector/tests -t .   # 네트워크 없이 (소켓 차단)
python3.12 -m collector.collect                             # 실제 수집 (API 약 5회 + 바뀐 GPX)
```

- 서비스키는 환경 변수 `DATA_GO_KR_SERVICE_KEY`, 없으면 `~/.secrets/datagokr/service_key`에서 읽습니다.
  키는 출력·파일·커밋 어디에도 남기지 않습니다.
- 종료 코드: 0 성공, 1 20% 규칙으로 중단, 2 설정 오류(키 없음), 3 API 호출 실패, 4 예기치 않은 오류.
  1·2·3이면 아무 파일도 쓰지 않습니다.
- 테스트 입력은 `collector/tests/fixtures/real/`의 실측 응답(서비스키 제거)이고,
  `fixtures/hand/`는 오류 경로용 손 fixture입니다.
