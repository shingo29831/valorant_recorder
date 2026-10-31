"""Module: @role: VALORANTログ行の正規表現定義とマップ・状態遷移パース処理を担うモジュール"""
from datetime import datetime
import re
import time
from typing import Optional, Tuple

TIME_PATTERN = re.compile(r"^\[(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2}):(\d{3})\]")
MAP_NAME_PATTERN = re.compile(r"\[Map Name:\s*([^\]|]+)", re.IGNORECASE)
WORLD_MAP_PATTERN = re.compile(r"/Game/Maps/(?:[A-Za-z0-9_]+/)*([A-Za-z0-9_]+)", re.IGNORECASE)
RANGE_KEYWORD_PATTERN = re.compile(r"(?:Range|ShootingRange|Pingo|BasicTraining)", re.IGNORECASE)

STATE_TRANSITION_PATTERN = re.compile(
    r"(?:Match State Changed from|Transitioning from|State changed from|State:)\s*(\w+)\s*(?:to|->)\s*(\w+)",
    re.IGNORECASE,
)
STATE_TO_PATTERN = re.compile(
    r"(?:Broadcasting state changed to|new state:)\s*(\w+)",
    re.IGNORECASE,
)

IGNORED_MODULE_PATTERN = re.compile(
    r"Log(?:ShooterVoice|RNetVoice|Party|TextChat|Social|Presence)",
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
    "Match ended",
    "Leaving session",
)

MENU_LOG_PATTERNS: tuple[str, ...] = (
    "Broadcasting state changed to Menus",
    "Broadcasting state changed to MainMenu",
    "Broadcasting state changed to TransitionToMainMenu",
    "Loopstate changed from INGAME to MENUS",
    "Loopstate changed from PREGAME to MENUS",
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


def parse_log_timestamp(line: str) -> float:
    m = TIME_PATTERN.match(line)
    if m:
        try:
            year, month, day, hour, minute, second, ms = map(int, m.groups())
            dt = datetime(year, month, day, hour, minute, second, ms * 1000)
            return dt.timestamp()
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
    return to_state in ("Menus", "MainMenu") and ("Match State Changed" in line or "LogGameFlowStateManager" in line)


def parse_map_update_from_line(
    line: str, current_map_name: str, is_range: bool
) -> Tuple[str, bool, bool, bool]:
    """ログ行から(更新後マップ名, 射撃場フラグ, 実マップ更新有無, メニューリセット有無)を返す"""
    if "LogMapLoadModel: Update:" not in line and "/Game/Maps/" not in line:
        return current_map_name, is_range, False, False

    m_map = MAP_NAME_PATTERN.search(line)
    raw_map_name = m_map.group(1).strip() if m_map else ""

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
