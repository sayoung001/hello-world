# 출처

이 확장은 destory1984/saveticker_filter 의 `extension/` 을 그대로 가져온 것이다
(MIT, 원 저작권은 LICENSE 참조). <https://github.com/destory1984/saveticker_filter>

하는 일: saveticker.com 탭 안에서 페이지가 받는 `/api/news/list` 와
`/api/news/top-stories`(SAVE PICK) 응답을 기록해 `Downloads\saveticker\` 에
날짜별 CSV 로 저장한다. 사이트 입장에서는 사용자가 페이지를 읽는 것과 같다.

우리는 CSV 만 쓴다. 원 프로젝트의 로컬 판별 서버(127.0.0.1:18765)는 쓰지
않으며, 확장이 거기로 보내는 ping 은 실패해도 조용히 무시된다.
