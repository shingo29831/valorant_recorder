#include "AudioProcessor.h"
#include <cstring>
#include <cmath>
#include <iostream>
#include <algorithm>
#include <speex/speex_preprocess.h>

// DeepFilterNet C API
#include "deep_filter.h"

AudioProcessor::AudioProcessor(int sample_rate, int channels, const char* model_path)
    : sample_rate_(sample_rate > 0 ? sample_rate : 48000),
      preprocess_type_(1),
      denoise_type_(0),
      channels_(channels > 0 ? channels : 1) {

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

    try {
        if (model_path != nullptr && std::strlen(model_path) > 0) {
            std::string path_str(model_path);
            std::replace(path_str.begin(), path_str.end(), '\\', '/');

            DFState* state = df_create(path_str.c_str(), 100.0f, "warn");
            if (!state) {
                std::cerr << "[AudioProcessor] Error: df_create returned nullptr with path: " << path_str << std::endl;
            } else {
                df_set_post_filter_beta(state, 0.02f);
                // Rust側の初期化ログバッファを解放してメモリリークを防止
                while (char* msg = df_next_log_msg(state)) {
                    df_free_log_msg(msg);
                }
                df_states_.push_back(state);
            }
        }
    } catch (const std::exception& e) {
        std::cerr << "[AudioProcessor] Exception in DeepFilterNet init: " << e.what() << std::endl;
    } catch (...) {
        std::cerr << "[AudioProcessor] Unknown exception in DeepFilterNet init." << std::endl;
    }
}

AudioProcessor::~AudioProcessor() {
    for (DFState* state : df_states_) {
        if (state) {
            df_free(state);
        }
    }
    df_states_.clear();
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
    denoise_type_ = type;
    if (!speex_pre_states_.empty() && speex_pre_states_[0]) {
        SpeexPreprocessState* pre_st = static_cast<SpeexPreprocessState*>(speex_pre_states_[0]);
        // DeepFilterNetが利用不能な場合や軽量RNNoise/SpeexDSP選択時はSpeexDSPノイズ抑制を有効化
        bool use_speex_denoise = (denoise_type_ == 2) || (denoise_type_ == 1 && df_states_.empty());
        int denoise = use_speex_denoise ? 1 : 0;
        int noise_suppress = -25;
        speex_preprocess_ctl(pre_st, SPEEX_PREPROCESS_SET_DENOISE, &denoise);
        speex_preprocess_ctl(pre_st, SPEEX_PREPROCESS_SET_NOISE_SUPPRESS, &noise_suppress);
    }
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

    // 1. Pre-process: 80Hz Butterworth HPF (空調音・机の振動等の低周波ノイズを除去)
    if (preprocess_type_ == 1) {
        for (int i = 0; i < num_frames; ++i) {
            float x0 = temp_buffer1_[i];
            float y0 = hpf_b0_ * x0 + hpf_b1_ * hpf_x1_ + hpf_b2_ * hpf_x2_
                       - hpf_a1_ * hpf_y1_ - hpf_a2_ * hpf_y2_;
            hpf_x2_ = hpf_x1_;
            hpf_x1_ = x0;
            hpf_y2_ = hpf_y1_;
            hpf_y1_ = y0;
            temp_buffer1_[i] = y0;
        }
    }

    // 2. Denoise Pipeline
    bool use_df = (denoise_type_ == 1 && !df_states_.empty() && df_states_[0] != nullptr);
    bool use_speex_denoise = (denoise_type_ == 2) || (denoise_type_ == 1 && !use_df);

    if (use_df) {
        std::memcpy(temp_buffer2_.data(), temp_buffer1_.data(), num_frames * sizeof(float));
        DFState* state = df_states_[0];
        for (int offset = 0; offset + frame_size <= num_frames; offset += frame_size) {
            df_process_frame(state, temp_buffer1_.data() + offset, temp_buffer2_.data() + offset);
        }
        while (char* msg = df_next_log_msg(state)) {
            df_free_log_msg(msg);
        }
    } else if (use_speex_denoise && !speex_pre_states_.empty() && speex_pre_states_[0]) {
        int16_buffer_.resize(num_frames);
        for (int i = 0; i < num_frames; ++i) {
            float val = std::clamp(temp_buffer1_[i] * 32768.0f, -32768.0f, 32767.0f);
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
        std::memcpy(temp_buffer2_.data(), temp_buffer1_.data(), num_frames * sizeof(float));
    }

    // 処理後のモノラル信号を全出力チャンネルに展開
    if (channels_ == 1) {
        std::memcpy(output, temp_buffer2_.data(), num_frames * sizeof(float));
    } else {
        for (int i = 0; i < num_frames; ++i) {
            float s = std::clamp(temp_buffer2_[i], -1.0f, 1.0f);
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

    void AudioProcessor_Destroy(void* processor) {
        if (processor) {
            delete static_cast<AudioProcessor*>(processor);
        }
    }
}
