#pragma once

#include <speex/speex_resampler.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <mutex>
#include <vector>

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

public:
    AudioSyncQueue(size_t channels = 2, size_t maxDelayMs = 2000)
        : m_channels(channels),
          m_maxBufferSamples((48000 * channels * maxDelayMs) / 1000) {}

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
    }

    bool PopChunk(size_t samples, std::vector<float>& out, size_t maxAllowedBuffer) {
        std::lock_guard<std::mutex> lock(m_mtx);

        // クロックドリフト蓄積時は適正クッション量(50ms)まで戻し遅延肥大化を防止
        if (m_buffer.size() > maxAllowedBuffer) {
            size_t targetKeep = samples * 5;
            if (m_buffer.size() > targetKeep) {
                size_t excess = m_buffer.size() - targetKeep;
                excess = (excess / m_channels) * m_channels;
                m_buffer.erase(m_buffer.begin(), m_buffer.begin() + excess);
            }
        }

        if (m_buffer.size() < samples) {
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
        float smoothingFactor = open ? 0.35f : 0.03f;

        for (size_t f = 0; f < frames; ++f) {
            m_currentGain += (targetGain - m_currentGain) * smoothingFactor;
            for (size_t c = 0; c < channels; ++c) {
                buffer[f * channels + c] *= m_currentGain;
            }
        }
    }
};

inline void DownmixToStereo(
    const std::vector<float>& in,
    uint32_t frames,
    uint32_t channels,
    float gain,
    std::vector<float>& stereoOut
) {
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
