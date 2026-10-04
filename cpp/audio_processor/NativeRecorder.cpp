#include "NativeRecorder.h"
#include "WasapiCapture.h"
#include <speex/speex_resampler.h>
#include <string>
#include <thread>
#include <atomic>
#include <mutex>
#include <memory>
#include <vector>
#include <deque>
#include <cmath>
#include <algorithm>
#include <windows.h>
#include <condition_variable>
#include <fstream>
#include <iomanip>
#include <mmsystem.h>
#include <chrono>
#include <ctime>

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

// AudioProcessor C API
extern "C" {
    void* AudioProcessor_Create(int sample_rate, int channels, const char* model_path);
    void AudioProcessor_Destroy(void* processor);
    void AudioProcessor_SetPreProcessType(void* processor, int type);
    void AudioProcessor_SetDenoiseType(void* processor, int type);
    void AudioProcessor_Process(void* processor, const float* input, float* output, int num_frames);
}

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
    NativeLog("[ERROR] " + msg);
}

// SpeexDSP リサンプラーラッパー
class Resampler {
    SpeexResamplerState* m_state = nullptr;
    uint32_t m_channels;
    uint32_t m_inRate;
    uint32_t m_outRate;
    std::vector<float> m_resampleBuffer;

public:
    Resampler(uint32_t channels, uint32_t inRate, uint32_t outRate)
        : m_channels(channels), m_inRate(inRate), m_outRate(outRate) {
        if (m_inRate != m_outRate) {
            int err = 0;
            m_state = speex_resampler_init(m_channels, m_inRate, m_outRate, 3, &err);
        }
    }

    ~Resampler() {
        if (m_state) {
            speex_resampler_destroy(m_state);
            m_state = nullptr;
        }
    }

    void Process(const float* in, uint32_t inFrames, std::vector<float>& out) {
        if (m_inRate == m_outRate || !m_state) {
            out.assign(in, in + inFrames * m_channels);
            return;
        }

        uint32_t estimatedOutFrames = static_cast<uint32_t>(
            std::ceil(static_cast<double>(inFrames) * m_outRate / m_inRate)
        ) + 64;

        if (m_resampleBuffer.size() < estimatedOutFrames * m_channels) {
            m_resampleBuffer.resize(estimatedOutFrames * m_channels);
        }

        uint32_t inLen = inFrames;
        uint32_t outLen = estimatedOutFrames;

        speex_resampler_process_interleaved_float(
            m_state, in, &inLen, m_resampleBuffer.data(), &outLen
        );

        out.assign(m_resampleBuffer.data(), m_resampleBuffer.data() + outLen * m_channels);
    }
};

static void DownmixToStereo(const std::vector<float>& in, uint32_t frames, uint32_t channels, float gain, std::vector<float>& stereoOut) {
    stereoOut.resize(frames * 2);
    if (channels == 1) {
        for (uint32_t i = 0; i < frames; ++i) {
            float s = in[i] * gain;
            stereoOut[i * 2 + 0] = s;
            stereoOut[i * 2 + 1] = s;
        }
    } else if (channels == 2) {
        for (uint32_t i = 0; i < frames; ++i) {
            stereoOut[i * 2 + 0] = in[i * 2 + 0] * gain;
            stereoOut[i * 2 + 1] = in[i * 2 + 1] * gain;
        }
    } else if (channels >= 6) {
        for (uint32_t i = 0; i < frames; ++i) {
            const float* ch = &in[i * channels];
            float fl  = ch[0];
            float fr  = ch[1];
            float fc  = ch[2];
            float lfe = ch[3];
            float sl  = ch[4];
            float sr  = ch[5];
            float rl  = (channels >= 8) ? ch[6] : 0.0f;
            float rr  = (channels >= 8) ? ch[7] : 0.0f;

            float left  = (fl + 0.7071f * fc + 0.5f * lfe + 0.7071f * sl + 0.7071f * rl) * 0.6f * gain;
            float right = (fr + 0.7071f * fc + 0.5f * lfe + 0.7071f * sr + 0.7071f * rr) * 0.6f * gain;

            stereoOut[i * 2 + 0] = std::clamp(left, -1.0f, 1.0f);
            stereoOut[i * 2 + 1] = std::clamp(right, -1.0f, 1.0f);
        }
    } else {
        for (uint32_t i = 0; i < frames; ++i) {
            stereoOut[i * 2 + 0] = in[i * channels + 0] * gain;
            stereoOut[i * 2 + 1] = in[i * channels + 1] * gain;
        }
    }
}

static std::atomic<bool> g_isWriterRunning{false};
static std::thread g_writerThread;

static void WriterThreadFunc(HANDLE hWritePipe, AudioSyncQueue* outQueue) {
    NativeLog("Starting WriterThreadFunc...");
    std::vector<float> outBuf;
    const size_t framesPerChunk = 480;
    const size_t outSamples = framesPerChunk * 6;

    while (g_isWriterRunning) {
        if (outQueue->PopWait(outSamples, outBuf, 100)) {
            DWORD written = 0;
            if (!WriteFile(hWritePipe, outBuf.data(), static_cast<DWORD>(outBuf.size() * sizeof(float)), &written, NULL)) {
                DWORD err = GetLastError();
                NativeLog("WriteFile failed. Error code: " + std::to_string(err) + ". Pipe closed.");
                break;
            }
        }
    }
    NativeLog("Exiting WriterThreadFunc...");
}

static void RecordingThreadFunc(RecorderConfig configCopy, std::string micDevice, std::string ffmpegCmd, std::string dfModelPath) {
    NativeLog("Starting RecordingThreadFunc...");
    
    g_sysCapture = std::make_unique<WasapiCapture>(CaptureType::SystemLoopback);
    g_sysCapture->SetLogger(NativeLog);
    
    g_micCapture = std::make_unique<WasapiCapture>(CaptureType::Microphone, micDevice);
    g_micCapture->SetLogger(NativeLog);

    if (!g_sysCapture->Initialize()) {
        SetLastErrorMsg("System audio init failed: " + g_sysCapture->GetLastError());
        g_isRunning = false;
        return;
    }
    g_micCapture->Initialize();

    AudioSyncQueue sysQueue(2, 80);
    AudioSyncQueue micQueue(2, 80);
    std::unique_ptr<Resampler> sysResampler;
    std::unique_ptr<Resampler> micResampler;
    std::mutex resamplerMtx;

    float sysGain = configCopy.system_gain;
    float micGain = configCopy.mic_gain;

    g_sysCapture->SetCallback([&](const float* data, uint32_t frames, uint32_t channels, uint32_t sampleRate) {
        std::lock_guard<std::mutex> lock(resamplerMtx);
        if (!sysResampler) sysResampler = std::make_unique<Resampler>(channels, sampleRate, 48000);
        std::vector<float> resampled;
        sysResampler->Process(data, frames, resampled);
        
        uint32_t outFrames = static_cast<uint32_t>(resampled.size() / channels);
        std::vector<float> stereoOut;
        DownmixToStereo(resampled, outFrames, channels, sysGain, stereoOut);
        sysQueue.Push(stereoOut);
    });

    g_micCapture->SetCallback([&](const float* data, uint32_t frames, uint32_t channels, uint32_t sampleRate) {
        std::lock_guard<std::mutex> lock(resamplerMtx);
        if (!micResampler) micResampler = std::make_unique<Resampler>(channels, sampleRate, 48000);
        std::vector<float> resampled;
        micResampler->Process(data, frames, resampled);
        
        uint32_t outFrames = static_cast<uint32_t>(resampled.size() / channels);
        std::vector<float> stereoOut;
        DownmixToStereo(resampled, outFrames, channels, micGain, stereoOut);
        micQueue.Push(stereoOut);
    });

    HANDLE hReadPipe, hWritePipe;
    SECURITY_ATTRIBUTES saAttr;
    saAttr.nLength = sizeof(SECURITY_ATTRIBUTES);
    saAttr.bInheritHandle = TRUE;
    saAttr.lpSecurityDescriptor = NULL;
    
    DWORD pipeSize = 8 * 1024 * 1024; 
    if (!CreatePipe(&hReadPipe, &hWritePipe, &saAttr, pipeSize)) {
        SetLastErrorMsg("Failed to create pipe");
        g_isRunning = false;
        return;
    }
    SetHandleInformation(hWritePipe, HANDLE_FLAG_INHERIT, 0);

    std::string ffmpegLogPath = "native_ffmpeg.log";
    std::string saveDirStr = configCopy.save_file_path ? configCopy.save_file_path : "";
    if (!saveDirStr.empty()) {
        size_t pos = saveDirStr.find_last_of("\\/");
        if (pos != std::string::npos) {
            ffmpegLogPath = saveDirStr.substr(0, pos) + "/native_ffmpeg.log";
        }
    }
    
    SECURITY_ATTRIBUTES saLog;
    saLog.nLength = sizeof(SECURITY_ATTRIBUTES);
    saLog.bInheritHandle = TRUE;
    saLog.lpSecurityDescriptor = NULL;
    
    HANDLE hLogFile = CreateFileA(ffmpegLogPath.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &saLog, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hLogFile == INVALID_HANDLE_VALUE) {
        hLogFile = CreateFileA("NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &saLog, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    }

    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.hStdInput = hReadPipe;
    si.hStdOutput = hLogFile;
    si.hStdError = hLogFile;
    si.dwFlags |= STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    ZeroMemory(&pi, sizeof(pi));

    std::vector<char> cmdBuf(ffmpegCmd.begin(), ffmpegCmd.end());
    cmdBuf.push_back('\0');

    NativeLog("Executing FFmpeg command: " + ffmpegCmd);

    if (!CreateProcessA(NULL, cmdBuf.data(), NULL, NULL, TRUE, CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, NULL, NULL, &si, &pi)) {
        DWORD err = GetLastError();
        SetLastErrorMsg("Failed to start FFmpeg process (CreateProcessA error: " + std::to_string(err) + ")");
        CloseHandle(hReadPipe);
        CloseHandle(hWritePipe);
        if (hLogFile != INVALID_HANDLE_VALUE) CloseHandle(hLogFile);
        g_isRunning = false;
        return;
    }
    CloseHandle(hReadPipe);
    if (hLogFile != INVALID_HANDLE_VALUE) CloseHandle(hLogFile);

    DWORD waitResult = WaitForSingleObject(pi.hProcess, 150);
    if (waitResult == WAIT_OBJECT_0) {
        DWORD exitCode = 0;
        GetExitCodeProcess(pi.hProcess, &exitCode);
        SetLastErrorMsg("FFmpeg process crashed immediately with exit code " + std::to_string(exitCode));
        CloseHandle(hWritePipe);
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        g_isRunning = false;
        return;
    }

    void* audioProc = nullptr;
    if (configCopy.denoise_mode > 0 || configCopy.preprocess_mode > 0) {
        if (!dfModelPath.empty()) {
            audioProc = AudioProcessor_Create(48000, 2, dfModelPath.c_str());
            if (audioProc) {
                AudioProcessor_SetPreProcessType(audioProc, configCopy.preprocess_mode);
                AudioProcessor_SetDenoiseType(audioProc, configCopy.denoise_mode);
            }
        }
        if (!audioProc) {
            NativeLog("Notice: AudioProcessor initialization bypassed. Continuing standard capture.");
        }
    }

    NoiseGate micNoiseGate;
    micNoiseGate.SetThreshold(configCopy.noise_gate_threshold);

    g_sysCapture->Start();
    g_micCapture->Start();

    AudioSyncQueue outQueue(6, 10000);
    g_isWriterRunning = true;
    g_writerThread = std::thread(WriterThreadFunc, hWritePipe, &outQueue);

    const size_t framesPerChunk = 480;
    const size_t sysSamples = framesPerChunk * 2;
    const size_t micSamples = framesPerChunk * 2;

    std::vector<float> sysBuf(sysSamples);
    std::vector<float> micBuf(micSamples);
    std::vector<float> micProcessed(micSamples);
    std::vector<float> outBuf(framesPerChunk * 6);

    timeBeginPeriod(1);
    
    auto startTime = std::chrono::steady_clock::now();
    uint64_t totalWrittenFrames = 0;
    auto lastLogTime = startTime;

    NativeLog("Entering main recording loop (Self-Clocked A/V Sync)...");

    while (g_isRunning) {
        auto now = std::chrono::steady_clock::now();
        double elapsedSec = std::chrono::duration<double>(now - startTime).count();
        uint64_t targetFrames = static_cast<uint64_t>(elapsedSec * 48000.0);

        while (g_isRunning && totalWrittenFrames < targetFrames) {
            uint32_t chunkFrames = static_cast<uint32_t>((std::min<uint64_t>)(framesPerChunk, targetFrames - totalWrittenFrames));
            if (chunkFrames == 0) break;

            size_t reqSysSamples = chunkFrames * 2;
            size_t reqMicSamples = chunkFrames * 2;

            sysQueue.PopExact(reqSysSamples, sysBuf);
            micQueue.PopExact(reqMicSamples, micBuf);

            micNoiseGate.Process(micBuf.data(), chunkFrames, 2);

            if (audioProc && (configCopy.denoise_mode > 0 || configCopy.preprocess_mode > 0)) {
                AudioProcessor_Process(audioProc, micBuf.data(), micProcessed.data(), static_cast<int>(chunkFrames));
            } else {
                std::copy(micBuf.begin(), micBuf.begin() + reqMicSamples, micProcessed.begin());
            }

            for (size_t i = 0; i < chunkFrames; ++i) {
                float sysL = sysBuf[i * 2 + 0];
                float sysR = sysBuf[i * 2 + 1];
                float micL = micProcessed[i * 2 + 0];
                float micR = micProcessed[i * 2 + 1];

                outBuf[i * 6 + 0] = std::clamp(sysL + micL, -1.0f, 1.0f);
                outBuf[i * 6 + 1] = std::clamp(sysR + micR, -1.0f, 1.0f);
                outBuf[i * 6 + 2] = sysL;
                outBuf[i * 6 + 3] = sysR;
                outBuf[i * 6 + 4] = micL;
                outBuf[i * 6 + 5] = micR;
            }

            std::vector<float> chunkOut(outBuf.begin(), outBuf.begin() + chunkFrames * 6);
            outQueue.Push(chunkOut);
            totalWrittenFrames += chunkFrames;
        }

        if (std::chrono::duration_cast<std::chrono::seconds>(now - lastLogTime).count() >= 5) {
            NativeLog("Status: Running. Total frames written: " + std::to_string(totalWrittenFrames) + 
                      ", Target: " + std::to_string(targetFrames));
            lastLogTime = now;
        }

        Sleep(2);
    }

    timeEndPeriod(1);
    NativeLog("Exiting main recording loop...");

    g_sysCapture->Stop();
    g_micCapture->Stop();
    if (audioProc) {
        AudioProcessor_Destroy(audioProc);
        audioProc = nullptr;
    }

    g_isWriterRunning = false;
    if (g_writerThread.joinable()) {
        g_writerThread.join();
    }

    CloseHandle(hWritePipe);
    
    DWORD waitProc = WaitForSingleObject(pi.hProcess, 5000);
    if (waitProc == WAIT_TIMEOUT) {
        NativeLog("Warning: FFmpeg process did not exit in time. Terminating...");
        TerminateProcess(pi.hProcess, 0);
        WaitForSingleObject(pi.hProcess, 2000);
    }

    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
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

    // ログファイルの初期化 (録画保存先ディレクトリに出力)
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
            
            std::string saveDir = config->save_file_path ? config->save_file_path : "NULL";
            g_logFile << "Requested save path was: " << saveDir << "\n";
        }
    }

    std::string micDevice = config->mic_device_name ? config->mic_device_name : "";
    std::string ffmpegCmd = config->ffmpeg_command ? config->ffmpeg_command : "";
    std::string dfModelPath = config->df_model_path ? config->df_model_path : "";
    
    RecorderConfig configCopy = *config;

    g_isRunning = true;
    SetLastErrorMsg("");

    // C++のネイティブスレッドで録画処理を開始（PythonのGILの影響を完全に排除）
    g_recordingThread = std::thread(RecordingThreadFunc, configCopy, micDevice, ffmpegCmd, dfModelPath);

    return true;
}

EXPORT_API void Recorder_Stop() {
    if (g_isRunning) {
        g_isRunning = false; // スレッドに停止を通知
        if (g_recordingThread.joinable()) {
            g_recordingThread.join(); // 終了を待機
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

EXPORT_API const char* Recorder_GetLastError() {
    std::lock_guard<std::mutex> lock(g_errorMutex);
    return g_lastError.c_str();
}


class AudioSyncQueue {
    std::deque<float> m_buffer;
    std::mutex m_mtx;
    std::condition_variable m_cv;
    size_t m_channels;
    size_t m_maxBufferSamples;

public:
    AudioSyncQueue(size_t channels = 2, size_t maxDelayMs = 80)
        : m_channels(channels), m_maxBufferSamples((48000 * channels * maxDelayMs) / 1000) {}

    void Push(const std::vector<float>& data) {
        if (data.empty()) return;
        std::lock_guard<std::mutex> lock(m_mtx);
        m_buffer.insert(m_buffer.end(), data.begin(), data.end());

        if (m_buffer.size() > m_maxBufferSamples) {
            size_t overflow = m_buffer.size() - m_maxBufferSamples;
            overflow = (overflow / m_channels) * m_channels;
            m_buffer.erase(m_buffer.begin(), m_buffer.begin() + overflow);
        }
        m_cv.notify_one();
    }

    void PopExact(size_t samples, std::vector<float>& out) {
        std::lock_guard<std::mutex> lock(m_mtx);
        out.assign(samples, 0.0f);

        size_t available = (std::min)(samples, m_buffer.size());
        if (available > 0) {
            std::copy(m_buffer.begin(), m_buffer.begin() + available, out.begin());
            m_buffer.erase(m_buffer.begin(), m_buffer.begin() + available);
        }
    }

    bool PopWait(size_t samples, std::vector<float>& out, int timeoutMs) {
        std::unique_lock<std::mutex> lock(m_mtx);
        if (!m_cv.wait_for(lock, std::chrono::milliseconds(timeoutMs), [this, samples]() {
            return m_buffer.size() >= samples;
        })) {
            return false;
        }
        out.assign(m_buffer.begin(), m_buffer.begin() + samples);
        m_buffer.erase(m_buffer.begin(), m_buffer.begin() + samples);
        return true;
    }
};


class NoiseGate {
    float m_threshold = 0.0f;
    float m_currentGain = 1.0f;

public:
    void SetThreshold(float threshold) {
        m_threshold = threshold;
    }

    void Process(float* buffer, size_t frames, size_t channels) {
        if (m_threshold <= 0.0001f) return;

        double sumSquares = 0.0;
        size_t totalSamples = frames * channels;
        for (size_t i = 0; i < totalSamples; ++i) {
            sumSquares += buffer[i] * buffer[i];
        }
        float rms = static_cast<float>(std::sqrt(sumSquares / totalSamples));

        float targetGain = (rms < m_threshold) ? 0.0f : 1.0f;
        float smoothingFactor = (targetGain < m_currentGain) ? 0.05f : 0.2f;

        for (size_t f = 0; f < frames; ++f) {
            m_currentGain += (targetGain - m_currentGain) * smoothingFactor;
            for (size_t c = 0; c < channels; ++c) {
                buffer[f * channels + c] *= m_currentGain;
            }
        }
    }
};
