#include "AudioProcessor.h"
#include "AudioProcessorUtils.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <iostream>
#include <speex/speex_preprocess.h>

using namespace audio_proc_detail;

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
