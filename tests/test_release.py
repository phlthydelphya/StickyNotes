# SPDX-License-Identifier: GPL-3.0-or-later
"""Release regressions: use temporary data and mocked application shutdown."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from stickynotes import app as app_module
from stickynotes.app import StickyApp
from stickynotes.hub_window import HubWindow
from stickynotes.store import GLib, LoadState, NoteStore


@pytest.fixture
def timers(monkeypatch):
    calls = []
    monkeypatch.setattr(GLib, 'timeout_add', lambda *a: calls.append(a) or len(calls))
    monkeypatch.setattr(GLib, 'timeout_add_seconds', lambda *a: calls.append(a) or len(calls))
    monkeypatch.setattr(GLib, 'source_remove', lambda *_: None)
    return calls


def populated(tmp_path, data):
    store = NoteStore(tmp_path)
    store.images_dir.mkdir()
    store.image_path('keep.png').write_bytes(b'keep')
    store.file.write_text(json.dumps(data))
    return store


def test_newer_is_read_only(tmp_path, timers):
    store = populated(tmp_path, {'version': 99})
    before = store.file.read_bytes()
    assert store.load() is LoadState.NEWER
    assert store.read_only
    with pytest.raises(OSError):
        store.save_now()
    store.schedule_save()
    assert not timers
    store.gc_images()
    assert store.file.read_bytes() == before
    assert store.image_path('keep.png').exists()


@pytest.mark.parametrize('data', [
    {'version': 1},
    {'version': 1, 'notes': [{'content': []}]},
    {'version': 1, 'notes': [{'id': 'x', 'content': 'bad'}]},
    {'version': 1, 'notes': [{'id': 'x', 'content': [{'img': '../x'}]}]},
    {'version': True, 'notes': []},
    {'version': 1, 'notes': [{'id': 'x', 'content': [{'t': 'hi', 's': 'bold'}]}]},
])
def test_corrupt_keeps_images(tmp_path, data):
    store = populated(tmp_path, data)
    before = store.file.read_bytes()
    assert store.load() is LoadState.CORRUPT
    assert not store.notes
    assert list(tmp_path.glob('notes.json.corrupt-*'))[0].read_bytes() == before
    store.gc_images()
    assert store.image_path('keep.png').exists()


def test_delete_save_failure_keeps_images_and_retries(tmp_path, monkeypatch, timers):
    store = populated(tmp_path, {'version': 1, 'notes': [
        {'id': 'x', 'content': [{'img': 'keep.png'}]}]})
    assert store.load() is LoadState.OK
    def fail():
        raise OSError('disk full')
    monkeypatch.setattr(store, 'save_now', fail)
    assert store.try_save() is False
    assert timers[0][0] == 5
    store.delete('x')
    store.gc_images()
    assert store.unsaved and store.last_error == 'disk full'
    assert store.image_path('keep.png').exists()
    assert len(timers) == 1


class Dialog:
    instances = []
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.responses = {}
        self.instances.append(self)
    def add_response(self, name, label):
        self.responses[name] = label
    def set_response_appearance(self, *a): pass
    def set_default_response(self, *a): pass
    def set_close_response(self, *a): pass
    def connect(self, _, callback): self.callback = callback
    def present(self, parent): self.parent = parent


def fake_app(store):
    events = []
    obj = SimpleNamespace(store=store, note_windows={}, _shutdown_saved=False,
                          _save_dialog=None, exit_status=0,
                          quit=lambda: events.append('quit'),
                          get_active_window=lambda: object())
    for name in ('_flush_all', 'quit_saving', '_on_save_response', '_save_for_shutdown'):
        setattr(obj, name, lambda *args, name=name: getattr(StickyApp, name)(obj, *args))
    return obj, events


def test_failed_quit_dialog_retry_and_cancel(tmp_path, monkeypatch, timers):
    store = NoteStore(tmp_path)
    store.create().content = [{'t': 'unsaved'}]
    original = store.save_now
    def fail(): raise OSError('disk full')
    monkeypatch.setattr(store, 'save_now', fail)
    monkeypatch.setattr(app_module.Adw, 'AlertDialog', Dialog)
    obj, events = fake_app(store)
    assert obj._flush_all() is False
    obj.quit_saving()
    assert not events
    dialog = obj._save_dialog
    assert 'disk full' in dialog.kwargs['heading']
    assert dialog.responses == {'cancel': 'Cancel', 'retry': 'Retry', 'quit': 'Quit Anyway'}
    dialog.callback(dialog, 'cancel')
    assert not events
    obj.quit_saving()
    dialog = obj._save_dialog
    monkeypatch.setattr(store, 'save_now', original)
    dialog.callback(dialog, 'retry')
    assert events == ['quit']
    assert not store.unsaved


@pytest.mark.parametrize('emergency_fails', [False, True])
def test_shutdown_fallback(tmp_path, monkeypatch, timers, capsys, emergency_fails):
    store = NoteStore(tmp_path)
    store.create().content = [{'t': 'recover me'}]
    def fail(): raise OSError('primary full')
    monkeypatch.setattr(store, 'save_now', fail)
    if emergency_fails:
        def fail_emergency(): raise OSError('backup full')
        monkeypatch.setattr(store, 'save_emergency', fail_emergency)
    obj, events = fake_app(store)
    StickyApp._on_sigterm(obj)
    assert events == ['quit']
    output = capsys.readouterr().err
    assert 'primary full' in output
    if emergency_fails:
        assert obj.exit_status == 1 and 'backup full' in output
    else:
        saved = list(tmp_path.glob('notes.json.unsaved-*'))
        assert len(saved) == 1
        assert json.loads(saved[0].read_text())['notes'][0]['content'] == [{'t': 'recover me'}]
        assert obj.exit_status == 0
    obj._save_for_shutdown()  # shutdown after SIGTERM must not save twice
    assert len(list(tmp_path.glob('notes.json.unsaved-*'))) == (0 if emergency_fails else 1)


def test_shutdown_virtual_method_uses_fallback(monkeypatch):
    calls = []
    obj = SimpleNamespace(_save_for_shutdown=lambda: calls.append('saved'))
    monkeypatch.setattr(app_module.Adw.Application, 'do_shutdown', lambda _: calls.append('parent'))
    StickyApp.do_shutdown(obj)
    assert calls == ['saved', 'parent']


def test_main_returns_failure(monkeypatch):
    monkeypatch.setattr(app_module, 'StickyApp', lambda: SimpleNamespace(run=lambda _: 0, exit_status=1))
    assert app_module.main() == 1


def test_unsaved_title(tmp_path, timers):
    store = NoteStore(tmp_path)
    subtitles = []
    hub = SimpleNamespace(store=store, _closed=False, window_title=SimpleNamespace(set_subtitle=subtitles.append))
    store.connect_save_state(lambda: HubWindow._update_save_state(hub))
    store.create()
    assert subtitles[-1] == 'Not saved'
    store.try_save()
    assert subtitles[-1] == ''


def test_newer_activation_never_opens_notes():
    events = []
    obj = SimpleNamespace(store=SimpleNamespace(read_only=True), _show_newer=lambda: events.append('alert'),
                          _restore=lambda: events.append('restore'), show_list=lambda: events.append('list'))
    StickyApp.do_activate(obj)
    StickyApp.do_command_line(obj, None)
    assert events == ['alert', 'alert']


def test_installer_preserves_working_copy(tmp_path):
    source = Path(__file__).resolve().parents[1]
    checkout = tmp_path / 'source'
    checkout.mkdir()
    for name in ('stickynotes', 'data'):
        shutil.copytree(source / name, checkout / name)
    for name in ('install.sh', 'dusk-sticky-notes'):
        shutil.copy2(source / name, checkout / name)
    home = tmp_path / 'home'
    home.mkdir()
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
               XDG_DATA_HOME=str(home / '.local/share'), PYTHONDONTWRITEBYTECODE='1')
    notes = home / '.local/share/dusk-sticky-notes/notes.json'
    notes.parent.mkdir(parents=True)
    notes.write_text('precious notes')
    def install(*args):
        return subprocess.run(['bash', str(checkout / 'install.sh'), *args], env=env,
                              capture_output=True, text=True)
    def runs():
        return subprocess.run([str(home / '.local/bin/dusk-sticky-notes'), '--help'], env=env,
                              capture_output=True, text=True).returncode == 0
    assert install().returncode == 0
    assert runs()
    module = checkout / 'stickynotes/app.py'
    module.write_text(module.read_text() + '\n# upgraded\n')
    assert install('--autostart').returncode == 0
    installed = home / '.local/lib/dusk-sticky-notes/stickynotes/app.py'
    before = installed.read_bytes()
    assert b'# upgraded' in before
    assert runs()
    # A failure after switching the library must also restore the old version.
    fake_bin = tmp_path / 'fake-bin'
    fake_bin.mkdir()
    failing_ln = fake_bin / 'ln'
    failing_ln.write_text('#!/bin/sh\nexit 42\n')
    failing_ln.chmod(0o755)
    old_path = env['PATH']
    env['PATH'] = str(fake_bin) + os.pathsep + old_path
    module.write_text(module.read_text() + '\n# must roll back\n')
    assert install().returncode == 42
    env['PATH'] = old_path
    assert installed.read_bytes() == before and runs()
    module.write_text('raise RuntimeError("forced broken stage")\n')
    result = install()
    assert result.returncode != 0 and 'forced broken stage' in result.stderr
    assert installed.read_bytes() == before and runs()
    assert notes.read_text() == 'precious notes'
    assert not list(installed.parents[1].glob('.stage-*'))
    assert install('--uninstall').returncode == 0
    assert notes.read_text() == 'precious notes'


def test_quit_anyway_uses_emergency_copy(tmp_path, monkeypatch, timers):
    store = NoteStore(tmp_path)
    store.create().content = [{'t': 'keep'}]
    def fail(): raise OSError('full')
    monkeypatch.setattr(store, 'save_now', fail)
    monkeypatch.setattr(app_module.Adw, 'AlertDialog', Dialog)
    obj, events = fake_app(store)
    obj.quit_saving()
    dialog = obj._save_dialog
    dialog.callback(dialog, 'quit')
    assert events == ['quit']
    assert len(list(tmp_path.glob('notes.json.unsaved-*'))) == 1


def test_newer_dialog_quits_without_saving(tmp_path, monkeypatch):
    store = populated(tmp_path, {'version': 99})
    before = store.file.read_bytes()
    store.load()
    events = []
    window = SimpleNamespace(present=lambda: events.append('present'))
    monkeypatch.setattr(app_module.Adw, 'ApplicationWindow', lambda **_: window)
    monkeypatch.setattr(app_module.Adw, 'AlertDialog', Dialog)
    obj, quit_events = fake_app(store)
    obj._newer_window = None
    StickyApp._show_newer(obj)
    dialog = Dialog.instances[-1]
    assert dialog.kwargs['heading'] == 'Notes were saved by a newer version'
    dialog.callback(dialog, 'close')
    obj._save_for_shutdown()
    assert quit_events == ['quit']
    assert store.file.read_bytes() == before
    assert not list(tmp_path.glob('notes.json.unsaved-*'))


def test_recovery_files_preserved_and_disclosed(tmp_path, monkeypatch):
    store = populated(tmp_path, {'version': 1, 'notes': []})
    recovery = tmp_path / 'notes.json.unsaved-123'
    recovery.write_text('{"version": 1, "notes": []}')
    store.load()
    store.gc_images()
    assert store.image_path('keep.png').exists()
    assert store.recovery_files() == [recovery]
    monkeypatch.setattr(app_module.Adw, 'AlertDialog', Dialog)
    hub = SimpleNamespace(store=store)
    HubWindow._show_recovery_notice(hub)
    assert str(recovery) in Dialog.instances[-1].kwargs['body']
    assert recovery.read_text() == '{"version": 1, "notes": []}'
