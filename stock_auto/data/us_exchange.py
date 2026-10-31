"""(호환 shim) → stock_auto.markets.us.exchange 로 이동했다. 새 코드는 그쪽을 import."""
from stock_auto.markets.us.exchange import *  # noqa: F401,F403
from stock_auto.markets.us.exchange import (  # noqa: F401 — 밑줄 이름도 넘긴다
    _read, _fetch, _NAME_CUT, _NAME_SUFFIX)

if __name__ == "__main__":
    import runpy
    runpy.run_module("stock_auto.markets.us.exchange", run_name="__main__")
