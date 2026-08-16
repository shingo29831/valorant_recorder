import os
import re

def main():
    # VALORANTのログファイルのパスを自動取得
    local_app_data = os.environ.get('LOCALAPPDATA')
    if not local_app_data:
        print("LOCALAPPDATA environment variable not found.")
        return

    log_path = os.path.join(local_app_data, 'VALORANT', 'Saved', 'Logs', 'ShooterGame.log')
    output_path = 'filtered_log.txt'

    if not os.path.exists(log_path):
        print(f"Log file not found: {log_path}")
        return

    # ==========================================
    # 1. 不要なログ（除外対象）のパターン
    # ==========================================
    EXCLUDE_PATTERNS = [
        r"LogNet:",
        r"LogOnline:",
        r"LogShooterVoice:",
        r"LogRiotTencentKms:",
        r"LogRiotClient:",
        r"LogRiotOS:",
        r"LogPlatform:",
        r"LogPakFile:",
        r"LogAudio:",
        r"LogAkAudio:",
        r"LogHttp:",
        r"LogUMG:",
        r"LogSlate:",
        r"LogGameMode:",
        r"LogTexture:",
        r"LogLoad:",
        r"LogStreaming:",
        r"LogD3D11RHI:",
        r"LogRHI:",
        r"LogRenderer:",
        r"LogContentStreaming:",
        r"LogTemp:",
        r"LogConfig:",
        r"LogInit:",
        r"LogShooterUI:",
        r"LogShooterEos:",
        r"LogVanguard:",
        r"LogConsoleManager:",
        r"LogDeviceProfileManager:",
        r"LogMemory:",
        r"LogShaderLibrary:",
        r"LogAntiLag2:",
        r"LogUObjectGlobals:",
        r"LogShooterGameUserSettings:",
        r"LogActor:",
        r"LogInterchangeImport:",
        r"LogRGIPatchlineData:",
        r"LogSlateStyle:",
        r"LogInventoryManager:",
        r"UmbraCulling:",
        r"LogRSOManager:",
        r"LogRiotGamesApiClient:",
        r"LogTelemetryManager:",
        r"LogRMSManager:",
        r"LogThreadedChatManager:",
        r"LogPlatformCommon:",
        r"LogJson:",
        r"LogDailyRewardsManager:",
        r"LogRoamingSettingsManager:",
        r"ImportText",
        r"LogPlatformInitializerV2:",
        r"LogTextChatManagerV2:",
        r"LogShooter: Display:",
        r"LogVNGManager:",
        r"LogActionBindingsManager:",
        r"LogCNAntiAddictionManager:",
        r"LogTextChatRoomV2:",
        r"LogCoreGameManager:",
        r"LogPregameManager:",
        r"LogPersonalizationManagerV2:",
        r"LogRMSService:",
        r"LogRewardGrantModelFactory:",
        r"LogContractsManager:",
        r"LogPremierSeasonsModel:",
        r"LogFlushGuard:",
        r"LogReplayData:",
        r"LogPresenceService:",
        r"LogRNetVoiceManager:",
        r"LogTravelManager:",
        r"LogBasePlayerController:",
        r"LogPlayerController:",
        r"LogPlatformPlayerManager:",
        r"LogStringTable:",
        r"LogMenuStackManager:",
        r"LogWebBrowser:",
        r"LogLoadTimeMetrics:",
        r"LogContentLibraryCharacter:",
        r"LogPlayerFeedbackManager:",
        r"LogShellScreen:",
        r"LogUINavigationModel:",
        r"LogContentLibrary:",
        r"LogRGIFriendsRiotGamesApi:",
        r"LogAresCommonAnalogCursor:",
        r"LogAresPlayerController:",
        r"LogShooterBlueprintLibrary:",
        r"LogShooterHUD:",
        r"LogShooterGameState:",
        r"LogShooterPlayerController:",
        r"LogScript:",
        r"LogPartyService:",
        r"LogSkeletalMesh:",
        r"LogAbilitySystem:",
        r"LogAresNetDriver:",
        r"LogShellScreenViewModel:",
        r"LogMeshMaterialManager:",
        r"LogActorComponent:",
        r"AnimBlueprintLog:",
        r"LogLandingScreen:",
        r"LogChatUtils:",
        r"LogAresListWidget:",
        r"LogRemoteClientMovementComponent:",
        r"LogPreloadManager:",
        r"r\.ScreenPercentage",
        r"PIE: Unable to listen",
        r"LogMMRManager:",
        r"LogFlyoutManager:",
        r"LogPhysics:",
        r"LogAresMinimapComponent:",
        r"LogIsLastPlayerAliveOnTeamViewModel:",
        r"LogInventory:",
        r"LogAres:",
        r"LogEffectContainer:",
        r"LogResourceComponent:",
        r"LogInstabilityTrackingDetails:",
        r"LogPrimitiveComponent:",
        r"LogScriptStateComponent:",
        r"LogMovieSceneECS:",
        r"LogMovieScene:",
        r"LogClientPerRoundTelemetryComponent:",
        r"LogUObjectBase:",
        r"LogAnimation:",
        r"LogParticles:",
        r"LogTransitionManagerWidget:",
        r"LogTextChatService:",
        r"LogSocialViewControllerV3:",
        r"LogPartyFunctionLibrary:",
        r"LogDisplayNameManager:",
        r"LogGameplayEffects:",
        r"LogMaterial:",
        r"ShooterUICoordinator:",
        r"LogFiringEffectComponent:",
        r"LogFiringStateComponent:",
        r"LogGameFlowPredictionManager:",
        r"LogMatchDetailsManager:",
        r"LogAccountLevelViewModel:",
        r"Model View Viewmodel:",
        r"LogAutoTransitionLandingScreenViewModel:",
        r"LogPlayerFeedbackViewModel:",
        r"LogStateMachineComponent:",
        r"LogNetPlayerMovement:",
        r"LogAresInputStateComponent:",
        r"LogBlueprintUserMessages:",
        r"LogContentManager:",
        r"LogContentLibraryPremierSeason:",
        r"LogModuleManager:",
        r"UAresNetDriver Lifetime Stats:",
        r"Log file open,",
        r"Log file closed,",
        # JSONダンプや複数行にわたる不要な出力を除外
        r"^\s*\{",
        r"^\s*\}",
        r"^\s*\"",
        r"^\s*\]",
        r"^\s*->",
        r"^\t",  # タブで始まる行（テレメトリの複数行出力など）
        r"^\s*Loading:.*Spawning:",
    ]

    # ==========================================
    # 2. すでに用途が確定している必要なログのパターン
    # ==========================================
    KNOWN_PATTERNS = [
        r"LogMapLoadModel:",
        r"LogGameFlowStateManager:",
        r"LogPlatformSessionManager:",
    ]

    compiled_exclude = [re.compile(pattern) for pattern in EXCLUDE_PATTERNS]
    compiled_known = [re.compile(pattern) for pattern in KNOWN_PATTERNS]
    
    print(f"Reading log from: {log_path}")
    print("Filtering...")

    kept_lines_count = 0
    total_lines_count = 0

    with open(log_path, 'r', encoding='utf-8', errors='replace') as infile, \
         open(output_path, 'w', encoding='utf-8') as outfile:
        
        for line in infile:
            total_lines_count += 1
            
            # 不要なログにマッチする場合はスキップ
            if any(pattern.search(line) for pattern in compiled_exclude):
                continue
                
            # 既知の必要なログにマッチする場合のみ出力
            if any(pattern.search(line) for pattern in compiled_known):
                outfile.write(line)
                kept_lines_count += 1

    print(f"Done! Filtered log saved to: {output_path}")
    print(f"Total lines: {total_lines_count} -> Known (required) lines: {kept_lines_count}")

if __name__ == "__main__":
    main()