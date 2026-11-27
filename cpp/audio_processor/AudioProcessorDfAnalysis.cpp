#include "AudioProcessor.h"
#include "AudioProcessorUtils.h"

#include <algorithm>
#include <cmath>

using namespace audio_proc_detail;

float AudioProcessor::compute_voiced_periodicity() const {
    const int eff_sr = (std::max)(4000, sample_rate_ / (std::max)(1, df_ds_step_));
    const int win_len = (std::max)(60, static_cast<int>(0.015f * static_cast<float>(eff_sr)));
    const int min_lag = (std::max)(8, static_cast<int>(static_cast<float>(eff_sr) / 360.0f));
    const int max_lag = (std::min)(kVoicedHistSize - win_len - 1, static_cast<int>(static_cast<float>(eff_sr) / 75.0f));
    if (max_lag <= min_lag || df_voiced_count_ < (win_len + max_lag)) {
        return 0.0f;
    }

    auto read_recent = [this](int samples_back) -> float {
        const int idx = (df_voiced_head_ + kVoicedHistSize - 1 - samples_back) % kVoicedHistSize;
        return df_voiced_hist_[idx];
    };

    float sum_a = 0.0f;
    for (int i = 0; i < win_len; ++i) {
        sum_a += read_recent(i);
    }
    const float mean_a = sum_a / static_cast<float>(win_len);

    float e0 = 0.0f;
    for (int i = 0; i < win_len; ++i) {
        const float da = read_recent(i) - mean_a;
        e0 += da * da;
    }
    // 非発話時の定常ハム・ファン成分以上のエネルギーを要求し誤ロックを防止
    const float mean_e0 = e0 / static_cast<float>(win_len);
    const float min_voiced_energy = (std::max)(1.0e-8f, df_sc_noise_energy_ * 3.5f);
    if (mean_e0 < min_voiced_energy) {
        return 0.0f;
    }

    float best_r = 0.0f;
    for (int lag = min_lag; lag <= max_lag; ++lag) {
        float sum_b = 0.0f;
        for (int i = 0; i < win_len; ++i) {
            sum_b += read_recent(i + lag);
        }
        const float mean_b = sum_b / static_cast<float>(win_len);

        float num = 0.0f;
        float e1 = 0.0f;
        for (int i = 0; i < win_len; ++i) {
            const float da = read_recent(i) - mean_a;
            const float db = read_recent(i + lag) - mean_b;
            num += da * db;
            e1 += db * db;
        }
        const float denom = std::sqrt(e0 * e1) + 1.0e-10f;
        best_r = (std::max)(best_r, num / denom);
    }
    return best_r;
}

float AudioProcessor::effective_noise_floor() const {
    if (df_noise_initialized_) {
        return (std::max)(df_noise_floor_, df_fast_noise_floor_);
    }
    if (df_noise_init_count_ > 0) {
        return compute_robust_noise_floor(df_noise_init_samples_, df_noise_init_count_);
    }
    if (df_init_min_rms_ > 0.0f) {
        if (df_init_speech_streak_ >= 2 && df_init_voiced_confirmed_) {
            return (std::max)(kMinPhysicalRms, df_init_min_rms_ * 0.1f);
        }
        // 未確定かつ有声音未確認時は大音量定常ノイズを過小評価して誤発話連鎖を起こさない
        return (std::max)(kMinPhysicalRms, df_init_min_rms_ * 0.8f);
    }
    return kDefaultNoiseFloorRms;
}

void AudioProcessor::analyze_df_frame_input(const float* frame_samples, const float* next_frame_samples,
                                            size_t df_hop, float inv_hop, float& out_in_rms,
                                            float& out_in_peak, float& out_next_peak, float& out_sc_mean_energy) {
    float sum_sq_in = 0.0f;
    float in_peak = 0.0f;
    float next_peak = 0.0f;
    float sc_sum_sq = 0.0f;
    int sc_samples = 0;

    for (size_t i = 0; i < df_hop; ++i) {
        const float clean = sanitize_soft_clip(frame_samples[i]);
        df_clean_frame_[i] = clean;
        sum_sq_in += clean * clean;
        in_peak = (std::max)(in_peak, std::fabs(clean));

        const float next_clean = sanitize_soft_clip(next_frame_samples[i]);
        next_peak = (std::max)(next_peak, std::fabs(next_clean));

        // サイドチェイン専用 75Hz 4次HPF + 1000Hz LPF で低い男性声基音を保ちつつハム・折り返しを遮断
        float yh1 = sc_hpf_b0_ * clean + sc_hpf_b1_ * sc_hpf1_x1_ + sc_hpf_b2_ * sc_hpf1_x2_
                  - sc_hpf_a1_ * sc_hpf1_y1_ - sc_hpf_a2_ * sc_hpf1_y2_;
        if (!std::isfinite(yh1)) {
            sc_hpf1_x1_ = sc_hpf1_x2_ = sc_hpf1_y1_ = sc_hpf1_y2_ = 0.0f;
            yh1 = 0.0f;
        } else {
            sc_hpf1_x2_ = sc_hpf1_x1_;
            sc_hpf1_x1_ = clean;
            sc_hpf1_y2_ = sc_hpf1_y1_;
            sc_hpf1_y1_ = yh1;
        }

        float yh2 = sc_hpf_b0_ * yh1 + sc_hpf_b1_ * sc_hpf2_x1_ + sc_hpf_b2_ * sc_hpf2_x2_
                  - sc_hpf_a1_ * sc_hpf2_y1_ - sc_hpf_a2_ * sc_hpf2_y2_;
        if (!std::isfinite(yh2)) {
            sc_hpf2_x1_ = sc_hpf2_x2_ = sc_hpf2_y1_ = sc_hpf2_y2_ = 0.0f;
            yh2 = 0.0f;
        } else {
            sc_hpf2_x2_ = sc_hpf2_x1_;
            sc_hpf2_x1_ = yh1;
            sc_hpf2_y2_ = sc_hpf2_y1_;
            sc_hpf2_y1_ = yh2;
        }

        float yl = sc_lpf_b0_ * yh2 + sc_lpf_b1_ * sc_lpf_x1_ + sc_lpf_b2_ * sc_lpf_x2_
                 - sc_lpf_a1_ * sc_lpf_y1_ - sc_lpf_a2_ * sc_lpf_y2_;
        if (!std::isfinite(yl)) {
            sc_lpf_x1_ = sc_lpf_x2_ = sc_lpf_y1_ = sc_lpf_y2_ = 0.0f;
            yl = 0.0f;
        } else {
            sc_lpf_x2_ = sc_lpf_x1_;
            sc_lpf_x1_ = yh2;
            sc_lpf_y2_ = sc_lpf_y1_;
            sc_lpf_y1_ = yl;
        }

        ++df_ds_accum_;
        if (df_ds_accum_ >= df_ds_step_) {
            df_ds_accum_ = 0;
            df_voiced_hist_[df_voiced_head_] = yl;
            df_voiced_head_ = (df_voiced_head_ + 1) % kVoicedHistSize;
            if (df_voiced_count_ < kVoicedHistSize) {
                ++df_voiced_count_;
            }
            sc_sum_sq += yl * yl;
            ++sc_samples;
        }
    }

    out_in_rms = std::sqrt(sum_sq_in * inv_hop);
    out_in_peak = in_peak;
    out_next_peak = next_peak;
    out_sc_mean_energy = (sc_samples > 0) ? (sc_sum_sq / static_cast<float>(sc_samples)) : 0.0f;
    df_total_frames_ = (std::min)(df_total_frames_ + 1, 1000000);
}

void AudioProcessor::update_df_noise_and_speech_state(float in_rms, float sc_mean_energy, float lsnr,
                                                      float frame_dt_sec, float& periodicity) {
    // 初期ノイズフロア収集: lsnr >= 1.3f すべてで4フレーム未満の断片をロールバックし真の無音区間のみで確定
    if (!df_noise_initialized_ && in_rms > kMinPhysicalRms) {
        if (periodicity >= 0.65f) {
            df_init_voiced_confirmed_ = true;
        }
        if (lsnr >= 2.2f && in_rms > kMinPhysicalRms * 20.0f &&
            (df_total_frames_ >= 5 || periodicity >= 0.65f)) {
            df_init_speech_streak_ = (std::min)(df_init_speech_streak_ + 1, 1000);
        } else if (lsnr < 1.0f) {
            df_init_speech_streak_ = 0;
        }

        if (lsnr < 1.3f) {
            df_noise_init_low_streak_ = (std::min)(df_noise_init_low_streak_ + 1, 1000);
            if (df_noise_init_count_ < kNoiseInitFrames) {
                df_noise_init_samples_[df_noise_init_count_++] = in_rms;
            }
        } else {
            if (df_noise_init_low_streak_ > 0 && df_noise_init_low_streak_ < 4) {
                const int rollback = (std::min)(df_noise_init_low_streak_, df_noise_init_count_);
                df_noise_init_count_ -= rollback;
                if (df_noise_init_count_ == 0) {
                    df_noise_init_wait_frames_ = 0;
                }
            }
            df_noise_init_low_streak_ = 0;
        }

        if (df_noise_init_count_ > 0) {
            ++df_noise_init_wait_frames_;
        }
        if (df_noise_init_count_ >= kNoiseInitFrames ||
            (df_noise_init_wait_frames_ >= kMaxNoiseInitWaitFrames && df_noise_init_count_ >= 4)) {
            df_noise_floor_ = compute_robust_noise_floor(df_noise_init_samples_, df_noise_init_count_);
            df_smooth_rms_ = df_noise_floor_;
            df_fast_noise_floor_ = df_noise_floor_;
            df_min_block_rms_ = df_noise_floor_;
            df_prev_block_min_rms_ = df_noise_floor_;
            df_noise_initialized_ = true;
        }
    }

    const float nf_ref = effective_noise_floor();
    const float snr_ratio = in_rms / (std::max)(nf_ref, kMinPhysicalRms);
    const bool strong_onset = (lsnr >= 1.8f && in_rms > kMinPhysicalRms * 4.0f && snr_ratio >= 2.2f);
    const bool weak_onset_cand = (lsnr >= 0.8f && in_rms > kMinPhysicalRms * 5.0f && snr_ratio >= 2.8f);
    if (weak_onset_cand) {
        df_onset_confirm_frames_ = (std::min)(df_onset_confirm_frames_ + 1, 10);
    } else {
        df_onset_confirm_frames_ = 0;
    }
    const bool clear_speech = strong_onset || (df_onset_confirm_frames_ >= 2);

    if (clear_speech) {
        if (df_speech_ref_rms_ <= 0.0f) {
            df_speech_ref_rms_ = in_rms;
        } else {
            const float ref_alpha = (in_rms > df_speech_ref_rms_)
                ? time_const_alpha(frame_dt_sec, 0.05f)
                : time_const_alpha(frame_dt_sec, 0.60f);
            df_speech_ref_rms_ += (in_rms - df_speech_ref_rms_) * ref_alpha;
        }
        const float env_alpha = (in_rms > df_in_speech_env_)
            ? time_const_alpha(frame_dt_sec, 0.03f)
            : time_const_alpha(frame_dt_sec, 0.30f);
        df_in_speech_env_ += (in_rms - df_in_speech_env_) * env_alpha;

        df_speech_hold_sec_ = 0.22f;
        df_voiced_hold_active_ = false;
        df_non_voiced_low_lsnr_sec_ = 0.0f;
        df_voiced_low_lsnr_sec_ = 0.0f;
    } else if (df_speech_hold_sec_ > 0.0f) {
        const bool energy_sustained =
            (in_rms > (std::max)(nf_ref * 2.5f, kMinPhysicalRms * 4.0f)) &&
            (in_rms > df_speech_ref_rms_ * 0.25f);
        if (energy_sustained) {
            if (periodicity < 0.0f) {
                periodicity = compute_voiced_periodicity();
            }
            if (periodicity >= 0.60f && df_voiced_low_lsnr_sec_ < 2.50f) {
                df_voiced_low_lsnr_sec_ += frame_dt_sec;
                df_speech_hold_sec_ = (std::max)(df_speech_hold_sec_, 0.16f);
                df_voiced_hold_active_ = true;
                df_non_voiced_low_lsnr_sec_ = 0.0f;
            } else {
                df_voiced_hold_active_ = false;
                df_non_voiced_low_lsnr_sec_ += frame_dt_sec;
                if (df_non_voiced_low_lsnr_sec_ < 0.22f) {
                    df_speech_hold_sec_ = (std::max)(df_speech_hold_sec_, 0.12f);
                } else {
                    df_speech_hold_sec_ = (std::max)(0.0f, df_speech_hold_sec_ - frame_dt_sec);
                }
            }
        } else {
            df_voiced_hold_active_ = false;
            df_speech_hold_sec_ = (std::max)(0.0f, df_speech_hold_sec_ - frame_dt_sec);
        }
    } else {
        df_voiced_hold_active_ = false;
        df_non_voiced_low_lsnr_sec_ = 0.0f;
        df_voiced_low_lsnr_sec_ = 0.0f;
        df_speech_ref_rms_ = 0.0f;
        df_in_speech_env_ = 0.0f;
    }

    const bool speech_active = (df_speech_hold_sec_ > 0.0f);
    if (!speech_active) {
        df_voiced_hold_active_ = false;
        df_speech_ref_rms_ = 0.0f;
        df_in_speech_env_ = 0.0f;
    }

    if (df_noise_initialized_ && in_rms > kMinPhysicalRms) {
        if (!speech_active && in_rms < df_noise_floor_) {
            const float down_alpha = time_const_alpha(frame_dt_sec, 0.15f);
            df_noise_floor_ += ((std::max)(in_rms, kMinPhysicalRms) - df_noise_floor_) * down_alpha;
        }
        // 発話ホールド中の無声子音誤追従を防ぐため、非発話低LSNR区間でのみサージ追従・ブロック推定を行う
        if (!speech_active && lsnr < 0.8f && df_onset_confirm_frames_ == 0) {
            df_non_speech_low_streak_ = (std::min)(df_non_speech_low_streak_ + 1, 10000);
            const float rms_alpha = time_const_alpha(frame_dt_sec, 0.04f);
            df_smooth_rms_ += (in_rms - df_smooth_rms_) * rms_alpha;

            if (df_non_speech_low_streak_ >= 6 && df_smooth_rms_ > df_fast_noise_floor_) {
                const float surge_alpha = time_const_alpha(frame_dt_sec, 0.03f);
                df_fast_noise_floor_ += (df_smooth_rms_ - df_fast_noise_floor_) * surge_alpha;
            }

            if (sc_mean_energy > 0.0f) {
                const float sc_alpha = time_const_alpha(frame_dt_sec, 0.25f);
                if (df_sc_noise_energy_ <= 0.0f) {
                    df_sc_noise_energy_ = sc_mean_energy;
                } else {
                    df_sc_noise_energy_ += (sc_mean_energy - df_sc_noise_energy_) * sc_alpha;
                }
            }
            df_min_block_rms_ = (std::min)(df_min_block_rms_, df_smooth_rms_);
            df_min_block_elapsed_sec_ += frame_dt_sec;
            if (df_min_block_elapsed_sec_ >= 0.60f) {
                if (df_prev_block_min_rms_ < 1.0e8f) {
                    const float robust_min = (std::min)(df_min_block_rms_, df_prev_block_min_rms_);
                    if (robust_min > df_noise_floor_) {
                        df_noise_floor_ += (robust_min - df_noise_floor_) * 0.45f;
                    }
                } else if (df_low_lsnr_streak_frames_ >= 15 && df_min_block_rms_ > df_noise_floor_) {
                    df_noise_floor_ += (df_min_block_rms_ - df_noise_floor_) * 0.45f;
                }
                df_prev_block_min_rms_ = df_min_block_rms_;
                df_min_block_rms_ = 1.0e9f;
                df_min_block_elapsed_sec_ = 0.0f;
            }
        } else {
            if (clear_speech || lsnr >= 1.2f) {
                df_non_speech_low_streak_ = 0;
            } else if (df_non_speech_low_streak_ > 0) {
                --df_non_speech_low_streak_;
            }
            // サージ保護フロアの1フレーム段差急落を防ぐため連続減衰で定常フロアへ戻す
            if (df_fast_noise_floor_ > df_noise_floor_) {
                const float decay_tau = clear_speech ? 0.06f : 0.25f;
                const float decay_alpha = time_const_alpha(frame_dt_sec, decay_tau);
                df_fast_noise_floor_ += (df_noise_floor_ - df_fast_noise_floor_) * decay_alpha;
            } else {
                df_fast_noise_floor_ = df_noise_floor_;
            }
        }
    }
}
