#pragma once

#include <vector>
#include <memory>

class AudioProcessor {
public:
    AudioProcessor(int sample_rate, int channels, const char* model_path);
    ~AudioProcessor();

    void process(const float* input, float* output, int num_frames);
    void set_preprocess_type(int type);

private:
    int sample_rate_;
    int preprocess_type_; // 0: None, 1: SpeexDSP, 2: WebRTC
    int channels_;

    // SpeexDSP state pointers (kept per channel)
    std::vector<void*> speex_pre_states_;
    std::vector<void*> speex_post_states_;

    // NVIDIA Maxine Handle
    void* nvafx_handle_;

    bool is_nvidia_loaded_;

    // Internal buffers (float)
    std::vector<float> temp_buffer1_;
    std::vector<float> temp_buffer2_;
    
    // Internal buffers (int16_t, for SpeexDSP)
    std::vector<int16_t> int16_buffer_;
};

extern "C" {
    // C interface for calling from Python (ctypes) etc.
#ifdef _WIN32
    __declspec(dllexport) void* AudioProcessor_Create(int sample_rate, int channels, const char* model_path);
    __declspec(dllexport) void AudioProcessor_Process(void* processor, const float* input, float* output, int num_frames);
    __declspec(dllexport) void AudioProcessor_SetPreProcessType(void* processor, int type);
    __declspec(dllexport) void AudioProcessor_Destroy(void* processor);
#else
    void* AudioProcessor_Create(int sample_rate, int channels, const char* model_path);
    void AudioProcessor_Process(void* processor, const float* input, float* output, int num_frames);
    void AudioProcessor_SetPreProcessType(void* processor, int type);
    void AudioProcessor_Destroy(void* processor);
#endif
}
