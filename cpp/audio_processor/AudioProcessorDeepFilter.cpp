#include "AudioProcessor.h"
#include "AudioProcessorUtils.h"
#include "deep_filter.h"

#include <algorithm>
#include <cmath>
#include <iostream>

using namespace audio_proc_detail;

void AudioProcessor::process_df_frame(const float* frame_samples, const float* next_frame_samples,
                                      size_t df_hop, float frame_dt_sec, float inv_hop, bool& df_corrupted) {
    float in_rms = 0.0f;
    float in_peak = 0.0f;
    float next_peak = 0.0f;
    float sc_mean_energy = 0.0f;
    analyze_df_frame_input(frame_samples, next_frame_samples, df_hop, inv_hop,
                           in_rms, in_peak, next_peak, sc_mean_energy);

    if (df_corrupted || !is_deepfilter_loaded_unlocked()) {
        df_out_ring_.push(df_clean_frame_.data(), df_hop);
        return;
    }

    if (!df_noise_initialized_ && in_rms > kMinPhysicalRms) {
        df_init_min_rms_ = (df_init_min_rms_ <= 0.0f) ? in_rms : (std::min)(df_init_min_rms_, in_rms);
    }

    const float curr_nf = effective_noise_floor();
    float periodicity = -1.0f;
    if (df_speech_hold_sec_ > 0.0f || in_rms > curr_nf * 1.5f ||
        (!df_noise_initialized_ && in_rms > kMinPhysicalRms * 4.0f)) {
        periodicity = compute_voiced_periodicity();
    }

    const float in_snr_ratio = in_rms / (std::max)(curr_nf, kMinPhysicalRms);
    const bool step_rise_from_noise = (df_smooth_rms_ > 0.0f && in_rms > df_smooth_rms_ * 1.8f);
    const bool voiced_onset = (periodicity >= 0.62f && in_snr_ratio >= 1.7f);
    // 無音後の低SNR小声でも立ち上がり・有声音周期性でプリゲインを起動させつつ定常サージを抑制
    const bool allow_onset_pregain =
        (df_low_lsnr_streak_frames_ < 5) ||
        (step_rise_from_noise && in_snr_ratio >= 2.0f) ||
        voiced_onset;
    const bool in_speech_for_pregain = df_noise_initialized_
        ? ((df_speech_hold_sec_ > 0.0f && in_rms > (std::max)(curr_nf * 1.8f, kMinPhysicalRms * 5.0f)) ||
           (allow_onset_pregain && in_snr_ratio >= 1.7f &&
            (voiced_onset || in_snr_ratio >= 2.2f) && in_rms > kMinPhysicalRms * 5.0f))
        : (df_init_speech_streak_ >= 2 && in_rms > kMinPhysicalRms * 20.0f &&
           (df_init_voiced_confirmed_ || df_init_min_rms_ < kMaxAmplifiedNoiseRms));

    if (!df_pregain_speech_active_ && in_speech_for_pregain) {
        // 過去大声の残像は消しつつ微弱呼気での極端な語頭乱高下を防ぐ下限で初期化
        df_speech_peak_env_ = (std::max)(in_peak, 0.07f);
    } else if (in_peak > df_speech_peak_env_) {
        const float att_alpha = time_const_alpha(frame_dt_sec, 0.015f);
        df_speech_peak_env_ += (in_peak - df_speech_peak_env_) * att_alpha;
    } else if (in_speech_for_pregain) {
        const float rel_alpha = time_const_alpha(frame_dt_sec, 0.60f);
        df_speech_peak_env_ += (in_peak - df_speech_peak_env_) * rel_alpha;
    } else {
        const float idle_alpha = time_const_alpha(frame_dt_sec, 0.25f);
        df_speech_peak_env_ += (kPreGainTargetPeak - df_speech_peak_env_) * idle_alpha;
    }
    df_pregain_speech_active_ = in_speech_for_pregain;

    const float pregain_cap_max = df_noise_initialized_ ? kPreGainMax : kEarlyPreGainMax;
    const float raw_pre_gain = std::clamp(
        kPreGainTargetPeak / (df_speech_peak_env_ + 1.0e-4f),
        1.0f,
        pregain_cap_max
    );
    const float safe_nf_for_cap = df_noise_initialized_
        ? curr_nf
        : (df_init_voiced_confirmed_ ? curr_nf : (std::max)(curr_nf, df_init_min_rms_));
    const float noise_safe_gain_cap = (df_noise_initialized_ || !df_init_voiced_confirmed_)
        ? std::clamp(kMaxAmplifiedNoiseRms / (std::max)(safe_nf_for_cap, kMinPhysicalRms), 1.0f, pregain_cap_max)
        : pregain_cap_max;
    float target_pre_gain = in_speech_for_pregain ? (std::min)(raw_pre_gain, noise_safe_gain_cap) : 1.0f;

    const float curr_frame_cap = (in_peak > 1.0e-5f)
        ? std::clamp(0.85f / in_peak, 1.0f, pregain_cap_max)
        : pregain_cap_max;
    const float next_frame_cap = (next_peak > 1.0e-5f)
        ? std::clamp(0.85f / next_peak, 1.0f, pregain_cap_max)
        : pregain_cap_max;
    target_pre_gain = (std::min)({target_pre_gain, curr_frame_cap, next_frame_cap});

    const float start_gain = std::clamp(df_pre_gain_, 1.0f, kPreGainMax);
    float end_gain = start_gain;
    if (next_frame_cap < start_gain || curr_frame_cap < start_gain) {
        // 1フレーム先読みにより次フレームの突発ピーク手前までに10msコサインランプで安全ゲインへ確実に到達
        end_gain = std::clamp((std::min)(target_pre_gain, next_frame_cap), 1.0f, kPreGainMax);
    } else {
        const float pre_alpha = (target_pre_gain < start_gain)
            ? time_const_alpha(frame_dt_sec, 0.15f)
            : time_const_alpha(frame_dt_sec, 0.07f);
        end_gain = std::clamp(start_gain + (target_pre_gain - start_gain) * pre_alpha, 1.0f, kPreGainMax);
    }

    for (size_t i = 0; i < df_hop; ++i) {
        const float frac = static_cast<float>(i + 1) * inv_hop;
        const float cos_t = 0.5f - 0.5f * std::cos(kPiF * frac);
        const float g_i = start_gain + (end_gain - start_gain) * cos_t;
        df_gain_frame_[i] = g_i;
        const float amplified = df_clean_frame_[i] * g_i + next_micro_dither(dither_rng_);
        df_input_frame_[i] = std::clamp(amplified, -0.99f, 0.99f);
    }
    df_pre_gain_ = end_gain;
    df_gain_delay_ring_.push(df_gain_frame_.data(), df_hop);

    // 事前取得特徴量(自己相関周期性・残留音声エネルギー)で0フレーム遅れの保護判定を行い推論前に適用
    const float est_in_speech = std::sqrt((std::max)(in_rms * in_rms - curr_nf * curr_nf, 0.0f));
    const bool above_noise_gate = (in_rms > (std::max)(curr_nf * 1.8f, kMinPhysicalRms * 8.0f));
    const bool is_low_snr_region = (in_rms < (std::max)(curr_nf * 6.5f, kMinPhysicalRms * 25.0f));
    const bool is_tail_candidate =
        (df_in_speech_env_ > 0.0f && in_rms < df_in_speech_env_ * 0.45f && is_low_snr_region &&
         (est_in_speech > curr_nf * 2.1f || periodicity >= 0.65f));
    if (above_noise_gate && is_tail_candidate) {
        df_tail_fade_streak_ = (std::min)(df_tail_fade_streak_ + 1, 100);
    } else {
        df_tail_fade_streak_ = 0;
    }

    float target_fade_level = 0.0f;
    if (above_noise_gate && (df_speech_hold_sec_ > 0.0f || periodicity >= 0.68f)) {
        const bool is_onset_whisper =
            (df_speech_hold_sec_ <= 0.0f && periodicity >= 0.68f && is_low_snr_region);
        const bool is_extreme_whisper =
            (df_in_speech_env_ > curr_nf * 1.8f && df_in_speech_env_ < curr_nf * 3.5f);
        const bool is_tail_fade = (df_tail_fade_streak_ >= 2);
        if (is_onset_whisper || is_extreme_whisper || is_tail_fade) {
            target_fade_level = 1.0f;
        }
    }
    const float protect_tau = (target_fade_level > df_fade_protect_level_)
        ? 0.015f
        : (above_noise_gate ? 0.15f : 0.04f);
    const float protect_alpha = time_const_alpha(frame_dt_sec, protect_tau);
    df_fade_protect_level_ += (target_fade_level - df_fade_protect_level_) * protect_alpha;

    DFState* state = df_states_[0];

    // 35dBからノイズ連動安全下限(>=24dB)へ滑らかに緩和しポンピングと遅れを解消
    const float nf_db = 20.0f * std::log10((std::max)(curr_nf, kMinPhysicalRms));
    const float target_res_db = 20.0f * std::log10(kTargetResidualNoiseRms);
    const float safe_min_atten_db = std::clamp(
        nf_db - target_res_db,
        kMinSafeAttenLimDb,
        kDefaultAttenLimDb - 3.0f
    );
    const float target_atten_db =
        kDefaultAttenLimDb - (kDefaultAttenLimDb - safe_min_atten_db) * df_fade_protect_level_;
    const float atten_alpha = (target_atten_db < df_current_atten_lim_db_)
        ? time_const_alpha(frame_dt_sec, 0.015f)
        : time_const_alpha(frame_dt_sec, 0.05f);
    df_current_atten_lim_db_ += (target_atten_db - df_current_atten_lim_db_) * atten_alpha;
    if (df_last_applied_atten_lim_db_ < 0.0f ||
        std::fabs(df_current_atten_lim_db_ - df_last_applied_atten_lim_db_) >= 0.25f) {
        df_set_atten_lim(state, df_current_atten_lim_db_);
        df_last_applied_atten_lim_db_ = df_current_atten_lim_db_;
    }

    std::fill(df_output_frame_.begin(), df_output_frame_.end(), 0.0f);
    const float lsnr = df_process_frame(state, df_input_frame_.data(), df_output_frame_.data());
    df_state_dirty_ = true;
    if (lsnr < 0.8f) {
        df_low_lsnr_streak_frames_ = (std::min)(df_low_lsnr_streak_frames_ + 1, 10000);
    } else {
        df_low_lsnr_streak_frames_ = 0;
    }
    while (char* msg = df_next_log_msg(state)) {
        df_free_log_msg(msg);
    }

    update_df_noise_and_speech_state(in_rms, sc_mean_energy, lsnr, frame_dt_sec, periodicity);

    if (df_gain_delay_ring_.size() >= df_hop) {
        df_gain_delay_ring_.pop(df_delayed_gain_frame_.data(), df_hop);
    } else {
        std::fill(df_delayed_gain_frame_.begin(), df_delayed_gain_frame_.end(), 1.0f);
    }

    bool has_non_finite = false;
    for (size_t i = 0; i < df_hop; ++i) {
        const float g_out = (std::max)(df_delayed_gain_frame_[i], 1.0f);
        const float s = df_output_frame_[i] / g_out;
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
        destroy_deepfilter_state_unlocked();
        enable_speex_denoise_unlocked(true);
        df_corrupted = true;
    } else {
        df_out_ring_.push(df_output_frame_.data(), df_hop);
    }
}
