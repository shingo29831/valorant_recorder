#include "AudioProcessor.h"
#include <cstring>
#include <iostream>
#include <algorithm>
#include <speex/speex_preprocess.h>

// Enable the following header in the actual project
// #include "nvAudioEffects.h"

AudioProcessor::AudioProcessor(int sample_rate, int channels, const char* model_path)
    : sample_rate_(sample_rate), channels_(channels), preprocess_type_(1), nvafx_handle_(nullptr), is_nvidia_loaded_(false) {
    
    // Number of samples per frame (usually 10ms = sample_rate / 100)
    int frame_size = sample_rate_ / 100; 

    /* --- 1. Initialize SpeexDSP (Pre-process: HPF/Denoise) and 3. (Post-process: AGC) --- */
    // SpeexDSP is mono only, so create a state for each channel
    for (int c = 0; c < channels_; ++c) {
        // For pre-processing (HPF)
        SpeexPreprocessState* pre_st = speex_preprocess_state_init(frame_size, sample_rate_);
        int denoise = 1;
        speex_preprocess_ctl(pre_st, SPEEX_PREPROCESS_SET_DENOISE, &denoise); // Light denoise and DC cut (HPF)
        speex_pre_states_.push_back(pre_st);

        // For post-processing (AGC)
        SpeexPreprocessState* post_st = speex_preprocess_state_init(frame_size, sample_rate_);
        int agc = 1;
        int agc_level = 24000; // Target volume level (SpeexDSP requires int32)
        speex_preprocess_ctl(post_st, SPEEX_PREPROCESS_SET_AGC, &agc);
        speex_preprocess_ctl(post_st, SPEEX_PREPROCESS_SET_AGC_LEVEL, &agc_level);
        speex_post_states_.push_back(post_st);
    }

    /* --- 2. Initialize NVIDIA Maxine Audio Effects SDK --- */
    /*
    NvAFX_Status status = NvAFX_CreateEffect(NVAFX_EFFECT_DENOISER, &nvafx_handle_);
    if (status == NVAFX_STATUS_SUCCESS) {
        NvAFX_SetU32(nvafx_handle_, NVAFX_PARAM_SAMPLE_RATE, sample_rate_);
        NvAFX_SetU32(nvafx_handle_, NVAFX_PARAM_NUM_CHANNELS, channels_);
        if (model_path != nullptr) {
            NvAFX_SetString(nvafx_handle_, NVAFX_PARAM_MODEL_PATH, model_path);
        }
        
        status = NvAFX_Load(nvafx_handle_);
        if (status == NVAFX_STATUS_SUCCESS) {
            is_nvidia_loaded_ = true;
        } else {
            std::cerr << "Failed to load NVIDIA Maxine model." << std::endl;
        }
    }
    */
}

AudioProcessor::~AudioProcessor() {
    /*
    if (nvafx_handle_) {
        NvAFX_DestroyEffect(nvafx_handle_);
    }
    */
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
    } else if (preprocess_type_ == 2) {
        // WebRTC (For future use: currently bypassed)
        std::memcpy(temp_buffer2_.data(), temp_buffer1_.data(), total_samples * sizeof(float));
    } else {
        // None (Bypass)
        std::memcpy(temp_buffer2_.data(), temp_buffer1_.data(), total_samples * sizeof(float));
    }

    /* --- 2. AI Denoiser (NVIDIA Maxine) --- */
    /*
    if (is_nvidia_loaded_) {
        const float* nv_input[1] = { temp_buffer2_.data() };
        float* nv_output[1] = { temp_buffer1_.data() };
        // NVIDIA SDK supports non-interleaved or interleaved (depending on settings)
        NvAFX_Run(nvafx_handle_, nv_input, nv_output, num_frames, channels_);
    } else {
        std::memcpy(temp_buffer1_.data(), temp_buffer2_.data(), total_samples * sizeof(float));
    }
    */
    // For mock: copy as is
    std::memcpy(temp_buffer1_.data(), temp_buffer2_.data(), total_samples * sizeof(float));

    /* --- 3. SpeexDSP (Post-process: AGC) --- */
    // float32 -> int16_t conversion
    for (int i = 0; i < total_samples; ++i) {
        float val = temp_buffer1_[i] * 32768.0f;
        val = std::clamp(val, -32768.0f, 32767.0f);
        int16_buffer_[i] = static_cast<int16_t>(val);
    }
    
    // Process each channel separately
    for (int c = 0; c < channels_; ++c) {
        SpeexPreprocessState* post_st = static_cast<SpeexPreprocessState*>(speex_post_states_[c]);
        for (int offset = 0; offset + frame_size <= num_frames; offset += frame_size) {
            for (int i = 0; i < frame_size; ++i) {
                channel_buffer[i] = int16_buffer_[(offset + i) * channels_ + c];
            }
            speex_preprocess_run(post_st, channel_buffer.data());
            for (int i = 0; i < frame_size; ++i) {
                int16_buffer_[(offset + i) * channels_ + c] = channel_buffer[i];
            }
        }
    }
    
    // int16_t -> float32 conversion and output
    for (int i = 0; i < total_samples; ++i) {
        output[i] = int16_buffer_[i] / 32768.0f;
    }
}

// --- C API Implementation ---
extern "C" {
    void* AudioProcessor_Create(int sample_rate, int channels, const char* model_path) {
        return new AudioProcessor(sample_rate, channels, model_path);
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

    void AudioProcessor_Destroy(void* processor) {
        if (processor) {
            delete static_cast<AudioProcessor*>(processor);
        }
    }
}
