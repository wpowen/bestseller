"""Drain child output through the .env filter; raw bytes never enter log files."""

import subprocess
import threading
from tools.redact import Redactor


def spawn(command, path, env_file, **kwargs):
    scrub = Redactor(env_file)
    # Open before spawning so an unavailable log path never leaves an untracked child.
    output = path.open("a", encoding="utf-8")
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    except BaseException:
        output.close()
        raise

    def drain():
        try:
            with child.stdout, output:
                for line in iter(child.stdout.readline, b""):
                    output.write(scrub(line.decode("utf-8", errors="replace")))
                    output.flush()
        finally:
            output.close()

    child.log_thread = threading.Thread(target=drain, daemon=True)
    child.log_thread.start()
    return child
