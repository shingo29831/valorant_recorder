#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <memory>
#include <mutex>
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
        push_constant(0.0f, count);
    }

    void push_constant(float value, size_t count) {
        if (count == 0) return;
        ensure_capacity(size_ + count);
        const size_t cap = buf_.size();
        const size_t tail = (head_ + size_) % cap;
        const size_t first = (std::min)(count, cap - tail);
        std::fill(buf_.begin() + tail, buf_.begin() + tail + first, value);
        if (count > first) {
            std::fill(buf_.begin(), buf_.begin() + (count - first), value);
        }
        size_ += count;
    }

    size_t peek(float* dst, size_t count) const {
        if (count == 0 || !dst || size_ == 0) return 0;
        const size_t to_read = (std::min)(count, size_);
        const size_t cap = buf_.size();
        const size_t first = (std::min)(to_read, cap - head_);
        std::memcpy(dst, buf_.data() + head_, first * sizeof(float));
        if (to_read > first) {
            std::memcpy(dst + first, buf_.data(), (to_read - first) * sizeof(float));
        }
        return to_read;
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
    struct BgRecreateContext;

    static constexpr int kMaxDfDelayFrames = 8;
    static constexpr int kVoicedHistSize = 512;
    static constexpr int kNoiseInitFrames = 8;
    static constexpr int kMaxNoiseInitWaitFrames = 20;
    static constexpr float kDefaultAttenLimDb = 35.0f;
    static constexpr float kMinSafeAttenLimDb = 24.0f;
    static constexpr float kDefaultNoiseFloorRms = 1.0e-4f;
    static constexpr float kPreGainTargetPeak = 0.18f;

    static DFState* create_deepfilter_instance(const std::string& model_path, int sample_rate,
                                               int& out_frame_size, int& out_delay_samples);
    void destroy_deepfilter_state_unlocked();
    void reset_df_buffers_unlocked();
    void enable_speex_denoise_unlocked(bool enable);
    bool is_deepfilter_loaded_unlocked() const;
    bool is_deepfilter_active_unlocked() const;
    float compute_voiced_periodicity() const;
    float effective_noise_floor() const;
    void process_df_frame(const float* frame_samples, const float* next_frame_samples,
                          size_t df_hop, float frame_dt_sec, float inv_hop, bool& df_corrupted);

    mutable std::mutex mtx_;
    std::shared_ptr<BgRecreateContext> bg_ctx_;
    int sample_rate_;
    std::atomic<int> preprocess_type_; // 0: None, 1: SpeexDSP, 2: WebRTC
    std::atomic<int> denoise_type_;    // 0: None, 1: DeepFilterNet
    std::atomic<bool> df_loaded_{false};
    int channels_;
    uint64_t df_load_seq_ = 0;

    // SpeexDSP state pointers (kept per channel)
    std::vector<void*> speex_pre_states_;
    std::vector<void*> speex_post_states_;

    // DeepFilterNet state and ring buffers
    std::vector<DFState*> df_states_;
    std::string df_model_path_;
    int df_frame_size_ = 480;
    int df_delay_samples_ = 1440;
    bool df_lookahead_primed_ = false;
    bool df_non_aligned_primed_ = false;
    bool df_state_dirty_ = false;
    uint32_t dither_rng_ = 0x12345678u;

    std::array<float, kVoicedHistSize> df_voiced_hist_{};
    int df_voiced_head_ = 0;
    int df_voiced_count_ = 0;
    int df_ds_step_ = 4;
    int df_ds_accum_ = 0;

    // サイドチェイン専用 75Hz 4次HPF + 1000Hz LPF (低い男性声を残しつつハム基本波と折り返しを遮断)
    float sc_hpf_b0_ = 1.0f, sc_hpf_b1_ = 0.0f, sc_hpf_b2_ = 0.0f, sc_hpf_a1_ = 0.0f, sc_hpf_a2_ = 0.0f;
    float sc_hpf1_x1_ = 0.0f, sc_hpf1_x2_ = 0.0f, sc_hpf1_y1_ = 0.0f, sc_hpf1_y2_ = 0.0f;
    float sc_hpf2_x1_ = 0.0f, sc_hpf2_x2_ = 0.0f, sc_hpf2_y1_ = 0.0f, sc_hpf2_y2_ = 0.0f;
    float sc_lpf_b0_ = 1.0f, sc_lpf_b1_ = 0.0f, sc_lpf_b2_ = 0.0f, sc_lpf_a1_ = 0.0f, sc_lpf_a2_ = 0.0f;
    float sc_lpf_x1_ = 0.0f, sc_lpf_x2_ = 0.0f, sc_lpf_y1_ = 0.0f, sc_lpf_y2_ = 0.0f;
    float df_sc_noise_energy_ = 0.0f;

    std::array<float, kNoiseInitFrames> df_noise_init_samples_{};
    int df_total_frames_ = 0;
    int df_noise_init_count_ = 0;
    int df_noise_init_wait_frames_ = 0;
    int df_noise_init_low_streak_ = 0;
    int df_init_speech_streak_ = 0;
    bool df_init_voiced_confirmed_ = false;
    float df_init_min_rms_ = 0.0f;

    bool df_pregain_speech_active_ = false;
    float df_speech_peak_env_ = kPreGainTargetPeak;
    float df_pre_gain_ = 1.0f;
    float df_noise_floor_ = kDefaultNoiseFloorRms;
    float df_fast_noise_floor_ = kDefaultNoiseFloorRms;
    bool df_noise_initialized_ = false;
    float df_smooth_rms_ = 0.0f;
    float df_min_block_rms_ = 1.0e9f;
    float df_prev_block_min_rms_ = 1.0e9f;
    float df_min_block_elapsed_sec_ = 0.0f;
    int df_low_lsnr_streak_frames_ = 0;
    int df_non_speech_low_streak_ = 0;

    int df_onset_confirm_frames_ = 0;
    int df_tail_fade_streak_ = 0;
    float df_speech_hold_sec_ = 0.0f;
    bool df_voiced_hold_active_ = false;
    float df_non_voiced_low_lsnr_sec_ = 0.0f;
    float df_voiced_low_lsnr_sec_ = 0.0f;
    float df_speech_ref_rms_ = 0.0f;
    float df_in_speech_env_ = 0.0f;
    float df_fade_protect_level_ = 0.0f;
    float df_current_atten_lim_db_ = kDefaultAttenLimDb;
    float df_last_applied_atten_lim_db_ = -1.0f;

    // Internal buffers (float)
    std::vector<float> temp_buffer1_;
    std::vector<float> temp_buffer2_;
    FloatRingBuffer df_in_ring_;
    FloatRingBuffer df_out_ring_;
    FloatRingBuffer df_gain_delay_ring_;
    std::vector<float> df_raw_frame_;
    std::vector<float> df_next_raw_frame_;
    std::vector<float> df_clean_frame_;
    std::vector<float> df_input_frame_;
    std::vector<float> df_output_frame_;
    std::vector<float> df_gain_frame_;
    std::vector<float> df_delayed_gain_frame_;

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
