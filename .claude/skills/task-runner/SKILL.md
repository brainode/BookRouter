---
name: task-runner
description: Run the next BookRouter task(s) from TASKS.md with the coder → reviewer agents. Use when the person says "сделай следующую задачу", "выполни T4x", "run the next task".
---

# Running BookRouter tasks

1. Read `TASKS.md`. The next task is the first unchecked one whose "Зависит от" tasks are all checked. If the person named a task, use that one but refuse (and say why) if its dependencies are unchecked.
2. Dispatch the `coder` agent (Agent tool, `subagent_type: coder`) with the prompt: `Выполни задачу <ID>: файл tasks/<file>.md. Следуй агенту coder.` Do not paste the spec — the agent reads it.
3. When it reports done, dispatch the `reviewer` agent with `Проверь задачу <ID> (tasks/<file>.md).`
4. `VERDICT: REWORK` → send the findings back to the same coder agent (SendMessage) once. A second REWORK → stop and show the findings to the person.
5. `VERDICT: ACCEPT` → tell the person in 2–3 lines what changed and the pytest result. Commit only if the person asked; commit message in English, one task per commit.
6. A task marked "Ручной шаг" in TASKS.md (e.g. running a migration on the real library) is never run by an agent — tell the person the exact command instead.
