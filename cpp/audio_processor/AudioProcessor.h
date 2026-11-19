#pragma once

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

// DeepFilterNet state forward declaration
struct DFState;

class FloatRingBuffer {
public:
    void clear() {
        head_ = 0;
        size_ = 0;
    }
    size_t size() const { return size_; }
    bool empty() const { return size_ == 0; }

    void push_zeros(size_t count) {
        if (count == 0) return;
        ensure_capacity(size_ + count);
        const size_t cap = buf_.size();
        const size_t tail = (head_ + size_) % cap;
        const size_t first = (std::min)(count, cap - tail);
        std::fill(buf_.begin() + tail, buf_.begin() + tail + first, 0.0f);
        if (count > first) {
            std::fill(buf_.begin(), buf_.begin() + (count - first), 0.0f);
        }
        size_ += count;
    }

    void push(const float* data, size_t count) {
        if (count == 0 || !data) return;
        ensure_capacity(size_ + count);
        const size_t cap = buf_.size();
        const size_t tail = (head_ + size_) % cap;
        const size_t first = (std::min)(count, cap - tail);
        std::memcpy(buf_.data() + tail, data, first * sizeof(float));
        if (count > first) {
            std::memcpy(buf_.data(), data + first, (count - first) * sizeof(float));
        }
        size_ += count;
    }

    void pop(float* dst, size_t count) {
        if (count == 0 || !dst || size_ == 0) return;
        const size_t to_read = (std::min)(count, size_);
        const size_t cap = buf_.size();
        const size_t first = (std::min)(to_read, cap - head_);
        std::memcpy(dst, buf_.data() + head_, first * sizeof(float));
        if (to_read > first) {
            std::memcpy(dst + first, buf_.data(), (to_read - first) * sizeof(float));
        }
        head_ = (head_ + to_read) % cap;
        size_ -= to_read;
        if (size_ == 0) head_ = 0;
    }

private:
    void ensure_capacity(size_t required) {
        if (required <= buf_.size()) return;
        size_t new_cap = buf_.empty() ? 1024 : buf_.size() * 2;
        while (new_cap < required) new_cap *= 2;
        std::vector<float> next(new_cap, 0.0f);
        if (size_ > 0) {
            const size_t cap = buf_.size();
            const size_t first = (std::min)(size_, cap - head_);
            std::memcpy(next.data(), buf_.data() + head_, first * sizeof(float));
            if (size_ > first) {
                std::memcpy(next.data() + first, buf_.data(), (size_ - first) * sizeof(float));
            }
        }
        buf_.swap(next);
        head_ = 0;
    }

    std::vector<float> buf_;
    size_t head_ = 0;
    size_t size_ = 0;
};

class AudioProcessor {
public:
    AudioProcessor(int sample_rate, int channels, const char* model_path);
    ~AudioProcessor();

    void process(const float* input, float* output, int num_frames);
    void set_preprocess_type(int type);
    void set_denoise_type(int type);
    bool load_deepfilter(const char* model_path);
    bool is_deepfilter_loaded() const;
    bool is_deepfilter_active() const;

private:
    bool init_deepfilter();
    void destroy_deepfilter_state();
    void reset_df_buffers();
    void enable_speex_denoise(bool enable);

    int sample_rate_;
    int preprocess_type_; // 0: None, 1: SpeexDSP, 2: WebRTC
    int denoise_type_;    // 0: None, 1: DeepFilterNet
    int channels_;

    // SpeexDSP state pointers (kept per channel)
    std::vector<void*> speex_pre_states_;
    std::vector<void*> speex_post_states_;

    // DeepFilterNet state and ring buffers
    std::vector<DFState*> df_states_;
    std::string df_model_path_;
    int df_frame_size_ = 480;
    bool df_non_aligned_primed_ = false;
    uint32_t dither_rng_ = 0x12345678u;

    // Internal buffers (float)
    std::vector<float> temp_buffer1_;
    std::vector<float> temp_buffer2_;
    FloatRingBuffer df_in_ring_;
    FloatRingBuffer df_out_ring_;
    std::vector<float> df_raw_frame_;
    std::vector<float> df_clean_frame_;
    std::vector<float> df_input_frame_;
    std::vector<float> df_output_frame_;

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
    __declspec(dllexport) int AudioProcessor_LoadDeepFilter(void* processor, const char* model_path);
    __declspec(dllexport) int AudioProcessor_IsDeepFilterLoaded(void* processor);
    __declspec(dllexport) int AudioProcessor_IsDeepFilterActive(void* processor);
    __declspec(dllexport) void AudioProcessor_Destroy(void* processor);
#else
    void* AudioProcessor_Create(int sample_rate, int channels, const char* model_path);
    void AudioProcessor_Process(void* processor, const float* input, float* output, int num_frames);
    void AudioProcessor_SetPreProcessType(void* processor, int type);
    void AudioProcessor_SetDenoiseType(void* processor, int type);
    int AudioProcessor_LoadDeepFilter(void* processor, const char* model_path);
    int AudioProcessor_IsDeepFilterLoaded(void* processor);
    int AudioProcessor_IsDeepFilterActive(void* processor);
    void AudioProcessor_Destroy(void* processor);
#endif
}
