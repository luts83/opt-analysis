"""LLM 본문 후처리: 번호 초안 제거, 뉴스 1회만, 섹션 리듬 정리."""
from __future__ import annotations

import re


def polish_narrative(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\r\n", "\n")

    # "1. 📊 제목:" / "2. 🎯 한 줄 요약:" 같은 작성 지시 번호
    t = re.sub(r"(?m)^\s*\d+\.\s+(?=[\U0001F300-\U0001FAFF📊🎯💰🚨🌡️🟢🔴📈🔮⚠️📰])", "", t)
    t = re.sub(r"(?m)^\s*\d+\.\s+(📊|🎯|💰|🚨|🌡️|🟢|🔴|📈|🔮|⚠️|📰)", r"\1", t)

    # "📊 제목:" "🎯 한 줄 요약:" 접두 제거
    t = re.sub(r"(?m)^📊\s*제목\s*[:：]\s*", "📊 ", t)
    t = re.sub(r"(?m)^🎯\s*한\s*줄\s*요약\s*[:：]\s*", "🎯 ", t)
    t = re.sub(r"(?m)^💰\s*지금\s*주가\s*[:：]\s*", "💰 가격\n", t)
    t = re.sub(r"(?m)^🎯\s*그래서\s*뭘\s*해야\s*하나\s*", "🎯 오늘 체크포인트\n", t)

    # 뉴스 섹션 전부 제거 후 호출부에서 1회 재삽입
    t = re.sub(
        r"(?m)^📰[^\n]*\n(?:.*?\n)*?(?=^[\U0001F300-\U0001FAFF🎯⚠️📊💰🚨🌡️🟢🔴📈🔮]|^⚠️ 이 리포트|\Z)",
        "",
        t,
    )
    # 더 단순한 뉴스 블록 제거 (이모지 클래스 실패 대비)
    t = re.sub(r"(?m)^📰.*?(?=^🎯|^⚠️ 이 리포트|\Z)", "", t, flags=re.S)

    # 연속 빈 줄 압축
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() + "\n"


def inject_news_once(narrative: str, news_block: str) -> str:
    """뉴스 블록을 면책 앞 또는 액션 뒤에 1회만 삽입."""
    if not news_block.strip():
        return narrative
    # 기존 뉴스 제거
    text = re.sub(r"(?m)^📰.*?(?=^🎯|^⚠️ 이 리포트|\Z)", "", narrative, flags=re.S)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"

    disclaimer = "⚠️ 이 리포트는 투자 조언이 아니라 시장 정보 요약입니다."
    block = news_block.strip() + "\n"
    if disclaimer in text:
        return text.replace(disclaimer, block + "\n" + disclaimer, 1)
    return text.rstrip() + "\n\n" + block + "\n"
