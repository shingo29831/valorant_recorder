#pragma once

#include <vector>
#include <memory>

// DeepFilterNet state forward declaration
struct DFState;

class AudioProcessor {
public:
    AudioProcessor(int sample_rate, int channels, const char* model_path);
    ~AudioProcessor();

    void process(const float* input, float* output, int num_frames);
    void set_preprocess_type(int type);
    void set_denoise_type(int type);

private:
    int sample_rate_;
    int preprocess_type_; // 0: None, 1: SpeexDSP, 2: WebRTC
    int denoise_type_;    // 0: None, 1: DeepFilterNet
    int channels_;

    // SpeexDSP state pointers (kept per channel)
    std::vector<void*> speex_pre_states_;
    std::vector<void*> speex_post_states_;

    // DeepFilterNet state (one per channel)
    std::vector<DFState*> df_states_;

    // Internal buffers (float)
    std::vector<float> temp_buffer1_;
    std::vector<float> temp_buffer2_;
    
    // Internal buffers (int16_t, for SpeexDSP)
    std::vector<int16_t> int16_buffer_;

    // 2nd-order Butterworth High-Pass Filter (80Hz) state
    float hpf_x1_ = 0.0f;
    float hpf_x2_ = 0.0f;
    float hpf_y1_ = 0.0f;
    float hpf_y2_ = 0.0f;
    float hpf_b0_ = 1.0f;
    float hpf_b1_ = 0.0f;
    float hpf_b2_ = 0.0f;
    float hpf_a1_ = 0.0f;
    float hpf_a2_ = 0.0f;
};

extern "C" {
    // C interface for calling from Python (ctypes) etc.
#ifdef _WIN32
    __declspec(dllexport) void* AudioProcessor_Create(int sample_rate, int channels, const char* model_path);
    __declspec(dllexport) void AudioProcessor_Process(void* processor, const float* input, float* output, int num_frames);
    __declspec(dllexport) void AudioProcessor_SetPreProcessType(void* processor, int type);
    __declspec(dllexport) void AudioProcessor_SetDenoiseType(void* processor, int type);
    __declspec(dllexport) void AudioProcessor_Destroy(void* processor);
#else
    void* AudioProcessor_Create(int sample_rate, int channels, const char* model_path);
    void AudioProcessor_Process(void* processor, const float* input, float* output, int num_frames);
    void AudioProcessor_SetPreProcessType(void* processor, int type);
    void AudioProcessor_SetDenoiseType(void* processor, int type);
    void AudioProcessor_Destroy(void* processor);
#endif
}
