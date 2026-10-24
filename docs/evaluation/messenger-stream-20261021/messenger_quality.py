"""Compatibility import; the maintained implementation lives in scripts/."""
import sys
import alden_messenger_quality as _runtime

def __getattr__(name):
    return getattr(_runtime, name)

sys.modules[__name__] = _runtime
