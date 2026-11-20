"""Module: @role: VALORANTログ行の正規表現定義とマップ・状態遷移パース処理を担うモジュール"""
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import os
import re
import time
from typing import Optional, Tuple

TIME_PATTERN = re.compile(r"^\[(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2}):(\d{3})\]")
MAP_NAME_PATTERN = re.compile(r"\[Map Name:\s*([^\]|]+)", re.IGNORECASE)
MAP_UPDATE_PATTERN = re.compile(r"\[Map:\s*([^\]|]+)", re.IGNORECASE)
WORLD_MAP_PATTERN = re.compile(r"/Game/Maps/(?:[A-Za-z0-9_]+/)*([A-Za-z0-9_]+)", re.IGNORECASE)
RANGE_KEYWORD_PATTERN = re.compile(r"(?:Range|ShootingRange|Pingo|BasicTraining)", re.IGNORECASE)
GAME_MODE_URL_PATTERN = re.compile(
    r"[?&]game=(?:/Game/GameModes/)?([A-Za-z0-9_]+)(?:\.[A-Za-z0-9_]+)?",
    re.IGNORECASE,
)
QUEUE_ID_PATTERN = re.compile(
    r"(?:queueId|queue_id|\"queueId\")[\"'\s:=]+([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)

MAP_CODENAME_MAP: dict[str, str] = {
    "ascent": "Ascent",
    "bonsai": "Split",
    "duality": "Bind",
    "foxtrot": "Breeze",
    "canyon": "Fracture",
    "triad": "Haven",
    "port": "Icebox",
    "pitt": "Pearl",
    "jam": "Lotus",
    "juliett": "Sunset",
    "infinity": "Abyss",
    "pingo": "Range",
    "range": "Range",
    "basic_training": "Range",
    "kasbah": "Kasbah",
    "district": "District",
    "piazza": "Piazza",
    "drift": "Drift",
    "glitch": "Glitch",
}

QUEUE_NAME_MAP: dict[str, str] = {
    "competitive": "Competitive",
    "unrated": "Unrated",
    "spikerush": "Spike Rush",
    "quickbomb": "Spike Rush",
    "swiftplay": "Swiftplay",
    "deathmatch": "Deathmatch",
    "ggteam": "Escalation",
    "onefa": "Replication",
    "snowball": "Snowball Fight",
    "hurm": "Team Deathmatch",
    "newmap": "New Map",
    "custom": "Custom Game",
}

MODE_CLASS_MAP: dict[str, str] = {
    "bombgamemode": "Standard",
    "bomb": "Standard",
    "quickbombgamemode": "Spike Rush",
    "quickbomb": "Spike Rush",
    "deathmatchgamemode": "Deathmatch",
    "deathmatch": "Deathmatch",
    "gungame_gamemode": "Escalation",
    "oneforall_gamemode": "Replication",
    "snowballfight_gamemode": "Snowball Fight",
    "hurm_gamemode": "Team Deathmatch",
    "hurmgamemode": "Team Deathmatch",
    "shootingrangegamemode": "The Range",
}

STATE_TRANSITION_PATTERN = re.compile(
    r"(?:Match State Changed from|Transitioning from|State changed from|State:)\s*(\w+)\s*(?:to|->)\s*(\w+)",
    re.IGNORECASE,
)
STATE_TO_PATTERN = re.compile(
    r"(?:Broadcasting state changed to|new state:)\s*(\w+)",
    re.IGNORECASE,
)

IGNORED_MODULE_PATTERN = re.compile(
    r"Log(?:ShooterVoice|RNetVoice|Party|TextChat|Social|Presence|Vivox|EOS|Online|Http|WebSockets|RMS|Telemetry)",
    re.IGNORECASE,
)

VALID_GAME_STATES: set[str] = {
    "WaitingToStart",
    "PreRound",
    "InProgress",
    "PostRound",
    "WaitingPostMatch",
    "PostGame",
    "Menus",
    "MainMenu",
    "Pregame",
    "InGame",
    "Initialization",
    "TransitionToMainMenu",
    "TransitionToPregame",
    "TransitionToInGame",
}

RELEVANT_LOG_KEYWORDS: tuple[str, ...] = (
    "LogMapLoadModel",
    "LogGameFlowStateManager",
    "LogPlatformSessionManager",
    "Match State Changed",
    "State:",
    "Transitioning",
    "Broadcasting state changed",
    "Loopstate changed",
    "PreRound",
    "InProgress",
    "PostRound",
    "WaitingPostMatch",
    "PostGame",
    "LogAbilitySystem:",
    "Ability activated",
    "Performance drop",
    "Hitch detected",
    "LogExit",
    "RequestExit",
    "EngineExit",
    "Closing by request",
    "ApplicationWillTerminate",
    "Log file closed",
    "Log file open",
    "Window 'VALORANT",
    "being destroyed",
    "Destroy",
    "WM_CLOSE",
)

START_LOG_PATTERNS: tuple[str, ...] = (
    "Match State Changed from WaitingToStart to InProgress",
    "Match State Changed from WaitingToStart to PreRound",
    "State: WaitingToStart -> InProgress",
    "State: WaitingToStart -> PreRound",
    "Broadcasting state changed to InGame",
)

END_LOG_PATTERNS: tuple[str, ...] = (
    "Match State Changed to WaitingPostMatch",
    "Match State Changed to PostGame",
    "State: InProgress -> WaitingPostMatch",
    "State: PostRound -> WaitingPostMatch",
    "Transitioning to State: WaitingPostMatch",
    "Transitioning to State: PostGame",
    "LogGameFlowStateManager: Match ended",
)

MENU_LOG_PATTERNS: tuple[str, ...] = (
    "Broadcasting state changed to Menus",
    "Broadcasting state changed to MainMenu",
    "Broadcasting state changed to TransitionToMainMenu",
    "Loopstate changed from INGAME to MENUS",
    "Loopstate changed from PREGAME to MENUS",
)

SETTLED_MENU_LOG_PATTERNS: tuple[str, ...] = (
    "Broadcasting state changed to Menus",
    "Broadcasting state changed to MainMenu",
    "Loopstate changed from INGAME to MENUS",
    "Loopstate changed from PREGAME to MENUS",
)

PREGAME_LOG_PATTERNS: tuple[str, ...] = (
    "Broadcasting state changed to Pregame",
    "Broadcasting state changed to TransitionToPregame",
    "Loopstate changed from MENUS to PREGAME",
)

APP_EXIT_LOG_PATTERNS: tuple[str, ...] = (
    "LogExit: Preparing to exit",
    "LogExit: Game engine shut down",
    "LogExit: Exiting",
    "LogExit: Object subsystem successfully closed",
    "FPlatformMisc::RequestExit",
    "EngineExit(",
    "Log file closed",
)

PERF_WARNING_PATTERNS: tuple[str, ...] = (
    "Performance drop detected",
    "Severe hitch detected",
    "Long frame detected",
    "Large hitch",
)

NON_GAME_MAPS: set[str] = {
    "init",
    "mainmenuv2",
    "characterselectpersistentlevel",
    "menus",
    "mainmenu",
}


def estimate_log_tz_offset(raw_local_ts: float, reference_epoch: float) -> Optional[float]:
    """UEログのタイムスタンプ(通常UTC)とOS実時刻の時差を30分単位で推定する"""
    diff = reference_epoch - raw_local_ts
    # 範囲外の過去ログ等で0.0に固定しないようNoneを返し、後続の新規行で再推定可能にする
    if abs(diff) > 54000.0:
        return None
    return round(diff / 1800.0) * 1800.0


def parse_log_timestamp(line: str, tz_offset: Optional[float] = None, assume_utc: bool = False) -> float:
    m = TIME_PATTERN.match(line)
    if m:
        try:
            year, month, day, hour, minute, second, ms = map(int, m.groups())
            if assume_utc:
                dt = datetime(year, month, day, hour, minute, second, ms * 1000, tzinfo=timezone.utc)
                return dt.timestamp()
            dt = datetime(year, month, day, hour, minute, second, ms * 1000)
            ts = dt.timestamp()
            if tz_offset is not None:
                ts += tz_offset
            return ts
        except Exception:
            pass
    return time.time()


def parse_state_transition(line: str) -> Tuple[Optional[str], Optional[str]]:
    m = STATE_TRANSITION_PATTERN.search(line)
    if m:
        return m.group(1), m.group(2)
    m2 = STATE_TO_PATTERN.search(line)
    if m2:
        return None, m2.group(1)
    return None, None


def is_menu_transition_line(line: str, to_state: Optional[str] = None) -> bool:
    if any(p in line for p in MENU_LOG_PATTERNS):
        return True
    return to_state in ("Menus", "MainMenu", "TransitionToMainMenu") and (
        "Match State Changed" in line or "LogGameFlowStateManager" in line
    )


def is_menu_settled_line(line: str, to_state: Optional[str] = None) -> bool:
    if any(p in line for p in SETTLED_MENU_LOG_PATTERNS):
        return True
    return to_state in ("Menus", "MainMenu") and (
        "Match State Changed" in line or "LogGameFlowStateManager" in line
    )


def is_pregame_transition_line(line: str, to_state: Optional[str] = None) -> bool:
    if any(p in line for p in PREGAME_LOG_PATTERNS):
        return True
    return to_state in ("Pregame", "TransitionToPregame") and (
        "Match State Changed" in line or "LogGameFlowStateManager" in line
    )


def is_app_exit_line(line: str) -> bool:
    """VALORANTウィンドウを閉じた際（エンジン終了・ウィンドウ破棄）のログ行かを判定する"""
    if any(p in line for p in APP_EXIT_LOG_PATTERNS):
        return True
    if "LogExit:" in line:
        return True
    if "Window 'VALORANT" in line and ("being destroyed" in line or "Destroy" in line):
        return True
    if ("LogWindows:" in line or "LogSlate:" in line or "LogEngine:" in line) and (
        "RequestExit" in line
        or "ApplicationWillTerminate" in line
        or "Closing by request" in line
        or "WM_CLOSE" in line
    ):
        return True
    return False


def is_valorant_window_open() -> Optional[bool]:
    """OS上でVALORANTのゲームウィンドウハンドルが存在するか判定する（最小化や一時非表示でもTrue）"""
    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32
        enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        # 64bit環境でのHWND上位ビット欠落を防ぐためシグネチャを明示定義
        find_window = user32.FindWindowW
        find_window.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        find_window.restype = wintypes.HWND

        is_window = user32.IsWindow
        is_window.argtypes = [wintypes.HWND]
        is_window.restype = wintypes.BOOL

        get_class_name = user32.GetClassNameW
        get_class_name.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        get_class_name.restype = ctypes.c_int

        get_window_text = user32.GetWindowTextW
        get_window_text.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        get_window_text.restype = ctypes.c_int

        enum_windows = user32.EnumWindows
        enum_windows.argtypes = [enum_proc_type, wintypes.LPARAM]
        enum_windows.restype = wintypes.BOOL

        hwnd = find_window("UnrealWindow", "VALORANT  ")
        if hwnd and is_window(hwnd):
            return True

        found = False

        def _enum_cb(h_wnd: int, _lparam: int) -> bool:
            nonlocal found
            if not h_wnd or not is_window(h_wnd):
                return True
            cls_buf = ctypes.create_unicode_buffer(64)
            if get_class_name(h_wnd, cls_buf, 64) <= 0:
                return True
            if cls_buf.value != "UnrealWindow":
                return True
            title_buf = ctypes.create_unicode_buffer(128)
            get_window_text(h_wnd, title_buf, 128)
            if title_buf.value.strip().upper().startswith("VALORANT"):
                found = True
                return False
            return True

        enum_windows(enum_proc_type(_enum_cb), 0)
        return found
    except Exception:
        return None


def is_valorant_process_running() -> Optional[bool]:
    """OS上でVALORANT本体プロセスが生存しているか判定する（裏画面・別デスクトップ切替時の誤検知防止）"""
    if os.name != "nt":
        return None
    try:
        # 共有windll.kernel32のargtypes/restype書き換えによる他モジュール競合を防ぐためローカル取得
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        th32cs_snapprocess = 0x00000002
        invalid_handle = ctypes.c_void_p(-1).value

        class _PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        create_snapshot = kernel32.CreateToolhelp32Snapshot
        create_snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        create_snapshot.restype = wintypes.HANDLE

        process32_first = kernel32.Process32FirstW
        process32_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        process32_first.restype = wintypes.BOOL

        process32_next = kernel32.Process32NextW
        process32_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        process32_next.restype = wintypes.BOOL

        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL

        snapshot = create_snapshot(th32cs_snapprocess, 0)
        if not snapshot or snapshot == invalid_handle:
            return None
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            if not process32_first(snapshot, ctypes.byref(entry)):
                return False
            while True:
                if entry.szExeFile.lower() == "valorant-win64-shipping.exe":
                    return True
                if not process32_next(snapshot, ctypes.byref(entry)):
                    break
            return False
        finally:
            close_handle(snapshot)
    except Exception:
        return None


def parse_map_update_from_line(
    line: str, current_map_name: str, is_range: bool
) -> Tuple[str, bool, bool, bool]:
    """ログ行から(更新後マップ名, 射撃場フラグ, 実マップ更新有無, メニューリセット有無)を返す"""
    if "LogMapLoadModel: Update:" not in line and "/Game/Maps/" not in line:
        return current_map_name, is_range, False, False

    m_map = MAP_NAME_PATTERN.search(line)
    raw_map_name = m_map.group(1).strip() if m_map else ""

    if not raw_map_name:
        m_map2 = MAP_UPDATE_PATTERN.search(line)
        if m_map2:
            raw_map_name = m_map2.group(1).strip()

    if not raw_map_name:
        m_url = WORLD_MAP_PATTERN.search(line)
        if m_url:
            cand = m_url.group(1).strip()
            if cand.lower() not in NON_GAME_MAPS:
                raw_map_name = cand

    if not raw_map_name:
        return current_map_name, is_range, False, False

    lower_map = raw_map_name.lower()
    if lower_map in ("mainmenuv2", "init"):
        return "Unknown", False, False, True

    if lower_map in NON_GAME_MAPS:
        return current_map_name, is_range, False, False

    new_range = bool(RANGE_KEYWORD_PATTERN.search(raw_map_name))
    is_updated = (raw_map_name != current_map_name)
    return raw_map_name, new_range, is_updated, False


def normalize_map_name(raw_name: str) -> str:
    """内部コード名または表示名を正規化されたマップ表示名へ変換する"""
    clean = raw_name.strip()
    return MAP_CODENAME_MAP.get(clean.lower(), clean)


def parse_game_mode_from_line(line: str) -> Optional[str]:
    """ログ行からキューIDまたはGameModeクラスをパースして表示用ゲームモード名を返す"""
    m_queue = QUEUE_ID_PATTERN.search(line)
    if m_queue:
        raw_qid = m_queue.group(1).strip().lower()
        if raw_qid in QUEUE_NAME_MAP:
            return QUEUE_NAME_MAP[raw_qid]
        if raw_qid not in ("none", "null", "undefined"):
            return raw_qid.capitalize()

    m_url = GAME_MODE_URL_PATTERN.search(line)
    if m_url:
        raw_mode = m_url.group(1).strip().lower()
        if raw_mode in MODE_CLASS_MAP:
            return MODE_CLASS_MAP[raw_mode]
        for key, val in MODE_CLASS_MAP.items():
            if key in raw_mode:
                return val
        return raw_mode.capitalize()

    return None
