"""Local book UI, deliberately outside the engine package."""

from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).with_suffix("")))
    from server import main

    main()
