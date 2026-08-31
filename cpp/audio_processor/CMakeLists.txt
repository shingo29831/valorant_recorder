cmake_minimum_required(VERSION 3.15)
project(AudioProcessorModule)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 共有ライブラリ (DLL / SO) としてビルド
add_library(audio_processor SHARED
    AudioProcessor.cpp
    AudioProcessor.h
)

# --- サードパーティライブラリのパス設定 ---
set(THIRD_PARTY_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../third_party")

# SpeexDSP
set(SPEEX_INCLUDE_DIR "${THIRD_PARTY_DIR}/speexdsp/include")
set(SPEEX_LIB "${THIRD_PARTY_DIR}/speexdsp/lib/speexdsp.lib")
set(SPEEX_DLL "${THIRD_PARTY_DIR}/speexdsp/bin/libspeexdsp.dll") # vcpkgの出力名に合わせて修正

# NVIDIA Maxine
set(NVIDIA_INCLUDE_DIR "${THIRD_PARTY_DIR}/nvidia_maxine/include")
set(NVIDIA_LIB "${THIRD_PARTY_DIR}/nvidia_maxine/lib/nvafx.lib")
set(NVIDIA_DLL "${THIRD_PARTY_DIR}/nvidia_maxine/bin/nvafx.dll")
set(NVIDIA_MODELS_DIR "${THIRD_PARTY_DIR}/nvidia_maxine/models")

# --- インクルードとリンク ---
# SpeexDSPを有効化
target_include_directories(audio_processor PRIVATE ${SPEEX_INCLUDE_DIR})
target_link_libraries(audio_processor PRIVATE ${SPEEX_LIB})

# NVIDIA Maxine (必要になった際にコメントアウトを外す)
# target_include_directories(audio_processor PRIVATE ${NVIDIA_INCLUDE_DIR})
# target_link_libraries(audio_processor PRIVATE ${NVIDIA_LIB})

# --- 実行時バイナリのコピー ---
# Pythonからロードできるように、ビルド完了時にDLLとモデルフォルダを出力ディレクトリにコピーする
add_custom_command(TARGET audio_processor POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${SPEEX_DLL}"
        $<TARGET_FILE_DIR:audio_processor>
    # COMMAND ${CMAKE_COMMAND} -E copy_if_different
    #     "${NVIDIA_DLL}"
    #     $<TARGET_FILE_DIR:audio_processor>
    # COMMAND ${CMAKE_COMMAND} -E copy_directory
    #     "${NVIDIA_MODELS_DIR}"
    #     "$<TARGET_FILE_DIR:audio_processor>/models"
    COMMENT "Copying SpeexDSP DLL to output directory"
)
