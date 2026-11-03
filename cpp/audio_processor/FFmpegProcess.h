#pragma once

#include <windows.h>
#include <atomic>
#include <functional>
#include <string>
#include <thread>

// FFmpegプロセスの起動・パイプ入出力・ログ監視・終了制御をカプセル化するクラス
class FFmpegProcess {
public:
    using LoggerFunc = std::function<void(const std::string&)>;

    FFmpegProcess();
    ~FFmpegProcess();

    FFmpegProcess(const FFmpegProcess&) = delete;
    FFmpegProcess& operator=(const FFmpegProcess&) = delete;

    void SetLogger(LoggerFunc logger) { m_logger = logger; }

    bool Start(const std::string& command, const std::string& logPath, bool lowPriority, std::string& outError);
    bool WriteAudio(const void* data, size_t sizeBytes, DWORD& bytesWritten);
    void FlushInput();
    void CloseInputPipe();
    bool HasCaptureStarted() const { return m_captureStarted.load(); }
    bool IsExited(DWORD waitMs = 0, DWORD* outExitCode = nullptr);
    DWORD GetExitCode();
    void SetLowPriority(bool lowPriority);
    void Stop(DWORD waitTimeoutMs = 10000);

private:
    void Log(const std::string& msg);
    void CleanupHandles();

    HANDLE m_hProcess{NULL};
    HANDLE m_hThread{NULL};
    DWORD m_processId{0};
    HANDLE m_hWritePipe{NULL};
    HANDLE m_hErrReadPipe{NULL};

    std::atomic<bool> m_captureStarted{false};
    std::thread m_logThread;
    LoggerFunc m_logger;
};
