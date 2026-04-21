from __future__ import annotations


def main() -> int:
    from .interactive.shell import run_interactive

    return run_interactive()


if __name__ == "__main__":
    raise SystemExit(main())

