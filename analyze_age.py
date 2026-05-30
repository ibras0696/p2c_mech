#!/usr/bin/env python3
"""Анализ логов: связывает order_age_ms (возраст заявки на момент приёма WS)
с исходом claim (выиграли / проиграли).

Гипотеза: если у выигранных заявок order_age_ms стабильно МЕНЬШЕ, чем у
проигранных — значит гонку решает скорость доставки WS-кадра, а не take.

Использование:
    docker logs <bot-container> 2>&1 | python3 analyze_age.py
    # или
    python3 analyze_age.py < лог.txt
"""
from __future__ import annotations

import re
import sys
from statistics import median


def _num(pattern: str, line: str) -> int | None:
    m = re.search(pattern, line)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def main() -> None:
    wins: list[int] = []
    losses: list[int] = []
    win_take: list[int] = []
    loss_take: list[int] = []
    none_age = 0

    for line in sys.stdin:
        is_win = "event=claim_succeeded" in line
        is_loss = "event=claim_failed" in line and "reason=lost_race" in line
        if not (is_win or is_loss):
            continue
        age = _num(r"order_age_ms=(-?\d+)", line)
        if age is None:
            none_age += 1
            continue
        take = _num(r"take_http_ms=(\d+)", line)
        if is_win:
            wins.append(age)
            if take is not None:
                win_take.append(take)
        else:
            losses.append(age)
            if take is not None:
                loss_take.append(take)

    def stats(label: str, data: list[int]) -> None:
        if not data:
            print(f"{label:8} n=0")
            return
        print(
            f"{label:8} n={len(data):<4} "
            f"min={min(data):<6} med={int(median(data)):<7} "
            f"avg={int(sum(data)/len(data)):<7} max={max(data):<7}"
        )

    print("=" * 64)
    print("ВОЗРАСТ ЗАЯВКИ НА ПРИЁМЕ WS (order_age_ms), мс")
    print("=" * 64)
    stats("WIN", wins)
    stats("LOSE", losses)
    print()
    print("=" * 64)
    print("TAKE HTTP (take_http_ms), мс")
    print("=" * 64)
    stats("WIN", win_take)
    stats("LOSE", loss_take)
    print()

    total = len(wins) + len(losses)
    if total:
        wr = 100 * len(wins) / total
        print(f"Win rate: {wr:.1f}%  ({len(wins)}/{total})")
    if none_age:
        print(f"Строк без order_age_ms (старый билд?): {none_age}")
    print()

    # Вывод
    if wins and losses:
        wa = sum(wins) / len(wins)
        la = sum(losses) / len(losses)
        print("-" * 64)
        if wa < la * 0.7:
            print("ВЫВОД: выигранные заявки заметно СВЕЖЕЕ проигранных.")
            print("→ Гонку решает доставка WS, а не take. Копать сокет/сессию.")
        elif wa > la * 1.3:
            print("ВЫВОД: выигрываем СТАРЫЕ заявки — конкуренции на них нет.")
            print("→ Свежие забирают раньше нас по другому каналу.")
        else:
            print("ВЫВОД: возраст у win/lose примерно одинаков.")
            print("→ Дело не в возрасте. Гонка честная на уровне take/сети.")
        print("-" * 64)


if __name__ == "__main__":
    main()
