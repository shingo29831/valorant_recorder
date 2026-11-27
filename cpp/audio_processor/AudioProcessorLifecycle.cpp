#include "AudioProcessor.h"
#include "DeepFilterProbe.h"
#include "deep_filter.h"

#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <cstring>
#include <exception>
#include <iostream>
#include <mutex>
#include <speex/speex_preprocess.h>
#include <thread>
#include <vector>

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

DFState* AudioProcessor::create_deepfilter_instance(const std::string& model_path, int sample_rate,
                                                   int& out_frame_size, int& out_delay_samples) {
    if (model_path.empty()) {
        return nullptr;
    }
    try {
        const audio_proc_detail::DelayProbeResult probe =
            audio_proc_detail::measure_df_delay_samples(model_path, sample_rate, kMaxDfDelayFrames);
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
