# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""The AI assistant: a tab of the right sidebar, and its Options page
(which also switches the MCP bridge, core/ai/bridge.py).

Off by default. While it is off there is no tab, and nothing of core.ai is
imported (a test watches that): the window reads one setting.

A turn runs on a worker thread, which talks to the provider; every tool
the model asks for is handed to the GUI thread (``tool_request``), run
there through core.ai.tools -- the same Commands as the keyboard -- and
its result handed back. The whole turn is one undo step. Stop ends it at
the next step; closing the window stops it and waits.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr

SETTING_ON = "ai/assistant"
SETTING_MCP = "ai/mcp"
SETTING_PROVIDER = "ai/provider"
SETTING_MODEL = "ai/model"
SETTING_BASE_URL = "ai/base_url"
#: per provider: the user has read what is sent to it
SETTING_PRIVACY_OK = "ai/privacy_ok/{id}"


def _flag(key: str, default: bool = False) -> bool:
    raw = QSettings().value(key, None)
    return default if raw is None else str(raw).lower() in ("true", "1", "yes")


def enabled() -> bool:
    return _flag(SETTING_ON)


def mcp_enabled() -> bool:
    return _flag(SETTING_MCP)


def current_provider():
    """(provider, model, base_url) as the user set them."""
    from core.ai.providers import PROVIDERS

    settings = QSettings()
    provider = PROVIDERS.get(str(settings.value(SETTING_PROVIDER, "anthropic")),
                             PROVIDERS["anthropic"])
    model = str(settings.value(SETTING_MODEL, "") or "") or provider.model
    base_url = str(settings.value(SETTING_BASE_URL, "") or "")
    return provider, model, base_url


class _TurnWorker(QThread):
    """One turn with the provider, off the GUI thread."""

    text = Signal(str)
    tool_request = Signal(str, object)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, provider, model, key, history, prompt, base_url) -> None:
        super().__init__()
        self.setObjectName("ai-turn")
        self._args = (provider, model, key, history, prompt, base_url)
        self._answer = threading.Event()
        self._result = None
        self._stop = False

    def run(self) -> None:
        from core import gc_guard

        with gc_guard.paused():
            self._run()

    def _run(self) -> None:
        from core.ai.providers import ProviderError, run_turn

        provider, model, key, history, prompt, base_url = self._args
        try:
            reason = run_turn(provider, model, key, history, prompt,
                              self._execute, base_url=base_url,
                              stopped=lambda: self._stop,
                              on_text=self.text.emit)
        except ProviderError as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:          # noqa: BLE001 -- shown, never fatal
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.done.emit(reason)

    def _execute(self, name: str, args: dict):
        """Ask the GUI thread to run the tool; wait for its answer."""
        if self._stop:
            return {"error": "the user stopped the assistant"}
        self._answer.clear()
        self._result = None
        self.tool_request.emit(name, args)
        self._answer.wait()
        return self._result

    def answer(self, result) -> None:
        self._result = result
        self._answer.set()

    def stop(self) -> None:
        """Stop at the next step -- and never leave the thread waiting for
        a tool the window will not run any more."""
        self._stop = True
        if self._result is None:
            self._result = {"error": "the user stopped the assistant"}
        self._answer.set()


class AssistantPanel(QWidget):
    """The Assistant tab: the conversation, a line to type, Send, Stop."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.history: list = []
        self._worker = None
        self._turn = None
        layout = QVBoxLayout(self)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.log = QTextBrowser(self)
        self.log.setOpenExternalLinks(False)
        layout.addWidget(self.log, 1)
        self.input = QLineEdit(self)
        self.input.setPlaceholderText(tr("Ask the assistant..."))
        self.input.returnPressed.connect(self._send_typed)
        layout.addWidget(self.input)
        row = QHBoxLayout()
        self.send_button = QPushButton(tr("Send"), self)
        self.send_button.clicked.connect(self._send_typed)
        self.stop_button = QPushButton(tr("Stop"), self)
        self.stop_button.clicked.connect(self.stop)
        self.clear_button = QPushButton(tr("New conversation"), self)
        self.clear_button.clicked.connect(self.clear)
        for button in (self.send_button, self.stop_button, self.clear_button):
            row.addWidget(button)
        layout.addLayout(row)
        self._set_busy(False)
        self.refresh()

    # -- asking the user (methods, so tests can answer) ---------------------------
    def ask_privacy(self, provider) -> bool:
        answer = QMessageBox.question(
            self, tr("AI Assistant"),
            tr("Your requests go to {provider}, together with what the "
               "assistant reads to answer them: layer names, the entities it "
               "looks up and pictures of the view. Continue?",
               provider=provider.label))
        return answer == QMessageBox.Yes

    def confirm(self, question: str) -> bool:
        answer = QMessageBox.question(self, tr("AI Assistant"),
                                      question + "\n\n" + tr("Allow it?"))
        return answer == QMessageBox.Yes

    # -- the conversation ------------------------------------------------------------
    def refresh(self) -> None:
        provider, model, _base = current_provider()
        self.status.setText(tr("{provider} · {model}", provider=provider.label,
                               model=model or "?"))

    def _say(self, who: str, text: str, color: str = "") -> None:
        from html import escape

        style = f' style="color:{color}"' if color else ""
        body = escape(text).replace("\n", "<br>")
        self.log.append(f"<p{style}><b>{escape(who)}</b> {body}</p>")

    def _set_busy(self, busy: bool) -> None:
        self.send_button.setEnabled(not busy)
        self.input.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.clear_button.setEnabled(not busy)

    def busy(self) -> bool:
        return self._worker is not None

    def _send_typed(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.send(text)

    def send(self, text: str) -> bool:
        """Start a turn with ``text``. False when it could not start."""
        from core.ai import secrets
        from core.ai import tools as ai

        if self.busy():
            return False
        if self.window.document is None:
            self.window.new_document()
        provider, model, base_url = current_provider()
        key = secrets.load(provider.id) or ""
        if provider.needs_key and not key:
            self._say(tr("Assistant:"), tr(
                "There is no API key for {provider}. Set it in Options > "
                "AI Assistant.", provider=provider.label), "#d9534f")
            return False
        privacy = SETTING_PRIVACY_OK.format(id=provider.id)
        if not provider.local and not _flag(privacy):
            if not self.ask_privacy(provider):
                return False
            QSettings().setValue(privacy, True)
        self._say(tr("You:"), text)
        self._turn = ai.Turn(self.window, tr("AI"))
        self._turn.__enter__()
        worker = _TurnWorker(provider, model, key, self.history, text, base_url)
        worker.text.connect(self._on_text)
        worker.tool_request.connect(self._on_tool_request)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        self._set_busy(True)
        from core import gc_guard

        gc_guard.start(worker)
        return True

    def _on_text(self, text: str) -> None:
        self._say(tr("Assistant:"), text)

    def _on_tool_request(self, name: str, args) -> None:
        from core.ai import tools as ai

        worker = self._worker
        if worker is None:
            return
        if self.window.document is None:
            worker.answer({"error": "no drawing is open"})
            return
        self.log.append(f'<p style="color:#8a8f98">· {name}</p>')
        worker.answer(ai.call(self.window, name, dict(args or {}),
                              confirm=self.confirm))

    def _finish(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.wait()
        if self._turn is not None:
            self._turn.__exit__(None, None, None)
            self._turn = None
        self._set_busy(False)

    def _on_done(self, reason: str) -> None:
        self._finish()
        note = {"length": tr("(The answer was cut off.)"),
                "refusal": tr("(The model declined to answer.)"),
                "steps": tr("(Stopped after too many steps.)"),
                "stopped": tr("(Stopped.)")}.get(reason)
        if note:
            self._say("", note, "#8a8f98")

    def _on_failed(self, message: str) -> None:
        self._finish()
        self._say(tr("Assistant:"), message, "#d9534f")

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.stop()

    def clear(self) -> None:
        if not self.busy():
            self.history.clear()
            self.log.clear()

    def shutdown(self) -> None:
        """The window is closing: stop the turn and join its thread."""
        worker = self._worker
        if worker is not None:
            try:
                worker.tool_request.disconnect(self._on_tool_request)
            except (RuntimeError, TypeError):
                pass
            worker.stop()
            if not worker.wait(200):
                worker.terminate()
                worker.wait(50)
            self._worker = None
            if self._turn is not None:
                self._turn.__exit__(None, None, None)
                self._turn = None


class AiOptionsPage(QWidget):
    """Options > AI Assistant: on/off, provider, model, server, key."""

    def __init__(self, dialog, window) -> None:
        from core.ai.providers import PROVIDERS

        super().__init__(dialog)
        self.window = window
        form = QFormLayout(self)
        self.assistant_on = QCheckBox(tr("Turn on the AI assistant"), self)
        self.assistant_on.setChecked(enabled())
        form.addRow(self.assistant_on)
        note = QLabel(tr("Off by default. The assistant draws and edits "
                         "through the same commands as the keyboard; "
                         "everything it does in one answer is undone with "
                         "a single U."), self)
        note.setWordWrap(True)
        form.addRow(note)
        provider, model, base_url = current_provider()
        self.provider = QComboBox(self)
        for p in PROVIDERS.values():
            self.provider.addItem(tr(p.label), p.id)
        self.provider.setCurrentIndex(self.provider.findData(provider.id))
        self.provider.currentIndexChanged.connect(self._provider_changed)
        form.addRow(tr("Provider:"), self.provider)
        self.model = QLineEdit(self)
        self.model.setText(str(QSettings().value(SETTING_MODEL, "") or ""))
        form.addRow(tr("Model:"), self.model)
        self.base_url = QLineEdit(self)
        self.base_url.setText(base_url)
        form.addRow(tr("Server URL:"), self.base_url)
        self.key = QLineEdit(self)
        self.key.setEchoMode(QLineEdit.Password)
        keys = QHBoxLayout()
        keys.addWidget(self.key, 1)
        self.save_key = QPushButton(tr("Save key"), self)
        self.save_key.clicked.connect(self._save_key)
        self.forget_key = QPushButton(tr("Forget key"), self)
        self.forget_key.clicked.connect(self._forget_key)
        keys.addWidget(self.save_key)
        keys.addWidget(self.forget_key)
        form.addRow(tr("API key:"), keys)
        self.key_note = QLabel(self)
        self.key_note.setWordWrap(True)
        form.addRow(self.key_note)
        self.mcp_on = QCheckBox(tr("Serve the drawing to MCP clients "
                                   "(MCPSTART / MCPSTOP)"), self)
        self.mcp_on.setChecked(mcp_enabled())
        form.addRow(self.mcp_on)
        mcp_note = QLabel(tr("Lets an AI program on this computer (Claude "
                             "Desktop, Claude Code and others) work on the "
                             "open drawing, through a local socket only "
                             "you can use. Add IngeCAD to the program with "
                             "this configuration:"), self)
        mcp_note.setWordWrap(True)
        form.addRow(mcp_note)
        self.copy_config = QPushButton(tr("Copy configuration"), self)
        self.copy_config.clicked.connect(self._copy_config)
        form.addRow(self.copy_config)
        self._provider_changed()

    def _selected(self):
        from core.ai.providers import PROVIDERS

        return PROVIDERS[self.provider.currentData()]

    def _provider_changed(self, *_args) -> None:
        # the keyring is not asked here: opening Options must never pop up
        # an "unlock your keyring" prompt for an assistant that is off
        p = self._selected()
        self.model.setPlaceholderText(p.model)
        self.base_url.setPlaceholderText(p.base_url or "http://...")
        self.key.clear()
        self.key.setPlaceholderText("")
        if p.local:
            self.key_note.setText(tr("Runs on this computer: nothing is sent "
                                     "over the internet."))
        else:
            self.key_note.setText("")

    def _save_key(self) -> None:
        from core.ai import secrets

        value = self.key.text().strip()
        if not value:
            return
        try:
            where = secrets.store(self._selected().id, value)
        except secrets.SecretsError as exc:
            self.key_note.setText(tr("Could not keep the key: {error}",
                                     error=str(exc)))
            return
        self.key.clear()
        self.key.setPlaceholderText(tr("saved"))
        self.key_note.setText(
            tr("Saved in the system keyring.") if where != secrets.FILE else
            tr("The system keyring is not available: the key was saved in a "
               "file only you can read."))

    def _copy_config(self) -> None:
        from PySide6.QtWidgets import QApplication

        from core.ai.mcp_server import client_config

        QApplication.clipboard().setText(client_config())
        self.window.command_line.echo(tr("Copied to clipboard."))

    def _forget_key(self) -> None:
        from core.ai import secrets

        secrets.forget(self._selected().id)
        self.key.setPlaceholderText("")
        self.key_note.setText(tr("Key forgotten."))

    def apply(self) -> None:
        settings = QSettings()
        p = self._selected()
        settings.setValue(SETTING_ON, self.assistant_on.isChecked())
        settings.setValue(SETTING_MCP, self.mcp_on.isChecked())
        settings.setValue(SETTING_PROVIDER, p.id)
        settings.setValue(SETTING_MODEL, self.model.text().strip())
        settings.setValue(SETTING_BASE_URL, self.base_url.text().strip())
        if self.key.text().strip():
            self._save_key()
        for name in ("sync_assistant", "sync_mcp"):
            sync = getattr(self.window, name, None)
            if sync is not None:
                sync()
