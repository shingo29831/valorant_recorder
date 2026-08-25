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
    nvenc_driver_old="⚠️ NVIDIA Driver is too old for NVENC.\nPlease update to 610.00 or newer."
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
    nvenc_driver_old="⚠️ NVIDIAドライバが古いためNVENCが使用できません。\nバージョン 610.00 以降にアップデートしてください。"
)

def get_trans(lang_code: str) -> Translations:
    if lang_code == "ja":
        return JA
    return EN
