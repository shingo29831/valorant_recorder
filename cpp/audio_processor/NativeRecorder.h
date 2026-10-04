#pragma once

#include <stdint.h>

#ifdef _WIN32
#define EXPORT_API extern "C" __declspec(dllexport)
#else
#define EXPORT_API extern "C" __attribute__((visibility("default")))
#endif

// Pythonから渡される録画設定
struct RecorderConfig {
    float mic_gain;
    float system_gain;
    float noise_gate_threshold;
    int32_t denoise_mode;
    int32_t preprocess_mode;
    
    const char* mic_device_name;
    const char* ffmpeg_command;
    const char* save_file_path;
    const char* temp_file_path;
    const char* ffmpeg_path;
    const char* df_model_path;
};

// Python (ctypes) から呼び出すAPI
EXPORT_API bool Recorder_Start(const RecorderConfig* config);
EXPORT_API void Recorder_Stop();
EXPORT_API bool Recorder_IsRunning();
EXPORT_API const char* Recorder_GetLastError();
