#include "WasapiCapture.h"
#include <iostream>
#include <comdef.h>
#include <functiondiscoverykeys_devpkey.h>

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

    if (m_type == CaptureType::SystemLoopback) {
        hr = pEnumerator->GetDefaultAudioEndpoint(eRender, eConsole, &m_device);
    } else {
        if (m_deviceName.empty() || m_deviceName == "Default") {
            hr = pEnumerator->GetDefaultAudioEndpoint(eCapture, eConsole, &m_device);
        } else {
            // デバイス名（UTF-8）による検索
            IMMDeviceCollection* pCollection = nullptr;
            hr = pEnumerator->EnumAudioEndpoints(eCapture, DEVICE_STATE_ACTIVE, &pCollection);
            if (SUCCEEDED(hr)) {
                UINT count = 0;
                pCollection->GetCount(&count);
                for (UINT i = 0; i < count; i++) {
                    IMMDevice* pEndpoint = nullptr;
                    if (SUCCEEDED(pCollection->Item(i, &pEndpoint))) {
                        IPropertyStore* pProps = nullptr;
                        if (SUCCEEDED(pEndpoint->OpenPropertyStore(STGM_READ, &pProps))) {
                            PROPVARIANT varName;
                            PropVariantInit(&varName);
                            if (SUCCEEDED(pProps->GetValue(PKEY_Device_FriendlyName, &varName))) {
                                std::string utf8Name = WideToUTF8(varName.pwszVal);
                                // Python側(soundcard)のデバイス名と部分一致するか確認
                                if (utf8Name.find(m_deviceName) != std::string::npos || m_deviceName.find(utf8Name) != std::string::npos) {
                                    m_device = pEndpoint;
                                    m_device->AddRef();
                                    PropVariantClear(&varName);
                                    SAFE_RELEASE(pProps);
                                    SAFE_RELEASE(pEndpoint);
                                    break;
                                }
                            }
                            PropVariantClear(&varName);
                            SAFE_RELEASE(pProps);
                        }
                        SAFE_RELEASE(pEndpoint);
                    }
                }
                SAFE_RELEASE(pCollection);
            }
            if (!m_device) {
                SetError("Microphone device not found: " + m_deviceName);
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
    REFERENCE_TIME hnsRequestedDuration = 1000000; // 100ms buffer

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
}

void WasapiCapture::CaptureThreadFunc() {
    CoInitializeEx(NULL, COINIT_MULTITHREADED);

    UINT32 packetLength = 0;
    BYTE* pData = nullptr;
    UINT32 numFramesAvailable = 0;
    DWORD flags = 0;

    bool isFloat = (m_pwfx->wFormatTag == WAVE_FORMAT_IEEE_FLOAT) || 
                   (m_pwfx->wFormatTag == WAVE_FORMAT_EXTENSIBLE && 
                    reinterpret_cast<WAVEFORMATEXTENSIBLE*>(m_pwfx)->SubFormat == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT);

    std::vector<float> floatBuffer;

    while (m_isRunning) {
        Sleep(10); // Polling interval

        HRESULT hr = m_captureClient->GetNextPacketSize(&packetLength);
        if (FAILED(hr)) continue;

        while (packetLength != 0) {
            hr = m_captureClient->GetBuffer(&pData, &numFramesAvailable, &flags, NULL, NULL);
            if (FAILED(hr)) break;

            if (m_callback && numFramesAvailable > 0) {
                uint32_t totalSamples = numFramesAvailable * m_channels;
                floatBuffer.assign(totalSamples, 0.0f);

                if (!(flags & AUDCLNT_BUFFERFLAGS_SILENT) && pData) {
                    if (isFloat) {
                        memcpy(floatBuffer.data(), pData, totalSamples * sizeof(float));
                    } else if (m_pwfx->wBitsPerSample == 16) {
                        int16_t* p16 = reinterpret_cast<int16_t*>(pData);
                        for (uint32_t i = 0; i < totalSamples; ++i) {
                            floatBuffer[i] = p16[i] / 32768.0f;
                        }
                    }
                }
                m_callback(floatBuffer.data(), numFramesAvailable, m_channels, m_sampleRate);
            }

            hr = m_captureClient->ReleaseBuffer(numFramesAvailable);
            hr = m_captureClient->GetNextPacketSize(&packetLength);
        }
    }
    CoUninitialize();
}
