"""
구조화 출력 스키마 검사 — API 가 거부할 키워드를 **요청 전에** 잡는다.

왜 필요한가:
  2026-10-20 배치에서 후보 4건(HLT Eff 9.75 포함)이 전부 탈락해 추천 0건이
  됐다. 원인은 RECO_SCHEMA 의 value_pct 에 넣은 `minimum: 0, maximum: 20`.
  구조화 출력 API 는 number 의 수치 제약을 지원하지 않아 요청 전체를 400 으로
  거부했다. 종목 단위 격리가 그걸 조용히 "추천 0건"으로 넘겼고, 사용자는
  다이제스트만 보고는 알 수 없었다.

  스키마는 코드에 박혀 있어 배치가 돌 때까지 틀렸는지 모른다. 그래서 import
  시점에 검사할 수 있는 순수 함수로 두고, check_pipeline 과 점검 스크립트가
  부른다. 위반이 있으면 **어느 경로의 어느 키워드인지** 찍는다.

지원하지 않는 것(공식 문서 기준):
  - 수치 제약   minimum, maximum, exclusiveMinimum, exclusiveMaximum, multipleOf
  - 문자열 제약 minLength, maxLength, pattern, format
  - 배열 제약   minItems, maxItems, uniqueItems, contains
  - additionalProperties 가 false 이외의 값
  - 재귀($ref) 스키마
"""

from __future__ import annotations

from typing import Any

UNSUPPORTED = {
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "format",
    "minItems", "maxItems", "uniqueItems", "contains",
    "$ref", "$defs", "definitions",
}


def validate(schema: Any, path: str = "$") -> list[str]:
    """위반 목록. 비어 있으면 통과. 각 항목은 '경로: 사유'."""
    out: list[str] = []
    if isinstance(schema, dict):
        for k, v in schema.items():
            if k in UNSUPPORTED:
                out.append(f"{path}.{k}: 구조화 출력이 지원하지 않는 키워드")
                continue
            if k == "additionalProperties" and v is not False:
                out.append(f"{path}.additionalProperties: false 만 허용 (현재 {v!r})")
                continue
            if k == "properties" and isinstance(v, dict):
                for name, sub in v.items():
                    out += validate(sub, f"{path}.{name}")
            elif k == "items":
                out += validate(v, f"{path}[]")
            elif k in ("anyOf", "oneOf", "allOf") and isinstance(v, list):
                for i, sub in enumerate(v):
                    out += validate(sub, f"{path}.{k}[{i}]")
        # object 인데 additionalProperties 를 안 적으면 API 가 열려 있다고 본다
        if schema.get("type") == "object" and "properties" in schema \
                and "additionalProperties" not in schema:
            out.append(f"{path}: object 에 additionalProperties: false 가 없음")
    return out


def all_agent_schemas() -> dict[str, Any]:
    """이 프로젝트가 API 에 보내는 스키마 전부 — 새 에이전트를 만들면 여기에 추가."""
    from stock_auto.agents.analysts import _SENTIMENT_SCHEMA
    from stock_auto.agents.portfolio_agent import RECO_SCHEMA
    return {"analysts._SENTIMENT_SCHEMA": _SENTIMENT_SCHEMA,
            "portfolio_agent.RECO_SCHEMA": RECO_SCHEMA}


def check_all() -> list[str]:
    out: list[str] = []
    for name, sch in all_agent_schemas().items():
        out += [f"{name} {v}" for v in validate(sch)]
    return out


def main() -> int:
    bad = check_all()
    if bad:
        print(f"❌ 구조화 출력 스키마 위반 {len(bad)}건 — 이대로 보내면 요청이 400 으로 거부됩니다:")
        for b in bad:
            print(f"   • {b}")
        return 1
    n = len(all_agent_schemas())
    print(f"✅ 에이전트 스키마 {n}개 — 구조화 출력 지원 범위 안")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
