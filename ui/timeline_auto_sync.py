"""Module: @role: 動画フレームのハードカット解析によりAPIタイムラインとの時刻オフセットを自動推定するモジュール"""
from dataclasses import dataclass
import os
import shutil
import statistics
import subprocess
from typing import Callable, Optional
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from ui.player_utils import resolve_match_player
from ui.timeline_builder import compute_timeline_base_context

_FRAME_W = 48
_FRAME_H = 27
_FRAME_PIXELS = _FRAME_W * _FRAME_H
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


@dataclass
class AutoSyncResult:
    applied: bool
    sync_offset_ms: int
    confidence: float
    anchor_count: int
    reason: str = ""


def resolve_ffmpeg_executable() -> Optional[str]:
    app_data_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ValoReco")
    cand = os.path.join(app_data_dir, "bin", "ffmpeg.exe")
    if os.path.isfile(cand):
        return cand
    return shutil.which("ffmpeg")


def _get_buy_phase_ms(mode: str, round_index: int) -> int:
    m = (mode or "").lower()
    if "spike rush" in m:
        return 20000
    if "swiftplay" in m:
        return 45000 if round_index == 0 else 30000
    if round_index in (0, 12) or round_index >= 24:
        return 45000
    return 30000


def _get_halftime_round_index(mode: str) -> int:
    m = (mode or "").lower()
    if "swiftplay" in m:
        return 3
    if "spike rush" in m:
        return 2
    return 11


def extract_api_cut_anchors(
    match_info: dict,
    riot_id: str = "",
    tag_line: str = "",
    known_accounts: Optional[list[str]] = None,
) -> tuple[list[tuple[int, float]], bool]:
    """APIデータから画面ハードカットが起きる予測時刻(kill_time_in_match基準ms, 重み)とデスマッチ判定を返す"""
    kills = match_info.get("kills") or []
    rounds = match_info.get("rounds") or []
    meta = match_info.get("metadata") or {}
    mode = str(meta.get("mode") or "")
    queue = str(meta.get("queue") or "")
    is_dm = "deathmatch" in mode.lower() or "deathmatch" in queue.lower()

    if is_dm:
        player = resolve_match_player(match_info, riot_id, tag_line, known_accounts)
        target_puuid = player.get("puuid") if player else None
        target_name = player.get("name") if player else None
        anchors: list[tuple[int, float]] = []
        for k in kills:
            t_ms = int(k.get("kill_time_in_match") or 0)
            if t_ms <= 0:
                continue
            is_death = False
            if target_puuid and k.get("victim_puuid") == target_puuid:
                is_death = True
            elif target_name and target_name in str(k.get("victim_display_name") or ""):
                is_death = True
            if is_death:
                # デスマッチは死亡約3秒後にリスポーンによる全画面切替が発生
                anchors.append((t_ms + 3000, 0.85))
        return sorted(anchors, key=lambda x: x[0]), True

    kills_by_round: dict[int, list[dict]] = {}
    for k in kills:
        r_idx = k.get("round")
        if isinstance(r_idx, int) and r_idx >= 0:
            kills_by_round.setdefault(r_idx, []).append(k)

    round_starts: dict[int, int] = {}
    round_last_kills: dict[int, int] = {}
    for r_idx, r_kills in kills_by_round.items():
        starts = [
            int(k["kill_time_in_match"] - k["kill_time_in_round"])
            for k in r_kills
            if (k.get("kill_time_in_match") or 0) > 0 and (k.get("kill_time_in_round") or 0) > 0
        ]
        if starts:
            round_starts[r_idx] = int(statistics.median(starts))
        kill_times = [int(k.get("kill_time_in_match") or 0) for k in r_kills if (k.get("kill_time_in_match") or 0) > 0]
        if kill_times:
            round_last_kills[r_idx] = max(kill_times)

    anchors_map: dict[int, float] = {}
    halftime_idx = _get_halftime_round_index(mode)

    if rounds and len(rounds) >= 2:
        for r_idx in range(len(rounds) - 1):
            if r_idx == halftime_idx:
                continue
            r_obj = rounds[r_idx]
            end_type = str(r_obj.get("end_type") or "").lower()
            bomb_planted = bool(r_obj.get("bomb_planted"))
            cut_ms: Optional[int] = None

            if "eliminated" in end_type and not bomb_planted:
                if r_idx in round_last_kills:
                    cut_ms = round_last_kills[r_idx] + 7000
            elif "defused" in end_type:
                defuse_ev = r_obj.get("defuse_events") or {}
                d_time = defuse_ev.get("defuse_time_in_round")
                if r_idx in round_starts and isinstance(d_time, (int, float)) and d_time > 0:
                    cut_ms = round_starts[r_idx] + int(d_time) + 7000
            elif "detonated" in end_type:
                plant_ev = r_obj.get("plant_events") or {}
                p_time = plant_ev.get("plant_time_in_round")
                if r_idx in round_starts and isinstance(p_time, (int, float)) and p_time > 0:
                    cut_ms = round_starts[r_idx] + int(p_time) + 45000 + 7000

            if cut_ms is not None and cut_ms > 0:
                anchors_map[cut_ms] = 1.0

    for r_idx, r_start in sorted(round_starts.items()):
        if r_idx == 0:
            continue
        buy_ms = _get_buy_phase_ms(mode, r_idx)
        cand_cut = r_start - buy_ms
        if cand_cut <= 0:
            continue
        if not any(abs(existing - cand_cut) < 10000 for existing in anchors_map):
            anchors_map[cand_cut] = 0.7

    return sorted(anchors_map.items(), key=lambda x: x[0]), False


def compute_base_api_to_video_offset_ms(match_info: dict, duration_ms: int = 0) -> int:
    """timeline_builderと完全に同一の基準オフセット(t_video = t_api + base_offset)を返す"""
    return int(compute_timeline_base_context(match_info, duration_ms)["base_offset_ms"])


def _select_scan_window(
    anchors: list[tuple[int, float]],
    base_offset_ms: int,
    duration_ms: int,
    search_radius_ms: int,
    max_span_ms: int,
) -> tuple[int, int, list[tuple[int, float]]]:
    if not anchors:
        return 0, 0, []

    best_subset: list[tuple[int, float]] = []
    best_score = -1.0

    # 動画範囲内に収まり、信頼度の高いアンカーが最も密に集まる部分区間を選択
    for i in range(len(anchors)):
        subset: list[tuple[int, float]] = []
        start_api = anchors[i][0]
        for j in range(i, len(anchors)):
            if anchors[j][0] - start_api > max_span_ms:
                break
            proj_t = anchors[j][0] + base_offset_ms
            if -search_radius_ms <= proj_t <= duration_ms + search_radius_ms:
                subset.append(anchors[j])
            if len(subset) >= 5:
                break
        w_sum = sum(w for _, w in subset)
        if w_sum > best_score:
            best_score = w_sum
            best_subset = subset

    if len(best_subset) < 2:
        valid = [a for a in anchors if -search_radius_ms <= (a[0] + base_offset_ms) <= duration_ms + search_radius_ms]
        best_subset = valid[:5] if valid else anchors[:5]

    min_proj = min(a[0] + base_offset_ms for a in best_subset)
    max_proj = max(a[0] + base_offset_ms for a in best_subset)
    scan_start_ms = max(0, min_proj - search_radius_ms - 3000)
    scan_end_ms = min(duration_ms, max_proj + search_radius_ms + 3000)
    return scan_start_ms, scan_end_ms, best_subset


def _compute_cut_score_curve(raw_bytes: bytes, fps: int) -> np.ndarray:
    frame_bytes = _FRAME_PIXELS
    num_frames = len(raw_bytes) // frame_bytes
    if num_frames < fps * 4:
        return np.array([], dtype=np.float32)

    frames = np.frombuffer(raw_bytes[: num_frames * frame_bytes], dtype=np.uint8).reshape(num_frames, frame_bytes).astype(np.float32)
    means = frames.mean(axis=1, keepdims=True)
    stds = np.maximum(frames.std(axis=1, keepdims=True), 5.0)
    norm_frames = (frames - means) / stds

    win = max(2, int(round(fps * 1.25)))
    if num_frames <= win * 2 + 2:
        return np.array([], dtype=np.float32)

    cumsum = np.zeros((num_frames + 1, frame_bytes), dtype=np.float32)
    np.cumsum(norm_frames, axis=0, out=cumsum[1:])

    idx = np.arange(win, num_frames - win)
    before_avg = (cumsum[idx] - cumsum[idx - win]) / float(win)
    after_avg = (cumsum[idx + win] - cumsum[idx]) / float(win)
    d_win = np.mean(np.abs(after_avg - before_avg), axis=1)

    d_inst_all = np.mean(np.abs(norm_frames[1:] - norm_frames[:-1]), axis=1)
    d_inst = d_inst_all[idx - 1]

    scores = np.zeros(num_frames, dtype=np.float32)
    scores[idx] = d_win * np.minimum(d_inst, 2.0)
    return scores


def score_offsets_against_curve(
    scores: np.ndarray,
    fps: int,
    scan_start_ms: int,
    base_offset_ms: int,
    anchors: list[tuple[int, float]],
    search_radius_ms: int,
    tol_ms: int,
) -> AutoSyncResult:
    if len(scores) < fps * 4 or not anchors:
        return AutoSyncResult(False, 0, 0.0, len(anchors), "insufficient_frames")

    dt_ms = 1000.0 / float(fps)
    tol_frames = max(1, int(round(tol_ms / dt_ms)))
    max_shift_frames = max(1, int(round(search_radius_ms / dt_ms)))
    shifts = np.arange(-max_shift_frames, max_shift_frames + 1, dtype=np.int32)

    shift_scores = np.full(len(shifts), np.nan, dtype=np.float32)
    n_frames = len(scores)

    for s_i, d_frame in enumerate(shifts):
        d_ms = int(round(d_frame * dt_ms))
        per_anchor_vals: list[float] = []
        for api_ms, weight in anchors:
            t_vid = api_ms + base_offset_ms + d_ms
            center_idx = int(round((t_vid - scan_start_ms) / dt_ms))
            if tol_frames <= center_idx < (n_frames - tol_frames):
                local_peak = float(np.max(scores[center_idx - tol_frames : center_idx + tol_frames + 1]))
                per_anchor_vals.append(local_peak * weight)

        required_min = max(2, min(len(anchors), 3))
        if len(per_anchor_vals) < required_min:
            continue

        # タイムアウト等による1ラウンドの外れ値を除外して安定化
        if len(per_anchor_vals) >= 4:
            per_anchor_vals.sort()
            used_vals = per_anchor_vals[1:]
        else:
            used_vals = per_anchor_vals
        shift_scores[s_i] = float(np.mean(used_vals))

    valid_mask = ~np.isnan(shift_scores)
    if np.count_nonzero(valid_mask) < 15:
        return AutoSyncResult(False, 0, 0.0, len(anchors), "insufficient_search_range")

    valid_scores = shift_scores[valid_mask]
    median_val = float(np.median(valid_scores))
    mad = float(np.median(np.abs(valid_scores - median_val)))
    std_val = float(np.std(valid_scores))
    scale = max(mad * 1.4826, std_val * 0.5, 1e-6)

    best_pos = int(np.nanargmax(shift_scores))
    best_score = float(shift_scores[best_pos])
    best_d_frame = int(shifts[best_pos])
    best_offset_ms = int(round(best_d_frame * dt_ms))

    z_score = (best_score - median_val) / scale

    excl_frames = max(2, int(round(3000.0 / dt_ms)))
    non_peak = shift_scores.copy()
    lo = max(0, best_pos - excl_frames)
    hi = min(len(non_peak), best_pos + excl_frames + 1)
    non_peak[lo:hi] = np.nan
    if np.all(np.isnan(non_peak)):
        second_score = median_val
    else:
        second_score = float(np.nanmax(non_peak))

    margin_ratio = (best_score - median_val) / max(second_score - median_val, 1e-6)
    confident = (z_score >= 3.0) and (margin_ratio >= 1.20)

    return AutoSyncResult(
        applied=confident,
        sync_offset_ms=best_offset_ms if confident else 0,
        confidence=float(z_score),
        anchor_count=len(anchors),
        reason="ok" if confident else f"low_confidence(z={z_score:.2f},ratio={margin_ratio:.2f})",
    )


def estimate_video_timeline_offset(
    video_path: str,
    match_info: dict,
    duration_ms: int,
    riot_id: str = "",
    tag_line: str = "",
    known_accounts: Optional[list[str]] = None,
    wide_search: bool = False,
    register_process_cb: Optional[Callable[[Optional[subprocess.Popen]], None]] = None,
    is_cancelled_cb: Optional[Callable[[], bool]] = None,
) -> AutoSyncResult:
    if not video_path or not os.path.exists(video_path) or duration_ms <= 0:
        return AutoSyncResult(False, 0, 0.0, 0, "invalid_video")

    ffmpeg_path = resolve_ffmpeg_executable()
    if not ffmpeg_path:
        return AutoSyncResult(False, 0, 0.0, 0, "ffmpeg_not_found")

    anchors, is_dm = extract_api_cut_anchors(match_info, riot_id, tag_line, known_accounts)
    min_anchors = 4 if is_dm else 3
    if len(anchors) < min_anchors:
        return AutoSyncResult(False, 0, 0.0, len(anchors), "few_anchors")

    base_offset_ms = compute_base_api_to_video_offset_ms(match_info, duration_ms)

    if wide_search:
        fps = 2
        search_radius_ms = min(480000, max(180000, duration_ms // 2))
        max_span_ms = min(240000, max(120000, duration_ms // 3))
    else:
        fps = 4
        search_radius_ms = 75000
        max_span_ms = 220000

    scan_start_ms, scan_end_ms, selected_anchors = _select_scan_window(
        anchors, base_offset_ms, duration_ms, search_radius_ms, max_span_ms
    )
    scan_dur_ms = scan_end_ms - scan_start_ms
    if scan_dur_ms < 15000:
        return AutoSyncResult(False, 0, 0.0, len(selected_anchors), "short_scan_window")

    raw_bytes = _decode_gray_frames(
        ffmpeg_path,
        video_path,
        scan_start_ms,
        scan_dur_ms,
        fps,
        register_process_cb=register_process_cb,
        is_cancelled_cb=is_cancelled_cb,
    )
    if not raw_bytes:
        return AutoSyncResult(False, 0, 0.0, len(selected_anchors), "decode_failed_or_cancelled")

    scores = _compute_cut_score_curve(raw_bytes, fps)
    tol_ms = 800 if is_dm else 550
    return score_offsets_against_curve(
        scores=scores,
        fps=fps,
        scan_start_ms=scan_start_ms,
        base_offset_ms=base_offset_ms,
        anchors=selected_anchors,
        search_radius_ms=search_radius_ms,
        tol_ms=tol_ms,
    )


def _decode_gray_frames(
    ffmpeg_path: str,
    video_path: str,
    scan_start_ms: int,
    scan_dur_ms: int,
    fps: int,
    register_process_cb: Optional[Callable[[Optional[subprocess.Popen]], None]] = None,
    is_cancelled_cb: Optional[Callable[[], bool]] = None,
) -> bytes:
    # 再生中のデコード負荷・カクつきを防ぐためプロセス優先度とデコードスレッド数を制限
    creationflags = (subprocess.CREATE_NO_WINDOW | _BELOW_NORMAL_PRIORITY_CLASS) if os.name == "nt" else 0
    for use_skip_noref in (True, False):
        if is_cancelled_cb and is_cancelled_cb():
            return b""
        cmd = [ffmpeg_path, "-y", "-v", "error", "-threads", "2"]
        if use_skip_noref:
            cmd.extend(["-skip_frame", "noref"])
        cmd.extend([
            "-ss", f"{scan_start_ms / 1000.0:.3f}",
            "-t", f"{scan_dur_ms / 1000.0:.3f}",
            "-an", "-sn", "-dn",
            "-i", video_path,
            "-vf", f"fps={fps},scale={_FRAME_W}:{_FRAME_H},format=gray",
            "-f", "rawvideo",
            "pipe:1",
        ])
        proc: Optional[subprocess.Popen] = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            if register_process_cb:
                register_process_cb(proc)
            stdout_data, _ = proc.communicate(timeout=45)
            if is_cancelled_cb and is_cancelled_cb():
                return b""
            if proc.returncode == 0 and stdout_data and len(stdout_data) >= _FRAME_PIXELS * fps * 4:
                return stdout_data
        except subprocess.TimeoutExpired:
            if proc:
                try:
                    proc.kill()
                except OSError:
                    pass
            return b""
        except OSError:
            return b""
        finally:
            if register_process_cb:
                register_process_cb(None)
    return b""


class TimelineAutoSyncThread(QThread):
    syncCompleted = pyqtSignal(int, object)

    def __init__(
        self,
        load_sequence: int,
        video_path: str,
        match_info: dict,
        duration_ms: int,
        riot_id: str,
        tag_line: str,
        known_accounts: Optional[list[str]] = None,
        wide_search: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.load_sequence = load_sequence
        self.video_path = video_path
        self.match_info = match_info
        self.duration_ms = duration_ms
        self.riot_id = riot_id
        self.tag_line = tag_line
        self.known_accounts = list(known_accounts or [])
        self.wide_search = wide_search
        self._cancelled = False
        self._active_proc: Optional[subprocess.Popen] = None

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._active_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def _set_proc(self, proc: Optional[subprocess.Popen]) -> None:
        self._active_proc = proc
        if self._cancelled and proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def run(self) -> None:
        try:
            result = estimate_video_timeline_offset(
                video_path=self.video_path,
                match_info=self.match_info,
                duration_ms=self.duration_ms,
                riot_id=self.riot_id,
                tag_line=self.tag_line,
                known_accounts=self.known_accounts,
                wide_search=self.wide_search,
                register_process_cb=self._set_proc,
                is_cancelled_cb=lambda: self._cancelled,
            )
        except Exception as e:
            result = AutoSyncResult(False, 0, 0.0, 0, str(e))
        if not self._cancelled:
            self.syncCompleted.emit(self.load_sequence, result)
