"""Behaviour tests for the Auto Layers desktop-shell rework.

They exercise the public surface that must survive the rework, importable
from ``setup``/``main`` (legacy paths) or from an ``auto_layers`` package.
"""
import contextlib
import importlib
import io
import os
import pkgutil
import shutil
import sys
import tempfile
import types
import unittest
from configparser import ConfigParser
from contextlib import redirect_stdout
from dataclasses import asdict, fields
from unittest import mock


def _stub_pywinctl_if_unavailable():
    try:
        import pywinctl  # noqa: F401

        return
    except BaseException:
        pass

    class _Window:
        def getPID(self):
            return 0

    stub = types.ModuleType("pywinctl")
    stub.getActiveWindow = lambda: _Window()
    sys.modules["pywinctl"] = stub


_stub_pywinctl_if_unavailable()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_MISSING = object()
_MODULES = None


def repo_modules():
    global _MODULES
    if _MODULES is not None:
        return _MODULES
    mods = []
    for name in ("setup", "main", "raw_hid"):
        try:
            mods.append(importlib.import_module(name))
        except BaseException:
            pass
    try:
        pkg = importlib.import_module("auto_layers")
    except BaseException:
        pkg = None
    if pkg is not None:
        mods.insert(0, pkg)
        if hasattr(pkg, "__path__"):
            for _finder, sub, _ispkg in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + "."):
                try:
                    mods.insert(1, importlib.import_module(sub))
                except BaseException:
                    pass
    _MODULES = mods
    return mods


def find(attr):
    for module in repo_modules():
        if hasattr(module, attr):
            return getattr(module, attr), module
    raise AssertionError(f"required public symbol {attr!r} is missing")


@contextlib.contextmanager
def modules_attr(name, value):
    saved = []
    for module in repo_modules():
        saved.append((module, getattr(module, name, _MISSING)))
        setattr(module, name, value)
    try:
        yield
    finally:
        for module, old in saved:
            if old is _MISSING:
                try:
                    delattr(module, name)
                except AttributeError:
                    pass
            else:
                setattr(module, name, old)


@contextlib.contextmanager
def patch_all_modules(name, value):
    with contextlib.ExitStack() as stack:
        if name == "QIcon":
            stack.enter_context(mock.patch("PySide6.QtGui.QIcon", value))
        for module in repo_modules():
            stack.enter_context(mock.patch.object(module, name, value, create=True))
        yield


_QAPP = None


def qapp():
    global _QAPP
    if _QAPP is None:
        from PySide6.QtWidgets import QApplication

        _QAPP = QApplication.instance() or QApplication([])
    return _QAPP


class TempCwd(unittest.TestCase):
    def setUp(self):
        self._old_cwd = os.getcwd()
        self.tmpdir = tempfile.mkdtemp(prefix="autolayers_test_")
        os.chdir(self.tmpdir)

    def tearDown(self):
        os.chdir(self._old_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)


DEV1 = {"name": "kb1", "vid": 0x1234, "pid": 0x5678}
DEV2 = {"name": "kb2", "vid": 0x2345, "pid": 0x6789}
CONFIG1 = (
    "[general]\n"
    "last_device = kb1\n"
    "[kb1]\n"
    "vid = 0x1234\n"
    "pid = 0x5678\n"
    "layer_2 = {'a.exe', 'comma_separated.exe'}\n"
)


from PySide6.QtGui import QIcon as _RealQIcon


class FakeIcon(_RealQIcon):
    calls = []

    def __init__(self, path=None, *args, **kwargs):
        super().__init__(path)
        self.path = path
        FakeIcon.calls.append(path)


def build_app(testcase, devices=None, config=None):
    """Create an AutoLayers instance with run() disabled for the whole test."""
    devices = [dict(d) for d in (devices or [])]
    if config is not None:
        with open("config.ini", "w") as handle:
            handle.write(config)
    AutoLayers, _module = find("AutoLayers")
    send_calls = []
    holder = {"active": None, "send": None}

    def fake_send(data, vendor_id=0, product_id=0):
        send_calls.append((data, vendor_id, product_id))
        result = holder["send"]
        return result() if callable(result) else result

    def fake_active_window():
        return holder["active"]

    def fake_devices():
        return [dict(d) for d in devices]

    stack = contextlib.ExitStack()
    testcase.addCleanup(stack.close)
    for module in repo_modules():
        stack.enter_context(mock.patch.object(module, "list_qmk_devices", fake_devices, create=True))
        stack.enter_context(mock.patch.object(module, "send_raw_report", fake_send, create=True))
        stack.enter_context(mock.patch.object(module, "active_window_process_name", fake_active_window, create=True))
        stack.enter_context(mock.patch.object(module, "CONFIG_FOLDER", ".", create=True))
    stack.enter_context(mock.patch.object(AutoLayers, "run", lambda self: None))
    app = AutoLayers(qapp())
    return app, send_calls, holder


class TestStatesAndIcons(unittest.TestCase):
    def test_states_are_strenum_members_with_lowercase_values(self):
        States, _ = find("States")
        self.assertEqual(
            {member.name for member in States},
            {"RUNNING", "PAUSED", "BLOCKED", "GRABBING", "REMOVING", "QUITTING"},
        )
        for member in States:
            self.assertIsInstance(member, str)
            self.assertEqual(member, member.name.lower())

    def test_icons_are_strenum_paths(self):
        Icons, _ = find("Icons")
        expected = {
            "RUNNING": "icons/auto_layers_running.ico",
            "PAUSED": "icons/auto_layers_paused.ico",
            "BLOCKED": "icons/auto_layers_blocked.ico",
            "PAUSE_RESUME": "icons/auto_layers_pause_resume.ico",
            "GRAB": "icons/auto_layers_grab.ico",
            "REMOVE": "icons/auto_layers_remove.ico",
            "DEVICE": "icons/auto_layers_device.ico",
            "QUIT": "icons/auto_layers_quit.ico",
        }
        self.assertEqual({member.name for member in Icons}, set(expected))
        for name, path in expected.items():
            self.assertEqual(getattr(Icons, name), path)

    def test_default_block_list(self):
        block_list, _ = find("WE_ARE_NOT_FRIENDS")
        self.assertEqual(set(block_list), {"Vial.exe", "Vial", "VIA.exe"})


class TestMatches(unittest.TestCase):
    def test_layer_fields_requests_and_defaults(self):
        Matches, _ = find("Matches")
        request_ids, _ = find("REQUEST_IDS")
        matches = Matches()
        self.assertEqual([field.name for field in fields(matches)], [f"layer_{i}" for i in range(1, 10)])
        for i in range(1, 10):
            data = getattr(matches, f"layer_{i}")
            self.assertIsInstance(data, dict)
            self.assertEqual(data["apps"], set())
            self.assertEqual(data["request"], getattr(request_ids, f"id_layer_{i}"))
            self.assertEqual(data["request"], (0x30 + i,))

    def test_defaults_are_isolated_and_slots_used(self):
        Matches, _ = find("Matches")
        first, second = Matches(), Matches()
        first.layer_1["apps"].add("a.exe")
        self.assertEqual(second.layer_1["apps"], set())
        self.assertFalse(hasattr(first, "__dict__"))

    def test_asdict_keeps_all_layers(self):
        Matches, _ = find("Matches")
        data = asdict(Matches())
        self.assertEqual(set(data), {f"layer_{i}" for i in range(1, 10)})


class TestWindowHelpers(unittest.TestCase):
    def test_list_all_processes_returns_names(self):
        func, _ = find("list_all_processes")

        class Process:
            def __init__(self, name):
                self._name = name

            def name(self):
                return self._name

        import psutil

        with mock.patch.object(psutil, "process_iter", return_value=iter([Process("a.exe"), Process("b.exe")])):
            self.assertEqual(func(), ["a.exe", "b.exe"])

    def test_list_all_processes_error_returns_empty(self):
        func, _ = find("list_all_processes")
        import psutil

        with mock.patch.object(psutil, "process_iter", side_effect=RuntimeError("boom")):
            self.assertEqual(func(), [])

    def test_active_window_process_name_success(self):
        func, _ = find("active_window_process_name")
        import psutil
        import pywinctl

        class Window:
            def getPID(self):
                return 4321

        class Process:
            def name(self):
                return "editor.exe"

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(pywinctl, "getActiveWindow", return_value=Window()))
            stack.enter_context(mock.patch.object(psutil, "Process", return_value=Process()))
            self.assertEqual(func(), "editor.exe")

    def test_active_window_process_name_error_returns_none(self):
        func, _ = find("active_window_process_name")
        import pywinctl

        with mock.patch.object(pywinctl, "getActiveWindow", side_effect=RuntimeError("no window")):
            self.assertIsNone(func())


class TestWorker(unittest.TestCase):
    def test_worker_signals_declared(self):
        WorkerSignals, _ = find("WorkerSignals")
        signals = WorkerSignals()
        for name in ("finished", "update_devices", "icon_update", "layer_change", "block_check"):
            self.assertTrue(hasattr(signals, name), name)

    def test_worker_is_qrunnable(self):
        from PySide6.QtCore import QRunnable

        Worker, _ = find("Worker")
        self.assertIsInstance(Worker(lambda **kwargs: None), QRunnable)

    def test_worker_injects_signals_and_emits_finished(self):
        Worker, _ = find("Worker")
        seen = {}

        def fn(*args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs

        worker = Worker(fn, 1, 2, extra="x")
        finished = []
        worker.signals.finished.connect(lambda: finished.append(True))
        worker.run()
        self.assertEqual(seen["args"], (1, 2))
        self.assertEqual(seen["kwargs"]["extra"], "x")
        mapping = {
            "update_devices_signal": "update_devices",
            "icon_update_signal": "icon_update",
            "layer_change_signal": "layer_change",
            "block_check_signal": "block_check",
        }
        for key, signal_name in mapping.items():
            self.assertIs(seen["kwargs"][key], getattr(worker.signals, signal_name))
        self.assertEqual(finished, [True])

    def test_worker_swallows_exception_and_emits_finished(self):
        Worker, _ = find("Worker")

        def fn(**kwargs):
            raise ValueError("boom")

        worker = Worker(fn)
        finished = []
        worker.signals.finished.connect(lambda: finished.append(True))
        worker.run()
        self.assertEqual(finished, [True])


class FakeSignal:
    def __init__(self, side_effect=None):
        self.count = 0
        self.side_effect = side_effect

    def emit(self):
        self.count += 1
        if self.side_effect is not None:
            self.side_effect(self.count)


class TestAutoLayers(TempCwd):
    def test_init_without_config_pauses_with_no_device(self):
        from PySide6.QtCore import QThreadPool

        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1])
        self.assertEqual(app.name, "NONE")
        self.assertEqual(app.vid, -1)
        self.assertEqual(app.pid, -1)
        self.assertEqual(app.state, States.PAUSED)
        self.assertEqual(app.state_before_block, States.RUNNING)
        self.assertIsInstance(app.threadpool, QThreadPool)
        self.assertLessEqual({"Vial.exe", "Vial", "VIA.exe"}, set(app.block_list))

    def test_init_with_valid_last_device_resumes_and_loads_layers(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        self.assertEqual(app.name, "kb1")
        self.assertEqual(app.vid, 0x1234)
        self.assertEqual(app.pid, 0x5678)
        self.assertEqual(app.state, States.RUNNING)
        self.assertEqual(app.matches.layer_2["apps"], {"a.exe"})

    def test_init_with_unknown_device_pauses(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config="[general]\nlast_device = ghost\n")
        self.assertEqual(app.name, "NONE")
        self.assertEqual(app.state, States.PAUSED)

    def test_devices_menu_builds_and_rebuilds(self):
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        self.assertEqual([action.text() for action in app.devices_menu.actions()], ["No devices", "kb1"])
        self.assertFalse(app.no_devices_action.isVisible())
        with modules_attr("list_qmk_devices", lambda: []):
            app.update_devices()
            self.assertEqual([action.text() for action in app.devices_menu.actions()], ["No devices"])
            self.assertTrue(app.no_devices_action.isVisible())
        with modules_attr("list_qmk_devices", lambda: [dict(DEV2)]):
            app.update_devices()
            self.assertEqual([action.text() for action in app.devices_menu.actions()], ["No devices", "kb2"])
            self.assertFalse(app.no_devices_action.isVisible())

    def test_menu_actions_and_order(self):
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        self.assertEqual(
            [action.text() for action in app.menu.actions()],
            ["Pause/Resume", "Grab", "Remove", "Devices", "Quit"],
        )
        self.assertEqual(app.devices_menu.title(), "Devices")
        self.assertEqual(
            list(app.default_menu_actions),
            [app.pause_resume_action, app.grab_action, app.remove_action, app.quit_action],
        )

    def test_change_device_switches_and_loads(self):
        config = (
            "[general]\nlast_device = kb1\n"
            "[kb1]\nlayer_1 = {'a.exe'}\n"
            "[kb2]\nlayer_3 = {'b.exe'}\n"
        )
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1, DEV2], config=config)
        app.change_device(False, "kb2")
        self.assertEqual(app.name, "kb2")
        self.assertEqual(app.vid, DEV2["vid"])
        self.assertEqual(app.pid, DEV2["pid"])
        self.assertEqual(app.state, States.RUNNING)
        self.assertEqual(app.matches.layer_3["apps"], {"b.exe"})

    def test_last_device_does_not_override_explicit_choice(self):
        config = (
            "[general]\nlast_device = kb1\n"
            "[kb1]\nlayer_1 = {'a.exe'}\n"
            "[kb2]\nlayer_3 = {'b.exe'}\n"
        )
        app, _calls, holder = build_app(self, devices=[DEV1, DEV2], config=config)
        app.change_device(False, "kb2")
        app.load_config()
        self.assertEqual(app.name, "kb2")

    def test_pause_resume_toggle_from_every_state(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        self.assertEqual(app.state, States.RUNNING)
        app.pause_resume()
        self.assertEqual(app.state, States.PAUSED)
        app.pause_resume()
        self.assertEqual(app.state, States.RUNNING)
        for transient in (States.GRABBING, States.REMOVING, States.BLOCKED):
            app.state = transient
            app.pause_resume()
            self.assertEqual(app.state, States.PAUSED)

    def test_pause_resume_force(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.pause_resume(force=States.PAUSED)
        self.assertEqual(app.state, States.PAUSED)
        app.pause_resume(force=States.RUNNING)
        self.assertEqual(app.state, States.RUNNING)

    def test_pause_resume_updates_tray_icon(self):
        Icons, _ = find("Icons")
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        with patch_all_modules("QIcon", FakeIcon):
            FakeIcon.calls = []
            app.pause_resume(force=States.PAUSED)
            self.assertIn(Icons.PAUSED, FakeIcon.calls)
            FakeIcon.calls = []
            app.pause_resume(force=States.RUNNING)
            self.assertIn(Icons.RUNNING, FakeIcon.calls)

    def test_icon_update_maps_every_state(self):
        Icons, _ = find("Icons")
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        mapping = {
            States.RUNNING: Icons.RUNNING,
            States.PAUSED: Icons.PAUSED,
            States.BLOCKED: Icons.BLOCKED,
            States.GRABBING: Icons.GRAB,
            States.REMOVING: Icons.REMOVE,
            States.QUITTING: Icons.QUIT,
        }
        with patch_all_modules("QIcon", FakeIcon):
            for state, icon in mapping.items():
                FakeIcon.calls = []
                app.state = state
                app.icon_update()
                self.assertEqual(FakeIcon.calls[0], icon)

    def test_icon_update_marks_selected_device(self):
        Icons, _ = find("Icons")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        with patch_all_modules("QIcon", FakeIcon):
            FakeIcon.calls = []
            app.icon_update()
            self.assertIn(Icons.RUNNING, FakeIcon.calls)
            self.assertIn(Icons.DEVICE, FakeIcon.calls)

    def test_tray_activation(self):
        States, _ = find("States")
        from PySide6.QtWidgets import QSystemTrayIcon

        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.tray_activated(QSystemTrayIcon.DoubleClick)
        self.assertEqual(app.state, States.PAUSED)
        app.tray_activated(QSystemTrayIcon.Context)
        self.assertEqual(app.state, States.PAUSED)
        app.state = States.BLOCKED
        app.tray_activated(QSystemTrayIcon.DoubleClick)
        self.assertEqual(app.state, States.BLOCKED)

    def test_block_check_blocks_and_restores(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        self.assertEqual(app.state, States.RUNNING)
        with modules_attr("list_all_processes", lambda: ["Vial.exe"]):
            app.block_check()
        self.assertEqual(app.state, States.BLOCKED)
        self.assertEqual(app.state_before_block, States.RUNNING)
        for action in (app.pause_resume_action, app.grab_action, app.remove_action):
            self.assertFalse(action.isEnabled())
        with modules_attr("list_all_processes", lambda: []):
            app.block_check()
        self.assertEqual(app.state, States.RUNNING)
        for action in (app.pause_resume_action, app.grab_action, app.remove_action):
            self.assertTrue(action.isEnabled())

    def test_block_check_restores_paused(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.pause_resume(force=States.PAUSED)
        with modules_attr("list_all_processes", lambda: ["Vial.exe"]):
            app.block_check()
        self.assertEqual(app.state, States.BLOCKED)
        with modules_attr("list_all_processes", lambda: []):
            app.block_check()
        self.assertEqual(app.state, States.PAUSED)

    def test_block_check_active_window_list(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.block_if_active = {"z.exe"}
        holder["active"] = "z.exe"
        app.block_check()
        self.assertEqual(app.state, States.BLOCKED)
        holder["active"] = None
        app.block_check()
        self.assertEqual(app.state, States.RUNNING)

    def test_block_check_quit_is_noop(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.state = States.QUITTING
        app.pause_resume_action.setDisabled(True)
        app.block_check()
        self.assertEqual(app.state, States.QUITTING)
        self.assertFalse(app.pause_resume_action.isEnabled())

    def test_block_check_keeps_remembered_state_while_blocked(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.state = States.BLOCKED
        app.state_before_block = States.PAUSED
        with modules_attr("list_all_processes", lambda: ["Vial.exe"]):
            app.block_check()
        self.assertEqual(app.state, States.BLOCKED)
        self.assertEqual(app.state_before_block, States.PAUSED)
        with modules_attr("list_all_processes", lambda: []):
            app.block_check()
        self.assertEqual(app.state, States.PAUSED)

    def test_layer_change_none_layer_returns(self):
        app, calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        request_ids, _ = find("REQUEST_IDS")
        holder["send"] = None
        app.layer_change()
        self.assertEqual(calls, [(request_ids.id_current_layer, DEV1["vid"], DEV1["pid"])])

    def test_layer_change_no_active_window_returns(self):
        app, calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        request_ids, _ = find("REQUEST_IDS")
        holder["send"] = "3"
        holder["active"] = None
        app.layer_change()
        self.assertEqual(calls, [(request_ids.id_current_layer, DEV1["vid"], DEV1["pid"])])

    def test_layer_change_same_layer_no_request(self):
        app, calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        request_ids, _ = find("REQUEST_IDS")
        app.matches.layer_2["apps"] = {"a.exe"}
        holder["send"] = "2"
        holder["active"] = "a.exe"
        app.layer_change()
        self.assertEqual(calls, [(request_ids.id_current_layer, DEV1["vid"], DEV1["pid"])])

    def test_layer_change_matching_layer_sends_request(self):
        app, calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        request_ids, _ = find("REQUEST_IDS")
        app.matches.layer_2["apps"] = {"a.exe"}
        holder["send"] = "3"
        holder["active"] = "a.exe"
        app.layer_change()
        self.assertEqual(
            calls,
            [
                (request_ids.id_current_layer, DEV1["vid"], DEV1["pid"]),
                (app.matches.layer_2["request"], DEV1["vid"], DEV1["pid"]),
            ],
        )

    def test_layer_change_no_match_falls_back_to_layer_zero(self):
        app, calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        request_ids, _ = find("REQUEST_IDS")
        holder["send"] = "3"
        holder["active"] = "unknown.exe"
        app.layer_change()
        self.assertEqual(
            calls,
            [
                (request_ids.id_current_layer, DEV1["vid"], DEV1["pid"]),
                (request_ids.id_layer_0, DEV1["vid"], DEV1["pid"]),
            ],
        )

    def test_layer_change_no_match_on_layer_zero_sends_nothing(self):
        app, calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        request_ids, _ = find("REQUEST_IDS")
        holder["send"] = "0"
        holder["active"] = "unknown.exe"
        app.layer_change()
        self.assertEqual(calls, [(request_ids.id_current_layer, DEV1["vid"], DEV1["pid"])])

    def test_layer_change_first_match_wins(self):
        app, calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        request_ids, _ = find("REQUEST_IDS")
        app.matches.layer_1["apps"] = {"a.exe"}
        app.matches.layer_3["apps"] = {"a.exe"}
        holder["send"] = "3"
        holder["active"] = "a.exe"
        app.layer_change()
        self.assertEqual(
            calls,
            [
                (request_ids.id_current_layer, DEV1["vid"], DEV1["pid"]),
                (app.matches.layer_1["request"], DEV1["vid"], DEV1["pid"]),
            ],
        )

    def test_layer_change_ninth_layer(self):
        app, calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        request_ids, _ = find("REQUEST_IDS")
        app.matches.layer_2["apps"] = set()
        app.matches.layer_9["apps"] = {"a.exe"}
        holder["send"] = "8"
        holder["active"] = "a.exe"
        app.layer_change()
        self.assertEqual(
            calls,
            [
                (request_ids.id_current_layer, DEV1["vid"], DEV1["pid"]),
                (app.matches.layer_9["request"], DEV1["vid"], DEV1["pid"]),
            ],
        )

    def test_grab_moves_active_window_between_layers(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.matches.layer_1["apps"] = {"a.exe", "keep.exe"}
        app.matches.layer_3["apps"] = {"old.exe"}
        holder["active"] = "a.exe"
        holder["send"] = "3"
        seen_states = []
        app.save_config = mock.Mock(side_effect=lambda: seen_states.append(app.state))
        with mock.patch("time.sleep"):
            app.grab()
        self.assertEqual(app.matches.layer_1["apps"], {"keep.exe"})
        self.assertEqual(app.matches.layer_3["apps"], {"old.exe", "a.exe"})
        self.assertEqual(seen_states, [States.GRABBING])
        self.assertEqual(app.state, States.RUNNING)

    def test_grab_on_default_layer_is_noop(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.matches.layer_1["apps"] = {"a.exe"}
        holder["active"] = "a.exe"
        holder["send"] = "0"
        app.save_config = mock.Mock()
        with mock.patch("time.sleep"):
            app.grab()
        self.assertEqual(app.matches.layer_1["apps"], {"a.exe"})
        app.save_config.assert_not_called()
        self.assertEqual(app.state, States.RUNNING)

    def test_grab_without_layer_response_is_noop(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.matches.layer_1["apps"] = {"a.exe"}
        holder["active"] = "a.exe"
        holder["send"] = None
        app.save_config = mock.Mock()
        with mock.patch("time.sleep"):
            app.grab()
        self.assertEqual(app.matches.layer_1["apps"], {"a.exe"})
        app.save_config.assert_not_called()
        self.assertEqual(app.state, States.RUNNING)

    def test_grab_blocked_active_window_aborts(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.matches.layer_1["apps"] = {"Vial.exe"}
        holder["active"] = "Vial.exe"
        holder["send"] = "3"
        app.save_config = mock.Mock()
        with mock.patch("time.sleep"):
            app.grab()
        self.assertEqual(app.matches.layer_1["apps"], {"Vial.exe"})
        app.save_config.assert_not_called()
        self.assertEqual(app.state, States.RUNNING)

    def test_remove_drops_window_from_all_layers(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.matches.layer_1["apps"] = {"a.exe", "b.exe"}
        app.matches.layer_9["apps"] = {"b.exe"}
        holder["active"] = "b.exe"
        seen_states = []
        app.save_config = mock.Mock(side_effect=lambda: seen_states.append(app.state))
        with mock.patch("time.sleep"):
            app.remove()
        self.assertEqual(app.matches.layer_1["apps"], {"a.exe"})
        self.assertEqual(app.matches.layer_9["apps"], set())
        self.assertEqual(seen_states, [States.REMOVING])
        self.assertEqual(app.state, States.RUNNING)

    def test_quit_saves_and_marks_quitting(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.save_config = mock.Mock()
        app.quit()
        app.save_config.assert_called_once()
        self.assertEqual(app.state, States.QUITTING)

    def test_update_devices_signal_reloads(self):
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.update_devices = mock.Mock()
        app.load_config = mock.Mock()
        app.update_devices_signal()
        app.update_devices.assert_called_once()
        app.load_config.assert_called_once()

    def test_finished_signal_quits_application(self):
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.threadpool = mock.Mock()
        app.app = mock.Mock()
        app.finished_signal()
        app.threadpool.waitForDone.assert_called_once()
        app.app.quit.assert_called_once()


class TestConfig(TempCwd):
    def test_save_then_load_roundtrip(self):
        States, _ = find("States")
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.matches.layer_2["apps"].add("b.exe")
        app.matches.layer_9["apps"] = {"c.exe"}
        app.block_if_active = {"z.exe"}
        app.save_config()
        self.assertTrue(os.path.exists("config.ini"))
        second, _calls2, _holder2 = build_app(self, devices=[DEV1])
        self.assertEqual(second.name, "kb1")
        self.assertEqual(second.state, States.RUNNING)
        self.assertEqual(second.matches.layer_2["apps"], {"a.exe", "b.exe"})
        self.assertEqual(second.matches.layer_9["apps"], {"c.exe"})
        self.assertEqual(second.block_if_active, {"z.exe"})

    def test_save_config_file_layout(self):
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        app.matches.layer_4["apps"] = {"d.exe"}
        app.block_if_active = set()
        app.save_config()
        saved = ConfigParser()
        saved.read("config.ini")
        self.assertEqual(saved["general"]["last_device"], "kb1")
        self.assertEqual(saved["kb1"]["vid"], "0x1234")
        self.assertEqual(saved["kb1"]["pid"], "0x5678")
        self.assertEqual(eval(saved["kb1"]["layer_4"]), {"d.exe"})
        self.assertNotIn("layer_4", saved.sections())
        self.assertIn("comma_separated.exe", saved["general"]["block_if_active"])

    def test_save_empty_layers_uses_placeholder(self):
        app, _calls, holder = build_app(self, devices=[DEV1], config=CONFIG1)
        for i in range(1, 10):
            getattr(app.matches, f"layer_{i}")["apps"] = set()
        app.save_config()
        saved = ConfigParser()
        saved.read("config.ini")
        self.assertEqual(saved["kb1"]["layer_1"], "{'comma_separated.exe'}")
        self.assertEqual(saved["kb1"]["layer_9"], "{'comma_separated.exe'}")
        second, _calls2, _holder2 = build_app(self, devices=[DEV1])
        self.assertEqual(second.matches.layer_1["apps"], set())
        self.assertEqual(second.matches.layer_9["apps"], set())

    def test_load_unions_block_list_and_reads_block_if_active(self):
        config = (
            "[general]\n"
            "last_device = kb1\n"
            "block_list = {'extra.exe'}\n"
            "block_if_active = {'z.exe'}\n"
            "[kb1]\n"
            "layer_1 = {'a.exe'}\n"
        )
        app, _calls, holder = build_app(self, devices=[DEV1], config=config)
        self.assertLessEqual({"Vial.exe", "Vial", "VIA.exe", "extra.exe"}, set(app.block_list))
        self.assertEqual(app.block_if_active, {"z.exe"})

    def test_missing_device_section_resets_matches(self):
        config = "[general]\nlast_device = kb1\n"
        app, _calls, holder = build_app(self, devices=[DEV1], config=config)
        app.matches.layer_1["apps"].add("stale.exe")
        app.load_config()
        self.assertEqual(app.matches.layer_1["apps"], set())

    def test_bad_config_exits(self):
        buffer = io.StringIO()
        with mock.patch("sys.exit") as exit_mock, redirect_stdout(buffer):
            build_app(self, devices=[DEV1], config="[general]\nblock_list = notaset(\n")
        self.assertTrue(exit_mock.called)
        self.assertIn("Bad config.ini, ", buffer.getvalue())


class TestLoop(TempCwd):
    def _run_loop(self, states, devices=None):
        States, _ = find("States")
        loop = getattr(find("AutoLayers")[0], "loop")
        fake = types.SimpleNamespace(state=States.RUNNING, devices=list(devices or []), vid=0x11, pid=0x22)
        request_ids, _ = find("REQUEST_IDS")
        send_calls = []

        def fake_send(data, vendor_id=0, product_id=0):
            send_calls.append((data, vendor_id, product_id))
            return "0"

        def block_side_effect(count):
            fake.state = states[min(count, len(states)) - 1]

        block = FakeSignal(block_side_effect)
        layer = FakeSignal()
        icon = FakeSignal()
        update = FakeSignal()
        with modules_attr("list_qmk_devices", lambda: []), \
                modules_attr("send_raw_report", fake_send), \
                mock.patch("time.sleep"):
            loop(fake, update, icon, layer, block)
        return fake, block, layer, icon, update, send_calls, request_ids

    def test_loop_runs_full_cycle_then_quits_on_default_layer(self):
        States, _ = find("States")
        fake, block, layer, icon, update, send_calls, request_ids = self._run_loop([States.QUITTING])
        self.assertEqual(block.count, 1)
        self.assertEqual(layer.count, 1)
        self.assertEqual(icon.count, 1)
        self.assertEqual(update.count, 0)
        self.assertEqual(send_calls, [(request_ids.id_layer_0, fake.vid, fake.pid)])

    def test_loop_blocked_state_skips_layer_and_icon(self):
        States, _ = find("States")
        _fake, block, layer, icon, _update, _send_calls, _ids = self._run_loop(
            [States.BLOCKED, States.QUITTING]
        )
        self.assertEqual(block.count, 2)
        self.assertEqual(layer.count, 1)
        self.assertEqual(icon.count, 1)

    def test_loop_paused_state_skips_layer_change(self):
        States, _ = find("States")
        _fake, block, layer, icon, _update, _send_calls, _ids = self._run_loop(
            [States.PAUSED, States.QUITTING]
        )
        self.assertEqual(block.count, 2)
        self.assertEqual(layer.count, 1)
        self.assertEqual(icon.count, 2)

    def test_loop_detects_device_change(self):
        States, _ = find("States")
        loop = getattr(find("AutoLayers")[0], "loop")
        fake = types.SimpleNamespace(state=States.RUNNING, devices=[dict(DEV1)], vid=1, pid=2)
        counter = iter(range(0, 10000, 10))

        with modules_attr("list_qmk_devices", lambda: []), \
                modules_attr("send_raw_report", lambda *args, **kwargs: "0"), \
                mock.patch("time.sleep"), mock.patch("time.monotonic", side_effect=lambda: next(counter)):
            block = FakeSignal(lambda count: setattr(fake, "state", States.QUITTING))
            layer, icon, update = FakeSignal(), FakeSignal(), FakeSignal()
            loop(fake, update, icon, layer, block)
        self.assertEqual(update.count, 1)


if __name__ == "__main__":
    unittest.main()
