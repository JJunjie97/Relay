import pkgutil
import importlib
import inspect
import logging

from api.BaseApi import BaseApi
import api

logger = logging.getLogger("ApiFactory")

def CreateApi(module_name: str) -> BaseApi:
    """Scan the api package for a BaseApi subclass that handles the given module_name."""
    for _, name, _ in pkgutil.iter_modules(api.__path__):
        if not name.startswith("Api") or name == "ApiNodeData":
            continue
        try:
            mod = importlib.import_module(f"api.{name}")
            for _, cls in inspect.getmembers(mod, lambda c: inspect.isclass(c) and issubclass(c, BaseApi) and c is not BaseApi):
                if module_name in (getattr(cls, "MODULE_KEYS", None) or [getattr(cls, "MODULE_KEY", None)]):
                    return cls()
        except Exception as e:
            logger.warning(f"Failed to load API module {name}: {e}")
    return None
