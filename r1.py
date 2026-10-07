# SPDX-License-Identifier: GPL-2.0-only
"""Применение этапа 2 к реальной библиотеке (шаги R1 из TASKS.md).

  python r1.py plan    # резервная копия, оценка «до», безопасные backfill-ы, планы переносов в файлы
  python r1.py apply   # применить переносы (после просмотра планов), оценка «после», сводка

Отчёты и логи — в папке reports/r1-<дата>/. Ничего не удаляется: файлы только переносятся,
каждое действие откатывается `python library.py undo N`.
"""
import argparse
import datetime
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RECLASSIFY = [
    ("reclassify-statistics", ["reclassify", "--category", "Наука | Математика | Статистика"]),
    ("reclassify-needs-review", ["reclassify", "--status", "needs_review"]),
    ("reclassify-fiction-other", ["reclassify", "--category", "Художественные | Другое"]),
]


def run(report_dir, name, script, args, db):
    """Запускает команду, пишет вывод в <name>.txt и на экран; возвращает код."""
    cmd = [sys.executable, os.path.join(HERE, script), "--db", db] + args
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    print(f"\n=== {name}: {' '.join(args)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env, cwd=HERE)
    out = proc.stdout + proc.stderr
    with open(os.path.join(report_dir, f"{name}.txt"), "w", encoding="utf-8") as f:
        f.write(out)
    tail = out.strip().splitlines()[-3:]
    print("\n".join(tail))
    if proc.returncode:
        print(f"  (код возврата {proc.returncode})")
    return proc.returncode


def report_dir_for(date):
    path = os.path.join(HERE, "reports", f"r1-{date}")
    os.makedirs(path, exist_ok=True)
    return path


def ollama_ok():
    sys.path.insert(0, HERE)
    try:
        from preflight import check_ollama
        problems = check_ollama()
    except Exception as exc:
        return False, str(exc)
    return not problems, "; ".join(map(str, problems or []))


def cmd_plan(args):
    date = args.date or datetime.date.today().isoformat()
    rd = report_dir_for(date)
    backup = os.path.join(HERE, f"books.backup-{date}.db")
    if not os.path.exists(backup):
        shutil.copy2(args.db, backup)
        print(f"Резервная копия: {backup}")
    else:
        print(f"Резервная копия уже есть: {backup}")

    run(rd, "01-eval-fb2-before", "eval_quality.py", ["fb2"], args.db)
    run(rd, "02-eval-consistency-before", "eval_quality.py", ["consistency"], args.db)

    # Только запись в БД, файлы не трогаются (при сбое — восстановить из копии).
    for i, name in enumerate(["backfill-authors", "backfill-genres", "backfill-quality", "backfill-works"], 3):
        if run(rd, f"{i:02d}-{name}", "library.py", [name, "--apply"], args.db):
            sys.exit(f"Остановлено на {name}; копия БД: {backup}")

    # Пары авторов требуют решения человека — только список.
    run(rd, "07-suggest-authors", "library.py", ["suggest-authors"], args.db)
    # Планы переносов (без --apply).
    run(rd, "08-plan-rebuild-fiction", "library.py", ["rebuild-fiction"], args.db)
    run(rd, "09-plan-dedupe-editions", "library.py", ["dedupe-editions"], args.db)
    ok, msg = ollama_ok()
    if ok:
        for name, a in RECLASSIFY:
            run(rd, f"10-plan-{name}", "library.py", a, args.db)
    else:
        print(f"\nOllama недоступна ({msg}) — планы reclassify пропущены.")
    run(rd, "11-plan-prune-missing", "library.py", ["prune-missing"], args.db)
    print(f"\nПланы: {rd}\nПросмотрите их, затем: python r1.py apply --date {date}")


def cmd_apply(args):
    date = args.date or datetime.date.today().isoformat()
    rd = report_dir_for(date)
    if not os.path.exists(os.path.join(HERE, f"books.backup-{date}.db")):
        sys.exit("Нет резервной копии за эту дату — сначала `python r1.py plan`.")
    steps = [
        ("21-apply-rebuild-fiction", ["rebuild-fiction", "--apply"]),
        ("22-apply-dedupe-editions", ["dedupe-editions", "--apply"]),
    ]
    ok, msg = ollama_ok()
    if ok:
        steps += [(f"23-apply-{n}", a + ["--apply"]) for n, a in RECLASSIFY]
    else:
        print(f"Ollama недоступна ({msg}) — reclassify пропущен.")
    steps.append(("24-apply-prune-missing", ["prune-missing", "--apply"]))
    for name, a in steps:
        if run(rd, name, "library.py", a, args.db):
            print(f"Шаг {name} завершился с ошибкой — дальше не идём (см. {rd}).")
            break
    run(rd, "31-eval-fb2-after", "eval_quality.py", ["fb2"], args.db)
    run(rd, "32-eval-consistency-after", "eval_quality.py", ["consistency"], args.db)
    run(rd, "33-actions", "library.py", ["actions", "--limit", "50"], args.db)
    print(f"\nГотово. Отчёты: {rd}. Откат любого действия: python library.py undo N")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["plan", "apply"])
    parser.add_argument("--db", default=os.path.join(HERE, "books.db"))
    parser.add_argument("--date", help="ГГГГ-ММ-ДД для папки отчётов и копии (по умолчанию сегодня)")
    args = parser.parse_args()
    {"plan": cmd_plan, "apply": cmd_apply}[args.command](args)


if __name__ == "__main__":
    main()
