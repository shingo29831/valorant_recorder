#include "AudioProcessor.h"
#include <cstring>
#include <iostream>
#include <algorithm>
#include <speex/speex_preprocess.h>

// DeepFilterNet C API
#include "deep_filter.h"

AudioProcessor::AudioProcessor(int sample_rate, int channels, const char* model_path)
    : sample_rate_(sample_rate), channels_(channels), preprocess_type_(1), denoise_type_(0) {
    
    // Number of samples per frame (usually 10ms = sample_rate / 100)
    int frame_size = sample_rate_ / 100; 

    /* --- 1. Initialize SpeexDSP (Pre-process: HPF) and 3. (Post-process: AGC) --- */
    // SpeexDSP is mono only, so create a state for each channel
    for (int c = 0; c < channels_; ++c) {
        // For pre-processing (HPF)
        SpeexPreprocessState* pre_st = speex_preprocess_state_init(frame_size, sample_rate_);
        int denoise = 0; // AIノイズキャンセルと二重にかかると音がこもるため、SpeexDSP側のDenoiseは無効化
        speex_preprocess_ctl(pre_st, SPEEX_PREPROCESS_SET_DENOISE, &denoise); 
        speex_pre_states_.push_back(pre_st);

        // For post-processing (AGC)
        SpeexPreprocessState* post_st = speex_preprocess_state_init(frame_size, sample_rate_);
        int agc = 1;
        int agc_level = 16000; // ターゲット音量を少し下げて自然にする (最大32768)
        int vad = 1;           // VAD (音声検出) を有効化
        
        // VADを有効にすることで、無音時にAGCがノイズを無理に増幅する(ポンピング)のを防ぐ
        speex_preprocess_ctl(post_st, SPEEX_PREPROCESS_SET_VAD, &vad);
        speex_preprocess_ctl(post_st, SPEEX_PREPROCESS_SET_AGC, &agc);
        speex_preprocess_ctl(post_st, SPEEX_PREPROCESS_SET_AGC_LEVEL, &agc_level);
        speex_post_states_.push_back(post_st);
    }

    /* --- 2. Initialize DeepFilterNet --- */
    try {
        // DeepFilterNetの初期化 (モデルパス、減衰制限dB、ログレベル)
        // Rust側のパニック（クラッシュ）を防ぐため、有効なパスが渡された場合のみ初期化する
        if (model_path != nullptr && std::strlen(model_path) > 0) {
            // 減衰制限(Attenuation limit)を100dBに設定し、ノイズを強力にカットする
            // マルチチャンネル処理のため、チャンネルごとに独立したステートを生成する
            for (int c = 0; c < channels_; ++c) {
                DFState* state = df_create(model_path, 100.0f, 0);
                if (!state) {
                    std::cerr << "[AudioProcessor] Error: df_create returned nullptr for channel " << c << ". AI Denoise will not work." << std::endl;
                }
                df_states_.push_back(state);
            }
        } else {
            std::cerr << "[AudioProcessor] DeepFilterNet model path is empty. AI Denoise will be disabled." << std::endl;
        }
    } catch (...) {
        std::cerr << "[AudioProcessor] Failed to initialize DeepFilterNet." << std::endl;
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
        speex_preprocess_state_destroy(static_cast<SpeexPreprocessState*>(st));
    }
    for (void* st : speex_post_states_) {
        speex_preprocess_state_destroy(static_cast<SpeexPreprocessState*>(st));
    }
}

void AudioProcessor::set_preprocess_type(int type) {
    preprocess_type_ = type;
}

void AudioProcessor::set_denoise_type(int type) {
    denoise_type_ = type;
}

void AudioProcessor::process(const float* input, float* output, int num_frames) {
    int total_samples = num_frames * channels_;
    temp_buffer1_.resize(total_samples);
    temp_buffer2_.resize(total_samples);
    int16_buffer_.resize(total_samples);

    // Copy input to buffer as initial state
    std::memcpy(temp_buffer1_.data(), input, total_samples * sizeof(float));

    int frame_size = sample_rate_ / 100;
    std::vector<int16_t> channel_buffer(frame_size);

    /* --- 1. Pre-process (HPF) --- */
    if (preprocess_type_ == 1) {
        // SpeexDSP
        // float32 -> int16_t conversion
        for (int i = 0; i < total_samples; ++i) {
            float val = temp_buffer1_[i] * 32768.0f;
            val = std::clamp(val, -32768.0f, 32767.0f);
            int16_buffer_[i] = static_cast<int16_t>(val);
        }
        
        // Process each channel separately (SpeexDSP requires 10ms frames)
        for (int c = 0; c < channels_; ++c) {
            SpeexPreprocessState* pre_st = static_cast<SpeexPreprocessState*>(speex_pre_states_[c]);
            for (int offset = 0; offset + frame_size <= num_frames; offset += frame_size) {
                // Extract target channel from interleaved data
                for (int i = 0; i < frame_size; ++i) {
                    channel_buffer[i] = int16_buffer_[(offset + i) * channels_ + c];
                }
                speex_preprocess_run(pre_st, channel_buffer.data());
                // Put processed result back to original buffer
                for (int i = 0; i < frame_size; ++i) {
                    int16_buffer_[(offset + i) * channels_ + c] = channel_buffer[i];
                }
            }
        }
        
        // int16_t -> float32 conversion
        for (int i = 0; i < total_samples; ++i) {
            temp_buffer2_[i] = int16_buffer_[i] / 32768.0f;
        }
    } else {
        // None (Bypass)
        std::memcpy(temp_buffer2_.data(), temp_buffer1_.data(), total_samples * sizeof(float));
    }

    /* --- 2. AI Denoiser (DeepFilterNet) --- */
    // まず入力をそのままコピーしておく (処理されなかったチャンネルや端数の保護)
    std::memcpy(temp_buffer1_.data(), temp_buffer2_.data(), total_samples * sizeof(float));

    if (denoise_type_ == 1 && !df_states_.empty()) {
        std::vector<float> channel_in(frame_size);
        std::vector<float> channel_out(frame_size);
        
        // DeepFilterNet processes frame by frame (usually 10ms = 480 samples at 48kHz)
        // ステレオなどのマルチチャンネルの場合、チャンネルごとに独立して処理する必要がある
        for (int c = 0; c < channels_; ++c) {
            DFState* state = df_states_[c];
            if (!state) continue;
            
            for (int offset = 0; offset + frame_size <= num_frames; offset += frame_size) {
                // インターリーブされたデータから対象チャンネルを抽出 (デインターリーブ)
                for (int i = 0; i < frame_size; ++i) {
                    channel_in[i] = temp_buffer2_[(offset + i) * channels_ + c];
                }
                
                df_process_frame(state, channel_in.data(), channel_out.data());
                
                // 処理結果を元のバッファに戻す (インターリーブ)
                for (int i = 0; i < frame_size; ++i) {
                    temp_buffer1_[(offset + i) * channels_ + c] = channel_out[i];
                }
            }
        }
    }

    /* --- 3. SpeexDSP (Post-process: AGC) --- */
    // AIノイズキャンセルの後にSpeexDSPのAGCを適用すると、
    // 微小な残留ノイズを極端に増幅してしまい「ノイズカットが消えた」ように聞こえる問題や、
    // VADの誤動作によるプツプツ音が発生するため、AGCは完全にバイパスします。
    std::memcpy(output, temp_buffer1_.data(), total_samples * sizeof(float));
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
