#include "WasapiCapture.h"
#include <iostream>
#include <algorithm>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <comdef.h>
#include <functiondiscoverykeys_devpkey.h>
#include <mmsystem.h>

#define SAFE_RELEASE(punk) if ((punk) != NULL) { (punk)->Release(); (punk) = NULL; }

WasapiCapture::WasapiCapture(CaptureType type, const std::string& deviceName)
    : m_type(type), m_deviceName(deviceName) {
}

WasapiCapture::~WasapiCapture() {
    Stop();
    if (m_pwfx) {
        CoTaskMemFree(m_pwfx);
        m_pwfx = nullptr;
    }
    SAFE_RELEASE(m_captureClient);
    SAFE_RELEASE(m_audioClient);
    SAFE_RELEASE(m_silenceRenderClient);
    SAFE_RELEASE(m_silenceAudioClient);
    SAFE_RELEASE(m_device);
}

void WasapiCapture::SetError(const std::string& err) {
    m_lastError = err;
}

std::string WasapiCapture::WideToUTF8(const std::wstring& wstr) {
    if (wstr.empty()) return std::string();
    int size_needed = WideCharToMultiByte(CP_UTF8, 0, &wstr[0], (int)wstr.size(), NULL, 0, NULL, NULL);
    std::string strTo(size_needed, 0);
    WideCharToMultiByte(CP_UTF8, 0, &wstr[0], (int)wstr.size(), &strTo[0], size_needed, NULL, NULL);
    return strTo;
}

bool WasapiCapture::Initialize() {
    HRESULT hr = CoInitializeEx(NULL, COINIT_MULTITHREADED);

    IMMDeviceEnumerator* pEnumerator = nullptr;
    hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), NULL, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator), (void**)&pEnumerator);
    if (FAILED(hr)) {
        SetError("Failed to create MMDeviceEnumerator");
        return false;
    }

    EDataFlow dataFlow = (m_type == CaptureType::SystemLoopback) ? eRender : eCapture;
    if (m_deviceName.empty() || m_deviceName == "Default" || m_deviceName == "default") {
        hr = pEnumerator->GetDefaultAudioEndpoint(dataFlow, eConsole, &m_device);
    } else {
        // デバイス名（UTF-8）による検索
        IMMDeviceCollection* pCollection = nullptr;
        hr = pEnumerator->EnumAudioEndpoints(dataFlow, DEVICE_STATE_ACTIVE, &pCollection);
            if (SUCCEEDED(hr)) {
                UINT count = 0;
                pCollection->GetCount(&count);
                std::string lowerTarget = m_deviceName;
                std::transform(lowerTarget.begin(), lowerTarget.end(), lowerTarget.begin(), ::tolower);
                IMMDevice* partialMatchDevice = nullptr;

                for (UINT i = 0; i < count; i++) {
                    IMMDevice* pEndpoint = nullptr;
                    if (SUCCEEDED(pCollection->Item(i, &pEndpoint))) {
                        IPropertyStore* pProps = nullptr;
                        if (SUCCEEDED(pEndpoint->OpenPropertyStore(STGM_READ, &pProps))) {
                            PROPVARIANT varName;
                            PropVariantInit(&varName);
                            if (SUCCEEDED(pProps->GetValue(PKEY_Device_FriendlyName, &varName))) {
                                std::string utf8Name = WideToUTF8(varName.pwszVal);
                                std::string lowerUtf8 = utf8Name;
                                std::transform(lowerUtf8.begin(), lowerUtf8.end(), lowerUtf8.begin(), ::tolower);

                                if (lowerUtf8 == lowerTarget) {
                                    m_device = pEndpoint;
                                    m_device->AddRef();
                                    PropVariantClear(&varName);
                                    SAFE_RELEASE(pProps);
                                    SAFE_RELEASE(pEndpoint);
                                    break;
                                } else if (!partialMatchDevice && (lowerUtf8.find(lowerTarget) != std::string::npos || lowerTarget.find(lowerUtf8) != std::string::npos)) {
                                    partialMatchDevice = pEndpoint;
                                    partialMatchDevice->AddRef();
                                }
                            }
                            PropVariantClear(&varName);
                            SAFE_RELEASE(pProps);
                        }
                        SAFE_RELEASE(pEndpoint);
                    }
                }
                if (!m_device && partialMatchDevice) {
                    m_device = partialMatchDevice;
                } else {
                    SAFE_RELEASE(partialMatchDevice);
                }
                SAFE_RELEASE(pCollection);
            }
        if (!m_device) {
            if (m_logger) {
                m_logger("Warning: Specified audio device '" + m_deviceName + "' not found. Falling back to default endpoint.");
            }
            hr = pEnumerator->GetDefaultAudioEndpoint(dataFlow, eConsole, &m_device);
            if (FAILED(hr) || !m_device) {
                SetError("Audio device not found: " + m_deviceName + " and default fallback failed.");
                SAFE_RELEASE(pEnumerator);
                return false;
            }
        }
    }
    SAFE_RELEASE(pEnumerator);

    if (!m_device) {
        SetError("Failed to get audio endpoint");
        return false;
    }

    hr = m_device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, NULL, (void**)&m_audioClient);
    if (FAILED(hr)) {
        SetError("Failed to activate IAudioClient");
        return false;
    }

    hr = m_audioClient->GetMixFormat(&m_pwfx);
    if (FAILED(hr)) {
        SetError("Failed to get mix format");
        return false;
    }

    m_sampleRate = m_pwfx->nSamplesPerSec;
    m_channels = m_pwfx->nChannels;

    DWORD streamFlags = (m_type == CaptureType::SystemLoopback) ? AUDCLNT_STREAMFLAGS_LOOPBACK : 0;
    // キャプチャ遅延を極小化するため要求バッファを20msに設定
    REFERENCE_TIME hnsRequestedDuration = 200000;

    hr = m_audioClient->Initialize(AUDCLNT_SHAREMODE_SHARED, streamFlags, hnsRequestedDuration, 0, m_pwfx, NULL);
    if (FAILED(hr)) {
        SetError("Failed to initialize IAudioClient");
        return false;
    }

    hr = m_audioClient->GetService(__uuidof(IAudioCaptureClient), (void**)&m_captureClient);
    if (FAILED(hr)) {
        SetError("Failed to get IAudioCaptureClient");
        return false;
    }

    if (m_logger) {
        m_logger("Initialized device: " + (m_deviceName.empty() ? "Default" : m_deviceName) + 
                 ", Rate: " + std::to_string(m_sampleRate) + 
                 ", Channels: " + std::to_string(m_channels) + 
                 ", FormatTag: " + std::to_string(m_pwfx->wFormatTag) + 
                 ", Bits: " + std::to_string(m_pwfx->wBitsPerSample));
    }

    // Loopbackキャプチャ時は無音時でもパケットが継続生成されるようサイレントレンダーを待機
    if (m_type == CaptureType::SystemLoopback && m_device) {
        hr = m_device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, NULL, (void**)&m_silenceAudioClient);
        if (SUCCEEDED(hr)) {
            WAVEFORMATEX* pRenderWfx = nullptr;
            hr = m_silenceAudioClient->GetMixFormat(&pRenderWfx);
            if (SUCCEEDED(hr) && pRenderWfx) {
                hr = m_silenceAudioClient->Initialize(AUDCLNT_SHAREMODE_SHARED, 0, 200000, 0, pRenderWfx, NULL);
                if (SUCCEEDED(hr)) {
                    m_silenceAudioClient->GetBufferSize(&m_silenceBufferFrames);
                    m_silenceAudioClient->GetService(__uuidof(IAudioRenderClient), (void**)&m_silenceRenderClient);
                }
                CoTaskMemFree(pRenderWfx);
            }
        }
    }

    m_isInitialized = true;
    return true;
}

bool WasapiCapture::Start() {
    if (!m_isInitialized || m_isRunning) return false;

    HRESULT hr = m_audioClient->Start();
    if (FAILED(hr)) {
        SetError("Failed to start IAudioClient");
        return false;
    }

    if (m_silenceAudioClient && m_silenceRenderClient) {
        BYTE* pData = nullptr;
        if (SUCCEEDED(m_silenceRenderClient->GetBuffer(m_silenceBufferFrames, &pData))) {
            memset(pData, 0, m_silenceBufferFrames * m_pwfx->nBlockAlign);
            m_silenceRenderClient->ReleaseBuffer(m_silenceBufferFrames, AUDCLNT_BUFFERFLAGS_SILENT);
        }
        m_silenceAudioClient->Start();
        m_silenceRunning = true;
        m_silenceThread = std::thread(&WasapiCapture::SilenceThreadFunc, this);
    }

    m_isRunning = true;
    m_thread = std::thread(&WasapiCapture::CaptureThreadFunc, this);
    return true;
}

void WasapiCapture::Stop() {
    if (m_isRunning) {
        m_isRunning = false;
        if (m_thread.joinable()) {
            m_thread.join();
        }
        if (m_audioClient) {
            m_audioClient->Stop();
        }
    }
    if (m_silenceRunning) {
        m_silenceRunning = false;
        if (m_silenceThread.joinable()) {
            m_silenceThread.join();
        }
        if (m_silenceAudioClient) {
            m_silenceAudioClient->Stop();
        }
    }
}

void WasapiCapture::SilenceThreadFunc() {
    CoInitializeEx(NULL, COINIT_MULTITHREADED);
    timeBeginPeriod(1);
    while (m_silenceRunning) {
        Sleep(5);
        if (!m_silenceAudioClient || !m_silenceRenderClient) break;
        UINT32 padding = 0;
        if (SUCCEEDED(m_silenceAudioClient->GetCurrentPadding(&padding))) {
            if (m_silenceBufferFrames > padding) {
                UINT32 framesAvailable = m_silenceBufferFrames - padding;
                BYTE* pData = nullptr;
                if (SUCCEEDED(m_silenceRenderClient->GetBuffer(framesAvailable, &pData))) {
                    memset(pData, 0, framesAvailable * m_pwfx->nBlockAlign);
                    m_silenceRenderClient->ReleaseBuffer(framesAvailable, AUDCLNT_BUFFERFLAGS_SILENT);
                }
            }
        }
    }
    timeEndPeriod(1);
    CoUninitialize();
}

void WasapiCapture::CaptureThreadFunc() {
    CoInitializeEx(NULL, COINIT_MULTITHREADED);
    timeBeginPeriod(1);
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL);

    UINT32 packetLength = 0;
    BYTE* pData = nullptr;
    UINT32 numFramesAvailable = 0;
    DWORD flags = 0;

    // GUIDの明示的な定義 (環境依存の比較エラーを防ぐ)
    const GUID GUID_SUBTYPE_PCM = { 0x00000001, 0x0000, 0x0010, { 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71 } };
    const GUID GUID_SUBTYPE_IEEE_FLOAT = { 0x00000003, 0x0000, 0x0010, { 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71 } };

    // フォーマットを厳密に判定し、誤った型キャストによる波形破壊（ノイズ）を防ぐ
    bool isFloat = false;
    bool is16Bit = false;
    bool is24Bit = false;
    bool is32Bit = false;

    if (m_pwfx->wFormatTag == WAVE_FORMAT_IEEE_FLOAT) {
        isFloat = true;
    } else if (m_pwfx->wFormatTag == WAVE_FORMAT_EXTENSIBLE) {
        WAVEFORMATEXTENSIBLE* pEx = reinterpret_cast<WAVEFORMATEXTENSIBLE*>(m_pwfx);
        if (memcmp(&pEx->SubFormat, &GUID_SUBTYPE_IEEE_FLOAT, sizeof(GUID)) == 0) {
            isFloat = true;
        } else if (memcmp(&pEx->SubFormat, &GUID_SUBTYPE_PCM, sizeof(GUID)) == 0) {
            if (m_pwfx->wBitsPerSample == 16) is16Bit = true;
            else if (m_pwfx->wBitsPerSample == 24) is24Bit = true;
            else if (m_pwfx->wBitsPerSample == 32) is32Bit = true;
        }
    } else if (m_pwfx->wFormatTag == WAVE_FORMAT_PCM) {
        if (m_pwfx->wBitsPerSample == 16) is16Bit = true;
        else if (m_pwfx->wBitsPerSample == 24) is24Bit = true;
        else if (m_pwfx->wBitsPerSample == 32) is32Bit = true;
    }

    if (m_logger) {
        if (isFloat) m_logger("Detected Format: Float");
        else if (is16Bit) m_logger("Detected Format: 16bit PCM");
        else if (is24Bit) m_logger("Detected Format: 24bit PCM");
        else if (is32Bit) m_logger("Detected Format: 32bit PCM");
        else m_logger("Detected Format: UNKNOWN");
    }

    std::vector<float> floatBuffer;
    auto lastRealPacketTime = std::chrono::steady_clock::now();
    auto lastSilenceEmitTime = lastRealPacketTime;

    while (m_isRunning) {
        Sleep(3);

        HRESULT hr = m_captureClient->GetNextPacketSize(&packetLength);
        if (FAILED(hr)) {
            packetLength = 0;
        }

        // Silence Keep-Aliveによる常時供給を基本とし、デバイス切替等での欠落時のみ正確なフレーム数で補填
        if (packetLength == 0 && m_type == CaptureType::SystemLoopback) {
            auto now = std::chrono::steady_clock::now();
            double idleSec = std::chrono::duration<double>(now - lastRealPacketTime).count();
            if (idleSec >= 0.020) {
                uint32_t silentFrames = static_cast<uint32_t>(idleSec * m_sampleRate);
                if (silentFrames > 0 && m_callback) {
                    floatBuffer.assign(silentFrames * m_channels, 0.0f);
                    m_callback(floatBuffer.data(), silentFrames, m_channels, m_sampleRate);
                }
                lastRealPacketTime = now;
            }
        }

        while (packetLength != 0) {
            hr = m_captureClient->GetBuffer(&pData, &numFramesAvailable, &flags, NULL, NULL);
            if (FAILED(hr)) break;

            if (m_callback && numFramesAvailable > 0) {
                uint32_t totalSamples = numFramesAvailable * m_channels;
                floatBuffer.assign(totalSamples, 0.0f);

                if (!(flags & AUDCLNT_BUFFERFLAGS_SILENT) && pData) {
                    if (isFloat) {
                        memcpy(floatBuffer.data(), pData, totalSamples * sizeof(float));
                    } else if (is16Bit) {
                        int16_t* p16 = reinterpret_cast<int16_t*>(pData);
                        for (uint32_t i = 0; i < totalSamples; ++i) {
                            floatBuffer[i] = p16[i] / 32768.0f;
                        }
                    } else if (is24Bit) {
                        uint8_t* p24 = reinterpret_cast<uint8_t*>(pData);
                        for (uint32_t i = 0; i < totalSamples; ++i) {
                            // 24bit (リトルエンディアン) の正しい符号拡張と正規化
                            int32_t val = (static_cast<int32_t>(static_cast<int8_t>(p24[i * 3 + 2])) << 16) |
                                          (static_cast<int32_t>(p24[i * 3 + 1]) << 8) |
                                          static_cast<int32_t>(p24[i * 3]);
                            floatBuffer[i] = val / 8388608.0f;
                        }
                    } else if (is32Bit) {
                        int32_t* p32 = reinterpret_cast<int32_t*>(pData);
                        for (uint32_t i = 0; i < totalSamples; ++i) {
                            floatBuffer[i] = p32[i] / 2147483648.0f;
                        }
                    }
                }
                m_callback(floatBuffer.data(), numFramesAvailable, m_channels, m_sampleRate);
            }

            hr = m_captureClient->ReleaseBuffer(numFramesAvailable);
            hr = m_captureClient->GetNextPacketSize(&packetLength);
            lastRealPacketTime = std::chrono::steady_clock::now();
            lastSilenceEmitTime = lastRealPacketTime;
        }
    }
    timeEndPeriod(1);
    CoUninitialize();
}
