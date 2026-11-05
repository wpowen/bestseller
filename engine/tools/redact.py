"""Replace literal .env values before persisting logs and reports."""

import argparse
from pathlib import Path
import re
import sys


class Redactor:
    def __init__(self, *env_paths):
        values = set()
        for path in env_paths:
            try:
                lines = Path(path).read_text("utf-8").splitlines()
            except FileNotFoundError:
                continue
            for line in lines:
                line = line.strip().removeprefix("export ")
                if not line or line.startswith("#") or "=" not in line:
                    continue
                value = line.split("=", 1)[1].strip()
                if value.startswith(('"', "'")):
                    quote = value[0]
                    value = value[1:].split(quote, 1)[0]
                else:
                    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
                if len(value) >= 12:
                    values.add(value)
        self.pattern = (
            re.compile("|".join(re.escape(v) for v in sorted(values, key=len, reverse=True))) if values else None
        )

    def __call__(self, value):
        return self.pattern.sub("[REDACTED]", value) if self.pattern else value

    def write(self, path, value):
        Path(path).write_text(self(value), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    value = Redactor(*args.env)(sys.stdin.read())
    if args.output:
        args.output.write_text(value, encoding="utf-8")
    else:
        sys.stdout.write(value)


if __name__ == "__main__":
    main()
