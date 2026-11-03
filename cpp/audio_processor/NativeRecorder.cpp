#include "NativeRecorder.h"
#include "FFmpegProcess.h"
#include "NativeAudioUtils.h"
#include "WasapiCapture.h"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
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
    void AudioProcessor_Process(void* processor, const float* input, float* output, int num_frames);
}

static std::atomic<bool> g_isRunning{false};
static std::string g_lastError = "";
static std::mutex g_errorMutex;
static std::thread g_recordingThread;
static std::mutex g_processMutex;
static FFmpegProcess* g_activeProcess = nullptr;

static std::unique_ptr<WasapiCapture> g_sysCapture;
static std::unique_ptr<WasapiCapture> g_micCapture;

static void SetLastErrorMsg(const std::string& msg) {
    std::lock_guard<std::mutex> lock(g_errorMutex);
    g_lastError = msg;
    NativeLog("[ERROR] " + msg);
}

static std::atomic<bool> g_isWriterRunning{false};
static std::thread g_writerThread;

static void WriterThreadFunc(FFmpegProcess* ffmpeg, AudioSyncQueue* outQueue) {
    NativeLog("Starting WriterThreadFunc...");
    std::vector<float> outBuf;
    const size_t framesPerChunk = 480;
    const size_t outSamples = framesPerChunk * 4;

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

static void RecordingThreadFunc(RecorderConfig configCopy, std::string micDevice, std::string ffmpegCmd, std::string dfModelPath) {
    NativeLog("Starting RecordingThreadFunc...");

    g_sysCapture = std::make_unique<WasapiCapture>(CaptureType::SystemLoopback);
    g_sysCapture->SetLogger(NativeLog);

    if (!g_sysCapture->Initialize()) {
        NativeLog("[WARNING] System audio init failed: " + g_sysCapture->GetLastError() + ". Continuing with silent system audio track.");
    }

    if (micDevice == "__NONE__") {
        NativeLog("Notice: Microphone capture is disabled by configuration (None).");
    } else {
        g_micCapture = std::make_unique<WasapiCapture>(CaptureType::Microphone, micDevice);
        g_micCapture->SetLogger(NativeLog);
        if (!g_micCapture->Initialize()) {
            NativeLog("[WARNING] Failed to initialize microphone (" + micDevice + "): " + g_micCapture->GetLastError() + ". Continuing with silent mic stream.");
        }
    }

    AudioSyncQueue sysQueue(2, 2000);
    AudioSyncQueue micQueue(2, 2000);
    std::unique_ptr<Resampler> sysResampler;
    std::unique_ptr<Resampler> micResampler;
    std::mutex sysResamplerMtx;
    std::mutex micResamplerMtx;
    std::vector<float> sysRawIn, sysStereoIn, sysStereoOut;
    std::vector<float> micRawIn, micStereoIn, micStereoOut;

    float sysGain = configCopy.system_gain;
    float micGain = configCopy.mic_gain;

    g_sysCapture->SetCallback([&](const float* data, uint32_t frames, uint32_t channels, uint32_t sampleRate) {
        std::lock_guard<std::mutex> lock(sysResamplerMtx);
        sysRawIn.assign(data, data + frames * channels);
        DownmixToStereo(sysRawIn, frames, channels, sysGain, sysStereoIn);

        if (!sysResampler) sysResampler = std::make_unique<Resampler>(2, sampleRate, 48000);
        sysResampler->Process(sysStereoIn.data(), frames, sysStereoOut);
        sysQueue.Push(sysStereoOut);
    });

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
    std::string saveDirStr = configCopy.save_file_path ? configCopy.save_file_path : "";
    if (!saveDirStr.empty()) {
        size_t pos = saveDirStr.find_last_of("\\/");
        if (pos != std::string::npos) {
            ffmpegLogPath = saveDirStr.substr(0, pos) + "/native_ffmpeg.log";
        }
    }

    FFmpegProcess ffmpeg;
    ffmpeg.SetLogger(NativeLog);
    std::string ffmpegErr;
    if (!ffmpeg.Start(ffmpegCmd, ffmpegLogPath, configCopy.low_priority != 0, ffmpegErr)) {
        SetLastErrorMsg(ffmpegErr);
        g_isRunning = false;
        return;
    }

    {
        std::lock_guard<std::mutex> lock(g_processMutex);
        g_activeProcess = &ffmpeg;
    }

    const bool hasActiveMic = (g_micCapture && g_micCapture->IsInitialized());
    void* audioProc = nullptr;
    if (hasActiveMic && (configCopy.denoise_mode > 0 || configCopy.preprocess_mode > 0)) {
        const char* modelPathCStr = (configCopy.denoise_mode == 1 && !dfModelPath.empty()) ? dfModelPath.c_str() : "";
        audioProc = AudioProcessor_Create(48000, 2, modelPathCStr);
        if (audioProc) {
            AudioProcessor_SetPreProcessType(audioProc, configCopy.preprocess_mode);
            AudioProcessor_SetDenoiseType(audioProc, configCopy.denoise_mode);
            NativeLog("AudioProcessor initialized successfully. Preprocess: " +
                      std::to_string(configCopy.preprocess_mode) + ", Denoise: " +
                      std::to_string(configCopy.denoise_mode));
        } else {
            NativeLog("Notice: AudioProcessor initialization bypassed or failed. Continuing standard capture.");
        }
    }

    NoiseGate micNoiseGate;
    micNoiseGate.SetThreshold(configCopy.noise_gate_threshold);

    if (g_sysCapture && g_sysCapture->IsInitialized()) {
        g_sysCapture->Start();
    }
    if (g_micCapture && g_micCapture->IsInitialized()) {
        g_micCapture->Start();
    }

    AudioSyncQueue outQueue(4, 10000);
    g_isWriterRunning = true;
    g_writerThread = std::thread(WriterThreadFunc, &ffmpeg, &outQueue);

    const size_t framesPerChunk = 480;
    const size_t sysSamples = framesPerChunk * 2;
    const size_t micSamples = framesPerChunk * 2;

    std::vector<float> sysBuf(sysSamples);
    std::vector<float> micBuf(micSamples);
    std::vector<float> micProcessed(micSamples);
    std::vector<float> outBuf(framesPerChunk * 4);

    timeBeginPeriod(1);
    SetThreadPriority(GetCurrentThread(), (configCopy.low_priority != 0) ? THREAD_PRIORITY_NORMAL : THREAD_PRIORITY_ABOVE_NORMAL);

    // FFmpegのpipe:0プローブ完了を促すため最小限(10ms)の無音フレームのみ先行投入
    outQueue.Push(std::vector<float>(framesPerChunk * 4, 0.0f));

    const size_t cushionSamples = framesPerChunk * 2;
    auto syncWaitStart = std::chrono::steady_clock::now();
    while (g_isRunning) {
        if (ffmpeg.IsExited(0)) {
            DWORD exitCode = ffmpeg.GetExitCode();
            SetLastErrorMsg("FFmpeg process exited during initialization with code " + std::to_string(exitCode));
            g_isRunning = false;
            break;
        }
        auto elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - syncWaitStart
        ).count();
        bool hasSysAudio = (!g_sysCapture || !g_sysCapture->IsInitialized() || sysQueue.AvailableSamples() >= cushionSamples);
        bool hasMicAudio = (!g_micCapture || !g_micCapture->IsInitialized() || micQueue.AvailableSamples() >= cushionSamples);
        // D3D11/ハードウェアエンコーダの初期化完了とキャプチャ開始を検知して同期
        if ((ffmpeg.HasCaptureStarted() && hasSysAudio && hasMicAudio) || elapsedMs >= 3000) {
            break;
        }
        Sleep(5);
    }

    // 映像キャプチャ開始と同時にキューを直近20msにアラインし、A/Vの初期ズレを根絶
    sysQueue.DrainKeepLatest(cushionSamples);
    micQueue.DrainKeepLatest(cushionSamples);

    auto startTime = std::chrono::steady_clock::now();
    uint64_t totalWrittenFrames = 0;
    auto lastLogTime = startTime;

    const size_t reqSysSamples = framesPerChunk * 2;
    const size_t reqMicSamples = framesPerChunk * 2;
    const size_t maxAllowedBuffer = framesPerChunk * 20 * 2;

    auto processAndPushChunk = [&](bool drainMode) {
        if (drainMode) {
            sysQueue.PopExact(reqSysSamples, sysBuf);
            if (hasActiveMic) {
                micQueue.PopExact(reqMicSamples, micBuf);
            }
        } else {
            if (!sysQueue.PopChunk(reqSysSamples, sysBuf, maxAllowedBuffer)) {
                std::fill(sysBuf.begin(), sysBuf.end(), 0.0f);
            }
            if (hasActiveMic) {
                if (!micQueue.PopChunk(reqMicSamples, micBuf, maxAllowedBuffer)) {
                    std::fill(micBuf.begin(), micBuf.end(), 0.0f);
                }
            }
        }

        if (hasActiveMic) {
            if (audioProc && (configCopy.denoise_mode > 0 || configCopy.preprocess_mode > 0)) {
                AudioProcessor_Process(audioProc, micBuf.data(), micProcessed.data(), static_cast<int>(framesPerChunk));
            } else {
                std::copy(micBuf.begin(), micBuf.begin() + reqMicSamples, micProcessed.begin());
            }
            micNoiseGate.Process(micProcessed.data(), framesPerChunk, 2);
        } else {
            std::fill(micProcessed.begin(), micProcessed.end(), 0.0f);
        }

        for (size_t i = 0; i < framesPerChunk; ++i) {
            outBuf[i * 4 + 0] = sysBuf[i * 2 + 0];
            outBuf[i * 4 + 1] = sysBuf[i * 2 + 1];
            outBuf[i * 4 + 2] = micProcessed[i * 2 + 0];
            outBuf[i * 4 + 3] = micProcessed[i * 2 + 1];
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

    g_sysCapture->Stop();
    if (g_micCapture) {
        g_micCapture->Stop();
    }

    if (g_isWriterRunning && !ffmpeg.IsExited(0)) {
        size_t remainingSamples = (std::max)(sysQueue.AvailableSamples(), micQueue.AvailableSamples());
        size_t drainChunks = (std::max)(remainingSamples / reqSysSamples + 1, static_cast<size_t>(10));
        for (size_t c = 0; c < drainChunks; ++c) {
            processAndPushChunk(true);
        }
    }

    if (audioProc) {
        AudioProcessor_Destroy(audioProc);
        audioProc = nullptr;
    }

    g_isWriterRunning = false;
    outQueue.NotifyAll();
    if (g_writerThread.joinable()) {
        g_writerThread.join();
    }

    ffmpeg.Stop(10000);

    {
        std::lock_guard<std::mutex> lock(g_processMutex);
        g_activeProcess = nullptr;
    }
    g_isRunning = false;
}

EXPORT_API bool Recorder_Start(const RecorderConfig* config) {
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
        std::lock_guard<std::mutex> lock(g_logMutex);
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

    std::string micDevice = config->mic_device_name ? config->mic_device_name : "";
    std::string ffmpegCmd = config->ffmpeg_command ? config->ffmpeg_command : "";
    std::string dfModelPath = config->df_model_path ? config->df_model_path : "";

    RecorderConfig configCopy = *config;

    g_isRunning = true;
    SetLastErrorMsg("");

    g_recordingThread = std::thread(RecordingThreadFunc, configCopy, micDevice, ffmpegCmd, dfModelPath);
    return true;
}

EXPORT_API void Recorder_Stop() {
    if (g_isRunning) {
        g_isRunning = false;
        if (g_recordingThread.joinable()) {
            g_recordingThread.join();
        }
    }

    std::lock_guard<std::mutex> lock(g_logMutex);
    if (g_logFile.is_open()) {
        g_logFile << "--- Recording Session Ended ---\n";
        g_logFile.close();
    }
}

EXPORT_API bool Recorder_IsRunning() {
    return g_isRunning;
}

EXPORT_API void Recorder_SetLowPriority(bool lowPriority) {
    std::lock_guard<std::mutex> lock(g_processMutex);
    if (g_activeProcess != nullptr) {
        g_activeProcess->SetLowPriority(lowPriority);
    }
}

EXPORT_API const char* Recorder_GetLastError() {
    std::lock_guard<std::mutex> lock(g_errorMutex);
    return g_lastError.c_str();
}
