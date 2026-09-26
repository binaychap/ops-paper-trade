"""Compatibility entry point; implementation lives in app.options.brackets."""
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if __name__ == '__main__':
    import runpy
    runpy.run_module('app.options.brackets', run_name='__main__')
else:
    from importlib import import_module
    _module = import_module('app.options.brackets')
    globals().update({k: v for k, v in vars(_module).items() if not k.startswith('__')})
