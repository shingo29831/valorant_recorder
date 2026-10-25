import sys

def patch_soundcard_lib():
    """
    soundcardライブラリがWAVE_FORMAT_PCMやWAVE_FORMAT_IEEE_FLOATなどの
    非拡張フォーマットのデバイスを読み込もうとした際にクラッシュするバグを修正するモンキーパッチ。
    """
    if sys.platform != 'win32':
        return

    try:
        import ctypes
        import struct
        import threading
        # soundcard内部のMTA初期化がRPC_E_CHANGED_MODEで失敗しないよう先にインポートする
        import soundcard
        import soundcard.mediafoundation as mf
        import collections

        _soundcard_lock = threading.RLock()
        setattr(soundcard, '_lock', _soundcard_lock)

        # soundcardのCFFI定義は非パック(44B)であり実メモリ(40B)への直接書き込みはヒープ破壊を起こすためバイナリ構築する
        def patched_audio_client_init(self, ptr, samplerate, channels, blocksize, isloopback, exclusive_mode=False):
            with _soundcard_lock:
                self._ptr = ptr
                if isinstance(channels, int):
                    self.channelmap = list(range(channels))
                elif isinstance(channels, collections.abc.Iterable):
                    self.channelmap = list(channels)
                else:
                    raise TypeError('channels must be iterable or integer')

                if list(range(len(set(self.channelmap)))) != sorted(list(set(self.channelmap))):
                    raise TypeError('Due to limitations of WASAPI, channel maps on Windows '
                                    'must be a combination of `range(0, x)`.')

                if blocksize is None:
                    blocksize = self.deviceperiod[0] * samplerate

                ppMixFormat = mf._ffi.new('WAVEFORMATEXTENSIBLE**')
                hr = self._ptr[0][0].lpVtbl.GetMixFormat(self._ptr[0], ppMixFormat)
                mf._com.check_error(hr)
                try:
                    orig_tag = int(ppMixFormat[0][0].Format.wFormatTag)
                finally:
                    mf._ole32.CoTaskMemFree(ppMixFormat[0])

                channels_count = len(set(self.channelmap))
                if channels_count == 1:
                    channelmask = 0x4
                elif channels_count == 2:
                    channelmask = 0x3
                else:
                    channelmask = (1 << channels_count) - 1

                sr = int(samplerate)
                block_align = channels_count * 4
                avg_bytes = sr * block_align

                # 40バイト完全パック済みのWAVEFORMATEXTENSIBLE (KSDATAFORMAT_SUBTYPE_IEEE_FLOAT)
                raw_ext = struct.pack(
                    '<HHIIHHHHIIHH8B',
                    0xFFFE,
                    channels_count,
                    sr,
                    avg_bytes,
                    block_align,
                    32,
                    22,
                    32,
                    channelmask,
                    0x00000003,
                    0x0000,
                    0x0010,
                    0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71,
                )
                ext_buf = mf._ffi.new('char[]', raw_ext)
                ext_ptr = mf._ffi.cast('WAVEFORMATEXTENSIBLE*', ext_buf)

                raw_pcm = struct.pack(
                    '<HHIIHHH',
                    0x0003,
                    channels_count,
                    sr,
                    avg_bytes,
                    block_align,
                    32,
                    0,
                )
                pcm_buf = mf._ffi.new('char[]', raw_pcm)
                pcm_ptr = mf._ffi.cast('WAVEFORMATEXTENSIBLE*', pcm_buf)

                if exclusive_mode:
                    sharemode = mf._ole32.AUDCLNT_SHAREMODE_EXCLUSIVE
                else:
                    sharemode = mf._ole32.AUDCLNT_SHAREMODE_SHARED

                streamflags = 0x80000000 | 0x08000000 | 0x00080000
                if isloopback:
                    streamflags |= 0x00020000

                bufferduration = int(blocksize / samplerate * 10000000)
                first_ptr, second_ptr = (ext_ptr, pcm_ptr) if orig_tag == 0xFFFE else (pcm_ptr, ext_ptr)
                hr = self._ptr[0][0].lpVtbl.Initialize(
                    self._ptr[0], sharemode, streamflags, bufferduration, 0, first_ptr, mf._ffi.NULL
                )
                if hr != 0:
                    hr = self._ptr[0][0].lpVtbl.Initialize(
                        self._ptr[0], sharemode, streamflags, bufferduration, 0, second_ptr, mf._ffi.NULL
                    )
                mf._com.check_error(hr)

                self.samplerate = samplerate
                self._idle_start_time = None

        mf._AudioClient.__init__ = patched_audio_client_init

        # _COMLibrary.__init__ のモンキーパッチ (COM初期化の競合対策)
        def patched_com_library_init(self):
            import platform
            COINIT_MULTITHREADED = 0x0
            if platform.win32_ver()[0] == '8':
                hr = mf._ole32.CoInitialize(mf._ffi.NULL)
            else:
                hr = mf._ole32.CoInitializeEx(mf._ffi.NULL, COINIT_MULTITHREADED)
            try:
                if hr == 1: # S_FALSE
                    pass
                else:
                    self.check_error(hr)
                self.com_loaded = True
            except RuntimeError as e:
                RPC_E_CHANGED_MODE = 0x80010106
                if hr + 2 ** 32 == RPC_E_CHANGED_MODE or hr == RPC_E_CHANGED_MODE:
                    self.com_loaded = False
                else:
                    raise e

        mf._COMLibrary.__init__ = patched_com_library_init

        # スレッド終了やSTA再初期化でMTAが破棄されIMMDeviceEnumeratorが無効化されるのを防ぐ
        try:
            _mta_cookie = ctypes.c_void_p()
            ctypes.windll.ole32.CoIncrementMTAUsage(ctypes.byref(_mta_cookie))
            setattr(mf, '_mta_cookie', _mta_cookie)
        except Exception:
            pass

        # メインスレッドのMTAを一旦解除し、PyQt6が要求するSTAでCOMを再初期化する
        if hasattr(mf, '_com') and isinstance(mf._com, mf._COMLibrary):
            if getattr(mf._com, 'com_loaded', False):
                ctypes.windll.ole32.CoUninitialize()
                mf._com.com_loaded = False
        try:
            ctypes.windll.ole32.CoInitialize(None)
        except Exception:
            pass

        print("[Patcher] Successfully applied monkey patch to soundcard library.")
            
    except Exception as e:
        print(f"[Patcher] Failed to apply monkey patch to soundcard library: {e}")
