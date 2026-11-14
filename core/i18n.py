"""Module: @role: 多言語翻訳データクラス定義と言語コードに応じた翻訳インスタンス取得を担うモジュール"""
from dataclasses import dataclass
from core.i18n_en import EN_DICT
from core.i18n_ja import JA_DICT

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
    app_logs: str
    open_log_folder: str
    copy_log: str
    copy_diagnostic_report: str
    copied_to_clipboard: str
    log_not_found: str
    initializing: str
    start_recording: str
    stop_recording: str
    recording_started: str
    recording_stopped: str
    update_available_title: str
    update_available_msg: str
    update_now: str
    update_later: str
    update_downloading: str
    update_failed_title: str
    update_failed_msg: str
    env_test_failed_title: str
    env_test_failed_msg: str
    run_env_test: str
    env_test_running: str
    env_test_success_title: str
    env_test_success_msg: str
    env_test_mixed_msg: str
    lightweight_mode: str
    auto_perf_control: str
    recording_monitor: str = "Recording Monitor:"
    primary_monitor: str = "Primary Monitor"
    video_format: str = "Capture Mode:"
    system_audio_device: str = "System Audio:"
    default_device: str = "Default"
    mic_preprocess: str = "Pre-process (HPF):"
    quit_app: str = "Exit Application"
    confirm_quit_title: str = "Confirm Exit"
    confirm_quit_msg: str = "Are you sure you want to completely exit the application?"
    tutorial_title: str = "How to Use ValoReco"
    tutorial_welcome: str = "Welcome to ValoReco!"
    tutorial_auto_title: str = "1. Automatic Match Recording"
    tutorial_auto_desc: str = ""
    tutorial_manual_title: str = "2. Manual Start & Stop"
    tutorial_manual_desc: str = ""
    tutorial_dont_show_again: str = "Do not show again"
    tutorial_start_btn: str = "Get Started"
    env_test_dialog_title: str = "Environment Test"
    env_test_diagnosing: str = "Diagnosing environment..."
    env_test_all_passed: str = "All environment tests passed successfully."
    env_test_some_failed: str = "Issues were detected in some tests."
    env_status_waiting: str = "Waiting"
    env_status_running: str = "Testing..."
    env_status_passed: str = "Passed"
    env_status_failed: str = "Failed"
    env_item_ffmpeg: str = "FFmpeg Executable"
    env_item_encoder: str = "Video Encoder"
    env_item_audio: str = "Audio Devices"
    env_item_log: str = "VALORANT Log Access"
    env_item_pipeline: str = "Recording Pipeline"
    retest: str = "Retest"
    close: str = "Close"
    spec_dialog_title: str = "System Diagnosis & Recommended Settings"
    spec_dialog_subtitle: str = "Optimal recording settings have been detected for your PC."
    spec_hardware_title: str = "Detected System Specifications"
    spec_cpu: str = "CPU"
    spec_memory: str = "Memory"
    spec_gpu: str = "GPU (Graphics)"
    spec_recommended_title: str = "Recommended Recording Settings"
    spec_apply_btn: str = "Apply Recommended Settings"
    spec_skip_btn: str = "Keep Current Settings"
    run_spec_diag: str = "Diagnose Recommended Settings"
    close_confirm_title: str = "Confirm Exit"
    close_confirm_msg: str = "Do you want to completely exit the application, including background monitoring?\n\n* If you exit completely, VALORANT matches will not be recorded automatically.\n* Select 'Keep in Background' to minimize to the system tray and keep recording active."
    close_btn_quit: str = "Exit Completely"
    close_btn_minimize: str = "Keep in Background"

EN = Translations(**EN_DICT)
JA = Translations(**JA_DICT)

def get_trans(lang_code: str) -> Translations:
    if lang_code == "ja":
        return JA
    return EN
