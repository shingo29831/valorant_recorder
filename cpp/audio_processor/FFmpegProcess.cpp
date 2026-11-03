#include "FFmpegProcess.h"
#include <fstream>
#include <vector>

FFmpegProcess::FFmpegProcess() = default;

FFmpegProcess::~FFmpegProcess() {
    Stop(0);
}

void FFmpegProcess::Log(const std::string& msg) {
    if (m_logger) {
        m_logger(msg);
    }
}

bool FFmpegProcess::Start(const std::string& command, const std::string& logPath, bool lowPriority, std::string& outError) {
    HANDLE hReadPipe = NULL;
    HANDLE hWritePipe = NULL;
    SECURITY_ATTRIBUTES saAttr;
    saAttr.nLength = sizeof(SECURITY_ATTRIBUTES);
    saAttr.bInheritHandle = TRUE;
    saAttr.lpSecurityDescriptor = NULL;

    DWORD pipeSize = 8 * 1024 * 1024;
    if (!CreatePipe(&hReadPipe, &hWritePipe, &saAttr, pipeSize)) {
        outError = "Failed to create pipe";
        return false;
    }
    SetHandleInformation(hWritePipe, HANDLE_FLAG_INHERIT, 0);

    HANDLE hErrReadPipe = NULL;
    HANDLE hErrWritePipe = NULL;
    SECURITY_ATTRIBUTES saErr;
    saErr.nLength = sizeof(SECURITY_ATTRIBUTES);
    saErr.bInheritHandle = TRUE;
    saErr.lpSecurityDescriptor = NULL;

    if (!CreatePipe(&hErrReadPipe, &hErrWritePipe, &saErr, 0)) {
        outError = "Failed to create stderr pipe";
        CloseHandle(hReadPipe);
        CloseHandle(hWritePipe);
        return false;
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

    std::vector<char> cmdBuf(command.begin(), command.end());
    cmdBuf.push_back('\0');

    Log("Executing FFmpeg command: " + command);

    DWORD priorityClass = lowPriority ? BELOW_NORMAL_PRIORITY_CLASS : NORMAL_PRIORITY_CLASS;
    if (!CreateProcessA(NULL, cmdBuf.data(), NULL, NULL, TRUE,
                        CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | priorityClass,
                        NULL, NULL, &si, &pi)) {
        DWORD err = GetLastError();
        outError = "Failed to start FFmpeg process (CreateProcessA error: " + std::to_string(err) + ")";
        CloseHandle(hReadPipe);
        CloseHandle(hWritePipe);
        CloseHandle(hErrReadPipe);
        CloseHandle(hErrWritePipe);
        return false;
    }

    CloseHandle(hReadPipe);
    CloseHandle(hErrWritePipe);

    m_hProcess = pi.hProcess;
    m_hThread = pi.hThread;
    m_processId = pi.dwProcessId;
    m_hWritePipe = hWritePipe;
    m_hErrReadPipe = hErrReadPipe;
    m_captureStarted = false;

    m_logThread = std::thread([this, logPath]() {
        std::ofstream logFile(logPath, std::ios::out | std::ios::trunc);
        char buf[1024];
        DWORD bytesRead = 0;
        std::string accumulated;

        while (ReadFile(m_hErrReadPipe, buf, sizeof(buf) - 1, &bytesRead, NULL) && bytesRead > 0) {
            buf[bytesRead] = '\0';
            if (logFile.is_open()) {
                logFile.write(buf, bytesRead);
                if (!m_captureStarted) {
                    logFile.flush();
                }
            }
            if (!m_captureStarted) {
                accumulated.append(buf, bytesRead);
                if (accumulated.find("Stream mapping:") != std::string::npos ||
                    accumulated.find("Press [q]") != std::string::npos ||
                    accumulated.find("Output #0") != std::string::npos ||
                    accumulated.find("frame=") != std::string::npos) {
                    m_captureStarted = true;
                }
            }
        }
        CloseHandle(m_hErrReadPipe);
        m_hErrReadPipe = NULL;
    });

    if (WaitForSingleObject(m_hProcess, 200) == WAIT_OBJECT_0) {
        DWORD exitCode = GetExitCode();
        outError = "FFmpeg process crashed immediately with exit code " + std::to_string(exitCode);
        CleanupHandles();
        return false;
    }

    return true;
}

bool FFmpegProcess::WriteAudio(const void* data, size_t sizeBytes, DWORD& bytesWritten) {
    if (m_hWritePipe == NULL) {
        return false;
    }
    return WriteFile(m_hWritePipe, data, static_cast<DWORD>(sizeBytes), &bytesWritten, NULL) != FALSE;
}

void FFmpegProcess::FlushInput() {
    if (m_hWritePipe != NULL) {
        FlushFileBuffers(m_hWritePipe);
    }
}

void FFmpegProcess::CloseInputPipe() {
    if (m_hWritePipe != NULL) {
        CloseHandle(m_hWritePipe);
        m_hWritePipe = NULL;
    }
}

bool FFmpegProcess::IsExited(DWORD waitMs, DWORD* outExitCode) {
    if (m_hProcess == NULL) {
        if (outExitCode) *outExitCode = 0;
        return true;
    }
    DWORD res = WaitForSingleObject(m_hProcess, waitMs);
    if (res == WAIT_OBJECT_0) {
        if (outExitCode) {
            *outExitCode = GetExitCode();
        }
        return true;
    }
    return false;
}

DWORD FFmpegProcess::GetExitCode() {
    if (m_hProcess == NULL) return 0;
    DWORD exitCode = 0;
    GetExitCodeProcess(m_hProcess, &exitCode);
    return exitCode;
}

void FFmpegProcess::SetLowPriority(bool lowPriority) {
    if (m_hProcess != NULL) {
        DWORD prio = lowPriority ? BELOW_NORMAL_PRIORITY_CLASS : NORMAL_PRIORITY_CLASS;
        SetPriorityClass(m_hProcess, prio);
        Log(std::string("Updated FFmpeg process priority to ") + (lowPriority ? "BELOW_NORMAL" : "NORMAL"));
    }
}

void FFmpegProcess::Stop(DWORD waitTimeoutMs) {
    CloseInputPipe();

    if (m_hProcess != NULL) {
        if (WaitForSingleObject(m_hProcess, 0) == WAIT_TIMEOUT) {
            if (!GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, m_processId)) {
                if (GetConsoleWindow() == NULL && AttachConsole(m_processId)) {
                    SetConsoleCtrlHandler(NULL, TRUE);
                    GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, m_processId);
                    FreeConsole();
                }
            }
        }

        DWORD waitProc = WaitForSingleObject(m_hProcess, waitTimeoutMs);
        if (waitProc == WAIT_TIMEOUT) {
            Log("Warning: FFmpeg process did not exit in time. Terminating...");
            TerminateProcess(m_hProcess, 0);
            WaitForSingleObject(m_hProcess, 2000);
        }
    }

    CleanupHandles();
}

void FFmpegProcess::CleanupHandles() {
    CloseInputPipe();

    if (m_hProcess != NULL) {
        CloseHandle(m_hProcess);
        m_hProcess = NULL;
    }
    if (m_hThread != NULL) {
        CloseHandle(m_hThread);
        m_hThread = NULL;
    }
    if (m_logThread.joinable()) {
        m_logThread.join();
    }
}
