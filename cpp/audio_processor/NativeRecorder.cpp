#include "NativeRecorder.h"
#include "WasapiCapture.h"
#include <string>
#include <thread>
#include <atomic>
#include <mutex>
#include <memory>
#include <vector>
#include <cmath>
#include <windows.h>

// 内部状態管理
static std::atomic<bool> g_isRunning{false};
static std::string g_lastError = "";
static std::mutex g_errorMutex;
static std::thread g_recordingThread;

static std::unique_ptr<WasapiCapture> g_sysCapture;
static std::unique_ptr<WasapiCapture> g_micCapture;

static void SetLastErrorMsg(const std::string& msg) {
    std::lock_guard<std::mutex> lock(g_errorMutex);
    g_lastError = msg;
}

// 録画スレッドのメイン処理
static void RecordingThreadFunc(RecorderConfig configCopy, std::string micDevice, std::string savePath, std::string tempPath, std::string ffmpegPath) {
    g_sysCapture = std::make_unique<WasapiCapture>(CaptureType::SystemLoopback);
    g_micCapture = std::make_unique<WasapiCapture>(CaptureType::Microphone, micDevice);

    if (!g_sysCapture->Initialize()) {
        SetLastErrorMsg("System audio init failed: " + g_sysCapture->GetLastError());
        g_isRunning = false;
        return;
    }

    if (!g_micCapture->Initialize()) {
        SetLastErrorMsg("Mic audio init failed: " + g_micCapture->GetLastError());
        // マイク初期化失敗時はシステム音のみで続行するかは要検討（一旦続行）
    }

    // Python側で設定されたゲインとノイズゲートをキャプチャ
    float sysGain = configCopy.system_gain;
    float micGain = configCopy.mic_gain;
    float gateThreshold = configCopy.noise_gate_threshold;

    g_sysCapture->SetCallback([sysGain](const float* data, uint32_t frames, uint32_t channels, uint32_t sampleRate) {
        // TODO: リサンプリング (48000Hz等へ)
        // TODO: sysGain を適用してリングバッファへ
    });

    g_micCapture->SetCallback([micGain, gateThreshold](const float* data, uint32_t frames, uint32_t channels, uint32_t sampleRate) {
        // TODO: リサンプリング (48000Hz等へ)
        // TODO: micGain を適用
        // TODO: gateThreshold を用いたノイズゲート処理
        // TODO: AudioProcessor (SpeexDSP / DeepFilterNet) によるノイズ除去
        // TODO: リングバッファへ
    });

    g_sysCapture->Start();
    g_micCapture->Start();

    // TODO: FFmpegプロセスを CreateProcess で起動し、パイプ経由でミックスした音声を流し込むループ

    while (g_isRunning) {
        Sleep(100);
    }

    g_sysCapture->Stop();
    g_micCapture->Stop();
}

EXPORT_API bool Recorder_Start(const RecorderConfig* config) {
    if (g_isRunning) {
        SetLastErrorMsg("Recorder is already running.");
        return false;
    }

    if (!config) {
        SetLastErrorMsg("Invalid configuration.");
        return false;
    }

    // Python側のメモリが解放されても安全なように文字列をディープコピー
    std::string micDevice = config->mic_device_name ? config->mic_device_name : "";
    std::string savePath = config->save_file_path ? config->save_file_path : "";
    std::string tempPath = config->temp_file_path ? config->temp_file_path : "";
    std::string ffmpegPath = config->ffmpeg_path ? config->ffmpeg_path : "";
    
    RecorderConfig configCopy = *config;

    g_isRunning = true;
    SetLastErrorMsg("");

    // C++のネイティブスレッドで録画処理を開始（PythonのGILの影響を受けない）
    g_recordingThread = std::thread(RecordingThreadFunc, configCopy, micDevice, savePath, tempPath, ffmpegPath);

    return true;
}

EXPORT_API void Recorder_Stop() {
    if (g_isRunning) {
        g_isRunning = false; // スレッドに停止を通知
        if (g_recordingThread.joinable()) {
            g_recordingThread.join(); // 終了を待機
        }
    }
}

EXPORT_API bool Recorder_IsRunning() {
    return g_isRunning;
}

EXPORT_API const char* Recorder_GetLastError() {
    std::lock_guard<std::mutex> lock(g_errorMutex);
    return g_lastError.c_str();
}
