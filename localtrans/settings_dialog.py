from __future__ import annotations

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .config import Config
from .hotkeys import parse_hotkey


def _key_edit(text: str) -> QKeySequenceEdit:
    e = QKeySequenceEdit(QKeySequence(text))
    e.setMaximumSequenceLength(1)
    e.setClearButtonEnabled(True)
    return e


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.resize(520, 640)
        self.cfg = cfg
        lay = QVBoxLayout(self)

        hk = QGroupBox("ショートカットキー (ゲーム中でも有効)")
        f = QFormLayout(hk)
        self.k_select = _key_edit(cfg.hotkey_select)
        self.k_repeat = _key_edit(cfg.hotkey_repeat)
        self.k_auto = _key_edit(cfg.hotkey_auto)
        self.k_compact = _key_edit(cfg.hotkey_compact)
        f.addRow("範囲を選んで翻訳", self.k_select)
        f.addRow("選んでいる範囲で翻訳", self.k_repeat)
        f.addRow("選んでいる範囲を自動監視 ON/OFF", self.k_auto)
        f.addRow("最小表示 ON/OFF", self.k_compact)
        lay.addWidget(hk)

        tr = QGroupBox("翻訳")
        f = QFormLayout(tr)
        self.form = QComboBox()
        self.form.addItem("常体 (だ・である)", "plain")
        self.form.addItem("敬体 (です・ます)", "polite")
        self.form.setCurrentIndex(max(0, self.form.findData(cfg.form)))
        f.addRow("訳文の文体", self.form)
        self.use_ctx = QCheckBox("直前の原文を文脈として使う (会話の流れを踏まえて訳す)")
        self.use_ctx.setChecked(cfg.use_context)
        f.addRow(self.use_ctx)
        self.ctx_size = QSpinBox()
        self.ctx_size.setRange(1, 10)
        self.ctx_size.setValue(cfg.context_size)
        f.addRow("文脈に使う段落数", self.ctx_size)
        self.device = QComboBox()
        self.device.setEditable(True)
        self.device.addItems(["cuda:0", "cuda:1", "cpu"])
        self.device.setCurrentText(cfg.device)
        f.addRow("実行デバイス (再起動後に反映)", self.device)
        lay.addWidget(tr)

        ui = QGroupBox("表示・自動監視")
        f = QFormLayout(ui)
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 40)
        self.font_size.setValue(cfg.font_size)
        f.addRow("文字サイズ", self.font_size)
        self.interval = QSpinBox()
        self.interval.setRange(300, 10000)
        self.interval.setSingleStep(100)
        self.interval.setSuffix(" ms")
        self.interval.setValue(cfg.auto_interval_ms)
        f.addRow("自動監視の間隔", self.interval)
        self.c_font = QSpinBox()
        self.c_font.setRange(8, 48)
        self.c_font.setValue(cfg.compact_font_size)
        f.addRow("最小表示の文字サイズ", self.c_font)
        self.c_opacity = QSpinBox()
        self.c_opacity.setRange(0, 100)
        self.c_opacity.setSingleStep(5)
        self.c_opacity.setSuffix(" %")
        self.c_opacity.setValue(cfg.compact_opacity)
        f.addRow("最小表示の背景の濃さ", self.c_opacity)
        lay.addWidget(ui)

        gl = QGroupBox("用語集 (固有名詞などの訳し方を固定)")
        v = QVBoxLayout(gl)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["英語", "日本語"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for g in cfg.glossary:
            self._add_row(g.get("source", ""), g.get("target", ""))
        v.addWidget(self.table)
        row = QHBoxLayout()
        add, rm = QPushButton("行を追加"), QPushButton("選択行を削除")
        add.clicked.connect(lambda: self._add_row("", ""))
        rm.clicked.connect(self._remove_rows)
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch()
        v.addLayout(row)
        lay.addWidget(gl)

        self.err = QLabel()
        self.err.setStyleSheet("color: #d33")
        lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add_row(self, s: str, t: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(s))
        self.table.setItem(r, 1, QTableWidgetItem(t))

    def _remove_rows(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def _accept(self) -> None:
        keys = {}
        for name, edit in (("hotkey_select", self.k_select), ("hotkey_repeat", self.k_repeat),
                           ("hotkey_auto", self.k_auto), ("hotkey_compact", self.k_compact)):
            text = edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
            if text:
                try:
                    parse_hotkey(text)
                except ValueError as e:
                    self.err.setText(str(e))
                    return
            keys[name] = text
        if len({v for v in keys.values() if v}) != len([v for v in keys.values() if v]):
            self.err.setText("同じショートカットキーが重複しています")
            return
        for k, v in keys.items():
            setattr(self.cfg, k, v)
        self.cfg.form = self.form.currentData()
        self.cfg.use_context = self.use_ctx.isChecked()
        self.cfg.context_size = self.ctx_size.value()
        self.cfg.device = self.device.currentText().strip() or "cuda:0"
        self.cfg.font_size = self.font_size.value()
        self.cfg.auto_interval_ms = self.interval.value()
        self.cfg.compact_font_size = self.c_font.value()
        self.cfg.compact_opacity = self.c_opacity.value()
        gl = []
        for r in range(self.table.rowCount()):
            s = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            t = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            if s and t:
                gl.append({"source": s, "target": t})
        self.cfg.glossary = gl
        self.accept()
