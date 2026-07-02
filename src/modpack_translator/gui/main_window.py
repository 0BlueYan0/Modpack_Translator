from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QSize, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# src/modpack_translator/gui/ → 上 4 層到專案根目錄
_PROJECT_ROOT = Path(__file__).parents[3]
_APP_ICON_PATH = _PROJECT_ROOT / "assets" / "icon" / "app_icon.png"

from modpack_translator.config import load_config
from modpack_translator.gui.theme import apply_theme, eye_icon, restyle
from modpack_translator.gui.worker import ScanWorker, TranslateWorker
from modpack_translator.gui.stats import build_stats_text
from modpack_translator.version import APP_NAME, APP_VERSION, __version__
from scripts.updater import UpdateInfo, check_for_update, download_update, launch_apply_update


def _make_help_label(tooltip_text: str) -> QPushButton:
    btn = QPushButton("?")
    btn.setObjectName("helpButton")
    btn.setFixedSize(22, 22)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setCursor(Qt.CursorShape.WhatsThisCursor)
    btn.setToolTip(tooltip_text)
    return btn


_FMT_NAME_MAP: dict[str, str] = {
    "json_lang":            "JSON 語言檔",
    "legacy_lang":          "舊式 .lang 檔",
    "patchouli_json":       "Patchouli 書頁",
    "ftbq_snbt":            "FTB 任務 SNBT",
    "ftbq_inline_snbt":     "FTB 任務 inline SNBT",
    "heracles_snbt":        "Heracles 任務 SNBT",
    "heracles_inline_snbt": "Heracles inline SNBT",
    "bq_lang":              "Better Questing lang",
    "kubejs_json":          "KubeJS JSON",
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}{APP_VERSION}")
        if _APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(_APP_ICON_PATH)))
        self.setMinimumWidth(760)
        # 最小高度需容納完整版面（含 log 區的最小高度），否則底部輸出會被裁切
        self.setMinimumHeight(820)
        self.resize(900, 880)

        self._scan_targets: list = []
        self._scan_fmt_counts: dict = {}
        self._scan_total_pairs: int = 0
        self._translate_worker: TranslateWorker | None = None
        self._scan_worker: ScanWorker | None = None
        self._update_check_worker: UpdateCheckWorker | None = None
        self._update_download_worker: UpdateDownloadWorker | None = None
        self._conn_test_worker = None

        self._translated_modpack_path: str = ""
        self._translation_start_time: float = 0.0
        self._translation_total: int = 0
        self._current_progress: int = 0
        self._pairs_done: int = 0
        self._translation_cancelled: bool = False
        # 滑動視窗速度計算：(timestamp, cumulative_pairs) 最近 500 筆
        self._speed_samples: deque = deque(maxlen=500)

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_stats_label)

        # 60 秒逾時強制停止（safety net）
        self._force_stop_timer = QTimer(self)
        self._force_stop_timer.setSingleShot(True)
        self._force_stop_timer.setInterval(60_000)
        self._force_stop_timer.timeout.connect(self._force_stop_worker)

        self._cfg = None
        try:
            self._cfg = load_config(
                _PROJECT_ROOT / "configs" / "model.yaml",
                _PROJECT_ROOT / "configs" / "paths.yaml",
                _PROJECT_ROOT / "configs" / "languages" / "zh_tw.yaml",
            )
        except Exception:
            pass

        # 主題：讀取使用者上次的選擇，否則跟隨系統
        self._settings = QSettings("koudesuk", "ModpackTranslator")
        saved = self._settings.value("ui/theme", "")
        self._theme_mode = saved if saved in ("light", "dark") else self._detect_system_theme()

        self._build_ui()
        apply_theme(self._theme_mode)
        self._update_theme_button()
        self._load_remote_settings()
        QTimer.singleShot(1200, self._check_for_updates)

    @staticmethod
    def _detect_system_theme() -> str:
        app = QApplication.instance()
        try:
            if app and app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
                return "dark"
        except Exception:
            pass
        return "light"

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("rootCentral")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setSpacing(12)
        root_layout.setContentsMargins(18, 16, 18, 16)

        # ── 標題列 ────────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        title_lbl = QLabel("Minecraft 模組包翻譯器")
        title_lbl.setObjectName("titleLabel")
        version_chip = QLabel(APP_VERSION)
        version_chip.setObjectName("versionChip")
        version_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("themeToggle")
        self.theme_btn.setFixedSize(40, 32)
        self.theme_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.theme_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_btn.setToolTip("切換深色 / 淺色主題")
        self.theme_btn.clicked.connect(self._toggle_theme)

        header_row.addWidget(title_lbl)
        header_row.addWidget(version_chip)
        header_row.addStretch()
        header_row.addWidget(self.theme_btn)
        root_layout.addLayout(header_row)

        # ── 模組包群組 ────────────────────────────────────────────────────
        modpack_group = QGroupBox("模組包")
        mf = QFormLayout(modpack_group)
        mf.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        modpack_row = QHBoxLayout()
        self.modpack_edit = QLineEdit()
        self.modpack_edit.setPlaceholderText("模組包實例資料夾路徑…")
        self.modpack_edit.textChanged.connect(self._on_modpack_path_changed)
        _browse_modpack_btn = QPushButton("瀏覽…")
        _browse_modpack_btn.setFixedWidth(80)
        _browse_modpack_btn.clicked.connect(self._browse_modpack)
        modpack_row.addWidget(self.modpack_edit)
        modpack_row.addWidget(_browse_modpack_btn)
        mf.addRow("模組包資料夾：", modpack_row)

        root_layout.addWidget(modpack_group)

        # ── 模型設定群組 ──────────────────────────────────────────────────
        model_group = QGroupBox("模型設定")
        model_vbox = QVBoxLayout(model_group)

        # 後端模式切換
        mode_row = QHBoxLayout()
        self.backend_local_radio = QRadioButton("本地模型")
        self.backend_remote_radio = QRadioButton("遠端 API")
        self.backend_local_radio.setChecked(True)
        self._backend_group = QButtonGroup(self)
        self._backend_group.addButton(self.backend_local_radio)
        self._backend_group.addButton(self.backend_remote_radio)
        self.backend_local_radio.toggled.connect(self._on_backend_mode_changed)
        mode_help = _make_help_label(
            "本地模型：使用本機 llama.cpp server（需先執行初始化腳本）。\n"
            "遠端 API：使用 OpenAI 相容的遠端端點（OpenAI / OpenRouter / Groq / 自架 vLLM…）。"
        )
        mode_row.addWidget(QLabel("後端模式："))
        mode_row.addWidget(self.backend_local_radio)
        mode_row.addWidget(self.backend_remote_radio)
        mode_row.addWidget(mode_help)
        mode_row.addStretch()
        model_vbox.addLayout(mode_row)

        # ── 本地模型欄位（容器，遠端模式時整塊隱藏）───────────────────────
        self.local_box = QWidget()
        mgf = QFormLayout(self.local_box)
        mgf.setContentsMargins(0, 0, 0, 0)
        mgf.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        lora_row = QHBoxLayout()
        self.lora_edit = QLineEdit()
        self.lora_edit.setText(
            self._cfg.model.lora_gguf_path if self._cfg else "adapter/minecraft_translator_gemma4_e4b_lora.gguf"
        )
        _browse_lora_btn = QPushButton("瀏覽…")
        _browse_lora_btn.setFixedWidth(80)
        _browse_lora_btn.clicked.connect(self._browse_gguf)
        lora_help = _make_help_label(
            "LoRA 適配器為微調後的模型差異檔（.gguf），提供 Minecraft 翻譯專用能力。\n"
            "必須與基礎模型搭配使用。"
        )
        lora_row.addWidget(self.lora_edit)
        lora_row.addWidget(_browse_lora_btn)
        lora_row.addWidget(lora_help)
        mgf.addRow("LoRA 適配器：", lora_row)

        base_row = QHBoxLayout()
        self.base_gguf_edit = QLineEdit()
        self.base_gguf_edit.setPlaceholderText("留空自動下載（約 5 GB，僅首次）")
        self.base_gguf_edit.setText(self._cfg.model.base_gguf_path if self._cfg else "")
        _browse_base_btn = QPushButton("瀏覽…")
        _browse_base_btn.setFixedWidth(80)
        _browse_base_btn.clicked.connect(self._browse_base_gguf)
        base_help = _make_help_label(
            "基礎模型 GGUF 檔（約 5 GB）。\n"
            "留空時程式自動從 HuggingFace 下載並快取，僅首次需要網路連線。"
        )
        base_row.addWidget(self.base_gguf_edit)
        base_row.addWidget(_browse_base_btn)
        base_row.addWidget(base_help)
        mgf.addRow("基礎模型：", base_row)

        gpu_row = QHBoxLayout()
        self.gpu_layers_spin = QSpinBox()
        self.gpu_layers_spin.setRange(-1, 200)
        self.gpu_layers_spin.setValue(self._cfg.model.n_gpu_layers if self._cfg else -1)
        self.gpu_layers_spin.setFixedWidth(70)
        gpu_help = _make_help_label(
            "指定卸載至 GPU 的模型層數。\n"
            "-1 = 全部卸載至 GPU（最快）\n"
            " 0 = 僅使用 CPU（最慢但相容性最高）\n"
            "修改後請重新執行初始化腳本，讓本機模型服務設定生效。"
        )
        gpu_row.addWidget(self.gpu_layers_spin)
        gpu_row.addWidget(QLabel("  （−1 = 全 GPU，0 = 僅 CPU）"))
        gpu_row.addWidget(gpu_help)
        gpu_row.addStretch()
        mgf.addRow("GPU 層數：", gpu_row)

        model_vbox.addWidget(self.local_box)

        # ── 遠端 API 欄位（容器，本地模式時整塊隱藏）─────────────────────
        self.remote_box = QWidget()
        rgf = QFormLayout(self.remote_box)
        rgf.setContentsMargins(0, 0, 0, 0)
        rgf.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.remote_url_edit = QLineEdit()
        self.remote_url_edit.setPlaceholderText("https://api.openai.com/v1")
        self.remote_url_edit.textChanged.connect(self._save_remote_settings)
        url_help = _make_help_label("遠端 OpenAI 相容端點的 Base URL，通常以 /v1 結尾。")
        url_row = QHBoxLayout()
        url_row.addWidget(self.remote_url_edit)
        url_row.addWidget(url_help)
        rgf.addRow("Base URL：", url_row)

        self.remote_key_edit = QLineEdit()
        self.remote_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.remote_key_edit.setPlaceholderText("sk-...")
        self.remote_key_edit.textChanged.connect(self._save_remote_settings)
        self.remote_key_show_btn = QPushButton()
        self.remote_key_show_btn.setObjectName("eyeButton")
        self.remote_key_show_btn.setCheckable(True)
        self.remote_key_show_btn.setFixedSize(36, 32)
        self.remote_key_show_btn.setIconSize(QSize(20, 20))
        self.remote_key_show_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.remote_key_show_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.remote_key_show_btn.setToolTip("顯示 / 隱藏金鑰")
        self.remote_key_show_btn.toggled.connect(self._toggle_key_visibility)
        self._update_key_visibility_icon()
        key_help = _make_help_label("API 金鑰，儲存在本機 QSettings（明文）。自架且不需金鑰時可留空。")
        key_row = QHBoxLayout()
        key_row.addWidget(self.remote_key_edit)
        key_row.addWidget(self.remote_key_show_btn)
        key_row.addWidget(key_help)
        rgf.addRow("API Key：", key_row)

        self.remote_model_edit = QLineEdit()
        self.remote_model_edit.setPlaceholderText("例如 gpt-4o-mini")
        self.remote_model_edit.textChanged.connect(self._save_remote_settings)
        model_help = _make_help_label("遠端模型名稱，需與該端點提供的模型一致。")
        rmodel_row = QHBoxLayout()
        rmodel_row.addWidget(self.remote_model_edit)
        rmodel_row.addWidget(model_help)
        rgf.addRow("模型名稱：", rmodel_row)

        test_row = QHBoxLayout()
        self.test_conn_btn = QPushButton("測試連線")
        self.test_conn_btn.setFixedWidth(96)
        self.test_conn_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_conn_btn.clicked.connect(self._on_test_connection)
        self.test_conn_label = QLabel("")
        self.test_conn_label.setObjectName("statsLabel")
        test_help = _make_help_label("以極短請求測試 URL／金鑰／模型名是否正確（約消耗 1 個 token）。")
        test_row.addWidget(self.test_conn_btn)
        test_row.addWidget(self.test_conn_label)
        test_row.addWidget(test_help)
        test_row.addStretch()
        rgf.addRow("", test_row)

        model_vbox.addWidget(self.remote_box)
        self.remote_box.setVisible(False)

        root_layout.addWidget(model_group)

        # ── 選項群組 ──────────────────────────────────────────────────────
        options_group = QGroupBox("選項")
        opt_vbox = QVBoxLayout(options_group)

        checkbox_row = QHBoxLayout()
        self.chk_mods = QCheckBox("翻譯模組 (.jar)")
        self.chk_mods.setChecked(True)
        chk_mods_help = _make_help_label(
            "掃描並翻譯模組 .jar 中的 en_us 語言檔。\n"
            "翻譯結果直接注入回 jar（原始 jar 備份至 mods_bak/）。"
        )
        self.chk_quests = QCheckBox("翻譯任務書")
        self.chk_quests.setChecked(True)
        chk_quests_help = _make_help_label(
            "翻譯 FTB Quests、Heracles、Better Questing 及 KubeJS 的語言字串。\n"
            "原始配置備份至 quests_bak/。"
        )
        checkbox_row.addWidget(self.chk_mods)
        checkbox_row.addWidget(chk_mods_help)
        checkbox_row.addSpacing(16)
        checkbox_row.addWidget(self.chk_quests)
        checkbox_row.addWidget(chk_quests_help)
        checkbox_row.addStretch()

        retry_row = QHBoxLayout()
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(0, 10)
        self.retry_spin.setValue(3)
        self.retry_spin.setFixedWidth(90)
        retry_help = _make_help_label(
            "當後處理器偵測到佔位符遺失時，自動重試翻譯的次數。\n"
            "適用於含有 {0}、%1$s 等格式代碼的字串。\n"
            "0 = 不重試，直接以原文回退並記錄至 Failed Items/。"
        )
        retry_row.addWidget(QLabel("重試次數："))
        retry_row.addWidget(self.retry_spin)
        retry_row.addWidget(retry_help)
        retry_row.addStretch()

        opt_vbox.addLayout(checkbox_row)
        opt_vbox.addLayout(retry_row)

        root_layout.addWidget(options_group)

        # ── 操作按鈕 ──────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.scan_btn = QPushButton("🔍  掃描模組包")
        self.scan_btn.setFixedHeight(40)
        self.scan_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.scan_btn.clicked.connect(self._on_scan)

        self.translate_btn = QPushButton("▶  開始翻譯")
        self.translate_btn.setObjectName("primaryButton")
        self.translate_btn.setFixedHeight(40)
        self.translate_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.translate_btn.setEnabled(False)
        self.translate_btn.clicked.connect(self._on_translate_toggle)

        btn_row.addWidget(self.scan_btn)
        btn_row.addWidget(self.translate_btn)
        root_layout.addLayout(btn_row)

        # ── 進度條（加厚） ────────────────────────────────────────────────
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(24)
        self.progress_bar.setProperty("accent", "blue")
        root_layout.addWidget(self.progress_bar)

        # 速度/時間統計標籤
        self.stats_label = QLabel("")
        self.stats_label.setObjectName("statsLabel")
        self.stats_label.setVisible(False)
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root_layout.addWidget(self.stats_label)

        # ── 掃描結果面板 ──────────────────────────────────────────────────
        result_header = QHBoxLayout()
        result_lbl = QLabel("輸出記錄")
        result_lbl.setObjectName("sectionLabel")
        copy_btn = QPushButton("複製")
        copy_btn.setFixedWidth(64)
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.clicked.connect(self._copy_log)
        result_header.addWidget(result_lbl)
        result_header.addStretch()
        result_header.addWidget(copy_btn)
        root_layout.addLayout(result_header)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        mono = QFont("Consolas", 9)
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.log_edit.setFont(mono)
        self.log_edit.setMinimumHeight(220)
        root_layout.addWidget(self.log_edit, stretch=1)

    # ------------------------------------------------------------------ 瀏覽

    def _browse_modpack(self):
        path = QFileDialog.getExistingDirectory(self, "選擇模組包實例資料夾")
        if path:
            self.modpack_edit.setText(path)

    def _browse_gguf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇 LoRA 適配器 GGUF",
            str(_PROJECT_ROOT / "adapter"),
            "GGUF Files (*.gguf);;All Files (*)",
        )
        if path:
            self.lora_edit.setText(path)

    def _browse_base_gguf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇基礎模型 GGUF",
            str(_PROJECT_ROOT),
            "GGUF Files (*.gguf);;All Files (*)",
        )
        if path:
            self.base_gguf_edit.setText(path)

    # ------------------------------------------------------------------ 後端模式 / 遠端設定

    def _on_backend_mode_changed(self, *_):
        remote = self.backend_remote_radio.isChecked()
        self.local_box.setVisible(not remote)
        self.remote_box.setVisible(remote)
        # 切換模式後，先前的測試連線結果（✓/✗）已不適用，清空避免混淆
        if hasattr(self, "test_conn_label"):
            self.test_conn_label.setText("")
        self._save_remote_settings()

    def _toggle_key_visibility(self, checked: bool):
        self.remote_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self._update_key_visibility_icon()

    def _update_key_visibility_icon(self):
        # 金鑰隱藏時顯示睜眼（點了可見），可見時顯示劃線眼（點了隱藏）
        slashed = self.remote_key_show_btn.isChecked()
        self.remote_key_show_btn.setIcon(eye_icon(self._theme_mode, slashed))

    def _load_remote_settings(self):
        # 先一次讀出所有值再寫入欄位：setText 會觸發 textChanged→_save_remote_settings，
        # 若邊讀邊寫，第一個 setText 就會把尚未載入的欄位用空字串覆寫回 QSettings。
        url = self._settings.value("model/remote_base_url", "") or ""
        key = self._settings.value("model/remote_api_key", "") or ""
        model = self._settings.value("model/remote_model", "") or ""
        mode = self._settings.value("model/backend_mode", "local") or "local"

        self._loading_settings = True
        try:
            self.remote_url_edit.setText(url)
            self.remote_key_edit.setText(key)
            self.remote_model_edit.setText(model)
            if mode == "remote":
                self.backend_remote_radio.setChecked(True)
            else:
                self.backend_local_radio.setChecked(True)
            self._on_backend_mode_changed()
        finally:
            self._loading_settings = False

    def _save_remote_settings(self, *_):
        if getattr(self, "_loading_settings", False):
            return
        mode = "remote" if self.backend_remote_radio.isChecked() else "local"
        self._settings.setValue("model/backend_mode", mode)
        self._settings.setValue("model/remote_base_url", self.remote_url_edit.text().strip())
        self._settings.setValue("model/remote_api_key", self.remote_key_edit.text().strip())
        self._settings.setValue("model/remote_model", self.remote_model_edit.text().strip())

    def _on_test_connection(self):
        base = self.remote_url_edit.text().strip()
        model = self.remote_model_edit.text().strip()
        key = self.remote_key_edit.text().strip()
        if not base or not model:
            self.test_conn_label.setText("✗ 請先填寫 Base URL 與模型名稱")
            return
        self.test_conn_btn.setEnabled(False)
        self.test_conn_label.setText("測試中…")
        self._conn_test_worker = ConnTestWorker(base, key, model)
        self._conn_test_worker.done.connect(self._on_test_connection_done)
        self._conn_test_worker.start()

    def _on_test_connection_done(self, ok: bool, msg: str):
        self.test_conn_btn.setEnabled(True)
        self.test_conn_label.setText(("✓ " if ok else "✗ ") + msg)

    # ------------------------------------------------------------------ 複製

    def _copy_log(self):
        QApplication.clipboard().setText(self.log_edit.toPlainText())

    def _append_log(self, msg: str):
        """附加一行帶時間戳的訊息到輸出記錄區並捲到底。worker signal 經 queued connection 呼叫,固定在 GUI 執行緒執行。"""
        ts = time.strftime("%H:%M:%S")
        self.log_edit.append(f"[{ts}] {msg}")
        self.log_edit.moveCursor(QTextCursor.MoveOperation.End)

    # ------------------------------------------------------------------ 主題 / 樣式

    def _toggle_theme(self):
        self._theme_mode = "dark" if self._theme_mode == "light" else "light"
        apply_theme(self._theme_mode)
        self._settings.setValue("ui/theme", self._theme_mode)
        self._update_theme_button()
        self._update_key_visibility_icon()

    def _update_theme_button(self):
        # 顯示「點下去會切換成」的圖示
        self.theme_btn.setText("☀" if self._theme_mode == "dark" else "🌙")

    # ------------------------------------------------------------------ 更新

    def _check_for_updates(self):
        if self._update_check_worker and self._update_check_worker.isRunning():
            return
        self._update_check_worker = UpdateCheckWorker(__version__)
        self._update_check_worker.update_available.connect(self._show_update_dialog)
        self._update_check_worker.start()

    def _show_update_dialog(self, info: UpdateInfo):
        size_mb = info.asset_size / (1024 * 1024) if info.asset_size else 0
        notes = info.notes.strip()
        if len(notes) > 1200:
            notes = notes[:1200].rstrip() + "\n..."
        message = (
            f"目前版本：{APP_VERSION}\n"
            f"最新版本：{info.tag_name}\n"
            f"下載大小：{size_mb:.1f} MB\n\n"
            f"{notes or '此版本沒有 release notes。'}\n\n"
            "是否下載並自動套用更新？程式會關閉，移除舊的虛擬環境與後端設定，"
            "重新執行 setup，完成後再啟動新版。"
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("發現新版本")
        box.setText(message)
        update_btn = box.addButton("自動更新", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("稍後", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is update_btn:
            self._download_and_apply_update(info)

    def _download_and_apply_update(self, info: UpdateInfo):
        if self._translate_worker and self._translate_worker.isRunning():
            QMessageBox.warning(self, "無法更新", "翻譯進行中不能更新。請先停止翻譯。")
            return
        if self._update_download_worker and self._update_download_worker.isRunning():
            return
        self._update_download_worker = UpdateDownloadWorker(info)
        self._update_download_worker.finished_path.connect(self._apply_downloaded_update)
        self._update_download_worker.error.connect(
            lambda msg: QMessageBox.critical(self, "更新失敗", msg)
        )
        self._update_download_worker.start()

    def _apply_downloaded_update(self, zip_path: str):
        try:
            launch_apply_update(Path(zip_path), restart=True)
        except Exception as exc:
            QMessageBox.critical(self, "更新失敗", str(exc))
            return
        QApplication.quit()

    def _set_tone(self, widget, tone: str):
        """設定按鈕語意狀態（""/danger/warning/success），由全域 QSS 上色。"""
        widget.setProperty("tone", tone)
        restyle(widget)

    def _set_accent(self, accent: str):
        """設定進度條顏色（blue/green/orange），由全域 QSS 上色。"""
        self.progress_bar.setProperty("accent", accent)
        restyle(self.progress_bar)

    # ------------------------------------------------------------------ 輔助

    def _validate_inputs(self) -> bool:
        modpack = self.modpack_edit.text().strip()
        if not modpack:
            QMessageBox.warning(self, "缺少輸入", "請選擇模組包資料夾。")
            return False
        if not Path(modpack).exists():
            QMessageBox.warning(self, "路徑無效", f"找不到模組包資料夾：\n{modpack}")
            return False
        if not self.chk_mods.isChecked() and not self.chk_quests.isChecked():
            QMessageBox.warning(self, "選項無效", "請至少勾選「翻譯模組」或「翻譯任務書」其中一項。")
            return False
        return True

    def _build_cfg(self):
        try:
            cfg = load_config(
                _PROJECT_ROOT / "configs" / "model.yaml",
                _PROJECT_ROOT / "configs" / "paths.yaml",
                _PROJECT_ROOT / "configs" / "languages" / "zh_tw.yaml",
            )
        except Exception as exc:
            QMessageBox.critical(self, "設定檔錯誤", f"無法載入設定檔：\n{exc}")
            return None

        cfg.model.lora_gguf_path = self.lora_edit.text().strip() or cfg.model.lora_gguf_path
        cfg.model.base_gguf_path = self.base_gguf_edit.text().strip()
        cfg.model.n_gpu_layers   = self.gpu_layers_spin.value()

        if self.backend_remote_radio.isChecked():
            cfg.model.backend_mode = "remote"
            cfg.model.remote_base_url = self.remote_url_edit.text().strip()
            cfg.model.remote_api_key = self.remote_key_edit.text().strip()
            cfg.model.remote_model = self.remote_model_edit.text().strip()
        else:
            cfg.model.backend_mode = "local"
        cfg.paths.create_output_dirs()
        return cfg

    def _set_busy(self, busy: bool):
        self.scan_btn.setEnabled(not busy)
        if not busy:
            self.translate_btn.setEnabled(len(self._scan_targets) > 0)

    def _update_stats_label(self):
        self.stats_label.setText(build_stats_text(
            now=time.monotonic(),
            start_time=self._translation_start_time,
            samples=self._speed_samples,
            pairs_done=self._pairs_done,
            total_pairs=self._scan_total_pairs,
        ))

    # ------------------------------------------------------------------ 掃描

    def _on_scan(self):
        if not self._validate_inputs():
            return

        self._set_busy(True)
        self.translate_btn.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("")
        self._set_accent("blue")
        self.progress_bar.setVisible(True)
        self.stats_label.setVisible(False)
        self.log_edit.setPlainText("")

        self._scan_worker = ScanWorker(
            modpack_path=Path(self.modpack_edit.text().strip()),
            skip_mods=not self.chk_mods.isChecked(),
            skip_quests=not self.chk_quests.isChecked(),
            lang_code=(self._cfg.language.code if self._cfg else "zh_tw"),
        )
        self._scan_worker.log.connect(self._append_log)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.error.connect(self._on_error)
        self._scan_worker.start()

    def _on_scan_finished(self, targets, fmt_counts, total_pairs, samples):
        self._scan_targets     = targets
        self._scan_fmt_counts  = fmt_counts
        self._scan_total_pairs = total_pairs

        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.progress_bar.setVisible(False)
        self._set_busy(False)

        if not targets:
            QMessageBox.warning(
                self,
                "未找到翻譯目標",
                "掃描完成，但未找到可翻譯的檔案。\n\n"
                "可能原因：\n"
                "  • 模組包路徑不正確（應選包含 mods/ 資料夾的目錄）\n"
                "  • 該模組包已全部翻譯完成\n"
                "  • 未勾選任何翻譯選項\n"
                "  • 模組語言檔不含英文（en_us）字串",
            )
            self.log_edit.append("掃描完成 — 未找到可翻譯的檔案。")
            return

        modpack_path = self.modpack_edit.text().strip()
        lines = [
            f"遊戲根目錄：{modpack_path}",
            "",
            f"翻譯目標總計：{len(targets)} 個檔案",
        ]
        for fmt, count in sorted(fmt_counts.items()):
            display_fmt = _FMT_NAME_MAP.get(fmt, fmt)
            lines.append(f"  {display_fmt}：{count} 個")

        lines += [
            "",
            f"待翻譯鍵值對總數：{total_pairs:,} 組",
        ]

        if samples:
            lines += ["", "樣本字串（每種格式最多 3 條）："]
            for fmt, fmt_samples in samples.items():
                display_fmt = _FMT_NAME_MAP.get(fmt, fmt)
                lines.append(f"  [{display_fmt}]")
                for mod_id, key, val in fmt_samples:
                    display = val[:80] + "…" if len(val) > 80 else val
                    lines.append(f"    ({mod_id})  {key}")
                    lines.append(f'    → "{display}"')

        self.log_edit.append("\n".join(lines))
        self.translate_btn.setEnabled(True)

    # ------------------------------------------------------------------ 翻譯

    def _on_translate_toggle(self):
        if self._translate_worker and self._translate_worker.isRunning():
            self._translation_cancelled = True
            self._translate_worker.cancel()
            self.translate_btn.setText("停止中…")
            self.translate_btn.setEnabled(False)
            self._force_stop_timer.start()   # 60 秒後若未停止則強制中止
        else:
            self._start_translation()

    def _start_translation(self):
        if not self._scan_targets:
            QMessageBox.information(self, "請先掃描", "請先執行掃描模組包。")
            return

        cfg = self._build_cfg()
        if cfg is None:
            return

        if cfg.model.backend_mode == "remote":
            if not cfg.model.remote_base_url or not cfg.model.remote_model:
                QMessageBox.warning(self, "遠端設定不完整",
                                    "請先填寫遠端 API 的 Base URL 與模型名稱。")
                return
        else:
            lora_path = Path(cfg.model.lora_gguf_path)
            if not lora_path.is_absolute():
                lora_path = _PROJECT_ROOT / lora_path
            if not lora_path.exists():
                QMessageBox.warning(self, "找不到 LoRA 適配器",
                                    f"找不到 LoRA 適配器 GGUF：\n{lora_path}")
                return

        modpack_path = Path(self.modpack_edit.text().strip()).resolve()

        self.translate_btn.setText("⏹  停止")
        self._set_tone(self.translate_btn, "danger")
        self.scan_btn.setEnabled(False)

        n_files = len(self._scan_targets)
        # 用字串對數作為進度條上限，讓進度隨每條字串平滑推進
        # 若掃描未統計出對數（罕見），退回使用檔案數
        n_pairs = self._scan_total_pairs if self._scan_total_pairs > 0 else n_files
        self.progress_bar.setRange(0, n_pairs)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self._set_accent("blue")
        self.progress_bar.setVisible(True)

        self._translation_start_time = time.monotonic()
        self._translation_total = n_files
        self._current_progress = 0
        self._pairs_done = 0
        self._translation_cancelled = False
        self._speed_samples.clear()
        self._update_stats_label()
        self.stats_label.setVisible(True)
        self._stats_timer.start()

        self.log_edit.append("\n" + "─" * 40)

        self._translate_worker = TranslateWorker(
            targets=self._scan_targets,
            cfg=cfg,
            modpack_path=modpack_path,
            retry_count=self.retry_spin.value(),
        )
        self._translate_worker.log.connect(self._append_log)
        self._translate_worker.progress.connect(self._on_translate_progress)
        self._translate_worker.pair_progress.connect(self._on_pair_progress)
        self._translate_worker.finished.connect(self._on_translate_finished)
        self._translate_worker.error.connect(self._on_error)
        self._translate_worker.start()

    def _on_translate_progress(self, current: int, total: int, mod_id: str, fmt: str, pairs_done: int):
        # 追蹤目前第幾個檔案並在 log 區顯示；進度條由 _on_pair_progress 逐條更新
        self._current_progress = current + 1
        display_fmt = _FMT_NAME_MAP.get(fmt, fmt)
        self._append_log(f"({current + 1}/{total}) 翻譯 {mod_id}（{display_fmt}）…")

    def _on_pair_progress(self, pairs_done: int):
        """每條字串翻譯完成後（節流版）由 worker 呼叫，同步更新進度條與滑動視窗樣本。"""
        now = time.monotonic()
        self._pairs_done = pairs_done
        self._speed_samples.append((now, pairs_done))
        # 進度條以字串對數平滑推進；clamp 防止估算差異造成超出 maximum
        self.progress_bar.setValue(min(pairs_done, self.progress_bar.maximum()))

    def _on_translate_finished(self, translated: int, cached: int, fallback: int, failed_files: int):
        self._stats_timer.stop()
        self._force_stop_timer.stop()
        self._update_stats_label()
        self._set_busy(False)

        existing = self.log_edit.toPlainText()
        summary_lines = ["", "─" * 40]

        if self._translation_cancelled:
            self._set_accent("orange")
            self.translate_btn.setText("↩  已停止，繼續？")
            self._set_tone(self.translate_btn, "warning")
            summary_lines += [
                "翻譯已中止",
                f"  已翻譯：{translated:,} 組",
                f"  快取命中：{cached:,} 組",
                f"  回退（使用原文）：{fallback:,} 組",
            ]
        else:
            self.progress_bar.setValue(self.progress_bar.maximum())
            self._set_accent("green")
            self.translate_btn.setText("✓  完成")
            self._set_tone(self.translate_btn, "success")
            self._translated_modpack_path = self.modpack_edit.text().strip()
            summary_lines += [
                "翻譯完成",
                f"  已翻譯：{translated:,} 組",
                f"  快取命中：{cached:,} 組",
                f"  回退（使用原文）：{fallback:,} 組",
            ]

        if failed_files > 0:
            summary_lines.append(
                f"  ⚠ {failed_files} 個模組/任務書含失敗項目 → 詳見 Failed Items/ 資料夾"
            )
        self.log_edit.setPlainText(existing + "\n" + "\n".join(summary_lines))
        self.log_edit.moveCursor(QTextCursor.MoveOperation.End)

    # ------------------------------------------------------------------ 錯誤

    def _on_error(self, msg: str):
        self._stats_timer.stop()
        self._force_stop_timer.stop()
        self.translate_btn.setText("▶  開始翻譯")
        self._set_tone(self.translate_btn, "")
        self.progress_bar.setVisible(False)
        self.stats_label.setVisible(False)
        self._set_busy(False)
        QMessageBox.critical(self, "錯誤", msg)

    # ------------------------------------------------------------------ 強制停止

    def _force_stop_worker(self):
        """
        60 秒逾時安全網：
        1. 向 Python 執行緒注入 SystemExit（比 terminate() 更安全，不在 C 層截斷）
        2. 等待 5 秒讓執行緒清理
        3. 仍未停止才用 QThread.terminate() 作最後手段
        備份已在翻譯開始前完成，即使強制停止也可從 mods_bak/quests_bak/ 還原。
        """
        if not (self._translate_worker and self._translate_worker.isRunning()):
            return

        import ctypes

        thread_id = getattr(self._translate_worker, "_thread_id", None)
        if thread_id is not None:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(thread_id),
                ctypes.py_object(SystemExit),
            )
            if self._translate_worker.wait(5000):
                return   # 注入成功，執行緒已停止

        # 最後手段
        self._translate_worker.terminate()
        self._translate_worker.wait(2000)

        QMessageBox.warning(
            self,
            "已強制停止",
            "翻譯執行緒因逾時已強制中止。\n\n"
            "如有 JAR 檔案損壞，請從 mods_bak/ 還原。\n"
            "如有任務設定損壞，請從 quests_bak/ 還原。",
        )
        self.translate_btn.setText("↩  已停止，繼續？")
        self._set_tone(self.translate_btn, "warning")
        self.translate_btn.setEnabled(True)
        self.scan_btn.setEnabled(True)
        self.stats_label.setVisible(False)

    # ------------------------------------------------------------------ 路徑變更

    def _on_modpack_path_changed(self, new_path: str):
        current_text = self.translate_btn.text()
        if current_text in ("✓  完成", "↩  已停止，繼續？"):
            self.translate_btn.setText("▶  開始翻譯")
            self._set_tone(self.translate_btn, "")
            self._set_accent("blue")

    def closeEvent(self, event):
        if self._translate_worker and self._translate_worker.isRunning():
            self._translation_cancelled = True
            self._translate_worker.cancel()
            if not self._translate_worker.wait(10_000):
                self._translate_worker.terminate()
                self._translate_worker.wait(2_000)
        event.accept()


class UpdateCheckWorker(QThread):
    update_available = Signal(object)

    def __init__(self, current_version: str):
        super().__init__()
        self._current_version = current_version

    def run(self):
        info = check_for_update(self._current_version)
        if info is not None:
            self.update_available.emit(info)


class UpdateDownloadWorker(QThread):
    finished_path = Signal(str)
    error = Signal(str)

    def __init__(self, info: UpdateInfo):
        super().__init__()
        self._info = info

    def run(self):
        try:
            path = download_update(self._info)
            self.finished_path.emit(str(path))
        except Exception as exc:
            self.error.emit(str(exc))


class ConnTestWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, base_url: str, api_key: str, model: str):
        super().__init__()
        self._base_url = base_url
        self._api_key = api_key
        self._model = model

    def run(self):
        from modpack_translator.pipeline.remote_translator import test_remote_connection
        ok, msg = test_remote_connection(self._base_url, self._api_key, self._model)
        self.done.emit(ok, msg)
