"""Compatibility entry point; implementation lives in app.bullish.stock_bracket."""
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == '__main__':
    import runpy
    runpy.run_module('app.bullish.stock_bracket', run_name='__main__')
else:
    from importlib import import_module
    _module = import_module('app.bullish.stock_bracket')
    globals().update({k: v for k, v in vars(_module).items() if not k.startswith('__')})
