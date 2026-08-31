#pragma once

#include <vector>
#include <memory>

// 前方宣言: 実際のプロジェクトでは各ライブラリのヘッダをインクルードします
// #include "webrtc/modules/audio_processing/include/audio_processing.h"
// #include "deep_filter_net.h" // または NVIDIA Maxine Audio Effects SDK

class AudioProcessor {
public:
    AudioProcessor(int sample_rate, int channels);
    ~AudioProcessor();

    // 音声処理パイプラインの実行
    // input: 入力音声データ (float, インターリーブ)
    // output: 出力音声データ (float, インターリーブ)
    // num_frames: 処理するフレーム数
    void process(const float* input, float* output, int num_frames);

private:
    int sample_rate_;
    int channels_;

    // 1. WebRTC APM (HPF用)
    // std::unique_ptr<webrtc::AudioProcessing> apm_pre_;

    // 2. AIノイズキャンセラー (DeepFilterNet または NVIDIA Maxine)
    // std::unique_ptr<AIDenoiser> ai_denoiser_;

    // 3. WebRTC APM (AGC用)
    // std::unique_ptr<webrtc::AudioProcessing> apm_post_;

    // 内部バッファ
    std::vector<float> temp_buffer1_;
    std::vector<float> temp_buffer2_;
};

extern "C" {
    // Python (ctypes) 等から呼び出すためのCインターフェース
#ifdef _WIN32
    __declspec(dllexport) void* AudioProcessor_Create(int sample_rate, int channels);
    __declspec(dllexport) void AudioProcessor_Process(void* processor, const float* input, float* output, int num_frames);
    __declspec(dllexport) void AudioProcessor_Destroy(void* processor);
#else
    void* AudioProcessor_Create(int sample_rate, int channels);
    void AudioProcessor_Process(void* processor, const float* input, float* output, int num_frames);
    void AudioProcessor_Destroy(void* processor);
#endif
}
