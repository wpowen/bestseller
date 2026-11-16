"""Re-run the book checkup on the de-AI final text once a book is complete.

The writer's own checkup reads drafts; de-AI runs after it and can change quotes and sentences,
so a finished book's checkup must describe what readers actually get. It runs as the public
checkup CLI in its own process, like every other web-started job, and only writes book_checkup.json.
"""

import sys
import threading

from process_logs import spawn


def checkup_command(folder):
    return [sys.executable, "-m", "tools.book_checkup", str(folder), "--source", "final"]


def start_final_checkup(queue, folder):
    """Start the checkup without blocking the queue; a failure is logged and leaves the book untouched."""
    try:
        child = spawn(
            checkup_command(folder),
            folder / "web-process.log",
            queue.env,
            cwd=queue.engine.parent,
            env=queue.child_env(),
            start_new_session=True,
        )
    except OSError as exc:
        with (folder / "web-process.log").open("a", encoding="utf-8") as log:
            log.write(f"final checkup not started: {type(exc).__name__}\n")
        return None
    threading.Thread(target=child.wait, name=f"final-checkup-{folder.name}", daemon=True).start()
    return child
