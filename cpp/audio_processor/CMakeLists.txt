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
set(SPEEX_DLL "${THIRD_PARTY_DIR}/speexdsp/bin/libspeexdsp.dll")

# DeepFilterNet
set(DEEPFILTER_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/include")
set(DEEPFILTER_LIB "${CMAKE_CURRENT_SOURCE_DIR}/lib/deep_filter.lib")
set(DEEPFILTER_DLL "${CMAKE_CURRENT_SOURCE_DIR}/bin/deep_filter.dll")

# --- インクルードとリンク ---
# SpeexDSPとDeepFilterNetを有効化
target_include_directories(audio_processor PRIVATE ${SPEEX_INCLUDE_DIR} ${DEEPFILTER_INCLUDE_DIR})

# Rustの静的ライブラリ(DeepFilterNet)が依存するWindowsシステムライブラリを追加
target_link_libraries(audio_processor PRIVATE 
    ${SPEEX_LIB} 
    ${DEEPFILTER_LIB}
    ntdll.lib
    bcrypt.lib
    ws2_32.lib
    userenv.lib
    advapi32.lib
)

# --- 実行時バイナリのコピー ---
# Pythonからロードできるように、ビルド完了時にDLLを出力ディレクトリにコピーする
add_custom_command(TARGET audio_processor POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${SPEEX_DLL}"
        $<TARGET_FILE_DIR:audio_processor>
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${DEEPFILTER_DLL}"
        $<TARGET_FILE_DIR:audio_processor>
    COMMENT "Copying SpeexDSP and DeepFilterNet DLLs to output directory"
)
