#include "AudioProcessor.h"
#include <cstring>
#include <cmath>
#include <iostream>
#include <algorithm>
#include <speex/speex_preprocess.h>

// DeepFilterNet C API
#include "deep_filter.h"

namespace {
// 無制限減衰(100dB)による小声・話し始めの消失を防ぎつつノイズを十分抑制する上限(dB)
constexpr float kDeepFilterAttenLimDb = 35.0f;

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

AudioProcessor::AudioProcessor(int sample_rate, int channels, const char* model_path)
    : sample_rate_(sample_rate > 0 ? sample_rate : 48000),
      preprocess_type_(1),
      denoise_type_(0),
      channels_(channels > 0 ? channels : 1),
      df_frame_size_(sample_rate_ / 100) {

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

void AudioProcessor::destroy_deepfilter_state() {
    for (DFState* state : df_states_) {
        if (state) {
            df_free(state);
        }
    }
    df_states_.clear();
}

void AudioProcessor::reset_df_buffers() {
    df_in_ring_.clear();
    df_out_ring_.clear();
    df_non_aligned_primed_ = false;
}

bool AudioProcessor::init_deepfilter() {
    destroy_deepfilter_state();
    reset_df_buffers();
    if (df_model_path_.empty()) {
        return false;
    }
    try {
        DFState* state = df_create(df_model_path_.c_str(), kDeepFilterAttenLimDb, "warn");
        if (!state) {
            std::cerr << "[AudioProcessor] Error: df_create returned nullptr with path: " << df_model_path_ << std::endl;
            return false;
        }
        // ポストフィルタ無効化(公式デフォルト)で小声や子音の過剰削りを防止
        df_set_post_filter_beta(state, 0.0f);
        const uintptr_t model_frame_len = df_get_frame_length(state);
        df_frame_size_ = (model_frame_len > 0 && model_frame_len <= 4800)
                             ? static_cast<int>(model_frame_len)
                             : (sample_rate_ / 100);
        // Rust側の初期化ログバッファを解放してメモリリークを防止
        while (char* msg = df_next_log_msg(state)) {
            df_free_log_msg(msg);
        }
        df_states_.push_back(state);
        return true;
    } catch (const std::exception& e) {
        std::cerr << "[AudioProcessor] Exception in DeepFilterNet init: " << e.what() << std::endl;
    } catch (...) {
        std::cerr << "[AudioProcessor] Unknown exception in DeepFilterNet init." << std::endl;
    }
    return false;
}

bool AudioProcessor::load_deepfilter(const char* model_path) {
    if (!model_path || std::strlen(model_path) == 0) {
        return false;
    }
    df_model_path_ = model_path;
    std::replace(df_model_path_.begin(), df_model_path_.end(), '\\', '/');
    const bool ok = init_deepfilter();
    const bool use_speex_denoise = (denoise_type_ == 2) || (denoise_type_ == 1 && !is_deepfilter_loaded());
    enable_speex_denoise(use_speex_denoise);
    return ok;
}

void AudioProcessor::enable_speex_denoise(bool enable) {
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

bool AudioProcessor::is_deepfilter_loaded() const {
    return (!df_states_.empty() && df_states_[0] != nullptr);
}

bool AudioProcessor::is_deepfilter_active() const {
    return (denoise_type_ == 1 && is_deepfilter_loaded());
}

AudioProcessor::~AudioProcessor() {
    destroy_deepfilter_state();
    reset_df_buffers();
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
    preprocess_type_ = type;
}

void AudioProcessor::set_denoise_type(int type) {
    if (denoise_type_ != type) {
        reset_df_buffers();
    }
    denoise_type_ = type;
    const bool use_speex_denoise = (denoise_type_ == 2) || (denoise_type_ == 1 && !is_deepfilter_loaded());
    enable_speex_denoise(use_speex_denoise);
}

void AudioProcessor::process(const float* input, float* output, int num_frames) {
    if (num_frames <= 0 || !input || !output) return;

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
    if (preprocess_type_ == 1) {
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
    bool use_df = is_deepfilter_active();
    bool use_speex_denoise = (denoise_type_ == 2) || (denoise_type_ == 1 && !use_df);

    if (use_df) {
        const size_t df_hop = static_cast<size_t>((df_frame_size_ > 0) ? df_frame_size_ : frame_size);
        const size_t req_frames = static_cast<size_t>(num_frames);

        // 非整数倍チャンク時のみ初回に(df_hop - 1)の定数プリロールを積み、以降の不足・時系列逆転を根絶
        if (!df_non_aligned_primed_ && ((df_in_ring_.size() + req_frames) % df_hop != 0)) {
            df_out_ring_.push_zeros(df_hop - 1);
            df_non_aligned_primed_ = true;
        }

        df_raw_frame_.resize(df_hop);
        df_clean_frame_.resize(df_hop);
        df_input_frame_.resize(df_hop);
        df_output_frame_.resize(df_hop);
        df_in_ring_.push(temp_buffer1_.data(), req_frames);

        bool df_corrupted = false;
        while (df_in_ring_.size() >= df_hop) {
            df_in_ring_.pop(df_raw_frame_.data(), df_hop);
            for (size_t i = 0; i < df_hop; ++i) {
                const float clean = sanitize_soft_clip(df_raw_frame_[i]);
                df_clean_frame_[i] = clean;
                df_input_frame_[i] = clean + next_micro_dither(dither_rng_);
            }

            if (df_corrupted || !is_deepfilter_loaded()) {
                df_out_ring_.push(df_clean_frame_.data(), df_hop);
                continue;
            }

            DFState* state = df_states_[0];
            std::fill(df_output_frame_.begin(), df_output_frame_.end(), 0.0f);
            df_process_frame(state, df_input_frame_.data(), df_output_frame_.data());
            while (char* msg = df_next_log_msg(state)) {
                df_free_log_msg(msg);
            }

            bool has_non_finite = false;
            for (size_t i = 0; i < df_hop; ++i) {
                const float s = df_output_frame_[i];
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
                destroy_deepfilter_state();
                enable_speex_denoise(true);
                df_corrupted = true;
            } else {
                df_out_ring_.push(df_output_frame_.data(), df_hop);
            }
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
            reset_df_buffers();
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
