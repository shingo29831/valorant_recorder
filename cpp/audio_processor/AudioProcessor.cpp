#include "AudioProcessor.h"
#include <cctype>
#include <cstring>
#include <cmath>
#include <condition_variable>
#include <iostream>
#include <algorithm>
#include <thread>
#include <unordered_map>
#include <speex/speex_preprocess.h>

// DeepFilterNet C API
#include "deep_filter.h"

namespace {
// 完全デジタル無音・極小ディザと有効マイク信号を切り分ける物理下限(-100dBFS)
constexpr float kMinPhysicalRms = 1.0e-5f;
constexpr float kPreGainMax = 2.5f;
constexpr float kEarlyPreGainMax = 2.0f;
constexpr float kMaxAmplifiedNoiseRms = 0.005f;
constexpr float kTargetResidualNoiseRms = 4.5e-4f;
constexpr float kPiF = 3.14159265358979323846f;

struct DelayCacheEntry {
    int frame_size = 480;
    int delay_samples = 1440;
};

struct DelayProbeResult {
    int frame_size = 480;
    int delay_samples = 1440;
    bool create_failed = false;
};

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

// 本体DFState生成前にプローブ専用DFStateを1回だけ生成・計測・即破棄し、状態汚染と同時二重メモリを防止する
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
} // namespace

struct AudioProcessor::BgRecreateContext {
    std::mutex mtx;
    std::condition_variable cv;
    bool alive = true;
    AudioProcessor* owner = nullptr;
    int sample_rate = 48000;
    bool worker_started = false;
    bool has_pending = false;
    std::string pending_path;
    uint64_t pending_seq = 0;
    std::vector<DFState*> garbage_states;
    std::thread worker;
};

AudioProcessor::AudioProcessor(int sample_rate, int channels, const char* model_path)
    : sample_rate_(sample_rate > 0 ? sample_rate : 48000),
      preprocess_type_(1),
      denoise_type_(0),
      channels_(channels > 0 ? channels : 1),
      df_frame_size_(sample_rate_ / 100),
      df_delay_samples_((sample_rate_ / 100) * 3) {

    bg_ctx_ = std::make_shared<BgRecreateContext>();
    bg_ctx_->owner = this;
    bg_ctx_->sample_rate = sample_rate_;

    int frame_size = sample_rate_ / 100;

    // 80Hz 2次Butterworthハイパスフィルタ係数の算出
    const double fc = 80.0;
    const double pi = 3.14159265358979323846;
    const double w0 = 2.0 * pi * fc / static_cast<double>(sample_rate_);
    const double cos_w0 = std::cos(w0);
    const double alpha = std::sin(w0) / std::sqrt(2.0);
    const double a0 = 1.0 + alpha;
    hpf_b0_ = static_cast<float>(((1.0 + cos_w0) * 0.5) / a0);
    hpf_b1_ = static_cast<float>(-(1.0 + cos_w0) / a0);
    hpf_b2_ = static_cast<float>(((1.0 + cos_w0) * 0.5) / a0);
    hpf_a1_ = static_cast<float>((-2.0 * cos_w0) / a0);
    hpf_a2_ = static_cast<float>((1.0 - alpha) / a0);

    // サイドチェイン専用: 75Hz 4次HPF+1000Hz LPFで低い男性声基音(85-110Hz)を保ちつつ電源ハム・折り返しを遮断
    df_ds_step_ = (sample_rate_ + 11999) / 12000;
    const double sc_fc_hpf = (std::min)(75.0, 0.25 * static_cast<double>(sample_rate_));
    const double sc_w0_h = 2.0 * pi * sc_fc_hpf / static_cast<double>(sample_rate_);
    const double sc_cos_h = std::cos(sc_w0_h);
    const double sc_alp_h = std::sin(sc_w0_h) / std::sqrt(2.0);
    const double sc_a0_h = 1.0 + sc_alp_h;
    sc_hpf_b0_ = static_cast<float>(((1.0 + sc_cos_h) * 0.5) / sc_a0_h);
    sc_hpf_b1_ = static_cast<float>(-(1.0 + sc_cos_h) / sc_a0_h);
    sc_hpf_b2_ = static_cast<float>(((1.0 + sc_cos_h) * 0.5) / sc_a0_h);
    sc_hpf_a1_ = static_cast<float>((-2.0 * sc_cos_h) / sc_a0_h);
    sc_hpf_a2_ = static_cast<float>((1.0 - sc_alp_h) / sc_a0_h);

    const double sc_fc_lpf = (std::min)(1000.0, 0.40 * static_cast<double>(sample_rate_));
    const double sc_w0_l = 2.0 * pi * sc_fc_lpf / static_cast<double>(sample_rate_);
    const double sc_cos_l = std::cos(sc_w0_l);
    const double sc_alp_l = std::sin(sc_w0_l) / std::sqrt(2.0);
    const double sc_a0_l = 1.0 + sc_alp_l;
    sc_lpf_b0_ = static_cast<float>(((1.0 - sc_cos_l) * 0.5) / sc_a0_l);
    sc_lpf_b1_ = static_cast<float>((1.0 - sc_cos_l) / sc_a0_l);
    sc_lpf_b2_ = static_cast<float>(((1.0 - sc_cos_l) * 0.5) / sc_a0_l);
    sc_lpf_a1_ = static_cast<float>((-2.0 * sc_cos_l) / sc_a0_l);
    sc_lpf_a2_ = static_cast<float>((1.0 - sc_alp_l) / sc_a0_l);

    // マイク音声処理は内部1ch(モノラル)に集約しCPU・メモリ負荷を半減させる
    SpeexPreprocessState* pre_st = speex_preprocess_state_init(frame_size, sample_rate_);
    if (pre_st) {
        int denoise = 0;
        int noise_suppress = -25;
        speex_preprocess_ctl(pre_st, SPEEX_PREPROCESS_SET_DENOISE, &denoise);
        speex_preprocess_ctl(pre_st, SPEEX_PREPROCESS_SET_NOISE_SUPPRESS, &noise_suppress);
        speex_pre_states_.push_back(pre_st);
    }

    if (model_path != nullptr && std::strlen(model_path) > 0) {
        load_deepfilter(model_path);
    }
}

float AudioProcessor::compute_voiced_periodicity() const {
    const int eff_sr = (std::max)(4000, sample_rate_ / (std::max)(1, df_ds_step_));
    const int win_len = (std::max)(60, static_cast<int>(0.015f * static_cast<float>(eff_sr)));
    const int min_lag = (std::max)(8, static_cast<int>(static_cast<float>(eff_sr) / 360.0f));
    const int max_lag = (std::min)(kVoicedHistSize - win_len - 1, static_cast<int>(static_cast<float>(eff_sr) / 75.0f));
    if (max_lag <= min_lag || df_voiced_count_ < (win_len + max_lag)) {
        return 0.0f;
    }

    auto read_recent = [this](int samples_back) -> float {
        const int idx = (df_voiced_head_ + kVoicedHistSize - 1 - samples_back) % kVoicedHistSize;
        return df_voiced_hist_[idx];
    };

    float sum_a = 0.0f;
    for (int i = 0; i < win_len; ++i) {
        sum_a += read_recent(i);
    }
    const float mean_a = sum_a / static_cast<float>(win_len);

    float e0 = 0.0f;
    for (int i = 0; i < win_len; ++i) {
        const float da = read_recent(i) - mean_a;
        e0 += da * da;
    }
    // 非発話時の定常ハム・ファン成分以上のエネルギーを要求し誤ロックを防止
    const float mean_e0 = e0 / static_cast<float>(win_len);
    const float min_voiced_energy = (std::max)(1.0e-8f, df_sc_noise_energy_ * 3.5f);
    if (mean_e0 < min_voiced_energy) {
        return 0.0f;
    }

    float best_r = 0.0f;
    for (int lag = min_lag; lag <= max_lag; ++lag) {
        float sum_b = 0.0f;
        for (int i = 0; i < win_len; ++i) {
            sum_b += read_recent(i + lag);
        }
        const float mean_b = sum_b / static_cast<float>(win_len);

        float num = 0.0f;
        float e1 = 0.0f;
        for (int i = 0; i < win_len; ++i) {
            const float da = read_recent(i) - mean_a;
            const float db = read_recent(i + lag) - mean_b;
            num += da * db;
            e1 += db * db;
        }
        const float denom = std::sqrt(e0 * e1) + 1.0e-10f;
        best_r = (std::max)(best_r, num / denom);
    }
    return best_r;
}

float AudioProcessor::effective_noise_floor() const {
    if (df_noise_initialized_) {
        return (std::max)(df_noise_floor_, df_fast_noise_floor_);
    }
    if (df_noise_init_count_ > 0) {
        return compute_robust_noise_floor(df_noise_init_samples_, df_noise_init_count_);
    }
    if (df_init_min_rms_ > 0.0f) {
        if (df_init_speech_streak_ >= 2 && df_init_voiced_confirmed_) {
            return (std::max)(kMinPhysicalRms, df_init_min_rms_ * 0.1f);
        }
        // 未確定かつ有声音未確認時は大音量定常ノイズを過小評価して誤発話連鎖を起こさない
        return (std::max)(kMinPhysicalRms, df_init_min_rms_ * 0.8f);
    }
    return kDefaultNoiseFloorRms;
}

DFState* AudioProcessor::create_deepfilter_instance(const std::string& model_path, int sample_rate,
                                                   int& out_frame_size, int& out_delay_samples) {
    if (model_path.empty()) {
        return nullptr;
    }
    try {
        const DelayProbeResult probe = measure_df_delay_samples(model_path, sample_rate, kMaxDfDelayFrames);
        if (probe.create_failed) {
            std::cerr << "[AudioProcessor] Error: df_create returned nullptr during probe with path: "
                      << model_path << std::endl;
            return nullptr;
        }

        DFState* state = df_create(model_path.c_str(), kDefaultAttenLimDb, "warn");
        if (!state) {
            std::cerr << "[AudioProcessor] Error: df_create returned nullptr with path: " << model_path << std::endl;
            return nullptr;
        }
        // ポストフィルタ無効化(公式デフォルト)で小声や子音の過剰削りを防止
        df_set_post_filter_beta(state, 0.0f);
        const uintptr_t model_frame_len = df_get_frame_length(state);
        out_frame_size = (model_frame_len > 0 && model_frame_len <= 4800)
                             ? static_cast<int>(model_frame_len)
                             : probe.frame_size;
        out_delay_samples = std::clamp(probe.delay_samples, 0, kMaxDfDelayFrames * out_frame_size);
        while (char* msg = df_next_log_msg(state)) {
            df_free_log_msg(msg);
        }
        return state;
    } catch (const std::exception& e) {
        std::cerr << "[AudioProcessor] Exception in DeepFilterNet init: " << e.what() << std::endl;
    } catch (...) {
        std::cerr << "[AudioProcessor] Unknown exception in DeepFilterNet init." << std::endl;
    }
    return nullptr;
}

void AudioProcessor::destroy_deepfilter_state_unlocked() {
    df_loaded_.store(false, std::memory_order_release);
    for (DFState* state : df_states_) {
        if (state) {
            df_free(state);
        }
    }
    df_states_.clear();
    df_state_dirty_ = false;
}

void AudioProcessor::reset_df_buffers_unlocked() {
    df_in_ring_.clear();
    df_out_ring_.clear();
    df_gain_delay_ring_.clear();
    df_lookahead_primed_ = false;
    df_non_aligned_primed_ = false;

    df_gain_delay_ring_.push_constant(1.0f, static_cast<size_t>((std::max)(0, df_delay_samples_)));

    df_voiced_head_ = 0;
    df_voiced_count_ = 0;
    df_ds_accum_ = 0;
    sc_hpf1_x1_ = sc_hpf1_x2_ = sc_hpf1_y1_ = sc_hpf1_y2_ = 0.0f;
    sc_hpf2_x1_ = sc_hpf2_x2_ = sc_hpf2_y1_ = sc_hpf2_y2_ = 0.0f;
    sc_lpf_x1_ = sc_lpf_x2_ = sc_lpf_y1_ = sc_lpf_y2_ = 0.0f;
    df_sc_noise_energy_ = 0.0f;

    df_total_frames_ = 0;
    df_noise_init_count_ = 0;
    df_noise_init_wait_frames_ = 0;
    df_noise_init_low_streak_ = 0;
    df_init_speech_streak_ = 0;
    df_init_voiced_confirmed_ = false;
    df_init_min_rms_ = 0.0f;

    df_pregain_speech_active_ = false;
    df_speech_peak_env_ = kPreGainTargetPeak;
    df_pre_gain_ = 1.0f;
    df_noise_floor_ = kDefaultNoiseFloorRms;
    df_fast_noise_floor_ = kDefaultNoiseFloorRms;
    df_noise_initialized_ = false;
    df_smooth_rms_ = 0.0f;
    df_min_block_rms_ = 1.0e9f;
    df_prev_block_min_rms_ = 1.0e9f;
    df_min_block_elapsed_sec_ = 0.0f;
    df_low_lsnr_streak_frames_ = 0;
    df_non_speech_low_streak_ = 0;

    df_onset_confirm_frames_ = 0;
    df_tail_fade_streak_ = 0;
    df_speech_hold_sec_ = 0.0f;
    df_voiced_hold_active_ = false;
    df_non_voiced_low_lsnr_sec_ = 0.0f;
    df_voiced_low_lsnr_sec_ = 0.0f;
    df_speech_ref_rms_ = 0.0f;
    df_in_speech_env_ = 0.0f;
    df_fade_protect_level_ = 0.0f;
    df_current_atten_lim_db_ = kDefaultAttenLimDb;
    // 再有効化(OFF->ON)時にDFStateへ基準atten_limを強制再適用させるため無効値を設定
    df_last_applied_atten_lim_db_ = -1.0f;
}

bool AudioProcessor::load_deepfilter(const char* model_path) {
    if (!model_path || std::strlen(model_path) == 0) {
        return false;
    }
    std::string normalized_path = model_path;
    std::replace(normalized_path.begin(), normalized_path.end(), '\\', '/');

    int new_frame_size = sample_rate_ / 100;
    int new_delay_samples = new_frame_size * 3;
    DFState* new_state = create_deepfilter_instance(normalized_path, sample_rate_, new_frame_size, new_delay_samples);

    {
        std::lock_guard<std::mutex> bg_lock(bg_ctx_->mtx);
        bg_ctx_->has_pending = false;
    }

    std::vector<DFState*> old_states;
    bool loaded = false;
    {
        std::lock_guard<std::mutex> lock(mtx_);
        ++df_load_seq_;
        df_loaded_.store(false, std::memory_order_release);
        old_states.swap(df_states_);
        df_model_path_ = normalized_path;
        if (new_state) {
            df_frame_size_ = new_frame_size;
            df_delay_samples_ = new_delay_samples;
            df_states_.push_back(new_state);
            loaded = true;
        }
        df_state_dirty_ = false;
        df_loaded_.store(loaded, std::memory_order_release);
        reset_df_buffers_unlocked();
        const int cur_denoise = denoise_type_.load(std::memory_order_acquire);
        const bool use_speex_denoise = (cur_denoise == 2) || (cur_denoise == 1 && !is_deepfilter_loaded_unlocked());
        enable_speex_denoise_unlocked(use_speex_denoise);
    }

    for (DFState* st : old_states) {
        if (st) {
            df_free(st);
        }
    }
    return loaded;
}

void AudioProcessor::enable_speex_denoise_unlocked(bool enable) {
    if (enable && speex_pre_states_.empty()) {
        int frame_size = sample_rate_ / 100;
        SpeexPreprocessState* pre_st = speex_preprocess_state_init(frame_size, sample_rate_);
        if (pre_st) {
            speex_pre_states_.push_back(pre_st);
        }
    }
    if (!speex_pre_states_.empty() && speex_pre_states_[0]) {
        SpeexPreprocessState* pre_st = static_cast<SpeexPreprocessState*>(speex_pre_states_[0]);
        int denoise = enable ? 1 : 0;
        int noise_suppress = -25;
        speex_preprocess_ctl(pre_st, SPEEX_PREPROCESS_SET_DENOISE, &denoise);
        speex_preprocess_ctl(pre_st, SPEEX_PREPROCESS_SET_NOISE_SUPPRESS, &noise_suppress);
    }
}

bool AudioProcessor::is_deepfilter_loaded_unlocked() const {
    return (!df_states_.empty() && df_states_[0] != nullptr);
}

bool AudioProcessor::is_deepfilter_active_unlocked() const {
    return (denoise_type_.load(std::memory_order_acquire) == 1 && is_deepfilter_loaded_unlocked());
}

bool AudioProcessor::is_deepfilter_loaded() const {
    return df_loaded_.load(std::memory_order_acquire);
}

bool AudioProcessor::is_deepfilter_active() const {
    return (denoise_type_.load(std::memory_order_acquire) == 1) && df_loaded_.load(std::memory_order_acquire);
}

AudioProcessor::~AudioProcessor() {
    // 終了時のdf_create/df_free競合クラッシュを防ぐため背景ワーカースレッドを確実にjoinしてから破棄
    std::thread worker_to_join;
    std::vector<DFState*> leftover_garbage;
    if (bg_ctx_) {
        std::lock_guard<std::mutex> bg_lock(bg_ctx_->mtx);
        bg_ctx_->alive = false;
        bg_ctx_->owner = nullptr;
        bg_ctx_->has_pending = false;
        leftover_garbage.swap(bg_ctx_->garbage_states);
        worker_to_join = std::move(bg_ctx_->worker);
        bg_ctx_->cv.notify_all();
    }
    if (worker_to_join.joinable()) {
        worker_to_join.join();
    }
    for (DFState* st : leftover_garbage) {
        if (st) {
            df_free(st);
        }
    }

    std::lock_guard<std::mutex> lock(mtx_);
    destroy_deepfilter_state_unlocked();
    reset_df_buffers_unlocked();
    for (void* st : speex_pre_states_) {
        if (st) {
            speex_preprocess_state_destroy(static_cast<SpeexPreprocessState*>(st));
        }
    }
    speex_pre_states_.clear();
    for (void* st : speex_post_states_) {
        if (st) {
            speex_preprocess_state_destroy(static_cast<SpeexPreprocessState*>(st));
        }
    }
    speex_post_states_.clear();
}

void AudioProcessor::set_preprocess_type(int type) {
    preprocess_type_.store(type, std::memory_order_release);
}

void AudioProcessor::set_denoise_type(int type) {
    bool need_recreate = false;
    std::string target_path;
    uint64_t expected_seq = 0;
    std::vector<DFState*> states_to_free;
    {
        std::lock_guard<std::mutex> lock(mtx_);
        const int prev_type = denoise_type_.load(std::memory_order_relaxed);
        if (prev_type != type) {
            if (type == 1 && !df_model_path_.empty() &&
                (df_state_dirty_ || !is_deepfilter_loaded_unlocked())) {
                // UI等の呼び出し元をブロックせずSpeexへ即時切替し、失敗時の再試行も含め背景ワーカーへ委譲
                states_to_free.swap(df_states_);
                df_loaded_.store(false, std::memory_order_release);
                df_state_dirty_ = false;
                reset_df_buffers_unlocked();
                need_recreate = true;
                target_path = df_model_path_;
                expected_seq = ++df_load_seq_;
            } else {
                reset_df_buffers_unlocked();
            }
        }
        denoise_type_.store(type, std::memory_order_release);
        const bool use_speex_denoise = (type == 2) || (type == 1 && !is_deepfilter_loaded_unlocked());
        enable_speex_denoise_unlocked(use_speex_denoise);
    }

    if (!need_recreate) {
        if (type != 1 && bg_ctx_) {
            std::lock_guard<std::mutex> bg_lock(bg_ctx_->mtx);
            bg_ctx_->has_pending = false;
        }
        return;
    }

    {
        std::lock_guard<std::mutex> bg_lock(bg_ctx_->mtx);
        for (DFState* st : states_to_free) {
            if (st) {
                bg_ctx_->garbage_states.push_back(st);
            }
        }
        bg_ctx_->pending_path = target_path;
        bg_ctx_->pending_seq = expected_seq;
        bg_ctx_->has_pending = true;
        if (!bg_ctx_->worker_started) {
            bg_ctx_->worker_started = true;
            std::shared_ptr<BgRecreateContext> ctx = bg_ctx_;
            bg_ctx_->worker = std::thread([ctx]() {
                while (true) {
                    std::string path;
                    uint64_t seq = 0;
                    int sr = 48000;
                    std::vector<DFState*> to_free;
                    {
                        std::unique_lock<std::mutex> bg_lock(ctx->mtx);
                        ctx->cv.wait(bg_lock, [&ctx]() {
                            return !ctx->alive || ctx->has_pending || !ctx->garbage_states.empty();
                        });
                        to_free.swap(ctx->garbage_states);
                        if (!ctx->alive) {
                            bg_lock.unlock();
                            for (DFState* st : to_free) {
                                if (st) df_free(st);
                            }
                            break;
                        }
                        if (ctx->has_pending) {
                            path = ctx->pending_path;
                            seq = ctx->pending_seq;
                            sr = ctx->sample_rate;
                            ctx->has_pending = false;
                        }
                    }
                    for (DFState* st : to_free) {
                        if (st) {
                            df_free(st);
                        }
                    }
                    if (path.empty()) {
                        continue;
                    }

                    int new_frame_size = sr / 100;
                    int new_delay_samples = new_frame_size * 3;
                    DFState* fresh_state = create_deepfilter_instance(path, sr, new_frame_size, new_delay_samples);
                    DFState* old_to_free = nullptr;
                    {
                        std::lock_guard<std::mutex> bg_lock(ctx->mtx);
                        if (ctx->alive && ctx->owner && !ctx->has_pending) {
                            std::lock_guard<std::mutex> proc_lock(ctx->owner->mtx_);
                            if (ctx->owner->denoise_type_.load(std::memory_order_acquire) == 1 &&
                                ctx->owner->df_model_path_ == path &&
                                ctx->owner->df_load_seq_ == seq) {
                                if (fresh_state) {
                                    if (!ctx->owner->df_states_.empty()) {
                                        old_to_free = ctx->owner->df_states_[0];
                                        ctx->owner->df_states_.clear();
                                    }
                                    ctx->owner->df_frame_size_ = new_frame_size;
                                    ctx->owner->df_delay_samples_ = new_delay_samples;
                                    ctx->owner->df_states_.push_back(fresh_state);
                                    fresh_state = nullptr;
                                    ctx->owner->df_state_dirty_ = false;
                                    ctx->owner->df_loaded_.store(true, std::memory_order_release);
                                }
                                ctx->owner->reset_df_buffers_unlocked();
                                const bool use_speex_denoise = !ctx->owner->is_deepfilter_loaded_unlocked();
                                ctx->owner->enable_speex_denoise_unlocked(use_speex_denoise);
                            }
                        }
                    }
                    if (fresh_state) {
                        df_free(fresh_state);
                    }
                    if (old_to_free) {
                        df_free(old_to_free);
                    }
                }
            });
        }
        bg_ctx_->cv.notify_one();
    }
}

void AudioProcessor::process_df_frame(const float* frame_samples, const float* next_frame_samples,
                                      size_t df_hop, float frame_dt_sec, float inv_hop, bool& df_corrupted) {
    float sum_sq_in = 0.0f;
    float in_peak = 0.0f;
    float next_peak = 0.0f;
    float sc_sum_sq = 0.0f;
    int sc_samples = 0;

    for (size_t i = 0; i < df_hop; ++i) {
        const float clean = sanitize_soft_clip(frame_samples[i]);
        df_clean_frame_[i] = clean;
        sum_sq_in += clean * clean;
        in_peak = (std::max)(in_peak, std::fabs(clean));

        const float next_clean = sanitize_soft_clip(next_frame_samples[i]);
        next_peak = (std::max)(next_peak, std::fabs(next_clean));

        // サイドチェイン専用 75Hz 4次HPF + 1000Hz LPF で低い男性声基音を保ちつつハム・折り返しを遮断
        float yh1 = sc_hpf_b0_ * clean + sc_hpf_b1_ * sc_hpf1_x1_ + sc_hpf_b2_ * sc_hpf1_x2_
                  - sc_hpf_a1_ * sc_hpf1_y1_ - sc_hpf_a2_ * sc_hpf1_y2_;
        if (!std::isfinite(yh1)) {
            sc_hpf1_x1_ = sc_hpf1_x2_ = sc_hpf1_y1_ = sc_hpf1_y2_ = 0.0f;
            yh1 = 0.0f;
        } else {
            sc_hpf1_x2_ = sc_hpf1_x1_;
            sc_hpf1_x1_ = clean;
            sc_hpf1_y2_ = sc_hpf1_y1_;
            sc_hpf1_y1_ = yh1;
        }

        float yh2 = sc_hpf_b0_ * yh1 + sc_hpf_b1_ * sc_hpf2_x1_ + sc_hpf_b2_ * sc_hpf2_x2_
                  - sc_hpf_a1_ * sc_hpf2_y1_ - sc_hpf_a2_ * sc_hpf2_y2_;
        if (!std::isfinite(yh2)) {
            sc_hpf2_x1_ = sc_hpf2_x2_ = sc_hpf2_y1_ = sc_hpf2_y2_ = 0.0f;
            yh2 = 0.0f;
        } else {
            sc_hpf2_x2_ = sc_hpf2_x1_;
            sc_hpf2_x1_ = yh1;
            sc_hpf2_y2_ = sc_hpf2_y1_;
            sc_hpf2_y1_ = yh2;
        }

        float yl = sc_lpf_b0_ * yh2 + sc_lpf_b1_ * sc_lpf_x1_ + sc_lpf_b2_ * sc_lpf_x2_
                 - sc_lpf_a1_ * sc_lpf_y1_ - sc_lpf_a2_ * sc_lpf_y2_;
        if (!std::isfinite(yl)) {
            sc_lpf_x1_ = sc_lpf_x2_ = sc_lpf_y1_ = sc_lpf_y2_ = 0.0f;
            yl = 0.0f;
        } else {
            sc_lpf_x2_ = sc_lpf_x1_;
            sc_lpf_x1_ = yh2;
            sc_lpf_y2_ = sc_lpf_y1_;
            sc_lpf_y1_ = yl;
        }

        ++df_ds_accum_;
        if (df_ds_accum_ >= df_ds_step_) {
            df_ds_accum_ = 0;
            df_voiced_hist_[df_voiced_head_] = yl;
            df_voiced_head_ = (df_voiced_head_ + 1) % kVoicedHistSize;
            if (df_voiced_count_ < kVoicedHistSize) {
                ++df_voiced_count_;
            }
            sc_sum_sq += yl * yl;
            ++sc_samples;
        }
    }

    const float in_rms = std::sqrt(sum_sq_in * inv_hop);
    const float sc_mean_energy = (sc_samples > 0) ? (sc_sum_sq / static_cast<float>(sc_samples)) : 0.0f;
    df_total_frames_ = (std::min)(df_total_frames_ + 1, 1000000);

    if (df_corrupted || !is_deepfilter_loaded_unlocked()) {
        df_out_ring_.push(df_clean_frame_.data(), df_hop);
        return;
    }

    if (!df_noise_initialized_ && in_rms > kMinPhysicalRms) {
        df_init_min_rms_ = (df_init_min_rms_ <= 0.0f) ? in_rms : (std::min)(df_init_min_rms_, in_rms);
    }

    const float curr_nf = effective_noise_floor();
    float periodicity = -1.0f;
    if (df_speech_hold_sec_ > 0.0f || in_rms > curr_nf * 1.5f ||
        (!df_noise_initialized_ && in_rms > kMinPhysicalRms * 4.0f)) {
        periodicity = compute_voiced_periodicity();
    }

    const float in_snr_ratio = in_rms / (std::max)(curr_nf, kMinPhysicalRms);
    const bool step_rise_from_noise = (df_smooth_rms_ > 0.0f && in_rms > df_smooth_rms_ * 1.8f);
    const bool voiced_onset = (periodicity >= 0.62f && in_snr_ratio >= 1.7f);
    // 無音後の低SNR小声でも立ち上がり・有声音周期性でプリゲインを起動させつつ定常サージを抑制
    const bool allow_onset_pregain =
        (df_low_lsnr_streak_frames_ < 5) ||
        (step_rise_from_noise && in_snr_ratio >= 2.0f) ||
        voiced_onset;
    const bool in_speech_for_pregain = df_noise_initialized_
        ? ((df_speech_hold_sec_ > 0.0f && in_rms > (std::max)(curr_nf * 1.8f, kMinPhysicalRms * 5.0f)) ||
           (allow_onset_pregain && in_snr_ratio >= 1.7f &&
            (voiced_onset || in_snr_ratio >= 2.2f) && in_rms > kMinPhysicalRms * 5.0f))
        : (df_init_speech_streak_ >= 2 && in_rms > kMinPhysicalRms * 20.0f &&
           (df_init_voiced_confirmed_ || df_init_min_rms_ < kMaxAmplifiedNoiseRms));

    if (!df_pregain_speech_active_ && in_speech_for_pregain) {
        // 過去大声の残像は消しつつ微弱呼気での極端な語頭乱高下を防ぐ下限で初期化
        df_speech_peak_env_ = (std::max)(in_peak, 0.07f);
    } else if (in_peak > df_speech_peak_env_) {
        const float att_alpha = time_const_alpha(frame_dt_sec, 0.015f);
        df_speech_peak_env_ += (in_peak - df_speech_peak_env_) * att_alpha;
    } else if (in_speech_for_pregain) {
        const float rel_alpha = time_const_alpha(frame_dt_sec, 0.60f);
        df_speech_peak_env_ += (in_peak - df_speech_peak_env_) * rel_alpha;
    } else {
        const float idle_alpha = time_const_alpha(frame_dt_sec, 0.25f);
        df_speech_peak_env_ += (kPreGainTargetPeak - df_speech_peak_env_) * idle_alpha;
    }
    df_pregain_speech_active_ = in_speech_for_pregain;

    const float pregain_cap_max = df_noise_initialized_ ? kPreGainMax : kEarlyPreGainMax;
    const float raw_pre_gain = std::clamp(
        kPreGainTargetPeak / (df_speech_peak_env_ + 1.0e-4f),
        1.0f,
        pregain_cap_max
    );
    const float safe_nf_for_cap = df_noise_initialized_
        ? curr_nf
        : (df_init_voiced_confirmed_ ? curr_nf : (std::max)(curr_nf, df_init_min_rms_));
    const float noise_safe_gain_cap = (df_noise_initialized_ || !df_init_voiced_confirmed_)
        ? std::clamp(kMaxAmplifiedNoiseRms / (std::max)(safe_nf_for_cap, kMinPhysicalRms), 1.0f, pregain_cap_max)
        : pregain_cap_max;
    float target_pre_gain = in_speech_for_pregain ? (std::min)(raw_pre_gain, noise_safe_gain_cap) : 1.0f;

    const float curr_frame_cap = (in_peak > 1.0e-5f)
        ? std::clamp(0.85f / in_peak, 1.0f, pregain_cap_max)
        : pregain_cap_max;
    const float next_frame_cap = (next_peak > 1.0e-5f)
        ? std::clamp(0.85f / next_peak, 1.0f, pregain_cap_max)
        : pregain_cap_max;
    target_pre_gain = (std::min)({target_pre_gain, curr_frame_cap, next_frame_cap});

    const float start_gain = std::clamp(df_pre_gain_, 1.0f, kPreGainMax);
    float end_gain = start_gain;
    if (next_frame_cap < start_gain || curr_frame_cap < start_gain) {
        // 1フレーム先読みにより次フレームの突発ピーク手前までに10msコサインランプで安全ゲインへ確実に到達
        end_gain = std::clamp((std::min)(target_pre_gain, next_frame_cap), 1.0f, kPreGainMax);
    } else {
        const float pre_alpha = (target_pre_gain < start_gain)
            ? time_const_alpha(frame_dt_sec, 0.15f)
            : time_const_alpha(frame_dt_sec, 0.07f);
        end_gain = std::clamp(start_gain + (target_pre_gain - start_gain) * pre_alpha, 1.0f, kPreGainMax);
    }

    for (size_t i = 0; i < df_hop; ++i) {
        const float frac = static_cast<float>(i + 1) * inv_hop;
        const float cos_t = 0.5f - 0.5f * std::cos(kPiF * frac);
        const float g_i = start_gain + (end_gain - start_gain) * cos_t;
        df_gain_frame_[i] = g_i;
        const float amplified = df_clean_frame_[i] * g_i + next_micro_dither(dither_rng_);
        df_input_frame_[i] = std::clamp(amplified, -0.99f, 0.99f);
    }
    df_pre_gain_ = end_gain;
    df_gain_delay_ring_.push(df_gain_frame_.data(), df_hop);

    // 事前取得特徴量(自己相関周期性・残留音声エネルギー)で0フレーム遅れの保護判定を行い推論前に適用
    const float est_in_speech = std::sqrt((std::max)(in_rms * in_rms - curr_nf * curr_nf, 0.0f));
    const bool above_noise_gate = (in_rms > (std::max)(curr_nf * 1.8f, kMinPhysicalRms * 8.0f));
    const bool is_low_snr_region = (in_rms < (std::max)(curr_nf * 6.5f, kMinPhysicalRms * 25.0f));
    const bool is_tail_candidate =
        (df_in_speech_env_ > 0.0f && in_rms < df_in_speech_env_ * 0.45f && is_low_snr_region &&
         (est_in_speech > curr_nf * 2.1f || periodicity >= 0.65f));
    if (above_noise_gate && is_tail_candidate) {
        df_tail_fade_streak_ = (std::min)(df_tail_fade_streak_ + 1, 100);
    } else {
        df_tail_fade_streak_ = 0;
    }

    float target_fade_level = 0.0f;
    if (above_noise_gate && (df_speech_hold_sec_ > 0.0f || periodicity >= 0.68f)) {
        const bool is_onset_whisper =
            (df_speech_hold_sec_ <= 0.0f && periodicity >= 0.68f && is_low_snr_region);
        const bool is_extreme_whisper =
            (df_in_speech_env_ > curr_nf * 1.8f && df_in_speech_env_ < curr_nf * 3.5f);
        const bool is_tail_fade = (df_tail_fade_streak_ >= 2);
        if (is_onset_whisper || is_extreme_whisper || is_tail_fade) {
            target_fade_level = 1.0f;
        }
    }
    const float protect_tau = (target_fade_level > df_fade_protect_level_)
        ? 0.015f
        : (above_noise_gate ? 0.15f : 0.04f);
    const float protect_alpha = time_const_alpha(frame_dt_sec, protect_tau);
    df_fade_protect_level_ += (target_fade_level - df_fade_protect_level_) * protect_alpha;

    DFState* state = df_states_[0];

    // 35dBからノイズ連動安全下限(>=24dB)へ滑らかに緩和しポンピングと遅れを解消
    const float nf_db = 20.0f * std::log10((std::max)(curr_nf, kMinPhysicalRms));
    const float target_res_db = 20.0f * std::log10(kTargetResidualNoiseRms);
    const float safe_min_atten_db = std::clamp(
        nf_db - target_res_db,
        kMinSafeAttenLimDb,
        kDefaultAttenLimDb - 3.0f
    );
    const float target_atten_db =
        kDefaultAttenLimDb - (kDefaultAttenLimDb - safe_min_atten_db) * df_fade_protect_level_;
    const float atten_alpha = (target_atten_db < df_current_atten_lim_db_)
        ? time_const_alpha(frame_dt_sec, 0.015f)
        : time_const_alpha(frame_dt_sec, 0.05f);
    df_current_atten_lim_db_ += (target_atten_db - df_current_atten_lim_db_) * atten_alpha;
    if (df_last_applied_atten_lim_db_ < 0.0f ||
        std::fabs(df_current_atten_lim_db_ - df_last_applied_atten_lim_db_) >= 0.25f) {
        df_set_atten_lim(state, df_current_atten_lim_db_);
        df_last_applied_atten_lim_db_ = df_current_atten_lim_db_;
    }

    std::fill(df_output_frame_.begin(), df_output_frame_.end(), 0.0f);
    const float lsnr = df_process_frame(state, df_input_frame_.data(), df_output_frame_.data());
    df_state_dirty_ = true;
    if (lsnr < 0.8f) {
        df_low_lsnr_streak_frames_ = (std::min)(df_low_lsnr_streak_frames_ + 1, 10000);
    } else {
        df_low_lsnr_streak_frames_ = 0;
    }
    while (char* msg = df_next_log_msg(state)) {
        df_free_log_msg(msg);
    }

    // 初期ノイズフロア収集: lsnr >= 1.3f すべてで4フレーム未満の断片をロールバックし真の無音区間のみで確定
    if (!df_noise_initialized_ && in_rms > kMinPhysicalRms) {
        if (periodicity >= 0.65f) {
            df_init_voiced_confirmed_ = true;
        }
        if (lsnr >= 2.2f && in_rms > kMinPhysicalRms * 20.0f &&
            (df_total_frames_ >= 5 || periodicity >= 0.65f)) {
            df_init_speech_streak_ = (std::min)(df_init_speech_streak_ + 1, 1000);
        } else if (lsnr < 1.0f) {
            df_init_speech_streak_ = 0;
        }

        if (lsnr < 1.3f) {
            df_noise_init_low_streak_ = (std::min)(df_noise_init_low_streak_ + 1, 1000);
            if (df_noise_init_count_ < kNoiseInitFrames) {
                df_noise_init_samples_[df_noise_init_count_++] = in_rms;
            }
        } else {
            if (df_noise_init_low_streak_ > 0 && df_noise_init_low_streak_ < 4) {
                const int rollback = (std::min)(df_noise_init_low_streak_, df_noise_init_count_);
                df_noise_init_count_ -= rollback;
                if (df_noise_init_count_ == 0) {
                    df_noise_init_wait_frames_ = 0;
                }
            }
            df_noise_init_low_streak_ = 0;
        }

        if (df_noise_init_count_ > 0) {
            ++df_noise_init_wait_frames_;
        }
        if (df_noise_init_count_ >= kNoiseInitFrames ||
            (df_noise_init_wait_frames_ >= kMaxNoiseInitWaitFrames && df_noise_init_count_ >= 4)) {
            df_noise_floor_ = compute_robust_noise_floor(df_noise_init_samples_, df_noise_init_count_);
            df_smooth_rms_ = df_noise_floor_;
            df_fast_noise_floor_ = df_noise_floor_;
            df_min_block_rms_ = df_noise_floor_;
            df_prev_block_min_rms_ = df_noise_floor_;
            df_noise_initialized_ = true;
        }
    }

    const float nf_ref = effective_noise_floor();
    const float snr_ratio = in_rms / (std::max)(nf_ref, kMinPhysicalRms);
    const bool strong_onset = (lsnr >= 1.8f && in_rms > kMinPhysicalRms * 4.0f && snr_ratio >= 2.2f);
    const bool weak_onset_cand = (lsnr >= 0.8f && in_rms > kMinPhysicalRms * 5.0f && snr_ratio >= 2.8f);
    if (weak_onset_cand) {
        df_onset_confirm_frames_ = (std::min)(df_onset_confirm_frames_ + 1, 10);
    } else {
        df_onset_confirm_frames_ = 0;
    }
    const bool clear_speech = strong_onset || (df_onset_confirm_frames_ >= 2);

    if (clear_speech) {
        if (df_speech_ref_rms_ <= 0.0f) {
            df_speech_ref_rms_ = in_rms;
        } else {
            const float ref_alpha = (in_rms > df_speech_ref_rms_)
                ? time_const_alpha(frame_dt_sec, 0.05f)
                : time_const_alpha(frame_dt_sec, 0.60f);
            df_speech_ref_rms_ += (in_rms - df_speech_ref_rms_) * ref_alpha;
        }
        const float env_alpha = (in_rms > df_in_speech_env_)
            ? time_const_alpha(frame_dt_sec, 0.03f)
            : time_const_alpha(frame_dt_sec, 0.30f);
        df_in_speech_env_ += (in_rms - df_in_speech_env_) * env_alpha;

        df_speech_hold_sec_ = 0.22f;
        df_voiced_hold_active_ = false;
        df_non_voiced_low_lsnr_sec_ = 0.0f;
        df_voiced_low_lsnr_sec_ = 0.0f;
    } else if (df_speech_hold_sec_ > 0.0f) {
        const bool energy_sustained =
            (in_rms > (std::max)(nf_ref * 2.5f, kMinPhysicalRms * 4.0f)) &&
            (in_rms > df_speech_ref_rms_ * 0.25f);
        if (energy_sustained) {
            if (periodicity < 0.0f) {
                periodicity = compute_voiced_periodicity();
            }
            if (periodicity >= 0.60f && df_voiced_low_lsnr_sec_ < 2.50f) {
                df_voiced_low_lsnr_sec_ += frame_dt_sec;
                df_speech_hold_sec_ = (std::max)(df_speech_hold_sec_, 0.16f);
                df_voiced_hold_active_ = true;
                df_non_voiced_low_lsnr_sec_ = 0.0f;
            } else {
                df_voiced_hold_active_ = false;
                df_non_voiced_low_lsnr_sec_ += frame_dt_sec;
                if (df_non_voiced_low_lsnr_sec_ < 0.22f) {
                    df_speech_hold_sec_ = (std::max)(df_speech_hold_sec_, 0.12f);
                } else {
                    df_speech_hold_sec_ = (std::max)(0.0f, df_speech_hold_sec_ - frame_dt_sec);
                }
            }
        } else {
            df_voiced_hold_active_ = false;
            df_speech_hold_sec_ = (std::max)(0.0f, df_speech_hold_sec_ - frame_dt_sec);
        }
    } else {
        df_voiced_hold_active_ = false;
        df_non_voiced_low_lsnr_sec_ = 0.0f;
        df_voiced_low_lsnr_sec_ = 0.0f;
        df_speech_ref_rms_ = 0.0f;
        df_in_speech_env_ = 0.0f;
    }

    const bool speech_active = (df_speech_hold_sec_ > 0.0f);
    if (!speech_active) {
        df_voiced_hold_active_ = false;
        df_speech_ref_rms_ = 0.0f;
        df_in_speech_env_ = 0.0f;
    }

    if (df_noise_initialized_ && in_rms > kMinPhysicalRms) {
        if (!speech_active && in_rms < df_noise_floor_) {
            const float down_alpha = time_const_alpha(frame_dt_sec, 0.15f);
            df_noise_floor_ += ((std::max)(in_rms, kMinPhysicalRms) - df_noise_floor_) * down_alpha;
        }
        // 発話ホールド中の無声子音誤追従を防ぐため、非発話低LSNR区間でのみサージ追従・ブロック推定を行う
        if (!speech_active && lsnr < 0.8f && df_onset_confirm_frames_ == 0) {
            df_non_speech_low_streak_ = (std::min)(df_non_speech_low_streak_ + 1, 10000);
            const float rms_alpha = time_const_alpha(frame_dt_sec, 0.04f);
            df_smooth_rms_ += (in_rms - df_smooth_rms_) * rms_alpha;

            if (df_non_speech_low_streak_ >= 6 && df_smooth_rms_ > df_fast_noise_floor_) {
                const float surge_alpha = time_const_alpha(frame_dt_sec, 0.03f);
                df_fast_noise_floor_ += (df_smooth_rms_ - df_fast_noise_floor_) * surge_alpha;
            }

            if (sc_mean_energy > 0.0f) {
                const float sc_alpha = time_const_alpha(frame_dt_sec, 0.25f);
                if (df_sc_noise_energy_ <= 0.0f) {
                    df_sc_noise_energy_ = sc_mean_energy;
                } else {
                    df_sc_noise_energy_ += (sc_mean_energy - df_sc_noise_energy_) * sc_alpha;
                }
            }
            df_min_block_rms_ = (std::min)(df_min_block_rms_, df_smooth_rms_);
            df_min_block_elapsed_sec_ += frame_dt_sec;
            if (df_min_block_elapsed_sec_ >= 0.60f) {
                if (df_prev_block_min_rms_ < 1.0e8f) {
                    const float robust_min = (std::min)(df_min_block_rms_, df_prev_block_min_rms_);
                    if (robust_min > df_noise_floor_) {
                        df_noise_floor_ += (robust_min - df_noise_floor_) * 0.45f;
                    }
                } else if (df_low_lsnr_streak_frames_ >= 15 && df_min_block_rms_ > df_noise_floor_) {
                    df_noise_floor_ += (df_min_block_rms_ - df_noise_floor_) * 0.45f;
                }
                df_prev_block_min_rms_ = df_min_block_rms_;
                df_min_block_rms_ = 1.0e9f;
                df_min_block_elapsed_sec_ = 0.0f;
            }
        } else {
            if (clear_speech || lsnr >= 1.2f) {
                df_non_speech_low_streak_ = 0;
            } else if (df_non_speech_low_streak_ > 0) {
                --df_non_speech_low_streak_;
            }
            // サージ保護フロアの1フレーム段差急落を防ぐため連続減衰で定常フロアへ戻す
            if (df_fast_noise_floor_ > df_noise_floor_) {
                const float decay_tau = clear_speech ? 0.06f : 0.25f;
                const float decay_alpha = time_const_alpha(frame_dt_sec, decay_tau);
                df_fast_noise_floor_ += (df_noise_floor_ - df_fast_noise_floor_) * decay_alpha;
            } else {
                df_fast_noise_floor_ = df_noise_floor_;
            }
        }
    }

    if (df_gain_delay_ring_.size() >= df_hop) {
        df_gain_delay_ring_.pop(df_delayed_gain_frame_.data(), df_hop);
    } else {
        std::fill(df_delayed_gain_frame_.begin(), df_delayed_gain_frame_.end(), 1.0f);
    }

    bool has_non_finite = false;
    for (size_t i = 0; i < df_hop; ++i) {
        const float g_out = (std::max)(df_delayed_gain_frame_[i], 1.0f);
        const float s = df_output_frame_[i] / g_out;
        if (!std::isfinite(s) || std::fabs(s) > 10.0f) {
            has_non_finite = true;
            break;
        }
        df_output_frame_[i] = std::clamp(s, -1.0f, 1.0f);
    }

    if (has_non_finite) {
        // 音声スレッドでの同期再ロードを避け、当該・残存フレームを安全出力した上でSpeexDSPへ移行
        std::cerr << "[AudioProcessor] DeepFilterNet NaN/Inf output detected. Switching to safe SpeexDSP fallback."
                  << std::endl;
        df_out_ring_.push(df_clean_frame_.data(), df_hop);
        destroy_deepfilter_state_unlocked();
        enable_speex_denoise_unlocked(true);
        df_corrupted = true;
    } else {
        df_out_ring_.push(df_output_frame_.data(), df_hop);
    }
}

void AudioProcessor::process(const float* input, float* output, int num_frames) {
    if (num_frames <= 0 || !input || !output) return;
    std::lock_guard<std::mutex> lock(mtx_);

    temp_buffer1_.resize(num_frames);
    temp_buffer2_.resize(num_frames);

    // マルチチャンネル入力をモノラルへスマートダウンミックス(片chのみのUSBマイクで音量が半減するのを防止)
    if (channels_ == 1) {
        std::memcpy(temp_buffer1_.data(), input, num_frames * sizeof(float));
    } else if (channels_ == 2) {
        float peakL = 0.0f;
        float peakR = 0.0f;
        for (int i = 0; i < num_frames; ++i) {
            peakL = (std::max)(peakL, std::fabs(input[i * 2 + 0]));
            peakR = (std::max)(peakR, std::fabs(input[i * 2 + 1]));
        }
        bool onlyL = (peakL > 1e-4f && peakR < peakL * 0.05f);
        bool onlyR = (peakR > 1e-4f && peakL < peakR * 0.05f);
        for (int i = 0; i < num_frames; ++i) {
            if (onlyL) {
                temp_buffer1_[i] = input[i * 2 + 0];
            } else if (onlyR) {
                temp_buffer1_[i] = input[i * 2 + 1];
            } else {
                temp_buffer1_[i] = 0.5f * (input[i * 2 + 0] + input[i * 2 + 1]);
            }
        }
    } else {
        for (int i = 0; i < num_frames; ++i) {
            float sum = 0.0f;
            for (int c = 0; c < channels_; ++c) {
                sum += input[i * channels_ + c];
            }
            temp_buffer1_[i] = sum / static_cast<float>(channels_);
        }
    }

    int frame_size = sample_rate_ / 100;

    for (int i = 0; i < num_frames; ++i) {
        if (!std::isfinite(temp_buffer1_[i])) {
            temp_buffer1_[i] = 0.0f;
        }
    }

    // 1. Pre-process: 80Hz Butterworth HPF (空調音・机の振動等の低周波ノイズを除去)
    if (preprocess_type_.load(std::memory_order_acquire) == 1) {
        for (int i = 0; i < num_frames; ++i) {
            float x0 = temp_buffer1_[i];
            float y0 = hpf_b0_ * x0 + hpf_b1_ * hpf_x1_ + hpf_b2_ * hpf_x2_
                       - hpf_a1_ * hpf_y1_ - hpf_a2_ * hpf_y2_;
            if (!std::isfinite(y0)) {
                hpf_x1_ = hpf_x2_ = hpf_y1_ = hpf_y2_ = 0.0f;
                y0 = x0;
            } else {
                hpf_x2_ = hpf_x1_;
                hpf_x1_ = x0;
                hpf_y2_ = hpf_y1_;
                hpf_y1_ = y0;
            }
            temp_buffer1_[i] = y0;
        }
    }

    // 2. Denoise Pipeline
    bool use_df = is_deepfilter_active_unlocked();
    const int cur_denoise = denoise_type_.load(std::memory_order_acquire);
    bool use_speex_denoise = (cur_denoise == 2) || (cur_denoise == 1 && !use_df);

    if (use_df) {
        const size_t df_hop = static_cast<size_t>((df_frame_size_ > 0) ? df_frame_size_ : frame_size);
        const size_t req_frames = static_cast<size_t>(num_frames);

        // 1フレーム先読み分のプリロールに加え、非整数倍チャンク時は(df_hop - 1)を補填し出力不足を根絶
        if (!df_lookahead_primed_) {
            df_out_ring_.push_zeros(df_hop);
            df_lookahead_primed_ = true;
        }
        if (!df_non_aligned_primed_ && ((df_in_ring_.size() + req_frames) % df_hop != 0)) {
            df_out_ring_.push_zeros(df_hop - 1);
            df_non_aligned_primed_ = true;
        }

        df_raw_frame_.resize(df_hop);
        df_next_raw_frame_.resize(df_hop);
        df_clean_frame_.resize(df_hop);
        df_input_frame_.resize(df_hop);
        df_output_frame_.resize(df_hop);
        df_gain_frame_.resize(df_hop);
        df_delayed_gain_frame_.resize(df_hop);
        df_in_ring_.push(temp_buffer1_.data(), req_frames);

        const float frame_dt_sec = static_cast<float>(df_hop) / static_cast<float>((std::max)(sample_rate_, 8000));
        const float inv_hop = 1.0f / static_cast<float>(df_hop);

        bool df_corrupted = false;
        while (df_in_ring_.size() >= 2 * df_hop) {
            df_in_ring_.pop(df_raw_frame_.data(), df_hop);
            df_in_ring_.peek(df_next_raw_frame_.data(), df_hop);
            process_df_frame(df_raw_frame_.data(), df_next_raw_frame_.data(),
                             df_hop, frame_dt_sec, inv_hop, df_corrupted);
        }

        if (df_out_ring_.size() >= req_frames) {
            df_out_ring_.pop(temp_buffer2_.data(), req_frames);
        } else {
            const size_t avail = df_out_ring_.size();
            if (avail > 0) {
                df_out_ring_.pop(temp_buffer2_.data(), avail);
            }
            std::fill(temp_buffer2_.begin() + avail, temp_buffer2_.begin() + req_frames, 0.0f);
        }

        // フォールバック移行後は現チャンク出力後にリングをリセット（非アライン異常時のみ最大10ms未満を破棄）
        if (df_corrupted) {
            reset_df_buffers_unlocked();
        }
    } else if (use_speex_denoise && !speex_pre_states_.empty() && speex_pre_states_[0]) {
        int16_buffer_.resize(num_frames);
        for (int i = 0; i < num_frames; ++i) {
            float val = std::clamp(sanitize_soft_clip(temp_buffer1_[i]) * 32768.0f, -32768.0f, 32767.0f);
            int16_buffer_[i] = static_cast<int16_t>(val);
        }
        SpeexPreprocessState* pre_st = static_cast<SpeexPreprocessState*>(speex_pre_states_[0]);
        for (int offset = 0; offset + frame_size <= num_frames; offset += frame_size) {
            speex_preprocess_run(pre_st, int16_buffer_.data() + offset);
        }
        for (int i = 0; i < num_frames; ++i) {
            temp_buffer2_[i] = int16_buffer_[i] / 32768.0f;
        }
    } else {
        for (int i = 0; i < num_frames; ++i) {
            temp_buffer2_[i] = sanitize_soft_clip(temp_buffer1_[i]);
        }
    }

    // 処理後のモノラル信号を全出力チャンネルに展開（NaN流出を最終段でも完全遮断）
    if (channels_ == 1) {
        for (int i = 0; i < num_frames; ++i) {
            float s = temp_buffer2_[i];
            output[i] = std::isfinite(s) ? std::clamp(s, -1.0f, 1.0f) : 0.0f;
        }
    } else {
        for (int i = 0; i < num_frames; ++i) {
            float s = temp_buffer2_[i];
            s = std::isfinite(s) ? std::clamp(s, -1.0f, 1.0f) : 0.0f;
            for (int c = 0; c < channels_; ++c) {
                output[i * channels_ + c] = s;
            }
        }
    }
}

// --- C API Implementation ---
extern "C" {
    void* AudioProcessor_Create(int sample_rate, int channels, const char* model_path) {
        try {
            return new AudioProcessor(sample_rate, channels, model_path);
        } catch (...) {
            std::cerr << "Exception caught in AudioProcessor_Create" << std::endl;
            return nullptr;
        }
    }

    void AudioProcessor_Process(void* processor, const float* input, float* output, int num_frames) {
        if (processor) {
            static_cast<AudioProcessor*>(processor)->process(input, output, num_frames);
        }
    }

    void AudioProcessor_SetPreProcessType(void* processor, int type) {
        if (processor) {
            static_cast<AudioProcessor*>(processor)->set_preprocess_type(type);
        }
    }

    void AudioProcessor_SetDenoiseType(void* processor, int type) {
        if (processor) {
            static_cast<AudioProcessor*>(processor)->set_denoise_type(type);
        }
    }

    int AudioProcessor_LoadDeepFilter(void* processor, const char* model_path) {
        if (processor) {
            return static_cast<AudioProcessor*>(processor)->load_deepfilter(model_path) ? 1 : 0;
        }
        return 0;
    }

    int AudioProcessor_IsDeepFilterLoaded(void* processor) {
        if (processor) {
            return static_cast<AudioProcessor*>(processor)->is_deepfilter_loaded() ? 1 : 0;
        }
        return 0;
    }

    int AudioProcessor_IsDeepFilterActive(void* processor) {
        if (processor) {
            return static_cast<AudioProcessor*>(processor)->is_deepfilter_active() ? 1 : 0;
        }
        return 0;
    }

    void AudioProcessor_Destroy(void* processor) {
        if (processor) {
            delete static_cast<AudioProcessor*>(processor);
        }
    }
}
