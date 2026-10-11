import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _tap import run  # noqa: E402


def main():
    loader = unittest.TestLoader()
    suite = loader.discover(HERE, pattern="test_issue.py", top_level_dir=HERE)
    # Keep the real stdout TAP-only: both Python print() noise and Qt's own
    # fd-level diagnostics are redirected to /dev/null while tests run.
    captured = io.StringIO()
    sys.stdout.flush()
    saved_fd = os.dup(1)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, 1)
        code = run(suite, stream=captured)
    finally:
        os.dup2(saved_fd, 1)
        os.close(saved_fd)
        os.close(devnull_fd)
    sys.stdout.write(captured.getvalue())
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
