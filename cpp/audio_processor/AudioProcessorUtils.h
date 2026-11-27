#pragma once

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <string>

namespace audio_proc_detail {

// 完全デジタル無音・極小ディザと有効マイク信号を切り分ける物理下限(-100dBFS)
constexpr float kMinPhysicalRms = 1.0e-5f;
constexpr float kPreGainMax = 2.5f;
constexpr float kEarlyPreGainMax = 2.0f;
constexpr float kMaxAmplifiedNoiseRms = 0.005f;
constexpr float kTargetResidualNoiseRms = 4.5e-4f;
constexpr float kPiF = 3.14159265358979323846f;

inline float time_const_alpha(float dt_sec, float tau_sec) {
    if (tau_sec <= 1.0e-6f) return 1.0f;
    return 1.0f - std::exp(-dt_sec / tau_sec);
}

inline std::string extract_lowercase_filename(const std::string& path) {
    const size_t slash = path.find_last_of("/\\");
    std::string name = (slash == std::string::npos) ? path : path.substr(slash + 1);
    std::transform(name.begin(), name.end(), name.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return name;
}

template <size_t N>
inline float compute_robust_noise_floor(const std::array<float, N>& samples, int count) {
    const int n = (std::min)(count, static_cast<int>(N));
    if (n <= 0) return kMinPhysicalRms;
    std::array<float, N> sorted = samples;
    std::sort(sorted.begin(), sorted.begin() + n);
    float robust = sorted[0];
    if (n >= 4) {
        robust = 0.5f * (sorted[1] + sorted[2]);
    } else if (n == 3) {
        robust = sorted[1];
    }
    return (std::max)(robust, kMinPhysicalRms);
}

inline float sanitize_soft_clip(float x) {
    if (!std::isfinite(x)) return 0.0f;
    const float ax = std::fabs(x);
    if (ax <= 0.9f) return x;
    const float shaped = 0.9f + 0.1f * std::tanh((ax - 0.9f) / 0.1f);
    return (x >= 0.0f) ? shaped : -shaped;
}

// FTZ環境での完全無音連続入力によるlibDF内部NaN発散を防ぐ極小ディザ(-120dBFS)
inline float next_micro_dither(uint32_t& rng_state) {
    rng_state = rng_state * 1664525u + 1013904223u;
    const float unit = static_cast<float>(static_cast<int32_t>(rng_state)) * (1.0f / 2147483648.0f);
    return unit * 1.0e-6f;
}

} // namespace audio_proc_detail
