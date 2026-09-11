#pragma once

#include <windows.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <string>
#include <vector>
#include <functional>
#include <atomic>
#include <thread>
#include <mutex>

enum class CaptureType {
    SystemLoopback,
    Microphone
};

class WasapiCapture {
public:
    WasapiCapture(CaptureType type, const std::string& deviceName = "");
    ~WasapiCapture();

    bool Initialize();
    bool Start();
    void Stop();

    // コールバック引数: data, numFrames, channels, sampleRate
    using DataCallback = std::function<void(const float*, uint32_t, uint32_t, uint32_t)>;
    void SetCallback(DataCallback cb) { m_callback = cb; }

    uint32_t GetSampleRate() const { return m_sampleRate; }
    uint32_t GetChannels() const { return m_channels; }
    std::string GetLastError() const { return m_lastError; }

private:
    void CaptureThreadFunc();
    void SetError(const std::string& err);
    std::string WideToUTF8(const std::wstring& wstr);

    CaptureType m_type;
    std::string m_deviceName;
    std::string m_lastError;
    
    IMMDevice* m_device = nullptr;
    IAudioClient* m_audioClient = nullptr;
    IAudioCaptureClient* m_captureClient = nullptr;
    WAVEFORMATEX* m_pwfx = nullptr;

    std::atomic<bool> m_isInitialized{false};
    std::atomic<bool> m_isRunning{false};
    std::thread m_thread;

    uint32_t m_sampleRate = 0;
    uint32_t m_channels = 0;

    DataCallback m_callback;
};
