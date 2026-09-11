from dataclasses import dataclass

@dataclass(frozen=True)
class Translations:
    settings_title: str
    back_to_recordings: str
    save_directory: str
    browse: str
    riot_id: str
    tag_line: str
    fetch_from_valorant: str
    fetch_success: str
    fetch_failed: str
    recording_fps: str
    encoder: str
    resolution: str
    auto_delete_after_days: str
    apply: str
    system_gain: str
    system_level: str
    microphone: str
    mic_gain: str
    noise_cancel: str
    noise_gate: str
    mic_level: str
    listen_to_mic: str
    monitor_warning: str
    confirm_auto_delete_change: str
    confirm_auto_delete_change_msg: str
    confirm_auto_delete_disable_msg: str
    yes: str
    no: str
    never: str
    language: str
    select_directory: str
    move_files_title: str
    move_files_msg: str
    copying_files: str
    cancel: str
    delete_original_title: str
    delete_original_msg: str
    confirm_deletion_title: str
    confirm_deletion_msg: str
    region: str
    nvenc_driver_old: str
    create_clip: str
    set_start: str
    set_end: str
    generate: str
    generating_clip: str
    clip_success: str
    clip_failed: str
    tab_general: str
    tab_record: str
    tab_playback: str
    system_volume: str
    mic_volume: str
    video_settings: str
    audio_settings: str
    none_option: str
    nvidia_broadcast_not_found_title: str
    nvidia_broadcast_not_found_msg: str
    filter_clear: str
    filter_favorite: str
    filter_agent: str
    filter_result: str
    filter_map: str
    result_win: str
    result_loss: str
    result_draw: str
    auto_start: str
    enable: str
    clip_save_directory: str
    check_update: str
    checking_update: str
    up_to_date: str
    up_to_date_msg: str
    mode_all: str
    mode_competitive: str
    mode_unrated: str
    mode_deathmatch: str
    mode_swiftplay: str
    all_records: str
    sort_date_desc: str
    sort_date_asc: str
    sort_mmr_desc: str
    sort_mmr_asc: str
    log_player_detected: str
    log_player_detect_failed: str
    log_manual_already_recording: str
    log_manual_starting: str
    log_manual_recording_to: str
    log_manual_start_failed: str
    log_manual_stopping: str
    log_real_match_end: str
    log_stopping_real_match_end: str
    log_range_detected_skip: str
    log_new_match_during_delay: str
    log_already_recording_cont: str
    log_recovery_mode: str
    log_match_started: str
    log_recording_to: str
    log_start_failed: str
    log_round_phase_changed: str
    log_performance_drop: str
    log_match_ended_wait: str
    log_stopping_after_delay: str
    log_stop_failed: str
    log_api_checking: str
    log_api_no_player_id: str
    log_api_fetching: str
    log_api_mmr_skipped: str
    log_api_fetch_success: str
    log_api_fetch_error: str
    log_storage_saved: str
    log_process_metadata_failed: str
    log_api_not_found: str
    log_bg_delete_failed: str
    log_bg_auto_deleted: str
    log_ffmpeg_crashed: str
    log_range_transition_cancel: str
    log_cancel_stop_failed: str
    log_cancelled_video_deleted: str
    log_cancel_delete_failed: str
    log_bg_pending_videos: str
    log_bg_player_detected: str
    log_bg_fetching: str
    log_bg_api_error: str
    log_bg_fetch_failed_perm: str
    log_bg_match_found: str
    log_bg_mmr_skipped: str
    log_bg_saved: str
    log_bg_custom_or_unavailable: str
    log_bg_error: str
    log_app_initialized: str
    log_fatal_error: str

EN = Translations(
    settings_title="APPLICATION SETTINGS",
    back_to_recordings="← BACK TO RECORDINGS",
    save_directory="Save Directory:",
    browse="Browse",
    riot_id="Riot ID:",
    tag_line="Tag Line:",
    fetch_from_valorant="Fetch from Valorant",
    fetch_success="Successfully fetched Riot ID and Tag Line from Valorant.",
    fetch_failed="Failed to fetch. Please make sure Valorant is running.",
    recording_fps="Recording FPS:",
    encoder="Encoder:",
    resolution="Resolution:",
    auto_delete_after_days="Auto Delete After (days):",
    apply="Apply",
    system_gain="System Gain:",
    system_level="System Level:",
    microphone="Microphone:",
    mic_gain="Mic Gain:",
    noise_cancel="Noise Cancel:",
    noise_gate="Noise Gate:",
    mic_level="Mic Level:",
    listen_to_mic="Listen to Microphone (Monitor)",
    monitor_warning="Note: AI (RNNoise) effect is applied only in actual recordings, not in this monitor.",
    confirm_auto_delete_change="Confirm Auto-Delete Change",
    confirm_auto_delete_change_msg="Are you sure you want to change the auto-delete period to {days} days?",
    confirm_auto_delete_disable_msg="Are you sure you want to disable auto-delete?",
    yes="Yes",
    no="No",
    never="Never",
    language="Language:",
    select_directory="Select Directory",
    move_files_title="Move Files?",
    move_files_msg="Do you want to move existing recordings to the new location?\n\nFrom: {old_dir}\nTo: {new_dir}",
    copying_files="Copying files...",
    cancel="Cancel",
    delete_original_title="Delete Original Files?",
    delete_original_msg="Videos have been copied to the new location.\nDo you want to delete the original files in:\n{old_dir}?",
    confirm_deletion_title="Confirm Deletion",
    confirm_deletion_msg="Are you absolutely sure you want to delete the original files? This action cannot be undone.",
    region="Region:",
    nvenc_driver_old="⚠️ NVIDIA Driver is too old for NVENC.\nPlease update to 610.00 or newer.",
    create_clip="Create Clip",
    set_start="Set Start",
    set_end="Set End",
    generate="Generate",
    generating_clip="Generating clip...",
    clip_success="Clip generated successfully:\n{path}",
    clip_failed="Failed to generate clip:\n{error}",
    tab_general="General",
    tab_record="Recording",
    tab_playback="Playback",
    system_volume="System Volume",
    mic_volume="Mic Volume",
    video_settings="Video Settings",
    audio_settings="Audio Settings",
    none_option="None",
    nvidia_broadcast_not_found_title="NVIDIA Broadcast Not Found",
    nvidia_broadcast_not_found_msg="NVIDIA Broadcast microphone was not found in the device list.\n\nPlease ensure the NVIDIA Broadcast app is installed, running, and the microphone effect is turned on.",
    filter_clear="Clear Filter",
    filter_favorite="Favorite",
    filter_agent="Agent",
    filter_result="Result",
    filter_map="Map",
    result_win="Win",
    result_loss="Loss",
    result_draw="Draw",
    auto_start="Auto Start on Boot:",
    enable="Enable",
    clip_save_directory="Clip Save Directory:",
    check_update="Check for Updates",
    checking_update="Checking...",
    up_to_date="Up to Date",
    up_to_date_msg="You are using the latest version.",
    mode_all="All",
    mode_competitive="Competitive",
    mode_unrated="Unrated",
    mode_deathmatch="Deathmatch",
    mode_swiftplay="Swiftplay",
    all_records="All Records",
    sort_date_desc="Date (Newest)",
    sort_date_asc="Date (Oldest)",
    sort_mmr_desc="MMR Change (Highest)",
    sort_mmr_asc="MMR Change (Lowest)",
    log_player_detected="[Watcher] Player detected: {name}#{tag} (Region: {region})",
    log_player_detect_failed="[Watcher] Failed to detect player from local API.",
    log_manual_already_recording="[Manual] Already recording.",
    log_manual_starting="[Manual] Starting manual recording...",
    log_manual_recording_to="[Manual] Recording to: {path}",
    log_manual_start_failed="[Error] Failed to start manual recording: {error}",
    log_manual_stopping="[Manual] Stopping manual recording...",
    log_real_match_end="[Recorder] Real match end verified in logs.",
    log_stopping_real_match_end="[Recorder] Stopping recording on real match end...",
    log_range_detected_skip="[Recorder] Range detected. Skipping recording and API fetch.",
    log_new_match_during_delay="[Recorder] New match started during delay. Stopping previous recording immediately...",
    log_already_recording_cont="[Recorder] Already recording. Continuing...",
    log_recovery_mode="[Recorder] Match in progress detected (Recovery mode). Auto-recovering recording...",
    log_match_started="[Recorder] Match started. Starting FFmpeg recording...",
    log_recording_to="[Recorder] Recording to: {path}",
    log_start_failed="[Error] Failed to start recording: {error}",
    log_round_phase_changed="[Recorder] Round phase changed: {phase} at {ts}ms (UNIX)",
    log_performance_drop="[Recorder] Performance drop detected. Lowering FFmpeg priority...",
    log_match_ended_wait="[Recorder] Match ended. Waiting 15 seconds to capture result screen...",
    log_stopping_after_delay="[Recorder] Stopping recording after delay...",
    log_stop_failed="[Error] Failed to stop recording: {error}",
    log_api_checking="[API] Checking for match data...",
    log_api_no_player_id="[API] No player ID detected. Saving as local-only match.",
    log_api_fetching="[API] Fetching match data for {name}#{tag} (Region: {region})...",
    log_api_mmr_skipped="[API] MMR fetch skipped (likely not competitive): {error}",
    log_api_fetch_success="[API] Successfully fetched current match data.",
    log_api_fetch_error="[API] Error fetching match data (attempt {attempt}/3): {error}",
    log_storage_saved="[Storage] Metadata saved: {path}",
    log_process_metadata_failed="[Error] Failed to process match metadata: {error}",
    log_api_not_found="[API] Match data not found after retries. Saving as local-only match.",
    log_bg_delete_failed="[Background] Failed to delete old file {path}: {error}",
    log_bg_auto_deleted="[Background] Auto-deleted {count} old file(s).",
    log_ffmpeg_crashed="[Watcher] FFmpeg process crashed (code {code}). Auto-restarting recording...",
    log_range_transition_cancel="[Recorder] Range transition detected during recording. Cancelling recording.",
    log_cancel_stop_failed="[Error] Failed to stop recording during cancel: {error}",
    log_cancelled_video_deleted="[Recorder] Cancelled video file deleted: {path}",
    log_cancel_delete_failed="[Error] Failed to delete cancelled video: {error}",
    log_bg_pending_videos="[Background] Found {count} pending video(s). Checking API...",
    log_bg_player_detected="[Background] Player detected: {name}#{tag} (Region: {region})",
    log_bg_fetching="[Background] Fetching match data for {name}#{tag} (Region: {region})...",
    log_bg_api_error="[Background] API fetch error: {error}",
    log_bg_fetch_failed_perm="[Background] Video {file} API fetch failed permanently. Saving as local-only.",
    log_bg_match_found="[Background] Match found for {file}.",
    log_bg_mmr_skipped="[Background] MMR fetch skipped (likely not competitive): {error}",
    log_bg_saved="[Background] Saved metadata: {path}",
    log_bg_custom_or_unavailable="[Background] Video {file} is likely a custom match or API not available. Skipping.",
    log_bg_error="[Background] Error: {error}",
    log_app_initialized="Valorant Recorder App initialized. Watching logs...",
    log_fatal_error="Fatal error: {error}"
)

JA = Translations(
    settings_title="アプリケーション設定",
    back_to_recordings="← 録画一覧に戻る",
    save_directory="保存先ディレクトリ:",
    browse="参照",
    riot_id="Riot ID:",
    tag_line="タグライン:",
    fetch_from_valorant="Valorantから取得",
    fetch_success="ValorantからRiot IDとタグラインを正常に取得しました。",
    fetch_failed="取得に失敗しました。Valorantが起動していることを確認してください。",
    recording_fps="録画 FPS:",
    encoder="エンコーダ:",
    resolution="解像度:",
    auto_delete_after_days="自動削除 (日後):",
    apply="適用",
    system_gain="システム音量:",
    system_level="システムレベル:",
    microphone="マイク:",
    mic_gain="マイク音量:",
    noise_cancel="ノイズキャンセル:",
    noise_gate="ノイズゲート:",
    mic_level="マイクレベル:",
    listen_to_mic="マイクの音を聞く (モニター)",
    monitor_warning="注: AI (RNNoise) エフェクトは実際の録画にのみ適用され、このモニターには適用されません。",
    confirm_auto_delete_change="自動削除の変更確認",
    confirm_auto_delete_change_msg="自動削除の期間を {days} 日に変更してもよろしいですか？",
    confirm_auto_delete_disable_msg="自動削除を無効にしてもよろしいですか？",
    yes="はい",
    no="いいえ",
    never="なし",
    language="言語 (Language):",
    select_directory="ディレクトリの選択",
    move_files_title="ファイルを移動しますか？",
    move_files_msg="既存の録画を新しい場所に移動しますか？\n\n移動元: {old_dir}\n移動先: {new_dir}",
    copying_files="ファイルをコピー中...",
    cancel="キャンセル",
    delete_original_title="元のファイルを削除しますか？",
    delete_original_msg="動画が新しい場所にコピーされました。\n元のファイルを削除しますか？\n{old_dir}",
    confirm_deletion_title="削除の確認",
    confirm_deletion_msg="本当に元のファイルを削除してもよろしいですか？この操作は取り消せません。",
    region="リージョン:",
    nvenc_driver_old="⚠️ NVIDIAドライバが古いためNVENCが使用できません。\nバージョン 610.00 以降にアップデートしてください。",
    create_clip="クリップ作成",
    set_start="開始位置",
    set_end="終了位置",
    generate="生成",
    generating_clip="クリップを生成中...",
    clip_success="クリップを生成しました:\n{path}",
    clip_failed="クリップの生成に失敗しました:\n{error}",
    tab_general="一般",
    tab_record="録画",
    tab_playback="再生",
    system_volume="システム音量",
    mic_volume="マイク音量",
    video_settings="映像設定",
    audio_settings="音声設定",
    none_option="なし",
    nvidia_broadcast_not_found_title="NVIDIA Broadcast が見つかりません",
    nvidia_broadcast_not_found_msg="NVIDIA Broadcast マイクがデバイスリストに見つかりませんでした。\n\nNVIDIA Broadcast アプリがインストールされ、起動しており、マイクエフェクトがオンになっていることを確認してください。",
    filter_clear="フィルターをクリア",
    filter_favorite="お気に入り",
    filter_agent="エージェント",
    filter_result="勝敗",
    filter_map="マップ",
    result_win="勝利",
    result_loss="敗北",
    result_draw="引き分け",
    auto_start="PC起動時に自動起動:",
    enable="有効にする",
    clip_save_directory="クリップ保存先:",
    check_update="アップデートを確認",
    checking_update="確認中...",
    up_to_date="最新版です",
    up_to_date_msg="現在最新バージョンを使用しています。",
    mode_all="全て",
    mode_competitive="コンペティティブ",
    mode_unrated="アンレート",
    mode_deathmatch="デスマッチ",
    mode_swiftplay="スイフト",
    all_records="すべての録画",
    sort_date_desc="日付 (新しい順)",
    sort_date_asc="日付 (古い順)",
    sort_mmr_desc="MMR変動 (高い順)",
    sort_mmr_asc="MMR変動 (低い順)",
    log_player_detected="[Watcher] プレイヤーを検出しました: {name}#{tag} (リージョン: {region})",
    log_player_detect_failed="[Watcher] ローカルAPIからのプレイヤー検出に失敗しました。",
    log_manual_already_recording="[Manual] 既に録画中です。",
    log_manual_starting="[Manual] 手動録画を開始します...",
    log_manual_recording_to="[Manual] 録画先: {path}",
    log_manual_start_failed="[Error] 手動録画の開始に失敗しました: {error}",
    log_manual_stopping="[Manual] 手動録画を停止します...",
    log_real_match_end="[Recorder] ログから実際の試合終了を確認しました。",
    log_stopping_real_match_end="[Recorder] 試合終了により録画を停止します...",
    log_range_detected_skip="[Recorder] 射撃訓練場(Range)を検知しました。録画とAPI取得をスキップします。",
    log_new_match_during_delay="[Recorder] 待機中に新しい試合が開始されました。直前の録画を即座に停止します...",
    log_already_recording_cont="[Recorder] 既に録画中です。継続します...",
    log_recovery_mode="[Recorder] 試合中の状態を検知しました(リカバリーモード)。録画を自動再開します...",
    log_match_started="[Recorder] 試合開始。FFmpeg録画を開始します...",
    log_recording_to="[Recorder] 録画先: {path}",
    log_start_failed="[Error] 録画の開始に失敗しました: {error}",
    log_round_phase_changed="[Recorder] ラウンドフェーズ変更: {phase} ({ts}ms UNIX)",
    log_performance_drop="[Recorder] パフォーマンス低下を検知しました。FFmpegの優先度を下げます...",
    log_match_ended_wait="[Recorder] 試合終了。リザルト画面をキャプチャするため15秒待機します...",
    log_stopping_after_delay="[Recorder] 待機完了。録画を停止します...",
    log_stop_failed="[Error] 録画の停止に失敗しました: {error}",
    log_api_checking="[API] 試合データを確認中...",
    log_api_no_player_id="[API] プレイヤーIDが検出されていません。ローカルのみの試合として保存します。",
    log_api_fetching="[API] 試合データを取得中: {name}#{tag} (リージョン: {region})...",
    log_api_mmr_skipped="[API] MMR取得をスキップしました (コンペティティブではない可能性): {error}",
    log_api_fetch_success="[API] 最新の試合データを正常に取得しました。",
    log_api_fetch_error="[API] 試合データの取得エラー (試行 {attempt}/3): {error}",
    log_storage_saved="[Storage] メタデータを保存しました: {path}",
    log_process_metadata_failed="[Error] 試合メタデータの処理に失敗しました: {error}",
    log_api_not_found="[API] リトライ後も試合データが見つかりませんでした。ローカルのみの試合として保存します。",
    log_bg_delete_failed="[Background] 古いファイルの削除に失敗しました {path}: {error}",
    log_bg_auto_deleted="[Background] {count} 個の古いファイルを自動削除しました。",
    log_ffmpeg_crashed="[Watcher] FFmpegプロセスがクラッシュしました (コード {code})。録画を自動再開します...",
    log_range_transition_cancel="[Recorder] 録画中に射撃場(Range)への遷移を検知しました。録画をキャンセルします。",
    log_cancel_stop_failed="[Error] キャンセル中の録画停止に失敗しました: {error}",
    log_cancelled_video_deleted="[Recorder] キャンセルされた動画ファイルを削除しました: {path}",
    log_cancel_delete_failed="[Error] キャンセルされた動画の削除に失敗しました: {error}",
    log_bg_pending_videos="[Background] {count} 個の未処理動画が見つかりました。APIを確認します...",
    log_bg_player_detected="[Background] プレイヤーを検出しました: {name}#{tag} (リージョン: {region})",
    log_bg_fetching="[Background] 試合データを取得中: {name}#{tag} (リージョン: {region})...",
    log_bg_api_error="[Background] API取得エラー: {error}",
    log_bg_fetch_failed_perm="[Background] 動画 {file} のAPI取得に恒久的に失敗しました。ローカルのみとして保存します。",
    log_bg_match_found="[Background] 動画 {file} の試合データが見つかりました。",
    log_bg_mmr_skipped="[Background] MMR取得をスキップしました (コンペティティブではない可能性): {error}",
    log_bg_saved="[Background] メタデータを保存しました: {path}",
    log_bg_custom_or_unavailable="[Background] 動画 {file} はカスタムマッチかAPIが利用できない可能性があります。スキップします。",
    log_bg_error="[Background] エラー: {error}",
    log_app_initialized="Valorant Recorder アプリが初期化されました。ログを監視しています...",
    log_fatal_error="致命的なエラー: {error}"
)

def get_trans(lang_code: str) -> Translations:
    if lang_code == "ja":
        return JA
    return EN
