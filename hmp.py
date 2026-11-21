"""hmp 的模块入口，使 ``python -m hmp`` 可用。

实现位于 ``_hmp`` 包中；本模块仅做转发。
"""

from _hmp import core
from _hmp.cli import main

__all__ = ["core", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
