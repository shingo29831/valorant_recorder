#pragma once

#include <string>

namespace audio_proc_detail {

struct DelayProbeResult {
    int frame_size = 480;
    int delay_samples = 1440;
    bool create_failed = false;
};

// 本体DFState生成前にプローブ専用DFStateを1回だけ生成・計測・即破棄し、状態汚染と同時二重メモリを防止する
DelayProbeResult measure_df_delay_samples(const std::string& model_path, int sample_rate, int max_delay_frames);

} // namespace audio_proc_detail
