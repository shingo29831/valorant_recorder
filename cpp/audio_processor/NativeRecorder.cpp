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

// スレッドセーフなオーディオキュー
class AudioQueue {
    // 【根本解決】
    // 以前は std::vector を使用していたが、バッファ上限到達時の古いデータ破棄 (erase) が O(N) の計算量となり、
    // 特に outQueue (最大10秒分 = 2,880,000要素) で erase が発生すると約11MBのメモリシフトが毎フレーム発生し、
    // CPU負荷が跳ね上がって録画ループ全体をブロック(遅延)させる根本原因となっていた。
    // std::deque に変更することで、先頭からの削除を O(削除要素数) で行えるようにし、遅延とカクつきを解消する。
    std::deque<float> buffer;
    std::mutex mtx;
    std::condition_variable cv;
    size_t max_samples;
public:
    AudioQueue(size_t max_samples = 48000 * 2) : max_samples(max_samples) {} // デフォルト1秒分(2ch)

    void Push(const std::vector<float>& data) {
        std::lock_guard<std::mutex> lock(mtx);
        buffer.insert(buffer.end(), data.begin(), data.end());
        // バッファ肥大化(メモリリーク)を防ぐため、上限を超えたら古いデータを破棄
        if (buffer.size() > max_samples) {
            size_t overflow = buffer.size() - max_samples;
            buffer.erase(buffer.begin(), buffer.begin() + overflow);
        }
        cv.notify_one();
    }
    
    // 指定サンプル数を必ず返す。足りない場合はあるだけ取り出し、残りは0埋めする。
    // これにより、データ不足時に一切取り出さずバッファに滞留し、後で一気に放出されることによるバリバリノイズを防ぐ。
    void PopExact(size_t samples, std::vector<float>& out, int timeout_ms) {
        std::unique_lock<std::mutex> lock(mtx);
        out.assign(samples, 0.0f);
        
        cv.wait_for(lock, std::chrono::milliseconds(timeout_ms), [this, samples]() { return buffer.size() >= samples; });
        
        // Windowsの min/max マクロとの競合(C2589エラー)を防ぐため三項演算子を使用
        size_t available = samples < buffer.size() ? samples : buffer.size();
        if (available > 0) {
            std::copy(buffer.begin(), buffer.begin() + available, out.begin());
            buffer.erase(buffer.begin(), buffer.begin() + available);
        }
    }

    // データが指定サンプル数揃うまで待機し、揃ったら取り出す（出力スレッド用）
    bool PopWait(size_t samples, std::vector<float>& out, int timeout_ms) {
        std::unique_lock<std::mutex> lock(mtx);
        if (!cv.wait_for(lock, std::chrono::milliseconds(timeout_ms), [this, samples]() { return buffer.size() >= samples; })) {
            return false; // タイムアウト
        }
        out.assign(buffer.begin(), buffer.begin() + samples);
        buffer.erase(buffer.begin(), buffer.begin() + samples);
        return true;
    }
};

// パイプ書き込み専用スレッドの制御フラグ
static std::atomic<bool> g_isWriterRunning{false};
static std::thread g_writerThread;

// パイプ書き込み専用スレッド
// FFmpegが映像待ちで音声パイプの読み込みをブロックしても、メインの録画ループを停止させないために分離。
static void WriterThreadFunc(HANDLE hWritePipe, AudioQueue* outQueue) {
    NativeLog("Starting WriterThreadFunc...");
    std::vector<float> outBuf;
    const size_t framesPerChunk = 480;
    const size_t outSamples = framesPerChunk * 6;

    while (g_isWriterRunning) {
        // データが揃うまで最大100ms待機
        if (outQueue->PopWait(outSamples, outBuf, 100)) {
            DWORD written;
            if (!WriteFile(hWritePipe, outBuf.data(), outBuf.size() * sizeof(float), &written, NULL)) {
                DWORD err = GetLastError();
                NativeLog("WriteFile failed. Error code: " + std::to_string(err) + ". Pipe closed.");
                break; // パイプが閉じられた (FFmpeg終了)
            }
        }
    }
    NativeLog("Exiting WriterThreadFunc...");
}

// 録画スレッドのメイン処理
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
    g_micCapture->Initialize(); // マイクは失敗しても続行

    AudioQueue sysQueue, micQueue;
    std::unique_ptr<Resampler> sysResampler;
    std::unique_ptr<Resampler> micResampler;
    std::mutex resamplerMtx;

    float sysGain = configCopy.system_gain;
    float micGain = configCopy.mic_gain;

    g_sysCapture->SetCallback([&](const float* data, uint32_t frames, uint32_t channels, uint32_t sampleRate) {
        std::lock_guard<std::mutex> lock(resamplerMtx);
        if (!sysResampler) sysResampler = std::make_unique<Resampler>(channels, sampleRate, 48000);
        std::vector<float> out;
        sysResampler->Process(data, frames, out);
        
        // 確実に2ch(ステレオ)に変換する (7.1ch環境などでの配列外参照・ノイズを防止)
        uint32_t out_frames = out.size() / channels;
        std::vector<float> stereoOut(out_frames * 2, 0.0f);
        for (uint32_t i = 0; i < out_frames; ++i) {
            if (channels == 1) {
                stereoOut[i * 2 + 0] = out[i] * sysGain;
                stereoOut[i * 2 + 1] = out[i] * sysGain;
            } else {
                stereoOut[i * 2 + 0] = out[i * channels + 0] * sysGain;
                stereoOut[i * 2 + 1] = out[i * channels + 1] * sysGain;
            }
        }
        sysQueue.Push(stereoOut);
    });

    g_micCapture->SetCallback([&](const float* data, uint32_t frames, uint32_t channels, uint32_t sampleRate) {
        std::lock_guard<std::mutex> lock(resamplerMtx);
        if (!micResampler) micResampler = std::make_unique<Resampler>(channels, sampleRate, 48000);
        std::vector<float> out;
        micResampler->Process(data, frames, out);
        
        // 確実に2ch(ステレオ)に変換する
        uint32_t out_frames = out.size() / channels;
        std::vector<float> stereoOut(out_frames * 2, 0.0f);
        for (uint32_t i = 0; i < out_frames; ++i) {
            if (channels == 1) {
                stereoOut[i * 2 + 0] = out[i] * micGain;
                stereoOut[i * 2 + 1] = out[i] * micGain;
            } else {
                stereoOut[i * 2 + 0] = out[i * channels + 0] * micGain;
                stereoOut[i * 2 + 1] = out[i * channels + 1] * micGain;
            }
        }
        micQueue.Push(stereoOut);
    });

    // FFmpegへの標準入力用パイプを作成
    HANDLE hReadPipe, hWritePipe;
    SECURITY_ATTRIBUTES saAttr;
    saAttr.nLength = sizeof(SECURITY_ATTRIBUTES);
    saAttr.bInheritHandle = TRUE;
    saAttr.lpSecurityDescriptor = NULL;
    
    // パイプバッファサイズを8MBに拡張 (約7秒分)
    // デフォルトサイズ(4KB)だと、FFmpegのエンコード負荷スパイク時にWriteFileがブロックされ、
    // 録画ループ全体が停止して映像のカクつき(フレームドロップ)を引き起こす根本原因となるため。
    DWORD pipeSize = 8 * 1024 * 1024; 
    if (!CreatePipe(&hReadPipe, &hWritePipe, &saAttr, pipeSize)) {
        SetLastErrorMsg("Failed to create pipe");
        g_isRunning = false;
        return;
    }
    SetHandleInformation(hWritePipe, HANDLE_FLAG_INHERIT, 0);

    // FFmpegのログ出力用ファイルハンドルを作成 (クラッシュ防止とデバッグ用)
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
    
    // FILE_SHARE_WRITE を追加し、FFmpeg内部からの複数ストリーム書き込みによる共有違反を防ぐ
    HANDLE hLogFile = CreateFileA(ffmpegLogPath.c_str(), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &saLog, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hLogFile == INVALID_HANDLE_VALUE) {
        // ログファイルの作成に失敗した場合は NUL デバイスを開き、無効なハンドルによるクラッシュを防ぐ
        hLogFile = CreateFileA("NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &saLog, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    }

    // FFmpegプロセスをバックグラウンドで起動
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

    if (!CreateProcessA(NULL, cmdBuf.data(), NULL, NULL, TRUE, CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, NULL, NULL, &si, &pi)) {
        SetLastErrorMsg("Failed to start FFmpeg process");
        CloseHandle(hReadPipe);
        CloseHandle(hWritePipe);
        if (hLogFile != INVALID_HANDLE_VALUE) CloseHandle(hLogFile);
        g_isRunning = false;
        return;
    }
    CloseHandle(hReadPipe); // 親プロセス側ではReadハンドルを閉じる
    
    if (hLogFile != INVALID_HANDLE_VALUE) {
        CloseHandle(hLogFile); // 子プロセスに継承されたため、親プロセス側のハンドルは直ちに閉じる
    }

    // モデルファイルの存在チェック (Rust側のパニック防止)
    DWORD fileAttr = GetFileAttributesA(dfModelPath.c_str());
    if (fileAttr == INVALID_FILE_ATTRIBUTES || (fileAttr & FILE_ATTRIBUTE_DIRECTORY)) {
        SetLastErrorMsg("DeepFilterNet model file not found: " + dfModelPath);
        CloseHandle(hWritePipe);
        g_isRunning = false;
        return;
    }

    // AudioProcessor (AIノイズ除去) の初期化
    void* audioProc = AudioProcessor_Create(48000, 2, dfModelPath.c_str());
    if (!audioProc) {
        SetLastErrorMsg("Failed to initialize AudioProcessor");
        CloseHandle(hWritePipe);
        g_isRunning = false;
        return;
    }
    AudioProcessor_SetPreProcessType(audioProc, 1); // 1 = SpeexDSP
    AudioProcessor_SetDenoiseType(audioProc, 1);    // 1 = DeepFilterNet

    g_sysCapture->Start();
    g_micCapture->Start();

    // 出力用キュー (FFmpegブロック時のバッファリング用、最大10秒分 = 48000 * 6 * 10)
    AudioQueue outQueue(48000 * 6 * 10);
    g_isWriterRunning = true;
    g_writerThread = std::thread(WriterThreadFunc, hWritePipe, &outQueue);

    const size_t framesPerChunk = 480; // 10ms at 48000Hz
    const size_t sysSamples = framesPerChunk * 2; // 2ch
    const size_t micSamples = framesPerChunk * 2; // 2ch

    std::vector<float> sysBuf, micBuf;
    std::vector<float> outBuf(framesPerChunk * 6); // 6ch (MixL, MixR, SysL, SysR, MicL, MicR)

    // ジッター(揺らぎ)吸収のための初期バッファリング (200ms分に拡張)
    // クロックドリフトに対する耐性を高め、バッファ枯渇を長期間防ぐ。
    std::vector<float> initialPadding(48000 * 2 * 200 / 1000, 0.0f);
    sysQueue.Push(initialPadding);
    micQueue.Push(initialPadding);

    // Windowsのタイマー精度を1msに向上させ、sleep_untilの遅延を防ぐ
    timeBeginPeriod(1);
    
    auto nextTime = std::chrono::steady_clock::now();
    const auto frameDuration = std::chrono::milliseconds(10);
    
    uint64_t totalWrittenFrames = 0;
    auto lastLogTime = std::chrono::steady_clock::now();

    NativeLog("Entering main recording loop...");

    while (g_isRunning) {
        nextTime += frameDuration;

        // 【重要】タイムアウトを 0 に設定する。
        // WASAPIは無音時にデータを出力しない(サイレンスサプレッション)ため、ここで待機するとループ全体が遅延し、
        // 1秒間に生成される音声データが48000Hzに満たなくなる(アンダーラン)。
        // タイムアウト0にすることで、無音時でも正確に10ms周期で無音データが生成され、FFmpegのAV Sync崩壊を防ぐ。
        sysQueue.PopExact(sysSamples, sysBuf, 0);
        micQueue.PopExact(micSamples, micBuf, 0);

        // マイク音声にAIノイズ除去を適用
        std::vector<float> micProcessed(framesPerChunk * 2);
        AudioProcessor_Process(audioProc, micBuf.data(), micProcessed.data(), framesPerChunk);

        // 6chにインターリーブ (FFmpeg側での複雑なミックス処理を避け、クラッシュと遅延を防ぐ)
        for (size_t i = 0; i < framesPerChunk; ++i) {
            float sysL = sysBuf[i * 2 + 0];
            float sysR = sysBuf[i * 2 + 1];
            float micL = micProcessed[i * 2 + 0];
            float micR = micProcessed[i * 2 + 1];
            
            // ミックス (クリッピング防止のため -1.0 ~ 1.0 にクランプ)
            // Windowsのmin/maxマクロとの競合(C2589エラー)を防ぐため std::clamp を使用
            float mixL = std::clamp(sysL + micL, -1.0f, 1.0f);
            float mixR = std::clamp(sysR + micR, -1.0f, 1.0f);
            
            outBuf[i * 6 + 0] = mixL;
            outBuf[i * 6 + 1] = mixR;
            outBuf[i * 6 + 2] = sysL;
            outBuf[i * 6 + 3] = sysR;
            outBuf[i * 6 + 4] = micL;
            outBuf[i * 6 + 5] = micR;
        }

        // WriteFileの代わりに出力キューへプッシュし、メインループのブロックを防ぐ
        outQueue.Push(outBuf);
        
        totalWrittenFrames += framesPerChunk;
        auto now = std::chrono::steady_clock::now();
        if (std::chrono::duration_cast<std::chrono::seconds>(now - lastLogTime).count() >= 5) {
            NativeLog("Status: Running. Total frames written: " + std::to_string(totalWrittenFrames));
            lastLogTime = now;
        }

        // 高精度タイマー待機 (Sleep + スピンロック)
        now = std::chrono::steady_clock::now();
        if (now < nextTime) {
            auto sleepMs = std::chrono::duration_cast<std::chrono::milliseconds>(nextTime - now).count();
            if (sleepMs > 2) {
                Sleep(static_cast<DWORD>(sleepMs - 2)); // 2ms前までOSのSleepで待機
            }
            // 残りの微小時間はスピンロックで正確に待機
            while (std::chrono::steady_clock::now() < nextTime) {
                std::this_thread::yield();
            }
        } else {
            // 【根本解決】
            // 以前はここで nextTime を現在時刻にリセットしていたが、これが映像カクつきの根本原因であった。
            // リセットすると遅れた分のループがスキップされ、1秒間に生成される音声サンプル数が48000Hzを下回る。
            // FFmpeg側は asetpts=N/SR/TB でサンプル数ベースで時間を進めているため、
            // 音声サンプルが足りないと音声ストリームの時間が遅れ、映像(ddagrab)との同期(AV Sync)が崩壊して映像がドロップ(カクつき)する。
            // したがって、通常はリセットせず、遅れを取り戻すために待機なしでループを回して無音を生成し続ける。
            // ただし、PCスリープ復帰時などの極端な遅延(500ms以上)の場合は、スパイラルを防ぐためリセットする。
            if (now - nextTime > std::chrono::milliseconds(500)) {
                nextTime = now;
            }
        }
    }

    timeEndPeriod(1); // タイマー精度を元に戻す

    NativeLog("Exiting main recording loop...");

    g_sysCapture->Stop();
    g_micCapture->Stop();
    AudioProcessor_Destroy(audioProc);

    // ライタースレッドの停止
    g_isWriterRunning = false;
    if (g_writerThread.joinable()) {
        g_writerThread.join();
    }

    // パイプを閉じてFFmpegにEOFを通知し、エンコード完了を待つ
    CloseHandle(hWritePipe);
    WaitForSingleObject(pi.hProcess, 15000); // 最大15秒待機
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
    // 遅延蓄積を防ぐため最大バッファサイズを80msに制限
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
