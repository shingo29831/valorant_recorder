#pragma once

#include <stdint.h>

#ifdef _WIN32
#define EXPORT_API extern "C" __declspec(dllexport)
#else
#define EXPORT_API extern "C" __attribute__((visibility("default")))
#endif

// Pythonから渡される録画設定
struct RecorderConfig {
    int video_fps;
    const char* video_resolution;
    const char* video_format;
    const char* video_input_source;
    const char* video_encoder;
    
    float mic_gain;
    float system_gain;
    float noise_gate_threshold;
    
    const char* mic_device_name;
    const char* save_file_path;
    const char* temp_file_path;
    const char* ffmpeg_path;
};

// Python (ctypes) から呼び出すAPI
EXPORT_API bool Recorder_Start(const RecorderConfig* config);
EXPORT_API void Recorder_Stop();
EXPORT_API bool Recorder_IsRunning();
EXPORT_API const char* Recorder_GetLastError();
