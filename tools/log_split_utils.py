"""Module: @role: 抽出・診断ログを貼り付け可能なサイズで複数パートファイルへ分割保存する共通ユーティリティ"""
import glob
import os
from typing import Iterable, List

DEFAULT_MAX_LINES_PER_PART = 250
DEFAULT_MAX_CHARS_PER_PART = 12000

EXCLUDE_LOG_PATTERNS = [
    r"LogNet:", r"LogOnline:", r"LogShooterVoice:", r"LogRiotTencentKms:", r"LogRiotClient:",
    r"LogRiotOS:", r"LogPlatform:", r"LogPakFile:", r"LogAudio:", r"LogAkAudio:", r"LogHttp:",
    r"LogUMG:", r"LogSlate:", r"LogGameMode:", r"LogTexture:", r"LogLoad:", r"LogStreaming:",
    r"LogD3D11RHI:", r"LogRHI:", r"LogRenderer:", r"LogContentStreaming:", r"LogTemp:",
    r"LogConfig:", r"LogInit:", r"LogShooterUI:", r"LogShooterEos:", r"LogVanguard:",
    r"LogConsoleManager:", r"LogDeviceProfileManager:", r"LogMemory:", r"LogShaderLibrary:",
    r"LogAntiLag2:", r"LogUObjectGlobals:", r"LogShooterGameUserSettings:", r"LogActor:",
    r"LogInterchangeImport:", r"LogRGIPatchlineData:", r"LogSlateStyle:", r"LogInventoryManager:",
    r"UmbraCulling:", r"LogRSOManager:", r"LogRiotGamesApiClient:", r"LogTelemetryManager:",
    r"LogRMSManager:", r"LogThreadedChatManager:", r"LogPlatformCommon:", r"LogJson:",
    r"LogDailyRewardsManager:", r"LogRoamingSettingsManager:", r"ImportText",
    r"LogPlatformInitializerV2:", r"LogTextChatManagerV2:", r"LogShooter: Display:",
    r"LogVNGManager:", r"LogActionBindingsManager:", r"LogCNAntiAddictionManager:",
    r"LogTextChatRoomV2:", r"LogCoreGameManager:", r"LogPregameManager:",
    r"LogPersonalizationManagerV2:", r"LogRMSService:", r"LogRewardGrantModelFactory:",
    r"LogContractsManager:", r"LogPremierSeasonsModel:", r"LogFlushGuard:", r"LogReplayData:",
    r"LogPresenceService:", r"LogRNetVoiceManager:", r"LogTravelManager:",
    r"LogBasePlayerController:", r"LogPlayerController:", r"LogPlatformPlayerManager:",
    r"LogStringTable:", r"LogMenuStackManager:", r"LogWebBrowser:", r"LogLoadTimeMetrics:",
    r"LogContentLibraryCharacter:", r"LogPlayerFeedbackManager:", r"LogShellScreen:",
    r"LogUINavigationModel:", r"LogContentLibrary:", r"LogRGIFriendsRiotGamesApi:",
    r"LogAresCommonAnalogCursor:", r"LogAresPlayerController:", r"LogShooterBlueprintLibrary:",
    r"LogShooterHUD:", r"LogShooterGameState:", r"LogShooterPlayerController:", r"LogScript:",
    r"LogPartyService:", r"LogSkeletalMesh:", r"LogAbilitySystem:", r"LogAresNetDriver:",
    r"LogShellScreenViewModel:", r"LogMeshMaterialManager:", r"LogActorComponent:",
    r"AnimBlueprintLog:", r"LogLandingScreen:", r"LogChatUtils:", r"LogAresListWidget:",
    r"LogRemoteClientMovementComponent:", r"LogPreloadManager:", r"r\.ScreenPercentage",
    r"PIE: Unable to listen", r"LogMMRManager:", r"LogFlyoutManager:", r"LogPhysics:",
    r"LogAresMinimapComponent:", r"LogIsLastPlayerAliveOnTeamViewModel:", r"LogInventory:",
    r"LogAres:", r"LogEffectContainer:", r"LogResourceComponent:",
    r"LogInstabilityTrackingDetails:", r"LogPrimitiveComponent:", r"LogScriptStateComponent:",
    r"LogMovieSceneECS:", r"LogMovieScene:", r"LogClientPerRoundTelemetryComponent:",
    r"LogUObjectBase:", r"LogAnimation:", r"LogParticles:", r"LogTransitionManagerWidget:",
    r"LogTextChatService:", r"LogSocialViewControllerV3:", r"LogPartyFunctionLibrary:",
    r"LogDisplayNameManager:", r"LogGameplayEffects:", r"LogMaterial:", r"ShooterUICoordinator:",
    r"LogFiringEffectComponent:", r"LogFiringStateComponent:", r"LogGameFlowPredictionManager:",
    r"LogMatchDetailsManager:", r"LogAccountLevelViewModel:", r"Model View Viewmodel:",
    r"LogAutoTransitionLandingScreenViewModel:", r"LogPlayerFeedbackViewModel:",
    r"LogStateMachineComponent:", r"LogNetPlayerMovement:", r"LogAresInputStateComponent:",
    r"LogBlueprintUserMessages:", r"LogContentManager:", r"LogContentLibraryPremierSeason:",
    r"LogModuleManager:", r"UAresNetDriver Lifetime Stats:", r"Log file open,", r"Log file closed,",
    r"LogRGIFriends:", r"LogDataTable:", r"LogActInfoInboxProvider:", r"LogGoldStarManager:",
    r"This is a placeholder file", r"Time Zone UTC/GMT", r"LogBaseMainMenuPlayerController:",
    r"BloomlinePlankDefaultModulesLog:", r"LogRankedProgressViewModel:", r"LogCustomGameManager:",
    r"LogDownedComponent:", r"LogStoryContentTracking:", r"LogStoreManager:", r"LogShooterCharacter:",
    r"LogVoteControllerComponent:", r"LogUIActionRouter:", r"LogTextFormatter:",
    r"LogLevelBorderViewModel:", r"LogPlayerTitleViewModel:", r"LogCurrencyViewModel:",
    r"LogAgentViewModel:",
    r"^\s*\{", r"^\s*\}", r"^\s*\"", r"^\s*\]", r"^\s*->", r"^\t", r"^\s*Loading:.*Spawning:",
]

KNOWN_LOG_PATTERNS = [
    r"LogMapLoadModel:.*Transitioning to State",
    r"LogGameFlowStateManager:.*Match State Changed",
    r"LogGameFlowStateManager:.*State:\s*\w+\s*->",
    r"LogGameFlowStateManager:.*Broadcasting state changed",
    r"LogGameFlowStateManager:.*Transitioning to State",
    r"LogPlatformSessionManager:.*Leaving session",
    r"Match State Changed",
    r"State:\s*\w+\s*->",
    r"Transitioning to State",
    r"Match ended",
    r"Phase Changed",
    r"Round Phase",
    r"RoundState",
    r"MatchState",
    r"Buy Phase",
    r"Combat Phase",
    r"Round Start",
    r"Round End",
]

POTENTIAL_LOG_KEYWORDS = [
    r"Match", r"State", r"End", r"Transition", r"Custom",
    r"Game", r"Phase", r"Victory", r"Defeat", r"Score",
    r"Leave", r"Exit", r"Stop", r"Quit", r"Result",
]


def get_valorant_log_path() -> str:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return ""
    return os.path.join(local_app_data, "VALORANT", "Saved", "Logs", "ShooterGame.log")


def save_split_log_files(
    base_filepath: str,
    lines: Iterable[str],
    max_lines_per_part: int = DEFAULT_MAX_LINES_PER_PART,
    max_chars_per_part: int = DEFAULT_MAX_CHARS_PER_PART,
) -> List[str]:
    """ログ行リストを単一ファイルおよびチャット貼り付け用の分割パートファイル群へ保存する"""
    normalized_lines = [line if line.endswith("\n") else f"{line}\n" for line in lines]
    stem, ext = os.path.splitext(base_filepath)
    if not ext:
        ext = ".txt"

    # 前回実行時の古い分割ファイルが残らないよう事前に削除
    for old_part in glob.glob(f"{stem}_part*{ext}"):
        try:
            os.remove(old_part)
        except OSError:
            pass

    with open(base_filepath, "w", encoding="utf-8") as full_f:
        full_f.writelines(normalized_lines)

    if not normalized_lines:
        return []

    chunks: List[tuple[int, int, List[str]]] = []
    current_chunk: List[str] = []
    current_chars = 0
    chunk_start_line = 1

    for idx, line in enumerate(normalized_lines, start=1):
        line_len = len(line)
        if current_chunk and (
            len(current_chunk) >= max_lines_per_part
            or (current_chars + line_len) > max_chars_per_part
        ):
            chunks.append((chunk_start_line, idx - 1, current_chunk))
            current_chunk = []
            current_chars = 0
            chunk_start_line = idx

        current_chunk.append(line)
        current_chars += line_len

    if current_chunk:
        chunks.append((chunk_start_line, len(normalized_lines), current_chunk))

    total_parts = len(chunks)
    saved_part_paths: List[str] = []
    base_name = os.path.basename(base_filepath)

    for part_idx, (start_ln, end_ln, chunk_lines) in enumerate(chunks, start=1):
        part_path = f"{stem}_part{part_idx:02d}{ext}"
        header = f"=== [{base_name} - Part {part_idx}/{total_parts} (Lines {start_ln}-{end_ln})] ===\n"
        with open(part_path, "w", encoding="utf-8") as pf:
            pf.write(header)
            pf.writelines(chunk_lines)
        saved_part_paths.append(part_path)

    return saved_part_paths
