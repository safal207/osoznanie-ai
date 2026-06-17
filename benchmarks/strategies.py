"""Retrieval strategies compared by the benchmark."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Protocol

from osoznanie.models import Lesson
from osoznanie.recall import RecallEngine, RecallQuery, RecallStore

from .models import RankedLesson, StrategyName

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class RetrievalStrategy(Protocol):
    name: StrategyName

    def rank(
        self,
        query: RecallQuery,
        store: RecallStore,
        *,
        now: datetime,
    ) -> list[RankedLesson]: ...


def _tokens(value: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(value.lower()))


def _query_tokens(query: RecallQuery) -> set[str]:
    values = [query.domain, query.task_type, *query.tags]
    return _tokens(" ".join(values))


class NoMemoryStrategy:
    name = StrategyName.NO_MEMORY

    def rank(
        self,
        query: RecallQuery,
        store: RecallStore,
        *,
        now: datetime,
    ) -> list[RankedLesson]:
        del query, store, now
        return []


class NaiveKeywordStrategy:
    """Rank raw lesson statements by query-token recall.

    This intentionally ignores structured scope, validation state, evidence,
    access policy, and recency so it behaves like a simple text baseline.
    """

    name = StrategyName.NAIVE_KEYWORD

    def rank(
        self,
        query: RecallQuery,
        store: RecallStore,
        *,
        now: datetime,
    ) -> list[RankedLesson]:
        del now
        query_tokens = _query_tokens(query)
        if not query_tokens:
            return []

        scored: list[tuple[str, float]] = []
        for record in store.list("lesson"):
            if not isinstance(record, Lesson):
                continue
            overlap = len(query_tokens & _tokens(record.statement))
            if overlap == 0:
                continue
            score = overlap / len(query_tokens)
            scored.append((record.id, round(score, 6)))

        scored.sort(key=lambda item: (-item[1], item[0]))
        return [
            RankedLesson(lesson_id=lesson_id, score=score, rank=index)
            for index, (lesson_id, score) in enumerate(
                scored[: query.max_items],
                start=1,
            )
        ]


class OsoznanieRecallStrategy:
    name = StrategyName.OSOZNANIE_RECALL

    def rank(
        self,
        query: RecallQuery,
        store: RecallStore,
        *,
        now: datetime,
    ) -> list[RankedLesson]:
        results = RecallEngine(store).recall(query, now=now)
        return [
            RankedLesson(lesson_id=result.lesson_id, score=result.score, rank=index)
            for index, result in enumerate(results, start=1)
        ]


DEFAULT_STRATEGIES: tuple[RetrievalStrategy, ...] = (
    NoMemoryStrategy(),
    NaiveKeywordStrategy(),
    OsoznanieRecallStrategy(),
)
