cmake_minimum_required(VERSION 3.15)
project(AudioProcessorModule)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 共有ライブラリ (DLL / SO) としてビルド
add_library(audio_processor SHARED
    AudioProcessor.cpp
    AudioProcessor.h
)

# 外部ライブラリのインクルードとリンク設定をここに追加します
# 例: WebRTC
# target_include_directories(audio_processor PRIVATE ${WEBRTC_INCLUDE_DIR})
# target_link_libraries(audio_processor PRIVATE ${WEBRTC_LIBRARY})

# 例: DeepFilterNet (C API) または NVIDIA Maxine
# target_include_directories(audio_processor PRIVATE ${DEEPFILTER_INCLUDE_DIR})
# target_link_libraries(audio_processor PRIVATE ${DEEPFILTER_LIBRARY})
