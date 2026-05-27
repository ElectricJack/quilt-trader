from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class Fold:
    index: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date


def compute_folds(
    *,
    start: date,
    end: date,
    train_years: float,
    test_years: float,
    step_months: float,
) -> list[Fold]:
    train_days = int(train_years * 365.25)
    test_days = int(test_years * 365.25)
    step_days = int(step_months * 30.4375)

    folds: list[Fold] = []
    i = 0
    cursor = start
    while True:
        train_start = cursor
        train_end = train_start + timedelta(days=train_days)
        test_start = train_end
        test_end = test_start + timedelta(days=test_days)
        if test_end > end:
            break
        folds.append(
            Fold(
                index=i,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
        i += 1
        cursor = cursor + timedelta(days=step_days)
    return folds
