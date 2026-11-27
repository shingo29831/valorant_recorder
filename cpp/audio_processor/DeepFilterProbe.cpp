#include "DeepFilterProbe.h"
#include "AudioProcessorUtils.h"
#include "deep_filter.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <unordered_map>
#include <vector>

namespace audio_proc_detail {

namespace {
struct DelayCacheEntry {
    int frame_size = 480;
    int delay_samples = 1440;
};
} // namespace

DelayProbeResult measure_df_delay_samples(const std::string& model_path, int sample_rate, int max_delay_frames) {
    static std::mutex cache_mtx;
    static std::unordered_map<std::string, DelayCacheEntry> delay_cache;

    const std::string cache_key = model_path + "@" + std::to_string(sample_rate);
    {
        std::lock_guard<std::mutex> lock(cache_mtx);
        const auto it = delay_cache.find(cache_key);
        if (it != delay_cache.end()) {
            return DelayProbeResult{it->second.frame_size, it->second.delay_samples, false};
        }
    }

    DFState* probe_state = df_create(model_path.c_str(), 35.0f, "warn");
    if (!probe_state) {
        return DelayProbeResult{0, 0, true};
    }

    df_set_post_filter_beta(probe_state, 0.0f);
    const uintptr_t model_frame_len = df_get_frame_length(probe_state);
    const int fallback_hop = (sample_rate > 0) ? (sample_rate / 100) : 480;
    const int p_hop = (model_frame_len > 0 && model_frame_len <= 4800)
                          ? static_cast<int>(model_frame_len)
                          : fallback_hop;

    // 標準DeepFilterNet3はSTFT(1)+lookahead(2)=3フレーム遅延、_llモデルは1フレーム遅延
    const std::string fname = extract_lowercase_filename(model_path);
    const int fallback_frames = std::clamp((fname.find("_ll") != std::string::npos) ? 1 : 3, 0, max_delay_frames);
    int measured_samples = fallback_frames * p_hop;

    if (p_hop > 0) {
        constexpr int kWarmupFrames = 6;
        constexpr int kBurstFrames = 3;
        constexpr int kSubPerFrame = 16;
        const int total_frames = kBurstFrames + max_delay_frames + 2;
        const float sr_f = static_cast<float>((sample_rate > 0) ? sample_rate : 48000);
        const float two_pi = 2.0f * kPiF;

        std::vector<float> in_buf(p_hop, 0.0f);
        std::vector<float> out_buf(p_hop, 0.0f);

        for (int w = 0; w < kWarmupFrames; ++w) {
            for (int i = 0; i < p_hop; ++i) {
                in_buf[i] = 1.0e-6f * std::sin(two_pi * 1000.0f * static_cast<float>(i) / sr_f);
            }
            std::fill(out_buf.begin(), out_buf.end(), 0.0f);
            df_process_frame(probe_state, in_buf.data(), out_buf.data());
        }

        const int burst_samples = kBurstFrames * p_hop;
        const int total_subs = total_frames * kSubPerFrame;
        const int burst_subs = kBurstFrames * kSubPerFrame;
        std::vector<float> in_env(burst_subs, 0.0f);
        std::vector<float> out_env(total_subs, 0.0f);
        float best_out_rms = 0.0f;

        for (int f = 0; f < total_frames; ++f) {
            if (f < kBurstFrames) {
                for (int i = 0; i < p_hop; ++i) {
                    const int g_idx = f * p_hop + i;
                    const float hann = 0.5f - 0.5f * std::cos(
                        two_pi * static_cast<float>(g_idx) / static_cast<float>((std::max)(burst_samples - 1, 1)));
                    const float tone = 0.50f * std::sin(two_pi * 220.0f * static_cast<float>(g_idx) / sr_f)
                                     + 0.35f * std::sin(two_pi * 440.0f * static_cast<float>(g_idx) / sr_f)
                                     + 0.15f * std::sin(two_pi * 880.0f * static_cast<float>(g_idx) / sr_f);
                    in_buf[i] = 0.25f * hann * tone;
                }
                for (int s = 0; s < kSubPerFrame; ++s) {
                    const int start = (s * p_hop) / kSubPerFrame;
                    const int end = ((s + 1) * p_hop) / kSubPerFrame;
                    float sq = 0.0f;
                    for (int i = start; i < end; ++i) sq += in_buf[i] * in_buf[i];
                    in_env[f * kSubPerFrame + s] = std::sqrt(sq / static_cast<float>((std::max)(end - start, 1)));
                }
            } else {
                for (int i = 0; i < p_hop; ++i) {
                    in_buf[i] = 1.0e-6f * std::sin(two_pi * 1000.0f * static_cast<float>(i) / sr_f);
                }
            }

            std::fill(out_buf.begin(), out_buf.end(), 0.0f);
            df_process_frame(probe_state, in_buf.data(), out_buf.data());

            float frame_sq = 0.0f;
            for (int i = 0; i < p_hop; ++i) {
                if (std::isfinite(out_buf[i])) {
                    frame_sq += out_buf[i] * out_buf[i];
                } else {
                    out_buf[i] = 0.0f;
                }
            }
            best_out_rms = (std::max)(best_out_rms, std::sqrt(frame_sq / static_cast<float>(p_hop)));

            for (int s = 0; s < kSubPerFrame; ++s) {
                const int start = (s * p_hop) / kSubPerFrame;
                const int end = ((s + 1) * p_hop) / kSubPerFrame;
                float sq = 0.0f;
                for (int i = start; i < end; ++i) sq += out_buf[i] * out_buf[i];
                out_env[f * kSubPerFrame + s] = std::sqrt(sq / static_cast<float>((std::max)(end - start, 1)));
            }
        }

        float in_e = 0.0f;
        for (float v : in_env) in_e += v * v;

        const int max_lag_sub = (std::min)(max_delay_frames * kSubPerFrame + kSubPerFrame / 2, total_subs - burst_subs);
        float best_corr = -1.0f;
        int best_lag_sub = -1;
        for (int lag = 0; lag <= max_lag_sub; ++lag) {
            float num = 0.0f;
            float out_e = 0.0f;
            for (int k = 0; k < burst_subs; ++k) {
                const float iv = in_env[k];
                const float ov = out_env[k + lag];
                num += iv * ov;
                out_e += ov * ov;
            }
            const float corr = num / (std::sqrt(in_e * out_e) + 1.0e-12f);
            if (corr > best_corr) {
                best_corr = corr;
                best_lag_sub = lag;
            }
        }

        if (best_lag_sub >= 0 && best_out_rms >= 0.02f && best_corr >= 0.70f) {
            const int raw_samples = static_cast<int>(std::lround(
                static_cast<double>(best_lag_sub) * static_cast<double>(p_hop) / static_cast<double>(kSubPerFrame)));
            measured_samples = std::clamp(raw_samples, 0, max_delay_frames * p_hop);
        }
    }

    while (char* msg = df_next_log_msg(probe_state)) {
        df_free_log_msg(msg);
    }
    df_free(probe_state);

    {
        std::lock_guard<std::mutex> lock(cache_mtx);
        delay_cache[cache_key] = DelayCacheEntry{p_hop, measured_samples};
    }
    return DelayProbeResult{p_hop, measured_samples, false};
}

} // namespace audio_proc_detail
