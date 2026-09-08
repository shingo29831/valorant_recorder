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
    potential_path = 'potential_logs.txt'
    useless_path = 'useless_logs.txt'

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
        r"LogRGIFriends:",
        r"LogDataTable:",
        r"LogActInfoInboxProvider:",
        r"LogGoldStarManager:",
        r"This is a placeholder file",
        r"Time Zone UTC/GMT",
        r"LogBaseMainMenuPlayerController:",
        r"BloomlinePlankDefaultModulesLog:",
        r"LogRankedProgressViewModel:",
        r"LogCustomGameManager:",
        r"LogDownedComponent:",
        r"LogStoryContentTracking:",
        r"LogStoreManager:",
        r"LogShooterCharacter:",
        r"LogVoteControllerComponent:",
        r"LogUIActionRouter:",
        r"LogTextFormatter:",
        r"LogLevelBorderViewModel:",
        r"LogPlayerTitleViewModel:",
        r"LogCurrencyViewModel:",
        r"LogAgentViewModel:",
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

    # ==========================================
    # 3. カスタムマッチ終了など、使えそうなログを抽出するためのキーワード
    # ==========================================
    POTENTIAL_KEYWORDS = [
        r"Match", r"State", r"End", r"Transition", r"Custom", 
        r"Game", r"Phase", r"Victory", r"Defeat", r"Score", 
        r"Leave", r"Exit", r"Stop", r"Quit", r"Result"
    ]

    compiled_exclude = [re.compile(pattern) for pattern in EXCLUDE_PATTERNS]
    compiled_known = [re.compile(pattern) for pattern in KNOWN_PATTERNS]
    compiled_potential = [re.compile(pattern, re.IGNORECASE) for pattern in POTENTIAL_KEYWORDS]
    
    print(f"Reading log from: {log_path}")
    print("Filtering...")

    kept_lines_count = 0
    potential_lines_count = 0
    useless_lines_count = 0
    total_lines_count = 0

    with open(log_path, 'r', encoding='utf-8', errors='replace') as infile, \
         open(output_path, 'w', encoding='utf-8') as outfile, \
         open(potential_path, 'w', encoding='utf-8') as pot_file, \
         open(useless_path, 'w', encoding='utf-8') as useless_file:
        
        for line in infile:
            total_lines_count += 1
            
            # 1. 最優先: 既知の必要なログ（状態遷移など）にマッチする場合は filtered_log.txt に出力
            if any(pattern.search(line) for pattern in compiled_known):
                outfile.write(line)
                kept_lines_count += 1
                continue
                
            # 2. 不要なログにマッチする場合はスキップ
            if any(pattern.search(line) for pattern in compiled_exclude):
                continue
                
            # 3. 未知のログの中で、使えそうなキーワードを含むものは potential_logs.txt に出力
            if any(pattern.search(line) for pattern in compiled_potential):
                pot_file.write(line)
                potential_lines_count += 1
            else:
                # 4. それ以外は useless_logs.txt に出力
                useless_file.write(line)
                useless_lines_count += 1

    print(f"Done! Logs have been categorized and saved.")
    print(f"Total lines: {total_lines_count}")
    print(f" -> Known (required) lines saved to {output_path}: {kept_lines_count}")
    print(f" -> Potential lines saved to {potential_path}: {potential_lines_count}")
    print(f" -> Useless lines saved to {useless_path}: {useless_lines_count}")

if __name__ == "__main__":
    main()