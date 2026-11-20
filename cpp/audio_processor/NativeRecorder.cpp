#include "NativeRecorder.h"
#include "FFmpegProcess.h"
#include "NativeAudioUtils.h"
#include "WasapiCapture.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <memory>
#include <mmsystem.h>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include <windows.h>

static std::ofstream g_logFile;
static std::mutex g_logMutex;

static void NativeLog(const std::string& msg) {
    std::lock_guard<std::mutex> lock(g_logMutex);
    if (g_logFile.is_open()) {
        auto now = std::chrono::system_clock::now();
        auto time = std::chrono::system_clock::to_time_t(now);
        g_logFile << std::put_time(std::localtime(&time), "%Y-%m-%d %H:%M:%S") << " " << msg << std::endl;
    }
}

extern "C" {
    void* AudioProcessor_Create(int sample_rate, int channels, const char* model_path);
    void AudioProcessor_Destroy(void* processor);
    void AudioProcessor_SetPreProcessType(void* processor, int type);
    void AudioProcessor_SetDenoiseType(void* processor, int type);
    int AudioProcessor_IsDeepFilterActive(void* processor);
    void AudioProcessor_Process(void* processor, const float* input, float* output, int num_frames);
}

static constexpr int32_t kStartupIdle = 0;
static constexpr int32_t kStartupInitializing = 1;
static constexpr int32_t kStartupRunning = 2;
static constexpr int32_t kStartupFailed = 3;

static std::atomic<bool> g_isRunning{false};
static std::atomic<int32_t> g_startupState{kStartupIdle};
static std::atomic<int64_t> g_actualStartTimeMs{0};
static std::atomic<int64_t> g_actualEndTimeMs{0};
static std::string g_lastError = "";
static std::mutex g_errorMutex;
static std::mutex g_recorderMutex;
static std::thread g_recordingThread;
static std::mutex g_processMutex;
static FFmpegProcess* g_activeProcess = nullptr;

static std::unique_ptr<WasapiCapture> g_sysCapture;
static std::unique_ptr<WasapiCapture> g_micCapture;
static std::unique_ptr<WasapiCapture> g_chatCapture;

static void SetLastErrorMsg(const std::string& msg) {
    std::lock_guard<std::mutex> lock(g_errorMutex);
    g_lastError = msg;
    NativeLog("[ERROR] " + msg);
}

static std::atomic<bool> g_isWriterRunning{false};
static std::thread g_writerThread;

static void WriterThreadFunc(FFmpegProcess* ffmpeg, AudioSyncQueue* outQueue, size_t outChannels) {
    NativeLog("Starting WriterThreadFunc (channels=" + std::to_string(outChannels) + ")...");
    std::vector<float> outBuf;
    const size_t framesPerChunk = 480;
    const size_t outSamples = framesPerChunk * outChannels;

    // 停止要求後もキュー内の未書き込み音声を全てパイプへ書き切って末尾欠損を防止
    while (g_isWriterRunning || outQueue->AvailableSamples() >= outSamples) {
        if (outQueue->PopWait(outSamples, outBuf, 50)) {
            DWORD written = 0;
            if (!ffmpeg->WriteAudio(outBuf.data(), outBuf.size() * sizeof(float), written)) {
                DWORD err = GetLastError();
                NativeLog("WriteFile failed. Error code: " + std::to_string(err) + ". Pipe closed.");
                g_isRunning = false;
                break;
            }
        }
    }
    ffmpeg->FlushInput();
    NativeLog("Exiting WriterThreadFunc...");
}

static void RecordingThreadFunc(
    RecorderConfig configCopy,
    std::string sysDevice,
    std::string micDevice,
    std::string chatDevice,
    std::string saveFilePath,
    std::string ffmpegCmd,
    std::string dfModelPath
) {
    try {
        NativeLog("Starting RecordingThreadFunc...");

    // 前回セッションのキャプチャインスタンス残留による不要キャプチャを防止
    g_sysCapture.reset();
    g_micCapture.reset();
    g_chatCapture.reset();

    const bool chatEnabled = (configCopy.chat_enabled != 0);
    const size_t outChannels = chatEnabled ? 6 : 4;

    if (sysDevice == "__NONE__") {
        NativeLog("Notice: System audio capture is disabled by configuration (None).");
    } else {
        g_sysCapture = std::make_unique<WasapiCapture>(CaptureType::SystemLoopback, sysDevice);
        g_sysCapture->SetLogger(NativeLog);
        if (!g_sysCapture->Initialize(true)) {
            NativeLog("[WARNING] System audio init failed: " + g_sysCapture->GetLastError() + ". Continuing with silent system audio track.");
        }
    }

    if (micDevice == "__NONE__") {
        NativeLog("Notice: Microphone capture is disabled by configuration (None).");
    } else {
        g_micCapture = std::make_unique<WasapiCapture>(CaptureType::Microphone, micDevice);
        g_micCapture->SetLogger(NativeLog);
        if (!g_micCapture->Initialize(true)) {
            NativeLog("[WARNING] Failed to initialize microphone (" + micDevice + "): " + g_micCapture->GetLastError() + ". Continuing with silent mic stream.");
        }
    }

    if (chatEnabled) {
        if (chatDevice == "__NONE__") {
            NativeLog("Notice: Voice chat capture is disabled by configuration (None).");
        } else {
            g_chatCapture = std::make_unique<WasapiCapture>(CaptureType::SystemLoopback, chatDevice);
            g_chatCapture->SetLogger(NativeLog);
            const bool allowFallback = (chatDevice.empty() || chatDevice == "default" || chatDevice == "Default");
            if (!g_chatCapture->Initialize(allowFallback)) {
                NativeLog("[WARNING] Voice chat audio init failed (" + chatDevice + "): " + g_chatCapture->GetLastError() + ". Continuing with silent chat track.");
            }
        }
    }

    AudioSyncQueue sysQueue(2, 2000);
    AudioSyncQueue micQueue(2, 2000);
    AudioSyncQueue chatQueue(2, 2000);
    std::unique_ptr<Resampler> sysResampler;
    std::unique_ptr<Resampler> micResampler;
    std::unique_ptr<Resampler> chatResampler;
    std::mutex sysResamplerMtx;
    std::mutex micResamplerMtx;
    std::mutex chatResamplerMtx;
    std::vector<float> sysRawIn, sysStereoIn, sysStereoOut;
    std::vector<float> micRawIn, micStereoIn, micStereoOut;
    std::vector<float> chatRawIn, chatStereoIn, chatStereoOut;

    float sysGain = configCopy.system_gain;
    float micGain = configCopy.mic_gain;
    float chatGain = configCopy.chat_gain;

    if (g_sysCapture) {
        g_sysCapture->SetCallback([&](const float* data, uint32_t frames, uint32_t channels, uint32_t sampleRate) {
            std::lock_guard<std::mutex> lock(sysResamplerMtx);
            sysRawIn.assign(data, data + frames * channels);
            DownmixToStereo(sysRawIn, frames, channels, sysGain, sysStereoIn);

            if (!sysResampler) sysResampler = std::make_unique<Resampler>(2, sampleRate, 48000);
            sysResampler->Process(sysStereoIn.data(), frames, sysStereoOut);
            sysQueue.Push(sysStereoOut);
        });
    }

    if (g_chatCapture) {
        g_chatCapture->SetCallback([&](const float* data, uint32_t frames, uint32_t channels, uint32_t sampleRate) {
            std::lock_guard<std::mutex> lock(chatResamplerMtx);
            chatRawIn.assign(data, data + frames * channels);
            DownmixToStereo(chatRawIn, frames, channels, chatGain, chatStereoIn);

            if (!chatResampler) chatResampler = std::make_unique<Resampler>(2, sampleRate, 48000);
            chatResampler->Process(chatStereoIn.data(), frames, chatStereoOut);
            chatQueue.Push(chatStereoOut);
        });
    }

    if (g_micCapture) {
        g_micCapture->SetCallback([&](const float* data, uint32_t frames, uint32_t channels, uint32_t sampleRate) {
            std::lock_guard<std::mutex> lock(micResamplerMtx);
            micRawIn.assign(data, data + frames * channels);
            DownmixToStereo(micRawIn, frames, channels, micGain, micStereoIn);

            // 2ch USBオーディオインターフェースで片chのみに入力がある場合の片耳化を防止
            if (channels == 2 && frames > 0) {
                float peakL = 0.0f, peakR = 0.0f;
                for (uint32_t i = 0; i < frames; ++i) {
                    peakL = (std::max)(peakL, std::fabs(micStereoIn[i * 2 + 0]));
                    peakR = (std::max)(peakR, std::fabs(micStereoIn[i * 2 + 1]));
                }
                if (peakL > 1e-4f && peakR < peakL * 0.05f) {
                    for (uint32_t i = 0; i < frames; ++i) micStereoIn[i * 2 + 1] = micStereoIn[i * 2 + 0];
                } else if (peakR > 1e-4f && peakL < peakR * 0.05f) {
                    for (uint32_t i = 0; i < frames; ++i) micStereoIn[i * 2 + 0] = micStereoIn[i * 2 + 1];
                }
            }

            if (!micResampler) micResampler = std::make_unique<Resampler>(2, sampleRate, 48000);
            micResampler->Process(micStereoIn.data(), frames, micStereoOut);
            micQueue.Push(micStereoOut);
        });
    }

    std::string ffmpegLogPath = "native_ffmpeg.log";
    if (!saveFilePath.empty()) {
        size_t pos = saveFilePath.find_last_of("\\/");
        if (pos != std::string::npos) {
            ffmpegLogPath = saveFilePath.substr(0, pos) + "/native_ffmpeg.log";
        }
    }

    const bool hasActiveSys = (g_sysCapture && g_sysCapture->IsInitialized());
    const bool hasActiveMic = (g_micCapture && g_micCapture->IsInitialized());
    const bool hasActiveChat = (chatEnabled && g_chatCapture && g_chatCapture->IsInitialized());
    auto audioProcDeleter = [](void* p) {
        if (p) AudioProcessor_Destroy(p);
    };
    std::unique_ptr<void, decltype(audioProcDeleter)> audioProc(nullptr, audioProcDeleter);

    // WASAPI Start()前かつFFmpeg起動前にモデル展開を完了し、初期パイプ遅延・A/V同期ズレを防止
    if (hasActiveMic && (configCopy.denoise_mode > 0 || configCopy.preprocess_mode > 0)) {
        const char* modelPathCStr = (configCopy.denoise_mode == 1 && !dfModelPath.empty()) ? dfModelPath.c_str() : "";
        NativeLog("Initializing AudioProcessor (Preprocess=" + std::to_string(configCopy.preprocess_mode) +
                  ", Denoise=" + std::to_string(configCopy.denoise_mode) +
                  ", ModelPath=" + (dfModelPath.empty() ? "<empty>" : dfModelPath) + ")");
        audioProc.reset(AudioProcessor_Create(48000, 2, modelPathCStr));
        if (audioProc) {
            AudioProcessor_SetPreProcessType(audioProc.get(), configCopy.preprocess_mode);
            AudioProcessor_SetDenoiseType(audioProc.get(), configCopy.denoise_mode);
            const bool dfActive = (AudioProcessor_IsDeepFilterActive(audioProc.get()) != 0);
            NativeLog("AudioProcessor initialized successfully. DeepFilterActive: " +
                      std::string(dfActive ? "true" : "false"));
        } else {
            NativeLog("Notice: AudioProcessor initialization bypassed or failed. Continuing standard capture.");
        }
    }

    FFmpegProcess ffmpeg;
    ffmpeg.SetLogger(NativeLog);
    std::string ffmpegErr;
    if (!ffmpeg.Start(ffmpegCmd, ffmpegLogPath, configCopy.low_priority != 0, ffmpegErr)) {
        SetLastErrorMsg(ffmpegErr);
        g_startupState.store(kStartupFailed);
        g_isRunning = false;
        return;
    }

    {
        std::lock_guard<std::mutex> lock(g_processMutex);
        g_activeProcess = &ffmpeg;
    }

    NoiseGate micNoiseGate;
    micNoiseGate.SetThreshold(configCopy.noise_gate_threshold);

    if (hasActiveSys) {
        g_sysCapture->Start();
    }
    if (hasActiveMic) {
        g_micCapture->Start();
    }
    if (hasActiveChat) {
        g_chatCapture->Start();
    }

    AudioSyncQueue outQueue(outChannels, 10000);
    g_isWriterRunning = true;
    g_writerThread = std::thread(WriterThreadFunc, &ffmpeg, &outQueue, outChannels);

    const size_t framesPerChunk = 480;
    const size_t sysSamples = framesPerChunk * 2;
    const size_t micSamples = framesPerChunk * 2;
    const size_t chatSamples = framesPerChunk * 2;

    std::vector<float> sysBuf(sysSamples, 0.0f);
    std::vector<float> micBuf(micSamples, 0.0f);
    std::vector<float> micProcessed(micSamples, 0.0f);
    std::vector<float> chatBuf(chatSamples, 0.0f);
    std::vector<float> outBuf(framesPerChunk * outChannels);

    timeBeginPeriod(1);
    SetThreadPriority(GetCurrentThread(), (configCopy.low_priority != 0) ? THREAD_PRIORITY_NORMAL : THREAD_PRIORITY_ABOVE_NORMAL);

    // FFmpegのpipe:0プローブ完了を促すため最小限(10ms)の無音フレームのみ先行投入
    outQueue.Push(std::vector<float>(framesPerChunk * outChannels, 0.0f));

    const size_t cushionSamples = framesPerChunk * 2;
    auto syncWaitStart = std::chrono::steady_clock::now();
    while (g_isRunning) {
        if (ffmpeg.IsExited(0)) {
            DWORD exitCode = ffmpeg.GetExitCode();
            SetLastErrorMsg("FFmpeg process exited during initialization with code " + std::to_string(exitCode));
            g_startupState.store(kStartupFailed);
            g_isRunning = false;
            break;
        }
        auto elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - syncWaitStart
        ).count();
        bool hasSysAudio = (!hasActiveSys || sysQueue.AvailableSamples() >= cushionSamples);
        bool hasMicAudio = (!hasActiveMic || micQueue.AvailableSamples() >= cushionSamples);
        bool hasChatAudio = (!hasActiveChat || chatQueue.AvailableSamples() >= cushionSamples);
        // D3D11/ハードウェアエンコーダの初期化完了とキャプチャ開始を検知して同期
        if ((ffmpeg.HasCaptureStarted() && hasSysAudio && hasMicAudio && hasChatAudio) || elapsedMs >= 3000) {
            break;
        }
        Sleep(5);
    }

    if (g_isRunning && !ffmpeg.IsExited(0)) {
        g_startupState.store(kStartupRunning);
    } else if (g_startupState.load() == kStartupInitializing) {
        g_startupState.store(kStartupFailed);
        g_isRunning = false;
    }

    // 映像キャプチャ開始と同時にキューを直近20msにアラインし、A/Vの初期ズレを根絶
    sysQueue.DrainKeepLatest(cushionSamples);
    micQueue.DrainKeepLatest(cushionSamples);
    if (chatEnabled) {
        chatQueue.DrainKeepLatest(cushionSamples);
    }

    auto actualStartSysMs = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
    g_actualStartTimeMs.store(actualStartSysMs);

    auto startTime = std::chrono::steady_clock::now();
    uint64_t totalWrittenFrames = 0;
    auto lastLogTime = startTime;

    const size_t reqSysSamples = framesPerChunk * 2;
    const size_t reqMicSamples = framesPerChunk * 2;
    const size_t reqChatSamples = framesPerChunk * 2;
    const size_t maxAllowedBuffer = framesPerChunk * 20 * 2;

    auto processAndPushChunk = [&](bool drainMode) {
        if (drainMode) {
            if (hasActiveSys) {
                sysQueue.PopExact(reqSysSamples, sysBuf);
            } else {
                std::fill(sysBuf.begin(), sysBuf.end(), 0.0f);
            }
            if (hasActiveMic) {
                micQueue.PopExact(reqMicSamples, micBuf);
            }
            if (hasActiveChat) {
                chatQueue.PopExact(reqChatSamples, chatBuf);
            } else if (chatEnabled) {
                std::fill(chatBuf.begin(), chatBuf.end(), 0.0f);
            }
        } else {
            if (!hasActiveSys || !sysQueue.PopChunk(reqSysSamples, sysBuf, maxAllowedBuffer)) {
                std::fill(sysBuf.begin(), sysBuf.end(), 0.0f);
            }
            if (hasActiveMic) {
                if (!micQueue.PopChunk(reqMicSamples, micBuf, maxAllowedBuffer)) {
                    std::fill(micBuf.begin(), micBuf.end(), 0.0f);
                }
            }
            if (chatEnabled) {
                if (!hasActiveChat || !chatQueue.PopChunk(reqChatSamples, chatBuf, maxAllowedBuffer)) {
                    std::fill(chatBuf.begin(), chatBuf.end(), 0.0f);
                }
            }
        }

        if (hasActiveMic) {
            if (audioProc && (configCopy.denoise_mode > 0 || configCopy.preprocess_mode > 0)) {
                AudioProcessor_Process(audioProc.get(), micBuf.data(), micProcessed.data(), static_cast<int>(framesPerChunk));
            } else {
                std::copy(micBuf.begin(), micBuf.begin() + reqMicSamples, micProcessed.begin());
            }
            micNoiseGate.Process(micProcessed.data(), framesPerChunk, 2);
        } else {
            std::fill(micProcessed.begin(), micProcessed.end(), 0.0f);
        }

        if (chatEnabled) {
            for (size_t i = 0; i < framesPerChunk; ++i) {
                outBuf[i * 6 + 0] = sysBuf[i * 2 + 0];
                outBuf[i * 6 + 1] = sysBuf[i * 2 + 1];
                outBuf[i * 6 + 2] = micProcessed[i * 2 + 0];
                outBuf[i * 6 + 3] = micProcessed[i * 2 + 1];
                outBuf[i * 6 + 4] = chatBuf[i * 2 + 0];
                outBuf[i * 6 + 5] = chatBuf[i * 2 + 1];
            }
        } else {
            for (size_t i = 0; i < framesPerChunk; ++i) {
                outBuf[i * 4 + 0] = sysBuf[i * 2 + 0];
                outBuf[i * 4 + 1] = sysBuf[i * 2 + 1];
                outBuf[i * 4 + 2] = micProcessed[i * 2 + 0];
                outBuf[i * 4 + 3] = micProcessed[i * 2 + 1];
            }
        }

        outQueue.Push(outBuf);
        totalWrittenFrames += framesPerChunk;
    };

    NativeLog("Entering main recording loop (A/V Aligned 48kHz Pipeline)...");

    while (g_isRunning) {
        if (ffmpeg.IsExited(0)) {
            DWORD exitCode = ffmpeg.GetExitCode();
            SetLastErrorMsg("FFmpeg process terminated unexpectedly with code " + std::to_string(exitCode));
            g_isRunning = false;
            break;
        }

        auto now = std::chrono::steady_clock::now();
        double elapsedSec = std::chrono::duration<double>(now - startTime).count();
        uint64_t targetFrames = static_cast<uint64_t>(elapsedSec * 48000.0);

        // 一時的負荷による遅延を蓄積させず、目標フレーム数まで即座に追従
        while (g_isRunning && (totalWrittenFrames + framesPerChunk) <= targetFrames) {
            processAndPushChunk(false);
        }

        if (std::chrono::duration_cast<std::chrono::seconds>(now - lastLogTime).count() >= 5) {
            NativeLog("Status: Running. Total frames written: " + std::to_string(totalWrittenFrames) +
                      ", Target: " + std::to_string(targetFrames));
            lastLogTime = now;
        }

        Sleep((configCopy.low_priority != 0) ? 4 : 2);
    }

    // パイプエラー等で異常終了した場合は末尾ドレインを行わず即座にクリーンアップへ進む
    if (g_isWriterRunning && !ffmpeg.IsExited(0)) {
        auto tailEnd = std::chrono::steady_clock::now() + std::chrono::milliseconds(250);
        while (g_isWriterRunning && !ffmpeg.IsExited(0) && std::chrono::steady_clock::now() < tailEnd) {
            auto now = std::chrono::steady_clock::now();
            double elapsedSec = std::chrono::duration<double>(now - startTime).count();
            uint64_t targetFrames = static_cast<uint64_t>(elapsedSec * 48000.0);
            while ((totalWrittenFrames + framesPerChunk) <= targetFrames) {
                processAndPushChunk(false);
            }
            Sleep(2);
        }
    }

    timeEndPeriod(1);
    NativeLog("Exiting main recording loop...");

    if (g_sysCapture) {
        g_sysCapture->Stop();
    }
    if (g_micCapture) {
        g_micCapture->Stop();
    }
    if (g_chatCapture) {
        g_chatCapture->Stop();
    }

    if (g_isWriterRunning && !ffmpeg.IsExited(0)) {
        size_t remainingSamples = (std::max)(sysQueue.AvailableSamples(), micQueue.AvailableSamples());
        if (chatEnabled) {
            remainingSamples = (std::max)(remainingSamples, chatQueue.AvailableSamples());
        }
        size_t drainChunks = (std::max)(remainingSamples / reqSysSamples + 1, static_cast<size_t>(10));
        for (size_t c = 0; c < drainChunks; ++c) {
            processAndPushChunk(true);
        }
    }

    audioProc.reset();

    g_isWriterRunning = false;
    outQueue.NotifyAll();
    if (g_writerThread.joinable()) {
        g_writerThread.join();
    }

    auto actualEndSysMs = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()
    ).count();
    g_actualEndTimeMs.store(actualEndSysMs);

    ffmpeg.Stop(10000);

    {
        std::lock_guard<std::mutex> lock(g_processMutex);
        g_activeProcess = nullptr;
    }
    g_isRunning = false;
    } catch (const std::exception& e) {
        SetLastErrorMsg(std::string("Exception in RecordingThreadFunc: ") + e.what());
        if (g_startupState.load() == kStartupInitializing) {
            g_startupState.store(kStartupFailed);
        }
        g_isRunning = false;
    } catch (...) {
        SetLastErrorMsg("Unknown exception in RecordingThreadFunc.");
        if (g_startupState.load() == kStartupInitializing) {
            g_startupState.store(kStartupFailed);
        }
        g_isRunning = false;
    }
}

EXPORT_API bool Recorder_Start(const RecorderConfig* config) {
    std::lock_guard<std::mutex> lock(g_recorderMutex);
    try {
        if (g_recordingThread.joinable()) {
            g_recordingThread.join();
        }

        if (g_isRunning) {
            SetLastErrorMsg("Recorder is already running.");
            return false;
        }

        if (!config) {
            SetLastErrorMsg("Invalid configuration.");
            return false;
        }

        std::string saveDir = config->save_file_path ? config->save_file_path : "";
        std::string logPath = "native_recorder_debug.log";
        if (!saveDir.empty()) {
            size_t pos = saveDir.find_last_of("\\/");
            if (pos != std::string::npos) {
                logPath = saveDir.substr(0, pos) + "/native_recorder_debug.log";
            }
        }

        {
            std::lock_guard<std::mutex> lockLog(g_logMutex);
            if (g_logFile.is_open()) {
                g_logFile.close();
            }
            g_logFile.open(logPath, std::ios::app);
            if (g_logFile.is_open()) {
                auto now = std::chrono::system_clock::now();
                auto time = std::chrono::system_clock::to_time_t(now);
                g_logFile << "\n--- New Recording Session Started at " << std::put_time(std::localtime(&time), "%Y-%m-%d %H:%M:%S") << " ---\n";
                g_logFile << "Log path: " << logPath << "\n";
                g_logFile << "Requested save path was: " << (config->save_file_path ? config->save_file_path : "NULL") << "\n";
            }
        }

        std::string sysDevice;
        if (config->system_device_name != nullptr) {
            sysDevice = config->system_device_name;
        } else {
            // 明示的な""(Default)指定を上書きしないようNULL時のみ旧環境変数を参照
            const char* envSys = std::getenv("VALORECO_SYS_AUDIO_DEVICE");
            if (envSys && envSys[0] != '\0') {
                sysDevice = envSys;
            }
        }
        std::string micDevice = config->mic_device_name ? config->mic_device_name : "";
        std::string chatDevice = config->chat_device_name ? config->chat_device_name : "";
        std::string saveFilePath = config->save_file_path ? config->save_file_path : "";
        std::string ffmpegCmd = config->ffmpeg_command ? config->ffmpeg_command : "";
        std::string dfModelPath = config->df_model_path ? config->df_model_path : "";

        RecorderConfig configCopy = *config;

        g_actualStartTimeMs.store(0);
        g_actualEndTimeMs.store(0);
        g_startupState.store(kStartupInitializing);
        g_isRunning = true;
        SetLastErrorMsg("");

        g_recordingThread = std::thread(
            RecordingThreadFunc,
            configCopy,
            sysDevice,
            micDevice,
            chatDevice,
            saveFilePath,
            ffmpegCmd,
            dfModelPath
        );
        return true;
    } catch (const std::exception& e) {
        SetLastErrorMsg(std::string("Exception in Recorder_Start: ") + e.what());
        g_startupState.store(kStartupFailed);
        g_isRunning = false;
        return false;
    } catch (...) {
        SetLastErrorMsg("Unknown exception in Recorder_Start.");
        g_startupState.store(kStartupFailed);
        g_isRunning = false;
        return false;
    }
}

EXPORT_API void Recorder_Stop() {
    std::lock_guard<std::mutex> lock(g_recorderMutex);
    try {
        g_isRunning = false;
        if (g_recordingThread.joinable()) {
            g_recordingThread.join();
        }
        g_startupState.store(kStartupIdle);

        std::lock_guard<std::mutex> lockLog(g_logMutex);
        if (g_logFile.is_open()) {
            g_logFile << "--- Recording Session Ended ---\n";
            g_logFile.close();
        }
    } catch (const std::exception& e) {
        SetLastErrorMsg(std::string("Exception in Recorder_Stop: ") + e.what());
    } catch (...) {
        SetLastErrorMsg("Unknown exception in Recorder_Stop.");
    }
}

EXPORT_API bool Recorder_IsRunning() {
    return g_isRunning.load();
}

EXPORT_API int32_t Recorder_GetStartupState() {
    return g_startupState.load();
}

EXPORT_API void Recorder_SetLowPriority(bool lowPriority) {
    try {
        std::lock_guard<std::mutex> lock(g_processMutex);
        if (g_activeProcess != nullptr) {
            g_activeProcess->SetLowPriority(lowPriority);
        }
    } catch (...) {}
}

EXPORT_API const char* Recorder_GetLastError() {
    try {
        std::lock_guard<std::mutex> lock(g_errorMutex);
        return g_lastError.c_str();
    } catch (...) {
        return "";
    }
}

EXPORT_API int64_t Recorder_GetActualStartTimeMs() {
    return g_actualStartTimeMs.load();
}

EXPORT_API int64_t Recorder_GetActualEndTimeMs() {
    return g_actualEndTimeMs.load();
}

EXPORT_API bool Recorder_SupportsChatTrack() {
    return true;
}
