"""支持 ``python -m _hmp`` 运行（``python -m hmp`` 走根目录 hmp.py）。"""

from .cli import main

raise SystemExit(main())
