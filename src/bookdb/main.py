"""bookdb 入口。

console_scripts: book = bookdb.main:main
"""

from __future__ import annotations

from .cli.commands import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
