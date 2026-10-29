"""Offline stand-ins for the parts of Agent Zero an API handler of this plugin imports.

The handlers in `api/` import `helpers.api` and `helpers.plugins` (Agent Zero) and reach the plugin
through `usr.plugins.api_rotation_by_kame`. This module registers small fakes for all of them, so a
handler can be run in a test: the saved plugin config is a dict, `activate` is a recorder, and the
plugin modules are the ones the test already imported (one copy of the engine, not two).
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "usr.plugins.api_rotation_by_kame"

CONFIG: dict = {}
SAVED: list = []
ACTIVATED: list = []


class ApiHandler:
    """The base class Agent Zero gives a handler; the tests call ``process`` directly."""

    def __init__(self, *args, **kwargs):
        pass


def _install(engine, settings):
    helpers = sys.modules.get("helpers") or types.ModuleType("helpers")   # the engine tests already made one
    api = types.ModuleType("helpers.api")
    api.ApiHandler, api.Input, api.Output, api.Request = ApiHandler, dict, dict, object
    plugins = types.ModuleType("helpers.plugins")

    def get_plugin_config(_plugin, agent=None):
        return dict(CONFIG)

    def save_plugin_config(_plugin, _project, _profile, config):
        CONFIG.clear()
        CONFIG.update(config)
        SAVED.append(dict(config))

    plugins.get_plugin_config, plugins.save_plugin_config = get_plugin_config, save_plugin_config
    helpers.api, helpers.plugins = api, plugins
    sys.modules.update({"helpers": helpers, "helpers.api": api, "helpers.plugins": plugins})

    usr, usr_plugins, package = (types.ModuleType(n) for n in ("usr", "usr.plugins", PACKAGE))
    package.__path__ = [str(ROOT)]
    activation = types.ModuleType(f"{PACKAGE}.kame_activation")
    activation.activate = lambda agent=None: ACTIVATED.append(agent)
    for name, module in (("kame_engine", engine), ("kame_settings", settings), ("kame_activation", activation)):
        sys.modules[f"{PACKAGE}.{name}"] = module
        setattr(package, name, module)
    usr.plugins, usr_plugins.api_rotation_by_kame = usr_plugins, package
    sys.modules.update({"usr": usr, "usr.plugins": usr_plugins, PACKAGE: package})


def load_api(name: str, engine, settings):
    """The `api/<name>.py` module, loaded from the plugin folder against the fakes above."""
    _install(engine, settings)
    spec = importlib.util.spec_from_file_location(f"kame_api_{name}", ROOT / "api" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call(handler_class, payload: dict) -> dict:
    """Run one handler the way Agent Zero does: ``await handler.process(input, request)``."""
    return asyncio.run(handler_class().process(payload, None))


def reset():
    CONFIG.clear()
    SAVED.clear()
    ACTIVATED.clear()
