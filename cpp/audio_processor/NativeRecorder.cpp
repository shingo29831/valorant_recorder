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
static std::mutex g_processMutex;
static HANDLE g_ffmpegProcess = NULL;

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
            m_state = speex_resampler_init(m_channels, m_inRate, m_outRate, 5, &err);
            if (m_state) {
                speex_resampler_skip_zeros(m_state);
            }
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

class AudioSyncQueue {
    std::deque<float> m_buffer;
    std::mutex m_mtx;
    std::condition_variable m_cv;
    size_t m_channels;
    size_t m_maxBufferSamples;
    size_t m_minStartSamples;
    bool m_isBuffering = true;

public:
    AudioSyncQueue(size_t channels = 2, size_t maxDelayMs = 2000)
        : m_channels(channels),
          m_maxBufferSamples((48000 * channels * maxDelayMs) / 1000),
          m_minStartSamples(480 * 3 * channels) {}

    size_t AvailableSamples() {
        std::lock_guard<std::mutex> lock(m_mtx);
        return m_buffer.size();
    }

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

    void DrainKeepLatest(size_t keepSamples) {
        std::lock_guard<std::mutex> lock(m_mtx);
        if (m_buffer.size() > keepSamples) {
            size_t drop = m_buffer.size() - keepSamples;
            drop = (drop / m_channels) * m_channels;
            m_buffer.erase(m_buffer.begin(), m_buffer.begin() + drop);
        }
        m_isBuffering = (m_buffer.size() < m_minStartSamples);
    }

    bool PopChunk(size_t samples, std::vector<float>& out, size_t maxAllowedBuffer) {
        std::lock_guard<std::mutex> lock(m_mtx);

        // クロックドリフト蓄積時は中間クッション量(50ms)まで戻し頻繁な破棄を防止
        if (m_buffer.size() > maxAllowedBuffer) {
            size_t targetKeep = (std::max)(samples * 5, m_minStartSamples);
            if (m_buffer.size() > targetKeep) {
                size_t excess = m_buffer.size() - targetKeep;
                excess = (excess / m_channels) * m_channels;
                m_buffer.erase(m_buffer.begin(), m_buffer.begin() + excess);
            }
        }

        // ヒステリシス付きプリバッファリングでパケット境界競合による周期的ゼロ埋め(低音化)を防止
        if (m_isBuffering) {
            if (m_buffer.size() < m_minStartSamples) {
                return false;
            }
            m_isBuffering = false;
        }

        if (m_buffer.size() < samples) {
            m_isBuffering = true;
            return false;
        }

        out.assign(m_buffer.begin(), m_buffer.begin() + samples);
        m_buffer.erase(m_buffer.begin(), m_buffer.begin() + samples);
        return true;
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

    void NotifyAll() {
        m_cv.notify_all();
    }
};

class NoiseGate {
    float m_threshold = 0.0f;
    float m_currentGain = 1.0f;
    int m_holdFramesRemaining = 0;
    static constexpr int kHoldFrames = 30; // 10ms * 30 = 300msのホールド時間で語尾・無声子音の途切れを防止

public:
    void SetThreshold(float thresholdPercent) {
        float normalized = std::clamp(thresholdPercent, 0.0f, 1.0f);
        // VolumeMeterの表示スケール(level = sqrt(peak))と一致するピーク振幅閾値へ変換
        if (normalized <= 0.001f) {
            m_threshold = 0.0f;
        } else {
            m_threshold = normalized * normalized;
        }
    }

    void Process(float* buffer, size_t frames, size_t channels) {
        if (m_threshold <= 0.00001f) return;

        float peak = 0.0f;
        size_t totalSamples = frames * channels;
        for (size_t i = 0; i < totalSamples; ++i) {
            peak = (std::max)(peak, std::fabs(buffer[i]));
        }

        bool open = (peak >= m_threshold);
        if (open) {
            m_holdFramesRemaining = kHoldFrames;
        } else if (m_holdFramesRemaining > 0) {
            --m_holdFramesRemaining;
            open = true;
        }

        float targetGain = open ? 1.0f : 0.0f;
        // 開く時は素早く(約5ms)、閉じる時は緩やかに(約150ms)フェードしてチャタリング・ブツ音を防止
        float smoothingFactor = open ? 0.35f : 0.03f;

        for (size_t f = 0; f < frames; ++f) {
            m_currentGain += (targetGain - m_currentGain) * smoothingFactor;
            for (size_t c = 0; c < channels; ++c) {
                buffer[f * channels + c] *= m_currentGain;
            }
        }
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
            float fl = ch[0];
            float fr = ch[1];
            float fc = ch[2];
            float sl = ch[4];
            float sr = ch[5];
            float rl = (channels >= 8) ? ch[6] : 0.0f;
            float rr = (channels >= 8) ? ch[7] : 0.0f;

            // ITU-R BS.775準拠: LFEを除外して低域過多・位相干渉を防止
            float left  = (fl + 0.7071f * fc + 0.7071f * sl + 0.7071f * rl) * 0.7071f * gain;
            float right = (fr + 0.7071f * fc + 0.7071f * sr + 0.7071f * rr) * 0.7071f * gain;

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
    const size_t outSamples = framesPerChunk * 4; // 4ch (Sys: 2ch, Mic: 2ch)

    // 停止要求後もキュー内の未書き込み音声を全てパイプへ書き切って末尾欠損を防止
    while (g_isWriterRunning || outQueue->AvailableSamples() >= outSamples) {
        if (outQueue->PopWait(outSamples, outBuf, 50)) {
            DWORD written = 0;
            if (!WriteFile(hWritePipe, outBuf.data(), static_cast<DWORD>(outBuf.size() * sizeof(float)), &written, NULL)) {
                DWORD err = GetLastError();
                NativeLog("WriteFile failed. Error code: " + std::to_string(err) + ". Pipe closed.");
                g_isRunning = false;
                break;
            }
        }
    }
    FlushFileBuffers(hWritePipe);
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

    HANDLE hErrReadPipe, hErrWritePipe;
    SECURITY_ATTRIBUTES saErr;
    saErr.nLength = sizeof(SECURITY_ATTRIBUTES);
    saErr.bInheritHandle = TRUE;
    saErr.lpSecurityDescriptor = NULL;

    if (!CreatePipe(&hErrReadPipe, &hErrWritePipe, &saErr, 0)) {
        SetLastErrorMsg("Failed to create stderr pipe");
        CloseHandle(hReadPipe);
        CloseHandle(hWritePipe);
        g_isRunning = false;
        return;
    }
    SetHandleInformation(hErrReadPipe, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.hStdInput = hReadPipe;
    si.hStdOutput = hErrWritePipe;
    si.hStdError = hErrWritePipe;
    si.dwFlags |= STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    ZeroMemory(&pi, sizeof(pi));

    std::vector<char> cmdBuf(ffmpegCmd.begin(), ffmpegCmd.end());
    cmdBuf.push_back('\0');

    NativeLog("Executing FFmpeg command: " + ffmpegCmd);

    // ゲームプロセスの描画スレッドを阻害しないよう通常時はNORMAL_PRIORITY_CLASSで起動
    DWORD priorityClass = (configCopy.low_priority != 0) ? BELOW_NORMAL_PRIORITY_CLASS : NORMAL_PRIORITY_CLASS;
    if (!CreateProcessA(NULL, cmdBuf.data(), NULL, NULL, TRUE, CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | priorityClass, NULL, NULL, &si, &pi)) {
        DWORD err = GetLastError();
        SetLastErrorMsg("Failed to start FFmpeg process (CreateProcessA error: " + std::to_string(err) + ")");
        CloseHandle(hReadPipe);
        CloseHandle(hWritePipe);
        CloseHandle(hErrReadPipe);
        CloseHandle(hErrWritePipe);
        g_isRunning = false;
        return;
    }
    CloseHandle(hReadPipe);
    CloseHandle(hErrWritePipe); // 子プロセスへの継承後に書き込み端を閉じる

    {
        std::lock_guard<std::mutex> lock(g_processMutex);
        g_ffmpegProcess = pi.hProcess;
    }

    // FFmpegのログを非同期受信し、エンコード開始（ddagrab初期化完了）を検知
    std::atomic<bool> ffmpegCaptureStarted{false};
    std::thread logReaderThread([hErrReadPipe, ffmpegLogPath, &ffmpegCaptureStarted]() {
        std::ofstream logFile(ffmpegLogPath, std::ios::out | std::ios::trunc);
        char buf[1024];
        DWORD bytesRead = 0;
        std::string accumulated;

        while (ReadFile(hErrReadPipe, buf, sizeof(buf) - 1, &bytesRead, NULL) && bytesRead > 0) {
            buf[bytesRead] = '\0';
            if (logFile.is_open()) {
                logFile.write(buf, bytesRead);
                // 起動完了後は毎パケットのディスクflushを抑止してI/O負荷を削減
                if (!ffmpegCaptureStarted) {
                    logFile.flush();
                }
            }
            if (!ffmpegCaptureStarted) {
                accumulated.append(buf, bytesRead);
                if (accumulated.find("Stream mapping:") != std::string::npos ||
                    accumulated.find("Press [q]") != std::string::npos ||
                    accumulated.find("Output #0") != std::string::npos ||
                    accumulated.find("frame=") != std::string::npos) {
                    ffmpegCaptureStarted = true;
                }
            }
        }
        CloseHandle(hErrReadPipe);
    });

    DWORD waitResult = WaitForSingleObject(pi.hProcess, 200);
    if (waitResult == WAIT_OBJECT_0) {
        DWORD exitCode = 0;
        GetExitCodeProcess(pi.hProcess, &exitCode);
        SetLastErrorMsg("FFmpeg process crashed immediately with exit code " + std::to_string(exitCode));
        CloseHandle(hWritePipe);
        if (logReaderThread.joinable()) logReaderThread.join();
        {
            std::lock_guard<std::mutex> lock(g_processMutex);
            g_ffmpegProcess = NULL;
        }
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
        g_isRunning = false;
        return;
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
    g_writerThread = std::thread(WriterThreadFunc, hWritePipe, &outQueue);

    const size_t framesPerChunk = 480;
    const size_t sysSamples = framesPerChunk * 2;
    const size_t micSamples = framesPerChunk * 2;

    std::vector<float> sysBuf(sysSamples);
    std::vector<float> micBuf(micSamples);
    std::vector<float> micProcessed(micSamples);
    std::vector<float> outBuf(framesPerChunk * 4);

    timeBeginPeriod(1);
    SetThreadPriority(GetCurrentThread(), (configCopy.low_priority != 0) ? THREAD_PRIORITY_NORMAL : THREAD_PRIORITY_ABOVE_NORMAL);

    // FFmpegのpipe:0ストリーム解析ブロックを即座に解除するため初期1チャンク(10ms)を送出
    outQueue.Push(std::vector<float>(framesPerChunk * 4, 0.0f));

    // キャプチャ開始直後の空バッファによる初回アンダーランを防ぐため、最低50msのプリバッファを確実に蓄積
    const size_t initialCushionSamples = framesPerChunk * 5 * 2;
    auto syncWaitStart = std::chrono::steady_clock::now();
    while (g_isRunning) {
        if (WaitForSingleObject(pi.hProcess, 0) == WAIT_OBJECT_0) {
            DWORD exitCode = 0;
            GetExitCodeProcess(pi.hProcess, &exitCode);
            SetLastErrorMsg("FFmpeg process exited during initialization with code " + std::to_string(exitCode));
            g_isRunning = false;
            break;
        }
        auto elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - syncWaitStart
        ).count();
        bool hasCushion = (sysQueue.AvailableSamples() >= initialCushionSamples) &&
                          (!g_micCapture || !g_micCapture->IsInitialized() || micQueue.AvailableSamples() >= initialCushionSamples);
        if ((ffmpegCaptureStarted && hasCushion) || elapsedMs >= 500) {
            break;
        }
        Sleep(5);
    }

    sysQueue.DrainKeepLatest(initialCushionSamples);
    micQueue.DrainKeepLatest(initialCushionSamples);

    auto startTime = std::chrono::steady_clock::now();
    uint64_t totalWrittenFrames = 0;
    auto lastLogTime = startTime;

    const size_t reqSysSamples = framesPerChunk * 2;
    const size_t reqMicSamples = framesPerChunk * 2;
    const size_t maxAllowedBuffer = framesPerChunk * 20 * 2; // 最大約200ms

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
            // ノイズキャンセリング後のクリーンな音声に対してノイズゲートを適用
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
        if (WaitForSingleObject(pi.hProcess, 0) == WAIT_OBJECT_0) {
            DWORD exitCode = 0;
            GetExitCodeProcess(pi.hProcess, &exitCode);
            SetLastErrorMsg("FFmpeg process terminated unexpectedly with code " + std::to_string(exitCode));
            g_isRunning = false;
            break;
        }

        auto now = std::chrono::steady_clock::now();
        double elapsedSec = std::chrono::duration<double>(now - startTime).count();
        uint64_t targetFrames = static_cast<uint64_t>(elapsedSec * 48000.0);

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

    // 停止押下時点の映像パイプライン遅延と音声プリバッファ分を確実に回収するためテールキャプチャを実行
    if (WaitForSingleObject(pi.hProcess, 0) == WAIT_TIMEOUT) {
        auto tailEnd = std::chrono::steady_clock::now() + std::chrono::milliseconds(250);
        while (std::chrono::steady_clock::now() < tailEnd) {
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

    // WASAPI停止後にキューへ残っているプリバッファクッション分を全てドレイン出力
    if (WaitForSingleObject(pi.hProcess, 0) == WAIT_TIMEOUT) {
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

    CloseHandle(hWritePipe);
    
    DWORD waitProc = WaitForSingleObject(pi.hProcess, 10000);
    if (waitProc == WAIT_TIMEOUT) {
        NativeLog("Warning: FFmpeg process did not exit in time. Terminating...");
        TerminateProcess(pi.hProcess, 0);
        WaitForSingleObject(pi.hProcess, 2000);
    }

    {
        std::lock_guard<std::mutex> lock(g_processMutex);
        g_ffmpegProcess = NULL;
    }
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    if (logReaderThread.joinable()) {
        logReaderThread.join();
    }
    g_isRunning = false;
}

EXPORT_API bool Recorder_Start(const RecorderConfig* config) {
    // 前回のスレッドが終了済みかつ未joinなら安全に回収
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

EXPORT_API void Recorder_SetLowPriority(bool lowPriority) {
    std::lock_guard<std::mutex> lock(g_processMutex);
    if (g_ffmpegProcess != NULL) {
        DWORD prio = lowPriority ? BELOW_NORMAL_PRIORITY_CLASS : NORMAL_PRIORITY_CLASS;
        SetPriorityClass(g_ffmpegProcess, prio);
        NativeLog(std::string("Updated FFmpeg process priority to ") + (lowPriority ? "BELOW_NORMAL" : "NORMAL"));
    }
}

EXPORT_API const char* Recorder_GetLastError() {
    std::lock_guard<std::mutex> lock(g_errorMutex);
    return g_lastError.c_str();
}
