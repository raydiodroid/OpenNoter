"""Keep standalone dependency stubs local to the module they exercise."""
import functools
import sys


def isolated_loader(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        saved = {k: v for k, v in sys.modules.items() if k == 'app' or k.startswith('app.')}
        try:
            return function(*args, **kwargs)
        finally:
            for key in list(sys.modules):
                if key == 'app' or key.startswith('app.'):
                    sys.modules.pop(key, None)
            sys.modules.update(saved)
    return run
